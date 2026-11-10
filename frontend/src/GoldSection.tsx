import { useCallback, useEffect, useState } from "react";
import { request } from "./api";
import { displayDate, money } from "./format";
import "./gold.css";

type Impact = "POSITIVE" | "NEGATIVE" | "MIXED" | "UNCLEAR";
type GlobalQuote = {
  usd_per_oz: string;
  as_of: string;
  source_url: string;
  freshness: string;
  change_pct?: string;
  change_since?: string;
};
type IndiaQuote = {
  city: string;
  inr_22k_per_g: string;
  inr_24k_per_g: string;
  change_22k_pct: string | null;
  change_24k_pct: string | null;
  rate_date: string;
  source_url: string;
  freshness: string;
  basis: string;
};
type Region = "india" | "hyderabad";
type Prices = {
  global: GlobalQuote | null;
  india: IndiaQuote | null;
  hyderabad: IndiaQuote | null;
};
type Headline = {
  id: number;
  title: string;
  source: string;
  url: string;
  published_at: string;
  impact: Impact;
  impact_reason: string;
  important: boolean;
};
type Delivery = {
  id: number;
  kind: string;
  status: string;
  sent_at: string | null;
  last_error: string | null;
};
type GoldResponse = {
  api_version: 2;
  prices: Prices;
  analysis: {
    bias: Impact;
    summary: string;
    what_next: string;
    method: string;
    local_context: Record<Region, string>;
    comparison_note: string;
    drivers: {
      headline_id: number;
      title: string;
      source: string;
      url: string;
      impact: Impact;
      reason: string;
      factor?: string;
      scope?: string;
    }[];
  } | null;
  headlines: Headline[];
  history: { recorded_on: string; fetched_at: string; prices: Prices }[];
  status: {
    enabled: boolean;
    major_event_alerts_enabled?: boolean;
    telegram_connected: boolean;
    last_refresh_at: string | null;
    last_success_at: string | null;
    errors: { source: string; reason: string }[];
    schedule: { name: string; time: string }[];
    next_alert_at: string;
    timezone: string;
    poll_minutes: number;
    move_threshold_pct: number;
    deliveries: Delivery[];
  };
};

const impacts: Record<Impact, string> = {
  POSITIVE: "Positive for gold",
  NEGATIVE: "Pressure on gold",
  MIXED: "Mixed signals",
  UNCLEAR: "Direction unclear",
};
const stamp = (value: string | null | undefined) => {
  if (!value || !Number.isFinite(new Date(value).getTime()))
    return "Not yet available";
  return (
    new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata",
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value)) + " IST"
  );
};
const dollars = (value: string) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(Number(value));
const percent = (value: string | null | undefined) =>
  value == null
    ? null
    : `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(2)}%`;

function Change({
  value,
  basis,
}: {
  value: string | null | undefined;
  basis: string;
}) {
  if (value == null)
    return (
      <span className="gold-price-note">
        Comparison builds from stored quotes
      </span>
    );
  return (
    <span
      className="gold-change"
      data-direction={
        Number(value) < 0 ? "down" : Number(value) > 0 ? "up" : "flat"
      }
    >
      {percent(value)} <small>{basis}</small>
    </span>
  );
}

