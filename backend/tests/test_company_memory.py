"""Retrieval is evaluated independently of generation; all test documents are SQLite-only."""
import copy
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.main import app
from app.ai.analyst.context_builder import build_context
from app.ai.analyst.fallback import verified_fallback
from app.ai.analyst.validator import InvalidAnalysis, validate_output
from app.models.event import CompanyEvent, Document, DocumentPage, EventType, DataOrigin, ProcessingStatus
from app.models.memory import DocumentMemoryIndex
from app.models.financial import FinancialScope, MetricCode, PeriodType
from app.models.commentary import ManagementStatement, ManagementGuidance
from app.schemas.memory import MemoryQuery
from app.services.memory.chunking import chunk_page
from app.services.memory.clients import MemoryUnavailable, QdrantStore, OllamaEmbeddings
from app.services.memory.service import index_company, query_memory, memory_status
from app.services.financials.service import calculate_company_metrics
from tests.test_market_valuation import market_db, company, add_quarter
from tests.test_ai_analyst import FakeProvider
from app.ai.analyst.service import analyze_period

EVALUATION = json.loads((Path(__file__).parent / "fixtures/memory_evaluation.json").read_text())


class Embeddings:
    """Deterministic test-only vectors, independent of Qwen."""
    calls = 0
    def embed(self, texts, *, query=False):
        self.calls += 1
        words = [r"guid\w*|outlook|expect\w*", r"margin\w*|profitability|power|pressure",
                 r"risk\w*|concentration|liquidity", r"strateg\w*|expansion|capex|capacity"]
        return [[len(re.findall(pattern, text, re.I)) for pattern in words] for text in texts]


class Store:
    def __init__(self):
        self.points = {}
        self.ignore_filters = False
    def ensure(self): pass
    def info(self): return {"ready": True}
    def upsert(self, points):
        self.points.update({p["id"]: copy.deepcopy(p) for p in points})
    def prune(self, company_id, document_id, revision=None):
        self.points = {k: p for k, p in self.points.items() if not (
            p["payload"]["company_id"] == company_id and p["payload"]["document_id"] == document_id
            and (revision is None or p["payload"]["revision"] != revision))}
    def count(self, company_id, document_id, revision):
        return sum(p["payload"]["company_id"] == company_id and p["payload"]["document_id"] == document_id
                   and p["payload"]["revision"] == revision for p in self.points.values())
    def search(self, vector, filters, limit):
        def match(p, condition):
            value = p.get(condition["key"])
            if "match" in condition:
                if "any" in condition["match"]:
                    return isinstance(value, list) and bool(set(value) & set(condition["match"]["any"]))
                wanted = condition["match"]["value"]
                return wanted in value if isinstance(value, list) else wanted == value
            return value is not None and all(value <= n if op == "lte" else value >= n
                                             for op, n in condition["range"].items())
        result = []
        for p in self.points.values():
            if not self.ignore_filters and (not all(match(p["payload"], c) for c in filters["must"])
                or any(match(p["payload"], c) for c in filters.get("must_not", []))):
                continue
            norm = math.sqrt(sum(x*x for x in vector) * sum(x*x for x in p["vector"]))
            score = sum(a*b for a, b in zip(vector, p["vector"])) / norm if norm else 0
            if score >= settings.memory_min_score:
                result.append({"id": p["id"], "payload": copy.deepcopy(p["payload"]), "score": score})
        return sorted(result, key=lambda p: (-p["score"], p["id"]))[:limit]


def document(db, issuer, text, *, day="2025-07-20", kind=EventType.EARNINGS_CALL, origin=DataOrigin.MANUAL):
    event = CompanyEvent(company_id=issuer.id, event_type=kind, title="Company research document",
        published_at=datetime.fromisoformat(day).replace(tzinfo=timezone.utc), source="MANUAL_UPLOAD",
        source_url="manual://test", data_origin=origin, fingerprint=uuid4().hex,
        processing_status=ProcessingStatus.PROCESSED, fiscal_year="FY2026", quarter=1)
    db.add(event); db.flush()
    doc = Document(company_id=issuer.id, event_id=event.id, source=event.source, source_url=event.source_url,
                   document_url=event.source_url, processing_status=ProcessingStatus.PROCESSED)
    db.add(doc); db.flush()
    db.add(DocumentPage(document_id=doc.id, page_number=1, text=text)); db.commit()
    return doc


