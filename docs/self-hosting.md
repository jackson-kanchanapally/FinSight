# Self-hosting

Almonds is designed for a trusted local user. Its API includes unauthenticated read and write endpoints. Keep the dashboard, API, database, Ollama and Qdrant private. This repository does not provide a complete authenticated, multi-user internet deployment.

Use the [quick start](../README.md#quick-start) for development, [configuration reference](configuration.md) for environment settings and [security policy](../SECURITY.md) for vulnerability reporting.

## Services and persistent data

| Component | Default location | Persistence |
| --- | --- | --- |
| FastAPI | `127.0.0.1:8000` | PostgreSQL and document files. |
| Vite development dashboard | `127.0.0.1:5173` when started with `run.sh` | No authoritative data. |
| PostgreSQL | Host port `5432` through Compose | Named Compose volume `finsight_postgres`, usually prefixed by the Compose project name. |
| Source PDFs | `backend/data/documents` | Content-addressed files; included in backups alongside the database. |
| Qdrant, optional | `127.0.0.1:6333` | Named Compose volume `almonds_qdrant`; embeddings can be rebuilt from retained documents. |
| Ollama, optional | `localhost:11434` | Models managed by the separate Ollama installation. |
| Local secrets | Root `.env` | Private environment configuration. |

The supplied Compose file publishes PostgreSQL on the host without a loopback IP restriction. For a local installation, change the `db` service's port binding in your deployment copy before starting Compose:

```yaml
ports:
  - "127.0.0.1:${DATABASE_PORT:-5432}:5432"
```

Qdrant is already bound to loopback in the supplied file. If you maintain separate Compose override files, use the same file selection for all startup, backup and maintenance commands.

## Serve a built dashboard

The existing production ASGI entry point serves the built frontend and API on one loopback origin. From the repository root, after installing dependencies and configuring `.env`:

```sh
npm ci --prefix frontend
npm run build --prefix frontend
docker compose up -d db
cd backend
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn app.production:app --host 127.0.0.1 --port 8000 --workers 1
```

If you use a Compose override, replace the Compose command above with the same explicit file selection you used to initialize the database. An existing independently managed PostgreSQL database can be used instead.

Open `http://127.0.0.1:8000`. The `app.production` entry point requires `frontend/dist`; a missing directory prevents startup. Run one API worker because schedulers run inside each API process. Multiple workers or multiple app instances can duplicate background work even where individual jobs have deduplication protections.

For a deployment beyond your own machine, add and review authentication, authorization, HTTPS, network restrictions, secret management, rate limits and backup procedures first. A reverse proxy alone does not add application-level permissions. Optional signed earnings-report links expose a narrowly scoped report route; they do not secure the rest of the API.

## Scheduling and availability

Company research, market briefs, gold and IPOs use the running API process's scheduler. There is no external wake-up service or cloud scheduler. The machine must be awake, the process must remain running, and PostgreSQL must be reachable. Telegram additionally requires a configured bot and destination.

Market brief times use IST (`Asia/Kolkata`) regardless of the machine's timezone. Missed messages follow each feature's documented cutoff and retry rules; starting the app later does not guarantee a replay. Review [market briefs](market-briefs.md), [gold](gold.md) and [IPOs](ipos.md) for their different delivery windows.

## Optional macOS login service

[Local macOS service instructions](production-tracking.md) describe `ops/install-production.py`. The installer copies the backend, Python environment, frontend build, `.env` and operational files to `~/Library/Application Support/Almonds`, then manages `com.almonds.portfolio` as a user LaunchAgent.

Review its behavior and paths before running it. It replaces the installed snapshot and restarts that service. The installed snapshot is separate from the checkout: code changes and `.env` changes in the checkout do not take effect there until explicitly installed. The user must be logged in, Docker must be available, and the Mac must remain awake for the service to run as intended.

The `prepare-*-update.py` scripts are narrow overlays for an existing compatible installed runtime. They validate expected source and migration fingerprints and are not a universal upgrade mechanism. A fresh clone should use the ordinary setup path first.

## Backups

A useful backup includes PostgreSQL, source PDFs and private configuration. Database references and content-addressed PDFs must remain together. Also retain the Git commit used by the installation so the matching schema and application can be reproduced.

For the default database name and role, an example PostgreSQL backup is:

```sh
mkdir -p ../almonds-backups
docker compose exec -T db pg_dump -U finsight -d finsight -Fc > ../almonds-backups/almonds-postgres.dump
tar -czf ../almonds-backups/almonds-documents.tar.gz backend/data/documents
```

Replace `finsight` with your configured role and database, and use your Compose override selection when applicable. Stop application writers while taking a coordinated backup. Store backups outside the repository and protect them as private financial data. Back up the installed document directory instead when running the macOS snapshot.

Copy `.env` separately to protected storage; do not commit it or publish a support bundle containing it. Qdrant can be backed up separately or rebuilt by indexing retained source documents again.

Test restoration into a separate PostgreSQL database and document directory before relying on a backup. Match the application version to the restored schema, confirm company and source-document records, and only then switch the active configuration. Do not test a restore over the only copy of your portfolio.

## Updates

Back up data and stop the application before updating an installation. Review release or commit notes and compare new example settings without replacing your private `.env`. Install the changed Python dependencies, run `npm ci` and rebuild the frontend, apply `alembic upgrade head`, then restart the single API process. Check `/health`, company records and a source-document link after restart.

For the macOS service, the checkout and installed runtime are separate; follow the installer or a reviewed compatible overlay workflow. A rollback of Python files alone does not roll back a database migration. Plan and test schema changes using a disposable copy first.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Database connection refused | Start PostgreSQL, confirm `DATABASE_HOST`/`DATABASE_PORT`, and inspect `docker compose logs db`. |
| Authentication fails after changing `.env` | Existing volumes retain initialized PostgreSQL users and passwords. Update that role deliberately or restore into a separate correctly configured database. |
| Port 8000 or 5173 is occupied | Stop the existing process or local service before starting another copy; `run.sh` refuses to claim those ports. |
| AI unavailable or timed out | Confirm Ollama is running, the configured model is installed, and its endpoint is reachable by the API. Review the labeled fallback instead of assuming a verified model response. |
| Company Memory unavailable | Start Qdrant and the embedding model, then index parsed documents for the selected company. |
| No verified financial period | Check source coverage, the filing's text layer, issuer matching, period/scope recognition and validation status. Eligible manual PDFs can be processed, but table validation does not independently authenticate them. Image-only PDFs need an external OCR workflow. |
| Source blocked or stale | Inspect source status and timestamps. Almonds reports source gaps; it does not bypass access controls or substitute invented data. |
| No Telegram alert | Check bot configuration, dashboard delivery status, app uptime, trading-calendar eligibility and the feature's delivery window. |
| Checkout change has no effect | Confirm whether the active process serves the checkout or the installed macOS snapshot. |

The health endpoint checks that the API responds; it is not a guarantee that every source, model, scheduled task or delivery succeeded. Consult feature-level source and delivery status as well.