function PriceCards({ prices, region }: { prices: Prices; region: Region }) {
  const global = prices.global;
  const india = prices[region];
  const label = region === "india" ? "INDIA REFERENCE" : "HYDERABAD";
  return (
    <div className="gold-price-grid" aria-label="Gold prices">
      <article
        className="gold-price-card"
        data-stale={global?.freshness === "STALE"}
      >
        <div className="gold-price-label">
          <span>GLOBAL SPOT</span>
          <span>USD / TROY OZ</span>
        </div>
        <strong className="gold-price-value">
          {global ? dollars(global.usd_per_oz) : "Unavailable"}
        </strong>
        <Change value={global?.change_pct} basis="vs previous stored quote" />
        <p>
          {global
            ? `Source updated ${stamp(global.as_of)}`
            : "Waiting for a global quote"}
        </p>
        {global?.freshness === "STALE" && (
          <span className="gold-age">Old quote · source needs an update</span>
        )}
      </article>
      {(["22", "24"] as const).map((purity) => (
        <article
          className="gold-price-card"
          key={purity}
          data-stale={india?.freshness === "STALE"}
        >
          <div className="gold-price-label">
            <span>
              {label} · {purity}K
            </span>
            <span>INR / GRAM</span>
          </div>
          <strong className="gold-price-value">
            {india
              ? money(
                  purity === "22" ? india.inr_22k_per_g : india.inr_24k_per_g,
                )
              : "Unavailable"}
          </strong>
          <Change
            value={
              purity === "22" ? india?.change_22k_pct : india?.change_24k_pct
            }
            basis="vs previous day"
          />
          <p>
            {india
              ? `Published rate: ${displayDate(india.rate_date)}`
              : `Waiting for the ${region === "india" ? "India" : "Hyderabad"} rate`}
          </p>
          {india?.freshness === "PREVIOUS_DAY" && (
            <span className="gold-age">Previous day's published rate</span>
          )}
          {india?.freshness === "STALE" && (
            <span className="gold-age">Old rate · source needs an update</span>
          )}
        </article>
      ))}
    </div>
  );
}

