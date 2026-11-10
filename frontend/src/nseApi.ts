import { useCallback, useEffect, useState } from "react";
import { request } from "./api";

/** Data from NSE's official MCP servers, proxied by the Almonds API. */

export type NseError = {
  source: string;
  reason: string;
  detail: string | null;
};
export type Session = {
  state: "OPEN" | "CLOSED";
  checked_at: string;
  data_date: string | null;
  stale: boolean;
  note: string;
};

export type NseQuote = {
  source: "LIVE" | "EOD";
  price: string;
  previous_close: string | null;
  change_pct: string | null;
  open: string | null;
  high: string | null;
  low: string | null;
  volume: number | null;
  value_cr: number | null;
  week52_high: string | null;
  week52_low: string | null;
  change_30d_pct: string | null;
  series: string | null;
  as_of: string | null;
};

export type Relative = {
  benchmark: string;
  base_date: string | null;
  date: string | null;
  stock_return_pct: number | null;
  index_return_pct: number | null;
  outperformance_pct_points: number | null;
  beta: number | null;
  correlation: number | null;
  method?: string;
};

export type LiveHolding = {
  symbol: string;
  name: string;
  quantity: string;
  cost_basis: string;
  average_purchase_price: string;
  quote: NseQuote | null;
  market_value: string | null;
  unrealised_pnl: string | null;
  unrealised_pnl_pct: string | null;
  day_change_value: string | null;
  from_52w_high_pct: string | null;
  weight_pct: string | null;
  relative: Relative | null;
  status: "PRICED" | "UNPRICED" | "NOT_NSE";
};

export type LivePortfolio = {
  session: Session;
  holdings: LiveHolding[];
  totals: {
    market_value: string | null;
    cost_basis_priced: string | null;
    unrealised_pnl: string | null;
    unrealised_pnl_pct: string | null;
    day_change_value: string | null;
    day_change_pct: string | null;
    beta: string | null;
    beta_coverage_pct: string | null;
    priced_positions: number;
    open_positions: number;
  };
  errors: NseError[];
  disclaimer: string;
};

export type IndexRow = {
  name: string;
  group: string | null;
  last: number | null;
  previous_close: number | null;
  change: number | null;
  change_pct: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  as_of: string | null;
};

type RatioRange = {
  current: number;
  min: number;
  min_date: string;
  max: number;
  max_date: string;
  median: number;
  mean: number;
  percentile: number;
  observations: number;
};

export type Breadth = {
  up: number;
  down: number;
  unchanged: number;
  up_pct: number;
  reading: string;
};

export type Mover = {
  symbol: string;
  series: string | null;
  ltp: number | null;
  previous_close: number | null;
  change_pct: number | null;
  volume: string | null;
  turnover: string | null;
};

export type IndexMover = {
  index: string;
  close: number;
  return_pct: number;
  points_change?: number;
};

export type MarketPulse = {
  session: Session;
  indices: { as_of: string | null; items: IndexRow[]; available: number };
  mood: {
    date: string;
    benchmarks?: Record<string, { close: number; change_pct: number }>;
    index_breadth?: Breadth & { equity_indices: number };
    stock_breadth?: Breadth & { stocks: number };
    india_vix?: {
      level: number;
      change_1d_pct: number;
      vs_1w_ago?: { change_pct: number; direction: string };
      vs_1m_ago?: { change_pct: number; direction: string };
      range?: {
        min: number;
        max: number;
        percentile: number;
        window_from: string;
        window_to: string;
      };
    };
    method?: string;
  } | null;
  valuation: {
    index: string;
    date: string;
    close: number;
    pe?: RatioRange;
    pb?: RatioRange;
    div_yield_pct?: RatioRange;
    window_from: string;
    window_to: string;
    method?: string;
  } | null;
  sector_movers: {
    date: string;
    base_date: string | null;
    period: string;
    gainers: IndexMover[];
    losers: IndexMover[];
  } | null;
  stock_movers: {
    updated_at: string | null;
    gainers: Mover[];
    losers: Mover[];
  } | null;
  errors: NseError[];
  disclaimer: string;
};

export type CorporateAction = {
  exDate: string;
  actionType: string;
  purpose: string;
  adjustmentFactor?: number;
};

