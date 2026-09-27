import { useEffect, useState } from "react";
import { api, type CompanyEvent, type EventAnalysis, type EventCheckSummary } from "./api";
import "./events.css";

const analysisRequests = new Map<number, Promise<EventAnalysis>>();

function analyzeEventOnce(eventId: number) {
  let request = analysisRequests.get(eventId);
  if (!request) {
    request = api.analyzeEvent(eventId).finally(() => analysisRequests.delete(eventId));
    analysisRequests.set(eventId, request);
  }
  return request;
}

function eventLabel(value: string) {
  return value
    .split("_")
    .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
    .join(" ");
}

function published(value: string, precision: "TIMESTAMP" | "DATE" = "TIMESTAMP") {
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    ...(precision === "TIMESTAMP" ? { hour: "numeric", minute: "2-digit" } : {}),
    timeZone: "Asia/Kolkata",
  }).format(new Date(value));
}

function originLabel(value: CompanyEvent["data_origin"]) {
  return value === "FIXTURE" ? "DEMO FIXTURE" : value;
}

function sourceLabel(value: string) {
  return value === "ESDS_IR" ? "ESDS Investor Relations" : value;
}

function isWebUrl(value: string) {
  return value.startsWith("https://") || value.startsWith("http://");
}

export function EventsSection({
  events,
  error,
  onCheck,
  onOpen,
}: {
  events: CompanyEvent[];
  error: string;
  onCheck: () => Promise<EventCheckSummary>;
  onOpen: (id: number) => void;
}) {
  const [checking, setChecking] = useState(false);
  const [summary, setSummary] = useState<EventCheckSummary | null>(null);
  const [checkError, setCheckError] = useState("");

  async function check() {
    setChecking(true);
    setCheckError("");
    try {
      setSummary(await onCheck());
    } catch (cause) {
      setCheckError(
        cause instanceof Error ? cause.message : "Event check failed.",
      );
    } finally {
      setChecking(false);
    }
  }

  return (
    <section id="events" className="section-block events-block">
      <div className="section-heading">
        <div>
          <p className="eyebrow">PRIMARY SOURCE WATCH</p>
          <h2>Company events</h2>
        </div>
        <button
          className="button button-outline button-small"
          onClick={() => void check()}
          disabled={checking}
        >
          {checking ? "Checking…" : "Check for events"}
        </button>
      </div>
      <p className="events-intro">
        NSE-listed companies are monitored automatically in live mode. The NSE feed
        contains recent filings, so a new company may have no current event.
        Each event keeps its source and document trail. Open one for a short AI brief.
      </p>
      {summary && (
        <div className="event-summary" role="status">
          Checked {summary.sources_checked}{" "}
          {summary.sources_checked === 1 ? "source" : "sources"} ·{" "}
          {summary.company_matches} company matches · {summary.new_events}{" "}
          new · {summary.documents_downloaded} PDFs stored
          {summary.errors.length > 0 && (
            <span> · {summary.errors.join("; ")}</span>
          )}
          <span> · {summary.duplicates} duplicates</span>
        </div>
      )}
      {summary && Object.keys(summary.sources).length > 0 && (
        <div className="event-source-results" aria-label="Source check results">
          {Object.entries(summary.sources).map(([source, result]) => (
            <span key={source}>{sourceLabel(source)}: {result.status} · {result.received} received · {result.new_events} new
              {result.unresolved_symbols?.length ? ` · unresolved: ${result.unresolved_symbols.join(", ")}` : ""}</span>
          ))}
        </div>
      )}
      {(error || checkError) && (
        <div className="event-error" role="alert">
          {error || checkError}
        </div>
      )}
      <div className="event-list">
        {events.length ? (
          events.map((event) => (
            <button
              className="event-row"
              key={event.id}
              onClick={() => onOpen(event.id)}
            >
              <span className="event-mark">{event.symbol.slice(0, 2)}</span>
              <span className="event-main">
                <span className="event-row-top">
                  <strong>{event.symbol}</strong>
                  <span className="event-type">
                    {eventLabel(event.event_type)}
                  </span>
                  <span className={`fixture-pill origin-${event.data_origin.toLowerCase()}`}>
                    {originLabel(event.data_origin)}
                  </span>
                </span>
                <span className="event-title">{event.title}</span>
                <span className="event-meta">
                  {published(event.published_at, event.publication_precision)} · {sourceLabel(event.source)} ·{" "}
                  {event.documents.length
                    ? `${event.documents.length} document`
                    : "No document"}
                </span>
              </span>
              <span className="event-chevron" aria-hidden="true">
                →
              </span>
            </button>
          ))
        ) : (
          <div className="event-empty">
            <strong>No company events yet</strong>
            <span>
              In live mode, add an NSE company and run a check. The current feed
              may have no filing for it. Fixture mode needs an open position and
              uses labeled sample disclosures.
            </span>
          </div>
        )}
      </div>
    </section>
  );
}

