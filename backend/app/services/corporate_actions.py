"""NSE corporate actions and the share basis they imply.

A split or bonus changes how many shares exist, so a per-share number (price or
EPS) is only comparable with another one on the same share *basis*. NSE publishes
an ``adjustmentFactor`` per action: the multiplier that puts a pre-ex-date price
on the post-ex-date basis (0.5 for a 1:1 bonus, 0.1 for a 10:1 split).

``cumulative(d)`` is the product of factors of every split/bonus with an
ex-date on or before ``d``. A per-share value observed on basis date ``a`` is
expressed on basis date ``b`` by multiplying with ``cumulative(b) / cumulative(a)``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.market.nse_mcp import NSEMCPError, get_nse_mcp
from app.models.company import Company
from app.models.market_events import CorporateAction, CorporateActionSync

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
SHARE_ACTIONS = {"SPLIT", "BONUS"}
LOOKBACK_DAYS = 5 * 365
LOOKAHEAD_DAYS = 180
# Splits and bonuses are announced weeks before the ex-date; a sync is trusted to
# have seen any ex-date up to this many days after it ran.
TRUSTED_DAYS_AFTER_SYNC = 14
SYMBOL = re.compile(r"^[A-Z0-9&-]{1,20}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ShareAction:
    ex_date: date
    factor: Decimal
    label: str


@dataclass
class ShareBasis:
    """Known split/bonus history for one company over a confirmed window."""

    from_date: date
    to_date: date
    actions: list[ShareAction] = field(default_factory=list)

    def covers(self, start: date, end: date) -> bool:
        return self.from_date <= start and end <= self.to_date

    def cumulative(self, day: date) -> Decimal:
        result = Decimal(1)
        for action in self.actions:
            if action.ex_date <= day:
                result *= action.factor
        return result

    def factor_between(self, basis_date: date, target_date: date) -> Decimal:
        """Multiply a per-share value on ``basis_date``'s basis to reach ``target_date``'s."""
        return self.cumulative(target_date) / self.cumulative(basis_date)

    def between(self, first: date, last: date) -> list[ShareAction]:
        low, high = min(first, last), max(first, last)
        return [a for a in self.actions if low < a.ex_date <= high]

    @staticmethod
    def describe(actions: list[ShareAction]) -> str:
        return ", ".join(f"{a.label} (ex {a.ex_date.isoformat()}, factor {a.factor.normalize()})" for a in actions)


def share_basis(db: Session, company_id: int) -> ShareBasis | None:
    """The confirmed split/bonus history, or ``None`` if it was never fully synced."""
    sync = db.get(CorporateActionSync, company_id)
    if sync is None or not sync.complete or not sync.synced_at or not sync.from_date or not sync.to_date:
        return None
    trusted_until = min(
        sync.to_date, _aware(sync.synced_at).astimezone(IST).date() + timedelta(days=TRUSTED_DAYS_AFTER_SYNC)
    )
    rows = db.scalars(
        select(CorporateAction)
        .where(CorporateAction.company_id == company_id, CorporateAction.action_type.in_(SHARE_ACTIONS))
        .order_by(CorporateAction.ex_date)
    )
    actions = []
    for row in rows:
        if row.adjustment_factor is None:
            return None  # A split we cannot quantify makes the basis unknown.
        actions.append(ShareAction(row.ex_date, Decimal(row.adjustment_factor), row.purpose))
    return ShareBasis(sync.from_date, trusted_until, actions)


def _factor(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and Decimal("0.0001") <= result < 1 else None


async def sync_company(db: Session, company: Company, *, force: bool = False) -> CorporateActionSync:
    """Fetch the company's NSE corporate actions for the last five years and next six months."""
    sync = db.get(CorporateActionSync, company.id)
    if sync is None:
        sync = CorporateActionSync(company_id=company.id, complete=False)
        db.add(sync)
    now = _now()
    if (
        not force
        and sync.complete
        and sync.synced_at
        and now - _aware(sync.synced_at) < timedelta(hours=20)
    ):
        return sync
    sync.last_attempt_at = now
    symbol = company.symbol.strip().upper()
    if company.exchange.strip().upper() != "NSE" or not SYMBOL.fullmatch(symbol):
        sync.last_error = "NSE_SYMBOL_REQUIRED"
        db.commit()
        return sync
    today = now.astimezone(IST).date()
    start, end = today - timedelta(days=LOOKBACK_DAYS), today + timedelta(days=LOOKAHEAD_DAYS)
    client = get_nse_mcp()
    try:
        payload = await client.call(
            "bhavcopy",
            "get_corporate_actions",
            {"symbol": symbol, "fromDate": start.isoformat(), "toDate": end.isoformat()},
            ttl=0,
        )
    except NSEMCPError as exc:
        sync.last_error = exc.reason
        db.commit()
        return sync
    rows = payload.get("actions") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        sync.last_error = "MALFORMED_RESPONSE"
        db.commit()
        return sync
    if isinstance(payload, dict) and payload.get("warning"):
        # NSE flags incomplete lists with a warning; never treat them as confirmed.
        sync.last_error = f"INCOMPLETE: {str(payload['warning'])[:180]}"
        sync.complete = False
        db.commit()
        return sync

    parsed: dict[tuple, CorporateAction] = {}
    unquantified = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ex_date = date.fromisoformat(str(row.get("exDate")))
        except ValueError:
            continue
        action_type = str(row.get("actionType") or "OTHER").upper()[:20]
        purpose = re.sub(r"\s+", " ", str(row.get("purpose") or action_type)).strip()[:500]
        factor = _factor(row.get("adjustmentFactor")) if action_type in SHARE_ACTIONS else None
        if action_type in SHARE_ACTIONS and factor is None:
            unquantified = True
        parsed[(ex_date, action_type, purpose)] = CorporateAction(
            company_id=company.id,
            ex_date=ex_date,
            action_type=action_type,
            purpose=purpose,
            adjustment_factor=factor,
            source="NSE_MCP",
            source_url=client.url("bhavcopy"),
            fetched_at=now,
        )
    # Replace the confirmed window atomically; NSE is the source of truth for it.
    db.execute(
        delete(CorporateAction).where(
            CorporateAction.company_id == company.id,
            CorporateAction.ex_date >= start,
            CorporateAction.ex_date <= end,
        )
    )
    db.add_all(parsed.values())
    previous_start = sync.from_date if sync.complete and sync.from_date else start
    sync.from_date = min(previous_start, start)
    sync.to_date = end
    sync.synced_at = now
    sync.complete = not unquantified
    sync.last_error = "SPLIT_OR_BONUS_WITHOUT_FACTOR" if unquantified else None
    db.commit()
    logger.info("corporate_actions.synced company=%s actions=%d", symbol, len(parsed))
    return sync


