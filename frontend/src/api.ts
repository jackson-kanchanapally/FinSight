export type Company = {
  id: number;
  symbol: string;
  name: string;
  exchange: string;
  bse_code: string | null;
  isin: string | null;
  sector: string | null;
  created_at: string;
};

export type Holding = {
  symbol: string;
  name: string;
  quantity: string;
  average_purchase_price: string;
  cost_basis: string;
  realised_pnl: string;
};

export type Portfolio = {
  holdings: Holding[];
  realised_pnl: string;
};

export type Transaction = {
  id: number;
  company_id: number;
  symbol: string;
  transaction_type: "BUY" | "SELL";
  quantity: string;
  price: string;
  transaction_date: string;
  charges: string;
  created_at: string;
};

export type CompanyInput = {
  symbol: string;
  name: string;
  exchange: string;
  bse_code?: string | null;
  isin?: string | null;
  sector?: string | null;
};

export type TransactionInput = {
  symbol: string;
  transaction_type: "BUY" | "SELL";
  quantity: string;
  price: string;
  transaction_date: string;
  charges: string;
};

export type EventDocument = {
  id: number;
  source: string;
  source_url: string;
  document_url: string;
  mime_type: string | null;
  file_name: string | null;
  sha256_hash: string | null;
  published_at: string | null;
  downloaded_at: string | null;
  processing_status: string;
  document_classification: string;
  page_count: number;
  download_url: string | null;
};

export type EventReference = {
  source: string;
  external_id: string | null;
  source_url: string;
  document_url: string | null;
  discovered_at: string;
  data_origin: "LIVE" | "FIXTURE" | "MANUAL";
};

export type CompanyEvent = {
  id: number;
  company_id: number;
  symbol: string;
  company_name: string;
  event_type: string;
  title: string;
  description: string | null;
  published_at: string;
  source: string;
  external_id: string | null;
  source_url: string;
  document_url: string | null;
  importance: string;
  processing_status: string;
  data_origin: "LIVE" | "FIXTURE" | "MANUAL";
  source_verified: boolean;
  publication_precision: "TIMESTAMP" | "DATE";
  period_end_date: string | null;
  fiscal_year: string | null;
  quarter: number | null;
  reporting_basis: "STANDALONE" | "CONSOLIDATED" | "BOTH" | null;
  created_at: string;
  references: EventReference[];
  documents: EventDocument[];
};

export type EventCheckSummary = {
  sources_checked: number;
  events_found: number;
  company_matches: number;
  portfolio_matches: number;
  new_events: number;
  documents_downloaded: number;
  duplicates: number;
  errors: string[];
  sources: Record<
    string,
    {
      status: string;
      received: number;
      company_matches: number;
      portfolio_matches: number;
      new_events: number;
      duplicates: number;
      documents_downloaded: number;
      unresolved_symbols?: string[];
      error: string | null;
    }
  >;
};

export type FinancialFact = {
  id: number;
  metric_code: string;
  value: string;
  unit: string;
  original_value: string | null;
  original_unit: string | null;
  source_document_id: number;
  source_page: number | null;
  source_label: string;
  source_column_date: string;
  extraction_method: string;
  status: string;
  document_url: string;
};

export type FinancialMetric = {
  metric_code: string;
  value: string;
  unit: string;
  input_fact_ids: number[];
  comparison_period_id: number | null;
  formula: string;
  inputs: FinancialFact[];
};

export type FinancialPeriod = {
  id: number;
  company_id: number;
  fiscal_year: string;
  quarter: number | null;
  period_start: string;
  period_end: string;
  period_type: "QUARTERLY" | "HALF_YEARLY" | "NINE_MONTH" | "ANNUAL";
  scope: "STANDALONE" | "CONSOLIDATED";
  status: "PENDING" | "EXTRACTED" | "VALIDATED" | "NEEDS_REVIEW" | "FAILED";
  status_note: string | null;
  source_event_id: number;
  source_document_id: number;
  source_title: string;
  source_name: string;
  source_url: string;
  document_url: string;
  published_at: string;
  facts: FinancialFact[];
  metrics: FinancialMetric[];
};

export type CompanyFinancials = {
  symbol: string;
  company_name: string;
  periods: FinancialPeriod[];
};

