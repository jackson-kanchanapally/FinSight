# Phase 7.2 — Senior Investor Quarterly Review

## 1. Architecture reused

Existing verified financial facts/metrics → Phase 6.3 results, earnings-quality and valuation outputs → existing attributed management statements/guidance + Phase 7 historical retrieval → material evidence selection → persistent checkpoints and period-specific outcomes → concise review → optional Qwen interpretation → existing evidence checks.

No separate extraction, ingestion, RAG, valuation service or analysis API was added. The existing financial analysis endpoint serves the new review. Results processing and commentary refresh update the latest quarterly deterministic review for each scope when company commentary exists; Qwen remains available through the existing analysis action.

## 2. Files and migration

New backend modules: `services/materiality.py`, `services/checkpoints.py`, `services/quarterly_review.py`, `models/checkpoint.py`, and `tests/test_phase72.py`.

Integrated with `services/commentary.py`, `services/financials/service.py`, `services/investor_report.py`, `services/memory/analyst.py`, `models/__init__.py`, the existing analyst context/prompts/schemas/service/validator and provider capability flags.

Frontend: new `QuarterlyReview.tsx`; integration in `InvestorJudgmentReport.tsx`, evidence labels in `FinancialsView.tsx`, types in `api.ts`, small additions to `financials.css`, and the opt-in `scripts/quarterly-review-check.mjs` browser check. README links this report and explains where to open the review.

Migration **0017_investor_checkpoints** creates `investor_checkpoints` and `checkpoint_evaluations`; applied to the local database. Earlier uncommitted Phase 7/7.1 files were preserved.

## 3. Materiality

Transparent text rules identify execution risk, commitments, capital allocation, operating context, order conversion, business changes, economics and disclosure boundaries. Category rotation prevents one topic filling the packet. Explicit checkpoint sources are retained. Greetings, administrative language, generic optimism and repeated financial recitation remain in drill-down. No numerical management or materiality score.

The prompt uses a bounded evidence packet, preserves authoritative calculations, and removes duplicate source metadata before trimming relevant memory. Full source pages and calculation lineage remain in the server registry. Complete short quotations are preserved.

## 4. Checkpoint model

Each checkpoint stores company, creation period, original attributed statement, optional existing guidance ID, topic, target period/date, metric, target range, units and measurement limitations. A separate evaluation records status, verified outcome, evidence and assessment date for each review period. Unique keys prevent repeat ingestion/review duplication. Historical promises are retained; a later quarter does not overwrite an earlier quarter's outcome.

Existing guidance evaluation supplies deterministic financial outcomes, with company, scope, unit, verification and publication-date checks. Explicitly linked revisions can establish reported delays or withdrawals. A promise alone never establishes delivery. Capex delivery alone does not validate earnings-growth assumptions.

## 5. ESDS checkpoints

Existing **consolidated Q1 FY2027**, transcript document 6; no production financial facts or documents fabricated.

| Source | Commitment | Target | Current status |
|---|---|---|---|
| Page 5 | Sydney AI-factory revenue commencement | Q3 FY27 | PENDING |
| Page 5 | Deploy 1,500 GPUs | Q4 FY27; Jan/Feb 2027, latest stated month ends 28 February | PENDING |
| Page 6 | PAT-margin target 15–20% | No reporting deadline stated | NOT_YET_ASSESSABLE |
| Page 7 | GPU revenue commencement | Q3 FY27 | PENDING |
| Page 16 | ₹1,500 crore capex language | FY27; total versus additional spending unclear | INSUFFICIENT_EVIDENCE |

The two revenue-start statements retain separate source records: their project equivalence is not assumed. The watch list groups the common metric/deadline without deleting either promise. No previous assessable ESDS commitments are available; no credibility score or delivery claim is generated.

## 6. ESDS investor reading

Revenue grew **7.28% YoY** and PAT **13.98% YoY**, but fell **20.20%** and **56.74% QoQ**, respectively. Higher pre-tax profit and lower tax both contributed to PAT growth. Management's seasonality explanation remains a claim because there are insufficient equivalent historical transitions to establish a pattern.

