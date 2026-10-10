# Hyperliquid Copy Agent

A system that finds profitable Hyperliquid traders, copies their perp trades in
a simulated paper account, and can copy them with real money once the paper
results justify it.

It watches a large pool of wallets, ranks them on their own trading history,
follows the best few in realtime, and mirrors their entries and exits at a size
that fits our account. Paper and live copy use the same decision rules, so the
paper account shows what live trading would have done.

Live trading is off by default. Read the [risk model](docs/risk-model.md)
before you turn it on.

## Documentation

- [Overview](docs/overview.md): what the system does and how the parts fit
  together, in plain language.
- [Risk model](docs/risk-model.md): how position size and losses are limited,
  and the lessons behind those limits.
- [Features](docs/features.md): every feature, endpoint, and setting in detail.
- [Architecture](docs/architecture.md): services, workers, data flows, and the
  database.
- [Development](docs/development.md): local setup, configuration, and tests.
- [Deployment](docs/deployment.md): VPS install, updates, live trading
  activation, and backups.
- [Changelog](CHANGELOG.md): what changed and when, including the original
  development phases.

## Quick Start

Requirements: Docker with Compose. For development outside Docker, Python 3.14
and Node.js 24 LTS.

```bash
cp .env.example .env
docker compose up --build
```

Database migrations run automatically before the backend and workers start.

Set `DASHBOARD_AUTH_PASSWORD` in `.env` before exposing anything. The dashboard
runs at http://localhost:3000 and the API at http://localhost:8000. Paper
copytrading starts once you create and start a paper account on the Accounts
page.

## Safety Defaults

- `LIVE_TRADING_ENABLED=false` in `.env` is the only switch for real orders.
- The Hyperliquid private key lives only in `.env`, never in the database. In
  production it must be an API wallet key, which can trade but not withdraw.
- Live accounts start disabled and need a fresh exchange reconciliation before
  they can trade.
- Every route except `/health` and `/ready` requires dashboard authentication.

## Tests

```bash
cd backend && python -m pytest tests -q
cd frontend && npm run test:run
```

The full CI checks and the Postgres integration suite are described in
[docs/development.md](docs/development.md#test-suites).
