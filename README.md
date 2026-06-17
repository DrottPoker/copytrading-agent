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
- `wallet_positions`, current open perp positions with `position_value_usd`
- `wallet_scores`
- `wallet_score_snapshots`
- `active_copy_wallets`
- `copy_signals`
- `copy_trades`
- `source_trade_links`
- `source_trades`
- `source_trade_ignored_fills`
- `source_trade_sync_states`
- `risk_events`
- `settings`
- `job_locks`
- `paper_trading_accounts`
- `paper_copy_allocations`
- `paper_positions`
- `paper_copy_fills`, simulated paper fills with source perp equity snapshots
- `audit_logs`

Run migrations from the repository root:

```bash
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```

Check current migration:

```bash
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini current
```

When running with Docker Compose, use:

```bash
docker compose -f docker-compose.vps.yml run --rm backend python -m alembic upgrade head
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
- Wallet detail pages also show current perp state, unrealized PnL, current
  unrealized drawdown, and spot balances.

Notes:

- Hyperliquid `userFillsByTime` is queried through `POST /info`.
- Wallet detail current state queries Hyperliquid `perpDexs`, then fetches
  `clearinghouseState` for default perp plus each known perp dex. Perp account
  value and open positions are aggregated across those venues. Position storage
  is only synced when every requested perp state fetch succeeds.
- Hyperliquid `spotClearinghouseState` is queried separately for spot balances.
- Each response is capped by Hyperliquid, so large backfills are paged conservatively.
- Imported fills are deduplicated by `wallet_address + external_fill_id`.
- By default, imports only store perp fills; spot-only wallets can be pruned.
- `targetFills` is counted after filtering, so `targetFills: 10000` means up to
  10k stored perp fills, not 10k raw spot/perp fills.
- Fill rows store only compact configured raw payload fields to keep database growth under control.
- Wallet scores can be recalculated with `POST /scores/recalculate` and are shown in the wallet pool.
- Scoring reconstructs source perp trades and ignores close-only PnL from positions that opened before the observed import window.
- Reconstructed source trades are materialized in `source_trades` and refreshed
  only when a wallet's fill count or latest fill timestamp changes.
- Consistency score now penalizes concentrated profits by measuring effective
  winning trades from winning closed trade profit shares.
- Profitability score is scale-invariant. It combines total net ROI, average
  trade ROI, and median trade ROI with 55/30/15 weights instead of rewarding
  absolute dollar PnL or current-equity effects from deposits and withdrawals.
  Each ROI subscore maps 0% or lower to 0 and +5% to 100.
- Score rows are kept only for wallets that still exist in the watched wallet
  pool, so pruned wallets do not remain rankable through stale scores.
- Wallet detail pages show reconstructed source trades from `GET /wallets/{address}/source-trades`.

## Phase 5

Realtime fill monitoring is available through the worker, Redis, API events, and dashboard.

Automated sourcing runs through Discovery. Hyperliquid leaderboard data is used
as a discovery source, not as a separate direct-to-pool import flow.

Worker loops:

By default, `backend/config/app.json` has `worker_run_in_api_process: true`.
That means starting the backend also starts discovery, pool reimport, wallet
scoring, pruning, and realtime monitoring. Run `python -m app.workers.monitor_worker`
separately only if you first set `worker_run_in_api_process` to `false`.

Docker Compose runs a dedicated `worker` service and overrides
`WORKER_RUN_IN_API_PROCESS=false` for the backend service. That keeps one worker
owner in compose while preserving the single-process default for local backend
development.

API:

- `POST /wallets/fills/import-pool`
- `POST /wallets/prune-all`
- `GET /events/recent`
- `GET /events` Server-Sent Events stream
- `GET /paper-trading`

Dashboard:

- `http://127.0.0.1:3000/live-feed`
- `http://127.0.0.1:3000/paper-trading`

Notes:

- The worker subscribes to Hyperliquid `userFills` for up to `max_realtime_wallets`.
- Realtime subscriptions reserve slots for source wallets with open paper
  positions first, then fill remaining slots with the highest positive
  `wallet_scores.score` wallets and active, exit-only, candidate, or copy-enabled
  fallback wallets.
- Automated sourcing runs through Discovery using `backend/config/discovery.json`.
- Discovery defaults to the configured Hyperliquid leaderboard and Hyperdash sources.
- Discovery auto-import runs every 6 hours by default.
- Discovery config is organized into discovery sources, import scheduling,
  prefiltering, candidate backfill, quality checks, and promotion.
