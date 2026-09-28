# almonds

almonds is a personal investment research and portfolio monitor. Phase 1 provides transaction-based portfolio accounting, Phase 2 adds a React dashboard, Phase 3 adds company event monitoring, Phase 3.5 adds live primary-source ingestion, Phase 4 adds deterministic financial-result extraction and calculations, and Phase 5 adds an optional local AI analyst. Transactions remain the source of truth; there is no separately maintained holdings table.

## Current functionality

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
- Enter a dated closing price, P/E, and market cap with a source URL. The results analyst uses a snapshot only while it is at most seven days old; no live market price is inferred.

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

The event migrations are 0002 and 0003; Phase 4 adds 0004_financial_reporting and 0005_calculated_fact_provenance; Phase 5 adds 0006_local_ai_analyst. Event briefs and dated market snapshots add 0007 and 0008; sourced forward EPS growth for PEG adds 0009. Run `alembic upgrade head` against the configured PostgreSQL database before starting the API.

## Start the dashboard

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

For a valuation comparison, enter a dated closing price, optional P/E and market cap, and an HTTPS source link in **Financial performance**. You may also enter a sourced forward annual EPS growth estimate and its HTTPS source URL. almonds calculates PEG as P/E divided by the growth rate expressed in percentage points; it leaves PEG unavailable when P/E or sourced positive growth is missing. The quote and growth estimate are user supplied, not a live feed or verified forecast. A snapshot over seven days old is excluded from new AI analysis. The results report shows the input date and sources and treats the valuation conclusion as provisional because one reporting period cannot establish whether future earnings growth will be achieved.

```sh
curl -X POST http://127.0.0.1:8000/api/companies/ESDS/financials/42/analyze
curl http://127.0.0.1:8000/api/companies/ESDS/analysis
curl http://127.0.0.1:8000/api/analysis/1
curl -X POST 'http://127.0.0.1:8000/api/companies/ESDS/financials/42/analyze?regenerate=true'
curl -X POST http://127.0.0.1:8000/api/events/1/analyze
curl http://127.0.0.1:8000/api/companies/ESDS/market-snapshot
```

Replace `42` with a real validated period ID from `GET /api/companies/ESDS/financials`. Invalid model output is retried once. If both responses fail validation, the API returns `VERIFIED_FALLBACK` with a cited summary assembled from verified facts and calculated metrics; the dashboard labels it and offers **Retry AI analysis**. The last validation reason remains in `failure_reason`. Provider failures return `FAILED` with a reason such as `OLLAMA_UNAVAILABLE` or `OLLAMA_TIMEOUT`. See [the analyst design and evaluation](docs/local-ai-analyst.md) for the schema, provenance rules, and limitations.

## Next phase

Phase 6 can add company memory and retrieval over historical official documents. Additional PDF formats need source-specific validation. Authentication is required before exposing monitoring outside a local environment.
