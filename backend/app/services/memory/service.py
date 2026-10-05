"""One retrieval boundary for API and analyst. PostgreSQL owns identity and authority."""
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.commentary import CommentaryDocument, ManagementGuidance, ManagementStatement
from app.models.event import CompanyEvent, Document, DocumentPage, DataOrigin, ProcessingStatus
from app.models.financial import FinancialFact, FinancialMetric, FinancialPeriod, FinancialStatus, FactStatus, MetricCode, PeriodType
from app.models.memory import DocumentMemoryIndex
from app.schemas.memory import MemoryQuery
from app.services.commentary import parse_statement, parse_guidance, guidance_vs_actual, guidance_change, guidance_target_end
from app.services.memory.chunking import VERSION, chunk_page, normalize, topics
from app.services.memory.clients import MemoryUnavailable, OllamaEmbeddings, QdrantStore, match

INDIA = ZoneInfo("Asia/Kolkata")
VERIFIED = {FactStatus.VERIFIED_PDF, FactStatus.VERIFIED_STRUCTURED, FactStatus.MANUALLY_VERIFIED}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def local_day(value):
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(INDIA).date()


def day_number(value):
    return int(value.strftime("%Y%m%d"))


def document_type(document):
    if document.document_type not in {"PDF", "HTML", "OTHER"}:
        return document.document_type
    if document.event.event_type.value == "CONCALL_TRANSCRIPT":
        return "CONCALL_TRANSCRIPT"
    if "transcript" in document.event.title.lower():
        return "TRANSCRIPT"
    return document.event.event_type.value


def manifest(db, document):
    event = document.event
    if (document.processing_status != ProcessingStatus.PROCESSED
            or event.data_origin == DataOrigin.FIXTURE or event.company_id != document.company_id):
        return None, []
    pages = list(db.scalars(select(DocumentPage).where(DocumentPage.document_id == document.id)
                           .order_by(DocumentPage.page_number)))
    periods = list(db.scalars(select(FinancialPeriod).where(
        FinancialPeriod.source_document_id == document.id, FinancialPeriod.company_id == document.company_id,
        FinancialPeriod.status == FinancialStatus.VALIDATED).order_by(FinancialPeriod.period_end.desc())))
    latest = [p for p in periods if p.period_end == periods[0].period_end] if periods else []
    associated = [db.get(FinancialPeriod, period_id) for period_id in db.scalars(
        select(CommentaryDocument.financial_period_id).where(
            CommentaryDocument.document_id == document.id,
            CommentaryDocument.company_id == document.company_id,
        ))]
    associated = [p for p in associated if p and p.status == FinancialStatus.VALIDATED]
    linked = latest or associated
    years, quarters = {p.fiscal_year for p in linked}, {p.quarter for p in linked}
    scopes = {p.scope.value for p in linked}
    basis = event.reporting_basis.value if event.reporting_basis else (next(iter(scopes)) if len(scopes) == 1 else None)
    # A conflicting later publication timestamp is the conservative availability bound.
    published = max((d for d in (document.published_at, event.published_at) if d),
                    key=lambda d: d.replace(tzinfo=d.tzinfo or timezone.utc))
    metadata = {
        "company_id": document.company_id, "document_id": document.id, "event_id": event.id,
        "document_type": document_type(document), "title": event.title,
        "published_at": published.replace(tzinfo=published.tzinfo or timezone.utc).astimezone(INDIA).isoformat(), "published_day": day_number(local_day(published)),
        "publication_precision": event.publication_precision.value,
        "fiscal_year": event.fiscal_year or (next(iter(years)) if len(years) == 1 else None),
        "quarter": event.quarter or (next(iter(quarters)) if len(quarters) == 1 else None),
        "reporting_basis": basis,
        "source": document.source, "source_url": document.source_url,
        "data_origin": event.data_origin.value, "sha256": document.sha256_hash,
        "references": sorted([{"source": ref.source, "source_url": ref.source_url,
                                "data_origin": ref.data_origin.value} for ref in event.references],
                             key=lambda r: (r["source"], r["source_url"])),
    }
    revision = digest({"metadata": metadata, "pages": [(p.id, p.page_number, p.text) for p in pages],
                       "version": VERSION, "model": settings.memory_embedding_model,
                       "dimensions": settings.memory_embedding_dimensions,
                       "size": settings.memory_chunk_chars, "overlap": settings.memory_overlap_chars})
    result = []
    for page in pages:
        page_scopes = {v.upper() for v in re.findall(r"\b(standalone|consolidated)\b", page.text or "", re.I)}
        page_scope = next(iter(page_scopes)) if len(page_scopes) == 1 else None
        for ordinal, chunk in enumerate(chunk_page(page.text or "", settings.memory_chunk_chars,
                                                   settings.memory_overlap_chars)):
            point_id = str(uuid5(NAMESPACE_URL, f"almonds:{document.id}:{revision}:{page.id}:{ordinal}"))
            payload = {**metadata, "revision": revision, "embedding_model": settings.memory_embedding_model,
                       "page_id": page.id, "page": page.page_number, "section": chunk.section,
                       "scope": chunk.scope or page_scope or (basis if basis in {"STANDALONE", "CONSOLIDATED"} else None),
                       "speaker": chunk.speaker, "topics": chunk.topics, "text": chunk.text,
                       "evidence_id": "C" + point_id,
                       "document_url": f"/api/documents/{document.id}/file#page={page.page_number}"}
            result.append({"id": point_id, "payload": payload})
    return revision, result