def test_chunking_preserves_headings_speakers_and_table_rows():
    chunks = chunk_page("OUTLOOK\nCFO: We expect capacity expansion to support enterprise revenue.\n\n"
        "Analyst: What risks threaten the investment programme?\nRISK FACTORS\n"
        "Customer concentration remains a material risk to collections and liquidity.", 400)
    assert chunks[0].section == "OUTLOOK" and chunks[0].speaker == "CFO"
    assert chunks[1].speaker == "Analyst" and "CFO:" not in chunks[1].text
    assert chunks[2].section == "RISK FACTORS" and chunks[2].speaker is None
    assert chunks[2].topics == ["RISKS", "CASH_FLOW"]
    table = chunk_page("SEGMENT RESULTS\nAmounts INR crore; Quarter ended June\n" + "Enterprise revenue | 200 | 180\n" * 40, 400)
    assert len(table) > 1 and all(len(c.text) <= 400 and c.section == "SEGMENT RESULTS" for c in table)
    assert all("Enterprise revenue | 200 | 180" in c.text for c in table)
    assert all("Amounts INR crore; Quarter ended June" in c.text for c in table)


def test_metadata_idempotence_updates_and_manual_provenance(market_db):
    issuer = company(market_db)
    doc = document(market_db, issuer, EVALUATION[0]["text"])
    store, embedder = Store(), Embeddings()
    first = index_company(market_db, issuer.id, store=store, embedder=embedder)
    original_ids = set(store.points)
    assert first["status"] == "READY" and original_ids
    p = next(iter(store.points.values()))["payload"]
    assert p["company_id"] == issuer.id and p["document_id"] == doc.id
    assert p["page_id"] and p["page"] == 1 and p["event_id"] == doc.event_id
    assert p["data_origin"] == "MANUAL" and p["fiscal_year"] == "FY2026" and p["quarter"] == 1
    assert p["section"] == "OUTLOOK" and p["speaker"] == "CFO" and p["scope"] is None
    second = index_company(market_db, issuer.id, store=store, embedder=embedder)
    assert second["documents"][0]["status"] == "UNCHANGED" and set(store.points) == original_ids
    assert embedder.calls == 1
    page = market_db.scalar(select(DocumentPage).where(DocumentPage.document_id == doc.id))
    page.text = EVALUATION[1]["text"]; market_db.commit()
    assert memory_status(market_db, issuer.id, store=store)["documents"][0]["status"] == "STALE"
    assert not query_memory(market_db, issuer.id, MemoryQuery(query="guidance outlook"), store=store, embedder=embedder)["results"]
    index_company(market_db, issuer.id, store=store, embedder=embedder)
    assert not set(store.points) & original_ids
    index_company(market_db, issuer.id, store=store, embedder=embedder, force=True)
    assert len(store.points) == 1


@pytest.mark.parametrize("case", EVALUATION, ids=lambda c: c["topic"])
def test_deterministic_retrieval_evaluation(market_db, case):
    issuer = company(market_db)
    expected = {}
    for item in EVALUATION:
        doc = document(market_db, issuer, item["text"])
        expected[item["topic"]] = doc.id
    store, embedder = Store(), Embeddings()
    index_company(market_db, issuer.id, store=store, embedder=embedder)
    found = query_memory(market_db, issuer.id, MemoryQuery(query=case["question"]), store=store, embedder=embedder)
    assert found["results"][0]["document_id"] == expected[case["topic"]]
    assert all(row["page_id"] and row["evidence_id"].startswith("C") and row["document_url"].endswith("#page=1") for row in found["results"])


def test_company_type_topic_fiscal_and_temporal_filters_defend_against_store(market_db):
    issuer, other = company(market_db, "ONE"), company(market_db, "TWO")
    text = EVALUATION[0]["text"]
    old = document(market_db, issuer, text, day="2025-07-20", kind=EventType.INVESTOR_PRESENTATION)
    document(market_db, issuer, text, day="2026-07-20")
    document(market_db, other, text, day="2025-07-20")
    store, embedder = Store(), Embeddings()
    for c in (issuer, other): index_company(market_db, c.id, store=store, embedder=embedder)
    store.ignore_filters = True  # even a broken/misconfigured backend cannot cross the boundary
    request = MemoryQuery(query="management guidance", as_of_date=date(2025, 7, 20),
        date_from=date(2025, 7, 20), document_type="INVESTOR_PRESENTATION", topic="GUIDANCE", fiscal_year="FY2026", quarter=1)
    result = query_memory(market_db, issuer.id, request, store=store, embedder=embedder)
    assert [r["document_id"] for r in result["results"]] == [old.id]
    before = request.model_copy(update={"as_of_date": date(2025, 7, 19), "date_from": None})
    assert not query_memory(market_db, issuer.id, before, store=store, embedder=embedder)["results"]


