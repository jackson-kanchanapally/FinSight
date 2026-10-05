import { useEffect, useState, type FormEvent } from "react";
import { api, type Company, type MemoryResult, type MemoryStatus } from "./api";
import "./memory.css";

const prompts = [
  "What did management previously guide?",
  "What explained margin changes?",
  "Which risks have appeared before?",
  "How has company strategy changed?",
];

function Research({ symbol }: { symbol: string }) {
  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [result, setResult] = useState<MemoryResult | null>(null);
  const [query, setQuery] = useState(prompts[0]);
  const [asOf, setAsOf] = useState("");
  const [type, setType] = useState("");
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .memoryStatus(symbol)
      .then((value) => {
        if (active) setStatus(value);
      })
      .catch((cause) => {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not load memory status.",
          );
      });
    return () => {
      active = false;
    };
  }, [symbol]);
  async function index() {
    setBusy("index");
    setError("");
    try {
      const indexed = await api.indexMemory(symbol);
      if (["UNAVAILABLE", "PARTIAL", "DISABLED"].includes(indexed.status))
        setError(
          indexed.reason ||
            "Some documents could not be indexed. Inspect document status below.",
        );
      setStatus(await api.memoryStatus(symbol));
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not index documents.",
      );
    } finally {
      setBusy("");
    }
  }
  async function search(event: FormEvent) {
    event.preventDefault();
    setBusy("search");
    setError("");
    setResult(null);
    try {
      setResult(
        await api.queryMemory(symbol, {
          query,
          ...(asOf ? { as_of_date: asOf } : {}),
          ...(type ? { document_type: type } : {}),
          ...(topic ? { topic } : {}),
        }),
      );
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not search company memory.",
      );
    } finally {
      setBusy("");
    }
  }
  return (
    <div className="memory-panel">
      <div className="memory-toolbar">
        <p>
          {status
            ? `${status.documents.filter((d) => d.status === "INDEXED").length} indexed documents · ${status.documents.reduce((sum, d) => sum + d.chunks, 0)} passages`
            : "Loading document status…"}
        </p>
        <button
          className="button button-outline"
          disabled={!!busy}
          onClick={index}
        >
          {busy === "index"
            ? "Indexing parsed documents…"
            : "Index / update documents"}
        </button>
      </div>
      {status?.status === "UNAVAILABLE" && (
        <p role="status">
          Company memory is unavailable. Financial analysis remains usable.
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <form onSubmit={search}>
        <label className="field">
          <span>Search company history</span>
          <input
            required
            minLength={3}
            maxLength={700}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="memory-prompts">
          {prompts.map((prompt) => (
            <button type="button" key={prompt} onClick={() => setQuery(prompt)}>
              {prompt}
            </button>
          ))}
        </div>
        <div className="memory-filters">
          <label className="field">
            <span>Known by date</span>
            <input
              type="date"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Document type</span>
            <select value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">All documents</option>
              {[
                ...new Set(status?.documents.map((d) => d.document_type) || []),
              ].map((t) => (
                <option key={t} value={t}>
                  {t.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Topic</span>
            <select value={topic} onChange={(e) => setTopic(e.target.value)}>
              <option value="">All topics</option>
              {[
                "GUIDANCE",
                "MARGINS",
                "RISKS",
                "STRATEGY",
                "EXECUTION",
                "REVENUE",
                "SEGMENTS",
                "CASH_FLOW",
              ].map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <button className="button button-primary" disabled={!!busy}>
            {busy === "search" ? "Searching…" : "Search evidence"}
          </button>
        </div>
      </form>
      {result && (
        <div aria-live="polite">
          {result.as_of_date && (
            <p>Evidence published by {result.as_of_date}.</p>
          )}
          <p className="muted">
            {result.results.length
              ? "Relevant historical passages. Statements retain their original source; reported promises are not proof of delivery."
              : result.status === "UNAVAILABLE"
                ? "Company memory is unavailable. Try again when local retrieval is running."
                : result.status === "NOT_INDEXED"
                  ? "Index this company's parsed documents to search its history."
                  : "No matching historical evidence for these filters."}
          </p>
          {result.conflicts.map((conflict, i) => (
            <p className="form-error" key={i}>
              Source discrepancy: {conflict.resolution} (
              {conflict.structured_evidence_id})
            </p>
          ))}
          {result.results.map((p) => (
            <article className="memory-passage" key={p.evidence_id}>
              <h4>
                <a href={p.document_url} target="_blank" rel="noreferrer">
                  {p.title} ↗
                </a>
              </h4>
              <p className="muted">
                {p.published_at.slice(0, 10)} · page {p.page}
                {p.section ? ` · ${p.section}` : ""} · {p.source} /{" "}
                {p.data_origin}
              </p>
              {p.speaker && <p>Speaker: {p.speaker}</p>}
              <blockquote>{p.text}</blockquote>
              <details>
                <summary>Evidence reference</summary>
                <p>
                  {p.evidence_id} · relevance {p.score.toFixed(3)}
                </p>
              </details>
            </article>
          ))}
          {!!result.structured_evidence.length && (
            <details>
              <summary>
                Authoritative structured records for these source pages
              </summary>
              <p>{result.authority}</p>
              {result.structured_evidence.map((e) => (
                <p key={e.evidence_id}>
                  {e.evidence_id} · {e.metric_code || e.metric} ·{" "}
                  {e.value ?? `${e.lower_bound}–${e.upper_bound}`} {e.unit} ·{" "}
                  {e.scope} {e.period_end || e.target_period}
                  {e.execution_outcome && (
                    <span>
                      {" "}
                      · Outcome: {e.execution_outcome.replaceAll("_", " ")}
                      {e.actual_value != null
                        ? ` · Actual: ${e.actual_value} ${e.unit}`
                        : ""}
                    </span>
                  )}
                  {e.actual_sources?.map((source) => (
                    <span key={source.fact_id}>
                      {" "}
                      ·{" "}
                      <a
                        href={source.document_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Actual source F{source.fact_id} ↗
                      </a>
                    </span>
                  ))}
                </p>
              ))}
            </details>
          )}
        </div>
      )}
      {!!status?.documents.length && (
        <details>
          <summary>Document indexing status</summary>
          {status.documents.map((d) => (
            <p key={d.document_id}>
              {d.title} · {d.status} · {d.chunks} passages {d.last_error || ""}
            </p>
          ))}
        </details>
      )}
    </div>
  );
}

export function CompanyMemory({ companies }: { companies: Company[] }) {
  const [selected, setSelected] = useState("");
  const symbol = companies.some((c) => c.symbol === selected)
    ? selected
    : companies[0]?.symbol;
  return (
    <section id="memory" className="section-block">
      <div className="section-heading">
        <div>
          <p className="eyebrow">COMPANY HISTORY</p>
          <h2>Company Memory</h2>
        </div>
        <label className="field">
          <span>Company</span>
          <select
            value={symbol || ""}
            onChange={(e) => setSelected(e.target.value)}
          >
            {companies.map((c) => (
              <option key={c.symbol} value={c.symbol}>
                {c.symbol}
              </option>
            ))}
          </select>
        </label>
      </div>
      {symbol ? (
        <Research key={symbol} symbol={symbol} />
      ) : (
        <p>Add a company to research its documents.</p>
      )}
    </section>
  );
}
