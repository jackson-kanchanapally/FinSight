import { request } from "./api";
import type { Evidence } from "./thesisApi";

export type AccuracyDefinition = {
  metric: string;
  financial_scope: string;
  period: string;
  period_type: string;
  expectation_type: string;
  expected_value: string | null;
  lower_value: string | null;
  upper_value: string | null;
  direction: string | null;
  unit: string;
  description: string;
  source_type: string;
  source_reference: string;
  historical_import: boolean;
  guidance_kind: string | null;
  lifecycle: string;
  confidence: string | null;
};
export type AccuracyEvaluation = {
  id: number;
  outcome: string;
  created_at: string;
  prospective_eligible: boolean;
  result: {
    actual_value: string | null;
    signed_error?: string | null;
    absolute_percentage_error?: string | null;
    normalized_unit?: string;
    reason: string;
    evidence: Evidence;
    withdrawn: boolean;
  };
};
export type AccuracyRow = {
  id: number;
  revision_id: number;
  version: number;
  created_at: string;
  cutoff_at: string;
  definition: AccuracyDefinition;
  status: string;
  evaluation: AccuracyEvaluation | null;
  provenance: {
    source_snapshot: {
      source_quote?: string;
      document_url?: string;
      published_at?: string;
      page?: number;
    } | null;
    claimed_issued_at: string | null;
    capture: string;
  };
};
export type AccuracyPage = {
  items: AccuracyRow[];
  total: number;
  offset: number;
  limit: number;
};
export type Calibration = {
  source: string;
  metric: string;
  scope: string;
  category: string;
  n: number;
  status: string;
  brier: string | null;
  warning: string;
  buckets: {
    lower: string;
    upper: string;
    predicted: string;
    observed: string;
    n: number;
    status: string;
  }[];
};
export type AccuracyStats = {
  total: number;
  evaluated: number;
  met: number;
  missed: number;
  partially_met: number;
  unresolved: number;
  evidence_coverage: { n: number; with_evidence: number; ratio: string | null };
  groups: {
    metric: string;
    scope: string;
    source: string;
    category: string;
    unit: string;
    n: number;
    met_rate: string | null;
    absolute_error: { n: number; median: string | null };
    periods: { period: string; n: number; met: number }[];
    rolling_last_20: { through_period: string; n: number; met: number }[];
  }[];
  calibration: Calibration[];
  policy: {
    minimum_n: number;
    sample: string;
    amount_met_pct: number;
    amount_partial_pct: number;
    percentage_met_pp: number;
  };
};
export type ThesisAccuracy = {
  available_theses: {
    id: number;
    name: string;
    version: number;
    created_at: string;
  }[];
  items: {
    thesis_revision_id: number;
    version: number;
    outcome: string;
    created_at: string;
    original_thesis: { name: string; description: string };
    expectations: AccuracyRow[];
    validated: number[];
    contradicted: number[];
    unresolved: number[];
    interpretation: string;
  }[];
  total: number;
  offset: number;
  limit: number;
};
const base = (symbol: string) => `/api/companies/${encodeURIComponent(symbol)}`;
export const accuracyApi = {
  list: (symbol: string, query: string) =>
    request<AccuracyPage>(`${base(symbol)}/expectations?${query}`),
  stats: (symbol: string, query: string) =>
    request<AccuracyStats>(`${base(symbol)}/accuracy?${query}`),
  guidance: (symbol: string, query: string) =>
    request<AccuracyPage>(`${base(symbol)}/accuracy/guidance?${query}`),
  thesis: (symbol: string, query: string) =>
    request<ThesisAccuracy>(`${base(symbol)}/accuracy/thesis-history?${query}`),
  revisions: (symbol: string, id: number) =>
    request<AccuracyRow[]>(`${base(symbol)}/expectations/${id}/revisions`),
  evaluate: (symbol: string) =>
    request(`${base(symbol)}/expectations/evaluate-pending`, {
      method: "POST",
    }),
  create: (symbol: string, body: Record<string, unknown>) =>
    request<AccuracyRow>(`${base(symbol)}/expectations`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
