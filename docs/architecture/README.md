# Almonds architecture

This is a code-grounded snapshot of Almonds at Git revision
`a33715ef34f83bb5fe4f35e3b64e15b38ad7c699`. The application was not imported
or executed to build the inventory, and no environment values or credentials are
included.

## Start here

Open [the interactive system overview](../../.archify/latest/system-overview.html).
Use its guided views to follow the major source, processing, persistence, API,
and UI paths. The other focused Archify views are:

- [Backend architecture](../../.archify/latest/backend-map.html)
- [Frontend to API](../../.archify/latest/frontend-api.html)
- [Database and entities](../../.archify/latest/database-map.html)
- [Processing and intelligence pipelines](../../.archify/latest/processing-pipelines.html)

The diagrams contain 77 components, 89 directed relationships, and 24 guided
views. Archify is the visual entry point. The exact file graph, function calls,
endpoints, and model metadata remain machine-readable so detail is preserved
without turning one visual into an unreadable graph.

## Coverage and notation

| Measure | Result |
| --- | ---: |
| Source files scanned | 172 |
| Application source files | 121 |
| Python/TypeScript functions, methods, or callable constants found | 1,034 |
| Static call expressions found | 10,313 |
| Project file import edges | 560 |
| HTTP endpoints | 58 |
| SQLAlchemy models/tables | 30 |
| Static project import cycles | 0 |
| End-to-end business flows documented | 16 |

Relationship meanings:

- **Direct call**: a call expression was found in the caller's AST or source.
- **Import**: a project-owned module directly imports another project module.
- **API**: frontend code references a route or a FastAPI decorator declares it.
- **Database**: a function queries or mutates a SQLAlchemy model/session.
- **External**: code invokes a configured process, HTTP source, or vector service.
- **Dynamic/inferred**: a runtime protocol, dependency injection, callback, ORM
  relationship, or route contract connects the elements; these are labeled and
  are not represented as proven direct function calls.

The generated inventories are:

- [Exact delivery file manifest](../../.archify/config/delivery-files.txt)
- [Repository inventory](../../.archify/latest/inventory.json)
- [File dependency graph](../../.archify/latest/file-dependencies.json)
- [Function and call-expression graph](../../.archify/latest/function-calls.json)
- [API map](../../.archify/latest/api-map.json)
- [Database model map](../../.archify/latest/database-map.json)

## A. System overview

The React/Vite client calls the FastAPI application over `/api`. FastAPI route
modules call service and domain modules, which read and write PostgreSQL through
SQLAlchemy. Event ingestion obtains disclosures from configured NSE, company IR,
fixture, or manual-upload inputs; document bytes and extracted pages are stored
locally and described in the database. Financial processing converts eligible
result documents into periods, facts, evidence, metrics, and investor reports.
Market snapshots feed deterministic valuation. Commentary, quarterly review,
company memory, and the living thesis use the same stored company evidence.
Ollama provides local model completion and embeddings; Qdrant stores document
vectors. See [main.py](../../backend/app/main.py),
[App.tsx](../../frontend/src/App.tsx), and
[database.py](../../backend/app/database.py).

## B. Backend architecture

The backend has these actual boundaries:

| Boundary | Code | Responsibility |
| --- | --- | --- |
| Application | [main.py](../../backend/app/main.py) | FastAPI lifecycle, ten routers, optional scheduler, health route |
| HTTP routes | [api](../../backend/app/api) | Request validation, transaction boundaries, service orchestration |
| Domain services | [services](../../backend/app/services) | Portfolio, financials, commentary, reports, memory, thesis |
| Ingestion | [ingestion](../../backend/app/ingestion) | Source adapters, polling, normalization, deduplication, PDF storage |
| AI | [ai](../../backend/app/ai) | Evidence packets, prompts, Ollama calls, validation, repair, fallback |
| Market/valuation | [market](../../backend/app/market) | Providers, dated snapshots, deterministic valuation and expectations |
| Persistence | [models](../../backend/app/models), [database.py](../../backend/app/database.py) | SQLAlchemy entities and sessions |
| Contracts | [schemas](../../backend/app/schemas) | Pydantic request/response contracts |

