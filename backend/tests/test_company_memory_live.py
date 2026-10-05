"""Opt-in retrieval evaluation with real local embeddings + a disposable Qdrant collection.

No production documents or financial facts are created. Generation is not involved.
"""
import os
from datetime import date
from uuid import uuid4
import pytest
from app.config import settings
from app.schemas.memory import MemoryQuery
from app.services.memory.clients import QdrantStore
from app.services.memory.service import index_company, query_memory
from tests.test_company_memory import EVALUATION, document
from tests.test_market_valuation import market_db, company

pytestmark = pytest.mark.skipif(os.getenv("RUN_MEMORY_LIVE") != "1", reason="Requires local Qdrant and nomic-embed-text")


def test_real_embeddings_retrieval_and_temporal_isolation(market_db, monkeypatch):
    monkeypatch.setattr(settings, "memory_collection", "almonds_eval_" + uuid4().hex)
    issuer, other = company(market_db, "EVAL_A"), company(market_db, "EVAL_B")
    expected = {}
    for case in EVALUATION:
        expected[case["topic"]] = document(market_db, issuer, case["text"]).id
    document(market_db, issuer, EVALUATION[0]["text"], day="2030-01-01")
    document(market_db, other, EVALUATION[0]["text"])
    store = QdrantStore()
    try:
        for c in (issuer, other):
            assert index_company(market_db, c.id)["status"] == "READY"
        for case in EVALUATION:
            response = query_memory(market_db, issuer.id, MemoryQuery(query=case["question"], as_of_date=date(2026, 1, 1)))
            assert response["status"] == "READY"
            assert response["results"][0]["document_id"] == expected[case["topic"]]
            assert all(p["company_id"] == issuer.id and p["published_day"] <= 20260101 for p in response["results"])
        assert not query_memory(market_db, issuer.id, MemoryQuery(query="management guidance", as_of_date=date(2020, 1, 1)))["results"]
        assert all(row["status"] == "UNCHANGED" for row in index_company(market_db, issuer.id)["documents"])
    finally:
        store.request("DELETE", store.path, missing_ok=True)
