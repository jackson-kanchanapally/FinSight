import { useEffect, useState } from "react";
import { request } from "./api";

type Evidence = Record<
  string,
  {
    document_url?: string;
    document_id?: number;
    page?: number;
    source_label?: string;
    published_at?: string;
  }
>;
type Calibration = {
  kind: string;
  bucket: string;
  n: number;
  observed: string;
  expected: string | null;
  brier: string | null;
  mean_interval_width: string | null;
  uncertainty_95: { lower: string; upper: string } | null;
  status: string;
  evaluation_ids: number[];
};
type Window = {
  through_period: string;
  n: number;
  mae: string | null;
  bias: string | null;
  wape_pct: string | null;
  calibration: Calibration[];
  evaluation_ids: number[];
};
type Group = {
  metric: string;
  model: string;
  engine_version: string;
  horizon_quarters: number | null;
  period_type: string;
  unit: string;
  reporting_basis: string;
  n: number;
  mae: string | null;
  rmse: string | null;
  bias: string | null;
  wape_pct: string | null;
  error_unit: string;
  interval_coverage: string | null;
  interval_n: number;
  mean_interval_width: string | null;
  directional_n: number;
  directional_accuracy: string | null;
  status: string;
  calibration: Calibration[];
  rolling_last_20: Window[];
  evaluation_ids: number[];
};
type Summary = {
  minimum_n: number;
  scoring_version: string;
  sample: string;
  methodology: string;
  calibration_note: string;
  overview: {
    total: number;
    evaluated: number;
    eligible_sample: number;
    pending: number;
    missing_actual: number;
    excluded: number;
    status: string;
  };
  groups: Group[];
  exclusions: { reason_code: string; n: number }[];
  total: number;
};
type Comparison = {
  expectation_id: number;
  revision_id: number;
  metric: string;
  scope: string;
  period_type: string;
  period_start: string;
  period_end: string;
  fiscal_year: string;
  reporting_basis: string;
  unit: string;
  currency: string;
  predicted: string | null;
  original_predicted: string | null;
  original_unit: string;
  actual: string | null;
  created_at: string;
  cutoff_at: string;
  information_cutoff: string;
  horizon_quarters: number | null;
  horizon_days: number;
  status: string;
  reason: string;
  reason_code: string;
  actual_published_at: string | null;
  evaluated_at: string | null;
  evaluation_id: number | null;
  absolute_error: string | null;
  signed_error: string | null;
  absolute_percentage_error: string | null;
  calculation_version: string;
  evidence: Evidence;
  later_evaluations: {
    evaluation_id: number;
    actual: string | null;
    absolute_error: string | null;
    evaluated_at: string;
    reason: string;
    evidence: Evidence;
  }[];
};
type Page = { items: Comparison[]; total: number };
const label = (s: string) => s.replaceAll("_", " ").toLowerCase();
const number = (s: string | number | null | undefined) =>
  s == null
    ? "Unavailable"
    : Number(s).toLocaleString(undefined, { maximumFractionDigits: 4 });
const percent = (s: string | null | undefined, fraction = false) =>
  s == null ? "Unavailable" : `${number(Number(s) * (fraction ? 100 : 1))}%`;
