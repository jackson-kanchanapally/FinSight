"""Bounded authoritative discovery and independent document-authenticity evidence."""
import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import select
from app.models.company import Company
from app.models.event import CompanyEvent, CompanySource, DataOrigin, Document, DocumentPage, EventType
from app.models.financial import FactStatus, FinancialPeriod, FinancialProcessingRun, FinancialStatus, FinancialUnit
from app.models.result_evidence import ResultDiscovery, ResultSourceCheck, ResultVerification


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def adapter_payload(reference):
    payload = reference.raw_payload or {}
    # Normalization retains the complete SourceEvent around the adapter payload.
    nested = payload.get("raw_payload")
    return nested if isinstance(nested, dict) else payload


def authoritative(db, company_id, source, url):
    """A URL alone is insufficient: callers must also require a LIVE adapter observation."""
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
            return False
    except ValueError:
        return False
    if source == "NSE":
        return parts.hostname in {"nsearchives.nseindia.com", "archives.nseindia.com"}
    if source == "ESDS_IR":
        mapping = db.scalar(select(CompanySource).where(CompanySource.company_id == company_id,
            CompanySource.source == source, CompanySource.enabled.is_(True)))
        return bool(mapping and mapping.ir_url and parts.hostname == urlsplit(mapping.ir_url).hostname)
    return False


def source_check(db, companies, source, status, *, unresolved=(), error=None):
    if source not in {"NSE", "ESDS_IR"}:
        return
    current = datetime.now(timezone.utc)
    for company in companies:
        row = db.scalar(select(ResultSourceCheck).where(ResultSourceCheck.company_id == company.company_id,
                                                       ResultSourceCheck.source == source))
        if row is None:
            row = ResultSourceCheck(company_id=company.company_id, source=source)
            db.add(row)
        row.attempted_at = current
        row.status = "UNRESOLVED" if company.symbol in unresolved else status
        row.error = error if status == "FAILED" else ("Issuer could not be resolved" if row.status == "UNRESOLVED" else None)
        row.coverage = "Recent announcements RSS window only" if source == "NSE" else "Configured investor-relations archive; bounded adapter coverage"
        if row.status == "SUCCESS":
            row.succeeded_at = current
    db.commit()


def _explicit_end(raw):
    if raw.period_end_date:
        return raw.period_end_date
    text = " ".join(filter(None, (raw.title, raw.description)))
    match = re.search(r"\bQ([1-4])\s*FY\s*(20\d{2}|\d{2})\b", text, re.I)
    if match:
        q, year = int(match[1]), int(match[2])
        year = year + 2000 if year < 100 else year
        month, day = {1: (6, 30), 2: (9, 30), 3: (12, 31), 4: (3, 31)}[q]
        return date(year if q == 4 else year-1, month, day)
    match = re.search(r"(?:period|quarter|year)\s+ended\s+(\d{4}-\d{2}-\d{2})", text, re.I)
    if match:
        try:
            return date.fromisoformat(match[1])
        except ValueError:
            pass
    return None


def record_discovery(db, normalized):
    if (normalized.data_origin != DataOrigin.LIVE or normalized.event_type != EventType.FINANCIAL_RESULTS
            or not authoritative(db, normalized.company_id, normalized.source, normalized.source_url)):
        return None
    key = digest([normalized.company_id, normalized.source, normalized.external_id or normalized.source_url])
    row = db.scalar(select(ResultDiscovery).where(ResultDiscovery.identity_key == key))
    declared_end = _explicit_end(normalized)
    if row is not None and row.period_end and declared_end and declared_end != row.period_end:
        key = digest([normalized.company_id, normalized.source, normalized.external_id or normalized.source_url, declared_end])
        row = db.scalar(select(ResultDiscovery).where(ResultDiscovery.identity_key == key))
    if row is None:
        scope = normalized.reporting_basis.value if normalized.reporting_basis else None
        row = ResultDiscovery(identity_key=key, company_id=normalized.company_id, source=normalized.source,
            source_url=normalized.source_url, document_url=normalized.document_url, title=normalized.title,
            published_at=normalized.published_at, period_end=_explicit_end(normalized), scope=scope,
            status="DETECTED", details=normalized.raw_payload or {})
        db.add(row)
        db.commit()  # Intentionally precedes download: a failed PDF must not hide detection.
    elif row.period_end is None and _explicit_end(normalized):
        row.period_end = _explicit_end(normalized)
        db.commit()
    return row


