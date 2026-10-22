# Phase 8.3 autonomous research — 17 October 2026

**Verdict: PARTIAL against the full requested coverage.** The supported autonomous workflow is implemented and tested, including live ESDS source refresh. Broader provider coverage, annual-only forecasting, an announcement calendar, and independent verification of arbitrary operating milestones remain limited. No mandatory expectation, price, or growth input is required for automatic research.

## Reused architecture

Preserved the existing work in progress and reused NSE RSS/securities mapping, ESDS IR/PDF discovery, document hashes/pages, source references, financial facts/corrections, deterministic calculations, market observations, valuation calculations, commentary extraction, investor checkpoints, thesis monitoring, and immutable expectation/revision/evaluation ledgers. Existing API routes remain available. No dependency, paid API, cloud model, destructive migration, or synthetic production history was added.

## Implemented behavior

| Area | Result |
| --- | --- |
| Collection | Company creation queues disclosures, market data, commentary, and research. ESDS receives its supported IR mapping automatically. Identical source registration is idempotent; conflicting mappings require an explicit update. |
| Source safeguards | Autonomous HTTP collection checks robots rules, uses existing trusted-host/size/redirect protections, reports document/processing failures, and persists provider Retry-After delays. Provider claims coordinate across PostgreSQL workers. |
| Guidance | Existing extraction runs independently of source collection, without memory embeddings or an Ollama dependency. Original wording, source dates/pages, ambiguity, overlap hints, and scope separation are preserved. Normalized numerical guidance uses the existing accountability ledger. |
| Forecasts | Seasonal naive, trailing four-quarter mean, and seasonal drift candidates are selected by publication-safe expanding-window MAE. Requires at least eight consecutive verified quarterly observations and four valid validation folds at each horizon. Supports revenue, EBITDA, EBITDA margin, PAT, PAT margin, and diluted EPS. |
| Forecast integrity | Immutable original snapshots and explicit revisions include evidence, model version, input hash, capture/cutoff dates, horizon, assumptions, and descriptive low/base/high envelopes. No qualitative confidence is converted into a probability. Corrections unavailable at a historical fold are excluded. |
| Evaluation | Verified new evidence triggers comparison of every eligible revision; original forecasts alone enter aggregate model statistics. Includes absolute/percentage errors, MAE, RMSE, WAPE/MAPE where appropriate, signed bias, interval coverage, frozen-baseline directional accuracy, horizon/model grouping, and error trends. Margin errors use percentage points. Existing frozen threshold rules and binary Brier/calibration remain separate; minimum sample is enforced at 20. |
| Valuation | Reuses dated historical valuation calculations and missing-input reasons. Forward P/E requires four consecutive eligible future EPS forecasts. Relative-value scenarios combine forecast earnings ranges with sufficiently covered historical P/E ranges, separating earnings and multiple sensitivity. Implied growth and margin of safety use explicit formulas. Stale quotes remain dated and flagged. |
| Orchestration | Persistent fenced leases, atomic claims, restart recovery, bounded retries, per-provider cooldowns, and independent job statuses. Existing APScheduler runs one worker, disabling duplicate legacy timers. Evidence hooks enqueue research; daily/source dependency hashes avoid unchanged recalculation. Forecast evidence uses six batched queries instead of per-fact lookups. |
| Dashboard | “Almonds Forecasts & Accuracy” leads with automatic forecasts, originals versus actuals, metric/model accuracy, valuation, and automation status. Existing management/thesis views remain. Manual registration is collapsed under Advanced. Panels handle loading, empty, partial failure, retry, and mobile layout. |

## Database and APIs

The only new table is `research_jobs`, introduced by additive migration `0021_research_jobs`. PostgreSQL was already at this revision when inspected; no production migration or reset was needed. Forecast/model/evaluation metadata reuses immutable ledger JSON. Latest computed valuation scenarios and dependency state are persisted in the research job result; this is **not** an immutable valuation-scenario archive.

New routes under `/api/companies/{symbol}`:

- `GET /forecasts` — latest snapshots; `original=true` selects originals, `upcoming=true` filters before pagination; scope/offset/limit supported.
- `GET /forecasts/accuracy` — continuous model performance, horizon groups, trends, and sample sizes.
- `GET /valuation/automated` — dated valuation, eligible forward estimates, scenarios, and evidence gaps.
- `GET /research/status` — job state, refresh timestamps, next run/retry, and coverage gaps.
- `POST /research/retry` — ensures supported jobs exist; preserves active leases and provider backoff.

Existing expectation revision/results/evaluation routes and `/accuracy/guidance`, `/accuracy/calibration`, and `/accuracy/thesis-history` are retained.

