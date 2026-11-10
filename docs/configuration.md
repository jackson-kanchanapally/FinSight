# Configuration

Copy [`backend/.env.example`](../backend/.env.example) to `.env` at the repository root for a new installation. Keep this file private. Run backend commands from `backend`, because the settings loader resolves `../.env` from its working directory. Values supplied in the process environment override the file; restart the API after changing settings.

The tables below describe defaults in the current [`Settings` class](../backend/app/config.py). The example file may choose a different operational value: for example, `EVENT_MONITOR_INTERVAL_MINUTES` is 10 in the example and 30 in the class. An installed copy of Almonds has its own environment file; editing the checkout does not update that copy automatically.

## Database and documents

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_NAME` | `finsight` | PostgreSQL database name. |
| `DATABASE_USER` | `finsight` | PostgreSQL role. |
| `DATABASE_PASSWORD` | `finsight_dev` | Development password; replace for your installation. |
| `DATABASE_HOST` | `localhost` | Database host visible to the API process. |
| `DATABASE_PORT` | `5432` | PostgreSQL port; also controls the Compose host port. |
| `DOCUMENT_STORAGE_PATH` | `backend/data/documents` | PDF storage, resolved from the backend source location unless overridden. |

Compose persists PostgreSQL in a named volume and Qdrant in a separate named volume. Changing a database password in `.env` does not change the password of an already initialized PostgreSQL volume. See [self-hosting](self-hosting.md) for access, persistence and backup guidance.

## Research automation and company sources

| Variable | Default | Purpose |
| --- | --- | --- |
| `RESEARCH_AUTOMATION_ENABLED` | `true` | Run the durable per-company research worker. |
| `RESEARCH_POLL_SECONDS` | `60` | Worker polling cadence; minimum 10 seconds. |
| `RESEARCH_BATCH_SIZE` | `12` | Maximum jobs processed in one worker cycle. |
| `EVENT_SOURCE_MODE` | `live` | `live` uses configured public sources; `fixture` supplies invented demo records; `none` disables event fetching. |
| `EVENT_MONITOR_ENABLED` | `false` | Legacy event-only scheduler, used when autonomous research is disabled. |
| `EVENT_MONITOR_INTERVAL_MINUTES` | `30` | Legacy check interval. The example sets 10. |
| `NSE_SOURCE_ENABLED` | `true` | Enable the NSE company-announcement adapter. |
| `BSE_SOURCE_ENABLED` | `false` | BSE adapter remains a documented placeholder. |
| `COMPANY_IR_SOURCE_ENABLED` | `true` | Enable supported company-specific IR adapters; this is not a universal website crawler. |
| `LIVE_SOURCE_TIMEOUT_SECONDS` | `15` | Bounded live-source request timeout. |

Live monitoring covers registered eligible companies, including companies without an open position. Fixture event checks use the demo path and are not scheduled by the autonomous research worker. For an isolated demo, set `EVENT_SOURCE_MODE=fixture`, `RESEARCH_AUTOMATION_ENABLED=false`, and disable the other live monitors below; then use **Check for events** manually. Fixtures are not real issuer news.

See [data sources](data-sources.md), [the user guide](user-guide.md) and [research operations](production-tracking.md) for source coverage and monitoring behavior.

## Local AI

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama HTTP endpoint. |
| `OLLAMA_MODEL` | `qwen3:8b` | Installed model used for analysis. |
| `OLLAMA_TIMEOUT_SECONDS` | `120` | General model timeout. |
| `QUARTERLY_REVIEW_TIMEOUT_SECONDS` | `360` | Longer timeout for quarterly synthesis. |
| `OLLAMA_NUM_CTX` | `8192` | Model context window requested by the provider. |
| `AI_TEMPERATURE` | `0.1` | Generation temperature. |
| `AI_MAX_CONTEXT_CHARS` | `24000` | Bound on the analyst's assembled context. |
| `AI_MAX_DOCUMENT_CHARS` | `1800` | Bound on document excerpts in that context. |

Install Ollama separately and pull the configured model before requesting AI analysis. The repository's provider uses Ollama; configuring a remote endpoint changes where supplied research context is processed. The numerical engines work independently of model-generated prose. Unavailable or rejected AI results are labeled and use the supported fallback behavior described in the [analyst documentation](local-ai-analyst.md).

## Company Memory

| Variable | Default | Purpose |
| --- | --- | --- |
| `COMPANY_MEMORY_ENABLED` | `true` | Allow retrieval over indexed company documents. |
| `QDRANT_BASE_URL` | `http://localhost:6333` | Local Qdrant endpoint. |
| `MEMORY_COLLECTION` | `almonds_company_memory_v1` | Qdrant collection. |
| `MEMORY_EMBEDDING_MODEL` | `nomic-embed-text:latest` | Installed Ollama embedding model. |
| `MEMORY_EMBEDDING_DIMENSIONS` | `768` | Expected embedding length. |
| `MEMORY_TIMEOUT_SECONDS` | `5` | Vector-store request timeout. |
| `MEMORY_EMBEDDING_TIMEOUT_SECONDS` | `45` | Embedding request timeout. |
| `MEMORY_CHUNK_CHARS` | `1600` | Maximum document chunk length. |
| `MEMORY_OVERLAP_CHARS` | `160` | Adjacent-chunk overlap. |
| `MEMORY_CONTEXT_CHARS` | `2400` | Bound on retrieved analyst context. |
| `MEMORY_MIN_SCORE` | `0.35` | Minimum retrieval similarity score. |

