# Phase 8 — Living Investment Thesis & Expectations Engine

Implemented in the existing company financial experience. No Phase 9 work.

## 1. Architecture reused

Company and financial-period identity, verified fact/metric evidence, saved Phase 7.4 AIAnalysis, commentary statements, investor checkpoints, deterministic Phase 6.3 valuation outputs, existing ReviewItem validation, configured local Qwen provider, and React Financials/QuarterlyReview. No new financial parser, checkpoint engine, RAG store, provider, or portfolio calculation.

Architecture decisions were recorded before implementation in [phase8-architecture.md](phase8-architecture.md).

## 2. Files and migration

Added:

- `backend/alembic/versions/0018_living_thesis.py`
- `backend/app/models/thesis.py`, `schemas/thesis.py`, `api/thesis.py`
- `backend/app/services/thesis.py`, `thesis_evidence.py`, `thesis_evaluation.py`
- `backend/tests/test_phase8.py`, `thesis_browser_app.py`
- `frontend/src/ThesisPanel.tsx`, `thesisApi.ts`, `thesis.css`
- `frontend/scripts/thesis-check.mjs`
- Phase 8 architecture, report, and ESDS regression evidence artifact.

Integrated via `models/__init__.py`, `main.py`, `ai/analyst/service.py`, `api/companies.py`, `FinancialsView.tsx`, `QuarterlyReview.tsx`, and the existing frontend request helper. Company deletion now returns a clear conflict when immutable thesis history exists. README documents entry points.

Migration 0018 is applied to local PostgreSQL. All five append-only triggers are installed. No production thesis beliefs were retained by regression runs. Earlier uncommitted Phase 7.4 work was preserved.

## 3. Data model

Five tables: stable pillar identity; versioned pillar definitions; versioned investor expectations; evaluation runs containing evidence/valuation snapshots; per-pillar evaluations containing prior/new state, explanation, exact expectation outcomes, contradiction flag and research gaps. PostgreSQL and SQLite triggers reject UPDATE/DELETE, with ORM guards as well. Edits append revisions; no history is overwritten.

## 4. States and transition rules

UNPROVEN, STRENGTHENING, ON_TRACK, WEAKENING, BROKEN, NOT_YET_TESTABLE, INSUFFICIENT_EVIDENCE. Verified direct test outcomes support changes. One missed requirement can weaken a pillar; BROKEN requires a core pillar, material contradiction, current verified failure and comparable failures across distinct reporting periods. Changed targets cannot borrow unrelated old failures. Reanalysis of the same quarter cannot establish a second delivery. Missing information cannot erase an established state; no price-only transition.

## 5. Expectations/checkpoints

Management-derived expectations link existing InvestorCheckpoint IDs. Targets and outcomes come from the saved checkpoint evidence; no second management-target evaluator is introduced. Investor numerical requirements specify exact metric, unit, operator/range, reporting basis and period end. Qualitative requirements remain unproven until measurable evidence exists. Test edits preserve previous definitions and do not inherit their old outcomes.

## 6. Provenance

USER, SYSTEM_INFERRED, MANAGEMENT_DERIVED and RESEARCH_DERIVED remain distinct. User acceptance is recorded separately from origin. Bootstrap returns proposals only; users review/edit/accept them. Acceptance today does not claim the user held that belief historically. Every inferred proposal has source IDs and a saved analysis reference.

## 7. Qwen evaluation

One structured request evaluates relevant active pillars against their stored requirements, prior states, recorded execution history, verified current evidence and relevant saved review items. Schema constraints restrict pillar IDs, citation IDs and source-quote choices; the existing validator still checks each item. Valid rows survive independently. Invalid or unavailable output uses conservative deterministic evaluation. Qwen cannot write metrics, checkpoint outcomes, scenario assumptions or historical states.

**Live model result:** local qwen3:8b supplied eight rows in the final ESDS regression; none passed all checks. Quote/management-attribution failures and a scenario-only claim were rejected. All eight resulting evaluations used deterministic fallback. Synthetic provider tests separately exercise acceptance of valid model conclusions and per-item rejection. Model-generated prose quality remains a limitation; validation was not relaxed to obtain acceptance.

## 8. Evidence validation

Evidence is filtered by real company/period ownership, scope, source publication, fact verification, metric lineage and saved-analysis availability. Existing numeric, quotation, attribution, causal and recommendation checks are reused. Citations must directly relate to the pillar's declared metrics or linked tests. Page/document references survive in definition snapshots, current state evidence, evaluations and timeline entries.

## 9. Expectation gap

The existing Phase 6.3 required EPS CAGR is compared only with verified historical EPS CAGR over the same horizon. Required minus observed CAGR: LOW at zero or below; MODERATE up to 10 percentage points; HIGH up to 25; EXTREME above 25. These exposed thresholds are an interpretation policy, not a score or forecast. Missing dates, inputs or compatible horizons yield INSUFFICIENT_EVIDENCE. No Phase 6.3 calculation changed.

## 10. Temporal protections

