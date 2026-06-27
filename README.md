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
- `trading_accounts`, generic paper and live account registry
- `trading_positions`, live-ready account/source/coin position state
- `trading_orders`, idempotent order intent and exchange status records
- `trading_fills`, reconciled paper or live execution fills
- `paper_trading_accounts`
- `paper_copy_allocations`
- `paper_positions`
- `paper_copy_fills`, simulated paper fills with source perp equity snapshots
- `audit_logs`

`wallet_fills.notional_usd` is stored for imported fills and backfilled from
`price * size` by migrations when older databases are upgraded.

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
- Wallet detail pages show 24h, 7d, 30d, 60D score-window, and all-time
  performance windows from reconstructed source trades. Close-only and
  pre-existing-position fills are excluded from these windows.
- Wallet detail pages also show current perp state, open position times when
  reconstructed, unrealized PnL, current unrealized drawdown, and spot balances.

Notes:

- Hyperliquid `userFillsByTime` is queried through `POST /info`.
- Wallet detail current state queries Hyperliquid `perpDexs`, then fetches
  `clearinghouseState` for default perp plus each known perp dex. Perp account
  value and open positions are aggregated across those venues. Position storage
  is only synced when every requested perp state fetch succeeds.
- Hyperliquid `userAbstraction` and `spotClearinghouseState` are queried
  separately. If a wallet is unified, account value and current drawdown
  denominator use unified USDC from `spotClearinghouseState`; `perp_equity_usd`
  remains the raw perps-only value.
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
- Wallet scores can be recalculated synchronously with `POST /scores/recalculate`
  or started as a background dashboard operation with
  `POST /scores/recalculate/start`.
- Wallet pool list and wallet detail responses include `poolRank`, which is the
  current rank among wallets with a stored score.
- Scoring reconstructs source perp trades and ignores close-only PnL from positions that opened before the observed import window.
- Close-only and pre-existing-position fills are diagnostics only. They do not
  reduce wallet score or discovery quality because they usually mean the import
  window missed the original entry.
- Reconstructed source trades are materialized in `source_trades` and refreshed
  only when a wallet's fill count or latest fill timestamp changes.
- Wallet detail source trades default to all materialized history for the
  wallet. Callers can still pass `days` to inspect a bounded window.
- Wallet score values on the same page remain score-window metrics and are
  labeled separately from all-history source trade totals.
- Wallet detail open perp positions show live unrealized PnL from Hyperliquid
  plus realized PnL, add fills, reduce fills, and liquidation fills from open
  reconstructed source trades when available.
- Score-window realized PnL and Profitability include realized partial closes
  from still-open source trades. Win rate and closed-trade count still only
  count fully closed reconstructed trades.
- Materialized source trades store whether an observed close was a liquidation,
  the liquidation fill count, and liquidation notional so trade history can tag
  affected closed trades. Scoring uses forced exits as normal component inputs:
  severity reduces Risk and liquidation close-fill ratio reduces Copyability,
  while there is no standalone final-score liquidation deduction.
- Recency score uses the latest non-liquidation trading fill, so opens, adds,
  reduces, closes, and flips count as activity while liquidation fills do not.
- Consistency score measures repeatability and evenness through profit
  distribution, largest-win dependency, ROI stability, downside stability,
  active-day regularity, and max inactive gap. It does not score win rate or
  profit factor.
- Profitability score is scale-invariant. It combines total net ROI, average
  trade ROI, and median trade ROI with 55/30/15 weights instead of rewarding
  absolute dollar PnL or current-equity effects from deposits and withdrawals.
  Each ROI subscore maps 0% or lower to 0 and +3% to 100.
- Copyability score excludes trade count because sample size is handled by
  sample caps and the low-confidence penalty. It scores copyable trade ratio,
  median trade notional, p25 trade notional, and execution simplicity.
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
`WORKER_ROLE=trading` to run realtime copy execution, copy recovery, and live
reconciliation, or
`WORKER_ROLE=maintenance` to run only discovery, pool reimport, scoring, and
pruning.

