"""Inspectable, deterministic checks for an ESDS financial analysis."""

import re

from app.ai.analyst.schemas import AnalystOutput
from app.ai.analyst.validator import InvalidAnalysis, validate_output


def evaluate_esds(
    output: AnalystOutput,
    registry: dict[str, dict],
    period_year: int,
    expected_scope: str = "CONSOLIDATED",
) -> dict:
    text = " ".join(
        [
            output.summary,
            *(
                item.title + " " + item.explanation
                for item in output.positives + output.concerns
            ),
            *(
                item.metric + " " + item.change + " " + item.interpretation
                for item in output.material_changes
            ),
            *output.questions,
            *output.data_quality_notes,
        ]
    ).lower()
    metric_codes = {
        item["metric_code"] for item in registry.values() if item["kind"] == "METRIC"
    }
    checks = {
        "revenue_change_explained": "revenue" in text
        and ("yoy" in text or "year" in text),
        "pat_change_explained": "pat" in text or "profit after tax" in text,
        "ebitda_margin_discussed": "EBITDA_MARGIN" not in metric_codes
        or "ebitda margin" in text,
        "improvement_identified": bool(output.positives),
        "deterioration_identified": bool(output.concerns),
        "missing_information_question": bool(output.questions),
        "every_major_claim_has_evidence": bool(output.summary_evidence_ids)
        and all(
            item.evidence_ids
            for item in [*output.positives, *output.concerns, *output.material_changes]
        ),
        "no_scope_confusion": (
            "standalone" not in text
            if expected_scope == "CONSOLIDATED"
            else "consolidated" not in text
        ),
        "no_recommendation": re.search(r"\b(?:buy|sell|hold)\b", text) is None,
    }
    try:
        validate_output(output.model_dump_json(), registry, period_year)
        checks["citations_and_numbers_grounded"] = True
    except InvalidAnalysis:
        checks["citations_and_numbers_grounded"] = False
    # Require named deterministic comparison IDs when discussing these changes.
    comparisons = {
        item["metric_code"]: key
        for key, item in registry.items()
        if item["kind"] == "METRIC"
    }
    claims = [*output.positives, *output.concerns, *output.material_changes]
    for code in ("REVENUE_YOY", "REVENUE_QOQ", "PAT_YOY", "PAT_QOQ"):
        if code in comparisons:
            name, direction = code.split("_")
            relevant = [
                claim
                for claim in claims
                if name.lower()
                in (
                    claim.title + " " + claim.explanation
                    if hasattr(claim, "title")
                    else claim.metric + " " + claim.change + " " + claim.interpretation
                ).lower()
                and direction.lower()
                in (
                    claim.title + " " + claim.explanation
                    if hasattr(claim, "title")
                    else claim.metric + " " + claim.change + " " + claim.interpretation
                ).lower()
            ]
            checks[f"{code.lower()}_cited"] = not relevant or all(
                comparisons[code] in claim.evidence_ids for claim in relevant
            )
    return {
        "case": "ESDS_FINANCIAL_RESULTS",
        "checks": checks,
        "passed": sum(checks.values()),
        "total": len(checks),
        "all_passed": all(checks.values()),
    }