def link_discovery(db, discovery, event_id, failed=False):
    if discovery is None:
        return
    discovery.event_id = event_id
    discovery.status = "AWAITING_INGESTION" if failed else "DOCUMENT_STORED"
    periods = list(db.scalars(select(FinancialPeriod).where(FinancialPeriod.source_event_id == event_id,
                                                          FinancialPeriod.status == FinancialStatus.VALIDATED)))
    if periods:
        extracted_end = max(p.period_end for p in periods)
        scopes = {p.scope.value for p in periods}
        if discovery.period_end and discovery.period_end != extracted_end:
            discovery.status = "NEEDS_REVIEW"
            discovery.details = {**discovery.details, "period_conflict": {
                "detected_period_end": discovery.period_end.isoformat(), "stored_pdf_period_end": extracted_end.isoformat()}}
        elif discovery.scope not in (None, "BOTH") and discovery.scope not in scopes:
            discovery.status = "NEEDS_REVIEW"
        else:
            discovery.period_end = extracted_end
            discovery.status = "INGESTED"
            discovery.scope = next(iter(scopes)) if len(scopes) == 1 else "BOTH"
    else:
        run = db.scalar(select(FinancialProcessingRun).where(FinancialProcessingRun.source_event_id == event_id))
        if run and run.status == FinancialStatus.NEEDS_REVIEW:
            discovery.status = "NEEDS_REVIEW"
    db.commit()