def index_document(db: Session, document: Document, store, embedder, *, force=False):
    # Serialize per-document writers, including first indexing; no global lock.
    db.execute(select(Document.id).where(Document.id == document.id).with_for_update()).all()
    state = db.get(DocumentMemoryIndex, document.id)
    revision, chunks = manifest(db, document)
    if state is None:
        state = DocumentMemoryIndex(document_id=document.id, collection=settings.memory_collection,
                                    embedding_model=settings.memory_embedding_model, status="PENDING", chunk_count=0)
        db.add(state)
    try:
        if revision is None or not chunks:
            store.prune(document.company_id, document.id)
            state.revision, state.chunk_count, state.status = None, 0, "SKIPPED"
        elif (not force and state.revision == revision and state.collection == settings.memory_collection
              and store.count(document.company_id, document.id, revision) == len(chunks)):
            store.prune(document.company_id, document.id, revision)
            state.status = "INDEXED"
            state.last_error = None
            db.commit()
            return {"document_id": document.id, "status": "UNCHANGED", "chunks": len(chunks)}
        else:
            for start in range(0, len(chunks), 16):
                batch = chunks[start:start + 16]
                vectors = embedder.embed([
                    "\n".join(filter(None, [p["payload"]["title"], p["payload"]["section"],
                                            p["payload"]["speaker"], p["payload"]["text"]])) for p in batch])
                if len(vectors) != len(batch):
                    raise MemoryUnavailable("EMBEDDING_BATCH_INCOMPLETE")
                store.upsert([{**point, "vector": vector} for point, vector in zip(batch, vectors)])
            # Keep the writer lock through cleanup. A reader during replacement may see
            # no matches, but can never use a partially published revision.
            store.prune(document.company_id, document.id, revision)
            # Publish only after every vector exists. Readers reject uncommitted revisions.
            state.revision, state.chunk_count, state.status = revision, len(chunks), "INDEXED"
        state.collection = settings.memory_collection
        state.embedding_model = settings.memory_embedding_model
        state.last_error = None
        state.updated_at = datetime.now(timezone.utc)
        db.commit()
        return {"document_id": document.id, "status": state.status, "chunks": state.chunk_count}
    except MemoryUnavailable as exc:
        # Retain the prior active revision. It is usable only if canonical content still matches.
        state.status, state.last_error = "FAILED", str(exc)
        db.commit()
        return {"document_id": document.id, "status": "FAILED", "reason": str(exc), "chunks": state.chunk_count}


def index_company(db, company_id, *, force=False, store=None, embedder=None):
    if not settings.company_memory_enabled:
        return {"status": "DISABLED", "documents": []}
    store, embedder = store or QdrantStore(), embedder or OllamaEmbeddings()
    try:
        store.ensure()
    except MemoryUnavailable as exc:
        return {"status": "UNAVAILABLE", "reason": str(exc), "documents": []}
    documents = list(db.scalars(select(Document).where(Document.company_id == company_id).order_by(Document.id)))
    rows = [index_document(db, doc, store, embedder, force=force) for doc in documents]
    return {"status": "PARTIAL" if any(r["status"] == "FAILED" for r in rows) else "READY", "documents": rows}