export type MarketSnapshotInput = {
  close_price: string;
  pe_ratio: string | null;
  market_cap_crore: string | null;
  forward_eps_growth_pct: string | null;
  growth_source_url: string | null;
  growth_source_type?: string | null;
  shares_outstanding?: string | null;
  note?: string | null;
  as_of_date: string;
  source_url: string;
};
export type MarketSnapshot = MarketSnapshotInput & {
  id: number;
  company_id: number;
  updated_at: string;
  peg_ratio: string | null;
  provider: string;
  data_origin: string;
  observed_at: string;
  fetched_at: string;
  shares_outstanding: string | null;
  open_price: string | null;
  high_price: string | null;
  low_price: string | null;
  volume: number | null;
};

export type ValuationDetail = {
  value: string | null;
  status: string;
  inputs: string[];
  formula: string;
  reason: string | null;
};
export type Valuation = {
  id: number;
  market_snapshot_id: number;
  market_date: string;
  market: MarketSnapshot;
  scope: string;
  calculation_status: string;
  metric_details: Record<string, ValuationDetail | Record<string, unknown>>;
  ttm_revenue: string | null;
  ttm_ebitda: string | null;
  ttm_pat: string | null;
  ttm_eps: string | null;
  pe_ttm: string | null;
  ps_ttm: string | null;
  ev_ebitda_ttm: string | null;
  earnings_yield_pct: string | null;
  peg_forward: string | null;
  updated_at: string;
};
export type ScenarioResult = {
  name: string;
  eps_growth_pct: string;
  terminal_pe: string;
  years: number;
  current_eps: string;
  current_price: string;
  future_eps: string;
  implied_future_price: string;
  total_return_pct: string;
  annualized_return_pct: string;
  label: string;
};
export type ReverseResult = {
  current_eps: string;
  current_price: string;
  terminal_pe: string;
  years: number;
  target_annualized_return_pct: string;
  target_future_price: string;
  required_future_eps: string;
  required_eps_cagr_pct: string;
  label: string;
  observed_eps_growth: {
    latest_eps_growth_pct: string;
    latest_period_end: string;
    previous_period_end: string;
    input_evidence_ids: string[];
  } | null;
  growth_gap: {
    relationship: string;
    absolute_difference: string | null;
  } | null;
};
export type ValuationChange = {
  status: "CALCULATED";
  old_date: string;
  new_date: string;
  old_price: string;
  new_price: string;
  old_eps: string;
  new_eps: string;
  old_pe: string;
  new_pe: string;
  price_change_pct: string;
  eps_change_pct: string;
  pe_change_pct: string;
  method: string;
};
export type ValuationChangeUnavailable = {
  status: "INSUFFICIENT_DATA";
  reason: string;
  old_date: string;
  new_date: string;
  missing_inputs: string[];
};
export type ReferenceComparison = {
  metric_code: string;
  almonds: string | null;
  market_lens: string;
  difference_pct: string | null;
  status: string;
  reference_date: string;
  source_url: string;
};
export type InvestorScenario = {
  name: "CONSERVATIVE" | "BASE" | "OPTIMISTIC";
  eps_growth_pct: string;
  terminal_pe: string;
  years: number;
  required_return_pct: string;
  source: string;
  source_type?: string;
  evidence_id?: string;
  implied_current_pe?: string;
};
export type TestedPERange = {
  status: string;
  verdict: string;
  confidence: string;
  current_pe?: string | null;
  current_price?: string | null;
  ttm_eps?: string | null;
  price_source?: { provider: string; url: string; origin: string } | null;
  observed_eps_growth?: {
    latest_eps_growth_pct: string;
    latest_period_end: string;
    previous_period_end: string;
    input_evidence_ids: string[];
  } | null;
  supported_pe_range?: { lower: string; central: string; upper: string } | null;
  scenarios?: InvestorScenario[];
  reverse_expectations?: {
    required_eps_cagr_pct: string;
    required_future_eps: string;
    target_future_price: string;
  } | null;
  expectations_gap?: {
    classification: string;
    reason: string;
    dispersion_pct?: string;
    dispersion_source?: string;
  };
  growth_comparisons?: {
    metric: string;
    value: string;
    source_type: string;
    evidence_ids: string[];
  }[];
  label?: string;
  quality_adjustment?: string;
  current_position?: string;
  methodology?: string;
  assumptions?: Record<string, string | number>;
  calculation?: Record<string, string>;
  missing: string[];
  market_date: string | null;
  input_evidence: string[];
  lenses: Record<string, string>;
  price_age_days?: number | null;
  scenario_verdict?: string;
  historical_pe?: {
    one_year: {
      median: string;
      low: string;
      high: string;
      percentile: string | null;
    } | null;
    three_year: {
      median: string;
      low: string;
      high: string;
      percentile: string | null;
    } | null;
    one_year_coverage: {
      count: number;
      required_count: number;
      span_days: number;
      required_span_days: number;
      first_date: string | null;
      last_date: string | null;
    };
    three_year_coverage: {
      count: number;
      required_count: number;
      span_days: number;
      required_span_days: number;
      first_date: string | null;
      last_date: string | null;
    };
    available_period: {
      median: string;
      low: string;
      high: string;
      count: number;
      first_date: string;
      last_date: string;
    } | null;
    first_stored_price_date: string | null;
    methodology: string;
    available_points: number;
  };
};
export type InvestorReport = {
  investor_stance?: string;
  investor_conclusion?: AIQualityNote | null;
  tested_pe?: TestedPERange;
  earnings_quality?: {
    text: string;
    basis: string | null;
    confidence: string;
    evidence_ids: string[];
    pat_bridge: {
      component: string;
      contribution_crore: string;
      evidence_ids: string[];
    }[];
    cash_conversion: {
      label: string;
      value: string;
      evidence_ids: string[];
      formula: string;
    }[];
  };
  research_gaps?: string[];
  strengthen_case?: string[];
  weaken_case?: string[];
  results: {
    verdict: string;
    confidence: string;
    reasons: string[];
    evidence_ids: string[];
    signals: Record<string, string>;
    methodology: string;
  };
  valuation: { verdict: string; confidence: string; reason: string };
  source_documents: {
    document_id: number;
    event_id: number;
    document_type: string;
    source: string;
    published_at: string;
    source_url: string | null;
    document_url: string;
    source_priority: number;
  }[];
  valuation_support: AIQualityNote[];
  valuation_challenges: AIQualityNote[];
  management_statements: {
    evidence_id: string;
    statement_type: string;
    topic: string;
    raw_statement: string;
    speaker_name: string | null;
    speaker_role: string | null;
    published_at: string;
    document_id: number;
    page_number: number;
    document_url: string;
    extraction_confidence: string;
  }[];
  management_guidance: {
    id: number;
    statement_evidence_id: string;
    metric: string;
    target_period: string;
    lower_bound: string;
    upper_bound: string;
    unit: string;
    change: string;
    actual_comparison: string;
    execution_outcome: string;
  }[];
  guidance_changes: {
    status: string;
    statement_evidence_id: string;
    topic: string;
    published_at: string;
  }[];
  claim_comparisons: {
    statement_evidence_id: string;
    status: string;
    financial_evidence_ids: string[];
    note: string;
  }[];
};