The material forward issues are AI/GPU revenue commencement, deployment timing and execution dependencies, the untimed PAT-margin target, unclear capex scope, funding through customer advances/IPO proceeds/debt, and conversion of the reported order book into revenue. Management declined numerical full-year guidance. These disclosures create follow-up questions; they do not establish future earnings or delivered milestones.

The valuation implication remains **INSUFFICIENT_EVIDENCE**. Existing deterministic valuation scenarios and verdicts are unchanged. Future operating and financial disclosures must demonstrate execution before promises can support the required growth assumptions.

## 7. Previous versus new output

Previously, the primary presentation exposed dozens of individual claims and repetitive assessment rows. Now it shows nine bounded review sections, an execution scorecard when prior commitments exist, and a short next-quarter watch list. All **87 ESDS statements**, original source links, calculations and valuation scenarios remain in collapsed drill-down.

The final saved ESDS review contains **422 narrative words**, compared with **3,961 words** in the underlying statements. Fourteen material statements inform the report; the final model input is **11,804 characters**. Five source records produce four grouped next-quarter checks. The local saved review is analysis **46**, served by the existing API.

Verified QoQ changes are distinguished from YoY changes and changes in management's message. Missing historical commentary is stated rather than replaced with an inferred strategy change.

## 8. Validation

The model grammar requires citations, evidence basis and quotation fields. Python still rejects invented numbers/IDs/quotes, unsupported causes and valuation claims, management claims presented as financial facts, invented delivery outcomes, unsupported historical changes, altered valuation implication status, recommendations and excessive length. The model cannot replace deterministic calculations or checkpoint statuses.

Live **qwen3:8b** testing first hit the normal timeout. A longer diagnostic run exposed excessive length and missing citation fields; the generation grammar was tightened to require attribution and citations. The final strict-schema response was 430 words, but both attempts made unsupported cost-control claims and were rejected. Analysis 45 records `UNSUPPORTED_CAUSAL_EXPLANATION`; no model interpretation from that run was accepted. The final API-generated analysis 46 uses the concise deterministic review with source-backed checkpoints. The longer timeout was a diagnostic override, not a change to application configuration. Qwen reliability remains a limitation.

## 9. Tests

Baseline before implementation: **206 passed, 5 skipped**. Focused coverage includes relevance/noise, checkpoint extraction and vague-target exclusion, delivery/miss/delay/withdrawal, historical preservation, company/scope/date isolation, guidance changes, source pages, valuation boundaries, strict model contract, idempotent automatic refresh and output bounds.

The opt-in ESDS corpus test verifies that GPU timing, PAT margin, capex/debt funding, order book, seasonality and guidance refusal survive within the configured 12,000-character packet. It rolls back its transaction and creates no documents or financial facts.

Final full backend suite: **236 passed, 6 skipped** (the skipped cases require opt-in live services/data); six existing dependency deprecation warnings. Phase 7.2 focused tests: **30 passed, 1 skipped** in normal mode. The separately enabled live ESDS packet regression passed. `npm run build` and `git diff --check` passed.

Desktop/mobile browser checks passed for all nine headings, the collapsed 87-statement commentary drill-down, PDF page links, no horizontal overflow and no JavaScript errors. The final API response passed its schema, and repeating deterministic generation returned analysis 46 again without duplicate review/checkpoint records.

## 10. Remaining limitations

- Checkpoint extraction is deliberately conservative and rule based. Vague growth acceleration and ambiguous project references are not forced into measurable targets.
- No verified ESDS operating-outcome disclosure is available yet. GPU/deployment milestones stay pending and become NOT_DISCLOSED after their deadlines if no assessable outcome exists. Generic automatic verification of every operational milestone is not implemented; financial guidance outcomes reuse the existing evaluator.
- ON_TRACK/PARTIALLY_DELIVERED are not inferred from tone or partial spending. Future verified operating evidence needs an explicit comparable measurement before those statuses can be supported.
- Current ESDS commentary does not establish repeated execution patterns or historical management credibility. Historical memory is supplementary documentary evidence, never an authoritative financial fact.
- Qwen can be slow or produce invalid interpretations. Strict validation and a deterministic fallback remain necessary; no validation guarantee is claimed for unconstrained language meaning.
