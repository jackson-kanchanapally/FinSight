import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ThesisPanel } from "./ThesisPanel";
import {
  api,
  type AIAnalysis,
  type AIClaim,
  type Company,
  type CompanyFinancials,
  type FinancialFact,
  type FinancialPeriod,
  type MarketSnapshot,
  type TestedPERange,
} from "./api";
import "./financials.css";
import { ValuationPanel } from "./ValuationPanel";
import { TestedPEPanel } from "./TestedPEPanel";
import { InvestorJudgmentReport } from "./InvestorJudgmentReport";

function indiaToday() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = (name: string) =>
    parts.find((part) => part.type === name)?.value || "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function marketSnapshotAge(snapshot: MarketSnapshot) {
  return Math.floor(
    (Date.parse(`${indiaToday()}T00:00:00Z`) -
      Date.parse(`${snapshot.as_of_date}T00:00:00Z`)) /
      86_400_000,
  );
}

function matchesMarketSnapshot(
  analysis: AIAnalysis,
  snapshot: MarketSnapshot | null,
) {
  if (!snapshot)
    return !Object.values(analysis.evidence_registry).some(
      (source) => source.kind === "MARKET_SNAPSHOT",
    );
  const quote = analysis.evidence_registry[`Q${snapshot.id}`];
  const sameNumber = (
    left: string | null | undefined,
    right: string | null | undefined,
  ) =>
    (left == null && right == null) ||
    (left != null && right != null && Number(left) === Number(right));
  return (
    quote?.kind === "MARKET_SNAPSHOT" &&
    quote.as_of_date === snapshot.as_of_date &&
    sameNumber(quote.price, snapshot.close_price) &&
    sameNumber(quote.pe_ratio, snapshot.pe_ratio) &&
    sameNumber(quote.market_cap_crore, snapshot.market_cap_crore) &&
    sameNumber(quote.forward_eps_growth_pct, snapshot.forward_eps_growth_pct) &&
    sameNumber(quote.peg_ratio, snapshot.peg_ratio) &&
    quote.source_url === snapshot.source_url &&
    quote.growth_source_url === snapshot.growth_source_url
  );
}

function periodName(period: FinancialPeriod) {
  return period.period_type === "ANNUAL"
    ? period.fiscal_year
    : period.quarter
      ? `Q${period.quarter} ${period.fiscal_year}`
      : period.period_end;
}

function valueText(value: string, unit: string) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const text = new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(number);
  if (unit === "INR_CRORE") return `₹${text} Cr`;
  if (unit === "INR_PER_SHARE") return `₹${text}`;
  if (unit === "PERCENT") return `${text}%`;
  if (unit === "BASIS_POINTS") return `${text} bps`;
  return text;
}

function originalUnit(unit: string) {
  return (
    (
      {
        INR_MILLION: "₹ million",
        INR_LAKH: "₹ lakh",
        INR_CRORE: "₹ crore",
        INR_PER_SHARE: "₹ per share",
        INR: "₹",
      } as Record<string, string>
    )[unit] || unit
  );
}

function indiaDate(value: string) {
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  }).format(new Date(value));
}

function indiaDateTime(value: string) {
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
    timeZoneName: "short",
  }).format(new Date(value));
}

function financialValue(
  period: FinancialPeriod,
  code: string,
  calculated = false,
) {
  const fact = period.facts.find((item) => item.metric_code === code);
  const metric = period.metrics.find((item) => item.metric_code === code);
  const entry = calculated ? metric : fact;
  if (!entry || period.status !== "VALIDATED")
    return <span className="financial-missing">—</span>;
  const inputFacts: FinancialFact[] = metric?.inputs || [];
  return (
    <details className="financial-value">
      <summary>{valueText(entry.value, entry.unit)}</summary>
      <div className="financial-provenance">
        <strong>
          {calculated
            ? "Calculated by almonds"
            : fact?.extraction_method === "CALCULATED"
              ? "Calculated from PDF rows"
              : "Source fact"}
        </strong>
        <span>{calculated ? metric?.formula : fact?.source_label}</span>
        <span>{period.source_title}</span>
        <span>
          {period.source_name} · source document date{" "}
          {indiaDate(period.published_at)}
        </span>
        {!calculated && fact && (
          <span>
            Page {fact.source_page ?? "unavailable"} · column{" "}
            {fact.source_column_date}
            {fact.original_value && fact.original_unit
              ? ` · ${Number(fact.original_value).toLocaleString("en-IN")} ${originalUnit(fact.original_unit)}`
              : ""}{" "}
            · {fact.extraction_method}
          </span>
        )}
        {calculated && (
          <span>
            {metric?.comparison_period_id
              ? `Compared with period #${metric.comparison_period_id}`
              : "From current period"}
          </span>
        )}
        {inputFacts.map((input) => (
          <span key={input.id}>
            #{input.id} {input.metric_code.replaceAll("_", " ")}:{" "}
            {valueText(input.value, input.unit)} · page{" "}
            {input.source_page ?? "unavailable"}
            {" · "}column {input.source_column_date}
          </span>
        ))}
        <a href={period.document_url} target="_blank" rel="noreferrer">
          Open official PDF ↗
        </a>
        <a href={period.source_url} target="_blank" rel="noreferrer">
          Open source page ↗
        </a>
      </div>
    </details>
  );
}

function metricValue(period: FinancialPeriod, code: string) {
  return financialValue(period, code, true);
}

