# FinSight

FinSight is a personal investment research and portfolio monitoring project. Phase 1 provides the backend foundation and transaction-based portfolio accounting. Transactions are the source of truth; there is no separately maintained holdings table.

## Phase 1 functionality

- Create and list companies, with case-insensitive symbol normalization to uppercase.
- Record BUY and SELL transactions and retrieve dated transaction history.
- Calculate open positions, weighted average acquisition price, remaining cost basis, and realised P&L.
- Reject a sale when its quantity exceeds the available shares.
- Use PostgreSQL, SQLAlchemy 2, Pydantic 2, and Alembic migrations.

Accounting uses weighted average cost. BUY charges are added to acquisition cost. SELL charges are deducted from proceeds and therefore reduce realised P&L. A SELL removes cost basis at the current weighted average; it does not change the per-share cost of remaining shares. Values use decimal numeric types, never binary floating point.

## Architecture

```text
FastAPI routes -> portfolio service -> SQLAlchemy models -> PostgreSQL
```

The service contains portfolio calculations and SELL validation. Alembic owns schema changes; the application does not call `create_all()` at startup.

## Prerequisites

- Python 3.11 or newer
- Docker with Docker Compose

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
uvicorn app.main:app --reload
```

Swagger UI: <http://127.0.0.1:8000/docs>. Health check: <http://127.0.0.1:8000/health>.

## Tests

The automated tests use an isolated in-memory SQLite database, so PostgreSQL does not need to be running for them.

```sh
cd backend
pytest
```

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

Read transaction history with `GET /api/transactions` or `GET /api/transactions/ESDS`. Read all portfolio positions with `GET /api/portfolio` or one symbol with `GET /api/portfolio/ESDS`. Company endpoints are `GET /api/companies` and `GET /api/companies/ESDS`.

## ESDS expected result

After buying 34 shares at ₹429 and selling 24 at ₹1,191, with no charges:

- Remaining quantity: 10
- Average acquisition price: ₹429
- Remaining cost basis: ₹4,290
- Realised P&L: (₹1,191 − ₹429) × 24 = ₹18,288

## Roadmap

- Phase 2 — React dashboard
- Phase 3 — Telegram integration
- Phase 4 — Company event monitoring
- Phase 5 — Financial analysis engine
- Phase 6 — Ollama/Qwen
- Phase 7 — OpenFang agents
- Phase 8 — Investment thesis engine
- Phase 9 — Qdrant/RAG