Start Qdrant with `docker compose up -d qdrant`, pull the embedding model with `ollama pull nomic-embed-text`, and use **Company Memory → Index / update documents**. PostgreSQL and source documents remain authoritative. Changing embedding dimensions or the embedding model requires a compatible collection and reindexing strategy; do not mix incompatible vectors in an existing collection.

## Prices and valuation

| Variable | Default | Purpose |
| --- | --- | --- |
| `MARKET_DATA_PROVIDER` | `nse_mcp` | `nse_mcp`, `alpha_vantage`, or `manual`. |
| `MARKET_DATA_ENABLED` | `false` | Enable the legacy EOD refresh scheduler; autonomous research schedules its own work separately. |
| `MARKET_DATA_TIMEOUT_SECONDS` | `12` | Price-provider timeout. |
| `MARKET_DATA_REFRESH_HOUR` / `MARKET_DATA_REFRESH_MINUTE` | `18` / `30` | Legacy weekday EOD refresh in IST, when autonomous research is disabled. |
| `MARKET_LENS_TOLERANCE_PCT` | `5` | Comparison tolerance for imported reference metrics. |
| `ALPHA_VANTAGE_API_KEY` | Empty | Optional secondary provider credential. |

## NSE market data (official MCP)

| Variable | Default | Purpose |
| --- | --- | --- |
| `NSE_MCP_ENABLED` | `true` | Markets page, live holdings value, add-company symbol check, and market-brief index fallback. |
| `NSE_MCP_BHAVCOPY_URL` / `NSE_MCP_LIVE_URL` | NSE's published endpoints | Override only if NSE moves them; HTTPS required. |
| `NSE_MCP_TIMEOUT_SECONDS` | `12` | Per-request timeout. |
| `NSE_MCP_LIVE_CACHE_SECONDS` / `NSE_MCP_EOD_CACHE_MINUTES` | `60` / `30` | Result reuse windows for live and Bhavcopy data. |
| `NSE_MCP_MAX_CONCURRENCY` | `4` | Concurrent NSE requests per process. |
| `PRICE_REACTIONS_ENABLED` | `true` | Background NSE corporate-action sync (share basis for valuation) and event price-reaction measurement. |
| `PRICE_REACTION_POLL_MINUTES` / `PRICE_REACTION_BATCH_SIZE` | `30` / `150` | Job interval and maximum events per run. |
| `PRICE_REACTION_EVENT_TYPES` | Material types | Comma-separated event types measured for price reaction. |

See [NSE market data](nse-mcp.md) for what each view shows and its limits.

Manual observations must retain their dates and sources. Provider coverage and data-use terms apply independently of the application's MIT license. See [market valuation](market-valuation.md).

