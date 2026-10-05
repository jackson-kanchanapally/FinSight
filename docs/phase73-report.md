# Phase 7.3 — Evidence-safe quarterly investor synthesis

## Root cause and observed ESDS failure

The Phase 7.2 ESDS Q1 FY2027 Qwen response was schema-valid and about 430 words, but both attempts asserted unsupported "cost control" as a cause. The all-or-nothing `validate_quarterly_review` rejected the entire interpretation, yielding `VERIFIED_FALLBACK` (saved analysis 45). Its trimmed context also omitted the explicit research gaps and some speaker/page metadata while still showing an entered reference P/E beside the Python-calculated P/E. These were observed in the saved raw responses and context, rather than inferred from the fallback label.

The first Phase 7.3 live diagnostic exposed a second integration error: a validated partial review was passed through a whole-output validator after Python fallback rows had been inserted. The second diagnostic showed that requiring a causal classification on every row caused Qwen to label ordinary trends as established causes. Both errors were corrected without disabling numeric, source, causal or valuation checks.

## Implementation

- `quarterly_packet.py` builds a compact packet from the existing context and report. It carries current verified values with scope and page locators, deterministic comparisons and earnings quality, selected attributed statements, checkpoints and outcomes, up to three historical passages, Python valuation assumptions, and explicit research gaps. The full registry remains the evidence and UI drill-down source. The entered reference P/E is excluded from the model packet.
- The existing `F`, `M`, `S`, `K`, `A`, `V`, `J`, `C` and `I_REVIEW` IDs are retained; no parallel alias system or migration was added. `I_REVIEW` now carries research gaps as well as valuation credibility and history coverage. The schema and validator only allow IDs actually supplied in the packet.
- Qwen receives a shorter senior-investor prompt and a constrained quarterly schema. Python remains authoritative for ratios, growth, results/valuation verdicts and checkpoint statuses. Management statements remain claims and require exact quotations and attribution. Causal basis is optional for non-causal statements but checked when a cause is asserted.
- `quarterly_validation.py` validates each generated item independently, discards rejected or duplicate items, keeps accepted items, and fills required fields from the deterministic review. One repair call targets at most six rejected items, prioritizing the investor view and valuation. The repair response cannot alter accepted items. Full fallback remains for unusable JSON, provider failure or fewer than two valid model items.
- Saved `review_trace` records packet size, supplied IDs, retrieval count, accepted/rejected counts, repair, partial status and fallback reason. Item diagnostics retain section/index and reason. The UI labels deterministic fallback correctly and flags a partly validated interpretation.
- The deterministic review's own source quote and valuation-context citations were made validator-compliant. No endpoint, financial calculation, document pipeline, RAG store or database schema was changed.

## ESDS comparison

| Path | Model result | Evidence safety |
|---|---|---|
| Phase 7.2, analysis 45 | `VERIFIED_FALLBACK` after two rejected responses | Unsupported cost-control claims rejected; model prose discarded |
| Phase 7.3 final, analysis 57 | `SUCCESS`, 7 model items retained, 11 rejected and 8 safe rows filled from Python after one focused Qwen repair | Every displayed item passed the saved-registry validator; unsafe causal, numeric, attribution, comparison and valuation claims excluded |

The newer review covers the divergent YoY and QoQ results, management's GPU revenue and deployment targets, unverified seasonality, funding questions, pending checkpoints and required future earnings. A verified financial comparison and the cited AI-factory milestone now lead the investor view. Its retained management quotes link back to the existing transcript pages. The primary review is **280 words**, compared with **412** in the previous deterministic fallback. It contains no unsupported new value or uncited management explanation.

The final analysis came from an actual local Qwen generation and its single focused repair. After one final fact-versus-interpretation check, the saved response was revalidated and the same real-model analysis record was updated; five replay-only diagnostic records created during development were removed. The model still left many optional claims unsupported, so the review is explicitly marked `PARTIAL_VALIDATED`. No facts, checkpoints, calculations or valuation verdicts were changed by the model. Analysis 57 is the latest ESDS Q1 FY2027 analysis in the local database.

## Verification

Focused tests cover stable packet IDs and source pages, multi-evidence synthesis, claims versus facts, attributed explanations, causal and numerical rejection, research-gap citations, pending checkpoint behavior, selective repair, partial success and bounded fallback. Existing Phase 7.2 tests continue to cover company/scope/date isolation and no look-ahead. An opt-in ESDS test validates the latest saved Qwen review against its persisted evidence registry; it creates no facts or documents.

Baseline before Phase 7.3: **236 passed, 6 skipped**. After implementation: **258 passed, 7 skipped**; frontend production build and `git diff --check` passed. The opt-in Phase 7.2 ESDS corpus packet test and opt-in Phase 7.3 saved-review validation both passed. The existing browser regression passed all nine sections, the collapsed 87-statement source drill-down, page links, desktop/mobile layout and JavaScript error check.

## Remaining limits

Local `qwen3:8b` may take several minutes and still emit unsupported claims. In the final ESDS run, seven of fifteen displayed items were model generated and eight were deterministic; the repair resolved the opening causal error but not every rejected claim. More capable local models may improve yield, but no relaxation of evidence checks was made. Existing ESDS history has no assessable prior execution pattern; management credibility stays undetermined. Missing cash flow and historical valuation coverage remain research gaps, not inferred facts. No generic language validator can prove every subtle interpretation; the model is never allowed to create authoritative values or outcomes.
