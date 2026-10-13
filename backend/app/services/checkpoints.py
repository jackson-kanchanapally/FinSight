"""Deterministic commitment tracking; management promises never prove delivery."""
import calendar
import re
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from app.models.checkpoint import InvestorCheckpoint, CheckpointEvaluation
from app.models.commentary import ManagementGuidance, ManagementStatement, CommentaryDocument
from app.models.event import CompanyEvent
from app.models.financial import FinancialPeriod, FinancialStatus
from app.services.commentary import guidance_target_end
from app.services.memory.service import guidance_record, local_day


def target_period(text, period):
    text = re.sub(r"FY\s*['’]", "FY", text, flags=re.I)
    pair = re.search(r"\bQ([1-4])\s*FY\s*(?:20)?(\d{2})\b", text, re.I)
    if pair:
        return f"Q{pair[1]}FY{pair[2]}"
    fy = re.search(r"\bFY\s*(?:20)?(\d{2})\b", text, re.I)
    q = re.search(r"\b(?:by|from|of|mid of|end of)\s+Q([1-4])\b", text, re.I)
    if q and (fy or (period.quarter and int(q[1]) >= period.quarter)):
        return f"Q{q[1]}FY{fy[1] if fy else period.fiscal_year[-2:]}"
    return f"FY{fy[1]}" if fy else None


def checkpoint_definition(statement, period, guidance=None):
    text = " ".join(statement.raw_statement.split())
    if guidance:
        return dict(definition_key=guidance.metric, topic=statement.topic, commitment=text,
            target_period=guidance.target_period, target_date=guidance_target_end(guidance.target_period),
            metric=guidance.metric, target={"lower": str(guidance.lower_bound), "upper": str(guidance.upper_bound)},
            unit=guidance.unit, measurement_note=None)
    if re.search(r"\b(?:cannot|not supposed|can't|not planned)\b", text, re.I):
        return None
    target = target_period(text, period)
    deadline = guidance_target_end(target) if target else None
    if deadline and deadline < local_day(statement.published_at):
        return None
    metric = unit = key = None
    values = {}
    note = None
    margin = re.search(r"\b(PAT|EBITDA) margins?.{0,50}?(\d+(?:\.\d+)?)\s*%?\s*(?:to|[-–])\s*(\d+(?:\.\d+)?)\s*%", text, re.I)
    capex = re.search(r"(?:INR|Rs\.?|₹)\s*(\d[\d,]*(?:\.\d+)?)\s*crores?", text, re.I)
    gpus = re.search(r"(\d[\d,]*)\s+GPUs?\b", text, re.I)
    if margin and re.search(r"target|expect|guidance", text, re.I):
        metric = key = margin[1].upper() + "_MARGIN"
        values = {"lower": margin[2], "upper": margin[3]}
        unit = "PERCENT"
        if not target:
            note = "Management did not state an assessable reporting period; no deadline is inferred."
    elif capex and re.search(r"\bcapex|capital expenditure", text, re.I) and re.search(r"target|will|plan", text, re.I):
        metric = key = "CAPEX"
        values = {"lower": capex[1].replace(",", ""), "upper": capex[1].replace(",", "")}
        unit = "INR_CRORE"
        if re.search(r"additional|don't have the numbers|do not have the numbers", text, re.I):
            metric = key = "CAPEX_SCOPE_UNCLEAR"
            note = "Total versus additional spending is unclear in the source. Confirm the budget basis before comparing actuals."
    elif gpus and re.search(r"target|we(?:'ll| will).{0,20}deploy", text, re.I):
        metric = key = "GPU_DEPLOYMENT"
        values = {"lower": gpus[1].replace(",", ""), "upper": gpus[1].replace(",", "")}
        unit = "GPU_COUNT"
        months = re.search(r"by\s+(January|February|March|April|May|June|July|August|September|October|November|December)(?:\s+(?:or|/)\s+(January|February|March|April|May|June|July|August|September|October|November|December))?\s+(next year|20\d{2})", text, re.I)
        if months:
            names = list(calendar.month_name)
            month = max(names.index(months[i].title()) for i in (1, 2) if months[i])
            year = local_day(statement.published_at).year + 1 if months[3].lower() == "next year" else int(months[3])
            deadline = date(year, month, calendar.monthrange(year, month)[1])
        note = "Deployment requires a verified operating disclosure; financial totals cannot establish GPU count."
    elif target and re.search(r"\brevenue\b", text, re.I) and re.search(r"will.{0,30}(?:revenue|from)|revenue.{0,30}(?:begin|start|from)", text, re.I):
        # Require a named business in this statement; pronouns alone are unsafe.
        subject = re.search(r"\b(GPU|AI factory|data cent(?:er|re))\b", text, re.I)
        if subject:
            metric = "REVENUE_START"
            key = metric + ":" + subject[1].upper().replace(" ", "_")
            note = "A future operating milestone. Require a dated disclosure of revenue commencement for this business."
    if not key:
        return None
    return dict(definition_key=key, topic=statement.topic, commitment=text,
        target_period=target, target_date=deadline, metric=metric, target=values,
        unit=unit, measurement_note=note)


