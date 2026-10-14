# Phase 8.3 — Historical Thesis Accuracy & Expectations Intelligence

Implemented in the existing FastAPI / SQLAlchemy / PostgreSQL / React application. This phase measures recorded expectations against verified business outcomes; it does not predict prices or change investment verdicts. No new infrastructure, paid API, hosted model, or additional Ollama call is used.

## Implementation and reuse

- Append-only expectation identities, revisions, source snapshots, and evaluation history. Server UTC capture timestamps cannot be supplied by API callers. Historical imports are explicitly marked; a claimed issue date cannot make an import contemporaneous. AI imports cannot establish forecast accuracy.
- Exact, range, directional and qualitative expectations, source attribution, immutable thesis-version links, original statement retention, source-page navigation, and optimistic revision conflicts. Management numerical targets must match existing validated guidance normalization; a valid source quotation cannot authorize invented numbers.
- Existing `FinancialPeriod`, `FinancialFact`, `FinancialMetric`, document, statement, guidance, checkpoint and thesis identifiers remain authoritative. Evaluations store reproducibility snapshots, not a second financial fact dataset. Existing normalization, calculation, metric freshness checks, official-source checks and checkpoint evaluation are reused.
- Automatic guidance registration reuses the existing official-commentary pipeline. Revised targets append ledger revisions. Conditional and aspirational guidance is separately labeled and excluded from credibility statistics. Withdrawals retain supporting official evidence and cannot erase outcomes already published.
- Evaluation runs at the existing committed financial-results, commentary and event monitoring boundaries, including companies without a saved thesis. Company locks serialize writers; PostgreSQL and SQLite unique constraints guard identities and evaluations. Identical consecutive evidence produces no new evaluation. Evidence correction, invalidation and restoration preserve earlier snapshots. Failure preserves committed ingestion; the next ingestion or explicit evaluation retries it.
- Company and system summaries separate metric, scope, period type, reporting basis, source and category. Original forecasts remain accountable after revision: revisions do not add independent samples or erase misses. The API exposes period summaries, rolling windows, error distributions, source coverage and calibration data.
- Historical Accuracy appears in Financials, sharing its company/scope selection. It includes filters, paginated comparisons, revision details, exact/range registration, thesis-version selection, guidance, original thesis outcomes, reliability plots and confidence buckets. Loading, empty, error/retry and responsive states use real API records.

## Mathematics and timing policy

Amounts normalize to INR crore using the existing calculator; per-share values remain separate. Margin/growth levels use percent, margin changes use basis points; a percentage-point change converts to basis points, while a relative percentage change cannot substitute for a margin change. Comparison requires matching company, scope, financial period type/end/start, metric, unit and reporting basis. Stale derived metrics and invalid inputs are excluded.

Signed error = forecast − actual. Absolute error = abs(signed error). Percentage error = signed error / abs(actual) × 100; absolute percentage error uses its absolute value. No percentage error is reported for abs(actual) ≤ 0.000001 or percentage-level/change metrics. For ranges, error is measured to the nearest boundary, and literal range coverage is also retained. Direction compares verified actual with a pre-cutoff baseline, retaining both values. Material sign changes between profit and loss cannot be hidden by a relative tolerance.

Default tolerances are 5% for MET and 10% for PARTIALLY_MET on amounts, with a 0.000001 absolute floor; percentage metrics use 0.5 and 1 percentage point (50/100 basis points for changes). Configuration is in `EXPECTATION_MET_TOLERANCE_PCT`, `EXPECTATION_PARTIAL_TOLERANCE_PCT`, and `EXPECTATION_PERCENTAGE_TOLERANCE_PP`. Each revision freezes its policy at capture; later configuration changes cannot rescore its tolerance.

A prospective sample must be captured before its cutoff and before result publication, with cutoff no later than publication. Date-only results conservatively use the start of the publication day. Future publications and post-cutoff baseline corrections cannot leak into a prediction. Imports remain excluded even when their claimed issue date is early. No independent historical-import attestation mechanism is introduced.

