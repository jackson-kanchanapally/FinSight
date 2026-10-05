import { request } from "./api";
export type Evidence = Record<
  string,
  {
    kind?: string;
    document_id?: number;
    page?: number;
    page_number?: number;
    document_url?: string;
    source_quote?: string;
    value?: string;
    metric_code?: string;
  }
>;
export type Expectation = {
  description: string;
  expectation_type: "CHECKPOINT" | "METRIC" | "QUALITATIVE";
  checkpoint_id: number | null;
  metric_code: string | null;
  operator: string | null;
  target_value: string | null;
  upper_value: string | null;
  unit: string | null;
  target_period_end: string | null;
  period_type: string | null;
  is_active: boolean;
  reason?: string;
  expected_version?: number;
  expectation_key?: string;
  version?: number;
  outcome?: {
    status: string;
    actual_value: string | null;
    evidence_ids: string[];
  } | null;
};
export type Pillar = {
  id?: number;
  version?: number;
  scope: string;
  name: string;
  category: string;
  description: string;
  importance: string;
  created_from: string;
  source_analysis_id: number | null;
  evidence_ids: string[];
  metric_codes: string[];
  is_active: boolean;
  reason?: string;
  expected_version?: number;
  expectations: Expectation[];
  status_evidence_ids?: string[];
  status_evidence?: Evidence;
  status?: string;
  status_reason?: string;
  source_evidence?: Evidence;
  source_components?: { name: string; description: string; created_from: string; evidence_ids: string[] }[];
  accepted_by_user?: boolean;
  contradiction?: boolean;
  research_gaps?: string[];
};
export type Gap = {
  classification: string;
  reason: string;
  required_eps_cagr_pct?: string;
  observed_eps_cagr_pct?: string;
  horizon_years?: number;
};
export type Impact = {
  run_id: number | null;
  analysis_id: number;
  evidence_as_of?: string;
  evidence?: Evidence;
  impacts: {
    pillar_id: number;
    name: string;
    previous_status: string;
    new_status: string;
    reason: string;
    evidence_ids: string[];
    method: string;
  }[];
};
export type Thesis = {
  pillars: Pillar[];
  as_of_date: string;
  overview: {
    summary: string | null;
    generated_from_research: boolean;
    cards: {
      id: number;
      name: string;
      status: string;
      interpretation: string;
      importance: string;
      next_test: ThesisTest | null;
    }[];
    what_working: string[];
    needs_proof: string[];
    worries: string[];
    next_big_test: ThesisTest | null;
    additional_tests: ThesisTest[];
    changes: { pillar: string; previous_status: string; new_status: string; reason: string; period: string | null }[];
  };
  current_investment_case: Record<
    string,
    { id: number; name: string; importance: string }[]
  >;
  expectation_gap: Gap;
  research_gaps: string[];
  available_analyses: { id: number; label: string; evidence_as_of: string }[];
  checkpoint_options: { id: number; description: string }[];
  metric_options: string[];
  unit_options: string[];
  history: {
    kind: string;
    name: string;
    effective_date: string;
    evidence_as_of?: string;
    previous_status?: string;
    new_status?: string;
    reason: string;
    evidence_ids: string[];
    evidence: Evidence;
    analysis_id?: number;
    definition?: { description?: string };
  }[];
};
export type ThesisTest = { pillar_id: number; pillar: string; question: string; target: string | null };
const path = (symbol: string) =>
  `/api/companies/${encodeURIComponent(symbol)}/thesis`;
export const thesisApi = {
  get: (symbol: string, scope: string, asOf: string) =>
    request<Thesis>(
      `${path(symbol)}?scope=${scope}${asOf ? `&as_of_date=${asOf}` : ""}`,
    ),
  bootstrap: (symbol: string, analysis_id: number) =>
    request<{ proposals: Pillar[]; note: string; evidence: Evidence }>(
      `${path(symbol)}/bootstrap`,
      { method: "POST", body: JSON.stringify({ analysis_id }) },
    ),
  save: (symbol: string, pillar: Pillar) => {
    const {
      scope,
      name,
      category,
      description,
      importance,
      created_from,
      source_analysis_id,
      evidence_ids,
      metric_codes,
      is_active,
      reason,
      expectations,
    } = pillar;
    return request(
      `${path(symbol)}/pillars${pillar.id ? `/${pillar.id}` : ""}`,
      {
        method: pillar.id ? "PUT" : "POST",
        body: JSON.stringify({
          scope,
          name,
          category,
          description,
          importance,
          created_from,
          source_analysis_id,
          evidence_ids,
          metric_codes,
          is_active,
          reason,
          expected_version: pillar.version,
          expectations: pillar.id ? [] : expectations,
        }),
      },
    );
  },
  test: (symbol: string, pillarId: number, test: Expectation) => {
    const {
      description,
      expectation_type,
      checkpoint_id,
      metric_code,
      operator,
      target_value,
      upper_value,
      unit,
      target_period_end,
      period_type,
      is_active,
      reason,
    } = test;
    return request(
      `${path(symbol)}/pillars/${pillarId}/expectations${test.expectation_key ? `/${test.expectation_key}` : ""}`,
      {
        method: test.expectation_key ? "PUT" : "POST",
        body: JSON.stringify({
          description,
          expectation_type,
          checkpoint_id,
          metric_code,
          operator,
          target_value,
          upper_value,
          unit,
          target_period_end,
          period_type,
          is_active,
          reason,
          expected_version: test.version,
        }),
      },
    );
  },
  evaluate: (symbol: string, analysis_id: number) =>
    request<Impact>(`${path(symbol)}/evaluate`, {
      method: "POST",
      body: JSON.stringify({ analysis_id }),
    }),
  impact: (analysisId: number) =>
    request<Impact>(`/api/analysis/${analysisId}/thesis-impact`),
};