Docker Compose runs two dedicated worker services and overrides
`WORKER_RUN_IN_API_PROCESS=false` for the backend service:

- `trading-worker`: realtime top-wallet subscriptions, realtime fills, paper
  copy, live copy when enabled, copy recovery, and live reconciliation.
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
- `GET /trading/accounts`
- `POST /trading/accounts/live`
- `PATCH /trading/accounts/{account_key}/status`
- `DELETE /trading/accounts/{account_key}`
- `POST /trading/accounts/{account_key}/start`
- `POST /trading/accounts/{account_key}/stop`
- `POST /trading/accounts/{account_key}/close-all-and-stop`
- `POST /trading/accounts/{account_key}/reconcile`
- `POST /trading/positions/{position_id}/close`
- `POST /trading/testnet/orders`
- `GET /paper-trading`
- `POST /paper-trading/accounts`
- `DELETE /paper-trading/accounts/{account_key}`
- `POST /paper-trading/accounts/{account_key}/start`
- `POST /paper-trading/accounts/{account_key}/stop`
- `POST /paper-trading/accounts/{account_key}/close-all-and-stop`
- `POST /paper-trading/positions/{position_id}/close`
- `POST /paper-trading/sources/{source_wallet}/close`

`GET /trading/accounts` returns the generic paper/live account registry plus
live positions, recent live fills, and recent live order attempts for Trading
dashboards. `GET /paper-trading` remains the paper simulator summary and manual
paper action API.

Dashboard:

- `http://127.0.0.1:3000/live-feed`
- `http://127.0.0.1:3000/analytics`
- `http://127.0.0.1:3000/ops`
- `http://127.0.0.1:3000/accounts`
- `http://127.0.0.1:3000/trading`

Notes:

- The trading worker subscribes to Hyperliquid `userFills` for up to
  `max_realtime_wallets`.
- Realtime subscriptions are derived from the same paper allocation refresh used
  by the paper summary and copy engine. Open paper-position sources reserve
  slots first, then remaining slots go to the highest scored eligible copy
  candidates. The worker checks the desired subscription list every
  `realtime_subscription_refresh_seconds` and reconnects only when the list
  changes.
- Automated sourcing runs through Discovery using `backend/config/discovery.json`.
- Discovery defaults to Hyperliquid 1D, 7D, and 30D leaderboard sources,
  Hyperliquid 7D and 30D vault leaders, leaderboard subaccounts, HyperTracker
  PnL segments, HyperTracker avg daily perp PnL leaderboard, and configured
  Hyperdash profitable cohorts. All-time leaderboard discovery is available as
  a manual source, but it is not enabled by default because it is less useful
  for current copy trading candidates.
- Vault discovery imports open normal vault addresses and their leader wallets as
  separate candidates, while skipping HLP protocol parent/child vaults. Vaults
  below the discovery `min_account_value_usd` threshold are skipped before the
  source limit is filled. Vault leader sources rank by their own window ROI
  first, 7D or 30D, then TVL as the stability tie-breaker.
- Discovery auto-import runs every 6 hours by default.
- Discovery config is organized into discovery sources, import scheduling,
  prefiltering, candidate backfill, quality checks, and promotion.
- Discovery candidate backfill and pool fill reimport use 90 days of historical
  fills by default so source-trade reconstruction has a larger entry window.
- Pool reimport and shared fill-import guards live in
  `backend/config/pool_fill_import.json`.
- Discovery imports only new addresses, skipping wallets already in candidates or in the pool.
- Discovery prefilters new candidates, then backfills accepted candidates.
- Candidates that pass backfill quality checks are inserted directly into the wallet pool.
- Discovery retries Hyperliquid 429 responses with backoff and stops the current
  backfill batch cleanly if rate limits persist.