def sync_checkpoints(db, period):
    statements = list(db.scalars(select(ManagementStatement).where(
        ManagementStatement.financial_period_id == period.id).order_by(ManagementStatement.id)))
    for statement in statements:
        guidance = db.scalar(select(ManagementGuidance).where(ManagementGuidance.statement_id == statement.id))
        definition = checkpoint_definition(statement, period, guidance)
        if not definition:
            continue
        if db.scalar(select(InvestorCheckpoint.id).where(
            InvestorCheckpoint.statement_id == statement.id,
            InvestorCheckpoint.definition_key == definition["definition_key"],
        )) is not None:
            continue
        # Reuse the existing evaluator when both a metric and its target period
        # are explicit. Untimed and ambiguous targets remain checkpoints only.
        if guidance is None and definition["target_period"] and definition["metric"] in {"PAT_MARGIN", "EBITDA_MARGIN", "CAPEX"}:
            guidance = ManagementGuidance(statement_id=statement.id, company_id=period.company_id,
                financial_period_id=period.id, metric=definition["metric"], target_period=definition["target_period"],
                lower_bound=Decimal(definition["target"]["lower"]), upper_bound=Decimal(definition["target"]["upper"]),
                unit=definition["unit"], published_at=statement.published_at)
            db.add(guidance)
            db.flush()
        db.add(InvestorCheckpoint(company_id=period.company_id, created_period_id=period.id,
            statement_id=statement.id, guidance_id=guidance.id if guidance else None, **definition))
    db.flush()


def review_as_of(db, period):
    event = db.get(CompanyEvent, period.source_event_id)
    dates = [local_day(event.published_at)]
    dates.extend(local_day(v) for v in db.scalars(select(CommentaryDocument.published_at).where(
        CommentaryDocument.financial_period_id == period.id)))
    return max(dates)