## Telegram and report links

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_ENABLED` | `false` | Allow the Telegram delivery worker to send queued alerts. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Empty | Bot credential and approved destination. |
| `TELEGRAM_TIMEOUT_SECONDS` | `10` | Telegram request timeout. |
| `TELEGRAM_POLL_SECONDS` | `5` | Delivery-worker cadence. |
| `TELEGRAM_MAX_EVENT_AGE_HOURS` | `48` | Maximum disclosure publication age for automatic alerts. |
| `TELEGRAM_IMPORTANT_EVENT_TYPES` | Approved investor-relevant event list | Narrow the approved categories sent automatically. The exact default list is in the example file. |
| `TELEGRAM_HOLDINGS_IN_CLOSE_BRIEF` | `true` | Add a **Your holdings** section to the 4 PM closing brief (needs NSE MCP). |
| `TELEGRAM_VOLUME_SPIKE_RATIO` | `2.0` | Volume multiple of the 30-session average that the closing brief flags. |
| `TELEGRAM_REACTION_FOLLOWUPS` / `TELEGRAM_REACTION_FOLLOWUP_SESSIONS` | `true` / `5` | Reply under a held company's results alert after 1, 5 or 20 sessions with its move vs the Nifty 50. |
| `TELEGRAM_BIG_MOVE_ALERTS` / `TELEGRAM_BIG_MOVE_PCT` | `false` / `7` | Intraday alert when a holding moves this many points against the Nifty 50; once per holding, direction and day. |
| `REPORT_PUBLIC_BASE_URL` | Empty | Optional HTTPS origin for restricted remote earnings-report links. |
| `REPORT_ACCESS_SECRET` | Empty | Separate secret of at least 32 characters for report-link signatures. |
| `REPORT_LINK_TTL_SECONDS` | `86400` | Signed-link lifetime; 300–86400 seconds. |

Use `backend/.venv/bin/python backend/scripts/setup_telegram.py` from the root for interactive bot setup. It writes credentials to `.env` and can send a test. Do not use the bot token as the report-access secret. Remote report links require a separately configured HTTPS reverse proxy exposing only the intended earnings-report paths, not the local administrative API. See [Telegram setup](telegram.md).

## Market briefs

| Variable | Default | Purpose |
| --- | --- | --- |
| `MARKET_BRIEFS_ENABLED` | `true` | Prepare regular NSE-session market briefs when Telegram is configured. |
| `MARKET_BRIEF_TIMES_IST` | `08:45,11:00,12:30,16:00` | Pre-open, updated morning, noon and closing slots. |
| `MARKET_BRIEF_SOURCE_TIMEOUT_SECONDS` | `12` | Source timeout. |
| `MARKET_BRIEF_AI_TIMEOUT_SECONDS` | `90` | Brief-generation timeout. |
| `MARKET_BRIEF_DIRECTION_THRESHOLD_PCT` | `0.3` | Direction threshold frozen into the original pre-open assessment. |

All four times use `Asia/Kolkata`. The pre-open slot must be 07:00–09:00, the updated morning slot 10:00–11:30, noon 11:00–13:30 and close 16:00–18:00. Updated morning and noon windows must be at least 30 minutes apart. The previous three-slot format is accepted by inserting an 11:00 update. Noon uses the saved updated morning news and outlook alongside fresh evidence; the original pre-open forecast remains separately traceable. Delivery windows, trading-calendar checks and source gaps are documented in [market briefs](market-briefs.md).

## Gold

| Variable | Default | Purpose |
| --- | --- | --- |
| `GOLD_MONITOR_ENABLED` | `true` | Enable independent gold monitoring. |
| `GOLD_POLL_MINUTES` | `15` | Normal source-refresh interval. |
| `GOLD_ALERT_TIMES_IST` | `11:00,17:00` | Two daily brief times in increasing order. |
| `GOLD_PRICE_MOVE_PCT` | `1` | Percentage move threshold for extra global-price alerts. |
| `GOLD_MOVE_COOLDOWN_MINUTES` | `60` | Minimum gap between price-move alerts. |
| `GOLD_SOURCE_TIMEOUT_SECONDS` | `15` | Gold-source timeout. |
| `GOLD_MAJOR_EVENT_ALERTS_ENABLED` | `true` | Allow qualifying major-event alerts. |
| `GOLD_MAJOR_EVENT_ALERTS_SINCE` | Unset | Optional publication cutoff used by the installed update tool to avoid old-event replay. |

See [gold sources and delivery rules](gold.md) for the distinction between global USD/troy-ounce prices, India/Hyderabad INR-per-gram references, routine dashboard news and extra alerts.

## IPOs

| Variable | Default | Purpose |
| --- | --- | --- |
| `IPO_MONITOR_ENABLED` | `true` | Enable mainboard-IPO discovery and screening. |
| `IPO_CHECK_HOUR_IST` | `8` | Start the daily discovery pass at 08:00 IST by default. |
| `IPO_MIN_GMP_PCT` | `10` | Minimum indicative GMP percentage for the attractive-only screen. |
| `IPO_MAX_PE` | `40` | Maximum P/E used by that screen. |
| `IPO_GMP_MAX_AGE_HOURS` | `24` | Maximum GMP quote age. |

GMP is unofficial and may be stale or unavailable. Telegram setup is independent of discovery, and only qualifying issues generate the documented alerts. See [IPOs](ipos.md).

## Historical expectation tolerances

| Variable | Default | Purpose |
| --- | --- | --- |
| `EXPECTATION_MET_TOLERANCE_PCT` | `5` | Default percentage tolerance for a met expectation. |
| `EXPECTATION_PARTIAL_TOLERANCE_PCT` | `10` | Default wider partial tolerance. |
| `EXPECTATION_PERCENTAGE_TOLERANCE_PP` | `0.5` | Default percentage-point tolerance for applicable metrics. |
| `EXPECTATION_MIN_SAMPLE` | `20` | Minimum sample for statistical summaries. |

Definitions and tolerances are frozen when an expectation is captured. Editing environment settings does not rewrite previous forecasts or rescore their original capture rules. See [forecast accuracy methodology](../PHASE_8_3B_REPORT.md).

## Disable background work

For portfolio-only use or an isolated manual demo, set these flags explicitly and restart:

```dotenv
RESEARCH_AUTOMATION_ENABLED=false
EVENT_MONITOR_ENABLED=false
MARKET_DATA_ENABLED=false
IPO_MONITOR_ENABLED=false
GOLD_MONITOR_ENABLED=false
MARKET_BRIEFS_ENABLED=false
TELEGRAM_ENABLED=false
```

Set `EVENT_SOURCE_MODE=none` for no source ingestion, or `fixture` for manual invented demo disclosures. Manual live fetch and analysis actions can still contact their configured services when requested.