- Manual pruning runs through `POST /wallets/prune-all`, which applies orphan-fill,
  zero-fill, stale-fill, minimum closed-trades, realized drawdown, low-score,
  and current drawdown cleanup in one reviewed operation.
- Pruning excludes source wallets that still have open paper positions. If a
  source was pruned earlier while paper exposure remains open, paper allocation
  refresh restores it as a neutral `pool` row.
- Wallet risk scoring can include current open perp drawdown from Hyperliquid.
  It also calculates open position stress from live unrealized loss, margin
  usage, and notional exposure. Unified wallets use unified account value from
  `spotClearinghouseState` as the denominator; standard wallets use perps
  account value. `backend/config/scoring.json` controls whether live state is
  enabled, fetch concurrency, missing-state penalty, max risk penalties, and
  current-drawdown final score caps. By default, current drawdown penalty scales
  from 5% to 75% drawdown, and the final score cap scales from 25% to 100%
  drawdown.
- Wallet detail pages include a Detailed scoring modal next to the score header.
  It shows gross score, penalty, final score before sample cap, component
  weights, weighted scores, live risk score cap, and the input-level subscores
  behind profitability, consistency, risk, copyability, recency, and penalty
  scoring.
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
- Non-snapshot realtime fills for selected scored wallets feed paper copy and
  live copy when live execution is enabled.
- A source wallet that falls out of the top 10 stays monitored while any paper
  account still has an open position from that source. When those positions are
  closed, the slot is released to the next highest eligible wallet.
- Sources with open paper positions are immune to pruning until every paper
  position for that source is closed.

## Phase 6

Paper copy simulation is available as the first execution layer.

API:

- `GET /paper-trading`
- `POST /paper-trading/accounts`
- `POST /paper-trading/accounts/{account_key}/start`
- `POST /paper-trading/accounts/{account_key}/stop`
- `POST /paper-trading/accounts/{account_key}/close-all-and-stop`
- `POST /paper-trading/accounts/{account_key}/reset`
- `POST /paper-trading/positions/{position_id}/close`
- `POST /paper-trading/sources/{source_wallet}/close`

Dashboard:

- `http://127.0.0.1:3000/trading`
- `http://127.0.0.1:3000/accounts`

Config:

- `backend/config/trading.json`
- `backend/config/paper_trading.json`

Paper accounts:

- Paper accounts are stored in Postgres and created from the dashboard or API.
- `backend/config/trading.json` owns shared copy allocation, min-order, price
  drift, and mid-price cache policy.
- `backend/config/paper_trading.json` owns paper simulation settings only, not
  account creation.

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
- A configurable fee rate is applied to paper fills. The default is 0.045%,
  matching Hyperliquid's base perp taker fee because paper fills model immediate
  taker-style execution.
- Paper opens or adds below `trading_copy_min_order_notional_usd` are adjusted up
  to the minimum when `trading_copy_adjust_small_orders_to_min_order` is enabled
  and the source and account caps can fit the adjusted margin. Otherwise they
  are skipped before any position is created. The default minimum is 10 USD to
  match Hyperliquid's live perp minimum order value.
- New entry fills older than `trading_copy_max_entry_age_seconds` are skipped
  before opening or adding exposure. This prevents snapshot or recovery fills
  from creating late entries minutes after the source traded. Close and reduce
  processing still runs for older fills so exits can catch up safely.
- Paper execution starts the configured simulated latency immediately while
  source account state is fetched in parallel.
  It then reads live mids and applies adverse slippage to the execution price.
- The trading worker keeps a WebSocket `allMids` cache for copy execution when
  `trading_copy_market_price_cache_enabled` is enabled. Fresh cached prices are
  used before HTTP, so realtime fills do not wait on a new `allMids` request per
  batch.
- If the cache is stale or missing a coin, paper copy falls back to HTTP
  `allMids`, then dex-specific `allMids`, then `metaAndAssetCtxs`.