function GoldView({ compact = false }: { compact?: boolean }) {
  const [region, setRegion] = useState<Region>(() => {
    try {
      return window.localStorage.getItem("almonds.gold.region") === "india"
        ? "india"
        : "hyderabad";
    } catch {
      return "hyderabad";
    }
  });
  const [data, setData] = useState<GoldResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    try {
      const response = await request<GoldResponse>("/api/gold?version=2");
      if (
        response?.api_version !== 2 ||
        !response?.prices ||
        !response.status?.schedule
      )
        throw new Error("Gold data is unavailable.");
      setData(response);
      setError("");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not load gold prices.",
      );
    }
  }, []);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 60000);
    return () => window.clearInterval(timer);
  }, [load]);
  function selectRegion(value: Region) {
    setRegion(value);
    try {
      window.localStorage.setItem("almonds.gold.region", value);
    } catch {
      // The choice still works for this view if browser storage is unavailable.
    }
  }
  async function refresh() {
    setBusy(true);
    setNotice("");
    try {
      const result = await request<{ status: string }>("/api/gold/refresh", {
        method: "POST",
      });
      setNotice(
        result.status === "FAILED"
          ? "Sources could not update. Last available prices are retained."
          : result.status === "BUSY"
            ? "Gold prices are already being refreshed."
            : result.status === "PARTIAL"
              ? "Available prices updated. Some sources need another check."
              : result.status === "CACHED"
                ? "These prices were checked within the last minute."
                : "Gold prices and news updated.",
      );
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Gold refresh failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      id={compact ? "gold-overview" : "gold"}
      className={`gold-section ${compact ? "gold-compact section-block" : ""}`}
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">GLOBAL MARKETS · LOCAL VALUE</p>
          <h2>Gold watch</h2>
        </div>
        {compact ? (
          <a className="button button-outline" href="#gold">
            Open gold watch ↗
          </a>
        ) : (
          <button
            className="button button-outline"
            onClick={() => void refresh()}
            disabled={busy}
          >
            {busy ? "Checking gold…" : "Refresh gold"}
          </button>
        )}
      </div>
      <p className="muted gold-intro">
        Global gold in dollars, India and Hyderabad references per gram, and a
        plain-language explanation of the latest news.
      </p>
      {error && (
        <div className="load-error" role="alert">
          <span>{error}</span>
          <button
            className="button button-outline button-small"
            onClick={() => void load()}
          >
            Retry loading
          </button>
        </div>
      )}
      {notice && (
        <p className="gold-notice" role="status">
          {notice}
        </p>
      )}
      {!data && !error && <p role="status">Loading gold prices…</p>}
      {data && (
        <>
          <div className="gold-rate-toolbar">
            <div
              className="gold-region-picker"
              role="group"
              aria-label="Indian rate location"
            >
              <span>Indian rates</span>
              <button
                type="button"
                aria-pressed={region === "india"}
                onClick={() => selectRegion("india")}
              >
                India
              </button>
              <button
                type="button"
                aria-pressed={region === "hyderabad"}
                onClick={() => selectRegion("hyderabad")}
              >
                Hyderabad
              </button>
            </div>
            <small>Telegram includes both locations</small>
          </div>
          <PriceCards prices={data.prices} region={region} />
          <p className="gold-source-line">
            Retail references exclude GST and making charges.{" "}
            <a
              href={
                region === "india"
                  ? "https://groww.in/gold-rates"
                  : "https://groww.in/gold-rates/gold-rate-today-in-hyderabad"
              }
              target="_blank"
              rel="noreferrer"
            >
              Groww rates ↗
            </a>
            <a href="https://gold-api.com/" target="_blank" rel="noreferrer">
              Gold API ↗
            </a>
          </p>
          {compact ? (
            <div className="gold-compact-footer">
              <span
                className="gold-impact"
                data-impact={data.analysis?.bias ?? "UNCLEAR"}
              >
                {data.analysis
                  ? impacts[data.analysis.bias]
                  : "Building the first brief"}
              </span>
              <span>
                {data.status.enabled && data.status.telegram_connected
                  ? `Next scheduled alert ${stamp(data.status.next_alert_at)}`
                  : "Automatic alerts are paused or Telegram is disconnected"}
              </span>
            </div>
          ) : (
            <>
              {!!data.status.errors.length && (
                <div className="gold-source-warning" role="alert">
                  <strong>Some sources could not update</strong>
                  <p>
                    Prices retain their original source dates.{" "}
                    {data.status.errors.map((item) => item.source).join(", ")}{" "}
                    will be checked again.
                  </p>
                </div>
              )}
              <div className="gold-research-grid">
                <article className="gold-analysis">
                  <div className="gold-panel-heading">
                    <p className="eyebrow">THE CURRENT READ</p>
                    <span
                      className="gold-impact"
                      data-impact={data.analysis?.bias ?? "UNCLEAR"}
                    >
                      {data.analysis
                        ? impacts[data.analysis.bias]
                        : "Waiting for evidence"}
                    </span>
                  </div>
                  <h3>What the news means</h3>
                  <p>
                    {data.analysis?.summary ??
                      "The first source check will build the brief. You can refresh now."}
                  </p>
                  {data.analysis?.drivers.map((driver) => (
                    <div
                      className="gold-driver"
                      key={`${driver.headline_id}:${driver.factor ?? driver.reason}`}
                    >
                      {driver.factor && <strong>{driver.factor}</strong>}
                      <p>{driver.reason}</p>
                      <a href={driver.url} target="_blank" rel="noreferrer">
                        {driver.title} ↗
                      </a>
                      <small>
                        {driver.source} · {impacts[driver.impact]}
                      </small>
                    </div>
                  ))}
                  {data.analysis && (
                    <>
                      <div className="gold-local-context">
                        <strong>
                          For {region === "india" ? "India" : "Hyderabad"}
                        </strong>
                        <p>{data.analysis.local_context[region]}</p>
                        <small>{data.analysis.comparison_note}</small>
                      </div>
                      <div className="gold-outlook">
                        <strong>What to watch next</strong>
                        <p>{data.analysis.what_next}</p>
                      </div>
                    </>
                  )}
                  <small className="gold-method">{data.analysis?.method}</small>
                </article>
                <article className="gold-alert-plan">
                  <p className="eyebrow">YOUR TELEGRAM ROUTINE</p>
                  <h3>Two daily updates</h3>
                  <p className="muted">
                    {data.status.enabled
                      ? "Monitoring enabled"
                      : "Monitoring paused"}{" "}
                    ·{" "}
                    {data.status.telegram_connected
                      ? "Telegram connected"
                      : "Telegram not connected"}
                  </p>
                  <div className="gold-schedule">
                    {data.status.schedule.map((slot) => {
                      const last = data.status.deliveries.find(
                        (delivery) => delivery.kind === slot.name,
                      );
                      return (
                        <div key={slot.name}>
                          <span>{slot.name}</span>
                          <strong>
                            {slot.time} <small>IST</small>
                          </strong>
                          <p>
                            {last?.sent_at
                              ? `Last sent ${stamp(last.sent_at)}`
                              : last
                                ? last.status.replaceAll("_", " ").toLowerCase()
                                : "Ready for the next scheduled check"}
                          </p>
                        </div>
                      );
                    })}
                  </div>
                  <div className="gold-material">
                    <strong>
                      {data.status.major_event_alerts_enabled
                        ? "Major events and large price moves"
                        : "Large price moves between briefs"}
                    </strong>
                    <p>
                      Prices and news are checked every {data.status.poll_minutes}{" "}
                      minutes. Global gold price moves of at least{" "}
                      {data.status.move_threshold_pct}% can trigger an extra
                      alert.{" "}
                      {data.status.major_event_alerts_enabled
                        ? "Major reported policy decisions, economic-data surprises and geopolitical events can also trigger alerts. Routine headlines stay in the daily briefs."
                        : "News is included in the daily briefs."}
                    </p>
                  </div>
                  <p className="gold-check-time">
                    Last check: {stamp(data.status.last_refresh_at)}
                  </p>
                  <p className="gold-check-time">
                    Almonds must be running and your Mac awake for local alerts.
                  </p>
                </article>
              </div>
              <article className="gold-news-panel">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">DATED &amp; LINKED</p>
                    <h3>Latest gold news</h3>
                  </div>
                  <span className="muted">Past 24 hours</span>
                </div>
                {data.headlines.length ? (
                  <div className="gold-news-list">
                    {data.headlines.map((item) => (
                      <div className="gold-news-item" key={item.id}>
                        <div>
                          <span
                            className="gold-impact"
                            data-impact={item.impact}
                          >
                            {impacts[item.impact]}
                          </span>
                          {item.important && (
                            <span className="gold-material-tag">
                              Material update
                            </span>
                          )}
                        </div>
                        <a href={item.url} target="_blank" rel="noreferrer">
                          {item.title} ↗
                        </a>
                        <small>
                          {item.source} · {stamp(item.published_at)}
                        </small>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="muted">
                    No recent gold headlines were verified. The next check will
                    try again.
                  </p>
                )}
              </article>
              {data.history.length > 0 && (
                <details className="gold-history">
                  <summary>Daily recorded prices · past 7 days</summary>
                  <p className="muted">
                    The latest stored snapshot for each day. These are recorded
                    observations, rather than official closing prices.
                  </p>
                  <div className="gold-table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Recorded</th>
                          <th>Global / troy oz</th>
                          <th>
                            {region === "india" ? "India" : "Hyderabad"} 22K / g
                          </th>
                          <th>
                            {region === "india" ? "India" : "Hyderabad"} 24K / g
                          </th>
                          <th>Retail rate date</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.history.map((row) => (
                          <tr key={row.recorded_on}>
                            <td>{displayDate(row.recorded_on)}</td>
                            <td>
                              {row.prices.global
                                ? dollars(row.prices.global.usd_per_oz)
                                : "—"}
                            </td>
                            <td>
                              {row.prices[region]
                                ? money(row.prices[region].inr_22k_per_g)
                                : "—"}
                            </td>
                            <td>
                              {row.prices[region]
                                ? money(row.prices[region].inr_24k_per_g)
                                : "—"}
                            </td>
                            <td>
                              {row.prices[region]
                                ? displayDate(row.prices[region].rate_date)
                                : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              )}
              {!!data.status.deliveries.length && (
                <details className="gold-history">
                  <summary>Recent Telegram deliveries</summary>
                  <ul>
                    {data.status.deliveries.map((delivery) => (
                      <li key={delivery.id}>
                        {delivery.kind} · {delivery.status.toLowerCase()}
                        {delivery.sent_at
                          ? ` · ${stamp(delivery.sent_at)}`
                          : ""}
                        {delivery.last_error ? ` · ${delivery.last_error}` : ""}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

export const GoldSection = () => <GoldView />;
export const GoldOverview = () => <GoldView compact />;