There is no separate repository/data-access package. Routes and services use
SQLAlchemy sessions and `select()` directly. This is recorded as current
architecture, not a proposed redesign.

## C. Frontend architecture

[App.tsx](../../frontend/src/App.tsx) owns the workspace shell, selected company,
navigation, company and portfolio loading, and renders the main screens.
[api.ts](../../frontend/src/api.ts) contains the principal HTTP client and
frontend contracts; [thesisApi.ts](../../frontend/src/thesisApi.ts) contains the
living-thesis client. Vite proxies `/api` and `/health` to FastAPI.

| Screen/component | Principal backend surface |
| --- | --- |
| Overview, Holdings, Activity, Companies in `App.tsx` | companies, transactions, portfolio |
| [EventsView.tsx](../../frontend/src/EventsView.tsx) | events, sources, PDF upload/download/pages, processing |
| [FinancialsView.tsx](../../frontend/src/FinancialsView.tsx) | financial periods/facts/metrics, analysis, quarterly review |
| [ValuationPanel.tsx](../../frontend/src/ValuationPanel.tsx) and [TestedPEPanel.tsx](../../frontend/src/TestedPEPanel.tsx) | market snapshots, tested P/E, valuation/reverse/scenario |
| [CompanyMemory.tsx](../../frontend/src/CompanyMemory.tsx) | memory status/index/query |
| [ThesisPanel.tsx](../../frontend/src/ThesisPanel.tsx) | thesis bootstrap, pillars, expectations, evaluation |
| [InvestorJudgmentReport.tsx](../../frontend/src/InvestorJudgmentReport.tsx) | saved deterministic and AI analysis output |
| [QuarterlyReview.tsx](../../frontend/src/QuarterlyReview.tsx) | evidence-grounded quarterly review within analysis payload |

State uses React component state/effects; no separate global state library was
found. Frontend route strings and response types are maintained in TypeScript
rather than generated from FastAPI schemas.

## D. Database and entity relationships

The 30 ORM models are defined in ten model files and evolved through 18 Alembic
migrations. The central chain is:

`Company -> CompanyEvent -> Document -> DocumentPage`

`Company/CompanyEvent/Document -> FinancialPeriod -> FinancialFact/FinancialMetric`

`FinancialPeriod -> AIAnalysis`, with dated market/valuation, commentary,
checkpoint, memory, and thesis records linked by foreign keys. Exact tables,
foreign keys, visible indexed columns, files, and line numbers are in the
[database inventory](../../.archify/latest/database-map.json).

The table groups are:

- Core and ingestion: `companies`, `company_events`, `event_references`,
  `documents`, `document_pages`, `monitoring_states`, `company_sources`.
- Financials: `financial_periods`, `financial_facts`,
  `financial_fact_corrections`, `financial_metrics`,
  `financial_processing_runs`.
- Market: `valuation_assumptions`, `market_snapshots`, `valuation_snapshots`,
  `reference_metrics`.
- Intelligence: `ai_analyses`, `event_analyses`, `commentary_documents`,
  `management_statements`, `management_guidance`, `investor_checkpoints`,
  `checkpoint_evaluations`, `document_memory_indexes`.
- Thesis: `thesis_pillars`, `thesis_pillar_revisions`,
  `thesis_expectation_revisions`, `thesis_evaluation_runs`,
  `thesis_pillar_evaluations`.
- Portfolio: `transactions`.

## E. API map

All 58 declared HTTP endpoints are preserved with method, full path, handler,
source file, and line in [api-map.json](../../.archify/latest/api-map.json).
Counts by route module are:

