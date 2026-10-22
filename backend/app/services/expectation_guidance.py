"""Register existing, validated management guidance without a second extraction pipeline."""
from datetime import datetime, time, timezone
import re
from sqlalchemy import select
from app.models.commentary import ManagementGuidance, ManagementStatement
from app.models.financial import FinancialPeriod
from app.models.expectations import HistoricalExpectation, HistoricalExpectationRevision
from app.models.checkpoint import InvestorCheckpoint
from app.schemas.expectations import ExpectationInput
from app.services.commentary import guidance_target_end, capex_scope_unclear
from app.services.expectation_evidence import statement_evidence, utc

METRICS = {"REVENUE_GROWTH": "REVENUE_YOY", "PAT_GROWTH": "PAT_YOY", "PAT_MARGIN": "PAT_MARGIN",
    "EBITDA_MARGIN": "EBITDA_MARGIN", "CAPEX": "CAPITAL_EXPENDITURE", "DEBT": "TOTAL_DEBT",
    "CASH_FLOW": "OPERATING_CASH_FLOW"}


def register_guidance(db, company_id, now):
    from app.services.expectations import save
    registered = set(db.scalars(select(HistoricalExpectationRevision.guidance_id).join(HistoricalExpectation).where(
        HistoricalExpectation.company_id == company_id, HistoricalExpectationRevision.guidance_id.is_not(None))))
    for guidance in db.scalars(select(ManagementGuidance).where(ManagementGuidance.company_id == company_id).order_by(ManagementGuidance.published_at, ManagementGuidance.id)):
        if guidance.id in registered or guidance.metric not in METRICS:
            continue
        statement = db.get(ManagementStatement, guidance.statement_id)
        owner = db.get(FinancialPeriod, guidance.financial_period_id)
        end = guidance_target_end(guidance.target_period)
        if not statement or not owner or not end or (guidance.metric == "CAPEX" and capex_scope_unclear(statement.raw_statement)):
            continue
        evidence = statement_evidence(db, statement, company_id, owner.scope.value, now)
        if not evidence:
            continue
        cutoff = datetime.combine(end, time.max, tzinfo=timezone.utc)
        if datetime.fromisoformat(evidence["published_at"]) >= cutoff:
            continue
        text = statement.raw_statement
        kind = "CONDITIONAL" if re.search(r"\bif\b|subject to|conditional", text, re.I) else (
            "ASPIRATION" if re.search(r"aspir|ambition|hope|aim", text, re.I) else
            "FIRM_TARGET" if re.search(r"\btarget|commit|will\b", text, re.I) else "ESTIMATE")
        checkpoint = db.scalar(select(InvestorCheckpoint).where(InvestorCheckpoint.guidance_id == guidance.id))
        imported = now >= cutoff
        data = ExpectationInput(metric=METRICS[guidance.metric], financial_scope=owner.scope.value,
            period=end, period_type="QUARTERLY" if guidance.target_period.startswith("Q") else "ANNUAL",
            expectation_type="RANGE", lower_value=guidance.lower_bound, upper_value=guidance.upper_bound,
            unit=guidance.unit, currency="INR" if guidance.unit.startswith("INR") else "NONE",
            description=statement.raw_statement, cutoff_at=cutoff, source_type="MANAGEMENT",
            source_reference=f"G{guidance.id}", evidence_id=statement.evidence_id,
            historical_import=imported, issued_at=utc(statement.published_at) if imported else None,
            guidance_kind=kind, guidance_id=guidance.id, checkpoint_id=checkpoint.id if checkpoint and checkpoint.target_date == end else None)
        # Same metric and deadline do not establish revision lineage. Preserve each
        # source statement; explicit user revisions remain available through the API.
        save(db, company_id, data)