export type StockInsight = {
  symbol: string;
  session: Session;
  quote: NseQuote | null;
  range_52w: {
    high: number;
    high_date: string;
    low: number;
    low_date: string;
    position_pct: number;
    from_high_pct: number;
    from_low_pct: number;
    last_close: number;
  } | null;
  moving_averages: {
    days: number;
    value: string;
    price_vs_average_pct: string | null;
  }[];
  trend: string | null;
  volume: {
    days_analysed: number;
    avg_volume: number;
    last_volume: number;
    last_vs_avg_pct: number;
    volume_spike_days: number;
  } | null;
  relative: Relative | null;
  dividends: {
    trailing_12m_per_share: string;
    events: number;
    yield_pct: string | null;
    basis: string;
  } | null;
  corporate_actions: {
    upcoming: CorporateAction[];
    recent: CorporateAction[];
  } | null;
  history: {
    from_date: string;
    to_date: string;
    summary: {
      return_pct?: number;
      period_high?: number;
      period_low?: number;
    } | null;
    points: { date: string; close: number; volume: number }[];
  } | null;
  warnings: string[];
  errors: NseError[];
  disclaimer: string;
};

export type SymbolMatch = {
  symbol: string;
  last_close: number;
  pct_change: number;
  last_date: string;
  inactive: boolean;
};

export type SymbolSearch = {
  query: string;
  results: SymbolMatch[];
  exact_match: SymbolMatch | null;
};

export const nseApi = {
  pulse: (period = "1D") =>
    request<MarketPulse>(
      `/api/nse/market-pulse?sector_period=${encodeURIComponent(period)}`,
    ),
  portfolio: () => request<LivePortfolio>("/api/nse/portfolio"),
  stock: (symbol: string) =>
    request<StockInsight>(`/api/nse/stocks/${encodeURIComponent(symbol)}`),
  search: (q: string) =>
    request<SymbolSearch>(`/api/nse/search?q=${encodeURIComponent(q)}`),
};

/** Live mark-to-market for holdings; refreshes every minute while NSE is open. */
export function useLivePortfolio(dependency: unknown) {
  const [data, setData] = useState<LivePortfolio | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setData(await nseApi.portfolio());
      setError("");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "NSE prices unavailable.",
      );
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load, dependency]);
  const open = data?.session.state === "OPEN";
  useEffect(() => {
    if (!open) return;
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, [open, load]);
  return { data, error, reload: load };
}

export const signed = (
  value: number | string | null | undefined,
  digits = 2,
) => {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number > 0 ? "+" : number < 0 ? "−" : ""}${Math.abs(number).toFixed(digits)}`;
};

export const tone = (value: number | string | null | undefined) =>
  value === null || value === undefined || Number(value) === 0
    ? ""
    : Number(value) > 0
      ? "positive-value"
      : "negative-value";

export const plain = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined
    ? "—"
    : new Intl.NumberFormat("en-IN", {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      }).format(value);

export const compact = (value: number | null | undefined) =>
  value === null || value === undefined
    ? "—"
    : new Intl.NumberFormat("en-IN", {
        notation: "compact",
        maximumFractionDigits: 1,
      }).format(value);

export type ReactionWindow = {
  sessions: number;
  date: string;
  close: string;
  return_pct: string;
  index_return_pct: string | null;
  excess_return_pct: string | null;
};

export type PriceReaction = {
  event_id: number;
  status: "PENDING" | "COMPLETE" | "UNAVAILABLE";
  reason: string | null;
  timing: string | null;
  base_date: string | null;
  base_close: string | null;
  reaction_date: string | null;
  benchmark: string;
  windows: Record<string, ReactionWindow>;
  volume_ratio: string | null;
  adjustments: { ex_date: string; factor: string; purpose: string }[];
  computed_at: string;
  method: string;
  event?: {
    id: number;
    title: string;
    event_type: string;
    published_at: string;
  };
};

export type CompanyReactions = {
  symbol: string;
  benchmark: string;
  reactions: PriceReaction[];
  summary_by_event_type: Record<
    string,
    {
      count: number;
      windows: Record<
        string,
        {
          observations: number;
          median_excess_return_pct: string;
          outperformed: number;
        }
      >;
    }
  >;
  note: string;
};

export const reactionApi = {
  event: (eventId: number) =>
    request<PriceReaction | null>(`/api/events/${eventId}/price-reaction`),
  company: (symbol: string) =>
    request<CompanyReactions>(
      `/api/companies/${encodeURIComponent(symbol)}/price-reactions`,
    ),
  refresh: (symbol: string) =>
    request<CompanyReactions>(
      `/api/companies/${encodeURIComponent(symbol)}/price-reactions/refresh`,
      {
        method: "POST",
      },
    ),
};