| Module | Endpoints | Scope |
| --- | ---: | --- |
| events | 12 | list/detail, upload, source settings, polling, document bytes/pages |
| valuation | 10 | tested P/E, current/history/change, recalculate, scenario, reverse, reference |
| thesis | 8 | current, bootstrap, pillars, expectations, evaluate, impact |
| market | 6 | reference import, snapshots, history, refresh |
| financials | 5 | periods/detail/latest, processing trigger/status |
| analysis | 4 | event and period analysis, list/detail |
| companies | 4 | create/list/detail/delete |
| memory | 3 | status/index/query |
| transactions | 3 | create/list/by symbol |
| portfolio | 2 | all/by symbol |
| application | 1 | `/health` |

API-to-service and service-to-model paths are shown in the backend and frontend
Archify views. Calls made through FastAPI dependency injection and SQLAlchemy are
labeled as framework-mediated rather than direct calls.

## F. File dependency graph

[file-dependencies.json](../../.archify/latest/file-dependencies.json) contains
560 direct project import edges. It records both directions by querying the
edge list: `from` is the importer and `to` is the imported module/file. The
inventory's `high_connectivity` section ranks central modules. Prominent hubs
include financial models, database/session setup, event models, financial
services, and analyst context building. Tarjan analysis found no multi-file
static import cycle in the scanned project graph.

## G. Function call graph

[function-calls.json](../../.archify/latest/function-calls.json) records 1,034
definitions and 10,313 Python call expressions with caller, target text, and
source line. Each Python definition also records parameters, source range, and
statically visible filesystem/database/external side-effect categories.
TypeScript records imported symbol calls and rendered components.

This is a static graph. Protocol dispatch (`EventSource`, market providers,
LLM providers), FastAPI dependency injection, SQLAlchemy relationships,
callbacks, nested TypeScript closures, and property calls cannot always be
resolved to one implementation. They remain textual/dynamic instead of being
promoted to invented direct edges.

## H. External integrations

| Integration | Verified code path | Role/status |
| --- | --- | --- |
| NSE announcements | [nse.py](../../backend/app/ingestion/sources/nse.py) -> [monitor.py](../../backend/app/ingestion/monitor.py) | Live disclosure discovery and document retrieval when enabled |
| Company IR | [company_ir.py](../../backend/app/ingestion/sources/company_ir.py), [esds_ir.py](../../backend/app/ingestion/sources/esds_ir.py) | Adapter boundary plus ESDS-specific implementation |
| BSE | [bse.py](../../backend/app/ingestion/sources/bse.py) | Explicit integration point; verified bulk feed/document retrieval is not configured |
| Manual PDF | [events.py](../../backend/app/api/events.py) | User upload with manual provenance into the canonical event/document pipeline |
| Local files | [storage.py](../../backend/app/ingestion/storage.py) | SHA-256-addressed PDF/document storage |
| NSE market data | [providers.py](../../backend/app/market/providers.py) | NSE MCP Bhavcopy provider |
| Alpha Vantage | [providers.py](../../backend/app/market/providers.py) | Optional configured daily market-data provider |
| Ollama | [ollama.py](../../backend/app/ai/providers/ollama.py), [memory clients](../../backend/app/services/memory/clients.py) | Local Qwen completion and embeddings |
| Qdrant | [memory clients](../../backend/app/services/memory/clients.py) | Company document vector storage/search |
| PostgreSQL | [database.py](../../backend/app/database.py) | Authoritative relational persistence |

The repository contains a user-entered source URL field, including Screener URLs
in saved data, but no verified automated Screener fetcher was found.

## I. Financial results pipeline

1. `check_for_events()` in [monitor.py](../../backend/app/ingestion/monitor.py)
   polls enabled sources, normalizes disclosures, deduplicates references, and
   saves canonical events/documents through
   [event_service.py](../../backend/app/services/events/event_service.py).
2. [downloader.py](../../backend/app/ingestion/downloader.py),
   [storage.py](../../backend/app/ingestion/storage.py), and
   [pdf.py](../../backend/app/ingestion/parsers/pdf.py) acquire content, hash it,
   store it, and persist page text/evidence.
3. `process_financial_event()` in
   [financials/service.py](../../backend/app/services/financials/service.py)
   checks eligibility and uses [extractor.py](../../backend/app/services/financials/extractor.py)
   plus [period.py](../../backend/app/services/financials/period.py) to identify
   reporting period, scope, units, and tabular facts.
