import { useCallback, useEffect, useMemo, useState } from "react";
import { request } from "./api";
import { displayDate, money } from "./format";
import "./ipos.css";

type Assessment = {
  status: "ATTRACTIVE" | "WATCH" | "INSUFFICIENT_DATA";
  missing: string[];
  reasons: string[];
  risks: string[];
  metrics: Record<string, string>;
  method: string;
};
type Issue = {
  id: number;
  name: string;
  stage: string;
  sector: string | null;
  opens_on: string | null;
  closes_on: string | null;
  lists_on: string | null;
  price_low: string | null;
  price_high: string | null;
  issue_cr: string | null;
  fresh_cr: string | null;
  ofs_cr: string | null;
  lot_size: number | null;
  gmp: string | null;
  gmp_pct: string | null;
  gmp_at: string | null;
  min_investment: string | null;
  subscription: string | null;
  annual_roe: string | null;
  fetched_at: string;
  business: string | null;
  objects: string[];
  source_url: string;
  rhp_url: string | null;
  assessment: Assessment;
};
type IPOResponse = {
  issues: Issue[];
  status: {
    enabled: boolean;
    telegram_enabled: boolean;
    telegram_configured: boolean;
    last_success_at: string | null;
    last_error: string | null;
    schedule: string;
    screen: { min_gmp_pct: number; max_pe: number; gmp_max_age_hours: number };
    deliveries: {
      id: number;
      status: string;
      sent_at: string | null;
      last_error: string | null;
    }[];
  };
};

const date = (value: string | null) => (value ? displayDate(value) : "TBA");
const stamp = (value: string | null) =>
  value
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value)) + " IST"
    : "Unavailable";
const cr = (value: string | null) =>
  value === null ? "Unavailable" : `${money(value)} Cr`;
const pct = (value: string | null | undefined) =>
  value == null ? "Unavailable" : `${value}%`;
const premium = (issue: Issue) => {
  const value = issue.gmp_pct === null ? NaN : Number(issue.gmp_pct);
  return Number.isFinite(value) ? value : null;
};

