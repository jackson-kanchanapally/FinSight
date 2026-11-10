<p align="center">
  <img src="frontend/public/almonds-mark.svg" alt="Almonds" width="72" />
</p>

# Almonds

**Your portfolio, company filings, and investment research in one self-hosted workspace.**

Almonds helps individual investors follow Indian listed companies from a disclosure to its financial results, valuation, investment thesis, and later outcomes. Record your trades, review evidence linked to original documents, use an optional local AI analyst, and receive selected updates through your own Telegram bot.

The application runs on your machine with a React dashboard, a Python API, and PostgreSQL. Its source is [MIT licensed](LICENSE); dependencies, model weights, and market data have their own terms. It is designed for a trusted local user. The API has unauthenticated operations, so keep it private unless you add suitable access controls. Investment conclusions and forecasts require your own review.

[Quick start](#quick-start) · [Features and usage](#features-and-usage) · [Alert schedule](#alert-schedule) · [Documentation](#documentation) · [Contributing](CONTRIBUTING.md) · [Support](SUPPORT.md)

## Quick start

### Requirements

| Component | Requirement |
| --- | --- |
| Python | 3.11 or newer, with `venv` and `pip` |
| Node.js | 20.19+ or 22.12+; Node 22 is a suitable development choice |
| Database | PostgreSQL; the supplied Compose service uses PostgreSQL 16 |
| Docker Compose | Needed for the supplied database/Qdrant services; an existing PostgreSQL installation also works |
| Shell | Bash for `run.sh`; macOS, Linux, or a suitable Windows WSL environment |
| Optional services | Ollama for AI analysis; Qdrant and an embedding model for Company Memory; Telegram for notifications |

### Install and start

```sh
git clone https://github.com/jackson-kanchanapally/FinSight.git almonds
cd almonds
cp backend/.env.example .env
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
./run.sh
```

Start Docker first if you use the supplied database. `run.sh` checks the configured PostgreSQL connection, starts the Compose database when necessary, applies Alembic migrations, and starts the API and dashboard. It does not overwrite an existing `.env`. Keep the terminal open; press **Ctrl+C** to stop the two development servers. The database volume persists.

| Service | Local address |
| --- | --- |
| Dashboard | <http://127.0.0.1:5173> |
| API documentation | <http://127.0.0.1:8000/docs> |
| Health check | <http://127.0.0.1:8000/health> |

The example `.env` enables live research and the IPO, Gold, and market-brief schedulers. These features can contact external sources; Telegram delivery stays disabled until you connect a bot. For a quiet portfolio-only setup, edit the following values in `.env` before starting:

```dotenv
RESEARCH_AUTOMATION_ENABLED=false
EVENT_MONITOR_ENABLED=false
EVENT_SOURCE_MODE=none
MARKET_DATA_ENABLED=false
IPO_MONITOR_ENABLED=false
GOLD_MONITOR_ENABLED=false
MARKET_BRIEFS_ENABLED=false
COMPANY_MEMORY_ENABLED=false
```

Use your own database credentials outside a disposable development environment. `.env`, local documents, and runtime data are ignored by Git. See [configuration](docs/configuration.md) for individual settings and [self-hosting](docs/self-hosting.md) for built-dashboard operation, backups, and service management.

### Your first company

1. Open **Companies** or **Add company** and register an NSE symbol and issuer name. Add an ISIN or BSE code when relevant.
2. Select **Record transaction** to enter a BUY, quantity, price, date, and charges. Use **Holdings** to inspect the position and **Activity** for its transactions.
3. Open **Events** and select **Check for events** to look for disclosures. You can also upload a PDF you already have permission to use, selecting its company in the upload form.
4. Open **Financials** after a results document has been processed. Select the company and reporting scope, inspect the latest validated period and source facts, then explore analysis, valuation, forecasts, or the thesis.

Registering a company is enough for live NSE monitoring; you do not need to buy shares to research it. External sources can fail or have no matching disclosures. Empty research panels explain which evidence is still needed.

## Features and usage

The [complete user guide](docs/user-guide.md) provides the detailed workflows and limitations. These are the features available in the current source tree.

### Portfolio accounting and company management

Use **Companies** to maintain the issuer registry and **Record transaction** to add BUY and SELL transactions. **Overview** and **Holdings** show remaining shares, weighted average acquisition cost, cost basis, realised P&L, and cost-based allocation. **Activity** shows dated transaction history.

BUY charges increase acquisition cost; SELL charges reduce sale proceeds. Sales remove shares at weighted average cost, and overselling is rejected. Monetary calculations use decimals. For example, buying 34 shares at ₹429 and selling 24 at ₹1,191, without charges, leaves 10 shares, ₹4,290 cost basis, and ₹18,288 realised P&L. These are recorded trade calculations; share-price data has a separate refresh and source date.

Company deletion asks for confirmation and removes associated records where allowed. Companies with immutable thesis or forecast history must be retained to protect that history.

### Company events and document provenance

In **Events**, run **Check for events**, browse disclosures, and open a record to inspect its classification, source references, PDF, numbered pages, and processing state. Live NSE monitoring covers registered NSE issuers; the implemented company IR source is specific to ESDS. Source failures are visible and do not become invented events.

The ingestion pipeline deduplicates source observations and PDF content, retains the original references, and stores documents by content hash. A fixture mode is available for development and labels its invented ESDS examples clearly. BSE bulk announcements and generic company IR adapters are placeholders; adding a company does not create those integrations. See [source coverage](docs/data-sources.md).

### Manual PDFs and financial extraction

Use the PDF upload action in **Events** for results, presentations, transcripts, or other supported disclosures. For financial results, process the document and review **Financials**. Manual results require issuer matching and table validation; a manual upload alone does not establish that a document is authentic.

Financial facts retain their reporting period, consolidated/standalone scope, units, source document, page, and validation status. Almonds calculates supported metrics such as EBITDA, margins, YoY, and QoQ comparisons in Python. Missing or incompatible inputs remain unavailable. Embedded PDF text is supported; image-only documents need an external OCR workflow. See [financial extraction](docs/financial-extraction.md).

### Local AI analyst and quarterly investor reports

After installing Ollama, pull the configured model and keep its server running:

```sh
ollama pull qwen3:8b
```

Set `OLLAMA_MODEL` and `OLLAMA_BASE_URL` in `.env` if needed, then restart the API. Opening an event requests a short event brief. In **Financials**, select the company and scope, then use **Analyze Results** on the latest validated period for the deeper quarterly report; use the regeneration/retry action to request another interpretation. Analysis for older validated periods is available through the API.

The report brings together result highlights, positives, concerns, an earnings bridge, management commentary, valuation assumptions, risks, and what to watch next. Claims carry evidence references, and financial arithmetic remains in Python. Citation and number checks reject unsupported output. Where supported, a deterministic fallback preserves verified analysis when AI output cannot be accepted. Portfolio accounting and document review work without a model. See [the AI analyst](docs/local-ai-analyst.md).

### Management commentary and investor checkpoints

Attach an appropriate management presentation or transcript to the relevant company and period, then review the commentary in **Financials**. The quarterly report separates attributed management statements from calculated financial evidence, records dated guidance, and surfaces milestones or assumptions that need checking.

Use source-page links and **View full commentary** to inspect the original wording. Investor checkpoints track whether later evidence supports the stated expectations; uncertainty and missing evidence remain visible. [The user guide](docs/user-guide.md) explains the commentary and checkpoint controls.

### Valuation, historical prices, and scenarios

Open **Valuation**, or the valuation panel inside **Financials**, and select a company and scope. **Refresh EOD price** requests a dated observation through the configured provider. You can also save a sourced, dated manual close in Financials.

Almonds combines eligible verified financials with dated prices to calculate supported TTM multiples and yields, including P/E, P/S, EV ratios, P/B, earnings yield, and FCF yield when their inputs exist. Expand a metric to inspect its formula and evidence. Price/multiple history and valuation-change explanations retain the underlying dates.

Use scenario controls for assumed EPS growth, future P/E, and return assumptions; reverse calculations show the earnings growth needed to support a chosen outcome. The investor report also exposes editable three-case valuation assumptions. Imported NSE Market Lens CSV references are available through the API and remain separate from Almonds' calculations. Historical EOD prices are unadjusted for corporate actions. See [valuation methodology and APIs](docs/market-valuation.md).

### Company Memory

Start the optional retrieval services:

```sh
docker compose up -d qdrant
ollama pull nomic-embed-text
```

Open **Company Memory**, select a company, and use **Index / update documents**. Search indexed document passages and follow their page references. Relevant historical passages can enrich company analysis, while PostgreSQL remains the authoritative store for structured facts and guidance. Retrieval can be unavailable without preventing ordinary financial analysis. See [Company Memory details](docs/phase7-report.md).

### Living investment thesis and monitoring

In **Financials → Investment Thesis**, review the investment case, thesis pillars, supporting evidence, risks, and the next test. Sufficient verified quarterly research can generate a grouped thesis automatically; **Edit thesis** lets you maintain your own requirements and corrections.

**Thesis Monitoring** records material developments, unresolved questions, evidence confidence, catalysts, and assessment history. Compare two saved assessments to see how the evidence changed. Thesis revisions and state transitions retain their history, and quarterly reports show the effect on the thesis. See [the user guide](docs/user-guide.md) for the editing and comparison workflows.

### Expectations, forecasts, and accuracy

In **Financials → Almonds Forecasts & Accuracy**, capture a dated expectation, inspect its revisions, and compare it with later reported results. The expectation ledger tracks source, capture/publication timing, reporting scope, tolerances, and evaluation reasons.

The **Forecast Accuracy** panel filters forecast-versus-actual comparisons by metric and horizon, distinguishes original scores from later corrections, and shows historical summaries and calibration when the sample supports them. Historical imports do not become predictions made before their actual capture time. Small samples and unscored cases are explicitly identified. See [forecast methodology](PHASE_8_3B_REPORT.md).

### Automatic company research

With `RESEARCH_AUTOMATION_ENABLED=true`, registering a company creates durable research jobs. The worker checks available sources, processes supported results, refreshes market evidence, and prepares eligible forecasts and valuation context when the required history exists.

In **Financials → Almonds Forecasts & Accuracy**, inspect **Research automation status** for job state, source freshness, blockers, and retry controls. Automatic forecasts depend on verified historical coverage; Almonds does not fill missing quarters with invented numbers. The legacy event and market timers are used when automatic research is disabled; see [configuration](docs/configuration.md).

### Telegram company and earnings alerts

Create a bot through Telegram's BotFather, open the bot, and send `/start`. Then run the setup helper from the repository root:

```sh
backend/.venv/bin/python backend/scripts/setup_telegram.py --test
```

The helper prompts for the token without echoing it, discovers the destination chat, saves the Telegram settings in `.env`, and sends the requested connection test. Restart your running API or update its installed runtime to load the new configuration. **Events → Telegram alerts** shows connection and delivery status.

Selected new live company disclosures are queued automatically. Earnings briefs show supported financial highlights and a result interpretation; other material events receive a short summary, impact, and conditional next steps. Routine categories and old archive imports are filtered, and delivery receipts/retries are persisted. Existing historical/manual events are not automatically replayed. See [Telegram setup and delivery rules](docs/telegram.md).

Optional signed earnings-report links require your own HTTPS route exposing only the report endpoints and a separate access secret. Leave those settings empty for local-only use; see [report access and confidence](docs/intelligent-earnings-alerts.md).

### Morning, noon, and closing market briefs

Market-wide Telegram briefs cover available Nifty 50, Sensex, Bank Nifty, volatility, breadth, sectors, and dated Indian/global news. The **11:00 AM brief refreshes the morning news and outlook; its saved context feeds the 12:30 PM update**, including headlines that have left the live feeds.

The original 8:45 AM forecast remains traceable for the pre-open direction comparison. Noon reviews the updated morning brief with current evidence, while the closing report records the final comparison and conditional scenarios for the next session. Missing sources or a missing morning brief are identified. History, status, and non-sending previews are available through `/api/market-briefs`; this feature currently has no dedicated sidebar screen. See [market brief rules](docs/market-briefs.md).

### Mainboard IPO research

Open **IPOs** to inspect discovered issues, opening dates, research, source gaps, and screening results. Automatic discovery checks supported public sources and refreshes each issue. The default attractive-issue screen requires fresh GMP of at least 10% and a usable positive P/E of at most 40, together with the other source/date checks.

Qualifying issues can produce a weekly Telegram shortlist and a fresh pre-opening review. GMP is an unofficial indication and can change; the screen is a configurable research filter, not a listing-gain prediction. Unconfirmed dates or failed source checks prevent alerts. See [IPO sources, screening, and timing](docs/ipos.md).

### Gold prices, news, and alerts

Open **Gold** for global USD-per-troy-ounce prices, India/Hyderabad 22K and 24K references per gram, dated USD/INR context, recorded history, and the news read. The **India / Hyderabad** choice is remembered in the browser and also updates the local-history view.

Telegram briefs arrive at 11 AM and 5 PM IST by default. Extra alerts require a qualifying global price move or a qualifying major reported event; routine news stays in the dashboard and regular briefs. Displayed retail references and source timestamps may differ from a dealer's executable quote. See [Gold sources and alert rules](docs/gold.md).

## Alert schedule

All times below are **India Standard Time (`Asia/Kolkata`)** and are the example configuration defaults.

| Alert | Schedule | Conditions |
| --- | --- | --- |
| Pre-open market outlook | 8:45 AM | Regular NSE trading days; delivery window ends at 9:10 AM |
| Updated morning brief | 11:00 AM | Regular NSE trading days; saved news and outlook feed noon |
| Noon market update | 12:30 PM | Regular NSE trading days; includes the updated morning context |
| Closing market review | 4:00 PM | Regular NSE trading days |
| Gold briefs | 11:00 AM and 5:00 PM | Independent Gold monitoring settings |
| IPO research | Daily pass after 8:00 AM; weekly shortlist and pre-opening checks | Confirmed dates, qualifying issues, and source availability |
| Company disclosures | As eligible new live disclosures are ingested | Important-category and publication-age filters |
| Extra Gold alerts | On a qualifying move/event | Defaults include a 1% global price-move threshold and cooldown |

These schedules run inside the application. The API, database, and host must be running and awake; a repository checkout does not create a cloud scheduler. Collection and analysis can delay a message within its allowed window, and expired messages are not replayed as fresh alerts. See each feature's guide for catch-up and retry rules.

## Architecture and data handling

| Layer | Implementation |
| --- | --- |
| Dashboard | React 19, TypeScript, Vite |
| API and jobs | FastAPI, Pydantic 2, APScheduler |
| Persistence | PostgreSQL, SQLAlchemy 2, Alembic migrations |
| Documents | Content-addressed PDF storage and numbered text extraction |
| Optional AI | Ollama with structured output and evidence validation |
| Optional retrieval | Qdrant with local document embeddings |
| Notifications | Telegram Bot API with a persistent delivery queue |

Source adapters collect dated observations. The application stores their provenance, computes financial/accounting values in Python, and supplies bounded evidence to the optional analyst. Verified facts, model interpretation, user assumptions, and later evaluations remain distinguishable. Alembic owns application schema changes.

Portfolio records, database files, documents, and model indexes remain on your configured infrastructure. Live collection contacts data providers; AI requests go to your configured Ollama endpoint; Telegram receives the messages you enable. Configuring a remote model or external review tool can send context outside your machine. The [security policy](SECURITY.md) and [third-party notices](THIRD_PARTY_NOTICES.md) explain the relevant boundaries.

```text
backend/app/          API, models, source adapters, analysis, jobs, and accounting
backend/alembic/      Database migrations
backend/tests/        Unit, API, fixture, and opt-in integration tests
frontend/src/         Dashboard and feature panels
frontend/scripts/     Isolated browser checks
ops/                  Optional local service and update tools
docs/                 User, setup, methodology, and architecture documentation
.github/              CI and contribution templates
```

## Development and tests

Normal backend tests use disposable SQLite databases and fake providers. They do not require PostgreSQL, Ollama, Qdrant, Telegram credentials, or live market access:

```sh
cd backend
.venv/bin/python -m pytest -q
cd ../frontend
npm ci
npm run build
npx playwright install chromium
npm run test:browser
```

The frontend build checks TypeScript and creates the production bundle. Browser checks start temporary fixture-backed servers. Live-service and official-PDF checks are opt-in; migration checks against PostgreSQL are separate. The [development guide](docs/development.md) documents the complete checks, fixture setup, and how to contribute migrations. GitHub Actions runs the checks defined in [CI](.github/workflows/ci.yml) on pushes and pull requests.

## API access

Open the local [interactive API documentation](http://127.0.0.1:8000/docs) after starting Almonds. It describes request bodies, query parameters, and response types. Common entry points include:

| Area | Examples |
| --- | --- |
| Companies and trades | `/api/companies`, `/api/transactions`, `/api/portfolio` |
| Events and financials | `/api/events`, `/api/companies/{symbol}/financials` |
| Valuation and research | `/api/companies/{symbol}/valuation`, `/api/companies/{symbol}/research/status` |
| Expectations and forecasts | `/api/companies/{symbol}/expectations`, `/api/companies/{symbol}/forecasts/accuracy` |
| Thesis and memory | `/api/companies/{symbol}/thesis`, `/api/companies/{symbol}/memory` |
| Alerts and independent research | `/api/telegram/status`, `/api/market-briefs`, `/api/ipos`, `/api/gold` |

Treat API mutation endpoints as trusted-user operations. The [user guide](docs/user-guide.md) and linked feature references include examples for the controls that are API-only.

## Documentation

| Guide | What it covers |
| --- | --- |
| [User guide](docs/user-guide.md) | Every feature, navigation, workflows, and limitations |
| [Configuration](docs/configuration.md) | Database, sources, jobs, AI, retrieval, alerts, and example settings |
| [Self-hosting](docs/self-hosting.md) | Built dashboard, local service, upgrades, backups, and troubleshooting |
| [Development](docs/development.md) | Development servers, test suites, browser checks, migrations, and CI |
| [Documentation index](docs/README.md) | Feature references, methodology, architecture, and historical implementation reports |
| [Contributing](CONTRIBUTING.md) | Issues, pull requests, validation, and review expectations |
| [Security](SECURITY.md) | Private reporting, trusted-local deployment, and secret handling |
| [Support](SUPPORT.md) | Where to ask for help and how to submit a useful report |

## Contributing

Contributions to documentation, accessibility, tests, extraction coverage, source adapters, and research tools are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), follow the [Code of Conduct](CODE_OF_CONDUCT.md), and open a focused [issue](https://github.com/jackson-kanchanapally/FinSight/issues) or pull request. Include reproducible evidence for data-source or financial-calculation changes. Never attach personal holdings, bot tokens, database dumps, or private documents to a public issue.

For bugs and usage questions, see [support](SUPPORT.md). Report vulnerabilities through the private routes described in [SECURITY.md](SECURITY.md).

## License and third-party terms

Almonds' project source is distributed under the [MIT License](LICENSE). Third-party libraries, model weights, and externally collected documents/data retain their own licenses and usage conditions; the project license does not grant rights to redistribute those materials. The required PyMuPDF PDF library uses separate AGPL/commercial licensing. Review [third-party notices](THIRD_PARTY_NOTICES.md) before packaging or redistributing an installation.