def test_duplicate_suppression_and_tampered_evidence(market_db):
    issuer = company(market_db)
    for _ in range(3): document(market_db, issuer, EVALUATION[0]["text"])
    store, embedder = Store(), Embeddings()
    index_company(market_db, issuer.id, store=store, embedder=embedder)
    request = MemoryQuery(query="management guidance")
    found = query_memory(market_db, issuer.id, request, store=store, embedder=embedder)
    assert len(found["results"]) == 1
    for p in store.points.values(): p["payload"]["text"] = "Invented guidance from a different document."
    found = query_memory(market_db, issuer.id, request, store=store, embedder=embedder)
    assert not found["results"] and found["stale_candidates_rejected"] == 3


def test_failed_reindex_never_activates_partial_revision_and_can_retry(market_db):
    issuer = company(market_db)
    doc = document(market_db, issuer, EVALUATION[0]["text"])
    store, embedder = Store(), Embeddings()
    index_company(market_db, issuer.id, store=store, embedder=embedder)
    original = market_db.get(DocumentMemoryIndex, doc.id).revision
    page = market_db.scalar(select(DocumentPage).where(DocumentPage.document_id == doc.id))
    page.text += "\nAdditional guidance concerns operating cash flow and liquidity."; market_db.commit()
    class Broken(Embeddings):
        def embed(self, *args, **kwargs): raise MemoryUnavailable("EMBEDDINGS_UNAVAILABLE")
    result = index_company(market_db, issuer.id, store=store, embedder=Broken())
    assert result["status"] == "PARTIAL" and market_db.get(DocumentMemoryIndex, doc.id).revision == original
    assert not query_memory(market_db, issuer.id, MemoryQuery(query="guidance"), store=store, embedder=embedder)["results"]
    assert index_company(market_db, issuer.id, store=store, embedder=embedder)["status"] == "READY"
    assert len(store.points) == 1


def test_unparsed_and_fixture_documents_are_not_indexed(market_db):
    issuer = company(market_db)
    document(market_db, issuer, EVALUATION[0]["text"], origin=DataOrigin.FIXTURE)
    broken = document(market_db, issuer, EVALUATION[0]["text"])
    broken.processing_status = ProcessingStatus.FAILED; market_db.commit()
    store = Store()
    result = index_company(market_db, issuer.id, store=store, embedder=Embeddings())
    assert all(row["status"] == "SKIPPED" for row in result["documents"]) and not store.points


def test_qdrant_unavailable_keeps_financial_analysis_usable(market_db, monkeypatch):
    issuer = company(market_db)
    period = add_quarter(market_db, issuer, date(2026, 6, 30), date(2026, 7, 20))
    document(market_db, issuer, EVALUATION[0]["text"])
    store, embedder = Store(), Embeddings()
    index_company(market_db, issuer.id, store=store, embedder=embedder)
    def unavailable(*args): raise MemoryUnavailable("QDRANT_UNAVAILABLE")
    monkeypatch.setattr(QdrantStore, "info", unavailable)
    result = query_memory(market_db, issuer.id, MemoryQuery(query="guidance"))
    assert result["status"] == "UNAVAILABLE" and not result["results"]
    context = build_context(market_db, issuer.name, issuer.symbol, period)
    assert context.fact_count and context.investor_report and verified_fallback(context.registry, period, context.reasoning)


