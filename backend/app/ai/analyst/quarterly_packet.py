"""A compact, typed view of existing authoritative investor evidence for Qwen."""

import json


FACTS = {"REVENUE_FROM_OPERATIONS", "EBITDA", "PAT", "PBT", "TOTAL_TAX_EXPENSE",
         "BASIC_EPS", "DILUTED_EPS", "OPERATING_CASH_FLOW", "FREE_CASH_FLOW",
         "TOTAL_DEBT", "CASH_AND_EQUIVALENTS", "CAPITAL_EXPENDITURE"}
METRICS = {"REVENUE_YOY", "REVENUE_QOQ", "PAT_YOY", "PAT_QOQ", "EPS_YOY",
           "EPS_QOQ", "EBITDA_MARGIN", "EBITDA_MARGIN_CHANGE_BPS", "PAT_MARGIN",
           "EFFECTIVE_TAX_RATE", "PBT_QOQ", "PBT_YOY", "TAX_QOQ", "TAX_YOY",
           "EBITDA_PROXY", "EBITDA_PROXY_MARGIN", "NET_DEBT", "DEBT_EQUITY", "OCF_PAT", "FREE_CASH_FLOW"}


def quarterly_packet(context) -> tuple[str, list[str], int]:
    """Preserve database evidence IDs and source locators while removing duplicate prose.

    The full registry and investor report still serve validation and drill-down.
    A manually entered reference P/E is deliberately absent from this packet:
    the Python-calculated P/E is the only ratio used for interpretation.
    """
    original = json.loads(context.text)
    report = context.investor_report
    registry = context.registry
    selected = report.get("review_packet", {})
    facts = [dict({k: row[k] for k in ("evidence_id", "metric_code", "value", "unit") if k in row},
             period_end=registry[row["evidence_id"]].get("period_end"),
             scope=registry[row["evidence_id"]].get("scope"),
             page=registry[row["evidence_id"]].get("page"),
             source_document_id=registry[row["evidence_id"]].get("document_id"))
             for row in original.get("verified_facts", []) if row["metric_code"] in FACTS]
    metrics = [{k: row[k] for k in ("evidence_id", "metric_code", "value", "unit", "formula", "definition") if k in row}
               for row in original.get("deterministic_metrics", []) if row["metric_code"] in METRICS]
    seasonality = [{"evidence_id": key, "metric_code": item.get("metric_code"),
                    "status": item.get("status"), "historical_count": item.get("historical_count")}
                   for key, item in registry.items() if item.get("kind") == "SEASONALITY"]
    statements = []
    for row in selected.get("material_statements", [])[:12]:
        source = registry.get(row["evidence_id"])
        if source is None:
            continue
        statements.append({"evidence_id": row["evidence_id"], "kind": "MANAGEMENT_CLAIM",
            "materiality": row["category"], "tier": row.get("tier"),
            "priority_factors": row.get("priority_factors", []), "speaker": source.get("speaker_name"),
            "topic": source.get("topic"), "statement_type": source.get("statement_type"),
            "page": source.get("page"), "statement": row["source_quote"]})
    current_statement_ids = {row["evidence_id"] for row in statements}
    checkpoints = []
    for row in selected.get("checkpoints", []):
        source = registry.get(row["evidence_id"], {})
        checkpoints.append({k: value for k, value in {
            "evidence_id": row["evidence_id"], "source_statement_id": row["source_statement_id"],
            "commitment": source.get("commitment") if row["source_statement_id"] not in current_statement_ids else None,
            "metric": row["metric"],
            "target_period": row["target_period"], "target_date": row["target_date"],
            "target": row["target"], "unit": row["unit"], "status": row["status"],
            "actual_outcome": row["actual_outcome"], "is_previous": row["is_previous"],
            "measurement_note": row["measurement_note"] if row["status"] in
                {"NOT_YET_ASSESSABLE", "INSUFFICIENT_EVIDENCE", "NOT_DISCLOSED"} else None,
        }.items() if value is not None and value != {} and value != []})
    memory = original.get("company_memory") or {}
    passages = [{k: row[k] for k in ("evidence_id", "published_at", "page", "scope", "excerpt") if k in row}
                for row in memory.get("passages", [])[:3] if row["evidence_id"] in registry]
    tested = report.get("tested_pe") or {}
    valuation = {
        "market_date": tested.get("market_date"),
        "calculated_pe": {"evidence_id": next((key for key, item in registry.items()
            if item.get("kind") == "VALUATION" and item.get("metric_code") == "PE_TTM"), None),
            "value": tested.get("current_pe")},
        "tested_pe_range": tested.get("supported_pe_range"),
        "reverse_expectations": {"evidence_id": "A_BASE" if "A_BASE" in registry else None,
            "required_eps_cagr_pct": (tested.get("reverse_expectations") or {}).get("required_eps_cagr_pct"),
            "assumption_not_forecast": True},
        "scenarios": [{k: row[k] for k in ("evidence_id", "name", "eps_growth_pct", "terminal_pe",
            "years", "required_return_pct", "implied_current_pe") if k in row}
            for row in tested.get("scenarios", [])],
        "deterministic_valuation_verdict": report["valuation"]["verdict"],
        "verdict_confidence": report["valuation"]["confidence"],
        "assumption_credibility_status": selected.get("valuation_implication_status", "INSUFFICIENT_EVIDENCE"),
        "interpretation_rule": "Calculated P/E and disclosed scenario range are Python outputs."
            " Required growth is an assumption, not a forecast. Do not use an entered reference P/E.",
    }
    packet = {
        "company": original["company"], "symbol": original["symbol"], "period": original["period"],
        "source": {k: original["source"].get(k) for k in ("origin", "document_id", "published_at")},
        "source_verification": report.get("source_verification"),
        "result_freshness": report.get("result_freshness"),
        "evidence_confidence": report.get("evidence_confidence"),
        "previous_thesis": report.get("previous_thesis"),
        "verified_facts": facts, "deterministic_metrics": metrics,
        "seasonality_history": seasonality,
        "earnings_quality": {"text": report["earnings_quality"].get("text"),
            "evidence_ids": report["earnings_quality"].get("evidence_ids", []),
            "basis": report["earnings_quality"].get("basis"),
            "research_gaps": report["earnings_quality"].get("research_gaps", [])[:2]},
        "quantified_profit_bridge": report["earnings_quality"].get("pat_bridge", []),
        "reconciliation": report["earnings_quality"].get("reconciliation", {}),
        "results_verdict": {k: report["results"][k] for k in ("verdict", "reasons", "evidence_ids")},
        "management_statements": statements, "checkpoints": checkpoints,
        "historical_memory": {"as_of_date": memory.get("as_of_date"), "passages": passages,
            "guidance": memory.get("structured_guidance", [])[:3]},
        "supported_changes": selected.get("changes", [])[:6],
        "history_coverage": selected.get("history_note"),
        "execution_summary": selected.get("execution_summary"),
        "valuation": valuation,
        "research_gaps": {"evidence_id": "I_REVIEW", "items": report.get("research_gaps", [])[:4]},
        "evidence_rules": {
            "verified_fact": "Only facts/metrics with F, M or J IDs are observed results.",
            "management_claim": "S statements and K targets are disclosures, not verified outcomes.",
            "investor_interpretation": "Connect cited facts and claims conditionally; do not invent a new result or cause.",
            "causal_attribution": "Management explanations require attribution and an exact quote."
                " A numeric PAT bridge can explain arithmetic contributions, not an operating cause.",
        },
    }
    evidence_ids = list(dict.fromkeys(
        [row["evidence_id"] for row in facts + metrics + seasonality + statements + checkpoints + passages]
        + ["I_REVIEW"] + [row["evidence_id"] for row in valuation["scenarios"]]
        + [valuation["calculated_pe"]["evidence_id"]]
        + report["earnings_quality"].get("evidence_ids", [])
        + report["results"].get("evidence_ids", [])
        + [key for row in selected.get("changes", []) for key in row.get("evidence_ids", [])]
        + [key for row in checkpoints for key in row.get("actual_outcome", {}).get("evidence_ids", [])]))
    evidence_ids = [key for key in evidence_ids if key and key in registry]
    return json.dumps(packet, ensure_ascii=False, separators=(",", ":")), evidence_ids, len(passages)
