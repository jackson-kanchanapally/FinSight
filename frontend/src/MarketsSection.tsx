import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import type { Company } from "./api";
import { displayDate, money } from "./format";
import {
  compact,
  nseApi,
  plain,
  signed,
  tone,
  type LivePortfolio,
  type MarketPulse,
  type Session,
  type StockInsight,
  type SymbolMatch,
} from "./nseApi";
import "./markets.css";
import { CompanyReactionHistory } from "./MarketReaction";

const PERIODS = ["1D", "1W", "1M", "3M", "6M", "1Y"] as const;

function openSymbol(symbol: string) {
  window.location.hash = `markets/${symbol}`;
}

function SessionBadge({
  session,
  asOf,
}: {
  session: Session;
  asOf?: string | null;
}) {
  const live = session.state === "OPEN" && !session.stale;
  return (
    <span
      className={`mk-session ${live ? "is-open" : ""}`}
      title={session.note}
    >
      <i aria-hidden="true" />
      {live
        ? "Market open"
        : session.stale
          ? "Showing last session"
          : "Market closed"}
      {asOf ? <small> · NSE {asOf}</small> : null}
    </span>
  );
}

/** A min–max track with the current value marked; optional median tick. */
function RangeMeter({
  min,
  max,
  value,
  median,
  minLabel,
  maxLabel,
  label,
}: {
  min: number;
  max: number;
  value: number;
  median?: number;
  minLabel: string;
  maxLabel: string;
  label: string;
}) {
  const span = max - min || 1;
  const at = (v: number) =>
    `${Math.min(100, Math.max(0, ((v - min) / span) * 100))}%`;
  return (
    <div className="mk-range" role="img" aria-label={label}>
      <div className="mk-range-track">
        {median !== undefined && (
          <span
            className="mk-range-median"
            style={{ left: at(median) }}
            title={`Median ${plain(median)}`}
          />
        )}
        <span className="mk-range-dot" style={{ left: at(value) }} />
      </div>
      <div className="mk-range-labels">
        <span>{minLabel}</span>
        <span>{maxLabel}</span>
      </div>
    </div>
  );
}

