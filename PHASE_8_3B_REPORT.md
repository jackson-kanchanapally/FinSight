# Phase 8.3B — Forecast accuracy and confidence calibration

Implementation: 18 October 2026; final baseline and read-only corpus verification: 19 October 2026 (Asia/Kolkata).

The extension is implemented and integrated with the existing ledger, financial evidence resolver, job queue and dashboard. **Real historical accuracy and calibration remain data-blocked for ESDS and Eternal:** neither company has historical numerical forecasts in the immutable forecast ledger. No predictions, confidence scores, actuals or source references were fabricated or backfilled.

## Repository inspection and implementation plan

The working tree already contained uncommitted Phase 8.3/audit/autonomous-research work. Those changes were preserved. No repository `AGENTS.md` was found in the workspace or its ancestor directories.

The inspected lifecycle was:

1. `forecasting.generate_scope` resolves verified financial facts/derived metrics, fits deterministic seasonal baselines using expanding windows, and freezes forecast metadata through `expectations.save`.
2. `historical_expectations` identifies the company/metric/scope/fiscal period; `historical_expectation_revisions` stores immutable definitions, cutoff, capture, source snapshots, model/input hashes and versions. Existing ORM and database triggers reject UPDATE/DELETE.
3. Financial processing validates facts, maintains same-document correction history, calculates metrics, and invokes the existing thesis/evidence refresh hook. The hook evaluates expectations and enqueues research. The persistent research worker also evaluates forecasts.
4. `actual_evidence` resolves company, metric, scope, period, verified source and units. `evaluate_revision` appends idempotent `expectation_evaluations` snapshots.
5. Before this extension, automatic accuracy selected the latest evaluation. Corrected actuals could therefore silently replace the number used in a historical aggregate. The existing API and React `AutonomousResearch` section already exposed forecasts and descriptive errors.
6. Confidence already had three distinct meanings: qualitative LOW/MEDIUM/HIGH labels; explicitly declared probabilities of MET under frozen tolerances; and deterministic scenario envelopes with **no nominal probability**. These are not interchangeable.

The evidence-based plan was to preserve first-actual scores, add typed/filtered/paginated comparisons and calibration, extend the existing dashboard, and verify mathematical, database, API, browser and real-corpus behavior. No separate forecaster, paid provider, LLM scoring call or new queue was introduced.

## Implementation

### Immutable observations and matching

- Original revisions alone contribute to primary Almonds forecast accuracy. Revisions remain accessible through existing history endpoints.
- Primary statistics choose the first eligible scored evaluation of the original prediction with an uncorrected verified actual snapshot. Later corrections, invalidation and restored evidence remain separate append-only evaluations; they cannot replace that primary score.
- If the first available scored snapshot already contains corrections, it is excluded with `ORIGINAL_ACTUAL_SNAPSHOT_UNAVAILABLE`. Correction values alone cannot reconstruct original units, verification status and source context reliably.
- New evaluation snapshots include `FORECAST_ACCURACY_V2`, fiscal start/end/year, scope, reporting basis, currency, information cutoff, actual snapshot, publication metadata, provenance and structured reason codes. The existing result contains original/frozen policy, errors and evidence IDs.
- Missing metrics, invalid sources, scope/period-type mismatch, pending cutoff, historical imports and post-publication capture are explicit states. Direct unit mismatches are excluded; derived metrics additionally reject incompatible input units.
- The older cross-source accuracy summaries now use primary evidence for AI forecasts too. The generic expectation ledger still displays latest evidence, explicitly labeled in the UI.
- Imported history is never promoted to prospective accuracy based solely on a claimed historical issue date.

**Boundary:** first-actual selection is bounded by verified records retained in the existing corpus. The financial model allows one fact per metric/period. A second PDF for the same period still goes through the existing revision-review path; this phase does not automatically accept unreviewed replacement filings or build a new multi-filing actuals archive. Accepted corrections and later evidence states are audited separately. Unknown original actuals remain excluded.

### Mathematics and calibration

