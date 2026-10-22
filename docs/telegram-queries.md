# On-demand Telegram portfolio analysis

Send `ETERNAL`, `ESDS`, `ICICI Bank`, an ISIN, or a maintained alias to the existing bot. Almonds resolves active holdings, acknowledges uncached generation, and replies with concise, source-linked analysis. Unknown companies receive exactly: `This company is not in your Almonds portfolio.` Ambiguous names receive a list of portfolio tickers for clarification. Unsupported sections are omitted; missing/stale inputs and source conflicts are disclosed.

## Setup

This repository has **one shared portfolio**, without account or portfolio-ownership tables. Both private-chat allowlists grant access to that portfolio. Configure only its owner. Adding another ID grants shared access; it does not create an isolated portfolio. Its existing registered company list is the uploaded portfolio, so entries without imported trade history are included. Entries with fully sold transaction-derived positions are excluded.

1. Stop the API before setup so two processes do not consume updates. Use the existing dedicated bot without a webhook or another `getUpdates` consumer. Open the bot privately and send `/start`.
2. From the project root, run:

   ```sh
   backend/.venv/bin/python backend/scripts/setup_telegram.py --queries
   ```

   Enter the token in the hidden prompt and choose your own private chat. Setup preserves other `.env` settings and writes the bot configuration plus both allowlists from the selected sender/chat IDs. Add `--test` only to request a connection test message. For an already connected bot, you can instead add the following to the project-root `.env`, substituting your own IDs:

   ```dotenv
   TELEGRAM_ENABLED=true
   TELEGRAM_QUERIES_ENABLED=true
   TELEGRAM_ALLOWED_USER_IDS=[123456789]
   TELEGRAM_ALLOWED_CHAT_IDS=[123456789]
   TELEGRAM_COMPANY_ALIASES={"ETERNAL":["Zomato"],"ESDS":["ESDS Software"]}
   TELEGRAM_QUERY_TIMEOUT_SECONDS=60
   TELEGRAM_QUERY_CACHE_SECONDS=600
   TELEGRAM_QUERY_COOLDOWN_SECONDS=15
   AI_PROVIDER=ollama
   ```

   Alias keys must exactly match canonical portfolio tickers. Names/aliases are Unicode, case and punctuation normalized; common company suffixes are ignored for names. There is no fuzzy matching or web company lookup. Both allowlists are mandatory; `TELEGRAM_CHAT_ID` alone never authorizes queries. Groups, channels, bots, edited messages and unauthorized updates are ignored without portfolio access or saved request content.

