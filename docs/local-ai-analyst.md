# Codex analyst

Opening any event now requests a short, cached Codex analysis from `POST /api/events/{id}/analyze`. It summarizes the disclosure, explains why it matters, suggests a research action (`WATCH`, `INVESTIGATE`, `NO_ACTION`, or `INSUFFICIENT_EVIDENCE`), and says what to watch next. Management statements appear only when the cited event text or stored PDF excerpt supports them. The event brief links every field to its source and rejects unknown citations, unsupported numbers, and trading calls. A failed or unavailable Codex run leaves the original event intact and shows the failure in the UI. Event analysis runs when an event is opened, so the event list does not launch a model call for every filing at once.

The Financials view accepts a user entered closing price, optional P/E and market cap, observation date, and HTTPS source URL through `PUT /api/companies/{symbol}/market-snapshot`. An optional forward annual EPS growth estimate needs its own HTTPS source URL. almonds calculates PEG as P/E divided by the estimated growth percentage, following the [CFA Institute's PEG definition](https://rpc.cfainstitute.org/blogs/enterprising-investor/2025/is-the-peg-ratio-a-reliable-market-timing-tool), and displays it only when both inputs are available. A snapshot older than seven calendar days is displayed as stale and excluded from new AI valuation context. These values are not a live feed, and the growth estimate is not a verified result. NSE publishes [official daily market reports](https://www.nseindia.com/all-reports), but its [website terms](https://www.nseindia.com/static/nse-terms-of-use) prohibit systematic automated collection. almonds therefore does not scrape prices from its quote pages or reports. A single quarter, P/E, and PEG cannot establish intrinsic value.

The analyst reads a `VALIDATED` financial period. `context_builder.py` includes verified facts, deterministic metrics with their input fact IDs, eligible verified comparison facts, and short relevant lines from the already stored PDF pages. It excludes `NEEDS_REVIEW` facts. It never fetches a website or sends a full PDF. The bounded JSON context is hashed; the hash, evidence registry, prompt version, model name, output, status, and failure reason are saved in `ai_analyses`.

`LLMProvider.generate(system, user, schema)` is the only model boundary. `CodexProvider` invokes `codex exec` in read-only, ephemeral mode and supplies the generated Pydantic JSON schema through `--output-schema`. It disables user configuration and repository rules for these bounded analysis calls. The analyst service has no model-specific logic, and the existing validators remain authoritative.

The original financial result fields remain:

```json
{
  "summary": "string",
  "summary_evidence_ids": ["F12"],
  "positives": [{"title":"string","explanation":"string","evidence_ids":["F12"]}],
  "concerns": [],
  "material_changes": [{"metric":"Revenue YoY","change":"+7.28%","interpretation":"string","evidence_ids":["M4"]}],
  "questions": ["string"],
  "data_quality_notes": ["string"]
}
```

The current financial schema also includes `management_commentary` when explicitly reported, a conditional `what_might_happen` note, a cited `result_view` with a positive/negative/neutral label, `valuation_assessment` with a provisional verdict, and a cited research action. Arrays may be empty when no claim is supported. Each claim and material change needs at least one citation. almonds creates stable `F` fact, `M` metric, `D...P...` PDF-page, and `Q` market-snapshot IDs. Validation rejects unknown IDs, numbers that cannot be matched to supplied values at 0–4 decimal places, HTML/links, extra JSON fields, and BUY/HOLD/SELL calls. Citation checks establish that an ID was supplied; they do **not** prove that the prose faithfully describes that source. A number check catches unsupported figures, not every possible semantic error. Human review remains important.

The V14 financial prompt asks for a central investor takeaway, supported positives and concerns, earnings-quality questions, and a conditional valuation view. It distinguishes annual from sequential changes and requires the direction of each cited growth metric to be preserved. The validator rejects negative figures when only a positive metric supports that magnitude. For example, ESDS PAT growth is **+13.98% YoY**; a model claim of **−13.9%** is rejected and receives the matching metric as repair guidance. A repeated identical invalid response stops further repair attempts and saves an evidence-cited fallback. Provider timeouts also save that fallback, with the failure reason retained. Repeated `regenerate=true` requests reuse a recent failed fallback for 15 minutes; `force=true` explicitly bypasses that cooldown. The Financials view shows when retry becomes available.

Phase 6.1 adds deterministic `R` relationship and `H` seasonality evidence. Python computes P/E cross-check direction and discrepancy, PAT versus revenue growth, EBITDA margin changes in percentage points and basis points, and equivalent-quarter transition statistics. External P/E is a calculation cross-check, never an intrinsic-value benchmark. At least three verified equivalent-quarter transitions are required before classifying a seasonal pattern. The model receives these objects and human-readable units, and cannot author the saved `quality_report`: Almonds assembles its operating, seasonal, earnings-quality, valuation, results-support, strengthening/weakening, and structured research-gap sections from verified evidence after validating the model response. The frontend shows these sections with source provenance and keeps the older fields for API compatibility. The validator also rejects contradictory numerical relationships, unsupported valuation judgments, and identifiable unsupported causal claims. These checks do not prove every sentence is causally grounded; human review remains necessary.

`backend/app/ai/evaluation/esds.py` emits inspectable pass/fail checks for revenue, PAT, EBITDA margin, improvement, deterioration, missing information, citations, numeric grounding, scope, and recommendations. The optional Codex integration test prints this report and fails when checks fail. `pytest` uses fake provider responses and stays fully offline.

## Historical V1 ESDS acceptance run (27 September 2026)

The stored live ESDS consolidated Q1 FY2027 period (period ID 4) was analyzed with local `qwen3:8b` and `FINANCIAL_ANALYST_V1`. The first response mentioned `56.74%` without its matching citation and was rejected. Targeted repair produced a successful second response, saved as analysis ID 4. The deterministic evaluation passed **14/14** checks. Manual comparison found:

| AI claim | almonds evidence | Audit |
| --- | --- | --- |
| Revenue ₹133.657 crore and PAT ₹29.277 crore | F26 and F38, official PDF page 7 | Exact |
| PAT margin 21.90%, EBITDA margin 43.03% | M11 21.904577%, M12 43.028798% | Rounded correctly |
| Revenue rose 7.28% YoY | M13 7.280053% | Correct |
| EBITDA margin improved 1.15 bps YoY | M15 1.146834 bps | Correct; small change |
| Revenue fell 20.20% QoQ | M16 −20.204776% | Correct |
| PAT fell 56.74% QoQ | M17 −56.739465% | Correct |

The model asked what caused the PAT and revenue declines. It gave no BUY/HOLD/SELL recommendation, invented evidence ID, or unsupported saved number. The `material_changes` array was empty, though its positives and concerns covered the material comparisons. It did not discuss the available PAT YoY increase of 13.98%. These are completeness weaknesses. Citation and number checks cannot prove all causal or qualitative interpretations, so read model prose alongside its evidence.

Use `POST /api/companies/{symbol}/financials/{period_id}/analyze`, `GET /api/companies/{symbol}/analysis`, and `GET /api/analysis/{id}`. Financial analysis remains manual; event briefs run when opened. Model errors do not interrupt source ingestion. React displays model text as escaped text and links evidence to stored documents or the user supplied market source.