- Decimal-only signed/absolute error, APE, MAE, RMSE, bias, WAPE and directional accuracy.
- APE uses `abs(actual)` and is unavailable at zero/near-zero denominators. Margins use percentage-point errors; EPS uses INR/share; monetary totals use INR crore.
- MAPE is secondary and restricted to positive actuals; its sample count is explicit. WAPE uses the sum of absolute actuals and is unavailable at an invalid denominator. Negative values otherwise remain valid observations.
- Equal weight per forecast within comparable groups; WAPE alone weights by absolute actual magnitude. Outliers are retained. Groups separate company, scope, metric, period type, unit, currency, accounting basis, model/engine and quarter horizon. No composite accuracy score is produced.
- Every aggregate supplies sample size and evaluation IDs. Rolling windows use the last 20 primary observations ordered by actual publication, with rolling calibration and error history.
- Explicit prediction intervals have frozen ordered bounds and nominal probability. The API validates these together with their point forecast. Descriptive scenario-envelope coverage/width remains separate from nominal interval calibration.
- Calibration buckets expose observed frequency/coverage, expected probability where applicable, sample size, mean interval width and Wilson 95% uncertainty bounds. These bounds assume independent observations; dependence across overlapping forecasts is disclosed.
- Brier scores are calculated only for genuine probabilities of the defined MET event, and suppressed below the configured minimum sample (20). Labels show descriptive MET frequency with no expected probability. Small samples display **Insufficient data**.
- No learned confidence mapping or retrospective confidence assignment is introduced. Expanding-window validation additionally enforces a simulated pre-quarter-end information cutoff, preventing training inputs published during the target reporting lag from leaking into a fold. This bounded validation change advances the existing engine identifier to `SEASONAL_BASELINES_V2`; it adds no forecasting model. Historical hindcasts never become prospective observations.

### Database and jobs

Migration **`0022_forecast_accuracy`**, parent `0021_research_jobs`, adds only:

```text
ix_expectation_evaluation_history (revision_id, id)
```

It is reversible by dropping the index. Existing immutable JSON snapshots already accommodate the new observation/calibration metadata; no duplicate ledger or destructive migration is needed. Existing foreign keys, uniqueness, locks and immutable-history triggers are reused.

The migration was applied to the local PostgreSQL database after disposable-schema checks. No company financial, forecast or evaluation records were backfilled.

The new `ACCURACY` job kind uses the existing `research_jobs` table, scheduler, leases, failure handling and retries. Repeated queue requests coalesce by company/kind and preserve active leases/backoff. This job runs evaluation without forecast generation. Existing financial-evidence refresh and research jobs continue to score automatically. The research dependency hash includes the scoring version so a deployment cannot leave an unchanged-input cache on the older scoring contract.

### APIs

All routes remain under the existing local API access model; no endpoint can update immutable predictions or scores.

| Route | Change |
| --- | --- |
| `GET /api/companies/{symbol}/forecasts/accuracy` | Extended typed summary: overview, exclusions, comparable metric/horizon groups, intervals, calibration, rolling history and observation IDs. |
| `GET /api/companies/{symbol}/forecasts/comparisons` | New typed paginated original/primary-actual comparison and later-evidence history; `exclusions_only=true` retrieves pending/missing/excluded records. |
| `POST /api/companies/{symbol}/forecasts/evaluate` | New 202 response enqueues deterministic evaluation through the existing worker. |
| Existing `/accuracy` and `/accuracy/calibration` | AI statistics now retain primary actuals; management/user semantics remain intact. |
| Existing `/expectations/{id}/results` and `/revisions` | Retained paginated immutable audit history. |

Accuracy/comparisons validate scope, financial metric enum, fiscal period type, quarter horizon, date range, `offset >= 0`, and `1 <= limit <= 200`. Summary pagination is by group; comparison pagination is by original forecast. Dates filter primary actual publication, or target period for unmatched records. Unknown companies return 404, invalid filters 422. Read routes do not trigger scoring or generation.

### Dashboard

The existing `AutonomousResearch` panel now contains `ForecastAccuracyPanel`:

- Overview counts and explicit insufficient-data status.
- Fiscal period, accounting basis, exact metric code, units/currency, capture/cutoff, source links, forecast/actual and absolute/signed/relative errors.
- Immutable primary scores beside expandable later corrections/evidence changes.
- Metric, horizon, period-type and publication-date filters; separately paginated comparisons and metric groups.
- Confidence bucket/nominal-interval tables with samples and uncertainty; rolling error/calibration history.
- Missing-data/exclusion reasons, loading/error/retry/partial results, and a scoring-queue button.
- No new manual forecast-entry form. The pre-existing advanced ledger form remains unchanged.