Source cutoff is bounded by the saved review's actual documents, its creation date and the evaluation date. Future-period facts, later publications, foreign-company evidence, other scopes, unavailable metric lineage and future market snapshots cannot enter the evaluation. Historical reads reconstruct stored revisions/evaluations; they do not regenerate the past from current state. Adoption/edit APIs do not accept backdated dates. An older review cannot replace a later period's thesis. UI hides current data immediately when a historical date is selected.

## 11–12. ESDS initial thesis and tests

Regression uses existing **consolidated Q1 FY2027**, saved analysis **60**, documentary cutoff **2026-09-29**. These are evidence-backed proposals, not automatically accepted beliefs.

| Proposed pillar | Initial evaluation | Next requirement / evidence |
| --- | --- | --- |
| Revenue commencement | NOT_YET_TESTABLE | Sydney AI factory revenue from Q3 FY27; existing K1/S24, transcript page 5 |
| Capacity deployment | NOT_YET_TESTABLE | Approximately 1,500 GPUs; stated January/February Q4 timeline; K2/S26, page 5 |
| Profitability | NOT_YET_TESTABLE | Management PAT margin target 15–20%; no invented deadline; K3/S28, page 6 |
| Capital spending | UNPROVEN | Clarify total versus additional FY27 capex before comparison; K6/S84, page 16 |
| Asset economics | UNPROVEN | Validate realized utilization, pricing and cash returns; asset-appreciation claim remains a claim; S57, page 11 |
| Funding capacity | UNPROVEN | Verify financing and cash generation supporting investment; S43, page 8 |
| Demand conversion | UNPROVEN | Funnel must convert into signed business, revenue and cash; S47, page 9 |
| Valuation expectations | INSUFFICIENT_EVIDENCE | Saved base scenario requires **86.367934% EPS CAGR over three years**; no compatible three-year observed EPS CAGR |

Missing OCF/FCF, uncertain capex basis and unverified realized economics remain research gaps. No current GPU growth, delivered deployment or proven funnel conversion was invented. No company-specific implementation branch exists.

See [phase8-esds-regression.json](phase8-esds-regression.json) for proposal inputs, source/page evidence, actual model rejection diagnostics and resulting states. Temporary acceptance/evaluation was rolled back; IDs in that artifact are regression references, not retained production thesis records.

## 13. Transition example

Generic unit regression: an investor's verified revenue requirement passes → UNPROVEN to STRENGTHENING. A later, stricter edited requirement misses → WEAKENING with an explicit contradiction, while the earlier date still reconstructs STRENGTHENING. Separate tests allow a valid model-supported BROKEN transition only after comparable core failures in distinct periods. ESDS itself has no fabricated delivered transition.

## 14. UI/API

Open **Financial performance → select company → Investment Thesis**. Select a saved review, choose **Propose initial thesis**, then **Review / edit / accept** each desired pillar. Add/edit tests and importance, deactivate pillars, evaluate the saved review, inspect evidence and choose a history date. The existing quarterly review ends with concise **Thesis Impact**.

Company-scoped endpoints under `/api/companies/{symbol}/thesis`: GET current/as-of view; POST bootstrap; POST/PUT pillars; POST/PUT linked expectations; POST evaluate. `/api/analysis/{id}/thesis-impact` returns the saved evaluation. Version checks reject conflicting edits.

## 15–16. Tests and results

- Baseline before implementation: **268 passed, 8 skipped**.
- Final backend suite: **303 passed, 9 skipped**; 35 new passing Phase 8 tests, with the opt-in live corpus test skipped by default.
- Explicit local PostgreSQL ESDS regression: **1 passed**; temporary writes rolled back.
- Local Qwen regression: completed; **0 accepted / 8 rejected**, with eight safe deterministic pillar evaluations.
- Isolated Playwright workflow: **passed** creation, importance, expectation add/edit, deactivation, immutable history, historical-date view, mobile overflow and browser-error checks.
- TypeScript/Vite production build: **passed**.
- Static migration schema, upgrade/downgrade and SQL immutability: **passed** in SQLite; PostgreSQL migration and five guards confirmed locally.
- Existing deprecation warnings remain (Starlette/httpx and PyMuPDF SWIG).

Coverage includes ownership/scope/provenance, history immutability, direct and material evidence, malformed rows, management-claim separation, repeated delays, repeated comparable failures, same-quarter reanalysis, horizon mismatch, price-only rejection, missing values, research gaps, future financial/management/memory leakage, automatic review integration, deterministic fallback and API version conflicts.

## 17. Remaining limitations

- ESDS has limited execution history and no compatible multi-year EPS CAGR; mature delivery conclusions and a quantified expectation gap would be premature.
- The live Qwen run failed strict item validation; usable safe output currently comes from deterministic fallback for that corpus.
- Qualitative requirements need linked checkpoints or explicit numerical tests before measured delivery can change their state. There is no autonomous semantic proof of business quality.
- Bootstrap is a conservative generic proposal builder using existing checkpoint and materiality classifications. It does not infer an investor's original purchase rationale or silently populate production beliefs.
- Corrections and deactivation preserve history. Companies with thesis records cannot be deleted through the existing destructive company-delete action.
- No intraday historical reconstruction: `as_of_date` represents a day; exact recorded timestamps remain in the audit data.

Stopped after Phase 8.