4. Facts retain document/page provenance and processing/validation state in
   [financial.py](../../backend/app/models/financial.py). Idempotency is based on
   the canonical event/document and processing records.
5. `calculate_company_metrics()` invokes deterministic calculations in
   [calculator.py](../../backend/app/services/financials/calculator.py) for
   margins and comparisons without replacing missing values with zero.
6. [investor_report.py](../../backend/app/services/investor_report.py) and
   [investor_verdict.py](../../backend/app/services/investor_verdict.py) build
   deterministic evidence and result/valuation judgments.
7. [financials.py](../../backend/app/api/financials.py) exposes periods, facts,
   metrics, and processing status to
   [FinancialsView.tsx](../../frontend/src/FinancialsView.tsx).

The dedicated processing diagram separates deterministic extraction,
calculation, and validation from LLM interpretation.

## J. AI and LLM pipeline

`analyze()` in [analyst/service.py](../../backend/app/ai/analyst/service.py)
ingests commentary, builds a bounded context in
[context_builder.py](../../backend/app/ai/analyst/context_builder.py) and
[quarterly_packet.py](../../backend/app/ai/analyst/quarterly_packet.py), applies
the instructions in [prompts.py](../../backend/app/ai/analyst/prompts.py), and
calls `OllamaProvider`. Pydantic schemas, deterministic validators, evidence
checks, repair prompts, and [fallback.py](../../backend/app/ai/analyst/fallback.py)
control the returned analysis before `AIAnalysis` persistence. Deterministic
Python report and valuation verdicts remain authoritative; prompts explicitly
tell the model not to replace them.

Event summaries have a smaller parallel pipeline under
[ai/events](../../backend/app/ai/events). Company memory chunks saved documents,
uses Ollama embeddings, stores vectors in Qdrant, and returns cited retrieval
results through [memory/service.py](../../backend/app/services/memory/service.py).

## K. Valuation pipeline

[market/service.py](../../backend/app/market/service.py) chooses and persists a
dated provider or manual market snapshot. `calculate_valuation()` in
[market/valuation.py](../../backend/app/market/valuation.py) combines an eligible
same-scope financial period, TTM/annualized facts, EPS/EBITDA, shares and price
with explicitly stored assumptions. It computes P/E, P/S, earnings yield,
tested P/E scenarios, reverse required EPS CAGR, and valuation verdict inputs.
[valuation.py](../../backend/app/api/valuation.py) persists/retrieves snapshots
and exposes scenario/reverse calculations to the valuation panels. Assumptions
are labeled as assumptions rather than facts or forecasts.

## L. Portfolio pipeline

`POST /api/transactions` in
[transactions.py](../../backend/app/api/transactions.py) calls
`record_transaction()` in [portfolio.py](../../backend/app/services/portfolio.py).
The service validates BUY/SELL quantities and prevents overselling before saving
`Transaction`. `calculate_position()` replays dated transactions to derive
quantity, average cost, invested amount, and realized values;
`get_positions()` supplies the portfolio routes. `App.tsx` loads the portfolio,
transactions, and company data and renders holdings/activity. Current market
value depends on the available dated price path rather than being written into
transactions.

## M. Event pipeline

- **LIVE**: enabled NSE and configured company IR adapters are constructed by
  ingestion source selection and polled by `check_for_events()`.
- **FIXTURE**: [fixture.py](../../backend/app/ingestion/sources/fixture.py)
  supplies deterministic non-live records when configured.
- **MANUAL**: `upload_company_pdf()` accepts a disclosure classification and
  stores manual provenance while reusing event/document processing.
- **BSE**: the source class exists but explicitly reports that a verified feed
  and retrieval path are not configured.

Normalization and deduplication converge before `CompanyEvent`, `EventReference`,
`Document`, and `DocumentPage` persistence. Event type controls downstream
eligibility; source provenance does not become document type or trust status.

## N. Management commentary and quarterly review

