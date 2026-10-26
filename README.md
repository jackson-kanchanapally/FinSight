# almonds

**almonds** is a self-hosted investment research and portfolio monitor. It combines transaction-based portfolio accounting with company disclosure monitoring, financial statement extraction, valuation tools, and an optional local AI research assistant.

The application is designed for a trusted local environment. Several API endpoints do not require authentication; do not expose the service to the public internet without adding and reviewing suitable access controls. The software provides research tools, not investment advice.

## Features

- Create and list companies, with case-insensitive symbol normalization to uppercase.
- Record BUY and SELL transactions and retrieve dated transaction history.
- Calculate open positions, weighted average acquisition price, remaining cost basis, and realised P&L.
- Reject a sale when its quantity exceeds the available shares.
- Use PostgreSQL, SQLAlchemy 2, Pydantic 2, and Alembic migrations.
- View holdings, cost basis, realised P&L, allocation, transaction history, and registered companies in a dark-themed React dashboard.
- Add or delete companies and record BUY/SELL transactions through the dashboard.
- Monitor disclosures for registered NSE companies in live mode, using manual checks or an optional scheduler; fixture mode remains tied to open positions.
- View classified events, source references, PDF documents, and processing status in the Events section.
- Store PDFs by SHA-256 and extract embedded text with one-based page numbers.
- Read the official NSE announcements RSS feed and ESDS investor announcements in live mode; label live, fixture, and manual records by origin.
- Extract financial statement facts from stored official PDFs with period, scope, units, page provenance, and validation status.
- Calculate EBITDA, margins, YoY and QoQ in Python, then inspect them in the Financials section.
- Open any event for a short local AI brief with source citations and a research action. Financial results have a deeper analysis of metrics, management commentary where reported, possible scenarios, and valuation context.
- Store dated official NSE MCP EOD OHLCV or manual observations, calculate point-in-time valuation from verified facts, inspect price and multiple history, compare imported Market Lens references, and run forward or reverse user-defined scenarios. The results analyst uses a snapshot only while it is at most seven days old.

Accounting uses weighted average cost. BUY charges are added to acquisition cost. SELL charges are deducted from proceeds and therefore reduce realised P&L. A SELL removes cost basis at the current weighted average; it does not change the per-share cost of remaining shares. Values use decimal numeric types, never binary floating point.

## Architecture

```text
registered NSE companies in live mode; open positions in fixture mode
  -> configured EventSource adapter (one feed fetch per source)
  -> validated SourceEvent
  -> company match + deterministic classification
  -> normalized event
  -> source identity / PDF hash deduplication
  -> PostgreSQL events, source references, documents, numbered document pages
  -> FastAPI event and document endpoints
  -> React Events list and details

LIVE financial-results event + verified PDF
  -> scope/period/table-column extraction
  -> validated financial periods and source facts
  -> deterministic Python metrics and comparisons
  -> FastAPI financial endpoints and React Financials section

validated period + verified facts + Python metrics + relevant stored PDF lines
  -> bounded context with evidence IDs
  -> local Ollama via LLMProvider
  -> structured JSON, citation and number validation
  -> separate ai_analyses record
  -> Financials AI Analysis panel
```

`EventSource` isolates feed formats and document retrieval from the rest of almonds. The monitor records the last attempt, last successful check, last external ID, error, and failure count per source. A failed source does not stop other sources. Each source observation retains its URL, external ID, origin, and raw payload in `event_references`; matching PDFs merge into one company event while retaining both source references. Different PDFs remain separate even when titles and dates match. Local PDF storage uses content-addressed paths behind a replaceable storage interface.

`MarketDataProvider` isolates price acquisition from valuation. The preferred official NSE MCP Bhavcopy provider retrieves raw daily OHLCV; manual and optional Alpha Vantage providers remain available. Dated observations are stored in `market_snapshots`; the Python engine combines eligible verified facts into `valuation_snapshots`. The AI interprets these calculated values and cites their evidence IDs. User-exported NSE Market Lens metrics are stored separately in `reference_metrics`.

Portfolio calculations and SELL validation remain in the portfolio service. Alembic owns schema changes; the application does not call `create_all()` at startup.

