# Phase 8.3 audit and hardening — 2026-10-14

## A. Audit findings

The ESDS gap was a missing read integration, not a missing migration or scope mismatch. `sync_checkpoints` persists operating milestones, undated targets, and ambiguous capex as `InvestorCheckpoint` records. The historical guidance endpoint previously selected only MANAGEMENT expectation ledgers registered from normalized `ManagementGuidance`. ESDS has five consolidated checkpoints, zero normalized guidance rows, and zero expectation ledgers. All five original statements passed official-source, PDF-page quotation, company, scope, processing, and publication checks.

Keeping these commitments outside forecast accuracy is correct. Hiding them behind “No registered guidance” was misleading. No historical forecasts or thesis associations should be manufactured from them.

Other confirmed defects repaired:

- Same metric/period guidance statements were automatically collapsed into revisions without evidence of common lineage.
- The older guidance normalizer could accept ambiguous “additional capex” wording before checkpoint classification rejected its spending basis.
- Pending evaluations displayed their underlying `INSUFFICIENT_EVIDENCE` outcome; summaries combined pending, unresolved, and excluded records.
- All three history panels shared one pagination offset.
- Thesis history fetched one thesis revision per group and loaded unnecessary company ledgers.
- The calibration helper depended solely on its caller to exclude retrospective/ineligible rows.
- Qualitative outcome evidence could lack a publication timestamp; it now remains unresolved.
- Thesis association validation checked cutoff but did not also enforce existence at actual capture time.

Existing Decimal comparisons, frozen tolerances, immutable database guards, original-revision scoring, post-publication exclusion, scope isolation, probability validation, and minimum sample size 20 were retained.

## B. Changes made

### Backend

- Added `services/checkpoint_guidance.py`: a read-only projection of existing checkpoint records, using stable `K{id}` identifiers, original statement/disclosure date, company/scope, target, and verified PDF/page provenance. Missing provenance remains explicitly unresolved; no backfill or write occurs on GET.
- Extended `/api/companies/{symbol}/accuracy/guidance` with typed `checkpoint_items`, `ledger_total`, and `checkpoint_total`. Existing ledger `items` remain intact. `total` now counts both kinds. Pagination orders ledgers first, then unlinked checkpoint IDs descending; the combined page respects `limit`. Checkpoints already represented in any ledger revision are omitted.
- Added possible-overlap IDs for matching metric/scope/deadline records. These are review hints; distinct statements are retained. Automatic numerical registration is idempotent per original guidance ID. Explicit revision registration remains available.
- Reused source validation through `validated_statement`; guidance history joins source records and batches official references. Query-count tests verify history reads do not grow per item/version.
- Added `assessment_status` (`MET`, `PARTIALLY_MET`, `MISSED`, `PENDING`, `UNRESOLVED`, `EXCLUDED`) alongside the existing ledger status and immutable evaluation outcomes. Statistics add disjoint `pending`, `excluded`, and `unresolved_evidence` counters; the legacy combined `unresolved` counter remains compatible.
- Added the capex ambiguity guard to normalization, automatic registration, and manual ledger registration; reused the same predicate in checkpoint extraction/display.
- Hardened calibration eligibility, qualitative evidence dating, and thesis capture validation; batched thesis history reads.

Files: `backend/app/api/expectations.py`, `schemas/expectations.py`, services `checkpoint_guidance.py`, `checkpoints.py`, `commentary.py`, `expectation_evidence.py`, `expectation_guidance.py`, `expectation_statistics.py`, and `expectations.py`.

### Frontend and tests

`frontend/src/HistoricalAccuracy.tsx` and `accuracyApi.ts` now show source-linked checkpoint cards, pending/unresolved reasons, possible overlaps, explicit scoring exclusions, separate pagination, and clear filter scope. Existing styling is retained. Empty registered expectations and lack of eligible prospective evaluations are explained separately.

Added `backend/tests/test_phase83_audit.py`; updated `test_phase83.py`, `accuracy_browser_app.py`, and `frontend/scripts/accuracy-check.mjs`. Synthetic records are confined to test databases.

### Database

**No new migration, schema change, or production backfill.** Local PostgreSQL remains at `0020_historical_expectations (head)`. Existing immutable records were not rewritten. The existing additive migration was tested against pre-existing financial rows in SQLite and in disposable PostgreSQL schemas; concurrency and SQL mutation guards passed.

## C. Verification

| Check | Result |
| --- | --- |
| Final full backend suite | **382 passed, 12 skipped, 4 pre-existing failures** |
| Focused Phase 8.3 + audit tests | **56 passed, 1 opt-in PostgreSQL skip**; included in final full run |
| Broader Phase 7.1/7.2/8/8.1/8.2/8.3 regression run before final three added tests | **164 passed, 5 skipped** |
| Separate PostgreSQL Phase 8.2/8.3 migration/concurrency/immutability tests | **2 passed**; disposable schemas removed |
| Separate live ESDS Phase 7.2/8/8.1 corpus regressions | **3 passed**; temporary changes rolled back |
| TypeScript and Vite production build | **Passed** |
| Historical Accuracy browser regression | **Passed**, including five checkpoint cards, source links, pending/ambiguous/undated states, overlap hints, registration, filters, loading/error/retry, and mobile overflow |
| Existing thesis and monitoring browser regressions | **Both passed** |
| Alembic current | **0020_historical_expectations (head)** |
| Read-only real ESDS API response validation | **Passed twice**, source/history table fingerprints unchanged |
| Whitespace validation | `git diff --check` passed |