[commentary.py](../../backend/app/services/commentary.py) identifies eligible
documents, associates them with a financial period, extracts attributed
statements and guidance with page evidence, and persists commentary records.
[materiality.py](../../backend/app/services/materiality.py) ranks evidence;
[checkpoints.py](../../backend/app/services/checkpoints.py) maintains testable
management commitments and outcomes. [quarterly_review.py](../../backend/app/services/quarterly_review.py)
builds the deterministic review, including material changes, capex/funding,
earnings milestones, guidance history, unusual assumptions, and evidence gaps.
`quarterly_packet.py` creates the bounded model packet; prompt, schema,
validation, targeted repair, persistence, and UI exposure then follow the same
analyst pipeline described above. Management claims remain attributed claims
until later evidence validates them.

## O. Background and scheduled processing

The FastAPI lifespan in [main.py](../../backend/app/main.py) starts an
`AsyncIOScheduler` only when configured. It schedules event checks and weekday
market refreshes. The same event check and market refresh paths can also be
triggered through HTTP. PDF financial processing and AI analysis are request
triggered or invoked by the ingestion/service chain; no separate task queue or
worker process was found. This means scheduler multiplicity depends on how many
web application processes start the lifespan.

## P. End-to-end business flows

The interactive views and inventories cover these 16 verified flows:

1. **Add company**: `App.tsx -> POST /api/companies -> create_company -> Company`.
2. **Buy stock**: UI -> transaction API -> `record_transaction` -> `Transaction` -> portfolio reload.
3. **Sell stock**: UI -> transaction API -> oversell validation -> `Transaction` -> recalculated position.
4. **Fetch NSE announcements**: scheduler/manual check -> `check_for_events` -> `NSESource` -> normalize/dedupe/store.
5. **Discover financial result**: source disclosure -> event classification -> `FINANCIAL_RESULTS` event.
6. **Download/process result PDF**: source/manual bytes -> downloader/hash/storage -> `Document` and pages.
7. **Extract financial facts**: process endpoint/ingestion -> `process_financial_event` -> period/scope/units/facts/evidence.
8. **Calculate financial metrics**: verified facts -> calculator -> stored `FinancialMetric` records.
9. **Generate result assessment**: metrics/evidence -> investor report/verdict -> API/UI and analyst context.
10. **Generate valuation assessment**: market snapshot + eligible same-scope financials -> valuation snapshot/verdict -> UI.
11. **Management commentary analysis**: source document -> statements/guidance -> materiality/checkpoints/review -> AI validation.
12. **Manual event/PDF**: upload UI -> upload API -> manual provenance -> canonical event/document -> eligible downstream processor.
13. **Portfolio calculation**: transaction ledger -> position replay -> portfolio response -> holdings screen.
14. **Frontend workspace loading**: selected company/navigation -> API client calls -> route/services/database -> panels.
15. **Company memory**: saved document/pages -> chunk/embed/index -> Qdrant -> cited query response -> memory UI.
16. **Living thesis**: saved analysis/review -> bootstrap or refresh -> pillars/expectations/evaluation history -> thesis UI.

## Q. Dependency and coupling analysis

- The static import graph has no detected cycles.
- `App.tsx`, `FinancialsView.tsx`, `api.ts`, analyst validation/context modules,
  financial services, and valuation modules are the largest/high-connectivity
  files. Exact line counts and connection ranks are in `inventory.json`.
- Route modules and services both access SQLAlchemy directly, so persistence
  concerns cross the HTTP/domain boundary.
- Frontend contracts duplicate backend schemas by hand in a broad API module.
- Provider interfaces create useful runtime boundaries, but their concrete
  target cannot always be selected statically.
- A zero incoming-import count does not establish dead code: entry points,
  Alembic migrations, tests, framework decorators, and dynamic imports can all
  be reached outside static project imports. No module is labeled dead from this
  scan alone.

## R. Architecture risk report

### Critical

No critical architecture issue was established by this static review.

### High

