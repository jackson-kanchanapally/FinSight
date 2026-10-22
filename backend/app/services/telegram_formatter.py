"""A short Telegram reading of the complete, persisted earnings evidence."""
from html import escape
import re
from app.schemas.earnings_notification import EarningsNotification
from app.schemas.earnings_notification import public_https_url
from app.models.event import DataOrigin


def units(text):
    return len(text.encode("utf-16-le")) // 2


def _short(text, limit=320):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit-1].rsplit(" ", 1)[0] + "…"


def _number(value):
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _bounded_html(text, max_units):
    """Escape source text without cutting a tag, entity or Unicode character."""
    text = " ".join(text.split())
    if units(escape(text)) <= max_units:
        return escape(text)
    pieces, used = [], 0
    for char in text:
        encoded = escape(char)
        used += units(encoded)
        if used > max_units - 1:
            break
        pieces.append(encoded)
    return "".join(pieces).rstrip() + "…"


def format_event(event, record):
    """Concise non-earnings brief; evidence IDs remain in the saved analysis."""
    from datetime import timezone, timedelta
    timestamp = event.published_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    stamp = timestamp.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y")
    kind = event.event_type.value.replace("_", " ").title()
    sections = [f"<b>ALMONDS | {_bounded_html(event.company.symbol, 120)} | {escape(kind)}</b>", stamp]
    insight = record.structured_output if record and record.status == "SUCCESS" else None
    if insight:
        summary = insight["summary"]["text"]
        impact = insight.get("impact")
        direction = impact["direction"] if impact else "NEUTRAL"
        reason = impact["text"] if impact else "The available details do not establish a clear positive or negative impact."
        next_step = insight["what_next"]["text"]
    else:
        summary = event.title + ". AI summary unavailable."
        direction = "NEUTRAL"
        reason = "Impact is unclear without a validated analysis."
        next_step = "Await further details before assessing the likely effects."
    sections.extend([
        "<b>AI SUMMARY</b>\n" + _bounded_html(_short(summary, 350), 1000),
        "<b>IMPACT: " + escape(direction.title()) + "</b>\n" + _bounded_html(_short(reason, 260), 800),
        "<b>WHAT MIGHT HAPPEN NEXT</b>\n" + _bounded_html(_short(next_step, 350), 1000),
    ])
    if event.data_origin == DataOrigin.FIXTURE:
        sections.append("<i>Fictional sample event.</i>")
    elif event.data_origin == DataOrigin.MANUAL:
        sections.append("<i>Source not independently verified.</i>")
    body = "\n\n".join(sections)
    if public_https_url(event.source_url):
        link = f'<a href="{escape(event.source_url, quote=True)}">View announcement</a>'
        if units(body + "\n\n" + link) <= 3800:
            body += "\n\n" + link
    return body


def metric_line(row):
    labels = {"REVENUE_FROM_OPERATIONS": "Revenue", "PAT": "Net profit", "EBITDA": "Reported EBITDA"}
    value = f"₹{_number(row.value)} cr" if row.unit == "INR_CRORE" else _number(row.value)
    changes = [f"<b>{change.value:+.1f}%</b> {label}" for change, label in (
        (row.yoy, "vs last year"), (row.qoq, "vs last quarter")) if change is not None]
    return "• " + escape(labels.get(row.code, row.label)) + ": <b>" + escape(value) + "</b>" + (
        " (" + "; ".join(changes) + ")" if changes else "")


def _thesis(notification):
    text = notification.verdict.text
    if notification.ai_status != "SUCCESS":
        explanation = next((row for row in reversed(notification.drivers)
            if row.basis == "DETERMINISTIC_INTERPRETATION"), None)
        if explanation:
            text = explanation.text
    text = re.sub(r"^(?:Very )?(?:Positive|Negative|Neutral|Insufficient Evidence):\s*", "", text, flags=re.I)
    text = text.replace("The earnings evidence weakened and warrants a review of the business thesis.",
                        "The result is weaker overall.")
    for pattern, replacement in [(r"\bPre-tax profit\b", "Profit before tax"),
        (r"\bPAT\b", "net profit"), (r"\bQOQ\b", "versus last quarter"),
        (r"\bYOY\b", "versus last year")]:
        text = re.sub(pattern, replacement, text, flags=re.I)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return escape(_short(" ".join(sentences[:2])))


def format_earnings(notification: EarningsNotification, limit=3800):
    if not 1000 <= limit <= 4000:
        raise ValueError("Telegram part limit must be between 1000 and 4000 UTF-16 units")
    n = notification
    header = f"<b>Almonds-Results | {escape(n.company_symbol)} | {escape(n.reporting_period)}</b>"
    sections = [escape(n.reporting_basis.title()) + " results · period ended " + n.period_end.strftime("%d %b %Y")]
    def section(title, lines):
        sections.append("<b>" + title + "</b>\n" + "\n".join(lines))

    # A proxy is useful in the full research report; it is not reported EBITDA.
    highlights = [metric_line(row) for row in n.snapshot if row.code in {"REVENUE_FROM_OPERATIONS", "PAT", "EBITDA"}]
    section("RESULT HIGHLIGHTS", highlights or ["Result figures are not available yet."])
    assessment = n.previous_thesis.get("current_assessment")
    if assessment is None:
        from app.services.investor_verdict import results_verdict
        assessment = results_verdict(n.evidence_registry)["verdict"]
    sentiment = {"POSITIVE": "Positive", "VERY_POSITIVE": "Positive",
        "NEGATIVE": "Negative", "VERY_NEGATIVE": "Negative"}.get(assessment, "Neutral")
    section("AI THESIS", ["<b>" + sentiment + "</b> — " + _thesis(n)])
    section("MANAGEMENT SAYS", ["• “" + escape(_short(row.text, 220)) + "”" for row in n.management_highlights[:2]]
        or ["No management commentary available for this period."])
    caveats = []
    if n.freshness.get("status") == "POTENTIALLY_STALE":
        caveats.append("A newer result is known; this is an older stored quarter.")
    if n.verification.get("status") == "CONFLICTING" or any("conflict" in row.text.lower() for row in n.contradictions):
        caveats.append("Source figures conflict; this reading is provisional.")
    elif n.verification.get("status") != "VERIFIED":
        caveats.append("Source not independently verified.")
    elif n.confidence.get("level") == "LOW":
        caveats.append("Limited evidence; treat this as an early reading.")
    if caveats:
        sections.append("<i>" + escape(" ".join(caveats)) + "</i>")
    if n.full_report.url:
        sections.append(f'<a href="{escape(n.full_report.url, quote=True)}">Full report</a>')
    # Pack whole sections, or whole lines of a long section. No tag/entity is sliced.
    chunks, current = [], []
    budget = limit-units(header)-70
    for block in sections:
        blocks = [block]
        if units(block) > budget:
            lines = block.split("\n")
            title, lines = (lines[0], lines[1:]) if lines[0].startswith("<b>") else ("", lines)
            blocks = []
            piece = title
            for line in lines:
                if units(line) > budget-units(title)-2:
                    raise ValueError("A notification line exceeds the Telegram part limit")
                if units(piece+"\n"+line) > budget:
                    blocks.append(piece)
                    piece = title
                piece += "\n"+line
            blocks.append(piece)
        for piece in blocks:
            if current and units("\n\n".join(current+[piece])) > budget:
                chunks.append("\n\n".join(current))
                current = []
            current.append(piece)
    if current:
        chunks.append("\n\n".join(current))
    return [header + (f" · {i}/{len(chunks)}" if len(chunks) > 1 else "") + "\n\n" + body for i, body in enumerate(chunks, 1)]