The default `FIXTURE` source replays four **invented ESDS disclosures** and generates one two-page PDF. They are labeled as demo fixtures in the UI and are not real filings. Live mode polls the official NSE announcements RSS feed for every registered NSE-listed company and the public ESDS investor-relations Announcements section for ESDS. NSE's official equity and SME securities CSVs supply exact issuer names for symbol matching. The BSE adapter is disabled because a suitable documented bulk feed was not verified; the generic company IR adapter is a placeholder. Live mode never substitutes fixture records after a source error. See [the source review](docs/data-sources.md) for exact URLs, fields, matching rules, and limitations. The downloader supports HTTPS host allowlists, timeouts, bounded size, limited retries, MIME and PDF signature checks, and no redirects. There is no generic news scraping or OCR. AI interpretation runs only when manually requested for a validated period.

## Prerequisites

- Python 3.11 or newer
- Docker with Docker Compose
- Node.js 20.19+ or 22.12+ and npm

## Local setup

From the project root:

```sh
cp backend/.env.example .env
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
docker compose up -d db
```

The example credentials are for local development only. Keep `.env` private; it is ignored by Git. To connect to an existing database, edit the `DATABASE_*` values in `.env`.

Run migrations and start the API from the backend directory:

```sh
cd backend
alembic upgrade head
alembic current
uvicorn app.main:app --reload
```

Swagger UI: <http://127.0.0.1:8000/docs>. Health check: <http://127.0.0.1:8000/health>.

The event migrations are 0002 and 0003; Phase 4 adds 0004_financial_reporting and 0005_calculated_fact_provenance; Phase 5 adds 0006_local_ai_analyst. Event briefs and dated market snapshots add 0007 and 0008; sourced forward EPS growth for PEG adds 0009. Phase 6 adds 0010_market_valuation_history and 0011_market_ohlcv_reference. Phase 6.2 adds 0012_management_commentary and 0013_commentary_documents. Run `alembic upgrade head` against the configured PostgreSQL database before starting the API.

## Start the dashboard

After completing local setup, run both servers from the project root with:

```sh
./run.sh
```

The launcher checks the existing `.env`, Python and frontend dependencies, starts PostgreSQL through Docker Compose if needed, applies Alembic migrations, and starts FastAPI and Vite. Press Ctrl+C to stop the application servers. Event scanning follows the settings in `.env`; the launcher does not change fixture or live mode.

For Phase 6 provider setup, point-in-time rules, formulas, API examples and ESDS integration, see [market data and valuation](docs/market-valuation.md).

For Phase 6.2 commentary, deterministic verdicts, tested P/E scenarios, and the ESDS audit, see [the Phase 6.2 report](docs/phase62-report.md).

Leave FastAPI running on port 8000. In a second terminal from the project root:

```sh
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The Vite development server forwards `/api` requests to FastAPI on `127.0.0.1:8000`, so no browser CORS change is needed for local development. For a production build, run `npm run build`; serve `frontend/dist` and route `/api` to FastAPI on the same origin.

Start by adding a company, then record a BUY. Current holdings and allocation are based on remaining cost basis. Realised P&L includes charges and closed positions; no live market values are shown.

## Try the event pipeline

Add ESDS in the dashboard and record a BUY so it has an open position. Then select **Events → Check for events**, or run:

```sh
curl -X POST http://127.0.0.1:8000/api/events/check
curl 'http://127.0.0.1:8000/api/events?symbol=ESDS'
```

In default fixture mode, the first check creates four events and stores one generated PDF with two numbered text pages. The next check reports `new_events: 0`. Select an event to view its source trail, PDF status, hash, and document link. These fixtures do not contain a real future meeting date, so the UI has no Upcoming Events section yet.

## Live ESDS setup

First register ESDS using the company API or dashboard. An open position is **not** required for NSE monitoring. Set these values in the root `.env`, then restart FastAPI:

```dotenv
EVENT_SOURCE_MODE=live
NSE_SOURCE_ENABLED=true
BSE_SOURCE_ENABLED=false
COMPANY_IR_SOURCE_ENABLED=true
EVENT_MONITOR_ENABLED=false
```

Add the ESDS IR mapping once. NSE monitoring now resolves the issuer name automatically from its official securities CSV, so its mapping is optional. The ESDS PDF identifies BSE code `544898` and NSE symbol `ESDS`.

```sh
curl -X POST http://127.0.0.1:8000/api/companies/ESDS/sources \
  -H 'Content-Type: application/json' \
  -d '{"source":"ESDS_IR","external_symbol":"ESDS","source_company_id":"544898","ir_url":"https://www.esds.co.in/investor"}'