def test_analyst_uses_historical_passages_and_validates_quotes(market_db, monkeypatch):
    from app.services.memory import analyst
    issuer = company(market_db)
    period = add_quarter(market_db, issuer, date(2026, 6, 30), date(2026, 7, 20))
    old = document(market_db, issuer, EVALUATION[0]["text"])
    document(market_db, issuer, EVALUATION[0]["text"], day="2026-08-01")
    store, embedder = Store(), Embeddings()
    index_company(market_db, issuer.id, store=store, embedder=embedder)
    monkeypatch.setattr(analyst, "query_memory", lambda *a, **kw: query_memory(*a, **kw, store=store, embedder=embedder))
    context = build_context(market_db, issuer.name, issuer.symbol, period)
    chunks = {key: v for key, v in context.registry.items() if v["kind"] == "MEMORY_CHUNK"}
    assert chunks and {v["document_id"] for v in chunks.values()} == {old.id}
    key, row = next(iter(chunks.items()))
    output = verified_fallback(context.registry, period, context.reasoning).model_dump()
    output["historical_context"] = [{"text": "Management previously described a growth outlook; delivery remains unestablished.",
        "source_quote": row["excerpt"][:80], "evidence_ids": [key]}]
    assert validate_output(json.dumps(output), context.registry, 2026).historical_context
    provider = FakeProvider([json.dumps(output)])
    analysis = analyze_period(market_db, issuer, period, provider=provider)
    assert analysis.status == "SUCCESS"
    assert key in analysis.evidence_registry and analysis.structured_output["historical_context"]
    assert "company_memory" in provider.prompts[0] and key in provider.schemas[0]["properties"]["summary_evidence_ids"]["items"]["enum"]
    output["historical_context"][0]["source_quote"] = "Management achieved every growth target."
    with pytest.raises(InvalidAnalysis, match="HISTORICAL_QUOTE_NOT_IN_SOURCE"):
        validate_output(json.dumps(output), context.registry, 2026)
    output["historical_context"][0].update(source_quote=row["excerpt"][:80], text="Management repeatedly missed its guidance.")
    with pytest.raises(InvalidAnalysis, match="HISTORICAL_EXECUTION_UNSUPPORTED"):
        validate_output(json.dumps(output), context.registry, 2026)
    output["historical_context"][0]["evidence_ids"] = ["Cnonexistent"]
    with pytest.raises(InvalidAnalysis, match="UNKNOWN_EVIDENCE_ID"):
        validate_output(json.dumps(output), context.registry, 2026)


def test_guidance_conflict_surfaces_structured_precedence(market_db):
    issuer = company(market_db)
    period = add_quarter(market_db, issuer, date(2026, 6, 30), date(2026, 7, 20))
    doc = document(market_db, issuer, "CFO: We expect revenue growth of 25-30% in FY2027 from enterprise demand.")
    statement = ManagementStatement(company_id=issuer.id, financial_period_id=period.id, event_id=doc.event_id,
        document_id=doc.id, page_number=1, statement_type="GUIDANCE", topic="REVENUE",
        raw_statement="We expect revenue growth of 10-15% in FY2027.", normalized_summary="Guidance",
        published_at=doc.event.published_at, source_type="MANUAL", extraction_confidence="HIGH")
    market_db.add(statement); market_db.flush()
    guidance = ManagementGuidance(statement_id=statement.id, company_id=issuer.id, financial_period_id=period.id,
        metric="REVENUE_GROWTH", target_period="FY27", lower_bound=10, upper_bound=15, unit="PERCENT", published_at=doc.event.published_at)
    market_db.add(guidance); market_db.commit()
    store, embedder = Store(), Embeddings()
    index_company(market_db, issuer.id, store=store, embedder=embedder)
    result = query_memory(market_db, issuer.id, MemoryQuery(query="guidance outlook revenue"), store=store, embedder=embedder)
    assert result["conflicts"][0]["type"] == "GUIDANCE_SOURCE_DISAGREEMENT"
    assert result["structured_evidence"][0]["lower_bound"] == "10"
    assert guidance.lower_bound == 10  # retrieval never writes source-of-truth guidance