def _identity(db, company, document_id):
    text = " ".join(db.scalars(select(DocumentPage.text).where(DocumentPage.document_id == document_id)))
    clean = lambda value: re.sub(r"[^A-Z0-9]", "", value.upper()).replace("LIMITED", "LTD")
    name = clean(company.name)
    name_match = len(name) >= 5 and name in clean(text)
    symbol_match = bool(re.search(r"\b(?:NSE|SYMBOL|SCRIP SYMBOL)\s*(?:SYMBOL|CODE)?\s*[:\-]\s*"
                                 + re.escape(company.symbol) + r"\b", text, re.I))
    cins = set(re.findall(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b", text.upper()))
    isins = set(re.findall(r"\bIN[A-Z0-9]{10}\b", text.upper()))
    return {"company_match": name_match or symbol_match or bool(company.isin and company.isin in isins),
            "cin": sorted(cins), "isin": sorted(isins), "symbol_match": symbol_match}


def _persist(db, period, status, details):
    key = digest([period.id, status, details])
    record = db.scalar(select(ResultVerification).where(ResultVerification.fingerprint == key))
    if record is None:
        record = ResultVerification(period_id=period.id, fingerprint=key, status=status, details=details)
        db.add(record)
        db.flush()
    return record


def assess_stored_period(db, period):
    company = db.get(Company, period.company_id)
    event = db.get(CompanyEvent, period.source_event_id)
    document = db.get(Document, period.source_document_id)
    identity = _identity(db, company, document.id)
    references = [r for r in event.references if r.data_origin == DataOrigin.LIVE
                  and authoritative(db, company.id, r.source, r.source_url)]
    # Require the actual downloaded document to have a live acquisition chain.
    live_document = (document.source != "MANUAL_UPLOAD" and document.document_url
        and any(r.document_url == document.document_url for r in references))
    payloads = [adapter_payload(r) for r in references]
    issuer_matched = any(p.get("issuer_symbol") == company.symbol for p in payloads)
    trusted = bool(live_document and identity["company_match"] and issuer_matched)
    status = "VERIFIED" if trusted else "PARTIALLY_VERIFIED" if references else "UNVERIFIED"
    details = {"document_id": document.id, "identity": identity,
               "period_end": period.period_end.isoformat(), "scope": period.scope.value,
               "references": [{"source": r.source, "url": r.source_url} for r in references],
               "reason": "Issuer, period and basis extracted from a PDF acquired through an authoritative adapter"
                         if trusted else "Authoritative acquisition and matching issuer identifiers have not all been established",
               "hash_is_authenticity_proof": False}
    details["authoritative_document_acquisition"] = bool(live_document)
    details["issuer_symbol_matched"] = issuer_matched
    prior = db.scalar(select(ResultVerification).where(ResultVerification.period_id == period.id)
                      .order_by(ResultVerification.id.desc()))
    if prior and prior.details.get("comparison_document_id"):
        return prior
    return _persist(db, period, status, details)


def compare_authoritative_candidate(db, period, event, document, raw):
    """Compare a second official PDF without overwriting the stored or restated history."""
    if event.data_origin != DataOrigin.LIVE or not any(
        r.data_origin == DataOrigin.LIVE and authoritative(db, event.company_id, r.source, r.source_url)
        for r in event.references):
        return None
    from app.services.financials.calculator import normalize_currency
    if (event.company_id != period.company_id or document.company_id != period.company_id
            or raw.identity.end != period.period_end or raw.identity.start != period.period_start
            or raw.identity.period_type != period.period_type or raw.scope != period.scope):
        return _persist(db, period, "CONFLICTING", {"comparison_document_id": document.id,
            "reason": "Company, reporting period or basis conflicts with the stored result"})
    company = db.get(Company, period.company_id)
    stored_identity, candidate_identity = (_identity(db, company, document_id)
                                          for document_id in (period.source_document_id, document.id))
    stored = {f.metric_code: f for f in period.facts if f.status in {FactStatus.VERIFIED_PDF, FactStatus.VERIFIED_STRUCTURED, FactStatus.MANUALLY_VERIFIED}}
    compared, conflicts = [], []
    for code, value in raw.facts.items():
        original = stored.get(code)
        if original is None:
            continue
        per_share = value.original_unit.value == "INR_PER_SHARE"
        expected_unit = FinancialUnit.INR_PER_SHARE if per_share else FinancialUnit.INR_CRORE
        normalized = value.original_value if per_share else normalize_currency(value.original_value, value.original_unit)
        tolerance = Decimal(".03") if per_share else normalize_currency(Decimal(".03"), value.original_unit)
        match = original.unit == expected_unit and original.source_column_date == raw.identity.end and abs(original.value-normalized) <= tolerance
        compared.append({"metric": code.value, "stored_fact_id": original.id, "stored": str(original.value),
                         "candidate": str(normalized), "unit": expected_unit.value, "page": value.page, "matches": match})
        if not match:
            conflicts.append(code.value)
    identifier_match = any(set(stored_identity[k]) & set(candidate_identity[k]) for k in ("cin", "isin"))
    identifier_conflict = any(stored_identity[k] and candidate_identity[k]
                              and not set(stored_identity[k]) & set(candidate_identity[k]) for k in ("cin", "isin"))
    core = {r["metric"] for r in compared if r["matches"]} >= {"REVENUE_FROM_OPERATIONS", "PAT", "PBT"}
    status = ("CONFLICTING" if conflicts or identifier_conflict else "VERIFIED"
              if core and identifier_match and stored_identity["company_match"] and candidate_identity["company_match"] and not raw.issues
              else "PARTIALLY_VERIFIED")
    details = {"document_id": period.source_document_id, "comparison_document_id": document.id,
               "comparison_event_id": event.id, "period_end": raw.identity.end.isoformat(), "scope": raw.scope.value,
               "stored_identity": stored_identity, "candidate_identity": candidate_identity,
               "identifier_match": identifier_match, "identifier_conflict": identifier_conflict,
               "figures": compared, "conflicts": conflicts, "extraction_issues": raw.issues,
               "reason": "Compared against independently acquired authoritative filing; originals retained",
               "hash_is_authenticity_proof": False}
    return _persist(db, period, status, details)


def verification_view(db, period):
    row = db.scalar(select(ResultVerification).where(ResultVerification.period_id == period.id)
                    .order_by(ResultVerification.id.desc()))
    return {"status": row.status if row else "UNVERIFIED", "details": row.details if row else {
        "reason": "No independent authoritative document comparison recorded"}, "assessment_id": row.id if row else None}


def event_source_verified(db, event):
    if event.event_type == EventType.FINANCIAL_RESULTS:
        periods = list(db.scalars(select(FinancialPeriod).where(FinancialPeriod.source_event_id == event.id)))
        return bool(periods) and all(verification_view(db, period)["status"] == "VERIFIED" for period in periods)
    return any(ref.data_origin == DataOrigin.LIVE and authoritative(db, event.company_id, ref.source, ref.source_url)
               and adapter_payload(ref).get("issuer_symbol") == event.company.symbol for ref in event.references)


def freshness_view(db, period):
    stored = db.scalar(select(FinancialPeriod).where(FinancialPeriod.company_id == period.company_id,
        FinancialPeriod.scope == period.scope, FinancialPeriod.period_type == period.period_type,
        FinancialPeriod.status == FinancialStatus.VALIDATED).order_by(FinancialPeriod.period_end.desc()).limit(1))
    discoveries = list(db.scalars(select(ResultDiscovery).where(ResultDiscovery.company_id == period.company_id,
        (ResultDiscovery.scope.is_(None) | ResultDiscovery.scope.in_([period.scope.value, "BOTH"])))
        .order_by(ResultDiscovery.published_at.desc())))
    comparable = [row for row in discoveries if row.period_end]
    latest = max(comparable, key=lambda row: (row.period_end, utc(row.published_at))) if comparable else (discoveries[0] if discoveries else None)
    event = db.get(CompanyEvent, period.source_event_id)
    newer = [row for row in discoveries if (row.period_end and row.period_end > period.period_end)
             or (row.period_end == period.period_end and row.status != "INGESTED" and utc(row.published_at) > utc(event.published_at))
             or (row.period_end is None and utc(row.published_at) > utc(event.published_at))]
    checks = list(db.scalars(select(ResultSourceCheck).where(ResultSourceCheck.company_id == period.company_id)))
    status = "POTENTIALLY_STALE" if newer or (stored and stored.period_end > period.period_end) else "LATEST_STORED_COVERAGE_LIMITED"
    return {"status": status, "latest_stored_period_end": stored.period_end.isoformat() if stored else None,
        "latest_detected": {"id": latest.id, "period_end": latest.period_end.isoformat() if latest.period_end else None,
                            "published_at": utc(latest.published_at).isoformat(), "source": latest.source,
                            "source_url": latest.source_url, "ingestion_status": latest.status} if latest else None,
        "newer_awaiting_ingestion": any(row.status != "INGESTED" for row in newer),
        "source_checks": [{"source": row.source, "status": row.status,
                           "last_success_at": utc(row.succeeded_at).isoformat() if row.succeeded_at else None,
                           "last_attempt_at": utc(row.attempted_at).isoformat(), "error": row.error,
                           "coverage": row.coverage} for row in checks],
        "coverage_note": "Available configured sources only; recent RSS windows and bounded IR archives are not exhaustive public-source coverage"}
