# Phase 8.2 — Thesis monitoring

Existing ingestion hooks now assess verified evidence without requiring a saved AI review. The Phase 8 evaluation tables, pillar verdicts, checkpoint calculations, source references and immutable history are reused. Migration `0019_thesis_monitoring` makes the review reference optional and adds financial-period, previous-assessment and assessment-snapshot fields. It has been applied to local PostgreSQL.

Assessments cache the evidence revision, thesis/test revisions, valuation gap and monitoring version. Company row locks serialize PostgreSQL writers; SQLite uses a writer transaction. Failed monitoring preserves committed ingestion and retries on the next ingestion or current thesis read. No paid API, extra AI call, or new service is required.

Changes distinguish improving, deteriorating, unchanged and insufficient evidence. The policy uses 5% revenue, 10% profit and 1 percentage point margin thresholds, with matching annual/quarterly year-over-year periods or corrections to the same period. Sequential decline alone does not imply structural deterioration. Verified requirement outcomes, guidance changes/withdrawals, risk disclosures and official corporate events retain their source references. Historical metric references resolve to the original input values.

The existing thesis dashboard includes current/previous states, confirmed comparisons versus management claims and AI interpretations, confidence, source pages, catalysts, unresolved questions and arbitrary historical comparisons.

Validation on 2026-10-12:

- Backend suite: **326 passed, 11 skipped, 4 failed**. All Phase 8/8.1/8.2 tests passed. The four failures reproduce on unchanged commit `a33715e`: fixed September valuation prices are now outside the freshness window (`test_market_valuation`, `test_phase62`, two `test_phase63` cases).
- Disposable PostgreSQL schema: migration, two concurrent assessments producing one run, and SQL history protection passed. Test schema removed afterward.
- Existing local ESDS corpus: five pillars, 161 evidence entries and an idempotent assessment; all temporary assessment writes rolled back.
- TypeScript/Vite production build passed. Phase 8.1 and Phase 8.2 browser regressions passed, including source links, historical comparisons, historical cutoff, idempotent reads and mobile overflow.

Limits: automatic monitoring requires an existing thesis and validated financial evidence. Qualitative disclosures with no measurable outcome remain insufficient evidence; a management target or withdrawal is explicitly a management statement. Missing or non-positive comparison bases do not produce invented growth figures. Only a directly verified reversal of a failed requirement establishes resolution; arbitrary narrative risks are retained for follow-up. Monitoring is deterministic; the existing explicit AI evaluation remains available. Existing valuation-test date failures remain unrelated to this phase.
