import { useEffect, useState, type FormEvent } from "react";
import {
  accuracyApi,
  type AccuracyRow,
  type AccuracyPage,
  type AccuracyStats,
  type ThesisAccuracy,
  type Calibration,
} from "./accuracyApi";
import "./accuracy.css";
const label = (value: string) => value.replaceAll("_", " ").toLowerCase();
const number = (value: string | null | undefined) =>
  value == null
    ? "—"
    : Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 });
const pct = (value: string | null) =>
  value == null ? "Insufficient data" : `${(Number(value) * 100).toFixed(1)}%`;
function expected(row: AccuracyRow) {
  const d = row.definition;
  return d.expectation_type === "RANGE"
    ? `${number(d.lower_value)}–${number(d.upper_value)} ${label(d.unit)}`
    : d.expectation_type === "DIRECTIONAL"
      ? label(d.direction || "")
      : d.expectation_type === "QUALITATIVE"
        ? d.description
        : `${number(d.expected_value)} ${label(d.unit)}`;
}
function EvidenceLinks({ row }: { row: AccuracyRow }) {
  const entries = Object.entries(row.evaluation?.result.evidence || {});
  const source = row.provenance.source_snapshot;
  return (
    <span className="accuracy-evidence">
      {source?.document_url && (
        <a href={source.document_url} target="_blank" rel="noreferrer">
          Expectation source · page {source.page}
        </a>
      )}
      {entries
        .filter(([, e]) => e.document_url || e.document_id)
        .map(([key, e]) => (
          <a
            key={key}
            href={
              e.document_url ||
              `/api/documents/${e.document_id}/file#page=${e.page || 1}`
            }
            target="_blank"
            rel="noreferrer"
          >
            {key} · page {e.page || 1}
          </a>
        ))}
      {!source?.document_url && !entries.length && (
        <span>No outcome evidence yet</span>
      )}
    </span>
  );
}
function Revisions({ symbol, id }: { symbol: string; id: number }) {
  const [rows, setRows] = useState<AccuracyRow[] | null>(null);
  const [error, setError] = useState("");
  return (
    <details
      onToggle={(event) => {
        if (event.currentTarget.open && !rows)
          accuracyApi
            .revisions(symbol, id)
            .then(setRows)
            .catch((e) => setError(e.message));
      }}
    >
      <summary>Revision history</summary>
      {error ? (
        <p role="alert">{error}</p>
      ) : !rows ? (
        <p>Loading history…</p>
      ) : (
        rows.map((row) => (
          <div key={row.revision_id} className="accuracy-revision">
            <strong>
              Version {row.version} · {label(row.status)}
            </strong>
            <p>
              {expected(row)} ·{" "}
              {row.evaluation ? label(row.evaluation.outcome) : "Pending"}
            </p>
            <small>
              Captured {new Date(row.created_at).toLocaleString()} · cutoff{" "}
              {new Date(row.cutoff_at).toLocaleString()}
            </small>
            <EvidenceLinks row={row} />
          </div>
        ))
      )}
    </details>
  );
}
function Reliability({ group }: { group: Calibration }) {
  return (
    <article className="accuracy-calibration">
      <h5>
        {label(group.metric)} · {label(group.source)} · {label(group.category)}
      </h5>
      <p>
        n = {group.n} · Brier score: {number(group.brier)} ·{" "}
        {label(group.status)}
      </p>
      <svg
        viewBox="0 0 300 235"
        role="img"
        aria-label={`Reliability chart for ${label(group.metric)} from ${label(group.source)}`}
      >
        <line x1="40" y1="195" x2="270" y2="195" className="accuracy-axis" />
        <line x1="40" y1="195" x2="40" y2="15" className="accuracy-axis" />
        <line x1="40" y1="195" x2="270" y2="15" className="accuracy-ideal" />
        <text x="40" y="211">
          0%
        </text>
        <text x="244" y="211">
          100%
        </text>
        <text x="3" y="22">
          100%
        </text>
        <text x="92" y="230">
          Predicted confidence
        </text>
        <text x="44" y="12">
          Observed success
        </text>
        {group.buckets.map((b) => (
          <circle
            key={b.lower}
            cx={40 + Number(b.predicted) * 230}
            cy={195 - Number(b.observed) * 180}
            r="5"
            className={
              b.status === "INSUFFICIENT_DATA"
                ? "accuracy-point uncertain"
                : "accuracy-point"
            }
          >
            <title>
              {pct(b.predicted)} predicted; {pct(b.observed)} observed; n ={" "}
              {b.n}; {label(b.status)}
            </title>
          </circle>
        ))}
      </svg>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Confidence bucket</th>
              <th>Predicted</th>
              <th>Observed</th>
              <th>n</th>
            </tr>
          </thead>
          <tbody>
            {group.buckets.map((b) => (
              <tr key={b.lower}>
                <td>
                  {pct(b.lower)}–{pct(b.upper)}
                </td>
                <td>{pct(b.predicted)}</td>
                <td>{pct(b.observed)}</td>
                <td>
                  {b.n}
                  {b.status === "INSUFFICIENT_DATA" ? " · limited" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <small>{group.warning}</small>
    </article>
  );
}
function Register({
  symbol,
  scope,
  saved,
  policy,
  theses,
}: {
  symbol: string;
  scope: string;
  saved: () => void;
  policy?: AccuracyStats["policy"];
  theses: ThesisAccuracy["available_theses"];
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [success, setSuccess] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setError("");
    setBusy(true);
    setSuccess("");
    const metric = String(values.get("metric"));
    const probability = String(values.get("confidence"));
    const source = String(values.get("source_type"));
    const kind = String(values.get("expectation_type"));
    try {
      await accuracyApi.create(symbol, {
        metric,
        financial_scope: scope,
        period: values.get("period"),
        period_type: values.get("period_type"),
        unit: values.get("unit"),
        currency: String(values.get("unit")).startsWith("INR") ? "INR" : "NONE",
        expectation_type: kind,
        expected_value: kind === "EXACT" ? values.get("expected_value") : null,
        lower_value: kind === "RANGE" ? values.get("expected_value") : null,
        upper_value: kind === "RANGE" ? values.get("upper_value") : null,
        description: values.get("description"),
        cutoff_at: new Date(String(values.get("cutoff_at"))).toISOString(),
        source_type: source,
        source_reference: values.get("source_reference"),
        evidence_id: values.get("evidence_id") || null,
        confidence: probability || null,
        confidence_event: probability ? "MET_UNDER_FROZEN_TOLERANCE" : null,
        guidance_kind:
          source === "MANAGEMENT" ? values.get("guidance_kind") : null,
        historical_import: values.get("historical_import") === "on",
        thesis_revision_id: values.get("thesis_revision_id")
          ? Number(values.get("thesis_revision_id"))
          : null,
      });
      form.reset();
      setSuccess("Expectation saved with its capture time and provenance.");
      saved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save expectation");
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="accuracy-register">
      <summary>Register an expectation</summary>
      <form onSubmit={submit}>
        <p>
          Capture the expectation before its result cutoff. Imports remain
          separate from prospective accuracy statistics.
        </p>
        <label>
          Original thesis version (optional)
          <select
            name="thesis_revision_id"
            aria-label="Original thesis version"
          >
            <option value="">No thesis link</option>
            {theses.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} · version {t.version} · {t.created_at.slice(0, 10)}
              </option>
            ))}
          </select>
        </label>
        <div className="accuracy-form-grid">
          <label>
            Metric
            <select name="metric">
              {[
                "REVENUE_FROM_OPERATIONS",
                "EBITDA",
                "EBITDA_MARGIN",
                "PAT",
                "PAT_MARGIN",
                "REVENUE_YOY",
                "PAT_YOY",
                "CAPITAL_EXPENDITURE",
                "TOTAL_DEBT",
              ].map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
          <label>
            Target period end
            <input type="date" name="period" required />
          </label>
          <label>
            Period type
            <select name="period_type">
              {["QUARTERLY", "ANNUAL", "HALF_YEARLY", "NINE_MONTH"].map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
          <label>
            Expectation type
            <select name="expectation_type">
              <option value="EXACT">Exact</option>
              <option value="RANGE">Range</option>
            </select>
          </label>
          <label>
            Expected value / range minimum
            <input type="number" step="any" name="expected_value" required />
          </label>
          <label>
            Range maximum
            <input type="number" step="any" name="upper_value" />
          </label>
          <label>
            Unit
            <select name="unit">
              {[
                "INR_CRORE",
                "INR_LAKH",
                "INR_MILLION",
                "INR",
                "PERCENT",
                "BASIS_POINTS",
              ].map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
          <label>
            Cutoff (local time)
            <input type="datetime-local" name="cutoff_at" required />
          </label>
          <label>
            Source
            <select name="source_type">
              {["USER", "ANALYST", "MANAGEMENT", "AI"].map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
          <label>
            Source reference
            <input name="source_reference" required maxLength={2000} />
          </label>
          <label>
            Evidence ID (required for management / AI)
            <input
              name="evidence_id"
              placeholder="S12 or F34"
              pattern="[FS][1-9][0-9]*"
            />
          </label>
          <label>
            Guidance kind
            <select name="guidance_kind">
              {["FIRM_TARGET", "ESTIMATE", "ASPIRATION", "CONDITIONAL"].map(
                (m) => (
                  <option key={m}>{m}</option>
                ),
              )}
            </select>
          </label>
          <label>
            Probability of meeting the tolerance (optional, 0–1)
            <input
              type="number"
              min="0"
              max="1"
              step="0.01"
              name="confidence"
            />
          </label>
        </div>
        <label>
          Original expectation
          <textarea
            name="description"
            required
            minLength={3}
            maxLength={4000}
          />
        </label>
        <label className="accuracy-check">
          <input type="checkbox" name="historical_import" /> Historical import
          (excluded from prospective scores)
        </label>
        <p className="muted">
          {policy
            ? `Configured tolerances: ${policy.amount_met_pct}% met / ${policy.amount_partial_pct}% partial for amounts; ${policy.percentage_met_pp} / ${policy.percentage_met_pp * 2} percentage points for percentages.`
            : "The configured tolerance policy is frozen when the expectation is captured."}{" "}
          Historical confidence and tolerance remain fixed.
        </p>
        {error && <p role="alert">{error}</p>}
        {success && <p role="status">{success}</p>}
        <button className="button button-primary" disabled={busy} type="submit">
          {busy ? "Saving…" : "Save expectation"}
        </button>
      </form>
    </details>
  );
}
export function HistoricalAccuracy({
  symbol,
  scope,
}: {
  symbol: string;
  scope: string;
}) {
  const [rows, setRows] = useState<AccuracyPage | null>(null);
  const [stats, setStats] = useState<AccuracyStats | null>(null);
  const [guidance, setGuidance] = useState<AccuracyPage | null>(null);
  const [thesis, setThesis] = useState<ThesisAccuracy | null>(null);
  const [period, setPeriod] = useState("");
  const [metric, setMetric] = useState("");
  const [source, setSource] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const query = new URLSearchParams({
      scope,
      ...(period ? { period } : {}),
      ...(metric ? { metric } : {}),
      ...(source ? { source } : {}),
    });
    Promise.all([
      accuracyApi.list(symbol, `${query}&offset=${offset}&limit=20`),
      accuracyApi.stats(symbol, String(query)),
      accuracyApi.guidance(symbol, `scope=${scope}&limit=20&offset=${offset}`),
      accuracyApi.thesis(symbol, `scope=${scope}&limit=20&offset=${offset}`),
    ])
      .then(([r, s, g, t]) => {
        if (active) {
          setRows(r);
          setStats(s);
          setGuidance(g);
          setThesis(t);
        }
      })
      .catch((e) => {
        if (active)
          setError(e.message || "Historical accuracy could not load.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [symbol, scope, period, metric, source, offset, revision]);
  async function evaluate() {
    setBusy(true);
    setError("");
    try {
      await accuracyApi.evaluate(symbol);
      setRevision((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Evaluation failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="thesis-panel accuracy-panel"
      aria-label="Historical Accuracy"
    >
      <header className="thesis-header">
        <div>
          <span className="thesis-eyebrow">EXPECTATIONS & ACCOUNTABILITY</span>
          <h3>Historical Accuracy</h3>
          <p>
            {symbol} · {label(scope)} · Expectations captured before results,
            measured against verified outcomes.
          </p>
        </div>
        <button
          className="button button-outline button-small"
          onClick={evaluate}
          disabled={busy}
        >
          {busy ? "Evaluating…" : "Evaluate available results"}
        </button>
      </header>
      <div className="accuracy-filters">
        <label>
          Period end
          <input
            type="date"
            value={period}
            onChange={(e) => {
              setPeriod(e.target.value);
              setOffset(0);
            }}
          />
        </label>
        <label>
          Metric
          <select
            value={metric}
            onChange={(e) => {
              setMetric(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All metrics</option>
            {[
              "REVENUE_FROM_OPERATIONS",
              "EBITDA",
              "EBITDA_MARGIN",
              "PAT",
              "PAT_MARGIN",
              "REVENUE_YOY",
              "PAT_YOY",
              "CAPITAL_EXPENDITURE",
              "TOTAL_DEBT",
              "BUSINESS_MILESTONE",
              "THESIS_CLAIM",
            ].map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </label>
        <label>
          Source
          <select
            aria-label="Source filter"
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All sources</option>
            {["USER", "ANALYST", "MANAGEMENT", "AI"].map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </label>
      </div>
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button
            className="button button-outline button-small"
            onClick={() => setRevision((n) => n + 1)}
          >
            Retry
          </button>
        </div>
      )}
      {loading ? (
        <p role="status">Loading historical accuracy…</p>
      ) : (
        !error &&
        stats &&
        rows && (
          <>
            <div className="accuracy-overview">
              {[
                ["Total expectations", stats.total],
                ["Evaluated prospective", stats.evaluated],
                ["Met", stats.met],
                ["Partially met", stats.partially_met],
                ["Missed", stats.missed],
                ["Unresolved / excluded", stats.unresolved],
              ].map(([name, value]) => (
                <div key={name}>
                  <strong>{value}</strong>
                  <span>{name}</span>
                </div>
              ))}
            </div>
            <p>
              Evidence coverage: {stats.evidence_coverage.with_evidence} /{" "}
              {stats.evidence_coverage.n} · {pct(stats.evidence_coverage.ratio)}
              . Scores use original expectations; minimum n ={" "}
              {stats.policy.minimum_n}.
            </p>
            <h4>Expectation vs actual</h4>
            {!rows.total ? (
              <p className="table-empty">
                No expectations match these filters. Register an expectation to
                begin an auditable history.
              </p>
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      {[
                        "Period",
                        "Metric / source",
                        "Expected",
                        "Actual",
                        "Variance",
                        "Outcome",
                        "Evidence",
                      ].map((h) => (
                        <th key={h}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.items.map((row) => (
                      <tr key={row.id}>
                        <td>
                          {row.definition.period}
                          <small>{label(row.definition.period_type)}</small>
                        </td>
                        <td>
                          {label(row.definition.metric)}
                          <small>
                            {label(row.definition.source_type)} · v{row.version}
                          </small>
                          <Revisions symbol={symbol} id={row.id} />
                        </td>
                        <td>
                          {expected(row)}
                          {row.definition.historical_import && (
                            <small>Historical import</small>
                          )}
                        </td>
                        <td>
                          {number(row.evaluation?.result.actual_value)}
                          <small>
                            {label(
                              row.evaluation?.result.normalized_unit || "",
                            )}
                          </small>
                        </td>
                        <td>
                          {number(row.evaluation?.result.signed_error)}
                          <small>forecast − actual</small>
                        </td>
                        <td>
                          <span
                            className={`accuracy-outcome outcome-${row.evaluation?.outcome || "PENDING"}`}
                          >
                            {label(row.evaluation?.outcome || row.status)}
                          </span>
                          <small>{row.evaluation?.result.reason}</small>
                        </td>
                        <td>
                          <EvidenceLinks row={row} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {stats.groups.length > 0 && (
              <>
                <h4>Accuracy by metric</h4>
                <div className="accuracy-group-grid">
                  {stats.groups.map((g, i) => (
                    <article key={i}>
                      <h5>
                        {label(g.metric)} · {label(g.source)}
                      </h5>
                      <p>
                        {label(g.category)} · n = {g.n} · met rate:{" "}
                        {pct(g.met_rate)}
                      </p>
                      <p>
                        Median absolute error: {number(g.absolute_error.median)}{" "}
                        {label(g.unit)} · n = {g.absolute_error.n}
                      </p>
                      <details>
                        <summary>Period and rolling history</summary>
                        {g.rolling_last_20.map((p) => (
                          <p key={p.through_period}>
                            Through {p.through_period}: {p.met} met / {p.n}{" "}
                            forecasts (last 20)
                          </p>
                        ))}
                      </details>
                    </article>
                  ))}
                </div>
              </>
            )}
            <h4>Confidence calibration</h4>
            <p>
              Probability means meeting the policy frozen at capture.
              Qualitative confidence labels are excluded.
            </p>
            {!stats.calibration.some((g) => g.n > 0) ? (
              <p>Insufficient data: no eligible probabilistic outcomes.</p>
            ) : (
              <div className="accuracy-group-grid">
                {stats.calibration
                  .filter((g) => g.n > 0)
                  .map((g, i) => (
                    <Reliability key={i} group={g} />
                  ))}
              </div>
            )}
            <h4>Management guidance</h4>
            <p>
              Management commitments are assessed separately from Almonds
              forecasts. Missing disclosure is unresolved.
            </p>
            {!guidance?.items.length ? (
              <p>No registered guidance for this company and scope.</p>
            ) : (
              guidance.items.map((row) => (
                <article className="accuracy-guidance" key={row.id}>
                  <blockquote>
                    {row.provenance.source_snapshot?.source_quote ||
                      row.definition.description}
                  </blockquote>
                  <p>
                    Issued{" "}
                    {row.provenance.source_snapshot?.published_at?.slice(
                      0,
                      10,
                    ) || "Unknown"}{" "}
                    · Target {row.definition.period} ·{" "}
                    {label(row.definition.guidance_kind || "ESTIMATE")}
                  </p>
                  <p>
                    Expected {expected(row)} · Actual{" "}
                    {number(row.evaluation?.result.actual_value)} ·{" "}
                    {label(row.evaluation?.outcome || row.status)}
                    {row.evaluation?.result.withdrawn ||
                    row.definition.lifecycle === "WITHDRAWN"
                      ? " · Withdrawn"
                      : ""}
                  </p>
                  <EvidenceLinks row={row} />
                  <Revisions symbol={symbol} id={row.id} />
                </article>
              ))
            )}
            <h4>Thesis history</h4>
            {!thesis?.items.length ? (
              <p>No expectations linked to an original thesis version.</p>
            ) : (
              thesis.items.map((t) => (
                <article
                  className="accuracy-guidance"
                  key={t.thesis_revision_id}
                >
                  <h5>
                    {t.original_thesis.name} · version {t.version} ·{" "}
                    {label(t.outcome)}
                  </h5>
                  <p>{t.original_thesis.description}</p>
                  <p>
                    {t.validated.length} validated · {t.contradicted.length}{" "}
                    contradicted · {t.unresolved.length} unresolved assumptions
                  </p>
                  {t.expectations.map((row) => (
                    <div key={row.revision_id}>
                      <p>
                        {row.definition.description} ·{" "}
                        {label(row.evaluation?.outcome || row.status)}
                      </p>
                      <EvidenceLinks row={row} />
                    </div>
                  ))}
                  <small>{t.interpretation}</small>
                </article>
              ))
            )}
            <div className="accuracy-pagination">
              <button
                className="button button-outline button-small"
                disabled={offset === 0}
                onClick={() => setOffset((n) => Math.max(0, n - 20))}
              >
                Previous
              </button>
              <span>Page {offset / 20 + 1}</span>
              <button
                className="button button-outline button-small"
                disabled={
                  offset + 20 >=
                  Math.max(rows.total, guidance?.total || 0, thesis?.total || 0)
                }
                onClick={() => setOffset((n) => n + 20)}
              >
                Next
              </button>
            </div>
          </>
        )
      )}
      <Register
        symbol={symbol}
        scope={scope}
        policy={stats?.policy}
        theses={thesis?.available_theses || []}
        saved={() => setRevision((n) => n + 1)}
      />
    </section>
  );
}