def filters_for(company_id, query):
    must = [match("company_id", company_id), match("embedding_model", settings.memory_embedding_model)]
    for key, value in {"document_type": query.document_type, "fiscal_year": query.fiscal_year,
                       "quarter": query.quarter, "topics": query.topic,
                       "scope": query.scope.value if query.scope else None}.items():
        if value is not None:
            must.append(match(key, value))
    if not query.topic and (intents := topics(query.query)):
        must.append({"key": "topics", "match": {"any": intents}})
    bounds = {}
    if query.as_of_date:
        bounds["lte"] = day_number(query.as_of_date)
    if query.date_from:
        bounds["gte"] = day_number(query.date_from)
    if bounds:
        must.append({"key": "published_day", "range": bounds})
    return {"must": must}


def matches(payload, company_id, query):
    if payload["company_id"] != company_id:
        return False
    for condition in filters_for(company_id, query)["must"]:
        actual = payload.get(condition["key"])
        if "match" in condition:
            if "any" in condition["match"]:
                if not isinstance(actual, list) or not set(actual) & set(condition["match"]["any"]):
                    return False
                continue
            value = condition["match"]["value"]
            if (value not in actual if isinstance(actual, list) else actual != value):
                return False
        elif actual is None or any((actual > bound if key == "lte" else actual < bound)
                                   for key, bound in condition["range"].items()):
            return False
    return True


