import { useEffect, useState } from "react";
import { request } from "./api";
import { ForecastAccuracyPanel } from "./ForecastAccuracyPanel";
import type { AccuracyPage, AccuracyRow } from "./accuracyApi";

type Forecast = {
  engine_version: string;
  model: string;
  base: string;
  low: string;
  high: string;
  information_cutoff: string;
  horizon_quarters: number;
  horizon_days: number;
  validation_n: number;
  uncertainty: string;
  interval_method: string;
  assumptions: string[];
  inputs: {
    period: string;
    value: string;
    evidence: Record<
      string,
      {
        document_id?: number;
        document_url?: string;
        page?: number;
        source_page?: number;
      }
    >;
  }[];
};
type ForecastRow = AccuracyRow & {
  definition: AccuracyRow["definition"] & { forecast?: Forecast };
};
type ForecastPage = Omit<AccuracyPage, "items"> & {
  items: ForecastRow[];
  availability: {
    metric: string;
    status: string;
    reason?: string;
    verified_quarters?: number;
    period?: string;
  }[];
};
type ResearchStatus = {
  enabled: boolean;
  gaps: string[];
  jobs: {
    id: number;
    kind: string;
    status: string;
    next_run_at: string;
    last_success_at: string | null;
    error: string | null;
  }[];
};
type Valuation = {
  status: string;
  reason: string;
  market: {
    price: string;
    date: string;
    stale: boolean;
    provider: string;
    source_url: string;
    classification: string;
  } | null;
  metrics: Record<string, { value?: string | null; reason?: string | null }>;
  forward: {
    status: string;
    reason?: string;
    pe?: string;
    eps?: { base: string };
  };
  history?: {
    available_points: number;
    one_year?: { low: string; median: string; high: string } | null;
  };
  scenarios: {
    earnings_scenario: string;
    multiple_scenario: string;
    value: string;
    margin_of_safety_pct: string | null;
    method: string;
  }[];
  scenario_gap?: string;
  implied_growth_pct?: string | null;
  implied_growth_method?: string;
};
const label = (s: string) => s.replaceAll("_", " ").toLowerCase();
const n = (s: string | null | undefined) =>
  s == null
    ? "—"
    : Number(s).toLocaleString(undefined, { maximumFractionDigits: 2 });

function ForecastEvidence({ forecast }: { forecast: Forecast }) {
  const sources = new Map<string, { url: string; page?: number }>();
  for (const input of forecast.inputs)
    for (const [key, value] of Object.entries(input.evidence)) {
      const page = value.page || value.source_page;
      const url =
        value.document_url ||
        (value.document_id
          ? `/api/documents/${value.document_id}/file#page=${page || 1}`
          : null);
      if (url) sources.set(key, { url, page });
    }
  return (
    <details>
      <summary>Model, assumptions and source evidence</summary>
      <p>
        {forecast.engine_version} · {label(forecast.model)} ·{" "}
        {forecast.validation_n} historical validation folds
      </p>
      <p>
        {forecast.interval_method}. {label(forecast.uncertainty)}.
      </p>
      {forecast.assumptions.map((a) => (
        <p key={a}>{a}</p>
      ))}
      <p>
        Information cutoff {forecast.information_cutoff}. Historical validation
        is separate from prospective accuracy.
      </p>
      <div className="accuracy-evidence">
        {[...sources].map(([key, s]) => (
          <a key={key} href={s.url} target="_blank" rel="noreferrer">
            {key} · page {s.page || "—"}
          </a>
        ))}
      </div>
    </details>
  );
}

