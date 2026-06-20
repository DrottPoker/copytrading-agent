# Hyperliquid Copy Agent

Paper-first Hyperliquid copytrading research system.

The MVP will monitor and score a large wallet pool with polling, reserve realtime
WebSocket monitoring for the active/exit-only copy set, and keep live trading
disabled by default.

## Phase 1

This repository currently contains the foundation:

- FastAPI backend with `/health`
- Redis and local Postgres connection checks
- Worker entrypoint placeholder
- Next.js internal dashboard shell
- Docker Compose for backend, trading worker, maintenance worker, frontend, local Postgres, Redis, and Caddy
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
- The Database page exposes per-index storage and scan counts so large unused
  indexes can be reviewed before any schema change.
- Docker Compose gives the Postgres container a 512 MB shared memory limit so
  heavier aggregate and cleanup queries do not hit Docker's small default
  `/dev/shm` limit.
- The Database page also includes manual fill retention cleanup. It defaults to
  dry-run, keeps 90 days, and protects active, realtime, copy-enabled, open
  paper-position, open-position, and top scored wallets.
- The Database page includes separate ignored-fill cleanup for raw close-only
  and pre-existing-position fills that are not needed for reconstructed source
  trades.
- Wallet scores can be recalculated with `POST /scores/recalculate` and are shown in the wallet pool.
- Wallet pool list and wallet detail responses include `poolRank`, which is the
  current rank among wallets with a stored score.
- Scoring reconstructs source perp trades and ignores close-only PnL from positions that opened before the observed import window.
- Close-only and pre-existing-position fills are diagnostics only. They do not
  reduce wallet score or discovery quality because they usually mean the import
  window missed the original entry.
- Reconstructed source trades are materialized in `source_trades` and refreshed
  only when a wallet's fill count or latest fill timestamp changes.
- Recency score uses the latest non-liquidation trading fill, so opens, adds,
  reduces, closes, and flips count as activity while liquidation fills do not.
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

Realtime fill monitoring is available through the trading worker, Redis, API events, and dashboard.

Automated sourcing runs through Discovery. Hyperliquid leaderboard data is used
as a discovery source, not as a separate direct-to-pool import flow.

Worker loops:

By default, `worker_role` is `all`, which starts both trading and maintenance
loops in one process. Run `python -m app.workers.monitor_worker` with
`WORKER_ROLE=trading` to run only realtime and paper-copy recovery, or
`WORKER_ROLE=maintenance` to run only discovery, pool reimport, scoring, and
pruning.

Docker Compose runs two dedicated worker services and overrides
`WORKER_RUN_IN_API_PROCESS=false` for the backend service:

- `trading-worker`: realtime top-wallet subscriptions, realtime fills, paper
  copy, and paper-copy recovery.
- `maintenance-worker`: discovery, pool reimport, scoring, and pruning.

Workers publish lightweight heartbeats to Postgres so the Ops Health page can
show whether `trading-worker` and `maintenance-worker` are fresh, stale, or
missing.

API:

- `POST /wallets/fills/import-pool`
- `POST /wallets/prune-all`
- `GET /events/recent`
- `GET /events` Server-Sent Events stream
- `GET /ops/health`
- `GET /analytics`
- `GET /paper-trading`
- `POST /paper-trading/positions/{position_id}/close`
- `POST /paper-trading/sources/{source_wallet}/close`

Dashboard:

- `http://127.0.0.1:3000/live-feed`
- `http://127.0.0.1:3000/analytics`
- `http://127.0.0.1:3000/ops`
- `http://127.0.0.1:3000/paper-trading`

Notes:

- The trading worker subscribes to Hyperliquid `userFills` for up to
  `max_realtime_wallets`.
- Realtime subscriptions are derived from the same paper allocation refresh used
  by the paper summary and copy engine. Open paper-position sources reserve
  slots first, then remaining slots go to the highest scored eligible copy
  candidates.
- Automated sourcing runs through Discovery using `backend/config/discovery.json`.
- Discovery defaults to Hyperliquid 1D, 7D, and 30D leaderboard sources,
  Hyperliquid vault leaders, leaderboard subaccounts, and configured Hyperdash
  sources. All-time leaderboard discovery is available as a manual source, but
  it is not enabled by default because it is less useful for current copy
  trading candidates.
