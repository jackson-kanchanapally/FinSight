# Phase 7 — Company Memory + RAG

Validation completed on 4 October 2026 against the existing local corpus.

## Architecture

Existing `Document` / `DocumentPage` records → investment-aware chunks → local Ollama embeddings → an Almonds Qdrant collection → one reusable retrieval service → the existing evidence registry and Qwen analyst.

PostgreSQL remains authoritative for financial facts, periods, metrics, valuation and guidance. The only new table, `document_memory_indexes`, records active indexing revisions and status. Qdrant contains source passages and document metadata; it does not own structured financial records. Retrieval reads matching structured evidence directly from PostgreSQL and surfaces detected guidance/source discrepancies.

Migration **0015_company_memory** was applied locally. The existing Qdrant instance was reused with a new collection. Compose also provides a Qdrant service with a named storage volume for fresh setups. The existing instance has no mounted volume; its vectors are rebuildable from PostgreSQL pages if that container is removed.

## Embeddings, collection and chunking

- Model: **`nomic-embed-text:latest`**, already installed locally; **768 dimensions**, cosine similarity. Document/query prefixes distinguish the two embedding tasks. No paid API.
- Collection: **`almonds_company_memory_v1`**. UUID point IDs derive from document ID, content/configuration revision, page and chunk ordinal.
- Chunker: **`investment_sections_v4`**. Preserves page boundaries, explicit headings, wrapped questions and speaker turns; packs nearby short paragraphs; splits oversized units at rows, sentences or words. Table chunks repeat available source headers. Standard signatures and generic legal/audit boilerplate are suppressed; explicit audit qualifications and emphasis passages are retained.
- Defaults: 1,600 characters per chunk; up to 160 characters of complete-sentence/line overlap only within oversized units; 2,400 passage-text characters per query. Analyst context can trim passages further to preserve structured facts.
- Metadata: company/document/event/page IDs, document type, title, publication time and precision, fiscal year/quarter where established, scope where explicit, section, speaker label, heuristic topics, source, ingestion origin, source references, PDF SHA and indexing revision. Unknown metadata remains null.
- Filtering: mandatory company; optional publication-date range, `as_of_date`, document type, fiscal year, quarter, topic and scope. Publication-day cutoffs use Asia/Kolkata. Topic cues from a research question help avoid irrelevant disclosures. Same-date near duplicates are suppressed; equivalent passages on distinct dates remain available for historical comparison.

Every hit is reconstructed and checked against the current PostgreSQL document/page and active revision. Changed or misattributed Qdrant payloads are rejected. New revisions are activated only after all vectors are stored; cleanup runs under the per-document writer lock. Failed indexing remains visible and can be retried. Unchanged indexing and forced reindexing use the same IDs.

## Actual indexed corpus

| Company | Existing document type | Documents | Chunks |
|---|---|---:|---:|
| ESDS | FINANCIAL_RESULTS | 1 | 41 |
| ESDS | EARNINGS_CALL — invitation, not a transcript | 1 | 10 |
| ETERNAL | FINANCIAL_RESULTS — manual upload with shareholder commentary | 1 | 76 |
| **Total** | | **3** | **127** |

No synthetic production documents or financial facts were added. Reindexing all three unchanged documents returned `UNCHANGED`, retaining **127** Qdrant points. ETERNAL retains `MANUAL_UPLOAD / MANUAL` provenance.

## Retrieval examples and temporal safety

- **ETERNAL, margin commentary:** page **4**, attributed Deepinder response, relevance **0.693753**. Source reference `C171f854a-d949-52d0-aa88-ee814c090053`.
- **ETERNAL, strategy:** page **6**, attributed Albinder response on expansion and competitive positioning, relevance **0.706102**. Reference `Cabdc64b1-5d3a-5b24-825f-68abc27ec5fd`.
- **ETERNAL, disclosed risk:** page **23**, auditor emphasis referring to Note 5, relevance **0.635945**. Reference `C9320d803-4ad3-5c30-bbc4-37f09597b948`.
- **ESDS, past guidance:** `NO_MATCHES`. Its available call document is an invitation and is not treated as a record of management promises. Margin queries return source expense tables, which establish reported values, not their business causes.

