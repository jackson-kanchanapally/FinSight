# Local production portfolio tracking

Activated on 19 October 2026 using the existing PostgreSQL database and all 54 registered portfolio entries, including entries without recorded trades. The compiled React dashboard and FastAPI API share **http://127.0.0.1:8000**. This trusted local service binds only to loopback.

## Runtime and persistence

- macOS LaunchAgent: `~/Library/LaunchAgents/com.almonds.portfolio.plist`.
- Runtime snapshot: `~/Library/Application Support/Almonds`.
- Logs: `~/Library/Application Support/Almonds/backend/data/runtime/production.log` and `production-error.log`.
- Documents: `~/Library/Application Support/Almonds/backend/data/documents`. Existing relative document references and hashes are preserved. Production writes new documents here; do not delete this directory when updating code.
- Database: existing `portfolioai` Compose project and PostgreSQL volume; no second database or reset. Activation used `0022_forecast_accuracy`; Telegram adds `0023_telegram_deliveries`, and intelligent earnings alerts add `0024_intelligent_earnings_alerts`.

The LaunchAgent starts at login and restarts the server after a crash. The Mac must be awake and the user logged in; Docker Desktop must run. If Docker is unavailable, launchd retries startup every 30 seconds. Laptop sleep and logout interrupt monitoring; due jobs resume on the next start. Source backoff and leases remain persisted in PostgreSQL.

The runtime is outside Desktop because macOS privacy controls prevent launchd from reading a Desktop workspace. Code updates require rebuilding and reinstalling the runtime snapshot; source edits do not modify the running deployment automatically.

## Coverage and scheduling

Every registered entry gets `RESEARCH`, `ACCURACY`, `COMMENTARY` and `MARKET` jobs, plus NSE disclosure jobs for NSE entries and existing configured source jobs. This includes all entries in the registered portfolio list, regardless of transaction history. New entries added through the dashboard enroll immediately; each worker cycle also reconciles the full list.

The scheduler polls every 60 seconds with up to 12 claimed jobs per cycle (`RESEARCH_BATCH_SIZE`). Existing provider cooldowns, leases and backoff still apply. Accuracy runs deterministically without LLM calls; first actual scores and immutable forecast history are preserved. Successful non-market jobs recur hourly; market jobs recur daily. Work resumes after downtime without bypassing provider limits.

All 54 accuracy jobs completed during activation. **All companies currently show Insufficient data:** the database has no eligible historical numerical forecast/actual pairs. Enrollment does not imply a forecast can be generated, a source is available, or a symbol is valid for every provider. ETFs and REITs do not automatically acquire company financial metrics.

The activation audit found 16 jobs in RETRY (13 disclosures and three market jobs), affecting 13 entries: BAGMANE, EMBASSY-RR, GOLDBEES, GOLDIETF, HDFCSML250, HINDZINC, MAHKTECH, MASPTOP50, MINDSPACE-RR, MODEFENCE, MON100, NIFTYIETF and STLTECH-BE. These remain enrolled and independently retry on their persisted schedules. The existing equity/SME issuer directory does not resolve every fund, REIT or broker-suffixed symbol; no aliases were guessed or company records relabeled. Inspect each company's Automation panel for current errors. An empty RSS window is not historical filing coverage.

## Operations

From the workspace root, update the tested runtime after code changes:

```sh
cd frontend && npm run build && cd ..
python3 ops/install-production.py
```

The installer copies the private `.env` with restricted permissions, copies verified dependencies without downloading packages, preserves production documents, retains the existing Compose project and installs/restarts the LaunchAgent. Install only after application checks pass. The launcher applies additive migrations and uses one Uvicorn worker, without development reload or Vite.

```sh
# Restart / stop / resume (run in a terminal on this Mac)
launchctl kickstart -k gui/$(id -u)/com.almonds.portfolio
launchctl bootout gui/$(id -u)/com.almonds.portfolio
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.almonds.portfolio.plist"
```

Audit the authoritative runtime configuration and database:

```sh
cd "$HOME/Library/Application Support/Almonds/backend"
.venv/bin/python scripts/portfolio_tracking.py --output data/runtime/portfolio-tracking.json
```

The default audit is read-only. `--activate` creates missing jobs idempotently; `--score` processes due accuracy jobs with normal claims/backoff and never generates forecasts. The detailed audit contains portfolio/source status and is private; runtime files are excluded from Git. The worker's actual liveness is verified separately through the LaunchAgent, HTTP health and advancing job timestamps.

Production downloads update the runtime document store, while the workspace's old document copy is retained. If using a development server against this same database, configure its `DOCUMENT_STORAGE_PATH` to the production document directory so newly downloaded PDFs can be served there too. Avoid running another production scheduler against this database unnecessarily.

## Validation at activation

- Backend suite with PostgreSQL and ESDS/Eternal regression flags: **452 passed, 7 skipped**. The previously documented optional legacy ESDS P/E expectation remains a separate known baseline failure; its flag was not enabled in this run.
- TypeScript/Vite production build passed.
- All 54 portfolio entries enrolled; all 54 first accuracy jobs succeeded.
- Live HTTP smoke: dashboard, health, 216 accuracy/comparison requests across both scopes, company automation status, and all nine existing document downloads verified by SHA-256.
- Production browser smoke passed: 54 selectable entries; ANANTRAJ, ESDS, ETERNAL, GOLDBEES and WABAG pages; desktop/mobile rendering; no browser errors. Reproduce with `cd frontend && node scripts/production-check.mjs` (read-only).

Current source failures and forecast availability must be checked in the live UI or a new audit; this document records activation evidence rather than a permanent provider-availability guarantee.

## Telegram integration

Telegram event alerts are installed with a durable delivery queue and a Company events status panel. The bot and private destination are configured locally. Follow [Telegram setup](telegram.md) to change them; after changing the project `.env`, reinstall the runtime to load those settings.

The intelligent earnings update passed **501 backend tests, with 16 optional checks skipped**, the frontend production build, and the general, monitoring and forecast-accuracy browser regressions. Existing local ESDS and ETERNAL PDFs were also checked without external data calls. Telegram and external source calls are mocked in automated tests. See [implementation and limitations](intelligent-earnings-alerts.md).

The user chose to keep Almonds on this Mac. No remote HTTPS endpoint is configured; the persisted notification records remote report access as unavailable, and the short Telegram brief omits the link. The dashboard and stored source PDFs remain accessible locally. The Mac must remain awake for alerts to run.

After installing the earnings update, live read-only checks confirmed healthy HTTP service, migration `0024_intelligent_earnings_alerts`, enabled/configured Telegram, and valid single-part ETERNAL and ESDS Q1 FY2027 previews. Both existing stored documents remain independently `UNVERIFIED`, with limited source coverage and `LOW` evidence confidence; migration does not retroactively authenticate them. ESDS uses the verified deterministic fallback when validated AI prose is unavailable. These checks did not enqueue or send alerts.

The later Telegram presentation update passed **507 backend tests, 16 skipped** and reduced ETERNAL's preview from roughly 3,000 to 550 UTF-16 units. It adds short result highlights, direction, thesis and attributed management highlights without changing frontend files, financial calculations or existing saved report history. No new migration is required.

The non-earnings follow-up passed **515 backend tests, 16 skipped**. Dividends and other disclosures now use a short AI summary, evidence-backed Positive/Negative/Neutral impact, and a conditional next-step explanation. The event analyst uses `EVENT_INSIGHT_V2`; prior analyses remain saved and legacy entries without impact explain that the direction is unclear. HTML formatting and receipts use the existing delivery worker. No migration or frontend change is required; tests mock Telegram and AI providers.
