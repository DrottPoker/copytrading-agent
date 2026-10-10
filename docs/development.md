# Development

How to run the system locally, where configuration lives, and how to run the
same checks as CI. For production see [deployment.md](deployment.md).

## Local Development

Runtime versions: Python 3.12 for the backend and Node.js 24 LTS (24.15 or
newer) for the dashboard. `frontend/.nvmrc` pins the Node major for version
managers, and the frontend Docker image and CI use the same major.

Tweakable non-secret settings live in config files:

- `backend/config/app.json`
- `backend/config/backup.env`
- `backend/config/discovery.json`
- `backend/config/database.json`
- `backend/config/live_trading.json`
- `backend/config/paper_trading.json`
- `backend/config/pool_fill_import.json`
- `backend/config/prune.json`
- `backend/config/scoring.json`
- `backend/config/trading.json`
- `frontend/config/app.json`

`backend/config/scoring.json` uses organized sections for schedule, window,
component weights, profitability, consistency, risk, copyability, recency,
penalties, and window scores.

`backend/config/app.json` owns non-secret runtime defaults such as worker mode,
worker supervision, realtime execution inbox timing, Ops monitoring, backup
status monitoring, wallet pool page limit, network, and shared infrastructure
request settings. `hyperliquid_info_weight_per_minute` is the Hyperliquid info
request weight budget per process. The default of 550 keeps the trading and
maintenance workers together below Hyperliquid's 1200 per minute IP limit. Lower
it when more processes share the same public IP.

`backend/config/backup.env` owns the automated Postgres backup interval and
retention. It is loaded directly by the Compose backup service.

`backend/config/prune.json` uses organized sections for prune rules, scheduled
worker behavior, and worker execution defaults.

`frontend/config/app.json` owns non-secret dashboard defaults such as dashboard
auth enablement, browser proxy URL, server backend URL, polling intervals, and
server-side backend fetch timeout.

`backend/config/discovery.json` uses organized sections for discovery sources,
discovery import, prefiltering, candidate backfill, quality checks, and
promotion.

`backend/config/pool_fill_import.json` owns scheduled pool reimport settings and
the shared fill-import storage and market-filter settings.

`backend/config/database.json` owns manual database maintenance defaults such as
fill retention days, batch size, max rows, and protected top scored wallets.

`backend/config/live_trading.json` owns account capital mode, execution limits,
reconciliation, and risk guardrails. `account.capital_mode` defaults to
`unified`, where Hyperliquid `spotClearinghouseState` is the balance source of
truth for live trading capital. Set it to `standard_per_dex` only when the live
wallet is intentionally using separate default and HIP-3 perp balances.

`backend/config/trading.json` owns shared copy policy used by both paper and live
copy: source count, equal-weight allocation budget, source and total exposure
caps, copy minimum notional and rounding, max entry age, price drift guard, the
live mid-price cache settings, and the source account state cache.
`copy.source_state_cache_max_age_seconds` (15) bounds how old a reused source
state may be, and `copy.source_state_refresh_seconds` (5) sets the warm-up
interval, which must be shorter than the cache age. A cache age of 0 always
fetches fresh source state.

`backend/config/paper_trading.json` owns paper-only simulation settings:
paper copy enablement, simulated fee rate, simulated slippage, simulated latency,
paper recovery cadence, order book fills, funding, liquidation, paper loss
limits, and the paper risk loop cadence.

Worker, inbox, Ops, and backup status settings are read from
`backend/config/app.json`. Backup interval and retention are read from
`backend/config/backup.env`. Do not duplicate these settings in `.env`.

Use `.env` only for secrets, connection strings, deployment identity, and the
single live trading switch:

```bash
cp .env.example .env
```

Docker Compose uses local Postgres by default. Set `POSTGRES_DB`,
`POSTGRES_USER`, and `POSTGRES_PASSWORD`; the compose files build
`DATABASE_URL` and `DATABASE_URL_DIRECT` for the app containers.

Change the default `DASHBOARD_AUTH_PASSWORD` before exposing the dashboard or
API. Production requires a non-empty username and a password of at least 8
characters other than the literal default `change-me`, although a longer unique
password is recommended. Both the backend and the dashboard enforce this when
`APP_ENV=production`. Backend auth is enabled by default and protects every route
except `/health` and `/ready`. The dashboard also enforces Basic Auth before serving pages or
`/api/backend` proxy routes, and throttles failed logins per client IP. Backend credentials are attached by the Next.js
server, so they are not placed in the client bundle. Browser mutation requests
must be same-origin or match the configured backend CORS origin allowlist.
Behind Caddy, the frontend reconstructs the public request origin from Caddy's
trusted `X-Forwarded-Host` and `X-Forwarded-Proto` headers instead of comparing
the browser origin with the internal `frontend:3000` address. The frontend proxy
performs the same-origin check before forwarding and the backend checks the
forwarded origin again. Non-browser clients without an `Origin` header continue
to use Basic Auth normally. These paired origin checks are the CSRF protection
for authenticated mutation routes.

For a fresh local Compose database, start Postgres and run migrations first:

```bash
docker compose up -d postgres redis
docker compose run --rm backend python -m alembic upgrade head
```

Run the stack:

```bash
docker compose up --build
```

Services:

- Dashboard: http://localhost:3000
- Backend: http://localhost:8000
- Health: http://localhost:8000/health
- Ops Health: http://localhost:3000/ops
- Postgres: localhost:5432
- Postgres backup worker: `postgres-backup`, writes to `backups/postgres`
- Caddy dashboard proxy: http://localhost:8080
- Caddy API proxy: http://localhost:8001

## Test Suites

Install the backend development tools once:

```powershell
cd backend
..\.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[dev]"
```

Run the regular backend and frontend suites without external test services:

```powershell
.venv\Scripts\python.exe -m pytest backend\tests -q
cd frontend
npm run test:run
```

Run the same quality checks as CI from `backend`:

```powershell
..\.venv\Scripts\python.exe -m ruff check app tests
..\.venv\Scripts\python.exe -m ruff format --check app tests
..\.venv\Scripts\python.exe -m mypy
..\.venv\Scripts\python.exe -m pip_audit --skip-editable
..\.venv\Scripts\python.exe -m pytest tests -q --cov=app --cov-report=term
```

And from `frontend`:

```powershell
npm run lint
npm run typecheck
npm run test:coverage
npm audit --omit=dev --audit-level=high
```

mypy checks `backend/app` with the settings in `backend/pyproject.toml`. It
always type checks for Linux, the production platform, so results match on
Windows. Property-based tests use Hypothesis. Set `HYPOTHESIS_PROFILE=thorough`
to run 2000 examples per property instead of the default 100.

Run the real Postgres, Redis, Alembic, and FastAPI integration suite on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File infra\run-integration-tests.ps1
```

The integration runner starts the disposable services from
`docker-compose.test.yml`, migrates an empty Postgres database to Alembic head,
runs tests marked `integration`, and removes the test volumes afterward. Use
`-KeepServices` only when the disposable database and Redis instance are needed
for debugging. The same unit, integration, frontend, lint, format, type,
dependency audit, coverage, compile, and build checks run in
`.github/workflows/ci.yml`.

Two repository settings are recommended on GitHub but are not enabled by the
code:

- Settings > Code security: enable Dependabot alerts and Dependabot security
  updates. `.github/dependabot.yml` covers routine version updates.
- Settings > Branches: add a protection rule for `main` that requires the
  `backend-quality`, `backend-tests`, and `frontend` checks. This requires pull
  requests instead of direct pushes to `main`.