export type AIClaim = {
  title: string;
  explanation: string;
  evidence_ids: string[];
};
export type AIMaterialChange = {
  metric: string;
  change: string;
  interpretation: string;
  evidence_ids: string[];
};
export type AIQualityNote = { text: string; evidence_ids: string[] };
export type AIResearchGap = {
  type: string;
  question: string;
  priority: "HIGH" | "MEDIUM" | "LOW";
  evidence_ids: string[];
};
export type AIQualityReport = {
  operating_performance: AIQualityNote;
  seasonality_context: AIQualityNote & { status: string };
  earnings_quality: AIQualityNote;
  valuation_expectations: AIQualityNote;
  results_vs_valuation: AIQualityNote & { assessment: string };
  strengthening_evidence: AIQualityNote[];
  weakening_evidence: AIQualityNote[];
  research_gaps: AIResearchGap[];
  research_priority: "HIGH" | "MEDIUM" | "LOW";
  confidence: "HIGH" | "MEDIUM" | "LOW";
};
export type EventInsightClaim = { text: string; evidence_ids: string[] };
export type EventInsight = {
  summary: EventInsightClaim;
  why_it_matters: EventInsightClaim;
  recommendation: {
    action: "WATCH" | "INVESTIGATE" | "NO_ACTION" | "INSUFFICIENT_EVIDENCE";
    reason: string;
    evidence_ids: string[];
  };
  what_next: EventInsightClaim;
  management_says: EventInsightClaim | null;
};
export type EventAnalysis = {
  id: number;
  event_id: number;
  status: "PENDING" | "SUCCESS" | "FAILED" | "INVALID";
  failure_reason: string | null;
  evidence_registry: Record<
    string,
    {
      kind: string;
      text: string;
      source_url?: string;
      document_url?: string;
      document_id?: number;
      page?: number;
    }
  >;
  structured_output: EventInsight | null;
};
export type AIAnalysis = {
  id: number;
  company_id: number;
  financial_period_id: number;
  analysis_type: string;
  model_provider: string;
  model_name: string;
  prompt_version: string;
  current_prompt_version: string;
  created_at: string;
  status: "PENDING" | "SUCCESS" | "VERIFIED_FALLBACK" | "FAILED" | "INVALID";
  failure_reason: string | null;
  validation_diagnostics?:
    | {
        code: string;
        field: string | null;
        reason: string;
        model_value: string | null;
        expected: unknown;
      }[]
    | null;
  evidence_registry: Record<
    string,
    {
      kind: string;
      metric_code?: string;
      value?: string;
      unit?: string;
      document_id?: number;
      page?: number | null;
      document_url?: string;
      period_end?: string;
      source_label?: string;
      source_url?: string;
      as_of_date?: string;
      price?: string;
      pe_ratio?: string | null;
      market_cap_crore?: string | null;
      forward_eps_growth_pct?: string | null;
      growth_source_url?: string | null;
      peg_ratio?: string | null;
      input_evidence_ids?: string[];
      formula?: string;
      relationship?: string;
      status?: string;
      role?: string;
      reference_status?: string;
      primary_value?: string;
      reference_value?: string;
      percentage_difference?: string;
    }
  >;
  structured_output: {
    summary: string;
    summary_evidence_ids: string[];
    positives: AIClaim[];
    concerns: AIClaim[];
    material_changes: AIMaterialChange[];
    questions: string[];
    data_quality_notes: string[];
    management_commentary?: { text: string; evidence_ids: string[] } | null;
    what_might_happen?: { text: string; evidence_ids: string[] } | null;
    result_view?: {
      sentiment: "POSITIVE" | "NEGATIVE" | "NEUTRAL";
      reason: string;
      evidence_ids: string[];
    } | null;
    valuation_assessment?: {
      status: "ASSESSED" | "INSUFFICIENT_DATA";
      verdict: "SUPPORTIVE" | "NOT_SUPPORTIVE" | "INCONCLUSIVE";
      text: string;
      evidence_ids: string[];
    } | null;
    research_action?: {
      action: "WATCH" | "INVESTIGATE" | "NO_ACTION" | "INSUFFICIENT_EVIDENCE";
      reason: string;
      evidence_ids: string[];
    } | null;
    quality_report?: AIQualityReport | null;
    investor_report?: InvestorReport | null;
  } | null;
};