/** Three-month closing-price line with a crosshair tooltip. */
function PriceLine({ points }: { points: { date: string; close: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const width = 640;
  const height = 170;
  const pad = { top: 12, right: 8, bottom: 22, left: 8 };
  const closes = points.map((p) => p.close);
  const low = Math.min(...closes);
  const high = Math.max(...closes);
  const spread = high - low || 1;
  const x = (i: number) =>
    pad.left +
    (i / Math.max(1, points.length - 1)) * (width - pad.left - pad.right);
  const y = (v: number) =>
    pad.top + (1 - (v - low) / spread) * (height - pad.top - pad.bottom);
  const path = points
    .map(
      (p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.close).toFixed(1)}`,
    )
    .join(" ");
  const area = `${path} L${x(points.length - 1).toFixed(1)},${height - pad.bottom} L${x(0).toFixed(1)},${height - pad.bottom} Z`;

  function move(event: ReactPointerEvent<SVGSVGElement>) {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const ratio = (event.clientX - box.left) / box.width;
    const index = Math.round(
      ((ratio * width - pad.left) / (width - pad.left - pad.right)) *
        (points.length - 1),
    );
    setHover(Math.min(points.length - 1, Math.max(0, index)));
  }

  const active = hover !== null ? points[hover] : null;
  return (
    <figure className="mk-line">
      <svg
        ref={ref}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        onPointerMove={move}
        onPointerLeave={() => setHover(null)}
        role="img"
        aria-label={`Closing prices from ${points[0].date} to ${points[points.length - 1].date}, between ${plain(low)} and ${plain(high)}`}
      >
        <line
          className="mk-line-base"
          x1={pad.left}
          x2={width - pad.right}
          y1={height - pad.bottom}
          y2={height - pad.bottom}
        />
        <path className="mk-line-area" d={area} />
        <path
          className="mk-line-path"
          d={path}
          vectorEffect="non-scaling-stroke"
        />
        {active && hover !== null && (
          <>
            <line
              className="mk-line-cross"
              x1={x(hover)}
              x2={x(hover)}
              y1={pad.top}
              y2={height - pad.bottom}
              vectorEffect="non-scaling-stroke"
            />
            <circle
              className="mk-line-dot"
              cx={x(hover)}
              cy={y(active.close)}
              r="4"
              vectorEffect="non-scaling-stroke"
            />
          </>
        )}
      </svg>
      {active && hover !== null && (
        <div
          className="mk-tooltip"
          style={{ left: `${(x(hover) / width) * 100}%` }}
        >
          <strong>{money(active.close)}</strong>
          <span>{displayDate(active.date)}</span>
        </div>
      )}
      <figcaption>
        <span>{displayDate(points[0].date)}</span>
        <span>
          Low {money(low)} · High {money(high)}
        </span>
        <span>{displayDate(points[points.length - 1].date)}</span>
      </figcaption>
    </figure>
  );
}

function SymbolSearch({ onPick }: { onPick: (symbol: string) => void }) {
  const [text, setText] = useState("");
  const [matches, setMatches] = useState<SymbolMatch[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    const query = text.trim();
    if (query.length < 2) {
      setMatches([]);
      return;
    }
    const timer = window.setTimeout(async () => {
      try {
        const result = await nseApi.search(query);
        setMatches(result.results.slice(0, 8));
        setError(
          result.results.length
            ? ""
            : "No NSE symbol matches. Try the ticker, e.g. TATAMOTORS.",
        );
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Search failed.");
      }
    }, 280);
    return () => window.clearTimeout(timer);
  }, [text]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const pick = matches[0]?.symbol ?? text.trim().toUpperCase();
    if (pick) {
      onPick(pick);
      setMatches([]);
    }
  }

  return (
    <form className="mk-search" onSubmit={submit} role="search">
      <div className="search-box">
        <svg
          aria-hidden="true"
          width="17"
          height="17"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m16 16 5 5" />
        </svg>
        <input
          aria-label="Search NSE symbols"
          placeholder="Research any NSE stock — symbol or name"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </div>
      {matches.length > 0 && (
        <ul className="mk-suggestions">
          {matches.map((match) => (
            <li key={match.symbol}>
              <button
                type="button"
                onClick={() => {
                  onPick(match.symbol);
                  setMatches([]);
                  setText("");
                }}
              >
                <strong>{match.symbol}</strong>
                <span>{money(match.last_close)}</span>
                <span className={tone(match.pct_change)}>
                  {signed(match.pct_change)}%
                </span>
                {match.inactive && (
                  <small>last traded {displayDate(match.last_date)}</small>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && !matches.length && <p className="field-hint">{error}</p>}
    </form>
  );
}

function StockCard({
  symbol,
  registered,
}: {
  symbol: string;
  registered: boolean;
}) {
  const [data, setData] = useState<StockInsight | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    nseApi
      .stock(symbol)
      .then((value) => alive && setData(value))
      .catch(
        (cause) =>
          alive &&
          setError(
            cause instanceof Error ? cause.message : "Could not load stock.",
          ),
      )
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [symbol]);

  if (loading)
    return <div className="loading-state">Reading NSE data for {symbol}…</div>;
  if (error || !data)
    return (
      <div className="load-error" role="alert">
        {error || "No data."}
      </div>
    );
  const q = data.quote;
  const range = data.range_52w;
  const rel = data.relative;
  return (
    <article className="panel mk-stock" aria-label={`${symbol} research`}>
      <header className="mk-stock-head">
        <div>
          <p className="eyebrow">
            {q?.source === "LIVE" ? "LIVE QUOTE" : "LAST CLOSE"}{" "}
            {registered && <span className="mk-chip">In your companies</span>}
          </p>
          <h2>{data.symbol}</h2>
          {q?.as_of && (
            <small className="mk-muted">
              As of {q.as_of} IST{q.series ? ` · series ${q.series}` : ""}
            </small>
          )}
        </div>
        {q && (
          <div className="mk-stock-price">
            <strong>{money(q.price)}</strong>
            <span className={tone(q.change_pct)}>
              {q.previous_close
                ? signed(Number(q.price) - Number(q.previous_close))
                : "—"}{" "}
              ({signed(q.change_pct)}%)
            </span>
            <small className="mk-muted">
              O {q.open ? money(q.open) : "—"} · H{" "}
              {q.high ? money(q.high) : "—"} · L {q.low ? money(q.low) : "—"}
            </small>
          </div>
        )}
      </header>

      {data.warnings.map((warning) => (
        <p className="mk-warning" key={warning}>
          ⚠ {warning}
        </p>
      ))}

      {data.history && data.history.points.length > 1 && (
        <section className="mk-block">
          <h3>
            Three-month closes
            {data.history.summary?.return_pct !== undefined && (
              <span className={tone(data.history.summary.return_pct)}>
                {" "}
                {signed(data.history.summary.return_pct)}%
              </span>
            )}
          </h3>
          <PriceLine points={data.history.points} />
        </section>
      )}

      <div className="mk-stock-grid">
        {range && (
          <section className="mk-block">
            <h3>52-week range</h3>
            <RangeMeter
              min={range.low}
              max={range.high}
              value={Number(q?.price ?? range.last_close)}
              minLabel={`${money(range.low)} · ${displayDate(range.low_date)}`}
              maxLabel={`${money(range.high)} · ${displayDate(range.high_date)}`}
              label={`Price sits at ${plain(range.position_pct, 0)}% of its 52-week range`}
            />
            <p className="mk-muted">
              {signed(range.from_high_pct)}% from high ·{" "}
              {signed(range.from_low_pct)}% from low
            </p>
          </section>
        )}

        {data.moving_averages.length > 0 && (
          <section className="mk-block">
            <h3>Moving averages</h3>
            <dl className="mk-dl">
              {data.moving_averages.map((average) => (
                <div key={average.days}>
                  <dt>{average.days}-day</dt>
                  <dd>
                    {money(average.value)}{" "}
                    <span className={tone(average.price_vs_average_pct)}>
                      price {signed(average.price_vs_average_pct)}%
                    </span>
                  </dd>
                </div>
              ))}
            </dl>
            {data.trend && <p className="mk-muted">{data.trend}</p>}
          </section>
        )}

        {rel && (
          <section className="mk-block">
            <h3>12 months vs {rel.benchmark}</h3>
            <dl className="mk-dl">
              <div>
                <dt>{data.symbol}</dt>
                <dd className={tone(rel.stock_return_pct)}>
                  {signed(rel.stock_return_pct)}%
                </dd>
              </div>
              <div>
                <dt>{rel.benchmark}</dt>
                <dd className={tone(rel.index_return_pct)}>
                  {signed(rel.index_return_pct)}%
                </dd>
              </div>
              <div>
                <dt>Out/under-performance</dt>
                <dd className={tone(rel.outperformance_pct_points)}>
                  {signed(rel.outperformance_pct_points)} pts
                </dd>
              </div>
              <div>
                <dt>Beta · correlation</dt>
                <dd>
                  {plain(rel.beta)} · {plain(rel.correlation)}
                </dd>
              </div>
            </dl>
            <p className="mk-muted">
              Split/bonus-adjusted by NSE; dividends excluded.
            </p>
          </section>
        )}

        {data.volume && (
          <section className="mk-block">
            <h3>Volume · {data.volume.days_analysed} sessions</h3>
            <dl className="mk-dl">
              <div>
                <dt>Average</dt>
                <dd>{compact(data.volume.avg_volume)}</dd>
              </div>
              <div>
                <dt>Last session</dt>
                <dd>
                  {compact(data.volume.last_volume)}{" "}
                  <span className={tone(data.volume.last_vs_avg_pct)}>
                    {signed(data.volume.last_vs_avg_pct, 0)}%
                  </span>
                </dd>
              </div>
              <div>
                <dt>Spike days (&gt;2× avg)</dt>
                <dd>{data.volume.volume_spike_days}</dd>
              </div>
            </dl>
          </section>
        )}

        <section className="mk-block">
          <h3>Dividends & corporate actions</h3>
          {data.dividends ? (
            <p>
              Trailing 12 months:{" "}
              <strong>{money(data.dividends.trailing_12m_per_share)}</strong>{" "}
              per share
              {data.dividends.yield_pct && (
                <> · yield {data.dividends.yield_pct}%</>
              )}
            </p>
          ) : (
            <p className="mk-muted">
              No dividend ex-dates in the last 12 months.
            </p>
          )}
          {data.corporate_actions?.upcoming.length ? (
            <ul className="mk-actions">
              {data.corporate_actions.upcoming.map((action) => (
                <li key={action.exDate + action.purpose}>
                  <span className="mk-chip">Upcoming</span> {action.purpose} ·
                  ex {displayDate(action.exDate)}
                </li>
              ))}
            </ul>
          ) : null}
          {data.corporate_actions?.recent.length ? (
            <ul className="mk-actions">
              {data.corporate_actions.recent.slice(0, 4).map((action) => (
                <li key={action.exDate + action.purpose}>
                  {action.purpose} · ex {displayDate(action.exDate)}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      </div>

      {registered && <CompanyReactionHistory symbol={data.symbol} />}

      {data.errors.length > 0 && (
        <p className="mk-muted mk-gaps">
          Not available from NSE right now:{" "}
          {data.errors.map((e) => e.source).join(", ")}.
        </p>
      )}
    </article>
  );
}

function Pulse({
  pulse,
  period,
  onPeriod,
}: {
  pulse: MarketPulse;
  period: string;
  onPeriod: (p: string) => void;
}) {
  const mood = pulse.mood;
  const vix = mood?.india_vix;
  const pe = pulse.valuation?.pe;
  const sectorMax = Math.max(
    1,
    ...[
      ...(pulse.sector_movers?.gainers ?? []),
      ...(pulse.sector_movers?.losers ?? []),
    ].map((m) => Math.abs(m.return_pct)),
  );
  return (
    <>
      {pulse.indices.items.length > 0 && (
        <section className="mk-indices" aria-label="Key NSE indices">
          {pulse.indices.items.map((index) => (
            <div className="mk-index" key={index.name}>
              <span>{index.name}</span>
              <strong>{plain(index.last)}</strong>
              <small className={tone(index.change_pct)}>
                {signed(index.change)} ({signed(index.change_pct)}%)
              </small>
            </div>
          ))}
        </section>
      )}

      <section className="mk-mood">
        {mood?.stock_breadth && (
          <div className="panel mk-panel">
            <p className="eyebrow">BREADTH · {displayDate(mood.date)}</p>
            <h3>{mood.stock_breadth.reading}</h3>
            <div
              className="mk-split"
              role="img"
              aria-label={`${mood.stock_breadth.up} advanced, ${mood.stock_breadth.down} declined`}
            >
              <span
                className="mk-split-up"
                style={{ flexGrow: mood.stock_breadth.up }}
              />
              <span
                className="mk-split-down"
                style={{ flexGrow: mood.stock_breadth.down }}
              />
            </div>
            <p className="mk-legend">
              <span>
                <i className="mk-key-up" />{" "}
                {mood.stock_breadth.up.toLocaleString("en-IN")} advanced
              </span>
              <span>
                <i className="mk-key-down" />{" "}
                {mood.stock_breadth.down.toLocaleString("en-IN")} declined
              </span>
            </p>
            {mood.index_breadth && (
              <p className="mk-muted">
                Indices: {mood.index_breadth.up} up / {mood.index_breadth.down}{" "}
                down ({mood.index_breadth.reading})
              </p>
            )}
          </div>
        )}
        {vix && (
          <div className="panel mk-panel">
            <p className="eyebrow">INDIA VIX · FEAR GAUGE</p>
            <h3>
              {plain(vix.level)}{" "}
              <span className={tone(-vix.change_1d_pct)}>
                {signed(vix.change_1d_pct)}%
              </span>
            </h3>
            {vix.range && (
              <RangeMeter
                min={vix.range.min}
                max={vix.range.max}
                value={vix.level}
                minLabel={`${plain(vix.range.min)} low`}
                maxLabel={`${plain(vix.range.max)} high`}
                label={`VIX higher than on ${plain(vix.range.percentile, 0)}% of days in the past year`}
              />
            )}
            <p className="mk-muted">
              Close {displayDate(mood.date)}.{" "}
              {vix.range && (
                <>
                  Higher than {plain(vix.range.percentile, 0)}% of days in a
                  year.{" "}
                </>
              )}
              {vix.vs_1w_ago && (
                <>
                  1W {vix.vs_1w_ago.direction} (
                  {signed(vix.vs_1w_ago.change_pct, 1)}%).{" "}
                </>
              )}
              {vix.vs_1m_ago && <>1M {vix.vs_1m_ago.direction}.</>}
            </p>
          </div>
        )}
        {pe && pulse.valuation && (
          <div className="panel mk-panel">
            <p className="eyebrow">NIFTY 50 VALUATION</p>
            <h3>
              P/E {plain(pe.current)}{" "}
              <small className="mk-muted">
                · cheaper than {plain(100 - pe.percentile, 0)}% of days
              </small>
            </h3>
            <RangeMeter
              min={pe.min}
              max={pe.max}
              value={pe.current}
              median={pe.median}
              minLabel={`${plain(pe.min)} min`}
              maxLabel={`${plain(pe.max)} max`}
              label={`P/E percentile ${pe.percentile}`}
            />
            <p className="mk-muted">
              Median {plain(pe.median)} · P/B{" "}
              {plain(pulse.valuation.pb?.current)} · yield{" "}
              {plain(pulse.valuation.div_yield_pct?.current)}% · window{" "}
              {displayDate(pulse.valuation.window_from)} –{" "}
              {displayDate(pulse.valuation.window_to)}
            </p>
          </div>
        )}
      </section>

      <section className="mk-two">
        {pulse.sector_movers && (
          <div className="panel mk-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">SECTOR ROTATION</p>
                <h2>Index movers</h2>
              </div>
              <div
                className="filter-group"
                role="group"
                aria-label="Sector period"
              >
                {PERIODS.map((p) => (
                  <button
                    key={p}
                    className={p === period ? "active" : ""}
                    onClick={() => onPeriod(p)}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
            <ul className="mk-bars">
              {[
                ...pulse.sector_movers.gainers,
                ...[...pulse.sector_movers.losers].reverse(),
              ].map((mover) => (
                <li key={mover.index}>
                  <span className="mk-bar-name">{mover.index}</span>
                  <span className="mk-bar-track">
                    <span
                      className={
                        mover.return_pct >= 0 ? "mk-bar-up" : "mk-bar-down"
                      }
                      style={{
                        width: `${(Math.abs(mover.return_pct) / sectorMax) * 50}%`,
                      }}
                    />
                  </span>
                  <span className={`mk-bar-value ${tone(mover.return_pct)}`}>
                    {signed(mover.return_pct)}%
                  </span>
                </li>
              ))}
            </ul>
            <p className="mk-muted">
              {pulse.sector_movers.base_date
                ? `${displayDate(pulse.sector_movers.base_date)} → ${displayDate(pulse.sector_movers.date)}`
                : `Session ${displayDate(pulse.sector_movers.date)}`}
            </p>
          </div>
        )}
        {pulse.stock_movers && (
          <div className="panel mk-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">ALL NSE SECURITIES</p>
                <h2>Top movers</h2>
              </div>
            </div>
            <div className="mk-movers">
              {(["gainers", "losers"] as const).map((side) => (
                <ul
                  key={side}
                  aria-label={side === "gainers" ? "Top gainers" : "Top losers"}
                >
                  <li className="mk-movers-head">
                    {side === "gainers" ? "Gainers" : "Losers"}
                  </li>
                  {pulse.stock_movers![side].map((mover) => (
                    <li key={mover.symbol}>
                      <button
                        className="text-link"
                        onClick={() => openSymbol(mover.symbol)}
                      >
                        {mover.symbol}
                      </button>
                      <span>{mover.ltp !== null ? money(mover.ltp) : "—"}</span>
                      <span className={tone(mover.change_pct)}>
                        {signed(mover.change_pct)}%
                      </span>
                    </li>
                  ))}
                </ul>
              ))}
            </div>
            <p className="mk-muted">
              Circuit-limited small caps often top this list; check liquidity
              before reading into it.
            </p>
          </div>
        )}
      </section>
    </>
  );
}

function HoldingsAtMarket({ live }: { live: LivePortfolio }) {
  const priced = live.holdings.filter((h) => h.status === "PRICED");
  if (!priced.length) return null;
  return (
    <section className="section-block">
      <div className="section-heading">
        <div>
          <p className="eyebrow">YOUR POSITIONS ON NSE</p>
          <h2>Holdings vs the market</h2>
        </div>
        <span className="section-aside">
          Portfolio beta {live.totals.beta ?? "—"} vs Nifty 50
          {live.totals.beta_coverage_pct
            ? ` · ${Number(live.totals.beta_coverage_pct).toFixed(0)}% of value covered`
            : ""}
        </span>
      </div>
      <div className="table-card">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Company</th>
                <th>Price</th>
                <th>Today</th>
                <th>From 52W high</th>
                <th>1Y return</th>
                <th>vs Nifty 50</th>
                <th>Beta</th>
                <th>Weight</th>
              </tr>
            </thead>
            <tbody>
              {priced.map((h) => (
                <tr key={h.symbol}>
                  <td>
                    <button
                      className="text-link"
                      onClick={() => openSymbol(h.symbol)}
                    >
                      <strong>{h.symbol}</strong>
                    </button>
                  </td>
                  <td>{h.quote ? money(h.quote.price) : "—"}</td>
                  <td className={tone(h.quote?.change_pct)}>
                    {signed(h.quote?.change_pct)}%
                  </td>
                  <td className={tone(h.from_52w_high_pct)}>
                    {h.from_52w_high_pct
                      ? `${signed(h.from_52w_high_pct)}%`
                      : "—"}
                  </td>
                  <td className={tone(h.relative?.stock_return_pct)}>
                    {h.relative
                      ? `${signed(h.relative.stock_return_pct)}%`
                      : "—"}
                  </td>
                  <td className={tone(h.relative?.outperformance_pct_points)}>
                    {h.relative
                      ? `${signed(h.relative.outperformance_pct_points)} pts`
                      : "—"}
                  </td>
                  <td>{plain(h.relative?.beta)}</td>
                  <td>
                    {h.weight_pct ? `${Number(h.weight_pct).toFixed(1)}%` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

export function MarketsSection({
  companies,
  symbol,
  live,
}: {
  companies: Company[];
  symbol: string | null;
  live: LivePortfolio | null;
}) {
  const [pulse, setPulse] = useState<MarketPulse | null>(null);
  const [period, setPeriod] = useState("1D");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const registered = useMemo(
    () => new Set(companies.map((c) => c.symbol.toUpperCase())),
    [companies],
  );

  const load = useCallback(async (p: string) => {
    setLoading(true);
    try {
      setPulse(await nseApi.pulse(p));
      setError("");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "NSE market data unavailable.",
      );
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load(period);
  }, [load, period]);
  useEffect(() => {
    if (pulse?.session.state !== "OPEN") return; // keeps polling on a delayed feed
    const timer = window.setInterval(() => void load(period), 60_000);
    return () => window.clearInterval(timer);
  }, [pulse?.session.state, load, period]);
  useEffect(() => {
    if (symbol)
      document
        .getElementById("mk-research")
        ?.scrollIntoView({ behavior: "smooth" });
  }, [symbol]);

  return (
    <section id="markets" className="mk-section">
      <div className="hero">
        <div>
          <p className="eyebrow">
            NSE MARKET DESK <span className="eyebrow-line" />
          </p>
          <h1>
            Markets,
            <br />
            <em>from the exchange.</em>
          </h1>
          <p className="hero-copy">
            Live indices, breadth, valuation and any listed stock — read
            directly from NSE's official MCP servers.
          </p>
        </div>
        <div className="hero-actions">
          {pulse && (
            <SessionBadge session={pulse.session} asOf={pulse.indices.as_of} />
          )}
          <button
            className="button button-outline"
            onClick={() => void load(period)}
            disabled={loading}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div className="load-error" role="alert">
          <span>{error}</span>
          <button
            className="button button-outline"
            onClick={() => void load(period)}
          >
            Try again
          </button>
        </div>
      )}
      {!pulse && loading && (
        <div className="loading-state">Connecting to NSE…</div>
      )}
      {pulse && <Pulse pulse={pulse} period={period} onPeriod={setPeriod} />}
      {pulse && pulse.errors.length > 0 && (
        <p className="mk-muted mk-gaps">
          Unavailable right now: {pulse.errors.map((e) => e.source).join(", ")}.
        </p>
      )}

      <section id="mk-research" className="section-block">
        <div className="section-heading">
          <div>
            <p className="eyebrow">STOCK RESEARCH</p>
            <h2>{symbol ? `${symbol} on NSE` : "Look up a stock"}</h2>
          </div>
          {symbol && (
            <button
              className="button button-quiet button-small"
              onClick={() => (window.location.hash = "markets")}
            >
              Clear
            </button>
          )}
        </div>
        <SymbolSearch onPick={openSymbol} />
        {symbol && (
          <StockCard
            key={symbol}
            symbol={symbol}
            registered={registered.has(symbol)}
          />
        )}
      </section>

      {live && <HoldingsAtMarket live={live} />}

      <p className="mk-disclaimer">{pulse?.disclaimer ?? live?.disclaimer}</p>
    </section>
  );
}