- Paper fills are skipped when adverse live mid price drift from the source fill
  price is above `trading_copy_max_price_drift_bps`, which defaults to 50 bps.
  Favorable drift is allowed and recorded as 0 bps.
- Recent paper fill rows show source price, live mid, adverse drift bps, the
  per-fill drift limit, and min-order adjustment markers when execution details
  are available.
- Open paper position rows show entry execution delay in milliseconds, measured
  from the source fill timestamp to when the paper position was created.
- Same-timestamp source fills are processed with close and flip-close fills first
  by descending source `startPosition`, so split source exits reduce paper
  positions in a stable order.
- Skip reasons distinguish minimum notional, source-wallet pocket cap, total
  account cap, missing matching positions, and price safety guards.
- The Trading page polls the paper summary API and generic trading account API,
  but the cockpit has an explicit Paper or Live mode toggle. The active mode
  owns top metrics, accounts, copy sources, wallet PnL history, open positions,
  closed activity, and recent execution activity. Paper mode shows only paper
  accounts, positions, closed trades, and paper fills. Live mode shows only live
  accounts, live positions, live source activity, reconstructed closed live
  trades, live exchange fills, rejected live orders, and live pre-submit skips
  with skip reasons. Recent Execution Activity uses the same result-oriented
  row semantics in both modes, so filled, skipped, rejected, and failed attempts
  are visible without mixing paper and live rows. Individual live reduce and
  close fills stay in Recent Execution Activity.
- Copy source monitor slots and source eligibility are shared across paper and
  live execution, but each mode renders its own exposure, PnL, activity, and
  execution status. A retained source remains retained in live mode unless the
  shared source eligibility allows new entries.
- The Accounts page stores the last selected account in the browser and
  defaults to that account on the next visit, otherwise the first synced account
  is selected. It shows paper account metrics, charts, allocations, market
  exposure, source performance, open positions, closed trades, and recent fills,
  and shows live account equity, balance, reconciliation, and routing details.
  The live Reconciled card includes a manual refresh icon that posts to
  `POST /trading/accounts/{account_key}/reconcile` and refreshes the selected
  account snapshot.
- The Accounts page can create dashboard-managed paper accounts with a selected
  USD starting balance and live accounts with a wallet name, optional wallet
  address, and optional vault address. Empty wallet address fields use and save
  `HYPERLIQUID_WALLET_ADDRESS`. Live account keys are generated internally from
  the wallet route, so the display name can change without creating a duplicate
  route. New accounts start with trading disabled, and creation immediately
  reconciles the exchange wallet snapshot so equity, balance, and open live
  positions are visible before trading is started. Repeating live account
  creation for an existing wallet route returns the existing local account
  instead of creating a duplicate.
- The Accounts page skips scheduled auto-refresh while the create account
  dialog is open, stores the in-progress draft in browser session storage, and
  keeps the dialog open until the user cancels, presses Escape, closes it, or
  finishes account creation.
- The Accounts page can start, stop, or close all and stop trading for the
  selected account. Starting enables new copy entries for that account.
  Stopping disables new entries and adds while still allowing reduce and exit
  fills to manage existing positions. Close all and stop trading disables the
  account and closes all open positions for that account while other accounts
  keep trading.
- The Accounts page can delete the selected account from local database state.
  Paper delete removes local account positions, fills, allocations, and history.
  Live delete removes local account, order, fill, and position snapshots only,
  and requires live trading to be stopped first. It does not close exchange
  positions.
- The dashboard shows paper accounts, monitored sources, currently trading
  sources, open positions, wallet PnL history, closed trade history, and recent
  fills as compact lists without horizontal scrolling.
- Source, position, wallet-history, closed-trade, and fill rows show the wallet
  label when available and fall back to the short address.
- Source rows split source PnL into realized and unrealized values, with total
  PnL shown as supporting context.
- Wallet PnL history rows use `monitored` when the source has a realtime slot
  and `history` otherwise.