function describeError(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item?.msg === "string") {
          const field = Array.isArray(item.loc)
            ? item.loc.slice(1).join(" → ")
            : "";
          return field ? `${field}: ${item.msg}` : item.msg;
        }
        return "Invalid request";
      })
      .join("; ");
  }
  return "The request could not be completed.";
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...options?.headers },
    });
  } catch {
    throw new Error(
      "Could not reach the API. Check that FastAPI is running on port 8000.",
    );
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    throw new Error(describeError(body?.detail));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  companies: () => request<Company[]>("/api/companies"),
  transactions: () => request<Transaction[]>("/api/transactions"),
  portfolio: () => request<Portfolio>("/api/portfolio"),
  events: () => request<CompanyEvent[]>("/api/events?limit=100"),
  event: (id: number) => request<CompanyEvent>(`/api/events/${id}`),
  uploadCompanyPdf: (
    symbol: string,
    file: File,
    details: {
      title: string;
      published_on: string;
      event_type: string;
      source_url: string;
    },
  ) => {
    const params = new URLSearchParams({
      title: details.title,
      published_on: details.published_on,
      event_type: details.event_type,
      file_name: file.name,
    });
    if (details.source_url.trim())
      params.set("source_url", details.source_url.trim());
    return request<CompanyEvent>(
      `/api/companies/${encodeURIComponent(symbol)}/events/upload-pdf?${params}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/pdf" },
        body: file,
      },
    );
  },
  analyzeEvent: (id: number, regenerate = false) =>
    request<EventAnalysis>(
      `/api/events/${id}/analyze${regenerate ? "?regenerate=true" : ""}`,
      { method: "POST" },
    ),
  checkEvents: () =>
    request<EventCheckSummary>("/api/events/check", { method: "POST" }),
  companyFinancials: (symbol: string) =>
    request<CompanyFinancials>(
      `/api/companies/${encodeURIComponent(symbol)}/financials`,
    ),
  marketSnapshot: (symbol: string) =>
    request<MarketSnapshot | null>(
      `/api/companies/${encodeURIComponent(symbol)}/market-snapshot`,
    ),
  saveMarketSnapshot: (symbol: string, value: MarketSnapshotInput) =>
    request<MarketSnapshot>(
      `/api/companies/${encodeURIComponent(symbol)}/market-snapshot`,
      { method: "PUT", body: JSON.stringify(value) },
    ),
  market: (symbol: string) =>
    request<{
      latest: MarketSnapshot | null;
      provider: string;
      refresh_available: boolean;
    }>(`/api/companies/${encodeURIComponent(symbol)}/market`),
  refreshMarket: (symbol: string) =>
    request<{ stored: number; latest: MarketSnapshot }>(
      `/api/companies/${encodeURIComponent(symbol)}/market/refresh`,
      { method: "POST" },
    ),
  marketHistory: (symbol: string) =>
    request<MarketSnapshot[]>(
      `/api/companies/${encodeURIComponent(symbol)}/market/history?limit=365`,
    ),
  valuation: (symbol: string, scope: string) =>
    request<Valuation | null>(
      `/api/companies/${encodeURIComponent(symbol)}/valuation?scope=${scope}`,
    ),
  automaticPe: (symbol: string, scope: string) =>
    request<TestedPERange>(
      `/api/companies/${encodeURIComponent(symbol)}/valuation/tested-pe?scope=${encodeURIComponent(scope)}`,
    ),
  testedPe: (
    symbol: string,
    payload: {
      scope: string;
      eps_growth_pct?: string | null;
      terminal_pe_low?: string | null;
      terminal_pe_high?: string | null;
      years?: number | null;
      required_return_pct?: string | null;
      assumption_source?: string | null;
      assumption_date?: string | null;
      scenarios?: InvestorScenario[];
      near_range_pct?: string;
      extreme_gap_pct?: string;
    },
  ) =>
    request<TestedPERange>(
      `/api/companies/${encodeURIComponent(symbol)}/valuation/tested-pe`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  valuationHistory: (symbol: string, scope: string) =>
    request<Valuation[]>(
      `/api/companies/${encodeURIComponent(symbol)}/valuation/history?scope=${scope}&limit=100`,
    ),
  scenarios: (
    symbol: string,
    scope: string,
    assumptions: {
      name: string;
      eps_growth_pct: string;
      terminal_pe: string;
      years: number;
    }[],
  ) =>
    request<ScenarioResult[]>(
      `/api/companies/${encodeURIComponent(symbol)}/valuation/scenario`,
      { method: "POST", body: JSON.stringify({ scope, assumptions }) },
    ),
  reverseGrowth: (
    symbol: string,
    scope: string,
    terminal_pe: string,
    years: number,
    target_annualized_return_pct: string,
  ) =>
    request<ReverseResult>(
      `/api/companies/${encodeURIComponent(symbol)}/valuation/reverse`,
      {
        method: "POST",
        body: JSON.stringify({
          scope,
          terminal_pe,
          years,
          target_annualized_return_pct,
        }),
      },
    ),
  valuationChange: (
    symbol: string,
    oldId: number,
    newId: number,
    scope: string,
  ) =>
    request<ValuationChange | ValuationChangeUnavailable>(
      `/api/companies/${encodeURIComponent(symbol)}/valuation/change?old_market_snapshot_id=${oldId}&new_market_snapshot_id=${newId}&scope=${scope}`,
    ),
  valuationReference: (symbol: string, scope: string) =>
    request<ReferenceComparison[]>(
      `/api/companies/${encodeURIComponent(symbol)}/valuation/reference?scope=${scope}`,
    ),
  companyAnalyses: (symbol: string) =>
    request<AIAnalysis[]>(
      `/api/companies/${encodeURIComponent(symbol)}/analysis`,
    ),
  analyzeFinancials: (
    symbol: string,
    periodId: number,
    regenerate = false,
    deterministicOnly = false,
  ) =>
    request<AIAnalysis>(
      `/api/companies/${encodeURIComponent(symbol)}/financials/${periodId}/analyze?regenerate=${regenerate}&deterministic_only=${deterministicOnly}`,
      { method: "POST" },
    ),
  processFinancials: (eventId: number, confirmResults = false) =>
    request<{ status: string; periods_created: number }>(
      `/api/financials/process/${eventId}${confirmResults ? "?confirm_results=true" : ""}`,
      { method: "POST" },
    ),
  financialProcessingStatus: (eventId: number) =>
    request<{
      status: "VALIDATED" | "NEEDS_REVIEW" | "FAILED";
      note: string | null;
    }>(`/api/financials/process/${eventId}`),
  createCompany: (value: CompanyInput) =>
    request<Company>("/api/companies", {
      method: "POST",
      body: JSON.stringify(value),
    }),
  deleteCompany: (symbol: string) =>
    request<void>(`/api/companies/${encodeURIComponent(symbol)}`, {
      method: "DELETE",
    }),
  createTransaction: (value: TransactionInput) =>
    request<Transaction>("/api/transactions", {
      method: "POST",
      body: JSON.stringify(value),
    }),
};
