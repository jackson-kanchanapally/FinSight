import { useEffect, useState, type FormEvent } from "react";
import { api, type FinancialPeriod, type MarketSnapshot, type ScenarioResult, type ReverseResult,
  type ValuationChange, type ReferenceComparison, type Valuation, type ValuationDetail } from "./api";
import "./valuation.css";

const METRICS: [string, string, string][] = [
  ["pe_ttm", "P/E TTM", "×"], ["ps_ttm", "P/S TTM", "×"],
  ["ev_ebitda_ttm", "EV/EBITDA", "×"], ["ev_sales_ttm", "EV/Sales", "×"],
  ["peg_forward", "Forward PEG", "×"],
  ["earnings_yield_pct", "Earnings yield", "%"], ["price_to_book", "P/B", "×"],
  ["fcf_yield_pct", "FCF yield", "%"],
];
const TTM: [string, string, string][] = [
  ["ttm_revenue", "Revenue", " Cr"], ["ttm_ebitda", "EBITDA", " Cr"],
  ["ttm_pat", "PAT", " Cr"], ["ttm_eps", "Diluted EPS", ""],
  ["ttm_ebit", "EBIT", " Cr"], ["ttm_operating_cash_flow", "Operating cash flow", " Cr"],
  ["ttm_free_cash_flow", "Free cash flow", " Cr"],
];

function format(value: string | null | undefined, suffix = "", prefix = "") {
  if (value == null) return "Unavailable";
  return `${prefix}${Number(value).toLocaleString("en-IN", {maximumFractionDigits: 2})}${suffix}`;
}

function age(marketDate: string) {
  const today = new Intl.DateTimeFormat("en-CA", {timeZone: "Asia/Kolkata", year: "numeric",
    month: "2-digit", day: "2-digit"}).format(new Date());
  return Math.floor((Date.parse(today) - Date.parse(marketDate)) / 86_400_000);
}

function Detail({valuation, code, periods}: {valuation: Valuation; code: string; periods: FinancialPeriod[]}) {
  const item = valuation.metric_details[code] as ValuationDetail | undefined;
  if (!item) return null;
  const metadata = valuation.metric_details.metadata as {period_ids?: number[]; ttm_method?: string} | undefined;
  return <details className="valuation-detail"><summary>Calculation and sources</summary>
    <p>{item.formula} · {item.status}</p>
    {item.reason && <p>{item.reason}</p>}
    <p>Inputs: {item.inputs.length ? item.inputs.join(", ") : "Insufficient verified inputs"}</p>
    {item.inputs.map((id) => {
      if (id === `Q${valuation.market_snapshot_id}`) return <p key={id}>{id}: closing price {format(valuation.market.close_price, "", "₹")}
        {valuation.market.market_cap_crore ? ` · market cap ${format(valuation.market.market_cap_crore, " Cr", "₹")}` : ""}</p>;
      const factId = Number(id.slice(1));
      const period = periods.find((entry) => entry.facts.some((fact) => fact.id === factId));
      const fact = period?.facts.find((entry) => entry.id === factId);
      return fact && period ? <p key={id}>{id}: {fact.metric_code.replaceAll("_", " ")} {format(fact.value,
        fact.unit === "INR_CRORE" ? " Cr" : "", "₹")} · {period.period_end} · {period.scope} ·
        <a href={period.document_url} target="_blank" rel="noreferrer"> source PDF ↗</a></p> : null;
    })}
    <p>TTM method: {metadata?.ttm_method === "ANNUAL_BRIDGE" ? "Audited FY annual + current Q1 − prior Q1" : "Four consecutive quarters"}</p>
    <p>TTM periods: {metadata?.period_ids?.length ? metadata.period_ids.map((id) => {
      const period = periods.find((entry) => entry.id === id);
      return period ? `${period.period_type === "ANNUAL" ? period.fiscal_year : `Q${period.quarter} ${period.fiscal_year}`} (#${id})` : `#${id}`;
    }).join(", ") : "Eligible periods unavailable"}</p>
    <p>Market date: {valuation.market_date} · Scope: {valuation.scope}</p>
    <p>Price: {format(valuation.market.close_price, "", "₹")} · TTM EPS: {format(valuation.ttm_eps, "", "₹")}</p>
    <p>Provider: {valuation.market.provider} · {valuation.market.data_origin}</p>
    <a href={valuation.market.source_url} target="_blank" rel="noreferrer">Price source ↗</a>
  </details>;
}

