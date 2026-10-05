# Phase 7.4 — Investor materiality and review quality

## 1. Root cause

The Phase 7.3 selector rotated through commentary categories and capped the selection at 14; the Qwen packet kept only the first 12. Late, material ESDS disclosures lost space to routine commentary. The excerpt builder chose a customer-concentration sentence from the GPU asset-economics statement, hiding its appreciation claim. Deterministic “What Changed” inserted revenue and PAT QoQ movements by default. Optional model sections could omit capex while remaining populated, and duplicate detection caught only near-exact wording. Live review inspection exposed two more validation gaps: an undated margin target acquired an FY27 deadline, and deployment was described as delayed without a cited delay. Historical retrieval returned zero passages; it limits execution comparisons but did not cause the ranking errors.

## 2. Files changed

Backend materiality, quarterly review, Qwen packet, prompt, item assessment and validator modules; Phase 7.2 and new Phase 7.4 tests; this report and the README link. No migration, endpoint, valuation calculation or frontend component changed.

## 3. Materiality and ranking

Deterministic rules assign CRITICAL, HIGH, MEDIUM, LOW or NOISE using explicit factors: timeline revisions, earnings milestones, capex, funding, capacity, margins, demand, supply, pricing, asset assumptions and customer risk. Quantified and dated commitments are marked. Capex is flagged relative to scale only when a verified earlier annual revenue fact exists in the same scope and unit. The flag affects order, never fact status, and emits no ratio. Existing checkpoints survive the cap; remaining slots favor distinct factors. Packet rows carry tier, factors, page and the existing evidence ID. Every management item remains a claim.

## 4. Change detection

QoQ metrics remain performance evidence and no longer populate “What Changed.” An explicit reported revision supports an attributed change; dated prior outcomes and checkpoint revisions remain usable. Without a supported story change, the review says “Baseline established this quarter.” ESDS has a management-reported Sydney timeline revision, but no assessable history of repeated execution.

## 5. Deduplication

Selection favors new material factors under the packet cap. Item assessment catches high-overlap paraphrases sharing evidence, including an opening paragraph repeated as a development. Deterministic developments, risks and watches have separate purposes. Related references can remain when one states a change, another its consequence, and a third an observable test.

## 6. Qwen prompt

The existing prompt now directs Qwen to rank investment impact, distinguish results from developments and expectations, challenge important management assumptions, and connect execution to Python's valuation expectations. It forbids assigning a year to an undated target or calling an outcome delayed without source support. It asks for concise attributed claims and distinct section roles. Prompt version: FINANCIAL_ANALYST_V22_PHASE74. The compact packet, item validator, one bounded repair and deterministic fallback remain.

## 7. ESDS Phase 7.3 versus 7.4

| Measure | Phase 7.3 analysis 57 | Phase 7.4 analysis 60 |
|---|---:|---:|
| Status | SUCCESS | SUCCESS |
| Primary review words | 280 | 398 |
| Valid model items retained | 7 | 7 |
| Rejected model items after repair | 11 | 13 |
| Deterministic items used | 8 | 10 |
| Retrieved historical passages | 0 | 0 |
| “What Changed” | Revenue and PAT QoQ | Attributed Sydney revision |
| Capex, funding, funnel, asset claim | Mostly absent | Present with verification conditions |
| Watch list | Revenue, GPUs, margin | Revenue, GPUs, capex scope, asset economics |

The longer Phase 7.4 review covers more distinct material subjects. The model still needs safe fallback. Neither path changed verified facts, checkpoint statuses or valuation calculations.

## 8. Important ESDS evidence selected

The Qwen packet includes the Sydney delay and Q3 revenue expectation, FY27 capex with ambiguous total-versus-additional scope, 1,500-GPU target, 15–20% PAT-margin target, supply delays, advance/IPO/debt funding, the >50,000-GPU funnel, pricing assumptions and GPU appreciation argument. Source pages and statement IDs remain available. The final primary review emphasizes the Sydney revision, capex, funding, funnel as unconverted demand, asset economics as a claim, verified YoY results and cash-conversion uncertainty.

## 9. Evidence left secondary

The PAT-margin target stays in the checkpoint table; Qwen's attempt to assign it an FY27 deadline was rejected because management stated no assessable period. The domestic order book and detailed pricing claims remain in transcript drill-down. They lost primary-review space to dated milestones, capital spending, funding, funnel conversion and asset economics. Supply constraints are in the packet but did not survive as a separate primary-review paragraph in analysis 60; the delivery milestone remains on the watch list.

## 10. Validation results

The stricter validator rejects invented target periods and uncited delays. A read-only replay of analysis 59 rejected both and produced a valid review. Final analysis 60 is SUCCESS with PARTIAL_VALIDATED trace: seven model items retained, 13 rejected after one repair and ten deterministic items used. Its 398-word review passes the saved-registry validator. Existing numeric, quote, attribution, causal, valuation, scope, date and evidence-ID checks remain active.

## 11. Tests added

Focused Phase 7.4 tests cover ranking, company-relative ordering, unusual assumptions and excerpts, noise, packet diversity, story change versus QoQ, baseline behavior, semantic repetition, safe material coverage, invented dates and delays, cash gaps, measurable watches, pages and an opt-in ESDS run. Existing Phase 7.2/7.3 tests cover checkpoint evaluation, company/scope/date isolation, no look-ahead, selective repair and fallback.

## 12. Full test results

Before changes: 258 passed, 7 skipped; frontend build passed. After changes: 268 passed, 8 skipped; frontend build and diff whitespace check passed. All three opt-in ESDS corpus tests passed against completed analysis 60. The Phase 7.2 live packet assertion now checks priority factors rather than demanding every transcript category in the trimmed context. No browser check was rerun because frontend code did not change.

## 13. Remaining limitations

The local Qwen model still emits many unsupported draft items; bounded repair and deterministic coverage remain necessary. ESDS lacks assessable prior commitments and retrieved historical passages, so credibility patterns cannot be asserted. Verified operating and free cash flow are unavailable for a compatible period, and FY27 capex wording is ambiguous. The 15–20% margin target has no stated deadline. These remain research questions, not projected outcomes.