Local `.env` now selects live collection and enables research automation; fixture ingestion is never scheduled. Settings defaults and `.env.example` reflect this. The scheduler operates while the FastAPI backend is running, and resumes persisted due work on startup. Bounded operator execution is available through `backend/scripts/run_research.py --symbol ESDS --limit 5`; it does not bypass backoff.

## Actual ESDS verification

The existing local PostgreSQL database was used after starting its existing Docker service. A bounded real-source pass completed NSE, ESDS IR, market, commentary, and the follow-up research jobs successfully. All five job types ended at `SUCCEEDED`; their next run times and attempts are persisted.

The final read-only audit (`backend/scripts/verify_autonomous_esds.py`) confirmed:

- Two consecutive verified consolidated quarters and one standalone quarter for the supported metrics. Every metric correctly reports `INSUFFICIENT_EVIDENCE`, with the eight-quarter requirement explained.
- Latest stored NSE EOD observation: **16 October 2026**, retrieved **17 October 2026**. The automatic market job advanced the dated observation from the previous day. This is not represented as a live executable price.
- No eligible four-quarter EPS forecast set; forward valuation remains insufficient evidence. No invented growth or fair value is supplied.
- Five original consolidated commitments remain linked to verified source evidence; standalone guidance is empty.
- Historical prediction counts remain unchanged by the audit; all candidate forecast writes are rolled back.

| Commitment | Retained deadline | Accountability |
| --- | --- | --- |
| Sydney AI factory revenue commencement | 31 December 2026 | PENDING |
| Approximately 1,500 GPUs | 28 February 2027, preserving the existing Jan/Feb wording | PENDING |
| PAT margin 15–20% | None stated | UNRESOLVED; no deadline inferred |
| GPU revenue commencement | 31 December 2026 | PENDING |
| FY27 capex | 31 March 2027 | UNRESOLVED; total/additional basis ambiguous |

The two revenue-start statements retain separate source evidence and possible-overlap hints. Operating claims are not counted as delivered without independent comparable evidence.

## Verification results

| Check | Actual result |
| --- | --- |
| Full backend suite | **412 passed, 13 skipped, 0 failed** |
| Focused autonomous / Phase 8.2 / Phase 8.3 run with PostgreSQL flags | **90 passed, 0 failed**, including disposable-schema migration, concurrent claims, and immutable-history guards |
| Existing ESDS Phase 7.2 / 8 / 8.1 corpus regressions | **3 passed, 0 failed**; temporary changes rolled back |
| TypeScript and Vite production build | **Passed** |
| Expanded accuracy browser regression | **Passed**, including automatic loading/empty/partial-error/retry states, guidance links, legacy registration, and mobile layout |
| Live ESDS worker and read-only API-schema audit | **Passed** |
| `git diff --check` | **Passed** |

The 13 ordinary-suite skips are opt-in PostgreSQL, existing-corpus, or local-service/PDF checks. Three PostgreSQL and three existing-corpus checks were subsequently exercised in the separate successful runs above; remaining optional checks were not claimed as passed. Existing dependency deprecation warnings remain.

The four earlier valuation test failures were resolved by freezing the test fixture's September 2026 freshness clock; production stale-price rules were not weakened. Three source-registration regressions were resolved through identical-payload idempotency. Synthetic forecast histories exist only in disposable test databases.

Required coverage includes ingestion/deduplication/timestamps, company and scope isolation, seasonal forecasts/refusal, immutable snapshots/revisions/leakage, actual matching, continuous errors/coverage/Brier scores, guidance ambiguity/pending states, missing/stale valuation, provider policy/backoff/restart/concurrency, API schemas, browser states, and earlier-phase regression tests.

## Remaining limitations

- No configured permitted public consensus, independent general-news, or peer benchmark provider. Official announcements remain the material-development source; unavailable inputs are explicitly reported.
- No verified future announcement-calendar connector. Forecasts are captured before quarter-end, not as post-quarter nowcasts. Annual-only histories are not forecast by this model.
- Historical RSS coverage is bounded. Company IR discovery/parsing is adapter-specific; unsupported companies/filings retain evidence gaps instead of manual-entry requirements.
- ESDS cannot produce defensible numerical forecasts from its currently verified history. Additional historical source collection is required; the scheduler reevaluates when evidence changes.
- Unnormalized operating milestones (for example GPU deployment) remain pending/unresolved unless the existing evidence engine can verify comparable outcomes. A general operating-KPI outcome extractor was not added.
- Historical P/E sensitivity is relative valuation, not intrinsic value. Scenario outputs persist as latest job state rather than as a separate immutable scenario archive. Missing shares, cash, debt, earnings, or sufficient historical multiples continue to block affected metrics.
- A bounded live refresh and restart-safe job mechanics were tested; continuous long-running operation across arbitrary source outages was not soak-tested.