- Vault discovery imports open normal vault addresses and their leader wallets as
  separate candidates, while skipping HLP protocol parent/child vaults. Vaults
  below the discovery `min_account_value_usd` threshold are skipped before the
  source limit is filled. Remaining vaults are ranked by 30D ROI first, then TVL
  as the stability tie-breaker.
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
  refresh restores it as a neutral `pool` row.
- Wallet risk scoring can include current open perp drawdown from Hyperliquid.
  It also calculates open position stress from live unrealized loss, margin
  usage, and notional exposure. `backend/config/scoring.json` controls whether
  live state is enabled, fetch concurrency, missing-state penalty, and max risk
  penalties.
- Wallet detail pages include a Detailed scoring modal next to the score header.
  It shows gross score, penalty, final score before sample cap, component
  weights, weighted scores, and the input-level subscores behind profitability,
  consistency, risk, copyability, recency, and penalty scoring.
- The Analytics tab aggregates pool coverage, score distribution, drawdown
  state, opportunity wallets, risk watchlists, 30D source and coin performance,
  paper source performance, skip reasons, discovery funnel quality, and data
  freshness from `GET /analytics`.
- Paper allocation only selects positive-score enabled wallets. When current
  drawdown scoring is enabled, the source wallet must also have
  `current_drawdown_status = "ok"` from its latest score.
- Pool wallets are incrementally refreshed from their last poll time with a small overlap.
- Manual pool reimport forces the enabled pool to refresh regardless of last poll time.
- The maintenance worker runs pool maintenance every 30 minutes by default:
  pool reimport, wallet scoring, then configured prune rules.
- Worker pruning is intentionally sharp by default. `backend/config/prune.json`
  sets `wallet_prune_worker_dry_run` to `false`, so scheduled pruning deletes
  matching wallets and related rows after pool import.
- Discovery import, pool import, scoring, pruning, and paper-copy recovery use
  database-backed job locks so worker services and manual dashboard actions do
  not run the same long job concurrently.
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
- `POST /paper-trading/accounts/{account_key}/reset`
- `POST /paper-trading/positions/{position_id}/close`
- `POST /paper-trading/sources/{source_wallet}/close`

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
- Stored paper position margin represents simulated entry margin. Adds increase
  margin by the copied fill margin, and partial closes reduce margin
  proportionally. Live current notional is calculated separately from mark price.
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
- The paper trading dashboard polls the summary API and separates total,
  realized, and unrealized PnL at the top of the page.
- The dashboard shows paper accounts, monitored sources, currently trading
  sources, open positions, wallet PnL history, closed trade history, and recent
  fills as compact lists without horizontal scrolling.
- Source, position, wallet-history, closed-trade, and fill rows show the wallet
  label when available and fall back to the short address.
- Source rows split source PnL into realized and unrealized values, with total
  PnL shown as supporting context.
- Wallet PnL history rows use `monitored` when the source has a realtime slot
  and `history` otherwise.
- Wallet PnL history, closed trade history, and recent fills show 10 rows per
  page with pagination controls.
- Account rows include a reset action that restores that account's configured
  starting balance, cash balance, equity, realized PnL, and fee counters while
  leaving open positions, copied fills, and closed trade history intact.
- Source rows show a primary monitor status and a source substatus. Primary
  status is `monitored` when the source has a realtime slot and `waiting` when
  it does not. Substatus is `trading`, `retained`, `waiting for trades`, or
  `waiting for slot`.
- Source row substatus is aggregated across all paper accounts. A source is
  `trading` if any enabled paper account can still open or manage that source
  and the source has open paper exposure.
- Source rows display `pool #` from the wallet score pool rank, not the realtime
  monitor slot or retained-source order. Retained rows also show the blocking
  reason, such as outside copy top 10, drawdown blocked, paper account disabled,
  cooldown, or missing score.
- Closed trade history comes from paper `close` and `flip_close` executions.
  Raw fills and skip rows remain available in the API for diagnostics, but they
  are not shown as trade history.