- **Large UI and contract hubs:** `FinancialsView.tsx` (1,679 lines), `App.tsx`
  (1,442), and `api.ts` (1,019) combine many screen states/contracts and create
  broad change impact.
- **Large analysis control modules:** `validator.py` (958 lines),
  `context_builder.py` (791), and the financial/valuation service modules carry
  many policy branches. Their tests are especially important because they sit
  between evidence, deterministic verdicts, and model output.

### Medium

- **Persistence boundary coupling:** routes and domain services issue direct ORM
  operations; there is no repository layer to isolate query/storage changes.
- **Web-process scheduler ownership:** every configured FastAPI process can run
  lifespan jobs. A multi-worker deployment needs an external single-owner rule
  to avoid duplicate polling/refresh work, even where downstream dedupe exists.
- **Frontend/backend contract drift:** manually repeated TypeScript and Pydantic
  shapes can diverge without a generated schema or contract test.

### Low

- BSE and generic company IR are explicit extension points with unavailable
  default retrieval, so diagrams must not imply they are active feeds.
- Static call analysis cannot resolve all provider, ORM, dependency-injection,
  and React closure calls. These are documented as dynamic relationships.
- Several result paths deliberately fall back when Ollama/Qdrant/providers are
  unavailable; operational observability determines how visible those degraded
  modes are.

### Informational

- No authentication/authorization subsystem was found in the scanned source.
  The UI labels the application as a local workspace; exposure beyond that
  trust boundary would require a separate security decision.
- No static project import cycle was found.
- No independent background queue/worker was found.

## Validation performed

All five Archify artifacts passed `validate`, `deliver`, `check`, and headless
`browser-check` at showcase quality. Source references were validated against
the pinned repository revision. Spot checks covered React -> API -> service ->
database, NSE/manual source -> event/document -> financial extraction -> facts
-> metrics -> UI, facts/market snapshot -> valuation -> verdict, and commentary
-> evidence packet -> Ollama -> validator -> saved analysis. All referenced
files and named functions in those paths exist.

Automated route review reports a small number of shared-node crossovers and
long detours in the frontend, database, and processing diagrams. One allowed
position-only repair was attempted for each affected diagram; the retained
versions pass all gates and the remaining automated route hints are preserved
in the validation receipts. No perceptual/manual browser inspection is claimed.

## Regeneration

Prerequisites are Node.js, Python 3, and a Chrome/Chromium executable available
to Archify. From the repository root run:

```bash
./scripts/generate-architecture.sh
```

The script reruns the deterministic inventory and finalizes every candidate at
showcase quality. Set `ARCHIFY_CHROME=/absolute/path/to/chrome` if Archify cannot
discover a browser. After application changes, first commit the source snapshot,
then update each candidate's `meta.repository.revision` and source reference
lines to that commit before regeneration; Archify intentionally rejects stale
or uncommitted source evidence.

The scanner is [inventory.py](../../.archify/tools/inventory.py), candidate
specifications are in [`.archify/latest/candidates`](../../.archify/latest/candidates),
configuration is in [manifest.json](../../.archify/config/manifest.json), and
pre-layout-review backups are retained in [`.archify/history`](../../.archify/history).

## Capability confirmation

- **FILE -> FILE:** yes, all scanned project imports are in `file-dependencies.json`.
- **FUNCTION -> FUNCTION:** yes for statically visible calls, with unresolved
  runtime dispatch explicitly limited in `function-calls.json`.
- **FLOW -> FLOW:** yes, 16 business flows are linked through focused guided views.
- **FRONTEND -> API -> SERVICE -> DATABASE:** yes, through the frontend/backend
  views plus endpoint, call, and model inventories.
- **EXTERNAL SOURCE -> INGESTION -> PROCESSING -> DATABASE -> API -> UI:** yes,
  through the system and processing views with source-backed references.

Archify does not natively render a useful interactive 10,313-edge function call
graph or 560-edge file graph at this repository size. The JSON inventories are
the closest lossless code-grounded artifacts and are deliberately supplementary
to the five readable Archify diagrams.