def actions_read(db: Session, company_id: int) -> dict:
    sync = db.get(CorporateActionSync, company_id)
    rows = db.scalars(
        select(CorporateAction)
        .where(CorporateAction.company_id == company_id)
        .order_by(CorporateAction.ex_date.desc())
    )
    basis = share_basis(db, company_id)
    return {
        "sync": {
            "complete": bool(sync and sync.complete),
            "from_date": sync.from_date.isoformat() if sync and sync.from_date else None,
            "to_date": sync.to_date.isoformat() if sync and sync.to_date else None,
            "synced_at": _aware(sync.synced_at).isoformat() if sync and sync.synced_at else None,
            "trusted_until": basis.to_date.isoformat() if basis else None,
            "last_error": sync.last_error if sync else None,
        },
        "actions": [
            {
                "ex_date": row.ex_date.isoformat(),
                "action_type": row.action_type,
                "purpose": row.purpose,
                "adjustment_factor": str(row.adjustment_factor.normalize()) if row.adjustment_factor is not None else None,
                "affects_share_basis": row.action_type in SHARE_ACTIONS,
            }
            for row in rows
        ],
        "method": "Splits and bonuses from NSE corporate actions. A per-share value on date A is restated "
        "to date B's share basis by multiplying with the product of adjustment factors for ex-dates in (A, B].",
    }
