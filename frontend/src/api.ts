export type Company = {
  id: number;
  symbol: string;
  name: string;
  exchange: string;
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
  sources: Record<string, {
    status: string;
    received: number;
    company_matches: number;
    portfolio_matches: number;
    new_events: number;
    duplicates: number;
    documents_downloaded: number;
    unresolved_symbols?: string[];
    error: string | null;
  }>;
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
  as_of_date: string;
  source_url: string;
};
export type MarketSnapshot = MarketSnapshotInput & {
  id: number;
  company_id: number;
  updated_at: string;
};

export type AIClaim = {title: string; explanation: string; evidence_ids: string[]};
export type AIMaterialChange = {metric: string; change: string; interpretation: string; evidence_ids: string[]};
export type EventInsightClaim = {text: string; evidence_ids: string[]};
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
  evidence_registry: Record<string, {
    kind: string; text: string; source_url?: string; document_url?: string;
    document_id?: number; page?: number;
  }>;
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
  created_at: string;
  status: "PENDING" | "SUCCESS" | "FAILED" | "INVALID";
  failure_reason: string | null;
  evidence_registry: Record<string, {
    kind: string; metric_code?: string; value?: string; unit?: string;
    document_id?: number; page?: number | null; document_url?: string;
    period_end?: string; source_label?: string; source_url?: string;
    as_of_date?: string; price?: string; pe_ratio?: string | null;
  }>;
  structured_output: {
    summary: string;
    summary_evidence_ids: string[];
    positives: AIClaim[];
    concerns: AIClaim[];
    material_changes: AIMaterialChange[];
    questions: string[];
    data_quality_notes: string[];
    management_commentary?: {text: string; evidence_ids: string[]} | null;
    what_might_happen?: {text: string; evidence_ids: string[]} | null;
    valuation_assessment?: {status: "ASSESSED" | "INSUFFICIENT_DATA"; text: string; evidence_ids: string[]} | null;
    research_action?: {action: "WATCH" | "INVESTIGATE" | "NO_ACTION" | "INSUFFICIENT_EVIDENCE";
      reason: string; evidence_ids: string[]} | null;
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
  analyzeEvent: (id: number, regenerate = false) =>
    request<EventAnalysis>(`/api/events/${id}/analyze${regenerate ? "?regenerate=true" : ""}`, {method: "POST"}),
  checkEvents: () =>
    request<EventCheckSummary>("/api/events/check", { method: "POST" }),
  companyFinancials: (symbol: string) =>
    request<CompanyFinancials>(`/api/companies/${encodeURIComponent(symbol)}/financials`),
  marketSnapshot: (symbol: string) =>
    request<MarketSnapshot | null>(`/api/companies/${encodeURIComponent(symbol)}/market-snapshot`),
  saveMarketSnapshot: (symbol: string, value: MarketSnapshotInput) =>
    request<MarketSnapshot>(`/api/companies/${encodeURIComponent(symbol)}/market-snapshot`,
      {method: "PUT", body: JSON.stringify(value)}),
  companyAnalyses: (symbol: string) =>
    request<AIAnalysis[]>(`/api/companies/${encodeURIComponent(symbol)}/analysis`),
  analyzeFinancials: (symbol: string, periodId: number, regenerate = false) =>
    request<AIAnalysis>(`/api/companies/${encodeURIComponent(symbol)}/financials/${periodId}/analyze${regenerate ? "?regenerate=true" : ""}`, {method: "POST"}),
  processFinancials: (eventId: number) =>
    request<{status: string; periods_created: number}>(`/api/financials/process/${eventId}`, {method: "POST"}),
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