An `as_of_date` before the existing filings returned no evidence for either company. Tests also cover inclusion on the exact publication day, exclusion before it, future-document exclusion, and company isolation even when a test vector store deliberately ignores its filters. Historical guidance outcomes remain unavailable until matching annual actuals have been published; quarterly and other-scope data cannot establish delivery against an annual target.

## Analyst and UI integration

Results analysis retrieves a compact selection of earlier documentary context, guided by current operating signals and reverse-expectations needs. It excludes the current results document from historical retrieval and cuts off at the results event's publication date. Explicit other-scope passages are excluded.

`C…` passages and `G…` PostgreSQL guidance records join the existing evidence registry. Source numeric text is omitted from the model excerpt; authoritative values come from structured evidence. Historical interpretation includes an exact supporting source quote. Unknown citations, invented quotes and unsupported assertions of achieved/missed guidance fail validation. Repeated-outcome claims require distinct target periods. Existing Python calculations and verdicts remain authoritative, and the existing validated fallback remains usable without Qdrant or embeddings.

The **Company Memory** view provides index/update, research search, date/type/topic filters, source passages, provenance, page links and status. AI Analysis displays retrieved historical passages and any validated historical interpretation. Search itself returns evidence rather than a separate chatbot answer, keeping retrieval failures distinct from generation failures.

The current corpus does **not** contain an earlier results commentary/transcript for either company's current results analysis. Current-document research search works; cross-period analyst integration and guidance execution are verified with isolated test data. More historical source documents are needed for real longitudinal conclusions.

## APIs and operation

- `GET /api/companies/{symbol}/memory`: retrieval and per-document index status.
- `POST /api/companies/{symbol}/memory/index`: index/update; `?force=true` re-embeds with stable IDs.
- `POST /api/companies/{symbol}/memory/query`: bounded research retrieval and source inspection in one response.

Example request body:

```json
{"query":"What explained margin changes?","as_of_date":"2026-09-30","topic":"MARGINS","limit":5}
```

For a fresh setup, run `alembic upgrade head` from `backend`, start Qdrant with `docker compose up -d qdrant` (or configure an existing instance), and run `ollama pull nomic-embed-text`. Use **Index / update documents** after new documents are parsed. Configuration is documented in `backend/.env.example`. No automatic crawling or autonomous agents were added.

The adapters follow the official [Ollama embedding API](https://docs.ollama.com/api/embed) and [Qdrant filtering API](https://qdrant.tech/documentation/search/filtering/).

## Tests and limitations

- Baseline: **166 passed, 4 skipped**.
- Final backend: **186 passed, 5 skipped**; includes **20** focused memory tests.
- Opt-in real local embeddings/Qdrant evaluation: **passed**, all four fixed research questions retrieved the expected document first. A disposable evaluation collection and SQLite facts keep this separate from production and from Qwen generation.
- Existing ESDS/ETERNAL PostgreSQL regressions: **2 passed**.
- Frontend TypeScript/production build: **passed**.
- Browser: **passed** for live retrieval, manual provenance, date exclusion, company switching, mobile containment and no page errors. Screenshots: `frontend/e2e-artifacts/phase7-memory-{desktop,mobile}.png`.
- `git diff --check`: **passed**.
- `alembic check`: **reports a pre-existing constraint-name mismatch** between `management_guidance_statement_id_key` (migration 0012) and `uq_management_guidance_statement` (existing model). No Phase 7 table drift was reported; unrelated schema was not changed.

```sh
cd backend
.venv/bin/pytest -q
RUN_MEMORY_LIVE=1 RUN_PHASE63_DB_REGRESSION=1 .venv/bin/pytest tests/test_company_memory_live.py tests/test_phase63_live.py -q
cd ../frontend
npm run build
node scripts/memory-check.mjs
```

Chunk boundaries, topics and candidate selection are deterministic heuristics; PDF extraction quality and corpus coverage limit recall. Guidance conflict detection is conservative and currently compares safely identified guidance bounds against existing structured records; it is not a universal semantic contradiction detector. Execution comparison reuses the existing annual revenue/PAT guidance rules. No new live Qwen-generation success is claimed: provider integration, citation validation and graceful fallback are tested independently of retrieval. Model output remains subject to the existing strict validation.