There is no composite credibility score. Aggregation uses counts, median absolute errors, median absolute percentage errors, min/max error distributions and range/direction counts within compatible groups. Every aggregate has a sample count. The default minimum for rates and overall calibration is 20 (`EXPECTATION_MIN_SAMPLE`, minimum allowed 10). Rolling summaries use the last 20 original forecasts, ordered by target period and identity. Small-sample errors and bucket frequencies are descriptive and explicitly marked insufficient; no forecast-skill claim is made.

A probability is eligible only with the event `MET_UNDER_FROZEN_TOLERANCE`. Brier = mean((p − y)²), where y = 1 for MET and 0 for MISSED or PARTIALLY_MET. Other outcomes are unscored. Labels such as HIGH are never converted to probabilities. Buckets are [0,.1), …, [.9,1]; calibration error is the sample-weighted absolute difference between mean predicted probability and observed MET frequency. Overall Brier/calibration error is withheld below the minimum. Historical probabilities never change.

Thesis outputs classify original linked assumptions as VALIDATED, PARTIALLY_VALIDATED, CONTRADICTED, UNRESOLVED or INSUFFICIENT_EVIDENCE. Forecast error alone does not establish business deterioration. Existing thesis definitions, historical assessments and investment verdict logic remain intact.

## Persistence and migration

Additive Alembic migration `0020_historical_expectations`, following `0019_thesis_monitoring`:

- `historical_expectations`: company/source/metric/period identity and matching index.
- `historical_expectation_revisions`: frozen definitions, cutoff, provenance and foreign keys to original thesis, official statement, guidance, checkpoint and document.
- `expectation_evaluations`: immutable outcomes, matching period reference, calculation/evidence snapshot and unique evaluation fingerprint.

ORM and database triggers reject history updates/deletions. Foreign keys use RESTRICT. A downgrade refuses to discard nonempty history. Existing guidance and statement tables provide source commitments and revisions; they are not duplicated. Status is derived from the latest evaluation/revision; matching and evaluation run atomically, while persisted API views expose PENDING, EVALUATED, UNVERIFIABLE and SUPERSEDED.

Applied to local PostgreSQL on 2026-10-13; `alembic current` reports `0020_historical_expectations (head)`. No existing financial facts were rewritten.

## API

Existing local application access conventions and company ownership checks are retained; no separate authentication architecture is added. All routes have Pydantic response contracts. Lists support bounded pagination; ledger and statistics support company, scope, period, metric and source filtering.

| Method | Route under `/api` | Capability |
| --- | --- | --- |
| POST / GET | `/companies/{symbol}/expectations` | Capture / list expectations |
| POST / GET | `/companies/{symbol}/expectations/{id}/revisions` | Append / retrieve revision history |
| POST | `/companies/{symbol}/expectations/{id}/evaluate` | Idempotently evaluate all revisions |
| GET | `/companies/{symbol}/expectations/{id}/results` | Retrieve immutable evaluation history |
| POST | `/companies/{symbol}/expectations/evaluate-pending` | Register available guidance and retry evaluations |
| GET | `/companies/{symbol}/accuracy` | Company statistics |
| GET | `/accuracy` | System statistics, separated by source/category |
| GET | `/companies/{symbol}/accuracy/calibration` | Probability calibration and sample safeguards |
| GET | `/companies/{symbol}/accuracy/guidance` | Registered official guidance |
| GET | `/companies/{symbol}/accuracy/thesis-history` | Original thesis outcomes and selectable thesis versions |

## Files

Created: `backend/alembic/versions/0020_historical_expectations.py`; `backend/app/models/expectations.py`; `backend/app/schemas/expectations.py`; `backend/app/api/expectations.py`; services `expectations.py`, `expectation_math.py`, `expectation_evidence.py`, `expectation_guidance.py`, `expectation_statistics.py`; `backend/tests/test_phase83.py`; `backend/tests/accuracy_browser_app.py`; `frontend/src/HistoricalAccuracy.tsx`, `accuracyApi.ts`, `accuracy.css`; `frontend/scripts/accuracy-check.mjs`; this report.