curl -X POST http://127.0.0.1:8000/api/events/check
curl 'http://127.0.0.1:8000/api/events?symbol=ESDS'
curl http://127.0.0.1:8000/api/events/sources/status
```

If the ESDS IR mapping already exists, use `PUT /api/companies/ESDS/sources/ESDS_IR` with the same JSON body. With the public page observed on 27 September 2026, the first check found two ESDS IR announcements, including the eight-page Q1 FY27 results PDF. The NSE feed was reachable but had no matching ESDS item in its current window. A repeated check reported no new events or PDF downloads. Current feed contents may differ when you run this.

The source-check response has per-source counts and errors. `GET /api/events/sources/status` shows each source's last attempt, success, and failure. The Events UI shows a **LIVE** origin badge, source URLs, PDF hash, page count, and a date-only label when the source publishes no clock time. The PDF letter date is **not** treated as an exact publication timestamp. Results metadata includes the reporting period, fiscal year, quarter, and standalone/consolidated basis when found in the official PDF.

To check up to the preceding 90 days of the ESDS Announcements section on demand, use the controlled bootstrap. It has a five-minute cooldown per symbol and does not bypass source validation:

```sh
curl -X POST http://127.0.0.1:8000/api/events/bootstrap \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"ESDS","days":90}'
```

You can inspect the stored records directly in PostgreSQL:

```sh
docker compose exec db psql -U finsight -d finsight -c \
  "SELECT id, title, data_origin, publication_precision, period_end_date, fiscal_year, quarter FROM company_events WHERE company_id = (SELECT id FROM companies WHERE symbol = 'ESDS') ORDER BY id;"

docker compose exec db psql -U finsight -d finsight -c \
  "SELECT d.id, d.sha256_hash, d.processing_status, count(p.id) AS pages FROM documents d LEFT JOIN document_pages p ON p.document_id = d.id GROUP BY d.id ORDER BY d.id;"