def test_api_rejects_company_override_and_invalid_date_range(market_db):
    issuer = company(market_db)
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            path = f"/api/companies/{issuer.symbol}/memory/query"
            assert client.post(path, json={"query": "guidance", "company_id": 999}).status_code == 422
            assert client.post(path, json={"query": "guidance", "date_from": "2026-01-02", "as_of_date": "2026-01-01"}).status_code == 422
            assert client.post(path, json={"query": "guidance"}).json()["status"] == "NOT_INDEXED"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_embedding_client_uses_query_prefix_and_rejects_truncation(monkeypatch):
    import httpx
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(200, json={"embeddings": [[1.0] + [0.0] * 767]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    OllamaEmbeddings().embed(["guidance"], query=True)
    assert calls[0]["input"] == ["search_query: guidance"] and calls[0]["truncate"] is False


def test_boilerplate_and_single_name_transcript_turns():
    assert not chunk_page("This document contains forward-looking statements regarding management expectations and strategy.")
    chunks = chunk_page("Q1. What explains profitability?\nAlbinder: Margin pressure followed higher delivery costs.\n\nAkshant: Capacity utilization remains below our internal expectations.")
    assert [c.speaker for c in chunks] == ["Albinder", "Akshant"]
    assert all(c.section == "Q1. What explains profitability?" for c in chunks)
    assert not chunk_page("Digitally signed by CHIEF FINANCIAL OFFICER Date: 2026.09.24")
    wrapped = chunk_page("Q2. What changed in margins\nover the last reporting period?\n\nAkshant: Profitability reflected our continued investment in new capacity.")
    assert len(wrapped) == 1 and wrapped[0].section == "Q2. What changed in margins over the last reporting period?"


def test_no_guidance_match_from_call_invitation(market_db):
    issuer = company(market_db)
    document(market_db, issuer, "The management invites investors to participate in our earnings conference call. Access details are provided below.")
    store, embedder = Store(), Embeddings()
    index_company(market_db, issuer.id, store=store, embedder=embedder)
    result = query_memory(market_db, issuer.id, MemoryQuery(query="What did management guide?"), store=store, embedder=embedder)
    assert result["status"] == "NO_MATCHES"


def test_historical_guidance_outcome_uses_only_available_same_scope_annual_actuals(market_db):
    issuer = company(market_db)
    prior = add_quarter(market_db, issuer, date(2025, 3, 31), date(2025, 5, 20), period_type=PeriodType.ANNUAL)
    current = add_quarter(market_db, issuer, date(2026, 3, 31), date(2026, 5, 20), period_type=PeriodType.ANNUAL)
    standalone = add_quarter(market_db, issuer, date(2026, 3, 31), date(2026, 4, 20),
                             period_type=PeriodType.ANNUAL, scope=FinancialScope.STANDALONE)
    for fact in current.facts:
        if fact.metric_code == MetricCode.REVENUE_FROM_OPERATIONS: fact.value = 120
    market_db.commit()
    calculate_company_metrics(market_db, issuer.id)
    doc = document(market_db, issuer, "CFO: We expect revenue growth of 10-15% in FY2026.", day="2025-07-20")
    statement = ManagementStatement(company_id=issuer.id, financial_period_id=prior.id, event_id=doc.event_id,
        document_id=doc.id, page_number=1, statement_type="GUIDANCE", topic="REVENUE",
        raw_statement="We expect revenue growth of 10-15% in FY2026.", normalized_summary="Guidance",
        published_at=doc.event.published_at, source_type="MANUAL", extraction_confidence="HIGH")
    market_db.add(statement); market_db.flush()
    market_db.add(ManagementGuidance(statement_id=statement.id, company_id=issuer.id, financial_period_id=prior.id,
        metric="REVENUE_GROWTH", target_period="FY26", lower_bound=10, upper_bound=15, unit="PERCENT", published_at=doc.event.published_at))
    market_db.commit()
    store, embedder = Store(), Embeddings()
    index_company(market_db, issuer.id, store=store, embedder=embedder)
    def lookup(day):
        return query_memory(market_db, issuer.id, MemoryQuery(query="growth guidance", as_of_date=day), store=store, embedder=embedder)["structured_evidence"][0]
    assert lookup(date(2026, 5, 19))["execution_outcome"] == "INSUFFICIENT_DATA"
    observed = lookup(date(2026, 5, 20))
    assert observed["execution_outcome"] == "EXCEEDED" and observed["actual_value"] == "20.000000"
    assert observed["actual_evidence_ids"] and observed["actual_sources"]
    assert standalone.id not in [s["period_id"] for s in observed["actual_sources"]]


def test_identical_risk_disclosures_on_distinct_dates_preserve_history(market_db):
    issuer = company(market_db)
    for day in ("2024-07-20", "2025-07-20"):
        document(market_db, issuer, EVALUATION[2]["text"], day=day)
    store, embedder = Store(), Embeddings()
    index_company(market_db, issuer.id, store=store, embedder=embedder)
    result = query_memory(market_db, issuer.id, MemoryQuery(query="recurring concentration risks"), store=store, embedder=embedder)
    assert len(result["results"]) == 2 and len({r["published_day"] for r in result["results"]}) == 2


def test_reindex_repairs_missing_vectors(market_db):
    issuer = company(market_db)
    document(market_db, issuer, EVALUATION[0]["text"])
    store, embedder = Store(), Embeddings()
    index_company(market_db, issuer.id, store=store, embedder=embedder)
    ids = set(store.points)
    store.points.clear()
    assert index_company(market_db, issuer.id, store=store, embedder=embedder)["documents"][0]["status"] == "INDEXED"
    assert set(store.points) == ids