Broader browser verification found existing icon-only mobile navigation retaining an invisible text gap. A one-line CSS correction removes that gap. The general browser harness was moved from a shared single-connection in-memory database to the temporary file-backed SQLite pattern already used by the other browser suites, avoiding concurrent-request instability. These are test/accessibility fixes, not ingestion or portfolio changes.

## Real ESDS and Eternal validation

`backend/scripts/verify_forecast_accuracy.py` uses PostgreSQL `SET TRANSACTION READ ONLY`, validates typed responses, and verifies unchanged history fingerprints. Separate TestClient regressions exercise both companies' real financial/forecast APIs without writes.

| Evidence | ESDS | Eternal |
| --- | --- | --- |
| Historical numerical forecast ledgers | 0 | 0 |
| Scorable historical forecast/actual pairs | 0 | 0 |
| Legacy thesis expectation revisions | 7: four checkpoint links, three qualitative hypotheses; no numerical targets | 0 |
| Existing eligible consecutive consolidated quarters | 2 for all six currently generated metrics | 0 under existing source-validation rules |
| Existing eligible consecutive standalone quarters | 1 for all six currently generated metrics | 0 under existing source-validation rules |
| Management commitments | 5 consolidated; 0 standalone | 0 |
| Accuracy/calibration result | Insufficient data | Insufficient data |

ESDS has verified Q4 FY2026 and Q1 FY2027 consecutive consolidated results, plus nonconsecutive earlier results and annual data. The existing automatic forecaster requires at least eight consecutive verified quarters; no numerical forecasts were manufactured to bypass it.

Eternal has validated March/June 2026 periods in both scopes, but these came from `MANUAL_UPLOAD` / `MANUAL` events dated 29 September 2026. Their facts are `VERIFIED_PDF`, not `MANUALLY_VERIFIED`, and lack an official LIVE source reference required by the existing forecast-evidence resolver. They remain visible in Financials and were not relabeled or given invented publication times. No metric can currently acquire a defensible historical accuracy score from these records.

## Tests and actual results

- Initial unchanged-working-tree backend baseline: **412 passed, 13 skipped, 0 failed**.
- Final complete backend run with relevant PostgreSQL and real-corpus flags: **449 passed, 7 skipped, 0 failed**. New Phase 8.3B file contributes 31 cases, including three opt-in database/corpus cases.
- Additional older stored-report corpus checks: **3 passed, 1 pre-existing failure**. `test_phase63_live.py::test_stored_q1_fy2027_report[ESDS-NEUTRAL-MEDIUM]` expects P/E `138.22`, while the stored report returns `124.97`. The identical assertion failed against an isolated, unchanged `git archive HEAD` checkout on 19 October (that baseline file: **1 passed, 1 failed**). The Eternal report and both additional ESDS review checks passed. No valuation logic or test expectation was changed to conceal this failure.
- All five self-contained browser suites passed: general portfolio/events, thesis, monitoring, existing accuracy, and new populated Phase 8.3B accuracy.
- TypeScript check and Vite production build passed.
- SQLite and PostgreSQL migration preservation/reversibility checks passed; existing PostgreSQL concurrency and immutability guards passed.
- Real ESDS/Eternal API and read-only integrity checks passed; local schema is `0022_forecast_accuracy`.
- `git diff --check` passed.

The seven skips in the consolidated run were: optional Ollama (1), Qdrant/embedding service (1), explicit local PDF fixture (1), and opt-in older corpus tests (4, subsequently run with the results above). Existing FastAPI/httpx and PyMuPDF deprecation warnings remain. No pre-existing backend failures occurred in the initial ordinary suite; enabling the older stored-report checks exposes the baseline ESDS failure. Browser failures discovered during broader checks were addressed as described above; no test assertion was weakened.

Reproduction commands (backend commands run from `backend`, browser/build commands from `frontend`):