function EvidenceLinks({
  analysis,
  ids,
}: {
  analysis: AIAnalysis;
  ids: string[];
}) {
  return (
    <div className="ai-evidence">
      Sources:{" "}
      {ids.map((id) => {
        const item = analysis.evidence_registry[id];
        if (!item) return null;
        const title =
          item.kind === "CHECKPOINT_RECORD"
            ? `Investor checkpoint · page ${item.page ?? "?"}`
            : item.kind === "REVIEW_CONTEXT"
              ? "Review context and research gaps"
              : item.kind === "MARKET_SNAPSHOT"
            ? `Market snapshot · ${item.as_of_date}`
            : item.kind === "VALUATION"
              ? `${item.metric_code?.replaceAll("_", " ")} · Almonds calculated`
              : item.kind === "RELATIONSHIP"
                ? `${item.metric_code?.replaceAll("_", " ")} · Almonds comparison`
                : item.kind === "SEASONALITY"
                  ? `${item.metric_code?.replaceAll("_", " ")} · historical pattern`
                  : item.kind === "METRIC"
                    ? `${item.metric_code?.replaceAll("_", " ")} · Python metric`
                    : item.kind === "SCENARIO"
                      ? `${item.metric_code} · explicit scenario assumption`
                      : item.kind === "FACT"
                        ? `${item.metric_code?.replaceAll("_", " ")} · page ${item.page ?? "?"}`
                        : `Source PDF · page ${item.page ?? "?"}`;
        const inputs = item.input_evidence_ids || [];
        if (
          [
            "VALUATION",
            "RELATIONSHIP",
            "SEASONALITY",
            "METRIC",
            "SCENARIO",
          ].includes(item.kind) &&
          inputs.length
        ) {
          return (
            <details key={id} className="ai-evidence-detail">
              <summary>{title}</summary>
              {item.formula && <span>{item.formula}</span>}
              {item.kind === "RELATIONSHIP" && item.relationship && (
                <span>
                  {item.relationship.replaceAll("_", " ")}
                  {item.role === "CALCULATION_CROSS_CHECK"
                    ? " · external reference is a calculation cross-check"
                    : ""}
                </span>
              )}
              {item.kind === "SEASONALITY" && (
                <span>{item.status?.replaceAll("_", " ")}</span>
              )}
              <div>
                {inputs.map((inputId) => {
                  const input = analysis.evidence_registry[inputId];
                  const inputUrl =
                    input?.document_url ||
                    (input?.kind === "MARKET_SNAPSHOT"
                      ? input.source_url
                      : undefined);
                  return inputUrl ? (
                    <a
                      key={inputId}
                      href={inputUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {inputId} ·{" "}
                      {input?.kind === "MARKET_SNAPSHOT"
                        ? "external market source"
                        : "source document"}{" "}
                      ↗
                    </a>
                  ) : (
                    <span key={inputId}>
                      {inputId} ·{" "}
                      {input?.kind === "VALUATION"
                        ? "Almonds calculated"
                        : "verified input"}
                    </span>
                  );
                })}
              </div>
            </details>
          );
        }
        const url =
          item.kind === "VALUATION"
            ? undefined
            : item.document_url || item.source_url;
        return url ? (
          <a key={id} href={url} target="_blank" rel="noreferrer">
            {title} ↗
          </a>
        ) : (
          <span
            key={id}
            title={item.value ? valueText(item.value, item.unit || "") : ""}
          >
            {title}
          </span>
        );
      })}
    </div>
  );
}

function MarketSnapshotPanel({
  symbol,
  onSaved,
}: {
  symbol: string;
  onSaved: (value: MarketSnapshot | null) => void;
}) {
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);
  const [price, setPrice] = useState("");
  const [pe, setPe] = useState("");
  const [marketCap, setMarketCap] = useState("");
  const [growth, setGrowth] = useState("");
  const [growthSourceUrl, setGrowthSourceUrl] = useState("");
  const [growthSourceType, setGrowthSourceType] = useState("USER_ASSUMPTION");
  const [shares, setShares] = useState("");
  const [note, setNote] = useState("");
  const [date, setDate] = useState(indiaToday());
  const [sourceUrl, setSourceUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    Promise.all([api.marketSnapshot(symbol), api.market(symbol)])
      .then(([value, status]) => {
        if (!active) return;
        setSnapshot(value);
        onSaved(status.latest);
        if (value) {
          setPrice(value.close_price);
          setPe(value.pe_ratio || "");
          setMarketCap(value.market_cap_crore || "");
          setGrowth(value.forward_eps_growth_pct || "");
          setGrowthSourceUrl(value.growth_source_url || "");
          setGrowthSourceType(value.growth_source_type || "USER_ASSUMPTION");
          setShares(value.shares_outstanding || "");
          setNote(value.note || "");
          setDate(value.as_of_date);
          setSourceUrl(value.source_url);
        }
      })
      .catch((cause) => {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not load market snapshot.",
          );
      });
    return () => {
      active = false;
    };
  }, [symbol, onSaved]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const value = await api.saveMarketSnapshot(symbol, {
        close_price: price,
        pe_ratio: pe || null,
        market_cap_crore: marketCap || null,
        forward_eps_growth_pct: growth || null,
        growth_source_url: growthSourceUrl.trim() || null,
        growth_source_type: growth ? growthSourceType : null,
        shares_outstanding: shares || null,
        note: note.trim() || null,
        as_of_date: date,
        source_url: sourceUrl.trim(),
      });
      setSnapshot(value);
      const status = await api.market(symbol);
      onSaved(status.latest);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not save market snapshot.",
      );
    } finally {
      setBusy(false);
    }
  }

  const daysOld = snapshot ? marketSnapshotAge(snapshot) : null;
  return (
    <div className="market-panel">
      <div>
        <p className="eyebrow">DATED MARKET CONTEXT</p>
        <h3>Share price and valuation</h3>
        <p className="financial-footnote">
          Enter a dated closing price and source. Optional entered P/E and PEG
          are reference values; Almonds calculates its own ratios from verified
          financial data below.{" "}
          <a
            href={`https://www.nseindia.com/get-quotes/equity?symbol=${encodeURIComponent(symbol)}`}
            target="_blank"
            rel="noreferrer"
          >
            Open NSE quote ↗
          </a>
        </p>
      </div>
      {snapshot && (
        <p className="market-current">
          Last entered close: ₹
          {Number(snapshot.close_price).toLocaleString("en-IN")} ·{" "}
          {snapshot.as_of_date}
          {snapshot.pe_ratio
            ? ` · source P/E reference ${Number(snapshot.pe_ratio).toLocaleString("en-IN")}`
            : " · source P/E unavailable"}
          {snapshot.peg_ratio
            ? ` · source PEG reference ${Number(snapshot.peg_ratio).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
            : " · source PEG unavailable"}
          {snapshot.market_cap_crore
            ? ` · Market cap ₹${Number(snapshot.market_cap_crore).toLocaleString("en-IN")} Cr`
            : ""}
          {daysOld !== null && daysOld > 7 ? " · Stale for analysis" : ""}
          {" · "}
          <a href={snapshot.source_url} target="_blank" rel="noreferrer">
            Price and P/E source ↗
          </a>
          {snapshot.growth_source_url && (
            <>
              {" "}
              ·{" "}
              <a
                href={snapshot.growth_source_url}
                target="_blank"
                rel="noreferrer"
              >
                Growth estimate source ↗
              </a>
            </>
          )}
        </p>
      )}
      <form className="market-form" onSubmit={(event) => void save(event)}>
        <label>
          Close price (₹)
          <input
            required
            type="number"
            min="0.0001"
            step="0.0001"
            value={price}
            onChange={(event) => setPrice(event.target.value)}
          />
        </label>
        <label>
          Source P/E (reference only)
          <input
            type="number"
            min="0.0001"
            step="0.0001"
            value={pe}
            onChange={(event) => setPe(event.target.value)}
          />
        </label>
        <label>
          Market cap (₹ crore)
          <input
            type="number"
            min="0.0001"
            step="0.0001"
            value={marketCap}
            onChange={(event) => setMarketCap(event.target.value)}
          />
        </label>
        <label>
          Shares outstanding
          <input
            type="number"
            min="1"
            step="1"
            value={shares}
            onChange={(event) => setShares(event.target.value)}
          />
        </label>
        <label>
          As of
          <input
            required
            type="date"
            max={indiaToday()}
            value={date}
            onChange={(event) => setDate(event.target.value)}
          />
        </label>
        <label>
          Forward annual EPS growth estimate (%)
          <input
            type="number"
            min="-1000"
            max="1000"
            step="0.0001"
            value={growth}
            onChange={(event) => setGrowth(event.target.value)}
          />
        </label>
        <label>
          Growth estimate type
          <select
            value={growthSourceType}
            onChange={(event) => setGrowthSourceType(event.target.value)}
          >
            <option value="USER_ASSUMPTION">User assumption</option>
            <option value="MANAGEMENT_GUIDANCE">Management guidance</option>
            <option value="ANALYST_CONSENSUS">Analyst consensus</option>
            <option value="OTHER_VERIFIED_SOURCE">Other verified source</option>
          </select>
        </label>
        <label className="market-growth-source">
          Growth estimate source URL
          <input
            type="url"
            pattern="https://.*"
            required={Boolean(growth)}
            placeholder="https://…"
            value={growthSourceUrl}
            onChange={(event) => setGrowthSourceUrl(event.target.value)}
          />
        </label>
        <label className="market-source">
          Source URL
          <input
            required
            type="url"
            pattern="https://.*"
            placeholder="https://…"
            value={sourceUrl}
            onChange={(event) => setSourceUrl(event.target.value)}
          />
        </label>
        <label className="market-source">
          Observation note
          <input
            maxLength={1000}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
        <button
          className="button button-outline button-small"
          type="submit"
          disabled={busy}
        >
          {busy ? "Saving…" : "Save market snapshot"}
        </button>
      </form>
      {error && (
        <p className="event-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function AnalysisPanel({
  symbol,
  period,
  snapshot,
}: {
  symbol: string;
  period: FinancialPeriod;
  snapshot: MarketSnapshot | null;
}) {
  const [analysis, setAnalysis] = useState<AIAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const [testedRange, setTestedRange] = useState<TestedPERange | null>(null);
  useEffect(() => {
    let active = true;
    setAnalysis(null);
    setTestedRange(null);
    setError("");
    api
      .companyAnalyses(symbol)
      .then((items) => {
        if (active)
          setAnalysis(
            items.find(
              (item) =>
                item.financial_period_id === period.id &&
                (item.status === "SUCCESS" ||
                  item.status === "VERIFIED_FALLBACK") &&
                item.analysis_type === "FINANCIAL_RESULTS" &&
                item.prompt_version === item.current_prompt_version &&
                matchesMarketSnapshot(item, snapshot),
            ) || null,
          );
      })
      .catch((cause) => {
        if (active)
          setError(
            cause instanceof Error ? cause.message : "Analysis could not load.",
          );
      });
    return () => {
      active = false;
    };
  }, [symbol, period.id, snapshot]);
  async function run(regenerate = false) {
    setLoading(true);
    setError("");
    try {
      setAnalysis(await api.analyzeFinancials(symbol, period.id, regenerate));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Analysis failed.");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    if (analysis?.status !== "VERIFIED_FALLBACK") return;
    const remaining =
      Date.parse(analysis.created_at) + 15 * 60_000 - Date.now();
    if (remaining <= 0) return;
    const timer = window.setTimeout(() => setNow(Date.now()), remaining + 100);
    return () => window.clearTimeout(timer);
  }, [analysis]);
  const retryCoolingDown =
    analysis?.status === "VERIFIED_FALLBACK" &&
    analysis?.model_provider !== "deterministic" &&
    now < Date.parse(analysis.created_at) + 15 * 60_000;
  const output = analysis?.structured_output;
  const quality = output?.quality_report;
  const report = output?.investor_report;
  const calculatedPe = Object.values(analysis?.evidence_registry || {}).find(
    (item) => item.kind === "VALUATION" && item.metric_code === "PE_TTM",
  )?.value;
  const claimList = (title: string, claims: AIClaim[], empty: string) => (
    <div className={`ai-group${title === "Concerns" ? " ai-concerns" : ""}`}>
      <h4>{title}</h4>
      {claims.length ? (
        claims.map((claim, index) => (
          <article key={index}>
            <strong>{claim.title}</strong>
            <p>{claim.explanation}</p>
            <EvidenceLinks analysis={analysis!} ids={claim.evidence_ids} />
          </article>
        ))
      ) : (
        <p>{empty}</p>
      )}
    </div>
  );
  const qualityNote = (
    title: string,
    note: { text: string; evidence_ids: string[] },
  ) => (
    <div className="ai-group">
      <h4>{title}</h4>
      <p>{note.text}</p>
      <EvidenceLinks analysis={analysis!} ids={note.evidence_ids} />
    </div>
  );
  return (
    <div className="ai-panel">
      <div className="ai-header">
        <div>
          <p className="eyebrow">
            {analysis?.model_provider === "deterministic"
              ? "VERIFIED FACTS · DETERMINISTIC ANALYSIS"
              : analysis?.status === "VERIFIED_FALLBACK"
                ? "VERIFIED FACTS · LOCAL FALLBACK"
                : "LOCAL MODEL · EVIDENCE CHECKED"}
          </p>
          <h3>AI Analysis · {periodName(period)}</h3>
        </div>
        <button
          className="button button-outline button-small"
          disabled={loading || retryCoolingDown}
          onClick={() => run(Boolean(analysis))}
        >
          {loading
            ? "Analyzing…"
            : retryCoolingDown
              ? "Retry available shortly"
              : analysis?.model_provider === "deterministic"
                ? "Generate AI analysis"
              : analysis?.status === "VERIFIED_FALLBACK"
                ? "Retry AI analysis"
                : analysis?.status === "SUCCESS"
                  ? "Regenerate"
                  : "Analyze Results"}
        </button>
      </div>
      <p className="financial-footnote">
        Interpretation of verified results, not an investment recommendation.
        Financial values come from almonds calculations.
      </p>
      {error && (
        <p className="event-error" role="alert">
          {error}
        </p>
      )}
      {analysis?.status === "VERIFIED_FALLBACK" &&
        !report?.investor_conclusion && (
          <p className="financial-footnote" role="status">
            {analysis.failure_reason?.startsWith("OLLAMA_") ||
            analysis.failure_reason === "MODEL_UNAVAILABLE"
              ? "The local model was unavailable or timed out. Showing an interpretation built from verified results."
              : "The model response did not pass validation. Showing an interpretation built from verified results."}
            {retryCoolingDown &&
              " A repeat request within 15 minutes uses this saved analysis."}
          </p>
        )}
      {analysis &&
        analysis.status !== "SUCCESS" &&
        analysis.status !== "VERIFIED_FALLBACK" && (
          <p className="event-error" role="alert">
            Analysis {analysis.status.toLowerCase()}: {analysis.failure_reason}
          </p>
        )}
      {output && report?.investor_conclusion ? (
        <InvestorJudgmentReport
          analysis={analysis!}
          report={report}
          symbol={symbol}
          scope={period.scope}
          evidence={(ids) => <EvidenceLinks analysis={analysis!} ids={ids} />}
          onScenarioSaved={() => {
            setLoading(true);
            void api
              .analyzeFinancials(symbol, period.id, true, true)
              .then(setAnalysis)
              .catch((cause) => setError(cause.message))
              .finally(() => setLoading(false));
          }}
        />
      ) : (
        output && (
          <>
            {report && (
              <>
                <div className="ai-verdict-summary">
                  <div>
                    <span>Results verdict</span>
                    <strong>
                      {report.results.verdict.replaceAll("_", " ")}
                    </strong>
                  </div>
                  <div>
                    <span>Valuation verdict</span>
                    <strong>
                      {(
                        testedRange?.verdict || report.valuation.verdict
                      ).replaceAll("_", " ")}
                    </strong>
                    {(testedRange?.verdict || report.valuation.verdict) ===
                      "INSUFFICIENT_EVIDENCE" && (
                      <small>No supported forward P/E range yet</small>
                    )}
                  </div>
                  <div>
                    <span>Current P/E</span>
                    <strong>
                      {calculatedPe
                        ? `${Number(calculatedPe).toFixed(2)}×`
                        : "Unavailable"}
                    </strong>
                  </div>
                  <div>
                    <span>Tested P/E range</span>
                    <strong>
                      {testedRange?.supported_pe_range
                        ? `${testedRange.supported_pe_range.lower}–${testedRange.supported_pe_range.upper}×`
                        : !testedRange
                          ? "Loading evidence…"
                          : testedRange.missing.some(
                                (item) =>
                                  item === "positive verified TTM EPS" ||
                                  item === "dated price",
                              )
                            ? "Missing price or EPS"
                            : "No scenario set"}
                    </strong>
                  </div>
                  <div>
                    <span>Valuation confidence</span>
                    <strong>
                      {(testedRange?.verdict || report.valuation.verdict) ===
                      "INSUFFICIENT_EVIDENCE"
                        ? "Not assessed"
                        : (
                            testedRange?.confidence ||
                            report.valuation.confidence
                          ).replaceAll("_", " ")}
                    </strong>
                  </div>
                </div>
                <details className="ai-supporting">
                  <summary>Why this results verdict?</summary>
                  <p>{report.results.reasons.join(" · ")}</p>
                  <p>{report.results.methodology}</p>
                  <EvidenceLinks
                    analysis={analysis!}
                    ids={report.results.evidence_ids}
                  />
                </details>
                <TestedPEPanel
                  key={`${symbol}-${period.scope}`}
                  symbol={symbol}
                  scope={period.scope}
                  onResult={setTestedRange}
                />
                <div className="ai-watch-grid">
                  <div className="ai-group">
                    <h4>What supports the current valuation?</h4>
                    {report.valuation_support?.length ? (
                      report.valuation_support.map((note, index) => (
                        <article key={index}>
                          <p>{note.text}</p>
                          <EvidenceLinks
                            analysis={analysis!}
                            ids={note.evidence_ids}
                          />
                        </article>
                      ))
                    ) : (
                      <p>
                        No verified supporting operating trend is available.
                      </p>
                    )}
                  </div>
                  <div className="ai-group ai-concerns">
                    <h4>What challenges the current valuation?</h4>
                    {report.valuation_challenges?.length ? (
                      report.valuation_challenges.map((note, index) => (
                        <article key={index}>
                          <p>{note.text}</p>
                          <EvidenceLinks
                            analysis={analysis!}
                            ids={note.evidence_ids}
                          />
                        </article>
                      ))
                    ) : (
                      <p>
                        No verified opposing operating trend is available.
                        Missing evidence remains listed below.
                      </p>
                    )}
                  </div>
                </div>
              </>
            )}
            <div className="ai-dates">
              <span>Result period: {period.period_end}</span>
              <span>Published: {indiaDate(period.published_at)}</span>
              <span>Market price: {snapshot?.as_of_date || "Unavailable"}</span>
              <span>Generated: {indiaDateTime(analysis!.created_at)}</span>
            </div>
            <div className="ai-group">
              <h4>Investor summary</h4>
              <p className="ai-summary">{output.summary}</p>
              <EvidenceLinks
                analysis={analysis!}
                ids={output.summary_evidence_ids}
              />
            </div>
            {output.historical_context?.length ||
            Object.values(analysis!.evidence_registry).some(
              (item) => item.kind === "MEMORY_CHUNK",
            ) ? (
              <div className="ai-group">
                <h4>Historical company evidence</h4>
                {output.historical_context?.map((note, i) => (
                  <article key={i}>
                    <p>{note.text}</p>
                    <blockquote>{note.source_quote}</blockquote>
                    <EvidenceLinks
                      analysis={analysis!}
                      ids={note.evidence_ids}
                    />
                  </article>
                ))}
                <details>
                  <summary>Retrieved passages used as context</summary>
                  {Object.entries(analysis!.evidence_registry)
                    .filter(([, item]) => item.kind === "MEMORY_CHUNK")
                    .map(([id, item]) => (
                      <article key={id}>
                        <strong>{item.title}</strong>
                        <p>
                          {item.published_at?.slice(0, 10)} · page {item.page} ·{" "}
                          {item.section} · {item.data_origin}
                        </p>
                        <p>{item.excerpt}</p>
                        <p className="muted">
                          Retrieved for: {item.retrieval_reason}
                        </p>
                        <EvidenceLinks analysis={analysis!} ids={[id]} />
                      </article>
                    ))}
                </details>
              </div>
            ) : null}
            {quality && (
              <>
                {qualityNote(
                  "Operating performance",
                  quality.operating_performance,
                )}
                {qualityNote(
                  "Historical / seasonal context",
                  quality.seasonality_context,
                )}
                {qualityNote("Earnings quality", quality.earnings_quality)}
                <div className="ai-group ai-valuation">
                  <h4>Current valuation and expectations</h4>
                  <div className="ai-valuation-grid">
                    <div>
                      <span>Dated share price</span>
                      <strong>
                        {snapshot
                          ? `₹${Number(snapshot.close_price).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
                          : "Unavailable"}
                      </strong>
                      <small>{snapshot?.as_of_date || "No fresh price"}</small>
                    </div>
                    <div>
                      <span>Almonds calculated P/E</span>
                      <strong>
                        {calculatedPe
                          ? `${Number(calculatedPe).toFixed(2)}×`
                          : "Unavailable"}
                      </strong>
                      <small>Verified trailing EPS basis</small>
                    </div>
                    <div>
                      <span>External P/E cross-check</span>
                      <strong>
                        {snapshot?.pe_ratio
                          ? `${Number(snapshot.pe_ratio).toFixed(2)}×`
                          : "Unavailable"}
                      </strong>
                      <small>
                        Calculation comparison, not a valuation benchmark
                      </small>
                    </div>
                  </div>
                  <p>{quality.valuation_expectations.text}</p>
                  <EvidenceLinks
                    analysis={analysis!}
                    ids={quality.valuation_expectations.evidence_ids}
                  />
                </div>
                <div className="ai-group ai-result-view">
                  <h4>Results vs valuation</h4>
                  <span className="ai-support-label">
                    {quality.results_vs_valuation.assessment.replaceAll(
                      "_",
                      " ",
                    )}
                  </span>
                  <p>{quality.results_vs_valuation.text}</p>
                  <EvidenceLinks
                    analysis={analysis!}
                    ids={quality.results_vs_valuation.evidence_ids}
                  />
                </div>
                <div className="ai-watch-grid">
                  <div className="ai-group">
                    <h4>What strengthens the case</h4>
                    {quality.strengthening_evidence.map((note, index) => (
                      <article key={index}>
                        <p>{note.text}</p>
                        <EvidenceLinks
                          analysis={analysis!}
                          ids={note.evidence_ids}
                        />
                      </article>
                    ))}
                  </div>
                  <div className="ai-group ai-concerns">
                    <h4>What weakens the case</h4>
                    {quality.weakening_evidence.map((note, index) => (
                      <article key={index}>
                        <p>{note.text}</p>
                        <EvidenceLinks
                          analysis={analysis!}
                          ids={note.evidence_ids}
                        />
                      </article>
                    ))}
                  </div>
                </div>
                <div className="ai-group">
                  <h4>Research gaps · {quality.research_priority} priority</h4>
                  <ul>
                    {quality.research_gaps.map((gap, index) => (
                      <li key={index}>
                        <strong>{gap.type.replaceAll("_", " ")}</strong>
                        {" · "}
                        {gap.question}
                        <EvidenceLinks
                          analysis={analysis!}
                          ids={gap.evidence_ids}
                        />
                      </li>
                    ))}
                  </ul>
                  <p className="ai-confidence">
                    Evidence confidence: {quality.confidence.toLowerCase()}
                  </p>
                </div>
                <details className="ai-supporting">
                  <summary>Positives, concerns and metric details</summary>
                  {claimList(
                    "Positives",
                    output.positives,
                    "No supported positive result changes were identified.",
                  )}
                  {claimList(
                    "Concerns",
                    output.concerns,
                    "No supported negative result changes were identified.",
                  )}
                  {output.material_changes.length > 0 && (
                    <div className="ai-group">
                      <h4>Material changes</h4>
                      {output.material_changes.map((change, index) => (
                        <article key={index}>
                          <strong>
                            {change.metric}: {change.change}
                          </strong>
                          <p>{change.interpretation}</p>
                          <EvidenceLinks
                            analysis={analysis!}
                            ids={change.evidence_ids}
                          />
                        </article>
                      ))}
                    </div>
                  )}
                </details>
                {output.questions.length > 0 && (
                  <details className="ai-supporting">
                    <summary>Questions to watch next quarter</summary>
                    <ul>
                      {output.questions.map((item, index) => (
                        <li key={index}>{item}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </>
            )}
            {!quality &&
              claimList(
                "Positives",
                output.positives,
                "No supported positive result changes were identified.",
              )}
            {!quality &&
              claimList(
                "Concerns",
                output.concerns,
                "No supported negative result changes were identified.",
              )}
            {!quality && output.result_view && (
              <div className="ai-group ai-result-view">
                <h4>Results view</h4>
                <span
                  className={`ai-sentiment ai-sentiment-${output.result_view.sentiment.toLowerCase()}`}
                >
                  {output.result_view.sentiment}
                </span>
                <p>{output.result_view.reason}</p>
                <EvidenceLinks
                  analysis={analysis!}
                  ids={output.result_view.evidence_ids}
                />
              </div>
            )}
            {!quality && output.material_changes.length > 0 && (
              <div className="ai-group">
                <h4>Material changes</h4>
                {output.material_changes.map((change, index) => (
                  <article key={index}>
                    <strong>
                      {change.metric}: {change.change}
                    </strong>
                    <p>{change.interpretation}</p>
                    <EvidenceLinks
                      analysis={analysis!}
                      ids={change.evidence_ids}
                    />
                  </article>
                ))}
              </div>
            )}
            {report && (
              <div className="ai-group">
                <h4>Management commentary</h4>
                <p>Availability: {report.commentary_availability?.status?.replaceAll("_", " ") || "NO COMMENTARY FOUND"}</p>
                {report.commentary_availability?.audio_sources?.map((source) => (
                  <p key={source.event_id}>
                    <a href={source.url} target="_blank" rel="noreferrer">
                      {source.source} recording announcement · {indiaDate(source.published_at)} ↗
                    </a>{" "}Audio has not been transcribed.
                  </p>
                ))}
                {report.source_documents?.length > 0 && (
                  <p>
                    Associated documents:{" "}
                    {report.source_documents.map((document, index) => (
                      <span key={document.document_id}>
                        {index ? " · " : ""}
                        <a
                          href={document.document_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {document.document_type.replaceAll("_", " ")} ·{" "}
                          {document.source} · {indiaDate(document.published_at)}{" "}
                          ↗
                        </a>
                        {document.source_references?.filter((ref) => ref.source !== document.source).map((ref) => (
                          <span key={`${document.document_id}-${ref.source}`}> · <a href={ref.source_url} target="_blank" rel="noreferrer">{ref.source} filing ↗</a></span>
                        ))}
                      </span>
                    ))}
                  </p>
                )}
                {report.management_statements.length ? (
                  report.management_statements.map((statement) => (
                    <article key={statement.evidence_id}>
                      <strong>
                        {statement.statement_type.replaceAll("_", " ")} ·{" "}
                        {statement.topic.replaceAll("_", " ")}
                      </strong>
                      <p>
                        {statement.statement_type === "GUIDANCE"
                          ? "Management guidance"
                          : "Management claim"}
                        : “{statement.raw_statement}”
                      </p>
                      <small>
                        {statement.speaker_name ||
                          statement.speaker_role ||
                          "Attributed management"}{" "}
                        · {indiaDate(statement.published_at)} · page{" "}
                        {statement.page_number} ·{" "}
                        <a href={statement.document_url} target="_blank" rel="noreferrer">
                          Source page ↗
                        </a>
                      </small>
                      <EvidenceLinks
                        analysis={analysis!}
                        ids={[statement.evidence_id]}
                      />
                    </article>
                  ))
                ) : (
                  <p>
                    No attributed management statement was found in associated processed documents.
                  </p>
                )}
              </div>
            )}
            {report && report.management_guidance.length > 0 && (
              <div className="ai-group">
                <h4>Management guidance</h4>
                {report.management_guidance.map((item) => (
                  <article key={item.id}>
                    <strong>
                      {item.metric.replaceAll("_", " ")} · {item.target_period}
                    </strong>
                    <p>
                      {item.lower_bound}–{item.upper_bound}
                      {item.unit === "PERCENT" ? "%" : " ₹ crore"} ·{" "}
                      {item.change.replaceAll("_", " ")} · actual:{" "}
                      {item.actual_comparison.replaceAll("_", " ")} · execution:{" "}
                      {item.execution_outcome.replaceAll("_", " ")}
                    </p>
                    <EvidenceLinks
                      analysis={analysis!}
                      ids={[item.statement_evidence_id]}
                    />
                  </article>
                ))}
              </div>
            )}
            {report && report.management_execution_history?.length > 0 && (
              <div className="ai-group">
                <h4>Management execution history</h4>
                {report.management_execution_history.map((item) => (
                  <article key={item.evidence_id}>
                    <strong>{item.target_period} · {item.metric.replaceAll("_", " ")} · {item.scope}</strong>
                    <p>Management guided {item.lower_bound}–{item.upper_bound} {item.unit === "PERCENT" ? "%" : "₹ crore"}. Outcome: {item.execution_outcome.replaceAll("_", " ")}.
                      {item.actual_value != null && ` Verified actual: ${item.actual_value}${item.unit === "PERCENT" ? "%" : " ₹ crore"}.`}
                    </p>
                    <small>{item.speaker_name || item.speaker_role || "Attributed management"} · {indiaDate(item.published_at)} · {item.change.replaceAll("_", " ")} · <a href={item.document_url} target="_blank" rel="noreferrer">Management source ↗</a>
                      {item.actual_sources.map((source, index) => (
                        <span key={index}> · <a href={source.document_url} target="_blank" rel="noreferrer">Actual evidence ↗</a></span>
                      ))}
                    </small>
                  </article>
                ))}
              </div>
            )}
            {report && report.guidance_changes?.length > 0 && (
              <div className="ai-group">
                <h4>Guidance changes</h4>
                {report.guidance_changes.map((item) => (
                  <article key={item.statement_evidence_id}>
                    <strong>
                      {item.status.replaceAll("_", " ")} ·{" "}
                      {item.topic.replaceAll("_", " ")}
                    </strong>
                    <p>
                      Management withdrew guidance on{" "}
                      {indiaDate(item.published_at)}. The prior guidance remains
                      in the history.
                    </p>
                    <EvidenceLinks
                      analysis={analysis!}
                      ids={[item.statement_evidence_id]}
                    />
                  </article>
                ))}
              </div>
            )}
            {report && report.claim_comparisons.length > 0 && (
              <div className="ai-group">
                <h4>Management claims vs financial evidence</h4>
                {report.claim_comparisons.map((item) => (
                  <article key={item.statement_evidence_id}>
                    <strong>{item.status.replaceAll("_", " ")}</strong>
                    <p>{item.note}</p>
                    <EvidenceLinks
                      analysis={analysis!}
                      ids={[
                        item.statement_evidence_id,
                        ...item.financial_evidence_ids,
                      ]}
                    />
                  </article>
                ))}
              </div>
            )}
            {output.management_commentary && (
              <div className="ai-group">
                <h4>Analyst reading of management commentary</h4>
                <p>{output.management_commentary.text}</p>
                <EvidenceLinks
                  analysis={analysis!}
                  ids={output.management_commentary.evidence_ids}
                />
              </div>
            )}
            {!quality && output.what_might_happen && (
              <div className="ai-group">
                <h4>Evidence to monitor</h4>
                <p>{output.what_might_happen.text}</p>
                <EvidenceLinks
                  analysis={analysis!}
                  ids={output.what_might_happen.evidence_ids}
                />
              </div>
            )}
            {!quality && output.valuation_assessment && (
              <div className="ai-group ai-valuation">
                <h4>Do the results support the current valuation?</h4>
                <div className="ai-valuation-grid">
                  <div>
                    <span>Dated share price</span>
                    <strong>
                      {snapshot
                        ? `₹${Number(snapshot.close_price).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
                        : "Unavailable"}
                    </strong>
                    <small>
                      {snapshot?.as_of_date || "Enter a recent sourced close"}
                    </small>
                  </div>
                  <div>
                    <span>Source P/E reference</span>
                    <strong>
                      {snapshot?.pe_ratio
                        ? Number(snapshot.pe_ratio).toLocaleString("en-IN", {
                            maximumFractionDigits: 2,
                          })
                        : "Unavailable"}
                    </strong>
                    <small>See calculated P/E in Valuation above</small>
                  </div>
                  <div>
                    <span>Source PEG reference</span>
                    <strong>
                      {snapshot?.peg_ratio
                        ? Number(snapshot.peg_ratio).toLocaleString("en-IN", {
                            maximumFractionDigits: 2,
                          })
                        : "Unavailable"}
                    </strong>
                    <small>
                      {snapshot?.peg_ratio
                        ? `Source P/E ÷ ${snapshot.forward_eps_growth_pct}% forward EPS growth`
                        : !snapshot?.pe_ratio
                          ? "Needs sourced P/E"
                          : snapshot.forward_eps_growth_pct !== null &&
                              Number(snapshot.forward_eps_growth_pct) <= 0
                            ? "Unavailable for nonpositive growth"
                            : "Needs a sourced forward EPS growth estimate"}
                    </small>
                  </div>
                </div>
                <p>
                  <strong>
                    View:{" "}
                    {output.valuation_assessment.verdict.replaceAll("_", " ")}
                  </strong>{" "}
                  · {output.valuation_assessment.text}
                </p>
                {snapshot && (
                  <p className="ai-valuation-sources">
                    <a
                      href={snapshot.source_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Price and P/E source ↗
                    </a>
                    {snapshot.growth_source_url && (
                      <>
                        {" "}
                        ·{" "}
                        <a
                          href={snapshot.growth_source_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Growth estimate source ↗
                        </a>
                      </>
                    )}
                  </p>
                )}
                <EvidenceLinks
                  analysis={analysis!}
                  ids={output.valuation_assessment.evidence_ids}
                />
              </div>
            )}
            {!quality && output.research_action && (
              <div className="ai-group">
                <h4>
                  Research step ·{" "}
                  {output.research_action.action.replaceAll("_", " ")}
                </h4>
                <p>{output.research_action.reason}</p>
                <EvidenceLinks
                  analysis={analysis!}
                  ids={output.research_action.evidence_ids}
                />
              </div>
            )}
            {!quality && output.questions.length > 0 && (
              <div className="ai-group">
                <h4>Questions to watch</h4>
                <ul>
                  {output.questions.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
            {output.data_quality_notes.length > 0 && (
              <div className="ai-group">
                <h4>Data quality notes</h4>
                <ul>
                  {output.data_quality_notes.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
            <p className="ai-metadata">
              {analysis!.status === "VERIFIED_FALLBACK"
                ? "Verified facts summary"
                : `Model: ${analysis!.model_provider} / ${analysis!.model_name}`}{" "}
              · Generated: {indiaDate(analysis!.created_at)} · Prompt:{" "}
              {analysis!.prompt_version}
            </p>
          </>
        )
      )}
    </div>
  );
}

export function FinancialsSection({
  companies,
  requestedSymbol,
}: {
  companies: Company[];
  requestedSymbol?: string | null;
}) {
  const [symbol, setSymbol] = useState(requestedSymbol || "");
  const [scope, setScope] = useState<"CONSOLIDATED" | "STANDALONE">(
    "CONSOLIDATED",
  );
  const [data, setData] = useState<CompanyFinancials | null>(null);
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);
  const selectedSymbol = companies.some((company) => company.symbol === symbol)
    ? symbol
    : (companies[0]?.symbol ?? "");
  const selectedCompany = companies.find(
    (company) => company.symbol === selectedSymbol,
  );
  const visibleSnapshot =
    snapshot?.company_id === selectedCompany?.id ? snapshot : null;
  const analysisSnapshot =
    visibleSnapshot &&
    marketSnapshotAge(visibleSnapshot) >= 0 &&
    marketSnapshotAge(visibleSnapshot) <= 7
      ? visibleSnapshot
      : null;
  useEffect(() => {
    if (symbol !== selectedSymbol) setSymbol(selectedSymbol);
  }, [symbol, selectedSymbol]);
  useEffect(() => {
    if (
      requestedSymbol &&
      companies.some((company) => company.symbol === requestedSymbol)
    )
      setSymbol(requestedSymbol);
  }, [requestedSymbol, companies]);
  useEffect(() => {
    if (!selectedSymbol) {
      setData(null);
      setError("");
      return;
    }
    let active = true;
    setData(null);
    setError("");
    api
      .companyFinancials(selectedSymbol)
      .then((value) => {
        if (active) {
          setData(value);
          setError("");
        }
      })
      .catch((cause) => {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "Financials could not load.",
          );
      });
    return () => {
      active = false;
    };
  }, [selectedSymbol]);
  const visibleData = data?.symbol === selectedSymbol ? data : null;
  const scopes = useMemo(
    () => new Set(visibleData?.periods.map((period) => period.scope) || []),
    [visibleData],
  );
  const effectiveScope = scopes.size === 0 || scopes.has(scope)
    ? scope
    : scopes.has("CONSOLIDATED")
      ? "CONSOLIDATED"
      : "STANDALONE";
  const periods = (visibleData?.periods || []).filter(
    (period) => period.scope === effectiveScope,
  );
  const validated = periods.filter((period) => period.status === "VALIDATED");
  const latest = validated[0];
  const review = periods.filter((period) => period.status === "NEEDS_REVIEW");

  return (
    <section id="financials" className="section-block financials-block">
      <div className="section-heading">
        <div>
          <p className="eyebrow">VERIFIED FINANCIAL MEMORY</p>
          <h2>Financial performance</h2>
        </div>
        {companies.length > 0 && (
          <label className="financial-company">
            Company
            <select
              value={selectedSymbol}
              onChange={(event) => setSymbol(event.target.value)}
            >
              {companies.map((company) => (
                <option key={company.id} value={company.symbol}>
                  {company.symbol}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <p className="financial-intro">
        Figures come from verified result tables. Open any number to see its
        source, page, original unit, or calculation.
      </p>
      {error && (
        <div className="event-error" role="alert">
          {error}
        </div>
      )}
      {!companies.length && (
        <p className="financial-empty">
          Add a company to view financial results.
        </p>
      )}
      {visibleData && scopes.size > 0 && (
        <>
          <div className="financial-scope" aria-label="Reporting scope">
            {(["CONSOLIDATED", "STANDALONE"] as const)
              .filter((item) => scopes.has(item))
              .map((item) => (
                <button
                  key={item}
                  className={effectiveScope === item ? "active" : ""}
                  onClick={() => setScope(item)}
                >
                  {item}
                </button>
              ))}
          </div>
          {latest && (
            <div className="financial-highlights">
              <div>
                <span>Revenue · {periodName(latest)}</span>
                {financialValue(latest, "REVENUE_FROM_OPERATIONS")}
              </div>
              <div>
                <span>EBITDA · calculated</span>
                {metricValue(latest, "EBITDA")}
              </div>
              <div>
                <span>Profit after tax</span>
                {financialValue(latest, "PAT")}
              </div>
            </div>
          )}
          {latest && (
            <AnalysisPanel
              key={`${selectedSymbol}-${latest.id}-${analysisSnapshot?.updated_at || "no-price"}`}
              symbol={selectedSymbol}
              period={latest}
              snapshot={analysisSnapshot}
            />
          )}
          <div className="table-card financial-history">
            <div className="table-overline">
              <span>{effectiveScope} HISTORY</span>
              <span>₹ CRORE EXCEPT EPS, MARGINS AND GROWTH</span>
            </div>
            {validated.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Period</th>
                      <th>Revenue</th>
                      <th>YoY</th>
                      <th>QoQ</th>
                      <th>EBITDA*</th>
                      <th>EBITDA margin</th>
                      <th>Margin Δ YoY</th>
                      <th>PAT</th>
                      <th>PAT YoY</th>
                      <th>PAT QoQ</th>
                      <th>PAT margin</th>
                      <th>Basic EPS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {validated.map((period) => (
                      <tr key={period.id}>
                        <th>
                          {periodName(period)}
                          <small>{period.period_end}</small>
                        </th>
                        <td>
                          {financialValue(period, "REVENUE_FROM_OPERATIONS")}
                        </td>
                        <td>{metricValue(period, "REVENUE_YOY")}</td>
                        <td>{metricValue(period, "REVENUE_QOQ")}</td>
                        <td>{metricValue(period, "EBITDA")}</td>
                        <td>{metricValue(period, "EBITDA_MARGIN")}</td>
                        <td>
                          {metricValue(period, "EBITDA_MARGIN_CHANGE_BPS")}
                        </td>
                        <td>{financialValue(period, "PAT")}</td>
                        <td>{metricValue(period, "PAT_YOY")}</td>
                        <td>{metricValue(period, "PAT_QOQ")}</td>
                        <td>{metricValue(period, "PAT_MARGIN")}</td>
                        <td>{financialValue(period, "BASIC_EPS")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="financial-empty">
                No validated financial periods for this scope yet.
              </p>
            )}
          </div>
          <p className="financial-footnote">
            * EBITDA is calculated as PBT + finance costs + depreciation and
            amortisation; the ESDS filing does not report EBITDA directly.
            “Validated” means checked against the PDF table, and does not mean
            audited. Missing comparisons remain blank.
          </p>
          {review.length > 0 && (
            <div className="financial-review">
              <strong>
                {review.length} period{review.length === 1 ? "" : "s"} need
                review
              </strong>
              {review.map((period) => (
                <p key={period.id}>
                  {periodName(period)}: {period.status_note}
                </p>
              ))}
            </div>
          )}
        </>
      )}
      {selectedSymbol && (
        <MarketSnapshotPanel
          key={selectedSymbol}
          symbol={selectedSymbol}
          onSaved={setSnapshot}
        />
      )}
      {selectedSymbol && <ThesisPanel key={`${selectedSymbol}:${effectiveScope}`} symbol={selectedSymbol} scope={effectiveScope} />}
      {selectedSymbol && (
        <ValuationPanel
          symbol={selectedSymbol}
          scope={effectiveScope}
          snapshot={visibleSnapshot}
          onMarketChanged={setSnapshot}
          periods={periods}
          growth={(
            ["REVENUE_YOY", "REVENUE_QOQ", "PAT_YOY", "PAT_QOQ"] as const
          ).flatMap((code) => {
            const metric = latest?.metrics.find(
              (item) => item.metric_code === code,
            );
            return metric
              ? [{ label: code.replaceAll("_", " "), value: metric.value }]
              : [];
          })}
        />
      )}
      {visibleData && visibleData.periods.length === 0 && (
        <p className="financial-empty">
          No validated financial results for {selectedSymbol} yet. If a
          financial-results PDF is available in Company events, open it and
          select “Process financial results.” Results that need review will
          appear here with their review notes.
        </p>
      )}
    </section>
  );
}
