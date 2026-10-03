# Phase 6.3 — Investor judgments and tested P/E

## Architecture

Validated facts → deterministic operating signals and earnings bridge → separate results judgment, scenario valuation judgment and research stance → bounded Qwen evidence context → strict validation or useful deterministic fallback → existing analysis API and Financials report.

The existing financial processor, market snapshots, point-in-time TTM calculations, reverse-growth function, historical valuation and commentary/guidance records are reused. No separate financial parser or analysis service was added.

Three named scenarios expose EPS growth, horizon, terminal P/E, required return and provenance. Python calculates implied current P/E and reverse expectations. The existing tested-P/E endpoint saves user assumptions per company/scope. Each analysis snapshots its assumptions; changing them changes its context hash. Default sensitivities are explicitly `SCENARIO_ASSUMPTION`, with LOW valuation confidence. They are not estimates of intrinsic value.

## Changed components

- `backend/app/market/expectations.py`: scenario assessment, observed growth comparisons and evidence-relative expectations gap.
- `backend/app/services/investor_evidence.py`, `investor_verdict.py`, `investor_report.py`: comparable EPS/EBITDA signals, reconciled PAT bridge, cash conversion, separate judgments and research conclusion.
- `backend/app/ai/analyst/{context_builder,prompts,service,validator,fallback,quality,schemas}.py`: structured evidence, exact numeric precision, constrained model citation IDs, persisted diagnostics and deterministic fallback/report refresh.
- `backend/app/api/{analysis,valuation}.py`: existing endpoints extended for saved scenarios and deterministic refresh.
- `backend/app/models/{analysis,market}.py`; migration **0014_investor_scenarios**: `ai_analyses.validation_diagnostics` and `valuation_assumptions`. Migration applied to the local PostgreSQL database.
- `backend/app/config.py`, `backend/.env.example`: default model context budget; older 12,000-character configurations remain supported through evidence-preserving prompt compaction.
- `frontend/src/{InvestorJudgmentReport,TestedPEPanel,FinancialsView}.tsx`, `api.ts`, `financials.css`: eight summary metrics, conclusion before details, editable scenarios, provenance and diagnostics.
- Tests: `test_phase63.py`, `test_phase63_live.py`; existing analyst and Phase 6.2 contract assertions updated.

## Validation failures diagnosed

1. Numeric validation previously accepted only rounded variants, omitting exact six-decimal authoritative values such as Eternal's `-47.126437`. Exact Decimal values are now included; an adjacent invented value is still rejected.
2. Live Qwen output cited `results_signals` as an evidence ID. Context headings are not evidence. The response schema now enumerates actual permissible IDs and omits unused server-report definitions.
3. A subsequent response combined insufficient valuation data with a conclusive model verdict. Model availability is now constrained by actual calculated multiples; mismatches also record the offending field, value and expectation.
4. The old blanket ban on valuation labels conflicted with explaining the new deterministic verdict. A label now requires the exact Python verdict, a cited scenario and explicit conditional wording. Reversed, uncited and unconditional conclusions remain invalid.

Diagnostics retain each rejected attempt. Model arithmetic, invented evidence, unsupported causes and trading recommendations remain prohibited. The model cannot supply or replace the server's investor report.

## Stored-data regression — 29 September 2026

| Item | ESDS Q1 FY2027 consolidated | Eternal Q1 FY2027 consolidated |
|---|---|---|
| Results | NEUTRAL / MEDIUM | NEGATIVE / LOW |
| Valuation | EXTREMELY_OVERVALUED / LOW, conditional on scenarios | INSUFFICIENT_EVIDENCE / NOT_ASSESSED |
| Dated price | ₹1,672.50, 28 September | ₹327, 29 September |
| Verified diluted TTM EPS | ₹12.10 | Unavailable |
| Current P/E | 138.22× | Unavailable |
| Base required EPS CAGR | 86.37% | Unavailable |
| Tested P/E | 17–50×; central 31× | No supported range without verified TTM EPS |
| Stance | WAIT_FOR_BETTER_VALUATION | THESIS_REQUIRES_REVIEW |

ESDS default scenarios: growth **6.77% / 13.55% / 20.32%**, terminal P/E **20 / 30 / 40**, horizon **3 years**, annual required return **12%**. Growth sensitivities use the latest observed diluted EPS YoY as an anchor; persistence and terminal multiples are explicit illustrative assumptions. Near-range tolerance is 10%; extreme gap is 35%, both configurable through the existing API. There is no hidden quality premium.

ESDS revenue/PAT grew 7.28%/13.98% YoY, but fell 20.20%/56.74% QoQ. The reconciled YoY PAT increase includes ₹1.786 crore higher pre-tax profit and ₹1.806 crore lower tax. Cash conversion and a normal seasonal pattern are unestablished.

Eternal revenue/EBITDA grew 16.88%/17.03% QoQ while PAT fell 47.13%. Its bridge shows ₹44 crore higher pre-tax profit, offset by ₹126 crore higher tax expense. The accounting movement is established; the underlying business/tax cause is not inferred. Manual document provenance and existing review exclusions are retained.

## Verification

- Backend: **159 passed, 4 skipped**. Two skips are optional PDF/model checks; two are the opt-in stored-data cases.
- Actual PostgreSQL ESDS/Eternal regressions: **2 passed** when explicitly enabled.
- Frontend TypeScript and production build: **passed**.
- Actual-report desktop/mobile browser checks: **passed**.
- Live Qwen (`qwen3:8b`): final captured run **VERIFIED_FALLBACK**. Attempt 1 rejected an uncited “overpriced” summary claim; attempt 2 timed out. The complete deterministic report was saved, with field/value/expected diagnostics. Schema citation/availability restrictions and conditional-verdict checks also pass the focused provider tests.

```sh
cd backend
.venv/bin/pytest -q
RUN_PHASE63_DB_REGRESSION=1 .venv/bin/pytest tests/test_phase63_live.py -q
cd ../frontend
npm run build
```

The live regression is opt-in and read-only; it requires the existing local filings. Browser verification covered both actual reports, eight summary metrics, valid editable decimal assumptions, source disclosure, mobile table containment and no page errors. Screenshots are local artifacts under `frontend/e2e-artifacts/phase63-*.png`.

## Limits

- The tested range is a sensitivity to disclosed assumptions, with LOW confidence; terminal multiples and persistence are not verified forecasts.
- Stored historical P/E coverage is insufficient for representative 1Y/3Y ranges. No verified peer premium is applied.
- Cash conversion, working-capital movements, exceptional items and management execution remain unassessed where the relevant verified evidence is absent.
- Eternal lacks verified TTM EPS and a usable YoY pair; its result judgment is provisional and sequential.
- Qwen can still fail semantic validation or time out. It cannot change deterministic judgments, and the same complete report remains available through fallback.