```sh
ALMONDS_PG_ACCURACY_TEST=1 ALMONDS_PG_RESEARCH_TEST=1 ALMONDS_PG_MONITOR_TEST=1 ALMONDS_LIVE_REGRESSION=1 RUN_PHASE72_LIVE=1 .venv/bin/python -m pytest -q -rs
RUN_PHASE63_DB_REGRESSION=1 RUN_PHASE73_LIVE=1 RUN_PHASE74_LIVE=1 .venv/bin/python -m pytest tests/test_phase63_live.py tests/test_phase73.py::test_live_esds_saved_qwen_review_is_evidence_valid tests/test_phase74.py::test_live_esds_priority_and_validated_review -q
.venv/bin/python scripts/verify_forecast_accuracy.py
```

```sh
for script in scripts/browser-check.mjs scripts/thesis-check.mjs scripts/monitoring-check.mjs scripts/accuracy-check.mjs scripts/forecast-accuracy-check.mjs; do node "$script" || exit "$?"; done
npm run build
```

Coverage includes exact matches, fiscal/type/scope separation, units/currency, zero/negative/missing/outlying values, EPS/margins/EBITDA, correction/invalidation history, post-publication and historical imports, immutable versions, retries, calculation formulas, known 80% synthetic coverage, Brier score, absent confidence/intervals, limited samples, pre-capture information checks, publication-ordered rolling windows, API validation/pagination/company isolation, browser loading/empty/error/partial/populated states and legacy workflows. Synthetic records exist only in disposable test databases.

## Files created or modified by this phase

New:

- `backend/alembic/versions/0022_forecast_accuracy.py`
- `backend/app/schemas/forecast_accuracy.py`
- `backend/app/services/forecast_scoring.py`
- `backend/app/services/forecast_observations.py`
- `backend/scripts/verify_forecast_accuracy.py`
- `backend/tests/test_phase83b.py`
- `backend/tests/forecast_accuracy_browser_app.py`
- `frontend/src/ForecastAccuracyPanel.tsx`
- `frontend/scripts/forecast-accuracy-check.mjs`
- `PHASE_8_3B_REPORT.md`

Extended existing working-tree files:

- `backend/app/api/research.py`
- `backend/app/models/expectations.py`
- `backend/app/schemas/expectations.py`, `research.py`
- `backend/app/services/expectations.py`, `expectation_evidence.py`, `expectation_statistics.py`, `forecast_accuracy.py`, `forecasting.py`, `research_dependencies.py`, `research_jobs.py`
- `backend/tests/browser_app.py`
- `frontend/src/AutonomousResearch.tsx`, `HistoricalAccuracy.tsx`, `accuracy.css`, `styles.css`
- `README.md`

Some of these files were already untracked or modified when this task started. This list describes this phase's edits, not ownership of all pre-existing working-tree changes.

## Acceptance boundaries and Phase 8.3C next steps

The implementation, deterministic matching/scoring, immutability, queue integration, APIs, UI and applicable tests are verified. **The acceptance criteria requiring demonstrated real historical forecast accuracy or sufficient real calibration evidence remain blocked by missing prospective records.** Passing synthetic tests does not establish Almonds' real-world forecasting skill.

1. Use permitted existing source adapters to obtain additional official historical ESDS filings and official provenance for Eternal. Verify publication timestamps, financial definitions and periods; retain manual-upload origin until independently validated. Do not infer publication dates or relabel old uploads automatically.
2. Acquire at least eight consecutive verified quarters for the existing generator. Capture genuine future forecasts with their original information cutoffs; allow the existing evidence hooks to score subsequently published actuals.
3. Build source-version support for independently published restatement PDFs through the existing review workflow. Retain original and revised facts with separate provenance before allowing first-published versus restated analysis. Never replace an existing primary score or reconstruct missing source context from correction values alone.
4. Accumulate at least 20 comparable prospective observations per metric/horizon/bucket before interpreting calibration. The present seasonal envelopes remain descriptive; any future probabilistic interval model must declare nominal coverage before results. Any learned recalibration must use a chronological training/holdout split or walk-forward fit using only already-published prior outcomes.
5. Re-run the read-only audit and regression commands after new evidence arrives. No paid APIs or LLM numerical scoring are required.
