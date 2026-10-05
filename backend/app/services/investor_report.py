"""Assemble traceable commentary, guidance and deterministic verdicts."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.commentary import (
    CommentaryDocument,
    ManagementGuidance,
    ManagementStatement,
)
from app.models.event import CompanyEvent, Document
from app.models.financial import FinancialPeriod
from app.services.commentary import commentary_availability
from app.services.memory.service import guidance_record, local_day
from app.services.investor_verdict import results_verdict
from app.market.expectations import scenario_assessment


def build_investor_report(
    db: Session,
    period: FinancialPeriod,
    registry: dict[str, dict],
    earnings_quality: dict | None = None,
) -> dict:
    associated = list(
        db.scalars(
            select(CommentaryDocument)
            .where(CommentaryDocument.financial_period_id == period.id)
            .order_by(
                CommentaryDocument.source_priority, CommentaryDocument.published_at
            )
        )
    )
    source_documents = []
    for link in associated:
        document = db.get(Document, link.document_id)
        event = db.get(CompanyEvent, link.event_id)
        source_documents.append(
            {
                "document_id": link.document_id,
                "event_id": link.event_id,
                "document_type": link.document_type,
                "source": link.source,
                "published_at": link.published_at.isoformat(),
                "source_url": document.source_url if document else None,
                "document_url": f"/api/documents/{link.document_id}/file",
                "source_priority": link.source_priority,
                "source_references": [
                    {"source": ref.source, "source_url": ref.source_url}
                    for ref in event.references
                ] if event else [],
            }
        )
    statements = list(
        db.scalars(
            select(ManagementStatement)
            .where(ManagementStatement.financial_period_id == period.id)
            .order_by(ManagementStatement.published_at, ManagementStatement.id)
        )
    )
    guidance = list(
        db.scalars(
            select(ManagementGuidance)
            .where(ManagementGuidance.financial_period_id == period.id)
            .order_by(ManagementGuidance.published_at, ManagementGuidance.id)
        )
    )
    result_event = db.get(CompanyEvent, period.source_event_id)
    as_of_date = max([local_day(result_event.published_at)] +
        [local_day(link.published_at) for link in associated])
    metric_registry = {
        item["metric_code"]: (key, Decimal(item["value"]))
        for key, item in registry.items()
        if item.get("kind") == "METRIC"
    }
    valuation_support = []
    valuation_challenges = []
    for code, label in (("REVENUE_YOY", "Revenue"), ("PAT_YOY", "PAT")):
        metric = metric_registry.get(code)
        if metric and metric[1] > 0:
            valuation_support.append(
                {
                    "text": f"{label} grew {metric[1]:.2f}% YoY; durability still needs testing.",
                    "evidence_ids": [metric[0]],
                }
            )
        elif metric and metric[1] < 0:
            valuation_challenges.append(
                {
                    "text": f"{label} fell {abs(metric[1]):.2f}% YoY.",
                    "evidence_ids": [metric[0]],
                }
            )
    revenue_qoq, pat_qoq = metric_registry.get("REVENUE_QOQ"), metric_registry.get(
        "PAT_QOQ"
    )
    if revenue_qoq and pat_qoq and revenue_qoq[1] <= -15 and pat_qoq[1] <= -40:
        valuation_challenges.append(
            {
                "text": "Both revenue and PAT fell sharply QoQ; the cause and seasonal context need verification.",
                "evidence_ids": [revenue_qoq[0], pat_qoq[0]],
            }
        )
    comparisons = []
    for statement in statements:
        evidence = []
        status = "INSUFFICIENT_EVIDENCE"
        if statement.topic in {"REVENUE", "DEMAND", "CUSTOMERS"}:
            metric = next(
                (
                    (key, Decimal(value["value"]))
                    for key, value in registry.items()
                    if value.get("kind") == "METRIC"
                    and value.get("metric_code") == "REVENUE_YOY"
                ),
                None,
            )
            if metric:
                evidence = [metric[0]]
                status = (
                    "NOT_YET_ESTABLISHED"
                    if statement.topic != "REVENUE"
                    else (
                        "PARTIALLY_SUPPORTED"
                        if metric[1] > 0
                        else (
                            "INCONSISTENT_WITH_AVAILABLE_EVIDENCE"
                            if metric[1] < 0
                            and "growth" in statement.raw_statement.lower()
                            else "NOT_YET_ESTABLISHED"
                        )
                    )
                )
        comparisons.append(
            {
                "statement_evidence_id": statement.evidence_id,
                "status": status,
                "financial_evidence_ids": evidence,
                "note": "Management's description is a claim; the cited figures do not establish its cause or durability.",
            }
        )
    guidance_items = []
    for item in guidance:
        statement = db.get(ManagementStatement, item.statement_id)
        record = guidance_record(db, item, statement, as_of_date)
        guidance_items.append(
            {
                "id": item.id,
                "statement_evidence_id": f"S{item.statement_id}",
                "metric": item.metric,
                "target_period": item.target_period,
                "lower_bound": str(item.lower_bound),
                "upper_bound": str(item.upper_bound),
                "unit": item.unit,
                "published_at": item.published_at.isoformat(),
                "change": record["change"],
                "actual_comparison": record["actual_comparison"],
                "execution_outcome": record["execution_outcome"],
            }
        )
    history = []
    all_guidance = db.execute(select(ManagementGuidance, ManagementStatement)
        .join(ManagementStatement, ManagementGuidance.statement_id == ManagementStatement.id)
        .join(FinancialPeriod, ManagementGuidance.financial_period_id == FinancialPeriod.id)
        .where(ManagementGuidance.company_id == period.company_id,
               FinancialPeriod.scope == period.scope,
               ManagementGuidance.published_at <= max([result_event.published_at] +
                   [link.published_at for link in associated]))
        .order_by(ManagementGuidance.published_at.desc(), ManagementGuidance.id.desc())
        .limit(24)).all()
    for row, statement in reversed(all_guidance):
        record = guidance_record(db, row, statement, as_of_date)
        history.append(record)
        registry[record["evidence_id"]] = record
    guidance_changes = []
    for statement in statements:
        if statement.statement_type == "GUIDANCE_WITHDRAWN":
            guidance_changes.append(
                {
                    "status": "GUIDANCE_WITHDRAWN",
                    "statement_evidence_id": statement.evidence_id,
                    "topic": statement.topic,
                    "published_at": statement.published_at.isoformat(),
                }
            )
    statement_items = []
    for item in statements:
        document = db.get(Document, item.document_id)
        statement_items.append(
            {
                "evidence_id": item.evidence_id,
                "statement_type": item.statement_type,
                "topic": item.topic,
                "raw_statement": item.raw_statement,
                "speaker_name": item.speaker_name,
                "speaker_role": item.speaker_role,
                "section_type": item.section_type,
                "source_type": item.source_type,
                "published_at": item.published_at.isoformat(),
                "document_id": item.document_id,
                "page_number": item.page_number,
                "source_url": document.source_url if document else None,
                "document_url": f"/api/documents/{item.document_id}/file#page={item.page_number}",
                "extraction_confidence": item.extraction_confidence,
            }
        )
    assessment = scenario_assessment(db, period.company_id, period.scope)
    results = results_verdict(registry)
    verdict = assessment["verdict"]
    for row in assessment["scenarios"]:
        registry[row["evidence_id"]] = {
            "kind": "SCENARIO",
            "metric_code": row["name"],
            "valuation_verdict": verdict,
            "source_type": row["source_type"],
            "assumptions": {
                key: row[key]
                for key in (
                    "eps_growth_pct",
                    "terminal_pe",
                    "years",
                    "required_return_pct",
                    "source",
                )
            },
            "value": row["implied_current_pe"],
            "unit": "MULTIPLE",
            "reverse": row.get("reverse"),
            "input_evidence_ids": assessment["input_evidence"]
            + row.get("evidence_ids", []),
            "as_of_date": assessment["market_date"],
        }
    if results["verdict"] in {"VERY_NEGATIVE", "NEGATIVE"}:
        stance = "THESIS_REQUIRES_REVIEW"
    elif verdict in {"OVERVALUED", "EXTREMELY_OVERVALUED"}:
        stance = "WAIT_FOR_BETTER_VALUATION"
    elif verdict in {"UNDERVALUED", "EXTREMELY_UNDERVALUED"} and results["verdict"] in {
        "VERY_POSITIVE",
        "POSITIVE",
    }:
        stance = "ATTRACTIVE_FOR_FURTHER_RESEARCH"
    elif verdict == "INSUFFICIENT_EVIDENCE":
        stance = "WAIT_FOR_BETTER_EARNINGS_EVIDENCE"
    else:
        stance = "WATCH_FOR_CONFIRMATION"
    result_text = {
        "VERY_POSITIVE": "The operating comparisons are strongly favorable.",
        "POSITIVE": "The available operating comparisons improved.",
        "NEUTRAL": "The result is mixed: improvement is offset by material weaknesses.",
        "NEGATIVE": "The earnings evidence weakened and warrants a review of the business thesis.",
        "VERY_NEGATIVE": "Material deterioration dominates the operating evidence.",
        "INSUFFICIENT_EVIDENCE": "Comparable operating evidence is too limited for a results judgment.",
    }[results["verdict"]]
    if results["signals"].get("SEQUENTIAL_SIGNAL") == "SEVERE_UNEXPLAINED_DECLINE":
        result_text = "Year-on-year growth is offset by a sharp sequential decline; the history does not yet establish a normal seasonal pattern."
    if (earnings_quality or {}).get("pat_bridge"):
        result_text += " " + earnings_quality["text"]
    range_text = (
        (
            "The dated P/E sits "
            + assessment["current_position"].lower().replace("_", " ")
            + " under the disclosed scenarios. This assessment depends on growth, terminal multiple and return assumptions."
        )
        if assessment.get("supported_pe_range")
        else (
            "The valuation cannot yet be quantified: "
            + "; ".join(assessment["missing"])
            + "."
        )
    )
    gap = assessment["expectations_gap"]["classification"]
    next_text = (
        "I would require evidence of sustained EPS growth and cash conversion before accepting the growth required by this price."
        if gap in {"ABOVE_EVIDENCE", "WELL_ABOVE_EVIDENCE"}
        else "I would monitor the next comparable result, cash conversion and whether management delivers its stated commitments."
    )
    ids = list(
        dict.fromkeys(
            results["evidence_ids"][:5]
            + (earnings_quality or {}).get("evidence_ids", [])
            + (["A_BASE"] if assessment.get("supported_pe_range") else [])
        )
    )
    research_gaps = (
        list((earnings_quality or {}).get("research_gaps", [])) + assessment["missing"]
    )
    if not (assessment.get("historical_pe") or {}).get("one_year"):
        research_gaps.append(
            "Historical valuation coverage is too short to establish a representative P/E range."
        )
    research_gaps.append(
        "Verified comparable peer valuations are unavailable; no peer or quality premium is applied."
    )
    report = {
        "results": results,
        "valuation": {
            "verdict": verdict,
            "confidence": assessment["confidence"],
            "reason": range_text,
        },
        "investor_stance": stance,
        "investor_conclusion": {
            "text": f"{result_text} {range_text} {next_text}",
            "evidence_ids": ids,
        },
        "tested_pe": assessment,
        "earnings_quality": earnings_quality or {},
        "research_gaps": list(dict.fromkeys(research_gaps)),
        "strengthen_case": [
            "Sustained comparable EPS growth with operating cash conversion would strengthen the earnings case.",
            "A lower required growth burden or verified evidence supporting the scenario assumptions would strengthen valuation support.",
        ],
        "weaken_case": [
            "Further profit deterioration or weak cash conversion would weaken the business case.",
            "Growth below the explicit scenario path, without a compensating valuation change, would weaken the valuation case.",
        ],
        "source_documents": source_documents,
        "valuation_support": valuation_support,
        "valuation_challenges": valuation_challenges,
        "management_statements": statement_items,
        "management_guidance": guidance_items,
        "management_execution_history": history,
        "commentary_availability": commentary_availability(db, period),
        "guidance_changes": guidance_changes,
        "claim_comparisons": comparisons,
    }
    from app.services.quarterly_review import build_review
    report.update(build_review(db, period, registry, report))
    return report
