# Local AI analyst

Opening any event now requests a short, cached local analysis from `POST /api/events/{id}/analyze`. It summarizes the disclosure, explains why it matters, suggests a research action (`WATCH`, `INVESTIGATE`, `NO_ACTION`, or `INSUFFICIENT_EVIDENCE`), and says what to watch next. Management statements appear only when the cited event text or stored PDF excerpt supports them. The event brief links every field to its source and rejects unknown citations, unsupported numbers, and trading calls. A failed or unavailable local model leaves the original event intact and shows the failure in the UI. Event analysis runs when an event is opened, so the event list does not launch a model call for every filing at once.

The Financials view accepts a user entered closing price, optional P/E and market cap, observation date, and HTTPS source URL through `PUT /api/companies/{symbol}/market-snapshot`. A snapshot older than seven calendar days is displayed as stale and excluded from new AI valuation context. These values are not a live feed. NSE publishes [official daily market reports](https://www.nseindia.com/all-reports), but its [website terms](https://www.nseindia.com/static/nse-terms-of-use) prohibit systematic automated collection. FinSight therefore does not scrape prices from its quote pages or reports. The model can compare a recent sourced price and P/E with verified results, while noting that a single quarter and a supplied P/E cannot establish intrinsic value.

The analyst reads a `VALIDATED` financial period. `context_builder.py` includes verified facts, deterministic metrics with their input fact IDs, and short relevant lines from the already stored PDF pages. It excludes `NEEDS_REVIEW` facts and comparison periods. It never fetches a website or sends a full PDF. The bounded JSON context is hashed; the hash, evidence registry, prompt version, model name, output, status, and failure reason are saved in `ai_analyses`.

`LLMProvider.generate(system, user, schema)` is the only model boundary. `OllamaProvider` uses the local `/api/chat` endpoint with structured format, streaming disabled, thinking disabled, configurable timeout and temperature. The analyst service has no model-specific logic. Other providers can implement the interface without changing finance code.

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

The current financial schema also includes `management_commentary` when explicitly reported, a conditional `what_might_happen` note, `valuation_assessment`, and a cited research action. Arrays may be empty. Each claim and material change needs at least one citation. FinSight creates stable `F` fact, `M` metric, `D...P...` PDF-page, and `Q` market-snapshot IDs. Validation rejects unknown IDs, numbers that cannot be matched to supplied values at 0–4 decimal places, HTML/links, extra JSON fields, and BUY/HOLD/SELL calls. Citation checks establish that an ID was supplied; they do **not** prove that the prose faithfully describes that source. A number check catches unsupported figures, not every possible semantic error. Human review remains important.

`backend/app/ai/evaluation/esds.py` emits inspectable pass/fail checks for revenue, PAT, EBITDA margin, improvement, deterioration, missing information, citations, numeric grounding, scope, and recommendations. The optional Ollama integration test prints this report and fails when checks fail. `pytest` uses fake provider responses and stays fully offline.

## Historical V1 ESDS acceptance run (27 September 2026)

The stored live ESDS consolidated Q1 FY2027 period (period ID 4) was analyzed with local `qwen3:8b` and `FINANCIAL_ANALYST_V1`. The first response mentioned `56.74%` without its matching citation and was rejected. Targeted repair produced a successful second response, saved as analysis ID 4. The deterministic evaluation passed **14/14** checks. Manual comparison found:

| AI claim | FinSight evidence | Audit |
| --- | --- | --- |
| Revenue ₹133.657 crore and PAT ₹29.277 crore | F26 and F38, official PDF page 7 | Exact |
| PAT margin 21.90%, EBITDA margin 43.03% | M11 21.904577%, M12 43.028798% | Rounded correctly |
| Revenue rose 7.28% YoY | M13 7.280053% | Correct |
| EBITDA margin improved 1.15 bps YoY | M15 1.146834 bps | Correct; small change |
| Revenue fell 20.20% QoQ | M16 −20.204776% | Correct |
| PAT fell 56.74% QoQ | M17 −56.739465% | Correct |

The model asked what caused the PAT and revenue declines. It gave no BUY/HOLD/SELL recommendation, invented evidence ID, or unsupported saved number. The `material_changes` array was empty, though its positives and concerns covered the material comparisons. It did not discuss the available PAT YoY increase of 13.98%. These are completeness weaknesses. Citation and number checks cannot prove all causal or qualitative interpretations, so read model prose alongside its evidence.

Use `POST /api/companies/{symbol}/financials/{period_id}/analyze`, `GET /api/companies/{symbol}/analysis`, and `GET /api/analysis/{id}`. Financial analysis remains manual; event briefs run when opened. Model errors do not interrupt source ingestion. React displays model text as escaped text and links evidence to stored documents or the user supplied market source.