export function AutonomousResearch({
  symbol,
  scope,
}: {
  symbol: string;
  scope: string;
}) {
  const [forecasts, setForecasts] = useState<ForecastPage | null>(null);
  const [status, setStatus] = useState<ResearchStatus | null>(null);
  const [valuation, setValuation] = useState<Valuation | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    setForecasts(null);
    setStatus(null);
    setValuation(null);
    setErrors([]);
  }, [symbol, scope]);
  useEffect(() => {
    let active = true;
    const base = `/api/companies/${encodeURIComponent(symbol)}`;
    setLoading(true);
    const requests = [
      request<ForecastPage>(
        `${base}/forecasts?scope=${scope}&upcoming=true&limit=100`,
      ),
      request<ResearchStatus>(`${base}/research/status`),
      request<Valuation>(`${base}/valuation/automated?scope=${scope}`),
    ] as const;
    Promise.allSettled(requests).then(([f, s, v]) => {
      if (!active) return;
      setForecasts(f.status === "fulfilled" ? f.value : null);
      setStatus(s.status === "fulfilled" ? s.value : null);
      setValuation(v.status === "fulfilled" ? v.value : null);
      const names = ["Forecasts", "Automation", "Valuation"];
      setErrors(
        [f, s, v].flatMap((r, i) =>
          r.status === "rejected"
            ? [`${names[i]}: ${r.reason?.message || "Unavailable"}`]
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
  }, [symbol, scope, refresh]);
  const upcoming =
    forecasts?.items.filter(
      (r) => r.assessment_status === "PENDING" && r.definition.forecast,
    ) || [];
  return (
    <div aria-label="Autonomous research">
      {loading && <p role="status">Loading automatic research…</p>}
      {errors.length > 0 && (
        <div role="alert">
          <p>
            Some research panels are unavailable. Available evidence remains
            below.
          </p>
          {errors.map((e) => (
            <p key={e}>{e}</p>
          ))}
          <button
            className="button button-outline button-small"
            onClick={() => setRefresh((v) => v + 1)}
          >
            Retry automatic research
          </button>
        </div>
      )}
      <h4>Upcoming forecasts</h4>
      <p>
        Almonds generates estimates from verified history automatically. No
        financial-input form is required.
      </p>
      {!loading && !upcoming.length && (
        <p>
          No eligible future forecasts yet. Missing evidence is reassessed
          automatically.
        </p>
      )}
      <div className="accuracy-group-grid">
        {upcoming.map((row) => {
          const f = row.definition.forecast!;
          return (
            <article key={row.revision_id} className="accuracy-guidance">
              <h5>
                {label(row.definition.metric)} · {row.definition.period}
              </h5>
              <p>
                MODEL ESTIMATE · Base {n(f.base)} {label(row.definition.unit)} ·
                Range {n(f.low)}–{n(f.high)}
              </p>
              <p>
                {f.horizon_quarters} quarter horizon · Captured {row.created_at}{" "}
                · Version {row.version}
              </p>
              <ForecastEvidence forecast={f} />
            </article>
          );
        })}
      </div>
      {forecasts?.availability
        .filter((g) => g.status === "INSUFFICIENT_EVIDENCE")
        .map((gap, i) => (
          <p key={i}>
            <strong>
              {label(gap.metric)}
              {gap.period ? ` · ${gap.period}` : ""}: insufficient evidence.
            </strong>{" "}
            {gap.reason}{" "}
            {gap.verified_quarters != null
              ? `${gap.verified_quarters} verified quarters.`
              : ""}
          </p>
        ))}
      <ForecastAccuracyPanel
        key={`${symbol}:${scope}`}
        symbol={symbol}
        scope={scope}
      />
      <h4>Valuation intelligence</h4>
      {valuation ? (
        <>
          <p>
            {label(valuation.status)} · {valuation.reason}
          </p>
          {valuation.market && (
            <p>
              <a
                href={valuation.market.source_url}
                target="_blank"
                rel="noreferrer"
              >
                {valuation.market.provider} · ₹{n(valuation.market.price)} ·{" "}
                {valuation.market.date}
              </a>{" "}
              · {label(valuation.market.classification)}
              {valuation.market.stale ? " · STALE PRICE" : ""}
            </p>
          )}
          <div className="accuracy-overview">
            {["pe_ttm", "ps_ttm", "earnings_yield_pct", "ev_ebitda_ttm"].map(
              (k) => (
                <div key={k}>
                  <strong>{n(valuation.metrics[k]?.value)}</strong>
                  <span>{label(k)}</span>
                  <small>{valuation.metrics[k]?.reason}</small>
                </div>
              ),
            )}
          </div>
          <p>
            Forward valuation: {label(valuation.forward.status)}.{" "}
            {valuation.forward.reason ||
              `Model forward P/E ${n(valuation.forward.pe)}; four-quarter EPS ${n(valuation.forward.eps?.base)}.`}
          </p>
          <p>
            Historical context: {valuation.history?.available_points || 0} dated
            observations
            {valuation.history?.one_year
              ? `; one-year P/E ${n(valuation.history.one_year.low)}–${n(valuation.history.one_year.high)}`
              : "; insufficient coverage for a one-year valuation range."}
          </p>
          {valuation.scenario_gap && <p>{valuation.scenario_gap}</p>}
          {valuation.scenarios.length > 0 && (
            <>
              <p>
                Relative valuation sensitivity using model EPS and observed
                historical multiples; not intrinsic value.
              </p>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>EPS scenario</th>
                      <th>Multiple scenario</th>
                      <th>Value</th>
                      <th>Margin of safety</th>
                    </tr>
                  </thead>
                  <tbody>
                    {valuation.scenarios.map((s, i) => (
                      <tr key={i}>
                        <td>{s.earnings_scenario}</td>
                        <td>{s.multiple_scenario}</td>
                        <td>₹{n(s.value)}</td>
                        <td>{n(s.margin_of_safety_pct)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {valuation.implied_growth_pct && (
            <p>
              Implied EPS growth {n(valuation.implied_growth_pct)}% ·{" "}
              {valuation.implied_growth_method}
            </p>
          )}
        </>
      ) : (
        <p>
          Valuation evidence is unavailable; source refresh retries
          automatically.
        </p>
      )}
      <h4>Research automation status</h4>
      {status && (
        <>
          <p>
            {status.enabled
              ? "Automatic research is enabled."
              : "Background research is paused in this environment."}
          </p>
          {!status.jobs.length && (
            <p>Initial research will be queued by the scheduler.</p>
          )}
          {status.jobs.map((j) => (
            <article key={j.id} className="accuracy-guidance">
              <strong>
                {label(j.kind)} · {label(j.status)}
              </strong>
              <p>
                Last success: {j.last_success_at || "Not yet completed"} · Next
                run/retry: {j.next_run_at}
              </p>
              {j.error && <p>{j.error}</p>}
            </article>
          ))}
          <details>
            <summary>Source coverage and missing data</summary>
            {status.gaps.map((g) => (
              <p key={g}>{g}</p>
            ))}
          </details>
        </>
      )}
    </div>
  );
}