- Pool reimport and shared fill-import guards live in
  `backend/config/pool_fill_import.json`.
- Discovery imports only new addresses, skipping wallets already in candidates or in the pool.
- Discovery prefilters new candidates, then backfills accepted candidates.
- Candidates that pass backfill quality checks are inserted directly into the wallet pool.
- Discovery retries Hyperliquid 429 responses with backoff and stops the current
  backfill batch cleanly if rate limits persist.
- Manual pruning runs through `POST /wallets/prune-all`, which applies orphan-fill,
  zero-fill, minimum closed-trades, realized drawdown, high-fill low-score,
  and current drawdown cleanup in one reviewed operation.
- Pruning excludes source wallets that still have open paper positions. If a
  source was pruned earlier while paper exposure remains open, paper allocation
  refresh restores it as `exit_only`.
- Wallet risk scoring can include current open perp drawdown from Hyperliquid.
  It also calculates open position stress from live unrealized loss, margin
  usage, and notional exposure. `backend/config/scoring.json` controls whether
  live state is enabled, fetch concurrency, missing-state penalty, and max risk
  penalties.
- Wallet detail pages include a Detailed scoring modal next to the score header.
  It shows gross score, penalty, final score before sample cap, component
  weights, weighted scores, and the input-level subscores behind profitability,
  consistency, risk, copyability, recency, and penalty scoring.
- Paper allocation only selects positive-score enabled wallets. When current
  drawdown scoring is enabled, the source wallet must also have
  `current_drawdown_status = "ok"` from its latest score.
- Pool wallets are incrementally refreshed from their last poll time with a small overlap.
- Manual pool reimport forces the enabled pool to refresh regardless of last poll time.
- The worker runs pool maintenance every 30 minutes by default: pool reimport,
  wallet scoring, then configured prune rules.
- Worker pruning is intentionally sharp by default. `backend/config/prune.json`
  sets `wallet_prune_worker_dry_run` to `false`, so scheduled pruning deletes
  matching wallets and related rows after pool import.
- Discovery import, pool import, scoring, and pruning use database-backed job
  locks so the API process, worker service, and manual dashboard actions do not
  run the same long job concurrently.
- The pool fill importer works through all due wallets in configured batches so older pool wallets are not left unpolled.
- Snapshot messages are stored safely through the same dedupe key as historical imports.
- Non-snapshot realtime fills are published to Redis and shown in the live feed.
- Non-snapshot realtime fills for selected scored wallets feed paper copy simulation.
- A source wallet that falls out of the top 10 stays monitored while any paper
  account still has an open position from that source. When those positions are
  closed, the slot is released to the next highest eligible wallet.
- Sources with open paper positions are immune to pruning until every paper
  position for that source is closed.

## Phase 6

Paper copy simulation is available as the first execution layer.

API:

- `GET /paper-trading`

Dashboard:

- `http://127.0.0.1:3000/paper-trading`

Config:

- `backend/config/paper_trading.json`

Default paper accounts:

- `paper_1000`: starts with 1,000 USD.
- `paper_10000`: starts with 10,000 USD.

Sizing policy:

- The top 10 scored wallets are eligible for paper copy allocation.
- Open paper-position sources have realtime priority until exit, so a newly
  promoted top 10 wallet may wait for a free subscription slot.
- Retained sources outside the current top 10 can add to existing matching paper
  positions and can reduce or close them, but cannot open completely new paper
  positions.
- All top 10 ranks receive a 20% account pocket.
- Total open copied margin is capped at 80% of each paper account equity.
- Paper order size is based on source fill notional divided by source perp
  equity, scaled inside that source wallet's pocket.
- Paper fill rows store that source perp equity snapshot as
  `paper_copy_fills.source_perp_equity_usd`. The API also exposes the read alias
  `sourceAccountValueUsd` for the same value.
- Source perp equity is fetched from Hyperliquid `clearinghouseState` per perp
  dex. Spot balances are not used for paper copy sizing. For isolated HIP-3
  positions, Hyperliquid `accountValue` can be isolated position equity and can
  move together with margin used.
- Source perp equity is required for opens and adds. It is not required for
  reduce, close, or flip-close parts against existing paper positions, because
  Hyperliquid can report zero source equity after the source has already exited.