```

Use the result event detail's `documents[].download_url` to retrieve the stored PDF through the API. Replace `1` below with that document ID if yours differs:

```sh
curl http://127.0.0.1:8000/api/documents/1/file -o /tmp/esds-results.pdf
shasum -a 256 /tmp/esds-results.pdf
curl http://127.0.0.1:8000/api/documents/1/pages
```

Compare the hash with `documents.sha256_hash` and confirm the pages response has numbered text pages. The downloaded file is served from content-addressed local storage. The ESDS source page, PDF and exchange feed URLs are documented in [data-sources.md](docs/data-sources.md).

## Financial results

Phase 4 processes a stored **LIVE FINANCIAL_RESULTS** PDF. For existing ESDS event 1, select **Process financial results** in its event detail or call:

```sh
curl -X POST http://127.0.0.1:8000/api/financials/process/1
curl http://127.0.0.1:8000/api/companies/ESDS/financials/latest
curl http://127.0.0.1:8000/api/companies/ESDS/financials
curl http://127.0.0.1:8000/api/financials/process/1
```

The event ID can differ on a fresh database. Processing the same event again returns `periods_created: 0`. Newly downloaded live financial-result PDFs are processed automatically after event ingestion; extraction errors do not roll back the event. Open **Financials** in React to switch between consolidated and standalone periods. Click any displayed number to see its PDF, page, source row, original unit or Python formula and input facts. `GET /api/companies/{symbol}/financials/{period_id}` returns one period with full provenance; `GET /api/companies/{symbol}/financials/latest?scope=STANDALONE` selects the latest validated standalone period.

The database has separate `financial_periods`, `financial_facts`, `financial_metrics` and `financial_processing_runs` tables. Period identity includes company, end date, period type and scope. Facts use controlled metric codes and `Decimal` values. Currency totals are normalized to INR crore while original printed values and units are preserved; EPS stays INR per share. Calculated EBITDA is PBT + finance costs + depreciation and amortisation, explicitly labeled calculated because ESDS does not report EBITDA directly. YoY compares the same period last fiscal year, QoQ the immediately prior quarter, always with the same scope and period type. Growth is (current − prior) / abs(prior) × 100; margins divide by revenue; margin differences multiply percentage-point changes by 100 for basis points. Missing comparisons and zero denominators remain unavailable, never 0%.

The ESDS Q1 FY2027 consolidated PDF table on page 7 shows revenue ₹1,336.57 million, PAT ₹292.77 million and basic EPS ₹2.92. almonds stores ₹133.657 crore revenue and ₹29.277 crore PAT, with the PDF page and original values. Current-quarter consolidated revenue YoY is 7.28% and QoQ is −20.20%. The current standalone table is on page 3. Some comparison and annual columns have damaged PDF text and remain `NEEDS_REVIEW`; questionable values are excluded from the displayed history and calculations. `VALIDATED` means table extraction checks passed, **not** that the company statements were audited. See [financial-extraction.md](docs/financial-extraction.md) for the field model, source comparison, formulas, and known limits.

The financial processing endpoint, like the event check, has no authentication and is intended for trusted local use.

`POST /api/events/check` and document endpoints have no authentication. Use this deployment only in a trusted local environment. Add authentication and access control before exposing it to other users or the internet.

### Event settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `EVENT_SOURCE_MODE` | `fixture` | `live` polls configured primary sources; `fixture` replays demo disclosures; `none` disables all sources. |
| `EVENT_MONITOR_ENABLED` | `false` | Enable periodic checks. Manual checks work independently. |
| `EVENT_MONITOR_INTERVAL_MINUTES` | `30` | Scheduler interval when enabled. |
| `NSE_SOURCE_ENABLED` | `true` | Poll official NSE announcements for registered NSE-listed companies in live mode. |
| `BSE_SOURCE_ENABLED` | `false` | Disabled placeholder; no verified bulk feed is implemented. |
| `COMPANY_IR_SOURCE_ENABLED` | `true` | Poll the ESDS investor Announcements section in live mode. |
| `LIVE_SOURCE_TIMEOUT_SECONDS` | `15` | Timeout for live source requests. |
| `DOCUMENT_STORAGE_PATH` | `backend/data/documents` | Local PDF store; downloaded files are ignored by Git. |

The scheduler runs in each API process when enabled, so use one API worker for local scheduling. Source failures are logged and retried on the next cycle; the API remains available.

### Event and source APIs

| Endpoint | Use |
| --- | --- |
| `GET /api/events` | Recent events; filters: `symbol`, `event_type`, `source`, `from_date`, `to_date`, `limit` (1–100). |
| `GET /api/events/{id}` | Event details, source references, and documents. |
| `GET /api/companies/{symbol}/events` | Events for a registered company. |
| `POST /api/companies/{symbol}/events/upload-pdf` | Attach a local PDF as a manual test event. |
| `POST /api/events/check` | One monitoring cycle with counts and source errors. |
| `GET /api/events/sources/status` | Source health, last attempt/success, and last error. |
| `POST /api/events/bootstrap` | Bounded ESDS IR lookback (1–90 days; five-minute cooldown). |
| `GET /api/documents/{id}/file` | Download a stored PDF. |
| `GET /api/documents/{id}/pages` | Read extracted text with one-based page numbers. |
| `GET/POST /api/companies/{symbol}/sources` | Read/add company source mappings. |
| `PUT /api/companies/{symbol}/sources/{source}` | Update a mapping or enable/disable it. |

Company source mappings hold an external symbol, source company ID, IR URL, and enabled flag. For example, `POST /api/companies/ESDS/sources` with `{"source":"FIXTURE","enabled":false}` disables the fixture feed for ESDS. In live mode, every registered company whose exchange contains `NSE` is monitored automatically, with or without an open position or explicit mapping. An explicit NSE mapping can override the issuer name or disable monitoring for that company. Company-specific IR sources still require their own mapping.

The [NSE securities directory](https://www.nseindia.com/static/market-data/securities-available-for-trading) provides the symbol, issuer name, and ISIN needed to match RSS items. For example, the CSV resolves `ETERNAL` to `ETERNAL LIMITED` even if the dashboard company name is just `ETERNAL`. An [official NSE filing](https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_153832_28042026202149_iXBRL_WEB.html) confirms that issuer identity. An optional override can still be set:

```sh
curl -X POST http://127.0.0.1:8000/api/companies/ETERNAL/sources \
  -H 'Content-Type: application/json' \
  -d '{"source":"NSE","external_symbol":"ETERNAL","source_company_id":"ETERNAL LIMITED","enabled":true}'