Modified: model/router registration; configuration and `.env.example`; the existing `refresh_monitoring` ingestion boundary; `FinancialsView.tsx`; Phase 8.2's disposable PostgreSQL setup to apply the new dependent migration.

## Validation

Validation on 2026-10-13:

- Phase 8 / 8.1 / 8.2 / 8.3 targeted suite: **103 passed, 4 skipped**. Phase 8.3 contributes **41 passed**; its PostgreSQL test is opt-in in the ordinary run.
- Full backend suite: **367 passed, 12 skipped, 4 failed**. All four failures reproduce in an isolated archive of unchanged baseline commit `0029278`. They are the previously documented September valuation-price freshness fixtures, not Phase 8.3 regressions: `test_ai_context_uses_calculated_valuation_evidence`, `test_tested_pe_api_and_historical_dates`, `test_three_scenarios_are_explicit_saved_and_reloaded`, and `test_scenario_inputs_resolve_to_source_pages_and_changes_invalidate_context`.
- PostgreSQL checks explicitly enabled separately: **2 passed**, covering both Phase 8.2 and Phase 8.3 additive migrations, concurrent writers, single evaluation identity and SQL history guards. Each disposable schema was removed afterward. Migration `0020` was then applied successfully to the local application database.
- TypeScript / Vite production build: **passed**.
- Historical Accuracy browser regression: **passed**, using disposable SQLite records through the actual API and services. Covers persisted outcomes, the registration form, sample safeguards, reliability chart, filters, source-page links, revision history, loading, empty, error/retry, successful registration and mobile overflow. Browser-generated records are test-only. Mobile screenshot: `/tmp/almonds-phase83-mobile.png`.
- Existing Phase 8.1 thesis and Phase 8.2 monitoring browser regressions: **passed**.
- `git diff --check`: **passed**.

The default full-suite skips require opt-in PostgreSQL/corpus access, external local Ollama/Qdrant services, or local PDFs not committed to the repository. The two skipped PostgreSQL migration tests were explicitly run and passed separately; the other optional live/PDF tests were not run. Existing dependency deprecation warnings remain.

Reproduce with `cd backend && .venv/bin/python -m pytest -q -rs`, `ALMONDS_PG_ACCURACY_TEST=1 .venv/bin/python -m pytest tests/test_phase83.py -q`, and `cd frontend && npm run build && node scripts/accuracy-check.mjs`. Local socket/database access is required for PostgreSQL and browser checks.

## Limits and remaining work

- Qualitative claims require independently supported checkpoint outcomes. The existing checkpoint engine cannot verify arbitrary project completion from prose, so unsupported milestones remain INSUFFICIENT_EVIDENCE; absence of disclosure is never failure.
- Automatic numerical guidance registration covers the metric definitions already normalized and supported by the current calculator. Unmapped debt-reduction amounts, EBITDA/EPS growth, capacity and order-book measures remain in the original commentary/checkpoint system. They are not silently substituted with incompatible financial metrics. Supported directional and qualitative registration is available through the API; the dashboard form covers exact/range targets.
- Historical imports are retained but cannot contribute to prospective reliability. Existing theses are not retroactively relabeled as predictions. Link a newly captured forecast to an original thesis version; unsupported historical reconstruction remains excluded.
- Guidance-kind classification uses explicit wording in validated statements. It is a transparent deterministic classification, not a model assessment of management intent.
- No scheduled polling was added. Failed evaluation retries on subsequent ingestion or through **Evaluate available results**; the new accuracy GET endpoints do not mutate the ledger (the existing thesis-read refresh can still invoke its monitoring hook). Initial registration of previously ingested guidance uses those same paths.
- Rates are descriptive frequencies, not evidence of independent forecasting skill. Forecasts about the same underlying result can be correlated; no significance test or benchmark-skill claim is supplied.
- Existing unrelated valuation fixture failures remain, as detailed in validation. No production valuation calculations or freshness rules were weakened to make those tests pass.
