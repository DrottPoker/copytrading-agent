# Hyperliquid Copy Agent

Paper-first Hyperliquid copytrading research system.

The MVP will monitor and score a large wallet pool with polling, reserve realtime
WebSocket monitoring for the active/exit-only copy set, and keep live trading
disabled by default.

## Phase 1

This repository currently contains the foundation:

- FastAPI backend with `/health`
- Redis and Neon/Postgres connection checks
- Worker entrypoint placeholder
- Next.js internal dashboard shell
- Docker Compose for backend, worker, frontend, Redis, and Caddy
- Paper-first config defaults with a live trading acknowledgement guard

## Phase 2

The database layer uses Alembic migrations and SQLAlchemy models.

Current schema includes:

- `watched_wallets`
- `wallet_fills`
- `wallet_positions`
- `wallet_scores`
- `wallet_score_snapshots`
- `active_copy_wallets`
- `copy_signals`
- `copy_trades`
- `source_trade_links`
- `risk_events`
- `settings`
- `audit_logs`

Run migrations from the repository root:

```bash
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```

Check current migration:

```bash
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini current
```

## Phase 3

Wallet management is available through the API and dashboard.

API:

- `GET /wallets`
- `POST /wallets`
- `GET /wallets/{address}`
- `PATCH /wallets/{address}`
- `DELETE /wallets/{address}`

Dashboard:

- `http://127.0.0.1:3000/wallets`

Example wallet payload:

```json
{
  "address": "0x0000000000000000000000000000000000000000",
  "label": "Research wallet",
  "notes": "Candidate source wallet"
}
```

## Phase 4

Historical Hyperliquid fill import is available as a manual operation.

API:

- `GET /wallets/{address}/fills`
- `POST /wallets/{address}/fills/import`

Import request defaults to the last 30 days:

```json
{
  "days": 30,
  "maxPages": 25,
  "targetFills": 10000,
  "aggregateByTime": false
}
```

Dashboard:

- Use `Import fills` in the Wallet Pool row.
- Open a wallet detail page from the address link to inspect recent fills.
- Wallet detail pages also show current perp state, unrealized PnL, and spot balances.

Notes:

- Hyperliquid `userFillsByTime` is queried through `POST /info`.
- Hyperliquid `clearinghouseState` and `spotClearinghouseState` are queried for current wallet exposure.
- Each response is capped by Hyperliquid, so large backfills are paged conservatively.
- Imported fills are deduplicated by `wallet_address + external_fill_id`.
- By default, imports only store perp fills; spot-only wallets can be pruned.
- `targetFills` is counted after filtering, so `targetFills: 10000` means up to
  10k stored perp fills, not 10k raw spot/perp fills.
- Fill rows store only compact configured raw payload fields to keep database growth under control.
- Wallet scores can be recalculated with `POST /scores/recalculate` and are shown in the wallet pool.
- Scoring reconstructs source perp trades and ignores close-only PnL from positions that opened before the observed import window.
- Wallet detail pages show reconstructed source trades from `GET /wallets/{address}/source-trades`.

## Phase 5

Realtime fill monitoring is available through the worker, Redis, API events, and dashboard.

The worker also imports the public Hyperliquid 30D leaderboard into the wallet
pool every 24 hours by default.

Worker loops:

By default, `backend/config/app.json` has `worker_run_in_api_process: true`.
That means starting the backend also starts discovery, pool reimport, wallet
scoring, pruning, and realtime monitoring. Run `python -m app.workers.monitor_worker`
separately only if you first set `worker_run_in_api_process` to `false`.

API:

- `POST /leaderboard/import`
- `POST /wallets/fills/import-pool`
- `POST /wallets/prune-all`
- `GET /events/recent`
- `GET /events` Server-Sent Events stream

Dashboard:

- `http://127.0.0.1:3000/live-feed`

Notes:

- The worker subscribes to Hyperliquid `userFills` for up to `max_realtime_wallets`.
- Automated sourcing runs through Discovery using `backend/config/discovery.json`.
- Discovery defaults to the configured Hyperliquid leaderboard and Hyperdash sources.
- Discovery auto-import runs every hour by default.
- Discovery imports only new addresses, skipping wallets already in candidates or in the pool.
- Discovery prefilters new candidates, then backfills accepted candidates.
- Candidates that pass backfill quality checks are inserted directly into the wallet pool.
- Discovery retries Hyperliquid 429 responses with backoff and stops the current
  backfill batch cleanly if rate limits persist.
- Manual pruning runs through `POST /wallets/prune-all`, which applies orphan-fill,
  zero-fill, minimum closed-trades, historical max drawdown, high-fill low-score,
  and current drawdown cleanup in one reviewed operation.
- Pool wallets are incrementally refreshed from their last poll time with a small overlap.
- Manual pool reimport forces the enabled pool to refresh regardless of last poll time.
- The worker runs pool maintenance every 10 minutes by default: pool reimport,
  wallet scoring, then configured prune rules.
- The pool fill importer works through all due wallets in configured batches so older pool wallets are not left unpolled.
- Snapshot messages are stored safely through the same dedupe key as historical imports.
- Non-snapshot realtime fills are published to Redis and shown in the live feed.
- The realtime selector only monitors active, exit-only, candidate, or copy-enabled wallets.

## Local Development

Tweakable non-secret settings live in config files:

- `backend/config/app.json`
- `backend/config/discovery.json`
- `backend/config/prune.json`
- `backend/config/scoring.json`
- `frontend/config/app.json`

Use `.env` only for secrets and connection strings:

```bash
cp .env.example .env
```

Run the stack:

```bash
docker compose up --build
```

If the frontend runs inside Docker, set `frontend/config/app.json` `serverApiBaseUrl`
to `http://backend:8000` before building the image.

Services:

- Dashboard: http://localhost:3000
- Backend: http://localhost:8000
- Health: http://localhost:8000/health
- Caddy dashboard proxy: http://localhost:8080
- Caddy API proxy: http://localhost:8001

## Safety Defaults

Live trading is disabled in `backend/config/app.json` unless both flags are explicitly set:

```json
{
  "live_trading_enabled": true,
  "live_trading_acknowledged": true
}
```

Do not enable live trading before paper trading proves edge after delay, fees,
slippage, and exit behavior.
