"""Explainable IPO screening, dated discovery, and deduplicated Telegram briefs."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from html import escape

from sqlalchemy import select, text

from app.config import settings
from app.models.ipo import IPOCheck, IPOIssue
from app.models.telegram import TelegramDelivery
from app.schemas.ipo import IPOData
from app.services.ipo_sources import IST, InvestorGainProvider
from app.services.result_sources import digest
from app.services.telegram_client import configured


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def gmp_pct(issue):
    return (issue.gmp / issue.price_high * 100).quantize(Decimal("0.01")) if issue.gmp is not None and issue.price_high else None


def ranked(issues):
    return sorted(issues, key=lambda i: (gmp_pct(i) is None, -(gmp_pct(i) or Decimal(0)), i.name.casefold()))


def screen(issue, current):
    """Annual comparisons only; unsupported/incomplete companies remain visible."""
    missing, reasons, risks = [], [], []
    for key in ("sector", "price_high", "issue_cr", "fresh_cr", "ofs_cr", "lot_size", "gmp", "gmp_at", "business", "annual_eps", "annual_eps_period"):
        if getattr(issue, key) is None:
            missing.append(key)
    if issue.detail_error:
        missing.append("company_details")
    if not issue.objects and issue.fresh_cr:
        missing.append("use_of_proceeds")
    if issue.segment != "MAINBOARD":
        reasons.append("Only mainboard IPOs are eligible")
    if not issue.opens_on or not issue.closes_on or issue.closes_on < issue.opens_on:
        missing.append("confirmed_dates")
    if issue.gmp_at and not timedelta(0) <= current - utc(issue.gmp_at) <= timedelta(hours=settings.ipo_gmp_max_age_hours):
        reasons.append("GMP quote is stale or future-dated")
    premium = gmp_pct(issue)
    if premium is not None and premium < Decimal(str(settings.ipo_min_gmp_pct)):
        reasons.append(f"GMP below {settings.ipo_min_gmp_pct:g}% screen")
    annual = sorted([p for p in issue.financials if p.get("annual")], key=lambda p: p["ended_on"], reverse=True)
    metrics = {}
    if len(annual) < 2 or int(annual[0]["ended_on"][:4]) - int(annual[1]["ended_on"][:4]) != 1:
        missing.append("two_consecutive_annual_financials")
    else:
        latest, previous = annual[:2]
        if str(issue.annual_eps_period) != latest["ended_on"]:
            missing.append("EPS_basis_matching_annual_financials")
        if (current.astimezone(IST).date() - datetime.fromisoformat(latest["ended_on"]).date()).days > 550:
            reasons.append("Annual financials are too old")
        metrics["annual_period"] = latest["ended_on"]
        for key, label in (("income_cr", "Total income"), ("pat_cr", "PAT")):
            value, prior = latest.get(key), previous.get(key)
            if value is None or prior is None:
                missing.append(key)
                continue
            value, prior = Decimal(value), Decimal(prior)
            metrics[key] = str(value)
            if value <= 0 or prior <= 0:
                reasons.append(f"{label} must be positive in both annual periods")
            else:
                growth = ((value / prior - 1) * 100).quantize(Decimal("0.01"))
                metrics[key + "_growth_pct"] = str(growth)
                if growth < 0:
                    reasons.append(f"Annual {label.lower()} declined")
        debt, worth = latest.get("debt_cr"), latest.get("net_worth_cr")
        if debt is not None and worth is not None and Decimal(worth) > 0:
            metrics["debt_to_equity"] = str((Decimal(debt) / Decimal(worth)).quantize(Decimal("0.01")))
            if Decimal(metrics["debt_to_equity"]) > 2:
                risks.append("Borrowings exceed 2× net worth; assess leverage in sector context")
        else:
            risks.append("Annual leverage could not be assessed")
        if worth is not None and Decimal(worth) <= 0:
            reasons.append("Non-positive net worth")
    if issue.annual_eps is not None and issue.price_high:
        if issue.annual_eps <= 0:
            reasons.append("Annual EPS is not positive")
        else:
            pe = (issue.price_high / issue.annual_eps).quantize(Decimal("0.01"))
            metrics["pe"] = str(pe)
            if pe > Decimal(str(settings.ipo_max_pe)):
                reasons.append(f"Annual P/E exceeds {settings.ipo_max_pe:g}× screen")
    if issue.issue_cr and issue.fresh_cr is not None and issue.ofs_cr is not None:
        if abs(issue.fresh_cr + issue.ofs_cr - issue.issue_cr) > Decimal("0.10"):
            reasons.append("Fresh issue and OFS do not reconcile to issue size")
        metrics["ofs_pct"] = str((issue.ofs_cr / issue.issue_cr * 100).quantize(Decimal("0.01")))
        if Decimal(metrics["ofs_pct"]) >= 75:
            risks.append("At least 75% OFS: most proceeds go to selling shareholders")
    risks.extend(["Third-party company data; verify the RHP", "GMP is unofficial and can change before listing"])
    status = "INSUFFICIENT_DATA" if missing else "WATCH" if reasons else "ATTRACTIVE"
    return {"status": status, "missing": missing, "reasons": reasons, "risks": risks, "metrics": metrics,
            "method": "Rules-based financial screen; no AI opinion or sector-relative valuation is implied"}


def read_issue(row):
    return IPOData.model_validate(row.details)


def issue_view(row, current):
    issue = read_issue(row)
    return {**issue.model_dump(mode="json"), "id": row.id, "fetched_at": row.fetched_at,
            "gmp_pct": str(gmp_pct(issue)) if gmp_pct(issue) is not None else None,
            "min_investment": str(issue.price_high * issue.lot_size) if issue.price_high and issue.lot_size else None,
            "assessment": screen(issue, current),
            "stage": "DATES_PENDING" if not issue.opens_on else "UPCOMING" if issue.opens_on > current.astimezone(IST).date()
                     else "OPEN" if issue.closes_on and issue.closes_on >= current.astimezone(IST).date() else "CLOSED"}


def list_issues(db, current):
    rows = list(db.scalars(select(IPOIssue).where(
        IPOIssue.closes_on.is_(None) | (IPOIssue.closes_on >= current.astimezone(IST).date()))))
    views = [issue_view(r, current) for r in rows]
    return sorted(views, key=lambda v: (v["gmp_pct"] is None, -Decimal(v["gmp_pct"] or "0"), v["name"]))


def validate_delivery(db, job, current):
    if not settings.ipo_monitor_enabled:
        return "IPO monitoring is paused"
    note = job.notification
    if utc(datetime.fromisoformat(note["expires_at"])) <= current:
        return "IPO brief expired; do not send stale GMP or a post-opening alert"
    for source_id, opening in zip(note["issue_ids"], note["opening_dates"]):
        row = db.scalar(select(IPOIssue).where(IPOIssue.source_id == source_id))
        if row is None or str(row.opens_on) != opening:
            return "IPO opening date changed; old brief superseded"
        if screen(read_issue(row), current)["status"] != "ATTRACTIVE":
            return "IPO no longer passes the attractiveness screen"
    return None


def format_brief(issue, assessment, heading, rank, total):
    def num(value, places=2):
        if value is None:
            return "N/A"
        value = Decimal(str(value)).quantize(Decimal(10) ** -places)
        return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else format(value, "f")

    def rupees(value):
        if value is None:
            return "N/A"
        whole, dot, fraction = num(value).partition(".")
        sign, digits = ("-", whole[1:]) if whole.startswith("-") else ("", whole)
        groups = [digits[-3:]]
        digits = digits[:-3]
        while digits:
            groups.insert(0, digits[-2:])
            digits = digits[:-2]
        return "₹" + sign + ",".join(groups) + (dot + fraction if dot else "")

    def day(value, year=False):
        return value.strftime("%d %b %Y" if year else "%d %b").lstrip("0") if value else "TBA"

    start, end = issue.opens_on, issue.closes_on
    if start and end and start.year == end.year:
        opening = str(start.day) if start.month == end.month else day(start)
        window = f"{opening}–{day(end, year=True)}"
    else:
        window = f"{day(start, year=True)}–{day(end, year=True)}"
    quote = (day(issue.gmp_at) + ", " + issue.gmp_at.astimezone(IST).strftime("%I:%M %p").lstrip("0") + " IST") if issue.gmp_at else "unavailable"
    # Describe proceeds with short categories, never clipped source paragraphs.
    uses = " ".join(issue.objects).casefold()
    purposes = []
    if any(word in uses for word in ("expan", "capital expenditure", "equipment", "new showroom")):
        purposes.append("Expansion")
    if "working capital" in uses:
        purposes.append("working capital")
    if any(word in uses for word in ("repay", "prepay", "pre-pay", "borrowings")):
        purposes.append("debt repayment")
    funds = ", ".join(purposes) if purposes else "See source for details"
    if issue.fresh_cr == 0:
        funds = "Selling shareholders receive the proceeds"

    m = assessment["metrics"]
    risks = []
    if m.get("debt_to_equity") is not None and Decimal(m["debt_to_equity"]) > 2:
        risks.append(f"High borrowings ({num(m['debt_to_equity'])}× net worth).")
    if m.get("ofs_pct") is not None and Decimal(m["ofs_pct"]) >= 75:
        risks.append(f"{num(m['ofs_pct'], 1)}% OFS goes to selling shareholders.")
    risks.append("GMP is unofficial; listing gains aren't guaranteed.")
    minimum = issue.price_high * issue.lot_size if issue.price_high and issue.lot_size else None
    ranking = f" · {rank}/{total}" if total > 1 else ""
    lines = [f"<b>{escape(issue.name)} · Mainboard IPO</b>",
        escape(heading + ranking), escape(issue.sector or "Sector unavailable"), "",
        f"<b>GMP: {num(gmp_pct(issue))}% ({rupees(issue.gmp)}/share)</b>",
        f"As of {quote}", "", "<b>IPO details</b>",
        f"Apply: {window} · Listing: {day(issue.lists_on, year=bool(end and issue.lists_on and end.year != issue.lists_on.year))}",
        f"Price: {rupees(issue.price_low)}–{rupees(issue.price_high)}",
        f"Minimum: {rupees(minimum)} ({issue.lot_size or 'TBA'} shares)",
        f"Size: {rupees(issue.issue_cr)} Cr · Fresh: {rupees(issue.fresh_cr)} Cr · OFS: {rupees(issue.ofs_cr)} Cr",
        "", "<b>Why shortlisted</b>",
        f"Annual profit growth: {num(m.get('pat_cr_growth_pct'), 1)}% · Income growth: {num(m.get('income_cr_growth_pct'), 1)}%",
        f"Annual P/E: {num(m.get('pe'))}×",
        f"Funds: {funds}", "", "<b>Risk:</b> " + escape(" ".join(risks)), "",
        f'<a href="{escape(issue.source_url, quote=True)}">Details &amp; GMP</a>']
    if issue.rhp_url:
        lines[-1] += f' · <a href="{escape(issue.rhp_url, quote=True)}">RHP</a>'
    return "\n".join(lines)


def enqueue_briefs(db, issues, current, kind, key):
    if not settings.ipo_monitor_enabled or not settings.telegram_enabled or not configured():
        return 0
    selected = [i for i in ranked(issues) if screen(i, current)["status"] == "ATTRACTIVE"]
    if not selected:
        return 0
    chat = settings.telegram_chat_id.strip()
    heading = "Weekly IPO shortlist" if kind == "weekly" else "IPO opens soon — fresh review"
    grouped = [(key, list(enumerate(selected, 1)))] if kind == "weekly" else [
        (f"{key}:{issue.source_id}", [(n, issue)]) for n, issue in enumerate(selected, 1)]
    count = 0
    for group_key, indexed in grouped:
        identity = digest(["ipo", kind, group_key, chat])
        if db.scalar(select(TelegramDelivery.id).where(TelegramDelivery.idempotency_key == identity)):
            continue
        members = [i for _, i in indexed]
        opening = min(datetime.combine(i.opens_on, time(9), IST) for i in members)
        expires = min(current + timedelta(hours=settings.ipo_gmp_max_age_hours), opening,
                      min(utc(i.gmp_at) + timedelta(hours=settings.ipo_gmp_max_age_hours) for i in members))
        if expires <= current:
            continue
        parts = [format_brief(i, screen(i, current), heading, n, len(selected)) for n, i in indexed]
        db.add(TelegramDelivery(event_id=None, version="ipo-v1", idempotency_key=identity,
            notification={"kind": "IPO", "expires_at": expires.isoformat(), "issue_ids": [i.source_id for i in members],
                          "opening_dates": [i.opens_on.isoformat() for i in members]},
            chat_id=chat, status="PENDING", attempts=0, next_attempt_at=current,
            message=parts[0], message_parts=parts, message_ids=[], parse_mode="HTML"))
        count += 1
    return count


async def refresh(db, *, provider=None, current=None, notify=True):
    current = utc(current or datetime.now(timezone.utc))
    # Serialize refresh + enqueue across API and scheduler without holding row locks
    # on portfolio records. Unique delivery keys provide a second deduplication layer.
    if db.bind.dialect.name == "postgresql" and not db.scalar(text("SELECT pg_try_advisory_xact_lock(840025)")):
        return {"status": "BUSY", "stored": 0, "queued": 0}
    check = IPOCheck(attempted_at=current)
    db.add(check)
    try:
        issues = await (provider or InvestorGainProvider()).fetch(current)
    except Exception as exc:
        # Never persist remote URLs/response bodies or replace data with fixtures.
        check.error = f"IPO discovery failed ({type(exc).__name__}); stored data retained"
        db.commit()
        return {"status": "FAILED", "stored": 0, "queued": 0, "error": check.error}
    issues = [i for i in issues if i.segment == "MAINBOARD"]
    for issue in issues:
        row = db.scalar(select(IPOIssue).where(IPOIssue.source_id == issue.source_id))
        if row is None:
            row = IPOIssue(source_id=issue.source_id)
            db.add(row)
        row.name, row.source_url = issue.name, issue.source_url
        row.opens_on, row.closes_on = issue.opens_on, issue.closes_on
        row.fetched_at, row.details = current, issue.model_dump(mode="json")
    check.succeeded_at = current
    failures = sum(i.detail_error is not None for i in issues)
    if failures:
        check.error = f"Company details unavailable for {failures} IPO(s); those issues are excluded from alerts"
    queued = 0
    local = current.astimezone(IST)
    week_start = local.date() - timedelta(days=local.weekday())
    week_key = week_start.isoformat()
    last_week = db.scalar(select(IPOCheck.id).where(IPOCheck.weekly_key == week_key))
    if notify and local.hour >= settings.ipo_check_hour_ist:
        if last_week is None:
            coming = [i for i in issues if i.opens_on and local.date() < i.opens_on <= local.date() + timedelta(days=14)]
            queued += int(enqueue_briefs(db, coming, current, "weekly", week_key))
            if settings.telegram_enabled and configured():
                check.weekly_key = week_key  # Empty shortlist also completes the week's scan.
        for opening in (local.date(), local.date() + timedelta(days=1)):
            before = [i for i in issues if i.opens_on == opening]
            # A fresh review of every company happens before filtering the Telegram list.
            # One key per company/opening also covers restart/day-of catch-up without duplicates.
            queued += int(enqueue_briefs(db, before, current, "preopen", opening.isoformat()))
    db.commit()
    return {"status": "OK", "stored": len(issues), "queued": queued,
            "detail_failures": failures}


async def run_due(session_factory, current=None):
    current = utc(current or datetime.now(timezone.utc))
    local = current.astimezone(IST)
    if not settings.ipo_monitor_enabled or local.hour < settings.ipo_check_hour_ist:
        return
    with session_factory() as db:
        last = db.scalar(select(IPOCheck).order_by(IPOCheck.id.desc()).limit(1))
        if last and utc(last.attempted_at).astimezone(IST).date() == local.date():
            if (last.succeeded_at and not last.error) or current - utc(last.attempted_at) < timedelta(minutes=30):
                return
        await refresh(db, current=current)


def status(db):
    last = db.scalar(select(IPOCheck).order_by(IPOCheck.id.desc()).limit(1))
    success = db.scalar(select(IPOCheck).where(IPOCheck.succeeded_at.is_not(None)).order_by(IPOCheck.id.desc()).limit(1))
    deliveries = list(db.scalars(select(TelegramDelivery).where(TelegramDelivery.version == "ipo-v1").order_by(TelegramDelivery.id.desc()).limit(10)))
    return {"enabled": settings.ipo_monitor_enabled, "telegram_enabled": settings.telegram_enabled,
            "telegram_configured": configured(), "last_attempt_at": last.attempted_at if last else None,
            "last_success_at": success.succeeded_at if success else None, "last_error": last.error if last else None,
            "schedule": f"Monday {settings.ipo_check_hour_ist:02}:00 IST weekly; daily at {settings.ipo_check_hour_ist:02}:00 IST for discovery and pre-opening reviews (30-minute retry after failures)",
            "screen": {"min_gmp_pct": settings.ipo_min_gmp_pct, "max_pe": settings.ipo_max_pe,
                       "gmp_max_age_hours": settings.ipo_gmp_max_age_hours},
            "deliveries": [{"id": d.id, "status": d.status, "sent_at": d.sent_at, "last_error": d.last_error} for d in deliveries]}