def guidance_record(db, row, statement, as_of_date):
    owner = db.get(FinancialPeriod, row.financial_period_id)
    target_type = PeriodType.QUARTERLY if row.target_period.startswith("Q") else PeriodType.ANNUAL
    result = {"evidence_id": f"G{row.id}", "kind": "GUIDANCE_RECORD", "metric": row.metric,
              "target_period": row.target_period, "lower_bound": str(row.lower_bound),
              "upper_bound": str(row.upper_bound), "unit": row.unit, "scope": owner.scope.value,
              "document_id": statement.document_id, "page": statement.page_number,
              "published_at": statement.published_at.isoformat(), "status": row.status,
              "speaker_name": statement.speaker_name, "speaker_role": statement.speaker_role,
              "document_url": f"/api/documents/{statement.document_id}/file#page={statement.page_number}",
              "change": "FIRST_OBSERVATION", "actual_value": None, "actual_evidence_ids": [], "actual_sources": [],
              "actual_comparison": "INSUFFICIENT_DATA", "execution_outcome": "INSUFFICIENT_DATA"}
    earlier = db.scalar(select(ManagementGuidance).join(FinancialPeriod)
        .where(ManagementGuidance.company_id == row.company_id, ManagementGuidance.metric == row.metric,
               ManagementGuidance.target_period == row.target_period,
               ManagementGuidance.published_at < row.published_at, FinancialPeriod.scope == owner.scope)
        .order_by(ManagementGuidance.published_at.desc(), ManagementGuidance.id.desc()))
    if earlier:
        result["change"] = guidance_change(earlier, row)
    # Only final values for the stated period are comparable. Every input must
    # be verified, have the right unit and scope, and be public by as_of_date.
    def ready_period(period):
        if (period is None or period.company_id != row.company_id
                or period.scope != owner.scope or period.period_type != target_type
                or period.status != FinancialStatus.VALIDATED):
            return False
        source_event = db.get(CompanyEvent, period.source_event_id)
        source_document = db.get(Document, period.source_document_id)
        return bool(source_event and source_document
            and local_day(source_event.published_at) <= as_of_date
            and (not source_document.published_at or local_day(source_document.published_at) <= as_of_date))

    def ready_fact(fact):
        if fact is None or fact.status not in VERIFIED or fact.unit.value != "INR_CRORE":
            return False
        source_document = db.get(Document, fact.source_document_id)
        return bool(source_document and source_document.company_id == row.company_id
            and (not source_document.published_at or local_day(source_document.published_at) <= as_of_date)
            and ready_period(db.get(FinancialPeriod, fact.financial_period_id)))

    def source(fact):
        return {"fact_id": fact.id, "document_id": fact.source_document_id,
                "page": fact.source_page, "period_id": fact.financial_period_id,
                "document_url": f"/api/documents/{fact.source_document_id}/file" +
                (f"#page={fact.source_page}" if fact.source_page else "")}

    candidates = db.scalars(select(FinancialPeriod).where(
        FinancialPeriod.company_id == row.company_id, FinancialPeriod.scope == owner.scope,
        FinancialPeriod.period_type == target_type, FinancialPeriod.status == FinancialStatus.VALIDATED))
    metric_code = {"REVENUE_GROWTH": MetricCode.REVENUE_YOY,
                   "PAT_GROWTH": MetricCode.PAT_YOY,
                   "EBITDA_MARGIN": MetricCode.EBITDA_MARGIN,
                   "PAT_MARGIN": MetricCode.PAT_MARGIN}.get(row.metric)
    fact_code = {"CAPEX": MetricCode.CAPITAL_EXPENDITURE,
                 "DEBT": MetricCode.TOTAL_DEBT,
                 "DEBT_REDUCTION": MetricCode.TOTAL_DEBT,
                 "CASH_FLOW": MetricCode.OPERATING_CASH_FLOW}.get(row.metric)
    for period in candidates:
        period_label = (f"Q{period.quarter}FY{period.fiscal_year[-2:]}"
                        if target_type == PeriodType.QUARTERLY else "FY" + period.fiscal_year[-2:])
        if period_label != row.target_period or not ready_period(period):
            continue
        value, evidence_ids, inputs = None, [], []
        if metric_code and row.unit == "PERCENT":
            if row.metric == "EBITDA_MARGIN" and re.search(r"\b(?:adjusted|normaliz\w*)\b", statement.raw_statement, re.I):
                continue
            metric = db.scalar(select(FinancialMetric).where(
                FinancialMetric.financial_period_id == period.id,
                FinancialMetric.metric_code == metric_code))
            if metric and metric.unit.value == row.unit and metric.input_fact_ids:
                inputs = [db.get(FinancialFact, fid) for fid in metric.input_fact_ids]
                owners = {fact.financial_period_id for fact in inputs if fact}
                expected = {period.id, metric.comparison_period_id} - {None}
                if (len(inputs) == len(set(metric.input_fact_ids))
                        and all(ready_fact(fact) for fact in inputs)
                        and owners == expected
                        and (metric_code in {MetricCode.EBITDA_MARGIN, MetricCode.PAT_MARGIN}
                             and metric.comparison_period_id is None
                             or metric_code in {MetricCode.REVENUE_YOY, MetricCode.PAT_YOY}
                             and metric.comparison_period_id is not None
                             and metric.comparison_period_id != period.id
                             and db.get(FinancialPeriod, metric.comparison_period_id).period_end < period.period_end)):
                    value, evidence_ids = metric.value, [f"M{metric.id}"] + [f"F{f.id}" for f in inputs]
        elif fact_code and row.unit == "INR_CRORE":
            if row.metric in {"DEBT", "DEBT_REDUCTION"} and "net debt" in statement.raw_statement.lower():
                continue
            if row.metric == "CASH_FLOW" and "operating cash flow" not in statement.raw_statement.lower():
                continue
            current = db.scalar(select(FinancialFact).where(
                FinancialFact.financial_period_id == period.id,
                FinancialFact.metric_code == fact_code))
            if not ready_fact(current):
                continue
            inputs = [current]
            value = current.value
            if row.metric == "DEBT_REDUCTION":
                if target_type != PeriodType.ANNUAL:
                    continue
                previous = db.scalar(select(FinancialPeriod).where(
                    FinancialPeriod.company_id == row.company_id,
                    FinancialPeriod.scope == owner.scope,
                    FinancialPeriod.period_type == PeriodType.ANNUAL,
                    FinancialPeriod.period_end == date(period.period_end.year - 1, 3, 31),
                    FinancialPeriod.status == FinancialStatus.VALIDATED))
                prior_debt = db.scalar(select(FinancialFact).where(
                    FinancialFact.financial_period_id == previous.id,
                    FinancialFact.metric_code == MetricCode.TOTAL_DEBT)) if previous else None
                if not ready_fact(prior_debt):
                    continue
                inputs.append(prior_debt)
                value = prior_debt.value - current.value
            evidence_ids = [f"F{fact.id}" for fact in inputs]
        if value is None:
            continue
        result["actual_value"] = str(value)
        result["actual_evidence_ids"] = evidence_ids
        result["actual_sources"] = [source(fact) for fact in inputs]
        comparison = guidance_vs_actual(row, value, row.target_period)
        result["actual_comparison"] = comparison
        if row.metric == "CAPEX":
            # Spending above or below a budget is a variance, not automatically
            # good or bad execution.
            result["execution_outcome"] = "ACHIEVED" if comparison == "WITHIN_GUIDANCE" else comparison
        elif row.metric == "DEBT":
            result["execution_outcome"] = {"BELOW_GUIDANCE": "EXCEEDED",
                "WITHIN_GUIDANCE": "ACHIEVED", "ABOVE_GUIDANCE": "MISSED"}.get(comparison, "INSUFFICIENT_DATA")
        else:
            result["execution_outcome"] = {"ABOVE_GUIDANCE": "EXCEEDED",
                "WITHIN_GUIDANCE": "ACHIEVED", "BELOW_GUIDANCE": "MISSED"}.get(comparison, "INSUFFICIENT_DATA")
        break
    if result["actual_value"] is None:
        # Before the target ends the outcome is not assessable; afterwards,
        # absent verified evidence remains insufficient data.
        target_end = guidance_target_end(row.target_period)
        if target_end and as_of_date < target_end:
            result["actual_comparison"] = "TOO_EARLY_TO_ASSESS"
            result["execution_outcome"] = "TOO_EARLY_TO_ASSESS"
    return result