const metrics = [
  "REVENUE_FROM_OPERATIONS",
  "EBITDA",
  "EBITDA_MARGIN",
  "PAT",
  "PAT_MARGIN",
  "BASIC_EPS",
  "DILUTED_EPS",
  "ADJUSTED_EBITDA",
  "TOTAL_INCOME",
  "PBT",
  "OPERATING_CASH_FLOW",
  "CAPITAL_EXPENDITURE",
  "TOTAL_DEBT",
  "CASH_AND_CASH_EQUIVALENTS",
  "TOTAL_EQUITY",
  "REVENUE_YOY",
  "REVENUE_QOQ",
  "PAT_YOY",
  "PAT_QOQ",
  "EBITDA_MARGIN_CHANGE_BPS",
];
function Sources({ evidence }: { evidence: Evidence }) {
  return (
    <div className="accuracy-evidence">
      {Object.entries(evidence)
        .filter(([, e]) => e.document_id || e.document_url)
        .map(([id, e]) => (
          <a
            key={id}
            href={
              e.document_url ||
              `/api/documents/${e.document_id}/file#page=${e.page || 1}`
            }
            target="_blank"
            rel="noreferrer"
          >
            {id} · {e.source_label || "Result source"} · page{" "}
            {e.page || "unknown"}
          </a>
        ))}
    </div>
  );
}
function Buckets({ rows }: { rows: Calibration[] }) {
  if (!rows.length)
    return (
      <p>
        Insufficient data: no frozen probability, confidence label or nominal
        prediction interval is available.
      </p>
    );
  return (
    <div className="table-scroll">
      <table>
        <caption>
          Confidence calibration · descriptive historical observations
        </caption>
        <thead>
          <tr>
            {[
              "Confidence definition",
              "Expected",
              "Observed",
              "95% uncertainty",
              "Sample",
              "Brier score",
              "Interval width",
              "Status",
            ].map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.kind}:${r.bucket}`}>
              <td>
                {label(r.kind)} ·{" "}
                {r.kind === "MET_PROBABILITY"
                  ? `${Number(r.bucket) * 10}–${(Number(r.bucket) + 1) * 10}% probability of MET`
                  : r.kind === "PREDICTION_INTERVAL"
                    ? `${Number(r.bucket) * 100}% interval`
                    : r.bucket}
              </td>
              <td>
                {r.kind === "CONFIDENCE_LABEL"
                  ? "Not a probability"
                  : percent(r.expected, true)}
              </td>
              <td>{percent(r.observed, true)}</td>
              <td>
                {r.uncertainty_95
                  ? `${percent(r.uncertainty_95.lower, true)}–${percent(r.uncertainty_95.upper, true)}`
                  : "Unavailable"}
              </td>
              <td>n={r.n}</td>
              <td>{number(r.brier)}</td>
              <td>{number(r.mean_interval_width)}</td>
              <td>
                {r.status === "INSUFFICIENT_DATA"
                  ? "Insufficient data"
                  : "Descriptive sample"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function Pages({
  offset,
  total,
  onChange,
  name,
}: {
  offset: number;
  total: number;
  onChange: (n: number) => void;
  name: string;
}) {
  return total > 20 ? (
    <nav className="accuracy-pagination" aria-label={`${name} pages`}>
      <button
        disabled={!offset}
        onClick={() => onChange(Math.max(0, offset - 20))}
      >
        Previous {name}
      </button>
      <span>
        {offset + 1}–{Math.min(offset + 20, total)} of {total}
      </span>
      <button
        disabled={offset + 20 >= total}
        onClick={() => onChange(offset + 20)}
      >
        Next {name}
      </button>
    </nav>
  ) : null;
}
export function ForecastAccuracyPanel({
  symbol,
  scope,
}: {
  symbol: string;
  scope: string;
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [offset, setOffset] = useState(0);
  const [groupOffset, setGroupOffset] = useState(0);
  const [metric, setMetric] = useState("");
  const [horizon, setHorizon] = useState("");
  const [periodType, setPeriodType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [exclusions, setExclusions] = useState(false);
  const [job, setJob] = useState("");
  const [queuing, setQueuing] = useState(false);
  const base = `/api/companies/${encodeURIComponent(symbol)}/forecasts`;
  useEffect(() => {
    let active = true;
    setLoading(true);
    setSummary(null);
    setPage(null);
    setErrors([]);
    const params = new URLSearchParams({ scope, limit: "20" });
    if (metric) params.set("metric", metric);
    if (horizon) params.set("horizon_quarters", horizon);
    if (periodType) params.set("period_type", periodType);
    if (from) params.set("date_from", from);
    if (to) params.set("date_to", to);
    Promise.allSettled([
      request<Summary>(`${base}/accuracy?${params}&offset=${groupOffset}`),
      request<Page>(
        `${base}/comparisons?${params}&offset=${offset}&exclusions_only=${exclusions}`,
      ),
    ]).then(([s, p]) => {
      if (!active) return;
      setSummary(s.status === "fulfilled" ? s.value : null);
      setPage(p.status === "fulfilled" ? p.value : null);
      setErrors(
        [s, p].flatMap((r, i) =>
          r.status === "rejected"
            ? [
                `${i === 0 ? "Accuracy summaries" : "Comparisons"}: ${r.reason?.message || "Unavailable"}`,
              ]
            : [],
        ),
      );
      setLoading(false);
    });
    const timer = setTimeout(() => setRefresh((v) => v + 1), 60000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [
    base,
    scope,
    metric,
    horizon,
    periodType,
    from,
    to,
    offset,
    groupOffset,
    exclusions,
    refresh,
  ]);
  function filter(setter: (v: string) => void, value: string) {
    setter(value);
    setOffset(0);
    setGroupOffset(0);
  }
  async function evaluate() {
    setQueuing(true);
    try {
      await request(`${base}/evaluate`, { method: "POST" });
      setJob(
        "Scoring queued. Results refresh automatically while the background worker is running.",
      );
    } catch (error) {
      setJob(error instanceof Error ? error.message : "Scoring unavailable");
    } finally {
      setQueuing(false);
    }
  }
  return (
    <section aria-label="Forecast Accuracy">
      <h4>Forecast Accuracy</h4>
      <p>
        Original Almonds predictions, first eligible actuals, and retained
        corrections. No historical prediction is reconstructed from its outcome.
      </p>
      <div className="accuracy-filters">
        <label>
          Forecast metric
          <select
            aria-label="Forecast metric"
            value={metric}
            onChange={(e) => filter(setMetric, e.target.value)}
          >
            <option value="">All metrics</option>
            {metrics.map((m) => (
              <option value={m} key={m}>
                {label(m)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Forecast horizon
          <select
            aria-label="Forecast horizon"
            value={horizon}
            onChange={(e) => filter(setHorizon, e.target.value)}
          >
            <option value="">All horizons</option>
            {[1, 2, 3, 4].map((h) => (
              <option key={h} value={h}>
                {h} quarters
              </option>
            ))}
          </select>
        </label>
        <label>
          Forecast period type
          <select
            aria-label="Forecast period type"
            value={periodType}
            onChange={(e) => filter(setPeriodType, e.target.value)}
          >
            <option value="">All periods</option>
            {["QUARTERLY", "HALF_YEARLY", "NINE_MONTH", "ANNUAL"].map((t) => (
              <option key={t} value={t}>
                {label(t)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Actual publication from
          <input
            aria-label="Actual publication from"
            type="date"
            value={from}
            onChange={(e) => filter(setFrom, e.target.value)}
          />
        </label>
        <label>
          Actual publication through
          <input
            aria-label="Actual publication through"
            type="date"
            value={to}
            onChange={(e) => filter(setTo, e.target.value)}
          />
        </label>
      </div>
      <p>
        Unmatched forecasts use their target period for date filtering. Scope:{" "}
        {label(scope)}.
      </p>
      <button
        className="button button-outline button-small"
        disabled={queuing}
        onClick={evaluate}
      >
        {queuing ? "Queuing scoring…" : "Evaluate available results"}
      </button>
      {job && <p role="status">{job}</p>}
      {loading && <p role="status">Loading forecast accuracy…</p>}
      {errors.length > 0 && (
        <div role="alert">
          {errors.map((e) => (
            <p key={e}>{e}</p>
          ))}
          <button onClick={() => setRefresh((v) => v + 1)}>
            Retry forecast accuracy
          </button>
        </div>
      )}
      {summary && (
        <>
          <div className="accuracy-overview">
            {(
              [
                ["Evaluated forecasts", summary.overview.evaluated],
                ["Eligible sample", summary.overview.eligible_sample],
                ["Pending forecasts", summary.overview.pending],
                ["Missing actuals", summary.overview.missing_actual],
                ["Excluded observations", summary.overview.excluded],
              ] as const
            ).map(([title, value]) => (
              <div key={title}>
                <strong>{value}</strong>
                <span>{title}</span>
              </div>
            ))}
          </div>
          <p>
            {summary.overview.status === "INSUFFICIENT_DATA"
              ? "Insufficient data"
              : "Descriptive historical sample"}{" "}
            · Minimum {summary.minimum_n} observations per comparable group. No
            combined accuracy score.
          </p>
        </>
      )}
      <h4>Prediction vs actual</h4>
      <label className="accuracy-check">
        <input
          type="checkbox"
          checked={exclusions}
          onChange={(e) => {
            setExclusions(e.target.checked);
            setOffset(0);
          }}
        />{" "}
        Show only pending, missing and excluded forecasts
      </label>
      {page && !page.items.length && (
        <p>
          {page.total === 0 &&
          !metric &&
          !horizon &&
          !exclusions &&
          !from &&
          !to &&
          !periodType
            ? "No original Almonds forecasts have been captured yet."
            : "No forecasts match these filters."}
        </p>
      )}
      {page && page.items.length > 0 && (
        <div className="table-scroll">
          <table>
            <caption>
              Frozen forecasts versus first eligible published actual
            </caption>
            <thead>
              <tr>
                {[
                  "Period / metric / basis",
                  "Forecast",
                  "First actual / publication",
                  "Error",
                  "Horizon",
                  "Status and sources",
                ].map((h) => (
                  <th scope="col" key={h}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {page.items.map((r) => (
                <tr key={r.revision_id}>
                  <td>
                    {label(r.metric)}
                    <br />
                    {r.period_start}–{r.period_end}
                    <br />
                    {r.fiscal_year} · {label(r.period_type)}
                    <br />
                    {label(r.scope)} · {label(r.reporting_basis)}
                    <br />
                    {label(r.unit)} · {r.currency}
                  </td>
                  <td>
                    {number(r.predicted)}
                    <br />
                    <small>Captured {r.created_at}</small>
                  </td>
                  <td>
                    {number(r.actual)}
                    <br />
                    {r.actual_published_at || "Publication unavailable"}
                  </td>
                  <td>
                    Absolute {number(r.absolute_error)}
                    <br />
                    Signed {number(r.signed_error)}
                    <br />
                    Relative {percent(r.absolute_percentage_error)}
                  </td>
                  <td>
                    {r.horizon_quarters == null
                      ? "Quarter horizon unavailable"
                      : `${r.horizon_quarters} quarters`}
                    <br />
                    {r.horizon_days} days to period end
                  </td>
                  <td>
                    {label(r.status)}
                    <br />
                    {label(r.reason_code)}
                    <details>
                      <summary>Snapshot and actual sources</summary>
                      <p>
                        Original value {r.original_predicted}{" "}
                        {label(r.original_unit)}. Information cutoff{" "}
                        {r.information_cutoff}; revision cutoff {r.cutoff_at}.
                      </p>
                      <p>{r.reason}</p>
                      <p>
                        Evaluation {r.evaluation_id ?? "unavailable"} ·{" "}
                        {r.calculation_version} ·{" "}
                        {r.evaluated_at || "Not yet evaluated"}
                      </p>
                      <Sources evidence={r.evidence} />
                    </details>
                    {r.later_evaluations.length > 0 && (
                      <details>
                        <summary>
                          Later corrections / evidence changes (
                          {r.later_evaluations.length})
                        </summary>
                        <p>
                          Primary score remains fixed. These observations do not
                          add independent samples.
                        </p>
                        {r.later_evaluations.map((l) => (
                          <div key={l.evaluation_id}>
                            <p>
                              Evaluation {l.evaluation_id} · {l.evaluated_at} ·
                              actual {number(l.actual)} · absolute error{" "}
                              {number(l.absolute_error)} · {l.reason}
                            </p>
                            <Sources evidence={l.evidence} />
                          </div>
                        ))}
                      </details>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pages
        offset={offset}
        total={page?.total || 0}
        onChange={setOffset}
        name="forecast comparisons"
      />
      <h4>Historical forecast accuracy</h4>
      {summary && !summary.groups.length && (
        <p>Insufficient data: no eligible evaluated Almonds forecasts.</p>
      )}
      {summary?.groups.map((g, i) => (
        <article className="accuracy-guidance" key={i}>
          <h5>
            {label(g.metric)} ·{" "}
            {g.horizon_quarters == null
              ? "Horizon not recorded"
              : `${g.horizon_quarters} quarter horizon`}{" "}
            · {label(g.period_type)}
          </h5>
          <p>
            {label(g.reporting_basis)} · {g.engine_version} / {label(g.model)} ·
            n={g.n} ·{" "}
            {g.status === "INSUFFICIENT_DATA"
              ? "Insufficient data"
              : "Descriptive sample"}
          </p>
          <p>
            MAE {number(g.mae)} · RMSE {number(g.rmse)} · Bias {number(g.bias)}{" "}
            ({label(g.error_unit)}) · WAPE {percent(g.wape_pct)}
          </p>
          <p>
            Directional accuracy {percent(g.directional_accuracy, true)} · n=
            {g.directional_n}. Envelope coverage{" "}
            {percent(g.interval_coverage, true)} · n={g.interval_n} · mean width{" "}
            {number(g.mean_interval_width)} {label(g.unit)}.
          </p>
          <h5>Confidence calibration</h5>
          <Buckets rows={g.calibration} />
          <details>
            <summary>
              Rolling accuracy and calibration history (last 20 forecasts)
            </summary>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Through period</th>
                    <th scope="col">Sample</th>
                    <th scope="col">MAE</th>
                    <th scope="col">Bias</th>
                    <th scope="col">WAPE</th>
                    <th scope="col">Evaluation IDs</th>
                  </tr>
                </thead>
                <tbody>
                  {g.rolling_last_20.map((w, j) => (
                    <tr key={j}>
                      <td>{w.through_period}</td>
                      <td>n={w.n}</td>
                      <td>{number(w.mae)}</td>
                      <td>{number(w.bias)}</td>
                      <td>{percent(w.wape_pct)}</td>
                      <td>{w.evaluation_ids.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {g.rolling_last_20
              .filter((w) => w.calibration.length > 0)
              .map((w, j) => (
                <details key={j}>
                  <summary>
                    Calibration through {w.through_period} · n={w.n}
                  </summary>
                  <Buckets rows={w.calibration} />
                </details>
              ))}
          </details>
          <details>
            <summary>Underlying observations</summary>
            <p>
              Evaluation IDs: {g.evaluation_ids.join(", ")}. Use comparisons and
              source links to reproduce these values.
            </p>
          </details>
        </article>
      ))}
      <Pages
        offset={groupOffset}
        total={summary?.total || 0}
        onChange={setGroupOffset}
        name="metric groups"
      />
      <h4>Data quality</h4>
      {summary &&
        (summary.exclusions.length ? (
          summary.exclusions.map((e) => (
            <p key={e.reason_code}>
              {label(e.reason_code)} · n={e.n}
            </p>
          ))
        ) : (
          <p>
            No unmatched forecasts in this selection. This does not establish
            historical forecast coverage.
          </p>
        ))}
      {summary && (
        <details>
          <summary>Scoring and calibration methodology</summary>
          <p>{summary.sample}</p>
          <p>{summary.methodology}</p>
          <p>{summary.calibration_note}</p>
          <p>Calculation version: {summary.scoring_version}</p>
        </details>
      )}
    </section>
  );
}