- Wallet PnL history, closed trade history, and recent execution activity show
  10 rows per page with pagination controls.
- Closed trade history rows show compact close time and duration when the
  original paper position open time is available.
- Account rows include a reset action that restores that account's starting
  balance, cash balance, equity, realized PnL, and fee counters while leaving
  open positions, copied fills, and closed trade history intact.
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
  are not shown as trade history. Closed trade rows show a liquidation tag when
  the source close fill was marked as a liquidation by Hyperliquid.
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

- Live trading can place Hyperliquid orders only when live trading is explicitly
  enabled, acknowledged, configured with credentials, and enabled per account.
  Paper execution remains the default simulation layer.
- Live copy reserves one source per live account and market while exposure is
  open. Another source opening the same market is skipped until the market is
  free, even if it is the same side, because Hyperliquid nets the exchange
  position and leverage at account level. Matching exits and adds from the
  already reserved source are still allowed.
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
wallet pool page limit, network, and shared infrastructure request settings.

`backend/config/prune.json` uses organized sections for prune rules, scheduled
worker behavior, and worker execution defaults.

`backend/config/discovery.json` uses organized sections for discovery sources,
discovery import, prefiltering, candidate backfill, quality checks, and
promotion.

`backend/config/pool_fill_import.json` owns scheduled pool reimport settings and
the shared fill-import storage and market-filter settings.

`backend/config/database.json` owns manual database maintenance defaults such as
fill retention days, batch size, max rows, and protected top scored wallets.

`backend/config/live_trading.json` owns live trading enablement,
acknowledgements, account capital mode, copy execution, execution limits, risk
guardrails, and market allow/block lists. `account.capital_mode` defaults to
`unified`, where Hyperliquid `spotClearinghouseState` is the balance source of
truth for live trading capital. Set it to `standard_per_dex` only when the live
wallet is intentionally using separate default and HIP-3 perp balances.

`backend/config/trading.json` owns shared copy policy used by both paper and live
copy: source ranking limits, allocation pockets, total allocation cap, copy
minimum notional, optional min-order adjustment, max entry age, price drift
guard, and the live mid-price cache settings.

`backend/config/paper_trading.json` owns paper-only simulation settings:
paper copy enablement, simulated fee rate, simulated slippage, simulated latency,
and paper recovery cadence.

The Ops Health page reads runtime settings from environment variables:

- `WORKER_HEARTBEAT_INTERVAL_SECONDS`
- `WORKER_HEARTBEAT_STALE_SECONDS`
- `OPS_DISK_PATH`
- `BACKUP_STATUS_ENABLED`
- `BACKUP_STATUS_DIRECTORY`
- `BACKUP_STATUS_STALE_SECONDS`
- `BACKUP_INTERVAL_SECONDS`
- `BACKUP_RETENTION_DAYS`

Use `.env` only for secrets and connection strings:

```bash
cp .env.example .env
```

Docker Compose uses local Postgres by default. Set `POSTGRES_DB`,
`POSTGRES_USER`, and `POSTGRES_PASSWORD`; the compose files build
`DATABASE_URL` and `DATABASE_URL_DIRECT` for the app containers.

Change `DASHBOARD_AUTH_PASSWORD` before exposing the dashboard or API. Backend
auth is enabled by default and protects every route except `/health` and
`/ready`. The dashboard also enforces Basic Auth before serving pages or
`/api/backend` proxy routes. Backend credentials are attached by the Next.js
server, so they are not placed in the client bundle.

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
to delete the database. The `postgres-backup` service runs `pg_dump` every 24
hours by default and writes dumps to `backups/postgres`. `/ops` checks that
folder when `BACKUP_STATUS_ENABLED=true`. Set `POSTGRES_DB`, `POSTGRES_USER`,
and `POSTGRES_PASSWORD` before the first Postgres start because changing them
later does not alter an existing database volume.