```

Use `GET /api/companies/ETERNAL/sources` to inspect explicit overrides; an empty list no longer means NSE monitoring is disabled. Use `PUT /api/companies/ETERNAL/sources/NSE` with the same JSON to correct an override. `POST /api/events/check` reports `company_matches` and `unresolved_symbols` if a registered symbol is missing or ambiguous in the official directory, or if its supplied ISIN disagrees. The older `portfolio_matches` response field remains as a compatibility alias. NSE's announcements RSS lists recent filings only, so adding a company may yield no immediate PDF when it has no item in the current feed. The ESDS IR adapter is company-specific and does not crawl Eternal's investor-relations site. `POST /api/events/bootstrap` currently applies only to ESDS.

### Add an ETERNAL PDF manually for testing

Register ETERNAL as a company if it is not already in the dashboard. In **Company events**, open **Upload a company PDF for testing**, choose **ETERNAL**, enter the PDF title and publication date, select its event type, and choose the local file. An HTTPS source URL is optional. The saved manual event opens automatically; its PDF and extracted text pages can be reviewed there. The upload accepts PDFs up to 10 MB and is idempotent by PDF content for that company.

The equivalent API call sends the PDF as the raw request body:

```sh
curl -X POST 'http://127.0.0.1:8000/api/companies/ETERNAL/events/upload-pdf?title=Q2%20FY27%20results&published_on=2026-09-28&event_type=FINANCIAL_RESULTS&file_name=eternal.pdf' \
  -H 'Content-Type: application/pdf' --data-binary '@/absolute/path/to/eternal.pdf'
```

Uploads are labeled `MANUAL` and are available for document review and event analysis. Image-only PDFs show `NO_TEXT` because OCR is not enabled. They do not become verified financial facts or add a share price: the financial extractor accepts live official filings, while the valuation page uses a separate dated market snapshot. Use the live NSE source and **Check for events** when testing the verified financial-results path; use **Refresh EOD price** or enter a dated sourced close for the price input.

## Tests

The automated tests use an isolated in-memory SQLite database, so PostgreSQL does not need to be running for them.

```sh
cd backend
pytest -q
alembic check
```

`alembic check` connects to PostgreSQL and should be run after `alembic upgrade head`. The frontend type and production build check is:

The AI unit and API tests use a fake `LLMProvider`, so the normal suite never needs Ollama. To run the optional real local model check against a seeded ESDS period, run `OLLAMA_TEST_ENABLED=true .venv/bin/python -m pytest -q -s tests/test_ai_live_optional.py` after starting Ollama and PostgreSQL. The check prints a per-question evaluation report.

The optional official-PDF integration check uses a previously downloaded local ESDS PDF and never fetches a live site:

```sh
cd backend
ALMONDS_ESDS_PDF=data/documents/6f/6fffc199c424ce799f656041afa5a4b5e9897104fe769565e5da4c6449e222fa.pdf .venv/bin/python -m pytest -q tests/test_esds_real_pdf.py
```

```sh
cd frontend
npm run build
```

To run the isolated Chromium browser check, first install the browser once:

```sh
cd frontend
npx playwright install chromium
npm run test:browser
```

This starts temporary FastAPI and Vite servers with an in-memory SQLite database, fixture source mode, and temporary document storage. It checks company creation, the ESDS BUY/SELL example, oversell feedback, event ingestion and deduplication, event details and PDF, the dark theme, and mobile layout. It does not write to your PostgreSQL database or contact live sources. Desktop, mobile, and event-detail screenshots are saved in ignored `frontend/e2e-artifacts/`. The Python virtual environment described above must already be installed.

## API examples

Create a company:

```sh
curl -X POST http://127.0.0.1:8000/api/companies \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"ESDS","name":"ESDS Software Solution","exchange":"NSE"}'
```

Record transactions:

```sh
curl -X POST http://127.0.0.1:8000/api/transactions \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"ESDS","transaction_type":"BUY","quantity":"34","price":"429","transaction_date":"2026-09-01","charges":"0"}'