function PriceLine({history}: {history: Valuation[]}) {
  const points = [...history].reverse().filter((item) => item.pe_ttm != null);
  if (points.length < 2) return <p className="financial-footnote">P/E history appears after two dated prices have reliable trailing EPS.</p>;
  const values = points.map((item) => Number(item.pe_ttm));
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const coordinates = values.map((value, i) => `${20 + i * 560 / (values.length - 1)},${116 - (value - min) / span * 94}`).join(" ");
  return <div className="valuation-chart"><svg viewBox="0 0 600 140" role="img" aria-label="Calculated P/E TTM history">
    <polyline points={coordinates} fill="none" stroke="currentColor" strokeWidth="3" />
  </svg><div><span>{points[0].market_date}: {format(points[0].pe_ttm, "×")}</span>
    <span>{points[points.length - 1].market_date}: {format(points[points.length - 1].pe_ttm, "×")}</span></div></div>;
}

function ClosingPriceLine({history}: {history: MarketSnapshot[]}) {
  const points = [...history].reverse();
  if (points.length < 2) return <p className="financial-footnote">Price history appears after two dated observations.</p>;
  const values = points.map((item) => Number(item.close_price));
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const coordinates = values.map((value, i) => `${20 + i * 560 / (values.length - 1)},${116 - (value - min) / span * 94}`).join(" ");
  return <div className="valuation-chart"><svg viewBox="0 0 600 140" role="img" aria-label="Unadjusted closing price history">
    <polyline points={coordinates} fill="none" stroke="currentColor" strokeWidth="3" />
  </svg><div><span>{points[0].as_of_date}: {format(points[0].close_price, "", "₹")}</span>
    <span>{points[points.length - 1].as_of_date}: {format(points[points.length - 1].close_price, "", "₹")}</span></div>
    <p className="financial-footnote">Raw closing prices; historical NSE prices are unadjusted for splits and bonuses.</p></div>;
}