export function IPOsSection() {
  const [data, setData] = useState<IPOResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [onlyAttractive, setOnlyAttractive] = useState(false);
  const load = useCallback(async () => {
    try {
      setData(await request<IPOResponse>("/api/ipos"));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load IPOs.");
    }
  }, []);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 60000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function refresh() {
    setBusy(true);
    setNotice("");
    try {
      const result = await request<{
        status: string;
        stored: number;
        queued: number;
        error?: string;
      }>("/api/ipos/refresh", { method: "POST" });
      if (result.status === "FAILED")
        setNotice(result.error || "Source fetch failed. Stored data retained.");
      else if (result.status === "BUSY")
        setNotice("An IPO refresh is already running.");
      else
        setNotice(
          `Checked ${result.stored} mainboard IPOs. ${result.queued} Telegram shortlist${result.queued === 1 ? "" : "s"} queued.`,
        );
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "IPO refresh failed.");
    } finally {
      setBusy(false);
    }
  }
  const issues = useMemo(
    () =>
      [...(data?.issues ?? [])]
        .filter(
          (issue) =>
            !onlyAttractive || issue.assessment.status === "ATTRACTIVE",
        )
        .sort((left, right) => {
          const a = premium(left);
          const b = premium(right);
          if (a === null)
            return b === null ? left.name.localeCompare(right.name) : 1;
          if (b === null) return -1;
          return b - a || left.name.localeCompare(right.name);
        }),
    [data, onlyAttractive],
  );
  return (
    <section id="ipos" className="section-block ipo-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">BEFORE THE OPENING BELL</p>
          <h2>Mainboard IPOs</h2>
        </div>
        <button
          className="button button-outline"
          onClick={() => void refresh()}
          disabled={busy}
        >
          {busy ? "Fetching IPO research…" : "Refresh IPOs"}
        </button>
      </div>
      <p className="muted">
        Upcoming and open issues, ranked by GMP percentage from highest to
        lowest. Missing GMP appears last. Refreshing may queue eligible Telegram
        alerts.
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
        <p role="status" className="ipo-notice">
          {notice}
        </p>
      )}
      {!data && !error && <p role="status">Loading IPO research…</p>}
      {data && (
        <>
          <div className="ipo-status">
            <div>
              <strong>
                {data.status.enabled
                  ? "Monitoring enabled"
                  : "Monitoring paused"}
              </strong>
              <p>{data.status.schedule}</p>
            </div>
            <div>
              <strong>
                Telegram{" "}
                {data.status.telegram_enabled && data.status.telegram_configured
                  ? "connected"
                  : "not enabled"}
              </strong>
              <p>Last successful fetch: {stamp(data.status.last_success_at)}</p>
            </div>
          </div>
          {data.status.last_error && (
            <p className="load-error" role="alert">
              {data.status.last_error}
            </p>
          )}
          <details className="ipo-rules">
            <summary>What qualifies for an alert?</summary>
            <p>
              Only mainboard IPOs with confirmed dates, complete issue details,
              positive profit and non-declining total income and PAT across two
              consecutive annual periods, annual P/E no higher than{" "}
              {data.status.screen.max_pe}×, and GMP of at least{" "}
              {data.status.screen.min_gmp_pct}%. GMP must be no older than{" "}
              {data.status.screen.gmp_max_age_hours} hours. The weekly shortlist
              covers the next 14 days. Every IPO gets a fresh review the day
              before opening; only qualifying issues are sent.
            </p>
            <p>
              This is a rules-based research shortlist. GMP is unofficial.
              Company data is sourced from InvestorGain; use the linked RHP to
              verify it. Missing information excludes an issue from alerts.
            </p>
          </details>
          <label className="ipo-filter">
            <input
              type="checkbox"
              checked={onlyAttractive}
              onChange={(e) => setOnlyAttractive(e.target.checked)}
            />{" "}
            Show attractive IPOs only{" "}
            <span className="muted">{issues.length} issues</span>
          </label>
          {!issues.length && (
            <div className="empty-panel">
              <h3>
                {onlyAttractive
                  ? "No IPOs pass the screen"
                  : "No upcoming or open mainboard IPOs stored"}
              </h3>
              <p>
                {onlyAttractive
                  ? "The shortlist stays empty when the evidence does not qualify."
                  : "Refresh to check the public feed. SME issues are excluded."}
              </p>
            </div>
          )}
          <div className="ipo-list">
            {issues.map((i, index) => {
              const a = i.assessment;
              const m = a.metrics;
              const gmpPercentage = premium(i);
              return (
                <article key={i.id} className="ipo-card">
                  <div className="ipo-card-head">
                    <div>
                      <p className="eyebrow">
                        {String(index + 1).padStart(2, "0")} / MAINBOARD ·{" "}
                        {i.stage.replaceAll("_", " ")}
                      </p>
                      <h3>{i.name}</h3>
                      <p className="muted">
                        {i.sector || "Sector unavailable"}
                      </p>
                    </div>
                    <div
                      className="ipo-premium"
                      data-premium={
                        gmpPercentage === null
                          ? "missing"
                          : gmpPercentage < 0
                            ? "negative"
                            : "quoted"
                      }
                    >
                      <strong>{pct(i.gmp_pct)}</strong>
                      <span>
                        GMP{" "}
                        {i.gmp === null
                          ? "unavailable"
                          : `${money(i.gmp)}/share`}
                      </span>
                      <small>{stamp(i.gmp_at)}</small>
                    </div>
                  </div>
                  <div className="ipo-facts">
                    <div>
                      <span>Issue size</span>
                      <strong>{cr(i.issue_cr)}</strong>
                    </div>
                    <div>
                      <span>Fresh issue / OFS</span>
                      <strong>
                        {cr(i.fresh_cr)} / {cr(i.ofs_cr)}
                      </strong>
                    </div>
                    <div>
                      <span>Price band</span>
                      <strong>
                        {i.price_low ? money(i.price_low) : "TBA"} –{" "}
                        {i.price_high ? money(i.price_high) : "TBA"}
                      </strong>
                    </div>
                    <div>
                      <span>Lot / minimum</span>
                      <strong>
                        {i.lot_size ?? "TBA"} shares /{" "}
                        {i.min_investment ? money(i.min_investment) : "TBA"}
                      </strong>
                    </div>
                    <div>
                      <span>Subscription dates</span>
                      <strong>
                        {date(i.opens_on)} – {date(i.closes_on)}
                      </strong>
                    </div>
                    <div>
                      <span>Listing / subscription</span>
                      <strong>
                        {date(i.lists_on)} /{" "}
                        {i.subscription === null
                          ? "Unavailable"
                          : `${i.subscription}×`}
                      </strong>
                    </div>
                  </div>
                  <div className="ipo-verdict" data-verdict={a.status}>
                    <strong>
                      {a.status === "ATTRACTIVE"
                        ? "Attractive · passes screen"
                        : a.status === "WATCH"
                          ? "Watch · below screen"
                          : "More evidence needed"}
                    </strong>
                    {a.reasons.length > 0 && <p>{a.reasons.join(" · ")}</p>}
                    {a.missing.length > 0 && (
                      <p>
                        Missing:{" "}
                        {a.missing
                          .map((v) => v.replaceAll("_", " "))
                          .join(", ")}
                      </p>
                    )}
                  </div>
                  <details className="ipo-analysis">
                    <summary>Company analysis &amp; risks</summary>
                    <p>
                      {i.business ||
                        "Business overview unavailable from source."}
                    </p>
                    <div className="ipo-facts">
                      <div>
                        <span>Annual period</span>
                        <strong>{date(m.annual_period || null)}</strong>
                      </div>
                      <div>
                        <span>Total income / YoY</span>
                        <strong>
                          {cr(m.income_cr ?? null)} /{" "}
                          {pct(m.income_cr_growth_pct)}
                        </strong>
                      </div>
                      <div>
                        <span>PAT / YoY</span>
                        <strong>
                          {cr(m.pat_cr ?? null)} / {pct(m.pat_cr_growth_pct)}
                        </strong>
                      </div>
                      <div>
                        <span>Annual P/E</span>
                        <strong>{m.pe ? `${m.pe}×` : "Unavailable"}</strong>
                      </div>
                      <div>
                        <span>Borrowings / net worth</span>
                        <strong>
                          {m.debt_to_equity
                            ? `${m.debt_to_equity}×`
                            : "Unavailable"}
                        </strong>
                      </div>
                      <div>
                        <span>Annual ROE</span>
                        <strong>{pct(i.annual_roe)}</strong>
                      </div>
                    </div>
                    <p>
                      <strong>Use of proceeds:</strong>{" "}
                      {i.objects.join("; ") ||
                        (i.fresh_cr === "0"
                          ? "OFS proceeds go to selling shareholders."
                          : "Unavailable")}
                    </p>
                    <ul>
                      {a.risks.map((r) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                    <p className="muted">{a.method}</p>
                  </details>
                  <div className="ipo-source">
                    <a href={i.source_url} target="_blank" rel="noreferrer">
                      Company source ↗
                    </a>
                    {i.rhp_url && (
                      <a href={i.rhp_url} target="_blank" rel="noreferrer">
                        RHP ↗
                      </a>
                    )}
                    <span>Fetched {stamp(i.fetched_at)}</span>
                  </div>
                </article>
              );
            })}
          </div>
          {data.status.deliveries.length > 0 && (
            <details className="ipo-rules">
              <summary>Recent IPO Telegram deliveries</summary>
              <ul>
                {data.status.deliveries.map((d) => (
                  <li key={d.id}>
                    {d.status}
                    {d.sent_at ? ` · ${stamp(d.sent_at)}` : ""}
                    {d.last_error ? ` · ${d.last_error}` : ""}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
