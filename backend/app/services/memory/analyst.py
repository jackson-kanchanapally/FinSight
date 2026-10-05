"""Compact historical context for the existing results analyst."""
import re
from decimal import Decimal
from app.schemas.memory import MemoryQuery
from app.services.memory.service import query_memory, local_day


def historical_context(db, period, event, registry, report):
    questions = ["previous management guidance outlook delivery execution strategy recurring risks"]
    categories = {s["category"] for s in report.get("review_packet", {}).get("material_statements", [])}
    if categories & {"COMMITMENT", "EXECUTION_RISK", "CAPITAL_ALLOCATION"}:
        questions.append("previous deployment deadlines capex funding commitments and execution delays")
    metrics = {v.get("metric_code"): Decimal(v["value"]) for v in registry.values() if v["kind"] == "METRIC"}
    if any(value < 0 for code, value in metrics.items() if code and ("MARGIN" in code or "REVENUE" in code)):
        questions.append("management explanations for margin pressure revenue weakness costs demand")
    if report["tested_pe"].get("reverse_expectations"):
        questions.append("growth guidance capacity expansion historical execution supporting required earnings growth")
    result = query_memory(db, period.company_id, MemoryQuery(
        query=". ".join(questions), as_of_date=local_day(event.published_at), limit=4),
        exclude_document_id=period.source_document_id)
    passages = []
    for row in result["results"]:
        if row["document_id"] == period.source_document_id or row.get("scope") not in {None, period.scope.value}:
            continue
        excerpt = re.sub(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?", "[number omitted]", row["text"])
        entry = {"kind": "MEMORY_CHUNK", **row, "excerpt": excerpt,
                 "authority": "DOCUMENTARY_CONTEXT_ONLY", "retrieval_reason": ". ".join(questions)}
        # Raw text remains available in the research view; numeric text cannot authorize model arithmetic.
        entry.pop("text", None)
        registry[row["evidence_id"]] = entry
        passages.append({key: entry[key] for key in (
            "evidence_id", "title", "published_at", "document_id", "page", "section", "scope",
            "speaker", "data_origin", "excerpt")})
    guidance = [item for item in result["structured_evidence"]
                if item["kind"] == "GUIDANCE_RECORD" and item["scope"] == period.scope.value
                and any(p["document_id"] == item["document_id"] and p["page"] == item["page"] for p in passages)]
    for item in guidance:
        registry.setdefault(item["evidence_id"], item)
    return {"status": result["status"], "as_of_date": result["as_of_date"], "passages": passages,
            "structured_guidance": guidance,
            "conflicts": result["conflicts"], "authority": result["authority"],
            "note": "Historical source statements are claims, not verified outcomes. Use structured guidance outcomes to judge delivery."}