export function EventDetails({
  eventId,
  onBack,
}: {
  eventId: number;
  onBack: () => void;
}) {
  const [event, setEvent] = useState<CompanyEvent | null>(null);
  const [error, setError] = useState("");
  const [processingFinancials, setProcessingFinancials] = useState(false);
  const [financialMessage, setFinancialMessage] = useState("");
  const [analysis, setAnalysis] = useState<EventAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .event(eventId)
      .then((value) => {
        if (active) setEvent(value);
      })
      .catch((cause) => {
        if (active)
          setError(cause instanceof Error ? cause.message : "Event not found.");
      });
    return () => {
      active = false;
    };
  }, [eventId]);

  useEffect(() => {
    let active = true;
    setAnalysis(null);
    setAnalysisError("");
    setAnalyzing(true);
    analyzeEventOnce(eventId).then((value) => {
      if (active) setAnalysis(value);
    }).catch((cause) => {
      if (active) setAnalysisError(cause instanceof Error ? cause.message : "Event analysis failed.");
    }).finally(() => {
      if (active) setAnalyzing(false);
    });
    return () => { active = false; };
  }, [eventId]);

  async function regenerateAnalysis() {
    setAnalyzing(true);
    setAnalysisError("");
    try {
      setAnalysis(await api.analyzeEvent(eventId, true));
    } catch (cause) {
      setAnalysisError(cause instanceof Error ? cause.message : "Event analysis failed.");
    } finally {
      setAnalyzing(false);
    }
  }

  function evidence(ids: string[]) {
    return <span className="event-ai-evidence">{ids.map((id) => {
      const source = analysis?.evidence_registry[id];
      const url = source?.document_url || source?.source_url;
      return url && (isWebUrl(url) || url.startsWith("/api/documents/"))
        ? <a key={id} href={url} target="_blank" rel="noreferrer">[{id}] ↗</a>
        : <span key={id}>[{id}]</span>;
    })}</span>;
  }

  return (
    <div className="event-detail-page">
      <button className="text-link event-back" onClick={onBack}>
        ← Back to events
      </button>
      {error && (
        <div className="event-error" role="alert">
          {error}
        </div>
      )}
      {!event && !error && (
        <div className="loading-state">Loading event details…</div>
      )}
      {event && (
        <>
          <p className="eyebrow">COMPANY DISCLOSURE / {sourceLabel(event.source)} / {originLabel(event.data_origin)}</p>
          <div className="event-detail-heading">
            <span className="event-mark large">{event.symbol.slice(0, 2)}</span>
            <div>
              <span className="event-detail-company">
                {event.symbol} · {event.company_name}
              </span>
              <h1>{event.title}</h1>
            </div>
          </div>
          {event.data_origin === "FIXTURE" && (
            <div className="fixture-notice">
              Demo fixture. This is not a live company filing.
            </div>
          )}
          <div className="panel event-ai-panel">
            <div className="panel-heading">
              <div><p className="eyebrow">LOCAL AI / SOURCE LINKED</p><h2>Event brief</h2></div>
              <button className="button button-outline button-small" disabled={analyzing}
                onClick={() => void regenerateAnalysis()}>
                {analyzing ? "Analyzing…" : "Regenerate"}
              </button>
            </div>
            {analyzing && !analysis && <p className="event-ai-status">Preparing a short assessment from the disclosure…</p>}
            {analysisError && <p className="event-error" role="alert">{analysisError}</p>}
            {analysis && analysis.status !== "SUCCESS" &&
              <p className="event-error" role="alert">Analysis {analysis.status.toLowerCase()}: {analysis.failure_reason}</p>}
            {analysis?.structured_output && <div className="event-ai-content">
              <p>{analysis.structured_output.summary.text} {evidence(analysis.structured_output.summary.evidence_ids)}</p>
              <div className="event-ai-grid">
                <div><strong>Why it matters</strong><p>{analysis.structured_output.why_it_matters.text} {evidence(analysis.structured_output.why_it_matters.evidence_ids)}</p></div>
                <div><strong>Research action · {analysis.structured_output.recommendation.action.replaceAll("_", " ")}</strong>
                  <p>{analysis.structured_output.recommendation.reason} {evidence(analysis.structured_output.recommendation.evidence_ids)}</p></div>
                <div><strong>What to watch</strong><p>{analysis.structured_output.what_next.text} {evidence(analysis.structured_output.what_next.evidence_ids)}</p></div>
                {analysis.structured_output.management_says &&
                  <div><strong>Management says</strong><p>{analysis.structured_output.management_says.text} {evidence(analysis.structured_output.management_says.evidence_ids)}</p></div>}
              </div>
            </div>}
            <p className="event-ai-footnote">Research action reflects this disclosure only. Check the cited source before acting.</p>
          </div>
          {event.data_origin === "LIVE" && event.event_type === "FINANCIAL_RESULTS" && (
            <div className="event-financial-action">
              <button className="button button-outline button-small" disabled={processingFinancials}
                onClick={() => {
                  setProcessingFinancials(true);
                  void api.processFinancials(event.id).then((result) => {
                    setFinancialMessage(`Financial processing: ${result.status.toLowerCase().replaceAll("_", " ")} · ${result.periods_created} new periods. Open Financials to inspect validated numbers and review flags.`);
                  }).catch((cause) => {
                    setFinancialMessage(cause instanceof Error ? cause.message : "Financial processing failed.");
                  }).finally(() => setProcessingFinancials(false));
                }}>{processingFinancials ? "Processing…" : "Process financial results"}</button>
              <a className="text-link" href="#financials">View full results analysis →</a>
              {financialMessage && <span role="status">{financialMessage}</span>}
            </div>
          )}
          <div className="event-detail-grid">
            <div className="panel">
              <p className="eyebrow">DISCLOSURE</p>
              <h2>Details</h2>
              <div className="event-facts">
                <div>
                  <span>Event type</span>
                  <strong>{eventLabel(event.event_type)}</strong>
                </div>
                <div>
                  <span>Published</span>
                  <strong>{published(event.published_at, event.publication_precision)}{event.publication_precision === "DATE" ? " (document date)" : ""}</strong>
                </div>
                <div>
                  <span>Source</span>
                  <strong>{sourceLabel(event.source)}</strong>
                </div>
                <div>
                  <span>Origin</span>
                  <strong>{originLabel(event.data_origin)}</strong>
                </div>
                <div>
                  <span>Processing</span>
                  <strong>{event.processing_status}</strong>
                </div>
              </div>
              {event.period_end_date && (
                <p className="event-description">Reporting period ended {event.period_end_date}{event.quarter ? ` · Q${event.quarter}` : ""}{event.fiscal_year ? ` ${event.fiscal_year}` : ""}{event.reporting_basis ? ` · ${event.reporting_basis.toLowerCase()}` : ""}</p>
              )}
              <p className="event-description">
                {event.description ||
                  "No description was supplied by the source."}
              </p>
            </div>
            <div className="panel">
              <p className="eyebrow">TRACEABILITY</p>
              <h2>{event.data_origin === "LIVE" ? "Official sources" : "Source trail"}</h2>
              <div className="event-references">
                {event.references.map((reference, index) => (
                  <div
                    className="event-reference"
                    key={`${reference.source}-${reference.external_id ?? index}`}
                  >
                    <strong>{sourceLabel(reference.source)}</strong>
                    <span>{reference.external_id || "No external ID"}</span>
                    {isWebUrl(reference.source_url) ? (
                      <a
                        href={reference.source_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open source ↗
                      </a>
                    ) : (
                      <small>{reference.source_url}</small>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="panel event-documents">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">EVIDENCE</p>
                <h2>Documents</h2>
              </div>
              <span className="panel-caption">
                {event.documents.length} ATTACHED
              </span>
            </div>
            {event.documents.length ? (
              event.documents.map((document) => (
                <div className="event-document" key={document.id}>
                  <span className="document-mark">PDF</span>
                  <span>
                    <strong>{document.file_name || "Document"}</strong>
                    <small>
                      {document.processing_status} · {document.page_count}{" "}
                      {document.page_count === 1 ? "page" : "pages"}
                      {document.sha256_hash
                        ? ` · SHA-256 ${document.sha256_hash.slice(0, 12)}…`
                        : ""}
                      {document.downloaded_at
                        ? ` · Downloaded ${published(document.downloaded_at)}`
                        : ""}
                    </small>
                  </span>
                  {document.download_url && (
                    <a
                      className="button button-outline button-small"
                      href={document.download_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open PDF ↗
                    </a>
                  )}
                </div>
              ))
            ) : (
              <p className="event-no-documents">
                No document was supplied for this event.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