- Open position rows include a manual close action. Manual closes use the same
  live mark, adverse slippage, and fee model as automated paper closes, then
  record a normal `close` row in `paper_copy_fills`.
- Copy source rows with open exposure include a close-all action. It closes all
  open paper positions for that source wallet across paper accounts using the
  same manual close execution model.
- Allocation source rows show current pocket usage from open paper margin.
- Paper account state, copied positions, copied fills, and allocations are stored
  in Postgres. Worker restarts recover missed fills for open-exposure sources
  and currently monitored allocation sources, and rely on copied fill IDs to
  avoid duplicate simulation.
- Realtime fills, recovery reconciliation, and manual closes are serialized per
  source wallet with a Postgres advisory transaction lock.
- Paper account rows are locked before copied fills are written, so different
  source wallets cannot concurrently update the same paper account balance.
- Recovery can retry exit skip rows caused by unavailable source state or
  unavailable execution price, so a copied close is not permanently blocked by a
  transient data issue.
- Recovery also compares open paper positions against source live perp state. If
  the source no longer has the same coin and side, paper closes the position at
  the current simulated market price with normal fee and slippage.
- In split-worker deployments, the trading worker runs paper-copy recovery every
  `paper_copy_recovery_interval_seconds` so maintenance imports cannot block
  trading state reconciliation.

Notes:

- This is paper money only. It never places Hyperliquid orders.
- Full old history is not imported into fresh paper accounts. Recovery only
  replays fills for current allocation sources or sources with open paper
  positions.
- Existing paper account balances are not reset when `starting_balance_usd` is
  edited. Use the dashboard account reset action to apply configured starting
  capital to an existing paper account.

## Local Development

Tweakable non-secret settings live in config files:

- `backend/config/app.json`
- `backend/config/discovery.json`
- `backend/config/database.json`
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

`backend/config/database.json` owns manual database maintenance defaults such as
fill retention days, batch size, max rows, and protected top scored wallets.

The Ops Health page reads runtime settings from environment variables:

- `WORKER_HEARTBEAT_INTERVAL_SECONDS`
- `WORKER_HEARTBEAT_STALE_SECONDS`
- `OPS_DISK_PATH`
- `BACKUP_STATUS_DIRECTORY`
- `BACKUP_STATUS_STALE_SECONDS`

Use `.env` only for secrets and connection strings:

```bash
cp .env.example .env
```

Docker Compose uses local Postgres by default. Set `POSTGRES_DB`,
`POSTGRES_USER`, and `POSTGRES_PASSWORD`; the compose files build
`DATABASE_URL` and `DATABASE_URL_DIRECT` for the app containers.

Change `DASHBOARD_AUTH_PASSWORD` before exposing the dashboard or API. Backend
auth is enabled by default and protects every route except `/health` and
`/ready`. The dashboard sends backend credentials from the Next.js server and
proxies browser API calls through `/api/backend`, so credentials are not placed in
the client bundle.

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
- Caddy dashboard proxy: http://localhost:8080
- Caddy API proxy: http://localhost:8001

## VPS Deployment

Use `docker-compose.vps.yml` for a Linux VPS. It exposes only Caddy on ports 80
and 443, keeps backend, frontend, Postgres, and Redis on the internal Docker
network, and persists Postgres and Redis data in Docker volumes.

Required first-time flow:

```bash
cp .env.example .env
# Edit POSTGRES_PASSWORD, DASHBOARD_AUTH_PASSWORD, and DASHBOARD_DOMAIN.
docker compose -f docker-compose.vps.yml build
docker compose -f docker-compose.vps.yml up -d postgres redis
docker compose -f docker-compose.vps.yml run --rm backend python -m alembic upgrade head
docker compose -f docker-compose.vps.yml up -d
```

Local VPS Postgres data lives in the `postgres_data` Docker volume. Do not run
`docker compose -f docker-compose.vps.yml down -v` unless you intentionally want
to delete the database. Use `bash infra/postgres-backup-local.sh` for manual
backups or add the cron job from the deployment guide. Set `POSTGRES_DB`,
`POSTGRES_USER`, and `POSTGRES_PASSWORD` before the first Postgres start because
changing them later does not alter an existing database volume.

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
