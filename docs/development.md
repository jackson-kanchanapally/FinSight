# Development

Start with the [quick start](../README.md#quick-start) and [contribution guide](../CONTRIBUTING.md). These instructions assume a POSIX shell on Linux or macOS. On Windows, use WSL or adapt virtual-environment paths and shell commands.

## Toolchain and dependencies

- Python 3.11 or newer. CI exercises Python 3.11 and 3.13.
- Node.js 20.19+ or 22.12+; the locked Vite dependencies require `^20.19.0 || >=22.12.0`. CI uses Node 22.
- npm and the committed `frontend/package-lock.json`.
- PostgreSQL 16 through Docker Compose, or a separately configured PostgreSQL instance, for the running application.
- Ollama and Qdrant only for features that use local models and retrieval.

Install from the repository root:

```sh
cp backend/.env.example .env
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
./run.sh
```

Keep an existing `.env` when updating an installation. The Python requirements declare supported version ranges; the frontend lockfile records resolved npm versions. Review dependency changes explicitly rather than replacing either dependency file as part of an unrelated change.

`run.sh` checks ports, starts the configured database through Compose if needed, waits for PostgreSQL, applies Alembic migrations, and starts the API and Vite on loopback. It stops those two processes on Ctrl+C; the database remains running.

## Run processes individually

From the repository root, start PostgreSQL:

```sh
docker compose up -d db
```

In a backend terminal:

```sh
cd backend
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In a frontend terminal:

```sh
cd frontend
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` and `/health` to the API on port 8000. Interactive API documentation is at `http://127.0.0.1:8000/docs`.

Run backend commands from `backend`: the settings loader reads `../.env` relative to the process's working directory. Environment variables override that file. See [configuration](configuration.md).

## Backend tests

From the repository root:

```sh
backend/.venv/bin/python -m pytest backend/tests -q
```

Or from `backend`:

```sh
.venv/bin/python -m pytest -q
```

The normal suite uses disposable SQLite databases, generated PDFs, mocked HTTP responses and fake model providers. It does not need PostgreSQL, Ollama, Qdrant, Telegram credentials or downloaded issuer filings. Background workers are disabled by the test fixtures and exercised explicitly against isolated sessions where needed.

The following integration checks are opt-in. Leave their variables unset for routine tests and CI:

| Variable | Additional requirement |
| --- | --- |
| `OLLAMA_TEST_ENABLED=true` | Running Ollama and the expected seeded PostgreSQL company/results data. |
| `RUN_MEMORY_LIVE=1` | Running Qdrant and the configured local embedding model. |
| `RUN_PHASE63_DB_REGRESSION=1` | The particular retained ESDS/Eternal corpus described by the test. |
| `ALMONDS_PG_RESEARCH_TEST=1` | A configured PostgreSQL development database; the test creates and drops a disposable research schema. |
| `ALMONDS_ESDS_PDF=/absolute/path/to/filing.pdf` | A previously downloaded official PDF with the exact periods expected by `test_esds_real_pdf.py`. The test never fetches it. `FINSIGHT_ESDS_PDF` is a legacy alias. |

Use a development database when running scripts or tests that explicitly request PostgreSQL. The scripts in `backend/scripts` include operational research and audit tools; they are not all part of the isolated test suite.

## Frontend checks

```sh
cd frontend
npm ci
npm run build
npx playwright install chromium
npm run test:browser
npm run test:gold
```

The build runs TypeScript checks before Vite. The main browser smoke test starts temporary FastAPI and Vite processes, uses disposable SQLite and document directories, and exercises portfolio and disclosure workflows. It uses `backend/.venv/bin/python` by default; set `ALMONDS_BACKEND_PYTHON` to another installed Python interpreter when needed. The Gold smoke test supplies intercepted fixture responses. Screenshots go to ignored `frontend/e2e-artifacts/`.

Additional focused browser scripts are available in `frontend/scripts` for memory, quarterly reviews, theses, monitoring and forecast accuracy. Inspect their startup and data requirements before running them. `production-check.mjs` reads an existing deployment and is intentionally excluded from CI.

`npm run format` formats frontend source and scripts with Prettier; it writes files, so run it only over changes you intend to review.

## Database migrations

Alembic owns the application schema. Add migrations under `backend/alembic/versions` and update the SQLAlchemy models together; do not call `create_all()` in application startup. Existing migrations are historical records and should remain unchanged after distribution.

Against a disposable PostgreSQL development database:

```sh
cd backend
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic check
```

`alembic check` connects to the configured database and compares its migrated schema with model metadata. It is separate from the isolated pytest checks. Back up real installations before migrations; test downgrade paths only with disposable data.

## CI

[The CI workflow](../.github/workflows/ci.yml) runs backend tests on Python 3.11 and 3.13, then builds the frontend and runs the portfolio/disclosure and Gold Chromium smoke checks. It does not start live data collection, require project secrets, or run production checks. Fork pull requests use the same read-only repository permissions.

Action major versions follow the official [checkout](https://github.com/actions/checkout), [setup-python](https://github.com/actions/setup-python) and [setup-node](https://github.com/actions/setup-node) usage documentation. Review action and dependency upgrades as code changes. A workflow run on GitHub is needed to verify the hosted runner environment; local checks cover the commands that it invokes.

## Code map

| Location | Responsibility |
| --- | --- |
| `backend/app/api` and `backend/app/schemas` | HTTP routes and validated request/response contracts. |
| `backend/app/models` | SQLAlchemy persistence and audit records. |
| `backend/app/services` | Portfolio, financial, research, alert and reporting logic. |
| `backend/app/ingestion` | Source adapters, access policy, document storage, parsing and deduplication. |
| `backend/app/market` | Dated prices, market references, valuation and forward/reverse scenarios. |
| `backend/app/ai` | Provider interface, bounded context, structured output and evidence validation. |
| `backend/alembic` | Database migrations. |
| `backend/tests` | Isolated API/service tests and opt-in integration checks. |
| `frontend/src` | React views, API clients and styles. |
| `frontend/scripts` | Browser regressions. |
| `ops` | Optional local macOS installation and narrow runtime update tools. |

The [architecture documentation](architecture/README.md) and historical implementation reports provide deeper background. Current code and maintained user guides take precedence when an older report describes an earlier behavior.