export function ValuationPanel({symbol, scope, snapshot, growth, onMarketChanged, periods}: {symbol: string; scope: string;
  snapshot: MarketSnapshot | null; growth: {label: string; value: string}[];
  onMarketChanged: (snapshot: MarketSnapshot | null) => void; periods: FinancialPeriod[]}) {
  const [valuation, setValuation] = useState<Valuation | null>(null);
  const [history, setHistory] = useState<Valuation[]>([]);
  const [priceHistory, setPriceHistory] = useState<MarketSnapshot[]>([]);
  const [change, setChange] = useState<ValuationChange | null>(null);
  const [changeNotice, setChangeNotice] = useState("");
  const [references, setReferences] = useState<ReferenceComparison[]>([]);
  const [refreshAvailable, setRefreshAvailable] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [scenarios, setScenarios] = useState<ScenarioResult[]>([]);
  const [reverse, setReverse] = useState<ReverseResult | null>(null);
  const [targetReturn, setTargetReturn] = useState("12");
  const [scenarioGrowth, setScenarioGrowth] = useState("20");
  const [terminalPe, setTerminalPe] = useState("30");
  const [years, setYears] = useState("3");
  useEffect(() => {
    let active = true;
    setError(""); setScenarios([]);
    Promise.all([api.market(symbol), api.valuation(symbol, scope), api.valuationHistory(symbol, scope),
      api.marketHistory(symbol), api.valuationReference(symbol, scope)])
      .then(([market, latest, entries, prices, imported]) => {if (active) {setRefreshAvailable(market.refresh_available);
        setValuation(latest); setHistory(entries); setPriceHistory(prices); setReferences(imported);
        const comparable = entries.filter((entry) => entry.pe_ttm != null && entry.ttm_eps != null &&
          Number(entry.pe_ttm) > 0 && Number(entry.ttm_eps) > 0);
        if (comparable.length >= 2 && comparable[0].market_date !== comparable[comparable.length - 1].market_date) {
          api.valuationChange(symbol, comparable[comparable.length - 1].market_snapshot_id,
            comparable[0].market_snapshot_id, scope).then((result) => {if (active) {
              setChange(result.status === "CALCULATED" ? result : null);
              setChangeNotice(result.status === "INSUFFICIENT_DATA" ? "Valuation change needs comparable dated earnings and P/E." : "");
            }}).catch(() => {if (active) {setChange(null); setChangeNotice("Valuation change could not load.");}});
        } else {setChange(null); setChangeNotice("Valuation change appears after two dated prices have comparable verified TTM EPS and P/E.");}}})
      .catch((cause) => {if (active) setError(cause instanceof Error ? cause.message : "Valuation could not load.");});
    return () => {active = false;};
  }, [symbol, scope, snapshot?.updated_at]);

  async function refresh() {
    setBusy(true); setError("");
    try {const refreshed = await api.refreshMarket(symbol); onMarketChanged(refreshed.latest);
      const [latest, entries, prices] = await Promise.all([
      api.valuation(symbol, scope), api.valuationHistory(symbol, scope), api.marketHistory(symbol)]);
      setValuation(latest); setHistory(entries); setPriceHistory(prices);
    } catch (cause) {setError(cause instanceof Error ? cause.message : "Price refresh failed.");}
    finally {setBusy(false);}
  }
  async function runScenario(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {setScenarios(await api.scenarios(symbol, scope, [{name: "Scenario A", eps_growth_pct: scenarioGrowth,
      terminal_pe: terminalPe, years: Number(years)}]));}
    catch (cause) {setError(cause instanceof Error ? cause.message : "Scenario unavailable.");}
    finally {setBusy(false);}
  }
  async function runReverse(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {setReverse(await api.reverseGrowth(symbol, scope, terminalPe, Number(years), targetReturn));}
    catch (cause) {setError(cause instanceof Error ? cause.message : "Reverse growth unavailable.");}
    finally {setBusy(false);}
  }
  const market = valuation?.market;
  const days = market ? age(market.as_of_date) : null;
  const availableMetrics = valuation ? METRICS.filter(([code]) =>
    (valuation.metric_details[code] as ValuationDetail | undefined)?.value != null) : [];
  const unavailableMetrics = valuation ? METRICS.filter(([code]) =>
    (valuation.metric_details[code] as ValuationDetail | undefined)?.value == null) : [];
  const availableTtm = valuation ? TTM.filter(([code]) =>
    (valuation.metric_details[code] as ValuationDetail | undefined)?.value != null) : [];
  const unavailableTtm = valuation ? TTM.filter(([code]) =>
    (valuation.metric_details[code] as ValuationDetail | undefined)?.value == null) : [];
  return <section id="valuation" className="valuation-panel" aria-label="Valuation">
    <div className="valuation-heading"><div><p className="eyebrow">ALMONDS CALCULATED · DATED</p><h3>Valuation</h3></div>
      {refreshAvailable && <button className="button button-outline button-small" disabled={busy} onClick={() => void refresh()}>Refresh EOD price</button>}</div>
    {error && <p className="event-error" role="alert">{error}</p>}
    {!valuation && <p className="financial-footnote">Add a dated market price to calculate valuation.</p>}
    {valuation && <>
      <p className="financial-footnote">Price {market?.as_of_date} · {market?.provider} / {market?.data_origin} · {days === 0 ? "Current date" : `${days} day${days === 1 ? "" : "s"} old`}{days !== null && days > 7 ? " · Stale" : ""}. Calculated ratios use verified financial facts available by this market date.</p>
      <div className="valuation-cards"><div className="valuation-card"><span>Closing price</span><strong>{format(market?.close_price, "", "₹")}</strong><small>{market?.as_of_date}</small></div>
        {(valuation.metric_details.market_cap as ValuationDetail)?.value != null && <div className="valuation-card"><span>Market cap</span><strong>{format((valuation.metric_details.market_cap as ValuationDetail)?.value, " Cr", "₹")}</strong><Detail valuation={valuation} code="market_cap" periods={periods} /></div>}
        {availableMetrics.map(([code, label, suffix]) => {const detail = valuation.metric_details[code] as ValuationDetail | undefined;
          return <div className="valuation-card" key={code}><span>{label}</span><strong>{format(detail?.value, suffix)}</strong>
            <small>{detail?.value == null ? detail?.reason || "Verified input missing" : "Almonds calculated"}</small>
            <Detail valuation={valuation} code={code} periods={periods} /></div>;})}</div>
      {unavailableMetrics.length > 0 && <details className="valuation-missing"><summary>Unavailable ratios ({unavailableMetrics.length})</summary>
        <ul>{unavailableMetrics.map(([code, label]) => <li key={code}>{label}: {(valuation.metric_details[code] as ValuationDetail)?.reason || "Verified input missing"}</li>)}</ul></details>}
      <p className="financial-footnote">Available ratios use {((valuation.metric_details.metadata as {ttm_method?: string})?.ttm_method === "ANNUAL_BRIDGE")
        ? "audited FY annual + current Q1 − prior Q1" : "four verified quarters"}. Forward PEG needs a sourced growth assumption; enterprise multiples need verified debt and cash.</p>
      <h4>Valuation inputs · TTM</h4><div className="valuation-cards valuation-inputs">{availableTtm.map(([code, label, suffix]) => {
        const detail = valuation.metric_details[code] as ValuationDetail | undefined;
        return <div className="valuation-card" key={code}><span>{label}</span><strong>{format(detail?.value, suffix, "₹")}</strong>
          <Detail valuation={valuation} code={code} periods={periods} /></div>;})}</div>
      {unavailableTtm.length > 0 && <details className="valuation-missing"><summary>Unavailable TTM inputs ({unavailableTtm.length})</summary>
        <ul>{unavailableTtm.map(([code, label]) => <li key={code}>{label}: {(valuation.metric_details[code] as ValuationDetail)?.reason || "Verified input missing"}</li>)}</ul></details>}
      <h4>Latest verified result trends</h4><div className="valuation-cards valuation-inputs">
        {growth.length ? growth.map((item) => <div className="valuation-card" key={item.label}><span>{item.label}</span>
          <strong>{format(item.value, "%")}</strong></div>) : <p className="financial-footnote">No comparable verified growth metrics yet.</p>}
      </div>
      <h4>Valuation history</h4><PriceLine history={history} />
      <h4>Closing price history</h4><ClosingPriceLine history={priceHistory} />
      {change && <div className="valuation-result"><h4>Valuation change · {change.old_date} → {change.new_date}</h4>
        <p>Price {format(change.price_change_pct, "%")} · diluted EPS {format(change.eps_change_pct, "%")} · P/E {format(change.old_pe, "×")} → {format(change.new_pe, "×")} ({format(change.pe_change_pct, "%")})</p>
        <p className="financial-footnote">{change.method}. These are changes in price, trailing earnings and multiple; raw prices may be affected by corporate actions.</p></div>}
      {!change && changeNotice && <p className="financial-footnote">{changeNotice}</p>}
      {references.length > 0 && <><h4>Almonds vs NSE Market Lens · imported reference</h4>
        <div className="valuation-reference"><div className="valuation-reference-head"><span>Metric</span><span>Almonds</span><span>Market Lens</span><span>Difference</span><span>Status</span></div>
          {references.map((item) => <div className="valuation-reference-row" key={item.metric_code}><span>{item.metric_code}</span>
            <span>{format(item.almonds)}</span><span>{format(item.market_lens)}</span><span>{format(item.difference_pct, "%")}</span>
            <span>{item.status} · <a href={item.source_url} target="_blank" rel="noreferrer">{item.reference_date}</a></span></div>)}</div></>}
      <form className="valuation-scenario" onSubmit={(event) => void runScenario(event)}><h4>Scenario analysis</h4>
        <p className="financial-footnote">Your assumptions, not a forecast. Requires reliable positive TTM EPS.</p>
        <label>Annual EPS growth (%)<input type="number" min="-99" max="1000" step="0.1" required value={scenarioGrowth} onChange={(event) => setScenarioGrowth(event.target.value)} /></label>
        <label>Terminal P/E<input type="number" min="0.01" max="1000" step="0.1" required value={terminalPe} onChange={(event) => setTerminalPe(event.target.value)} /></label>
        <label>Years<input type="number" min="1" max="10" step="1" required value={years} onChange={(event) => setYears(event.target.value)} /></label>
        <button className="button button-outline button-small" disabled={busy}>Calculate scenario</button>
      </form>
      {scenarios.map((item) => <p className="valuation-result" key={item.name}>{item.name}: future EPS {format(item.future_eps, "", "₹")}, implied price {format(item.implied_future_price, "", "₹")}, total return {format(item.total_return_pct, "%")}, annualized return {format(item.annualized_return_pct, "%")}</p>)}
      <form className="valuation-scenario" onSubmit={(event) => void runReverse(event)}><h4>Required EPS growth</h4>
        <p className="financial-footnote">What annual EPS growth would be needed for your target return? Uses the terminal P/E and years above.</p>
        <label>Target annual return (%)<input type="number" min="-99" max="1000" step="0.1" required value={targetReturn} onChange={(event) => setTargetReturn(event.target.value)} /></label>
        <button className="button button-outline button-small" disabled={busy}>Calculate required growth</button></form>
      {reverse && <p className="valuation-result">{reverse.label}: {format(reverse.required_eps_cagr_pct, "%")} EPS CAGR to reach {format(reverse.target_future_price, "", "₹")} in {reverse.years} years at {format(reverse.terminal_pe, "×")} terminal P/E.</p>}
    </>}
  </section>;
}