The four full-suite failures reproduce in a separate untouched archive of baseline commit `2415602`:

- `test_market_valuation.py::test_ai_context_uses_calculated_valuation_evidence`
- `test_phase62.py::test_tested_pe_api_and_historical_dates`
- `test_phase63.py::test_three_scenarios_are_explicit_saved_and_reloaded`
- `test_phase63.py::test_scenario_inputs_resolve_to_source_pages_and_changes_invalidate_context`

These fixtures use September 2026 prices which are stale at the current date. Production valuation rules and those unrelated tests were not changed. Five opt-in tests skipped in the ordinary suite were run separately above; remaining skips require optional local services/PDFs or other opt-in fixtures. No paid API, external model call, or new dependency was used.

### Actual ESDS integration

All records below are CONSOLIDATED, originally disclosed **2026-09-29 14:38:09 UTC**, and trace to source document **6**. No source claim is treated as achieved delivery.

| Stable ID / statement | Commitment | Stored deadline | Page | New guidance view |
| --- | --- | --- | --- | --- |
| K1 / S24 | Sydney AI factory revenue commencement, Q3 FY27 | 2026-12-31 | 5 | PENDING |
| K2 / S26 | Approximately 1,500 GPUs, Q4 FY27 | 2027-02-28 | 5 | PENDING; existing Jan/Feb date preserved |
| K3 / S28 | PAT margin 15–20% | None | 6 | UNRESOLVED; no deadline inferred |
| K4 / S37 | GPU revenue commencement, Q3 FY27 | 2026-12-31 | 7 | PENDING |
| K6 / S84 | FY27 capex ₹1,500 crore, total/additional unclear | 2027-03-31 | 16 | UNRESOLVED; ambiguous basis |

K1 and K4 flag possible overlap and remain separate. Standalone guidance returns zero records. Historical expectations, prospective evaluations, and linked thesis expectation histories correctly remain **zero**; calibration has no eligible observations, with minimum **20**.

### Required test coverage

| Requirement | Evidence |
| --- | --- |
| 1–3 checkpoint integration, repeated sync, distinct commitments | Five-checkpoint projection, distinct normalized statements, mixed pagination/overlap tests |
| 4–7 provenance, ambiguity, no deadline, future pending | Projection tests and real ESDS checks; browser assertions |
| 8–9 post-result exclusion and scope separation | Existing import/date-only/source tests plus new state/denominator and API isolation tests |
| 10–12 scoring boundaries, missing outcomes, frozen rules | Exact 5%/10% boundaries, existing range/direction/unit tests, frozen-policy configuration-change test |
| 13–14 Brier and insufficient samples | Existing n=20 known score 0.1875; defensive eligibility test with known score 0.10 and low-n suppression |
| 15 original thesis versions | Existing immutable guards plus later-edit association test and history query-count test |
| 16 filtering/pagination | Existing ledger API tests, combined guidance pagination/scope validation, independently paged UI |
| 17 migration preservation | Existing financial rows retained through additive SQLite migration; PostgreSQL guards; live read-only fingerprint comparison |
| 18 evaluation idempotence | Existing retry, concurrent evaluation, correction/invalidation/restoration tests; repeated guidance sync |

## D. Remaining risks and limits

- Three ESDS milestones require future independent operating disclosures. PAT needs an explicit assessable period; capex needs spending-basis clarification. Their unresolved states are intentional.
- Checkpoint projection is descriptive: it does not promote commitments into scored forecasts or infer results from mutable checkpoint evaluation caches. It displays no achieved/missed verdict without a registered, verified ledger evaluation.
- Imported history remains excluded from prospective statistics. Unsupported historical thesis reconstruction is not performed.
- Overlap flags are conservative metric/date comparisons, not semantic proof. Earlier automatically merged historical ledger versions, if present elsewhere, are not destructively split; ESDS has none.
- Unsupported numerical guidance metrics and arbitrary qualitative milestones retain the existing evidence requirements. Missing or undated evidence stays unresolved.
- Unrelated valuation fixture failures and optional service/PDF skips prevent claiming a completely green repository suite.

## E. Verdict

**PASS for the Phase 8.3 audit scope; ready to close this phase.** The guidance integration, source traceability, immutable history, eligible denominators, calibration mathematics, thesis associations, UI behavior, migration safety, and relevant earlier-phase regressions are verified. The overall repository suite is not fully green because of the four independently reproduced baseline valuation failures. Future management delivery remains unproven.