3. With the existing environment and PostgreSQL running:

   ```sh
   cd backend
   .venv/bin/python -m alembic upgrade head
   .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

   Ollama must be running with `OLLAMA_MODEL` installed (`ollama pull qwen3:8b` for the default). Existing Ollama URL/model, temperature, context and document bounds remain effective. `./run.sh` also migrates before starting development services. For an installed production snapshot, use the existing `python3 ops/install-production.py` workflow so new code and the private environment are copied together.

4. Upload/record holdings through the existing workflow, then send a held ticker privately. `/help` describes accepted input. `GET /api/telegram/status` includes `queries.enabled`, `queries.configured` and request-status counts without credentials. The HTTP APIs remain intended for trusted loopback access.

## Free model configuration

Ollama is the default. The existing `LLMProvider` abstraction and `configured_provider()` factory now also support one explicitly chosen compatible free route, including a local OmniRoute gateway:

```dotenv
AI_PROVIDER=openai_compatible
AI_API_BASE_URL=http://127.0.0.1:20128/v1
AI_API_KEY=
AI_MODEL=your-explicit-zero-cost-route
AI_FREE_MODELS=["your-explicit-zero-cost-route"]
```

Use your gateway's actual address/model name. Remote endpoints require HTTPS; HTTP is allowed only on loopback. The model must exactly match `AI_FREE_MODELS`; automatic/default routing names are rejected. Requests include a JSON schema, disabled provider fallback, and zero prompt/completion price ceilings. The application makes one attempt to the selected route and never tries a paid fallback. Timeouts, 429s, unavailable routes, mismatched reported model names and invalid output produce a temporary-unavailability reply.

The free allowlist is operator-maintained, **not a billing lookup**. Generic gateways may ignore price/fallback fields. Configure the gateway itself with only zero-cost upstreams and disabled paid fallback; upstream accounting cannot be verified by Almonds. Compatible endpoints must support schema-constrained `/chat/completions` and the supplied model ID. Endpoint changes invalidate cached output. Existing analyses use the same factory; retaining `AI_PROVIDER=ollama` preserves their default provider behavior.

## End-to-end flow

1. An independent scheduler job calls the existing Telegram transport's `getUpdates`. A durable cursor and PostgreSQL polling lease prevent competing consumers. Requests and the offset commit together; update IDs are idempotent. Unauthorized updates advance the cursor without saving their content.
2. Resolution uses the registered uploaded portfolio, existing transaction-derived positions when available, canonical ticker/ISIN/BSE identifiers, normalized names and aliases. No portfolio quantities, costs or trade prices enter model prompts. A user cooldown and per-company request coalescing limit repeated work.
3. A separate worker combines the existing financial `build_context()` and deterministic metric/valuation services with the event/PDF context builder, recent management statements, previous analyses and outstanding risks. Fixture data is excluded. Duplicate titles/filing URLs are removed; newer material events take priority. It uses stored evidence without requiring a Qdrant/embedding request.
4. Missing/stale inputs are coalesced into the **existing research queue** when `RESEARCH_AUTOMATION_ENABLED=true`. Disclosure, market and commentary jobs retain existing cooldowns, retry backoff and leases. The reply uses stored evidence immediately instead of waiting for ingestion. Automation-disabled deployments do not enqueue a refresh.
5. Cache identity includes evidence, prompt version, model/provider/endpoint and owner/chat, with a configurable TTL. Cache hits preserve the original analysis timestamp. The worker acknowledges uncached generation, bounds inference through transport and async deadlines, and validates structure, citations, quoted attribution, numeric grounding, comparative/causal claims, confidence and section eligibility.
6. Reported facts, management claims, estimates and AI interpretations remain distinct. A management pipeline cannot become signed orders or disclosed backlog. Valuation labels require deterministic scenario verdicts, calculated metrics and explicit assumptions. Unsupported sections are omitted. Public HTTPS source links come only from retrieved records. HTML escaping and UTF-16 message bounds are enforced.
7. Authorization and active holdings are rechecked before disclosure. Final content is saved before sending. Definitive transient send failures retry with backoff and Telegram `retry_after`. Ambiguous/interrupted sends become `UNCERTAIN` and never automatically replay. Existing event-alert enqueue/preparation/delivery code runs unchanged on its own job.

Migration `0025_telegram_queries` adds only `telegram_inbox` and `telegram_queries`. Existing transactions, financial facts and event-delivery receipts are preserved. Requests retain status, analysis/source timestamps, evidence/references, validated output, route identity and bounded error codes. Raw query text, raw model responses, secrets and portfolio quantities are not logged or added to request history. History remains until an operator applies a retention policy.

## Changed files

| Files | Purpose |
| --- | --- |
| `backend/app/services/telegram_queries.py` | Inbound authorization, polling, durable claims, cache and delivery |
| `backend/app/services/telegram_research.py` | Portfolio resolution, existing evidence composition and refresh scheduling |
| `backend/app/ai/quick_analysis.py` | Short structured contract, grounding validation and Telegram rendering |
| `backend/app/ai/providers/compatible.py` | Explicit free compatible inference route |
| `backend/app/ai/analyst/service.py`, `context_builder.py` | Shared configurable provider factory and optional stored-only context |
| `backend/app/models/telegram.py`, `models/__init__.py` | Inbox/query persistence registration |
| `backend/alembic/versions/0025_telegram_queries.py` | Additive inbox/query migration |
| `backend/app/config.py`, `main.py`, `api/telegram.py` | Settings, independent scheduler jobs and status |
| `backend/scripts/setup_telegram.py`, `backend/.env.example` | Owner setup and environment reference |
| `backend/tests/test_telegram_queries.py` | Offline feature, security, reliability and migration coverage |
| `README.md`, `docs/telegram.md`, this file | Setup, message flow and limitations |

## Testing

From the project root:

```sh
cd backend
.venv/bin/python -m pytest tests/test_telegram_queries.py tests/test_telegram.py tests/test_intelligent_earnings.py tests/test_ai_analyst.py tests/test_events.py -q
.venv/bin/python -m pytest tests -q
```

New tests use disposable SQLite databases and mocked models/Telegram calls. They require no paid APIs or live market access. Existing opt-in integration tests remain skipped unless their flags are enabled. Migration coverage exercises upgrade/downgrade and preservation of existing receipts. PostgreSQL code uses advisory locks, conditional claims and leases; the default suite does not exercise live PostgreSQL concurrency.

## Limits

- There is no multitenant ownership model: use one owner/portfolio per deployment. Existing local HTTP APIs were not converted into an authenticated public service.
- Independent news, analyst consensus and peer datasets are not configured. Coverage is limited to stored issuer evidence, with explicit gaps rather than invented news/estimates.
- BSE remains an existing placeholder adapter. Refresh/history depend on the currently supported, enabled exchange/issuer adapters and uploaded documents.
- There is no universal structured order-book ledger. Existing order filings and attributed commentary support discussion only when present. Aggregate backlog, conversion and concentration metrics are not inferred.
- Refresh is asynchronous, so a reply is not a promise of real-time coverage. Cache age, source age and reporting-period age are distinct. Bounded prompts may omit older/lower-priority sources.
- Replies target roughly 150–250 words including timestamps/limitations. Sparse evidence can produce shorter answers; long URLs may reduce displayed links. All selected references remain persisted.
- Grounding checks reject unsupported numbers, citations and known semantic errors, but cannot prove every model paraphrase correct. Consult the attached primary sources for consequential decisions.
- Offline tests do not certify live Telegram delivery, gateway billing/routing or PostgreSQL concurrency. Test the configured free route in your environment before use.
