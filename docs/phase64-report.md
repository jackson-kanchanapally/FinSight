# Phase 6.4 — Investor engine validation and hardening

## Audit and corrections

The baseline backend suite passed before edits: **159 passed, 4 skipped**. The stored-data regression used the existing local PostgreSQL database; no production facts were fabricated or changed.

The audit found five evidence-boundary defects:

1. Financial metric calculation accepted a verified fact with the wrong unit. Income-statement inputs now require `INR_CRORE`; EPS evidence requires `INR_PER_SHARE`. This also applies to PAT bridges and market growth comparisons.
2. Recalculation left old metric rows in place after an input became unavailable or `NEEDS_REVIEW`. The calculator now removes only metrics it can no longer derive, retaining IDs for still-valid metrics.
3. The AI context could expose a stored metric whose input facts came from a different scope/company/period type, or whose declared comparison had no matching prior input. It now checks all owners and requires the current and comparison periods to be represented by verified inputs.
4. The tested-P/E API accepted scenarios whose **calculated** implied P/E values ran opposite to their conservative/base/optimistic labels. It now rejects such a set before saving it.
5. These failures had no focused regression coverage. Seven tests now exercise the boundaries, including independently diverging business and valuation verdicts.

No valuation formula or Qwen calculation path was rewritten. Python remains authoritative; model output cannot replace the server-generated report.

## Stored-data checks — 3 October 2026

Only ESDS and ETERNAL have validated financial periods in this database. There is no third company with usable validated financial facts. Both companies have separate standalone and consolidated records, and the checks found no cross-scope metric inputs. Every audited verified fact has a source document and a matching stored page.

| Latest Q1 FY2027 result | ESDS standalone | ESDS consolidated | ETERNAL standalone | ETERNAL consolidated |
|---|---:|---:|---:|---:|
| Source page | 3 | 7 | 30 | 25 |
| Verified facts | 13 | 13 | 10 | 9 |
| Revenue, ₹ crore | 116.166 | 133.657 | 3,349 | 20,211 |
| EBITDA, ₹ crore | 45.331 | 57.511 | 860 | 969 |
| PAT, ₹ crore | 18.199 | 29.277 | 585 | 92 |
| Diluted EPS, ₹ | 1.77 | 2.85 | unavailable | unavailable |
| PAT margin | 15.666374% | 21.904577% | 17.467901% | 0.455198% |
| EBITDA margin | 39.022606% | 43.028798% | 25.679307% | 4.794419% |
| Revenue YoY / QoQ | unavailable / unavailable | 7.280053% / -20.204776% | unavailable / 13.410091% | unavailable / 16.880638% |
| PAT YoY / QoQ | unavailable / unavailable | 13.984816% / -56.739465% | unavailable / -17.021277% | unavailable / -47.126437% |
| Results verdict | INSUFFICIENT_EVIDENCE | NEUTRAL | NEUTRAL | NEGATIVE |
| Valuation verdict | INSUFFICIENT_EVIDENCE | EXTREMELY_OVERVALUED, conditional | INSUFFICIENT_EVIDENCE | INSUFFICIENT_EVIDENCE |

The Q1 FY2027 ESDS consolidated margin change is **+1.146834 basis points**. Its verified diluted TTM EPS is **₹12.10**. At the dated 28 September price of **₹1,672.50**, P/E is **138.223140×**, P/S is **40.733045×**, and earnings yield is **0.723468%**. PEG is unavailable because no usable forward growth input is present. Eternal's EPS, TTM EPS, P/E, P/S, earnings yield and PEG remain unavailable; missing values were not replaced with zero. Its dated price alone does not justify a P/E verdict.

The actual-data regression rechecked that each valuation contributing fact belongs to a validated period of the requested scope and points to an existing document page. It recomputed the stored metrics and reverse expectations from their inputs within the reported six-decimal precision. The date filters exclude filings published after the market observation date.

## Formula and judgment checks

The calculator uses `100 × (current / comparison − 1)` for growth, `100 × profit / revenue` for margins, `100 × (current margin − previous margin)` for basis-point changes, dated price divided by verified TTM EPS for P/E, dated market cap divided by verified TTM revenue for P/S, `100 × TTM EPS / price` for earnings yield, and forward P/E divided by positive forward EPS growth percentage for PEG. Missing/zero denominators yield unavailable values. TTM values use a complete, point-in-time same-scope sequence.

Reverse expectations use `price × (1 + required return)^years` for required future price, divide by terminal P/E for required future EPS, and use `(required future EPS / current EPS)^(1/years) − 1` for required EPS CAGR. The read-only stored-data check independently reproduced these and the scenario implied current P/E. Required return, horizon, terminal P/E, EPS growth, assumption source and conditional confidence are exposed in the investor report. Scenario growth and multiples are assumptions, not verified forecasts.

Results judgment uses operating evidence without the current share price. Valuation judgment compares the current P/E with a calculated, conditional scenario range. Focused tests show positive results with expensive valuation and negative results with cheap valuation; a high or low P/E alone is not a business-performance verdict.

## Verification

- Backend after changes: **166 passed, 4 skipped**.
- Opt-in ESDS/ETERNAL PostgreSQL regression: **2 passed**.
- Frontend production build: **passed**; there were no Phase 6.4 UI changes.
- Live local `qwen3:8b` read-only attempt on ESDS consolidated: **rejected by the existing evidence validator**. Its summary called the shares “overvalued” without the required conditional scenario citation. The deterministic investor report remains available through the existing fallback; the model did not alter calculations or verdicts.
- `git diff --check`: **passed**.

## Limits

- A third real company could not be audited because the database has no other validated periods. The generic boundaries are covered by unit tests, not a third live filing.
- Eternal has no verified diluted EPS/TTM EPS and no usable YoY pair, so its valuation and some comparisons remain unassessed.
- Scenario P/E ranges are conditional sensitivity results. Stored historical P/E coverage is too thin to establish a representative 1Y/3Y range.
- Qwen interpretation remains best effort; unsupported claims are rejected rather than promoted to authoritative analysis.
