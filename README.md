# FinSight

FinSight is a personal investment research and portfolio monitoring project. Phase 1 provides transaction-based portfolio accounting; Phase 2 adds a React dashboard. Transactions are the source of truth; there is no separately maintained holdings table.

## Current functionality

- Create and list companies, with case-insensitive symbol normalization to uppercase.
- Record BUY and SELL transactions and retrieve dated transaction history.
- Calculate open positions, weighted average acquisition price, remaining cost basis, and realised P&L.
- Reject a sale when its quantity exceeds the available shares.
- Use PostgreSQL, SQLAlchemy 2, Pydantic 2, and Alembic migrations.
- View holdings, cost basis, realised P&L, allocation, transaction history, and registered companies in a dark-themed React dashboard.
- Add companies and record BUY/SELL transactions through the dashboard.

Accounting uses weighted average cost. BUY charges are added to acquisition cost. SELL charges are deducted from proceeds and therefore reduce realised P&L. A SELL removes cost basis at the current weighted average; it does not change the per-share cost of remaining shares. Values use decimal numeric types, never binary floating point.

## Architecture

```text
React dashboard -> FastAPI routes -> portfolio service -> SQLAlchemy models -> PostgreSQL
```

The service contains portfolio calculations and SELL validation. Alembic owns schema changes; the application does not call `create_all()` at startup.

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
uvicorn app.main:app --reload
```

Swagger UI: <http://127.0.0.1:8000/docs>. Health check: <http://127.0.0.1:8000/health>.

## Start the dashboard

Leave FastAPI running on port 8000. In a second terminal from the project root:

```sh
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The Vite development server forwards `/api` requests to FastAPI on `127.0.0.1:8000`, so no browser CORS change is needed for local development. For a production build, run `npm run build`; serve `frontend/dist` and route `/api` to FastAPI on the same origin.

Start by adding a company, then record a BUY. Current holdings and allocation are based on remaining cost basis. Realised P&L includes charges and closed positions; no live market values are shown.

## Tests

The automated tests use an isolated in-memory SQLite database, so PostgreSQL does not need to be running for them.

```sh
cd backend
pytest
```

The frontend type and production build check is:

```sh
cd frontend
npm run build
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

- Phase 2 — React dashboard (implemented)
- Phase 3 — Telegram integration
- Phase 4 — Company event monitoring
- Phase 5 — Financial analysis engine
- Phase 6 — Ollama/Qwen
- Phase 7 — OpenFang agents
- Phase 8 — Investment thesis engine
- Phase 9 — Qdrant/RAG