def structured_evidence(db, company_id, passages, as_of_date):
    """Same-source structured records take priority. Guidance conflicts are explicit.

    Do not parse a narrative number into a financial fact or mark a promise delivered.
    Existing investor-report guidance outcomes remain authoritative.
    """
    evidence, conflicts = {}, []
    for passage in passages:
        page, doc_id = passage["page"], passage["document_id"]
        facts = db.execute(select(FinancialFact, FinancialPeriod).join(FinancialPeriod)
            .where(FinancialFact.source_document_id == doc_id, FinancialFact.source_page == page,
                   FinancialFact.status.in_(VERIFIED), FinancialPeriod.company_id == company_id,
                   FinancialPeriod.status == FinancialStatus.VALIDATED)).all()
        for fact, period in facts:
            key = f"F{fact.id}"
            evidence[key] = {"evidence_id": key, "kind": "FACT", "metric_code": fact.metric_code.value,
                "value": str(fact.value), "unit": fact.unit.value, "scope": period.scope.value,
                "period_end": period.period_end.isoformat(), "document_id": doc_id, "page": page}
        guidance = db.execute(select(ManagementGuidance, ManagementStatement)
            .join(ManagementStatement, ManagementGuidance.statement_id == ManagementStatement.id)
            .where(ManagementGuidance.company_id == company_id, ManagementStatement.document_id == doc_id,
                   ManagementStatement.page_number == page)).all()
        parsed = parse_statement(passage["text"])
        candidate = parse_guidance(parsed) if parsed else None
        for guidance_row, statement in guidance:
            if day_number(local_day(statement.published_at)) > passage["published_day"]:
                continue
            key = f"G{guidance_row.id}"
            evidence[key] = guidance_record(db, guidance_row, statement, as_of_date)
            if candidate and all(candidate[k] == getattr(guidance_row, k) for k in ("metric", "target_period", "unit")):
                if any(candidate[k] != getattr(guidance_row, k) for k in ("lower_bound", "upper_bound")):
                    conflicts.append({"type": "GUIDANCE_SOURCE_DISAGREEMENT", "evidence_id": passage["evidence_id"],
                                      "structured_evidence_id": key,
                                      "resolution": "Use the PostgreSQL guidance record; source discrepancy requires review."})
    return list(evidence.values()), conflicts


def similarity(a, b):
    words = lambda s: set(re.findall(r"\w+", s.lower()))
    x, y = words(a), words(b)
    return len(x & y) / max(1, len(x | y))


