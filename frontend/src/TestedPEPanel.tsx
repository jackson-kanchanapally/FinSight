import { useEffect, useState, type FormEvent } from "react";
import { api, type InvestorScenario, type TestedPERange } from "./api";

export function TestedPEPanel({
  symbol,
  scope,
  onResult,
  onSaved,
}: {
  symbol: string;
  scope: string;
  onResult?: (value: TestedPERange) => void;
  onSaved?: () => void;
}) {
  const [result, setResult] = useState<TestedPERange | null>(null);
  const [scenarios, setScenarios] = useState<InvestorScenario[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .automaticPe(symbol, scope)
      .then((value) => {
        if (active) {
          setResult(value);
          setScenarios(value.scenarios || []);
          onResult?.(value);
        }
      })
      .catch((cause) => {
        if (active) setError(cause.message);
      });
    return () => {
      active = false;
    };
  }, [symbol, scope, onResult]);
  function change(index: number, key: string, value: string) {
    setScenarios((items) =>
      items.map((item, i) =>
        i === index
          ? {
              ...item,
              [key]: key === "years" ? Number(value) : value,
              source: "User selected scenario",
              source_type: "USER_ASSUMPTION",
            }
          : item,
      ),
    );
  }
  async function calculate(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const value = await api.testedPe(symbol, { scope, scenarios });
      setResult(value);
      onResult?.(value);
      onSaved?.();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Scenario calculation failed",
      );
    } finally {
      setBusy(false);
    }
  }
  const number = (value?: string | null) =>
    value == null
      ? "Unavailable"
      : Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  return (
    <section className="ai-group tested-pe">
      <h4>What P/E do current fundamentals support?</h4>
      <p>
        <strong>SCENARIO — NOT FORECAST</strong>. The range is conditional on
        the assumptions below. Every input is visible and editable.
      </p>
      {error && (
        <p role="alert" className="event-error">
          {error}
        </p>
      )}
      {!result && !error && <p>Loading scenario evidence…</p>}
      {result && (
        <>
          <p>
            {result.supported_pe_range
              ? `Tested range ${result.supported_pe_range.lower}–${result.supported_pe_range.upper}×; base ${result.supported_pe_range.central}×.`
              : result.missing.join(" · ")}
          </p>
          <form onSubmit={calculate}>
            <div className="scenario-table-wrap">
              <table className="scenario-table">
                <thead>
                  <tr>
                    <th>Scenario</th>
                    <th>EPS growth %</th>
                    <th>Years</th>
                    <th>Terminal P/E</th>
                    <th>Required return %</th>
                    <th>Implied current P/E</th>
                  </tr>
                </thead>
                <tbody>
                  {scenarios.map((item, index) => (
                    <tr key={item.name}>
                      <th>{item.name.toLowerCase()}</th>
                      {(
                        [
                          "eps_growth_pct",
                          "years",
                          "terminal_pe",
                          "required_return_pct",
                        ] as const
                      ).map((key) => (
                        <td key={key}>
                          <input
                            aria-label={`${item.name} ${key.replaceAll("_", " ")}`}
                            type="number"
                            required
                            step={key === "years" ? "1" : "any"}
                            min={
                              key === "years"
                                ? 1
                                : key === "terminal_pe"
                                  ? 0.1
                                  : -99.9
                            }
                            max={key === "years" ? 20 : 1000}
                            value={item[key]}
                            onChange={(event) =>
                              change(index, key, event.target.value)
                            }
                          />
                        </td>
                      ))}
                      <td>
                        {number(
                          result.scenarios?.find(
                            (row) => row.name === item.name,
                          )?.implied_current_pe,
                        )}
                        ×
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button
              className="button button-outline button-small"
              disabled={busy}
            >
              {busy ? "Calculating…" : "Save assumptions and update report"}
            </button>
          </form>
          <details className="ai-supporting">
            <summary>Assumption provenance and methodology</summary>
            {result.scenarios?.map((item) => (
              <p key={item.name}>
                <strong>
                  {item.name} · {item.source_type}
                </strong>{" "}
                — {item.source}
              </p>
            ))}
            <p>{result.methodology}</p>
            <p>{result.quality_adjustment}</p>
            <p>
              Price date: {result.market_date || "unavailable"} · Verified
              diluted TTM EPS: {number(result.ttm_eps)}.
            </p>
            {result.price_source && (
              <a
                href={result.price_source.url}
                target="_blank"
                rel="noreferrer"
              >
                Dated price source
              </a>
            )}
          </details>
          <h4>What growth does today’s price require?</h4>
          {result.reverse_expectations ? (
            <>
              <p>
                The base scenario requires{" "}
                <strong>
                  {number(result.reverse_expectations.required_eps_cagr_pct)}%
                  EPS CAGR
                </strong>{" "}
                to earn its selected annual return at its terminal multiple.
              </p>
              <p>
                {result.expectations_gap?.classification.replaceAll("_", " ")} ·{" "}
                {result.expectations_gap?.reason}
              </p>
              {result.growth_comparisons?.map((row) => (
                <p key={row.metric}>
                  {row.metric.replaceAll("_", " ")}: {number(row.value)}% ·{" "}
                  {row.source_type}
                </p>
              ))}
              <details>
                <summary>Reverse calculation</summary>
                <p>
                  Required future price = current price × (1 + required
                  return)^years.
                </p>
                <p>
                  Required future EPS = required future price ÷ terminal P/E.
                  Required EPS CAGR = (required future EPS ÷ current
                  EPS)^(1/years) − 1.
                </p>
                <p>
                  Calculated future price: ₹
                  {number(result.reverse_expectations.target_future_price)};
                  required EPS: ₹
                  {number(result.reverse_expectations.required_future_eps)}.
                  These are scenario outputs.
                </p>
                {result.expectations_gap?.dispersion_pct && (
                  <p>
                    Comparison dispersion:{" "}
                    {number(result.expectations_gap.dispersion_pct)} percentage
                    points · {result.expectations_gap.dispersion_source}.
                  </p>
                )}
              </details>
            </>
          ) : (
            <p>{result.missing.join(" · ")}</p>
          )}
          <details className="ai-supporting">
            <summary>Historical valuation evidence</summary>
            <p>{result.historical_pe?.methodology}</p>
            <p>
              {result.historical_pe?.one_year
                ? `1Y observed P/E ${result.historical_pe.one_year.low}–${result.historical_pe.one_year.high}×; median ${result.historical_pe.one_year.median}×.`
                : `Only ${result.historical_pe?.available_points || 0} usable dated observations; a representative historical range is unavailable.`}
            </p>
          </details>
        </>
      )}
    </section>
  );
}