def evaluate_checkpoint(db, checkpoint, period, as_of):
    source = db.get(ManagementStatement, checkpoint.statement_id)
    outcome = {"actual_value": None, "evidence_ids": [], "note": checkpoint.measurement_note or "No comparable verified outcome is available."}
    # A revision must explicitly refer to a prior promise and name its metric.
    # A fresh optimistic statement or unrelated project cannot revise it.
    terms = {"PAT_MARGIN": r"PAT margin", "EBITDA_MARGIN": r"EBITDA margin",
             "CAPEX": r"capex|capital expenditure", "GPU_DEPLOYMENT": r"GPU.*deploy|deploy.*GPU"}
    if checkpoint.metric in terms:
        later = db.scalars(select(ManagementStatement).join(FinancialPeriod,
            ManagementStatement.financial_period_id == FinancialPeriod.id).where(
            ManagementStatement.company_id == period.company_id, FinancialPeriod.scope == period.scope,
            FinancialPeriod.period_end <= period.period_end,
            ManagementStatement.published_at > source.published_at).order_by(ManagementStatement.published_at.desc()))
        for statement in later:
            text = statement.raw_statement
            if local_day(statement.published_at) > as_of or not re.search(terms[checkpoint.metric], text, re.I):
                continue
            if not re.search(r"previous|prior|original", text, re.I):
                continue
            if re.search(r"withdraw|cancel", text, re.I):
                outcome.update(evidence_ids=[statement.evidence_id], note="Management explicitly withdrew the prior commitment.")
                return "WITHDRAWN", outcome
            revised = target_period(text, period)
            revised_end = guidance_target_end(revised) if revised else None
            if re.search(r"delay|postpon|defer", text, re.I) and revised_end and checkpoint.target_date and revised_end > checkpoint.target_date:
                outcome.update(evidence_ids=[statement.evidence_id], revised_target=revised,
                    note="Management explicitly moved the prior deadline later; this is a reported delay, not proof of delivery.")
                return "DELAYED", outcome
    if checkpoint.guidance_id:
        guidance = db.get(ManagementGuidance, checkpoint.guidance_id)
        result = guidance_record(db, guidance, source, as_of)
        outcome.update(actual_value=result["actual_value"], evidence_ids=result["actual_evidence_ids"],
            actual_sources=result["actual_sources"], change=result["change"], comparison=result["actual_comparison"])
        status = {"ACHIEVED": "DELIVERED", "EXCEEDED": "DELIVERED", "MISSED": "MISSED",
            "TOO_EARLY_TO_ASSESS": "PENDING"}.get(result["execution_outcome"], "INSUFFICIENT_EVIDENCE")
        if result["actual_value"] is not None:
            outcome["note"] = "Outcome computed from verified values in the stated period and scope."
        return status, outcome
    if checkpoint.target_date is None:
        return "NOT_YET_ASSESSABLE", outcome
    if checkpoint.metric == "CAPEX_SCOPE_UNCLEAR":
        return "INSUFFICIENT_EVIDENCE", outcome
    return ("PENDING" if as_of < checkpoint.target_date else "NOT_DISCLOSED"), outcome


def checkpoint_review(db, period, *, as_of=None):
    as_of = as_of or review_as_of(db, period)
    owners = list(db.scalars(select(FinancialPeriod).where(
        FinancialPeriod.company_id == period.company_id, FinancialPeriod.scope == period.scope,
        FinancialPeriod.period_end <= period.period_end, FinancialPeriod.status == FinancialStatus.VALIDATED)))
    for owner in owners:
        sync_checkpoints(db, owner)
    rows = list(db.scalars(select(InvestorCheckpoint).join(ManagementStatement,
        InvestorCheckpoint.statement_id == ManagementStatement.id).where(
        InvestorCheckpoint.created_period_id.in_([p.id for p in owners]),
    ).order_by(InvestorCheckpoint.id)))
    items = []
    for row in rows:
        statement = db.get(ManagementStatement, row.statement_id)
        if local_day(statement.published_at) > as_of:
            continue
        status, outcome = evaluate_checkpoint(db, row, period, as_of)
        evaluation = db.scalar(select(CheckpointEvaluation).where(
            CheckpointEvaluation.checkpoint_id == row.id, CheckpointEvaluation.review_period_id == period.id))
        if evaluation is None:
            evaluation = CheckpointEvaluation(checkpoint_id=row.id, review_period_id=period.id,
                as_of_date=as_of, status=status, actual_outcome=outcome)
            db.add(evaluation)
        elif evaluation.status != status or evaluation.actual_outcome != outcome or evaluation.as_of_date != as_of:
            evaluation.status, evaluation.actual_outcome, evaluation.as_of_date = status, outcome, as_of
        items.append({"evidence_id": f"K{row.id}", "kind": "CHECKPOINT_RECORD", "id": row.id,
            "company_id": row.company_id, "scope": period.scope.value, "created_period_id": row.created_period_id,
            "source_statement_id": statement.evidence_id, "commitment": row.commitment,
            "metric": row.metric, "topic": row.topic, "target_period": row.target_period,
            "target_date": row.target_date.isoformat() if row.target_date else None,
            "target": row.target, "unit": row.unit, "status": status, "actual_outcome": outcome,
            "published_at": statement.published_at.isoformat(), "page": statement.page_number,
            "document_id": statement.document_id,
            "document_url": f"/api/documents/{statement.document_id}/file#page={statement.page_number}",
            "measurement_note": row.measurement_note, "is_previous": row.created_period_id != period.id,
            "evaluated_as_of": as_of.isoformat()})
    db.flush()
    return items
