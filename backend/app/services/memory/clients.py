"""Small local HTTP adapters; no paid provider or second LLM framework."""
import math
import re
import httpx
from app.config import settings


class MemoryUnavailable(RuntimeError):
    pass


def match(key, value):
    return {"key": key, "match": {"value": value}}


class OllamaEmbeddings:
    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        prefix = "search_query: " if query else "search_document: "
        try:
            response = httpx.post(
                settings.ollama_base_url.rstrip("/") + "/api/embed",
                json={"model": settings.memory_embedding_model,
                      "input": [prefix + text for text in texts], "truncate": False},
                timeout=settings.memory_timeout_seconds if query else settings.memory_embedding_timeout_seconds,
            )
            response.raise_for_status()
            vectors = response.json()["embeddings"]
            if len(vectors) != len(texts) or any(
                len(v) != settings.memory_embedding_dimensions
                or not all(math.isfinite(x) for x in v) or not any(v) for v in vectors
            ):
                raise ValueError("Invalid embedding dimensions or values")
            return vectors
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise MemoryUnavailable("EMBEDDINGS_UNAVAILABLE") from exc


class QdrantStore:
    def __init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", settings.memory_collection):
            raise ValueError("Invalid memory collection name")
        self.path = "/collections/" + settings.memory_collection

    def request(self, method, path, body=None, *, missing_ok=False):
        try:
            response = httpx.request(method, settings.qdrant_base_url.rstrip("/") + path,
                                     json=body, timeout=settings.memory_timeout_seconds)
            if missing_ok and response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()["result"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise MemoryUnavailable("QDRANT_UNAVAILABLE") from exc

    def info(self):
        return self.request("GET", self.path, missing_ok=True)

    def ensure(self):
        info = self.info()
        if info is None:
            self.request("PUT", self.path, {"vectors": {
                "size": settings.memory_embedding_dimensions, "distance": "Cosine"}})
        else:
            vector = info["config"]["params"]["vectors"]
            if vector.get("size") != settings.memory_embedding_dimensions or vector.get("distance") != "Cosine":
                raise MemoryUnavailable("EMBEDDING_COLLECTION_MISMATCH")
        for key, kind in {"company_id": "integer", "document_id": "integer",
                          "published_day": "integer", "document_type": "keyword",
                          "fiscal_year": "keyword", "quarter": "integer", "scope": "keyword",
                          "topics": "keyword", "revision": "keyword", "embedding_model": "keyword"}.items():
            if info and key in info.get("payload_schema", {}):
                continue
            self.request("PUT", self.path + "/index?wait=true", {"field_name": key, "field_schema": kind})

    def upsert(self, points):
        self.request("PUT", self.path + "/points?wait=true", {"points": points})

    def prune(self, company_id, document_id, revision=None):
        filters = {"must": [match("company_id", company_id), match("document_id", document_id)]}
        if revision:
            filters["must_not"] = [match("revision", revision)]
        self.request("POST", self.path + "/points/delete?wait=true", {"filter": filters})

    def count(self, company_id, document_id, revision):
        result = self.request("POST", self.path + "/points/count", {
            "filter": {"must": [match("company_id", company_id), match("document_id", document_id),
                                match("revision", revision)]}, "exact": True})
        return result["count"]

    def search(self, vector, filters, limit):
        result = self.request("POST", self.path + "/points/query", {
            "query": vector, "filter": filters, "limit": limit,
            "with_payload": True, "with_vector": False,
            "score_threshold": settings.memory_min_score})
        return result["points"]