curl -X POST http://127.0.0.1:8000/api/transactions \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"ESDS","transaction_type":"SELL","quantity":"24","price":"1191","transaction_date":"2026-09-10","charges":"0"}'
```

Read transaction history with `GET /api/transactions` or `GET /api/transactions/ESDS`. Read all portfolio positions with `GET /api/portfolio` or one symbol with `GET /api/portfolio/ESDS`. Company endpoints are `GET /api/companies`, `GET /api/companies/ESDS`, and `DELETE /api/companies/ESDS`. Deleting a company permanently removes its trades, event and source records, financial results, market snapshot, and AI analyses. The dashboard asks for confirmation before deletion. Locally stored PDFs are removed when no remaining document references them.

## ESDS expected result

After buying 34 shares at ₹429 and selling 24 at ₹1,191, with no charges:

- Remaining quantity: 10
- Average acquisition price: ₹429
- Remaining cost basis: ₹4,290
- Realised P&L: (₹1,191 − ₹429) × 24 = ₹18,288

## Local AI analyst

Install [Ollama](https://ollama.com/), run `ollama pull qwen3:8b`, and start its local server (`ollama serve` if it is not already running). The default model was chosen from the models installed on this development machine; set `OLLAMA_MODEL` to any compatible local model in `.env`. Set `OLLAMA_BASE_URL`, `OLLAMA_TIMEOUT_SECONDS`, `OLLAMA_NUM_CTX`, `AI_TEMPERATURE`, `AI_MAX_CONTEXT_CHARS`, and `AI_MAX_DOCUMENT_CHARS` there as needed. `OLLAMA_NUM_CTX` defaults to 8192 so the model can see the full results context and citation guide. No cloud model or paid API is used. The local Ollama HTTP endpoint is intended for a trusted local machine.

Open any event to request its short local AI brief automatically. Successful briefs are cached by event content, model, and prompt; **Regenerate** requests another. Open **Financial performance → AI Analysis** for a validated period and select **Analyze Results** for a deeper review. Financial analysis is manual. Results and failures are stored separately from financial facts. The deeper view shows a summary, positives, concerns, a cited positive/negative/neutral view of the results, management statements when found, conditional scenarios, valuation context, research action, and evidence links.

For a valuation comparison, enter a dated closing price, optional source P/E reference and market cap, and an HTTPS source link in **Financial performance**. You may also enter a sourced forward annual EPS growth estimate and its source type. Almonds calculates its own P/E from verified trailing EPS, then calculates forward PEG from that P/E and the growth estimate in percentage points. The source P/E and legacy PEG are reference values only. A snapshot over seven days old is excluded from new AI analysis. The result review explains both annual and sequential performance and treats valuation conclusions as provisional when forecasts, cash flow or balance-sheet inputs are missing.

```sh
curl -X POST http://127.0.0.1:8000/api/companies/ESDS/financials/42/analyze
curl http://127.0.0.1:8000/api/companies/ESDS/analysis
curl http://127.0.0.1:8000/api/analysis/1
curl -X POST 'http://127.0.0.1:8000/api/companies/ESDS/financials/42/analyze?regenerate=true'
curl -X POST http://127.0.0.1:8000/api/events/1/analyze
curl http://127.0.0.1:8000/api/companies/ESDS/market-snapshot
```

Replace `42` with a real validated period ID from `GET /api/companies/ESDS/financials`. Invalid model output is retried once. If both responses fail validation, the API returns `VERIFIED_FALLBACK` with a cited summary assembled from verified facts and calculated metrics; the dashboard labels it and offers **Retry AI analysis**. The last validation reason remains in `failure_reason`. Provider failures return `FAILED` with a reason such as `OLLAMA_UNAVAILABLE` or `OLLAMA_TIMEOUT`. See [the analyst design and evaluation](docs/local-ai-analyst.md) for the schema, provenance rules, and limitations.

## Phase 6.3 investor report

The results report separates business results, scenario valuation and investor stance. It includes a reconciled earnings bridge, reverse required EPS growth and three editable P/E scenarios with explicit provenance. **Save assumptions and update report** uses the existing valuation and analysis APIs; all calculations and final judgments remain deterministic. Model failures preserve a complete report and structured validation diagnostics.

Apply migration `0014_investor_scenarios` with `alembic upgrade head`. See [Phase 6.3 implementation and regression results](docs/phase63-report.md) for the assumptions, ESDS/Eternal outcomes, tests and limitations.

## Phase 7 company memory

Company Memory indexes existing parsed document pages into local Qdrant with `nomic-embed-text` embeddings. PostgreSQL remains authoritative for structured facts and guidance. Company/date filters, source-page references and bounded historical context feed the existing analyst; financial analysis remains usable when retrieval is unavailable.

Apply migration `0015_company_memory` with `alembic upgrade head`. Start a local Qdrant instance (`docker compose up -d qdrant` for a fresh setup), run `ollama pull nomic-embed-text`, then select **Company Memory → Index / update documents**. The existing local Qdrant instance can be reused through `QDRANT_BASE_URL`; do not start a second service on the same port. See [Phase 7 architecture, APIs, evaluation and corpus limitations](docs/phase7-report.md).

## Phase 7.2 quarterly investor review

In **Financial performance → company → consolidated/standalone period → AI analysis**, the primary report is now a concise quarterly investor review. **View full commentary** retains the complete transcript and page links; calculations and valuation scenarios remain in a separate disclosure panel.

Apply migration `0017_investor_checkpoints` with `alembic upgrade head`. Results/commentary updates refresh the deterministic review and source-backed checkpoints. Use the existing AI analysis action for Qwen interpretation; rejected model output preserves the deterministic review. See [Phase 7.2 implementation, ESDS checkpoints and validation](docs/phase72-report.md).

## Phase 7.3 evidence-safe synthesis

The existing AI analysis action now sends Qwen a compact, attributed investor packet and checks each quarterly-review item independently. Supported interpretation survives when another item fails; one bounded repair attempts to fix rejected items. The saved analysis includes item diagnostics and a `review_trace`. Financial values, checkpoint outcomes and valuation verdicts remain Python outputs. `QUARTERLY_REVIEW_TIMEOUT_SECONDS` defaults to 360 for the local quarterly model. See [Phase 7.3 diagnosis, ESDS comparison and tests](docs/phase73-report.md).

## Phase 7.4 investor materiality

The quarterly review now prioritizes dated milestones, capex, funding, demand, and unusual management assumptions before Qwen. “What Changed” describes supported investment-story revisions. The existing validator rejects invented target periods and uncited delays. See [the Phase 7.4 ESDS comparison and tests](docs/phase74-report.md).

## Phase 8 living investment thesis

Financial performance includes **Investment Thesis** for the selected company and scope. When sufficient verified quarterly research exists, Almonds creates a grouped thesis and evaluates it from the saved review automatically. The main view shows the investment case, pillar cards, what is working, what needs proof, risks, and the next big test. **Edit thesis** retains the optional Phase 8 controls for corrections and investor-defined requirements. Revisions and evidence-backed state changes are append-only; historical views read stored records without backdating beliefs. Quarterly reviews include **Thesis Impact**.

Apply `alembic upgrade head` in `backend` for migration `0018_living_thesis`. Companies with retained thesis history must keep that audit trail; deactivate their pillars instead of deleting the company. Run the isolated UI regression with `cd frontend && node scripts/thesis-check.mjs`. See the [Phase 8 implementation report](docs/phase8-report.md) and [Phase 8.1 zero-touch validation](docs/phase81-report.md).

## Phase 8.2 thesis monitoring

Verified results, metric corrections, attributed commentary, guidance and material official disclosures now refresh existing theses automatically. **Thesis Monitoring** shows material developments, evidence confidence, unresolved questions, catalysts, source pages and an immutable assessment timeline. Choose any two assessments to compare their stored evidence. Numerical comparisons use deterministic Python; automatic monitoring makes no AI calls.

Apply migration `0019_thesis_monitoring` with `alembic upgrade head`. Run `node scripts/monitoring-check.mjs` in `frontend` for the isolated browser regression. See [Phase 8.2 validation and limits](docs/phase82-report.md).

## Phase 8.3B: forecast accuracy and calibration

The Forecast Accuracy panel retains primary forecast-versus-actual scores while showing later corrections separately. It provides metric/horizon summaries, publication-ordered rolling history, typed paginated comparisons, data-quality reasons and confidence/interval calibration with explicit sample uncertainty. No historical predictions or probabilities are backfilled.

Apply `alembic upgrade head` from `backend` for the reversible `0022_forecast_accuracy` history index. `GET /api/companies/{symbol}/forecasts/accuracy` provides summaries and calibration; `GET /api/companies/{symbol}/forecasts/comparisons` provides observations and exclusions; `POST /api/companies/{symbol}/forecasts/evaluate` queues deterministic scoring through the existing scheduler. Automatic financial-evidence evaluation remains enabled through the existing processing hooks.

See [the Phase 8.3B report](PHASE_8_3B_REPORT.md) for methodology, endpoints, migration/test results, the ESDS/Eternal data limitations, and Phase 8.3C next steps. Run `backend/scripts/verify_forecast_accuracy.py` with the backend virtual environment for a read-only audit of the configured PostgreSQL corpus. The new browser regression is `node scripts/forecast-accuracy-check.mjs` from `frontend`.