def query_memory(db, company_id, query: MemoryQuery, *, store=None, embedder=None, exclude_document_id=None):
    response = {"status": "NOT_INDEXED", "results": [], "structured_evidence": [], "conflicts": [],
                "as_of_date": query.as_of_date.isoformat() if query.as_of_date else None,
                "authority": "PostgreSQL facts, metrics and guidance take precedence; passages are documentary context."}
    if not settings.company_memory_enabled:
        return {**response, "status": "DISABLED"}
    active = list(db.scalars(select(DocumentMemoryIndex).join(Document)
        .where(Document.company_id == company_id, DocumentMemoryIndex.revision.is_not(None),
               DocumentMemoryIndex.collection == settings.memory_collection,
               DocumentMemoryIndex.embedding_model == settings.memory_embedding_model)))
    if not active:
        return response
    store, embedder = store or QdrantStore(), embedder or OllamaEmbeddings()
    try:
        # Check service availability before loading an embedding model.
        if store.info() is None:
            return response
        filters = filters_for(company_id, query)
        if exclude_document_id is not None:
            filters["must_not"] = [match("document_id", exclude_document_id)]
        hits = store.search(embedder.embed([query.query], query=True)[0], filters,
                            min(80, query.limit * 8))
    except MemoryUnavailable as exc:
        return {**response, "status": "UNAVAILABLE", "reason": str(exc)}
    states = {s.document_id: s for s in active}
    canonical = {}
    candidates = []
    stale = 0
    for hit in hits:
        payload = hit.get("payload") or {}
        doc_id = payload.get("document_id")
        state = states.get(doc_id)
        if not state or payload.get("company_id") != company_id or doc_id == exclude_document_id:
            continue
        if doc_id not in canonical:
            document = db.get(Document, doc_id)
            revision, chunks = manifest(db, document)
            canonical[doc_id] = {c["id"]: c["payload"] for c in chunks} if revision == state.revision else {}
        current = canonical[doc_id].get(str(hit["id"]))
        # Never accept altered, stale or wrongly attributed vector-store text.
        if current != payload:
            stale += 1
            continue
        if matches(current, company_id, query):
            candidates.append({**current, "score": round(float(hit["score"]), 6)})
    selected, per_document = [], Counter()
    budget = settings.memory_context_chars
    while candidates and len(selected) < query.limit:
        # Diversity penalty encourages other documents and suppresses repetitive boilerplate.
        candidates.sort(key=lambda c: (-(c["score"] - 0.08 * per_document[c["document_id"]]), c["evidence_id"]))
        candidate = candidates.pop(0)
        if any(similarity(candidate["text"], old["text"]) >= 0.82
               and (candidate["document_id"] == old["document_id"] or candidate["published_day"] == old["published_day"])
               for old in selected):
            continue
        if per_document[candidate["document_id"]] >= 2 or len(candidate["text"]) > budget:
            continue
        selected.append(candidate)
        per_document[candidate["document_id"]] += 1
        budget -= len(candidate["text"])
    structured, conflicts = structured_evidence(db, company_id, selected, query.as_of_date or datetime.now(INDIA).date())
    return {**response, "status": "READY" if selected else "NO_MATCHES", "results": selected,
            "structured_evidence": structured, "conflicts": conflicts, "stale_candidates_rejected": stale}


def memory_status(db, company_id, *, store=None):
    rows = []
    for document in db.scalars(select(Document).where(Document.company_id == company_id).order_by(Document.id)):
        state = db.get(DocumentMemoryIndex, document.id)
        revision, _ = manifest(db, document)
        status = state.status if state else "NOT_INDEXED"
        if state and state.revision and (state.revision != revision or state.collection != settings.memory_collection):
            status = "STALE"
        rows.append({"document_id": document.id, "document_type": document_type(document), "title": document.event.title,
                     "status": status, "chunks": state.chunk_count if state else 0,
                     "last_error": state.last_error if state else None})
    availability = "DISABLED"
    if settings.company_memory_enabled:
        try:
            availability = "READY" if (store or QdrantStore()).info() is not None else "NOT_INDEXED"
        except MemoryUnavailable:
            availability = "UNAVAILABLE"
    return {"status": availability, "collection": settings.memory_collection,
            "embedding_model": settings.memory_embedding_model, "documents": rows}