Full guide: [docs/deployment.md](docs/deployment.md)

## Safety Defaults

Live trading is disabled in `backend/config/live_trading.json` unless the
enablement and acknowledgement flags are explicitly set:

```json
{
  "enabled": true,
  "acknowledged": true,
  "mainnet_acknowledged": false,
  "account": {
    "capital_mode": "unified"
  },
  "copy_execution": {
    "enabled": false
  }
}
```

Mainnet live trading also requires `mainnet_acknowledged=true`. Automatic live
copy requires both `enabled=true` and `copy_execution.enabled=true`.
When live trading is enabled, startup requires `HYPERLIQUID_PRIVATE_KEY` and
`HYPERLIQUID_WALLET_ADDRESS`. The private key is read from environment/config
only and is not stored in Postgres.

Do not enable live trading before paper trading proves edge after delay, fees,
slippage, and exit behavior.

The live backend has a shared `TradeIntent` core, generic trading
account/order/fill tables, and a Hyperliquid SDK adapter for IOC reduce-only or
entry orders. The trading worker can reconcile enabled live accounts by reading
Hyperliquid order status, fills, default perp state, all discovered perp dex
states, `userAbstraction`, and spot/core USDC balances. In unified mode, live
account equity, cash, Start trading validation, and live copy sizing use unified
USDC from `spotClearinghouseState`. In `standard_per_dex` mode, live copy sizing
uses tradable perp equity on the same perp dex as the copied market, while spot
USDC remains visible but not used for opening perp entries. It can also execute
live copy orders when global live trading, copy execution, and account-level
trading are all enabled. Live copy uses shared allocation, minimum notional,
min-order adjustment, price drift, and mid-price cache policy from
`backend/config/trading.json`, while live order submission uses live-only
execution and risk guardrails from `backend/config/live_trading.json`. Before
submission, the live adapter normalizes order size and limit price to
Hyperliquid tick and lot precision from market metadata. If lot rounding would
push an adjusted entry below the configured minimum notional, the adapter rounds
to the next valid lot only when min-order adjustment is enabled. Live entries
also add `live_trading_min_order_notional_buffer_usd` before wire rounding so
orders near the exchange minimum do not fall back under the limit after tick and
lot normalization.
Live entries use IOC-limit orders with `live_trading_limit_slippage_bps` applied
to the live mid-price. The default is 20 bps so copied entries are more likely
to cross resting liquidity, while `live_trading_max_slippage_bps` remains the
hard guard against overly aggressive prices. If this value is too tight,
Hyperliquid can reject the order with "Order could not immediately match
against any resting orders."
Live reduce and close copy orders below the configured minimum notional are
skipped locally with `live_close_below_min_order_notional` instead of being sent
to Hyperliquid, because the exchange rejects sub-minimum reduce-only orders.
When current source state shows the source is flat or on the opposite side,
live copy treats that close fill as final and closes the full remaining copied
source position. If that full remaining notional is still below the configured
minimum, it is skipped locally as dust until it can be closed above the minimum.
Enabled and exit-only live accounts reconcile on
`live_trading_reconciliation_interval_seconds`. Live copy also refreshes a stale
account snapshot before sizing a new fill, so deposits are picked up before copy
order sizing if the background loop is late.

Source wallets can also be unified. When their per-dex `clearinghouseState`
reports zero perp equity, paper and live copy check `userAbstraction` and use
unified USDC from `spotClearinghouseState` for source exposure sizing if the
source account is in unified mode.

Manual live test orders are only available through `POST /trading/testnet/orders`
when `hyperliquid_network` is `testnet`. Use them with small notional values and
run `POST /trading/accounts/{account_key}/reconcile` after submission to confirm
the exchange fill and position state were observed.

Current wallet scoring feeds paper copy allocation, but it is still a research
ranking signal. Do not use it for live allocation until paper performance has
been validated with latency, slippage, exits, and account-level risk controls.
