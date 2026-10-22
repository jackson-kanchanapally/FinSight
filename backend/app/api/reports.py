"""Only scoped, authenticated report resources are suitable for remote routing."""
from html import escape
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.ingestion.storage import LocalDocumentStorage
from app.models.event import Document
from app.models.financial import FinancialPeriod, FinancialStatus
from app.models.telegram import TelegramDelivery
from app.schemas.earnings_notification import EarningsNotification
from app.services.earnings_notification import build_notification
from app.services.report_links import authorized, signed_link
from app.services.telegram_formatter import format_earnings, metric_line

router = APIRouter(prefix="/api/reports/earnings", tags=["reports"])
HEADERS = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
           "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"}


def _period(db, period_id, resource, expires, signature):
    if not authorized(resource, expires, signature):
        raise HTTPException(403, "Report access is invalid, expired, or not configured")
    period = db.get(FinancialPeriod, period_id)
    if period is None or period.status != FinancialStatus.VALIDATED:
        raise HTTPException(404, "Validated report not found")
    return period


@router.get("/{period_id}", response_class=HTMLResponse)
def report(period_id: int, expires: int = Query(...), signature: str = Query(...), db: Session = Depends(get_db)):
    period = _period(db, period_id, str(period_id), expires, signature)
    notification = build_notification(db, period)
    return _render_report(period, notification, expires)


def _render_report(period, notification, expires):
    content = "<hr>".join(format_earnings(notification)).replace("\n", "<br>")
    # Evidence table is escaped individually; never embed model HTML.
    lineage = []
    for key, row in notification.evidence_registry.items():
        if row.get("kind") not in {"FACT", "METRIC", "VALUATION"}:
            continue
        values = [key, row.get("metric_code", ""), row.get("value", ""), row.get("unit", ""),
                  row.get("formula", "Source fact"), ", ".join(row.get("input_evidence_ids", [])),
                  f"Document {row.get('document_id', '')}, page {row.get('page', '')}"]
        lineage.append("<tr>" + "".join("<td>"+escape(str(value))+"</td>" for value in values) + "</tr>")
    document_link = signed_link(period.id, document_id=period.source_document_id, expires=expires).url
    details = ("<h2>Full assessment</h2><p>"+escape(notification.verdict.text)+"</p>"
        + "<p>Analysis version: " + escape(str(notification.analysis_id or "deterministic")) + "</p>"
        + "<h2>Additional financial metrics</h2>" + "<br>".join(metric_line(row) for row in notification.additional_metrics)
        + "<h2>Risks and missing evidence</h2>" + "".join("<p>"+escape(row.text)+"</p>" for row in notification.risks)
        + "".join("<p>"+escape(key.replace("_", " ")+": Unassessed — "+value)+"</p>" for key, value in notification.unassessed.items())
        + "<h2>Confidence rationale</h2>" + "".join("<p>"+escape(value)+"</p>" for value in notification.confidence["rationale"]))
    html = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>Almonds earnings report</title><style>body{font:16px system-ui;max-width:900px;margin:2rem auto;padding:1rem;line-height:1.6}"
            "table{font-size:13px;border-collapse:collapse;display:block;overflow:auto}td,th{padding:8px;border:1px solid #aaa}</style></head><body>"
            + content + details + f'<p><a href="{escape(document_link or "", quote=True)}">Download source PDF</a></p>'
            + "<h2>Source and calculation lineage</h2><table><tr><th>ID</th><th>Metric</th><th>Value</th><th>Unit</th><th>Formula</th><th>Inputs</th><th>Source</th></tr>"
            + "".join(lineage) + "</table></body></html>")
    return HTMLResponse(html, headers=HEADERS)


@router.get("/{period_id}/deliveries/{delivery_id}", response_class=HTMLResponse)
def delivery_report(period_id: int, delivery_id: int, expires: int = Query(...), signature: str = Query(...), db: Session = Depends(get_db)):
    period = _period(db, period_id, f"{period_id}/deliveries/{delivery_id}", expires, signature)
    delivery = db.get(TelegramDelivery, delivery_id)
    if not delivery or not delivery.notification or delivery.notification.get("period_id") != period_id:
        raise HTTPException(404, "Delivery report snapshot not found")
    return _render_report(period, EarningsNotification.model_validate(delivery.notification), expires)


@router.get("/{period_id}/documents/{document_id}")
def document(period_id: int, document_id: int, expires: int = Query(...), signature: str = Query(...), db: Session = Depends(get_db)):
    period = _period(db, period_id, f"{period_id}/documents/{document_id}", expires, signature)
    if document_id != period.source_document_id:
        raise HTTPException(403, "Document is outside this report")
    doc = db.get(Document, document_id)
    if not doc or not doc.local_path:
        raise HTTPException(404, "Report document is unavailable")
    try:
        path = LocalDocumentStorage(settings.document_storage_path).resolve(doc.local_path)
    except (OSError, ValueError):
        raise HTTPException(404, "Report document is unavailable") from None
    return FileResponse(path, media_type="application/pdf", filename="almonds-results.pdf", headers=HEADERS)