- Paper copy reads the source wallet's current per-coin leverage from
  Hyperliquid `clearinghouseState` and uses it for margin accounting. If leverage
  is unavailable for a coin, paper falls back to 1x.
- A configurable fee rate is applied to paper fills.
- Paper opens below `paper_copy_min_order_notional_usd` are skipped before any
  position is created.
- Paper execution waits the configured simulated latency, reads live mids, and
  applies adverse slippage to the execution price.
- If live mids are enabled and default `allMids` lacks a `dex:COIN` market,
  paper copy falls back to dex-specific `allMids`, then `metaAndAssetCtxs`.
- Paper fills are skipped when live mid price drift from the source fill price
  is above the configured drift limit.
- Same-timestamp source fills are processed with close and flip-close fills first
  by descending source `startPosition`, so split source exits reduce paper
  positions in a stable order.
- Skip reasons distinguish minimum notional, source-wallet pocket cap, total
  account cap, missing matching positions, and price safety guards.
- The paper trading dashboard polls the summary API and shows live mark prices,
  unrealized PnL, account PnL, open positions, source-wallet PnL, allocations,
  and recent paper fills.
- Allocation rows show current pocket usage from open paper margin. The dashboard
  hides old inactive allocation rows unless that source still has open paper
  positions.
- Paper account state, copied positions, copied fills, and allocations are stored
  in Postgres. Worker restarts recover missed fills from the oldest open paper
  position when a source still has copied exposure, and rely on copied fill IDs
  to avoid duplicate simulation.
- Recovery can retry exit skip rows caused by unavailable source state or
  unavailable execution price, so a copied close is not permanently blocked by a
  transient data issue.
- Recovery also compares open paper positions against source live perp state. If
  the source no longer has the same coin and side, paper closes the position at
  the current simulated market price with normal fee and slippage.

Notes:

- This is paper money only. It never places Hyperliquid orders.
- Full old history is not imported into fresh paper accounts. Recovery only
  replays fills after a source wallet already has paper copy history or open
  paper positions.
- Existing paper account balances are not reset when `starting_balance_usd` is
  edited. A reset workflow should be added before serious experiments.

## Local Development

Tweakable non-secret settings live in config files:

- `backend/config/app.json`
- `backend/config/discovery.json`
- `backend/config/paper_trading.json`
- `backend/config/pool_fill_import.json`
- `backend/config/prune.json`
- `backend/config/scoring.json`
- `frontend/config/app.json`

`backend/config/scoring.json` uses organized sections for schedule, window,
component weights, profitability, consistency, risk, copyability, recency,
penalties, and window scores.

`backend/config/discovery.json` uses organized sections for discovery sources,
discovery import, prefiltering, candidate backfill, quality checks, and
promotion.

`backend/config/pool_fill_import.json` owns scheduled pool reimport settings and
the shared fill-import storage and market-filter settings.

Use `.env` only for secrets and connection strings:

```bash
cp .env.example .env
```

Change `DASHBOARD_AUTH_PASSWORD` before exposing the dashboard or API. Backend
auth is enabled by default and protects every route except `/health` and
`/ready`. The dashboard sends backend credentials from the Next.js server and
proxies browser API calls through `/api/backend`, so credentials are not placed in
the client bundle.

Run the stack:

```bash
docker compose up --build
```

Services:

- Dashboard: http://localhost:3000
- Backend: http://localhost:8000
- Health: http://localhost:8000/health
- Caddy dashboard proxy: http://localhost:8080
- Caddy API proxy: http://localhost:8001

## VPS Deployment

Use `docker-compose.vps.yml` for a Linux VPS. It exposes only Caddy on ports 80
and 443, keeps backend and frontend on the internal Docker network, and persists
Redis data in a Docker volume.

Required first-time flow:

```bash
cp .env.example .env
# Edit DATABASE_URL, DATABASE_URL_DIRECT, DASHBOARD_AUTH_PASSWORD, and DASHBOARD_DOMAIN.
docker compose -f docker-compose.vps.yml build
docker compose -f docker-compose.vps.yml run --rm backend python -m alembic upgrade head
docker compose -f docker-compose.vps.yml up -d
```

Full guide: [docs/deployment.md](docs/deployment.md)

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

Current wallet scoring feeds paper copy allocation, but it is still a research
ranking signal. Do not use it for live allocation until paper performance has
been validated with latency, slippage, exits, and account-level risk controls.
