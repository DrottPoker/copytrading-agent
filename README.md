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
- Paper-first config defaults with one explicit `LIVE_TRADING_ENABLED` switch

The dashboard uses a compact operations interface with grouped navigation for
execution, intelligence, and system views. Shared semantic UI tokens and
reusable panel, metric, control, button, status, and table primitives keep every
page visually consistent. Status colors are reserved for actual state, risk,
and outcome information. Account selection and account lifecycle actions are
kept in a dedicated control bar instead of being mixed into global page status.
All large data tables remain horizontally scrollable on narrow screens, and the
application includes loading, not-found, and recoverable error states.

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
- `realtime_execution_inbox`, durable realtime copy work and retry ownership
- `trading_accounts`, generic paper and live account registry
- `trading_positions`, live-ready account/source/coin position state
- `trading_orders`, idempotent order intent and exchange status records
- `trading_order_dispatches`, append-only live exchange-attempt ledger and delivery state
- `trading_close_all_operations`, resumable live close-all workflows
- `trading_close_all_items`, per-position close-all progress and latest order
- `trading_reconciliation_runs`, auditable live reconciliation results and
  component completeness
- `trading_fills`, reconciled paper or live execution fills
- `trading_funding_payments`, idempotent signed Hyperliquid funding ledger for live accounts
- `trading_account_cash_flows`, idempotent external deposit and withdrawal ledger
- `trading_account_performance_snapshots`, cash-flow-adjusted live account return history
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

When running with Docker Compose on an existing deployment, stop the API and
workers before applying schema changes:

```bash
docker compose -f docker-compose.vps.yml stop backend trading-worker maintenance-worker frontend caddy
docker compose -f docker-compose.vps.yml run --rm --no-deps backend python -m alembic upgrade head
```

Phase 6 migration `e5a1c7d9b3f2` adds account lifecycle and integrity controls:

- It preserves financial child rows and creates disabled archived parent rows for
  unambiguous legacy orphans.
- It stops for manual review if account types conflict, orphan account types are
  ambiguous, or duplicate active live routes contain history or active state.

Migration `a7d3e9f1c5b2` removes the former global live-entry gate and restores
accounts that were automatically moved to `exit_only` by its migration default.
`LIVE_TRADING_ENABLED` in `.env` is the only global live execution switch.
Migration `b8e4f0a2d6c1` expands wallet fill ingest latency to `BIGINT` so old
snapshot fills cannot overflow during realtime ingestion.
Migration `c9d5a1e7f3b2` stores the authoritative live margin mode on orders and
positions so cross and isolated execution survive retries, reconciliation, and
dashboard reads.

After upgrade, verify the current migration head before restarting the backend
and both workers. Starting an individual live account still requires complete
fresh reconciliation and the normal account lifecycle checks.

Migration `d1f6a9e4c2b3` adds the live-copy lifecycle foundation. It adds the lifecycle
state tables, source lifecycle key columns on live positions, the global
`watched_wallets.copy_eligibility_started_at` selection epoch, and strict
restart/bootstrap rules for live source attribution. On a deployment, apply the
migration, then verify the revision before starting workers:

Migration `e3b7f9d8c4a1` adds the unified durable live-copy work queue and
explicit execution timing columns. Realtime ingestion and every recovery path
now converge on one unique work item per source fill.

Migration `f9a1c5d2e7b4` changes live order dispatches into an append-only
attempt ledger. Existing rows are backfilled as attempt 1. A logical
`TradingOrder.client_order_id` remains the source-part idempotency key, while
each exchange submission uses its own deterministic 128-bit CLOID.

Migration `a2c4e6f8b0d1` adds the live funding-payment ledger. Live
reconciliation imports signed USDC funding payments from Hyperliquid separately
from trade fills. Live net realized PnL is Hyperliquid `closedPnl`, minus
Hyperliquid fill fees, plus signed Hyperliquid funding.

Migration `b3d5f7a9c1e2` adds the external account cash-flow ledger and
cash-flow-adjusted live performance snapshots. This is the current head.

```bash
python -m alembic current
```

The command must show `b3d5f7a9c1e2`. Do not start the backend or workers until
that revision is current.

For an existing VPS deployment after pulling this phase:

```bash
mkdir -p backups/postgres
docker compose -f docker-compose.vps.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > backups/postgres/pre-live-copy-lifecycle.dump
test -s backups/postgres/pre-live-copy-lifecycle.dump
docker compose -f docker-compose.vps.yml build backend trading-worker maintenance-worker frontend
docker compose -f docker-compose.vps.yml stop backend trading-worker maintenance-worker frontend caddy
docker compose -f docker-compose.vps.yml run --rm --no-deps backend python -m alembic upgrade head
docker compose -f docker-compose.vps.yml run --rm --no-deps backend python -m alembic current
docker compose -f docker-compose.vps.yml up -d backend trading-worker maintenance-worker frontend caddy
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
- Database stats use a fast fill summary by default. Add
  `exact_fill_stats=true` to `GET /database/stats` when an exact
  `wallet_fills` scan is needed for diagnostics.
- The Database page exposes per-index storage and scan counts so large unused
  indexes can be reviewed before any schema change.
- Docker Compose gives the Postgres container a 512 MB shared memory limit so
  heavier aggregate and cleanup queries do not hit Docker's small default
  `/dev/shm` limit.
- The Database page also includes manual fill retention cleanup. It defaults to
  dry-run, keeps 90 days, and protects active, realtime, copy-enabled, open
  paper-position, source-position, live-position, in-flight-order, active
  paper-allocation, and top scored wallets.
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

Worker runtime:

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

Each runtime acquires renewable Postgres capability leases named
`worker_runtime:trading` and `worker_runtime:maintenance`. An `all` worker owns
both capabilities. A duplicate worker waits and retries before starting its
loops instead of opening a second realtime subscription or maintenance
scheduler. Worker containers receive a 40 second stop grace period so the normal
30 second shutdown drain can release leases and durable claims. Every
renewal has a deadline shorter than the lease TTL. Renewal timeout or ownership
loss sets the runtime stop signal before the owner task is canceled, so intake,
heartbeat, and supervised loops stop together.

Long-running loops are supervised by name. An unexpected exit records the
failure, waits for the configured restart delay, and restarts the loop. Worker
heartbeats include the process instance, capabilities, per-loop state, restart
counts, last progress, and realtime execution queue health. The Ops Health page
uses this payload instead of treating a live heartbeat as proof that every loop
is healthy.

The trading worker commits every realtime source fill, its paper execution
payload, and a unique `live_copy_work` item in one Postgres transaction. The
bounded in-process queues are low-latency wakeup buffers only. Paper execution
continues to use `realtime_execution_inbox`. Live execution claims only
`live_copy_work`, in canonical per-source order, with `FOR UPDATE SKIP LOCKED`.
Both consumers retry failures with bounded backoff and reclaim stale claims
after a crash. Queue overflow cannot lose accepted work. Shutdown closes
WebSocket intake first and returns interrupted claims to pending state.
When idle, both consumers poll their durable Postgres work table every five
seconds as a bounded fallback for a restart or lost local wakeup. An in-process
queue item wakes the consumer immediately. Redis is not an execution queue
because event delivery is best effort and must never determine trading work.

Redis Streams powers the recent event feed and SSE resume cursor. Runtime event
publication is presentation-only and best effort. Redis latency or failure is
logged but cannot prevent fill persistence, live execution, paper execution, or
recovery. Raw fill, result, and error events are collected during processing and
published as bounded batches after their durable paper or live execution path.
Startup recovery runs in the background and prioritizes live copy before paper
copy.

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
- `POST /trading/accounts/{account_key}/disable`
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
paper action API. Use `include_market_prices=false` for fast server-rendered
navigation and `refresh_allocations=true` only when the caller explicitly needs
to rewrite copy allocations.

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
  by the paper summary and copy engine. When live trading is enabled, sources
  with open live exposure reserve slots first and the remaining slots go to the
  highest scored eligible copy candidates. Paper-only exposure does not displace
  live candidates and continues through historical fill import and paper-copy
  recovery. In paper-only mode, open paper-position sources retain realtime
  priority. The worker checks the desired subscription list every
  `realtime_subscription_refresh_seconds` and reconnects when the list changes
  or when Hyperliquid does not acknowledge every requested wallet within one
  refresh interval.
- The worker persists confirmed Hyperliquid `userFills` subscriptions separately
  from desired realtime slots. Allocation refresh records monitored time in
  `wallet_monitoring_stats` only for wallets with a confirmed subscription. The
  trading dashboard exposes monitored duration and realized PnL per monitored
  hour for each source wallet. Paper rows use the paper fill ledger. Live rows
  use all-time source-attributed `trading_fills`, divided by the same wallet's
  accumulated monitored seconds. Historical monitored time never determines
  current state.
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
  zero-fill, stale-fill, minimum closed-trades, realized drawdown, and low-score
  cleanup in one reviewed operation.
- Current drawdown is handled by scoring and paper allocation filters, not by
  the normal manual prune flow.
- The Database dashboard surfaces backend proxy and validation errors directly
  when manual prune cannot run, so a missing backend process or invalid request
  is visible instead of a generic failure banner.
- The frontend backend proxy uses `SERVER_API_BASE_URL` as the only upstream in
  production containers. In local development it can fall back to the configured
  local backend URL.
- Pruning and direct wallet deletion use one wallet dependency policy. They
  protect sources with active copy state, open source, paper, or live positions,
  active paper allocations, legacy open copy trades, or non-terminal trading
  orders. `DELETE /wallets/{address}` returns `409` while any protection remains.
- Wallet cleanup removes research and materialized source data, including fills,
  scores, monitoring state, reconstructed source trades, ignored-fill state,
  inactive allocations, and legacy copy links. Discovery records and completed
  paper or live execution history remain available for audit.
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
- Pool maintenance prunes only after scoring reports success. A failed or
  lock-skipped scoring run cannot silently feed stale scores into prune.
- The pool fill importer works through all due wallets in configured batches so older pool wallets are not left unpolled.
- Snapshot messages are stored safely through the same dedupe key as historical imports.
- Non-snapshot realtime fills are committed to Postgres, queued for ordered copy
  execution, and then published to the best-effort Redis event feed.
- Non-snapshot realtime fills for selected scored wallets feed paper copy and
  live copy when live execution is enabled.
- A source wallet that falls out of the top 10 stays monitored while any paper
  account or live source-attributed account still has an open position from that
  source. When those positions are closed, the slot is released to the next
  highest eligible wallet.
- Sources with open paper positions are immune to pruning until every paper
  position for that source is closed.

## Phase 6

Paper copy simulation remains the default execution layer. Live execution now
adds durable entry safety, guarded account lifecycle transitions, auditable risk
trips, and fail-closed defaults.

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
- `backend/config/live_trading.json`

Live activation and account lifecycle:

- `LIVE_TRADING_ENABLED=true` in `.env` is the only global live execution
  switch. Automatic live copy follows the same value.
- Starting an account performs a complete exchange reconciliation, requires a
  fresh snapshot, validates the configured wallet identity, and only then
  transitions the account to `enabled`.
- Stop transitions the account to `exit_only` and cancels unsent entries.
  Reduce-only position management remains available when configured through
  `risk.reduce_only_when_stopped`.
- Disable requires complete flat reconciliation and no non-terminal orders or
  unfinished close-all operation. Delete archives a disabled, freshly
  reconciled, flat account while retaining orders, fills, positions, close-all
  records, reconciliation runs, risk events, and audit history.
- Lifecycle changes record `lifecycle_version`, `status_changed_at`, and
  `status_reason`. Active live wallet routes are unique by network, wallet, and
  optional vault.
- Every new live entry must pass a fresh reconciliation check, intent TTL,
  50 percent weekly account-loss limit, 50 orders-per-minute limit, and price
  safety checks. A breached stateful
  account limit or stale reconciliation trips the account to
  `exit_only`, cancels unsent entries, and persists the event for audit. Expired
  intents and invalid exchange leverage or slippage values are rejected
  before submission without opening exposure.
- The renewable `live_execution:{account_key}` fence is shared by order
  submission, reconciliation, and margin-setting synchronization. A transient
  busy result does not imply that another visible fill exists. A fully validated
  entry that cannot submit because the fence is busy is persisted as
  `skip:live_execution_busy` and retries with its original logical order ID,
  size, notional, leverage, margin mode, limit price, and `created_at`.
  This retry uses the persisted leverage without requiring a fresh source
  leverage read. It rechecks current price drift and the normal account,
  lifecycle, reconciliation, risk, and capacity gates. The original 30-second TTL is
  never renewed, and expiry becomes `live_entry_intent_expired`. The operator
  message distinguishes expiry before any submission from expiry after an
  earlier exchange attempt or rejection. Generic leverage-missing skips are not
  reusable persisted intents.
- Weekly loss usage is net realized PnL after fees and signed Hyperliquid
  funding plus current aggregate
  exchange unrealized PnL. Its percentage base is reconstructed start-of-week
  account equity. Current unrealized PnL is included once and is not duplicated
  across positions or fill rows.
- Non-reduce-only orders copy both source leverage and source margin mode from
  current Hyperliquid `clearinghouseState`. Hyperliquid still requires positive
  whole-number leverage and enforces the market's supported leverage. The
  adapter applies `updateLeverage` with `isCross=true` for cross and
  `isCross=false` for isolated before order submission, and requires an `ok`
  response. Live entry is skipped when either source setting is unavailable.
  Reduce-only orders never change margin settings.
- Live copy recovery rechecks every open source-attributed position and updates
  the target leverage and margin mode when the source changes either setting
  without producing a fill. The default configured recovery interval is 15
  seconds.
- A live account and coin can belong to only one source while copied exposure
  is open. Another source on the same coin is skipped until the market is free,
  because Hyperliquid exposes one physical position and margin setting per
  account and coin. Different coins can independently use cross or isolated.

Live copy lifecycle and recovery:

- `watched_wallets.copy_eligibility_started_at` is the global source-selection
  epoch. `live_copy_source_states` stores one durable lifecycle state per live
  account and source wallet, with its own activation time and baseline. Normal
  entries require both an immutable first observation at or after
  `activated_at` and an authoritative source timestamp at or after the
  activation time. Baseline fill IDs only scope recovery candidates; they never
  grant entry permission. Same-timestamp arrivals can be candidates, but are
  not automatically eligible. A flat source lane becomes inactive when that
  account is no longer entry eligible. Start captures a fresh baseline before
  enabling entries, including lanes retained for exits, so fills accumulated
  while entries were off cannot replay.
- `live_copy_source_states.entry_eligible` is the authoritative per-account
  entry-routing flag. It is true only for a currently selected source lane.
  A retained lane with owned exposure or unresolved work remains active with
  `entry_eligible=false`, so it can manage the owned lifecycle without opening
  a new market. Reselection sets the flag to true only after a fresh baseline.
- Pending and retryable `reduce`, `close`, and `flip_close` lifecycle rows retain
  the source lane, realtime slot, watched-wallet record, account route, and
  recovery priority even when a source-attributed position or logical order row
  is temporarily missing.
- `live_copy_fill_states` stores one durable state per account, source fill, and
  planned fill part. It records the processing origin (`realtime`,
  `snapshot_recovery`, `startup_recovery`, or `periodic_recovery`), outcome,
  retry attempt count, next due time, expected part count, plan version, and the
  optional linked `TradingOrder`. Every part is committed before sequence zero
  may dispatch, so a worker crash cannot lose the second half of a flip. The
  final entry gate takes the account lifecycle lock before source-state and
  fill-state locks, then commits before exchange submission.
- Raw `WalletFill` rows remain the complete source audit record. A
  `TradingOrder` is created only for a real live order or a terminal order-level
  decision. Baseline and unowned preexisting source lifecycle parts are recorded
  as `baseline_ignored` in the live-copy state ledger and never manufactured as
  failed execution rows.
- An unowned preexisting add, reduce, close, or flip-close is ignored as part of
  the source lifecycle baseline. A post-baseline flip-open is a fresh lifecycle
  and can be copied only when the normal live guards pass. Crossing a fresh
  retained baseline for an entry is limited to a narrowly proven same-side
  continuation of an already owned position. The compact source marker is
  updated as the unowned lifecycle changes, without replaying its historical
  orders.
- Temporary reconciliation, source-state, source-equity, leverage, margin-mode,
  or execution-price failures use bounded backoff in `live_copy_fill_states`.
  When the event came through the realtime inbox, that inbox item remains
  pending for retry. No fake `TradingOrder` is emitted for a prerequisite that
  was not yet decided. Pipeline decisions without a `tradingOrderId` remain
  operationally visible in lifecycle state, but are not labeled as fills or
  orders.
- Each account, source, and coin is an ordered execution lane using canonical
  numeric source-fill ordering, with close and flip-close parts before opens.
  An unfinished earlier fill blocks later fills in that lane until it completes
  or becomes a terminal decision. Other coins continue independently.
- If reconciliation retained matching aggregate exchange exposure but lost the
  source-attributed position row, a continuation fill can restore attribution
  only when the current executed fill lifecycle reconstructs the same side and
  aggregate size, no competing source owns the market, and no unexplained
  manual exposure exists. Only the mathematically proven source size is
  restored. Ambiguous ownership retries without placing an order.
- Reconciliation repairs a previously exchange-attributed fill when its stored
  exchange order id or dispatch CLOID now matches exactly one durable logical
  order. The repaired ledger restores the missing source position before normal
  reduce-only exit recovery closes it. Unmatched manual fills and unproven
  exchange exposure remain untouched.
- A source flip closes the copied old side first. The new-side part waits for
  reconciliation to remove the old side before it can submit an entry.
- Entry fills that exceed the configured entry TTL receive one terminal stale
  decision. This is distinct from a transient retry and remains auditable with
  the original source timestamp and decision context. The decision API also
  exposes its processing origin, source timestamp, observation timestamps, and
  update time. The dashboard renders ingest lag, source-to-decision age, and
  processing lag so operators can distinguish delayed ingest from slow processing. A stale
  decision is a no-order terminal state and does not create a `TradingOrder`.
- Recovery removes completed fill dispositions before applying its per-source
  limit. It retains work for nonzero owned positions and unresolved orders,
  including filled orders whose exchange fills are not fully materialized. This
  overlap keeps older exits recoverable without allowing a fixed historical
  prefix to starve new fills. Recovery only revisits due pending or retryable
  state and unfinished terminal parts. Later non-stale fills behind an
  unfinished same-market predecessor are excluded before the recovery limit,
  while stale entries may bypass that query barrier only to become terminal
  decisions without submitting an order.
- Legacy attribution and lifecycle keys bootstrap only from strict current
  executed-fill proof. Exchange and manual-test reserved sources are excluded;
  historical existence alone cannot create attribution or an active lane.
- Existing `TradingOrder` history is never deleted or hidden by lifecycle
  recovery. New baseline and transient prerequisite states stay in the separate
  lifecycle ledger because they are not exchange execution attempts. Recent
  Execution Activity therefore remains sourced from actual order decisions,
  while separate pipeline decisions stay visible without being called fills or
  orders when no `tradingOrderId` exists.
- `live_copy_source_states` and its fill-state children are audit state protected
  by `RESTRICT` foreign keys. They are not owned by wallet cleanup and must not
  be deleted as wallet research data.

Paper accounts:

- Paper accounts are stored in Postgres and created from the dashboard or API.
- `backend/config/trading.json` owns shared copy allocation, min-order, price
  drift, and mid-price cache policy.
- `backend/config/paper_trading.json` owns paper simulation settings only, not
  account creation.

Sizing policy:

- The top 10 scored wallets are eligible for paper copy allocation.
- Open paper-position sources have realtime priority in paper-only mode. When
  live trading is enabled, open live exposure and the current top 10 own the
  realtime slots while paper-only exposure is maintained by fill import and
  recovery.
- Retained sources outside the current top 10 can add to existing matching paper
  positions and can reduce or close them, but cannot open completely new paper
  positions.
- All top 10 ranks receive a 30% account pocket.
- Total open copied margin is capped at 100% of each paper account equity.
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
- New entry fills first observed more than
  `trading_copy_max_entry_age_seconds` after the source timestamp are skipped
  before opening or adding exposure. The default is 20 seconds. Admission is
  fixed from the durable
  `WalletFill.received_at` timestamp, so the worker's own queue, preflight, or
  retry time cannot later reclassify a promptly received fill as stale. This
  still prevents late snapshot or recovery fills from creating exposure. Close
  and reduce processing runs regardless of entry admission so exits can catch
  up safely. Live copy stores a stale-entry decision as a terminal per-fill
  disposition, linked to a `TradingOrder` only when it is an actual order-level
  decision.
- Paper execution starts the configured simulated latency immediately while
  source account state is fetched in parallel.
  It then reads live mids and applies adverse slippage to the execution price.
- The trading worker keeps a WebSocket `allMids` cache for copy execution when
  `trading_copy_market_price_cache_enabled` is enabled. Fresh cached prices are
  used before HTTP, so realtime fills do not wait on a new `allMids` request per
  batch.
- Realtime execution uses every valid non-snapshot WebSocket fill, even when a
  concurrent poll inserted the same wallet fill first. Database insertion
  counts remain deduplicated, while the source-fill order key keeps live and
  paper execution idempotent.
- Live execution has one durable `live_copy_work` row per source fill. Realtime
  ingestion creates the work row in the same transaction as the source fill.
  Startup, snapshot, and periodic recovery enqueue that same unique work item
  instead of executing through a competing recovery path. A worker claim is
  committed before Hyperliquid reads begin, and retry state remains durable
  across worker restarts.
- The ordering pre-pass only claims entry parts that are already stale and must
  become terminal before an earlier unresolved fill. Fresh entries are claimed
  exactly once by the normal execution pass, so they cannot lease themselves as
  `processing` and then wait for their own retry timeout.
- The 30-second live entry-intent TTL starts when the local execution intent is
  constructed, not at the source fill timestamp. An existing retryable order
  keeps its original construction time, so retries cannot renew that TTL.
- Before an order exists, entry preparation has the same bounded window from
  the part's first durable processing claim. If attribution or another
  prerequisite is still unresolved when that window expires, the part becomes
  the terminal no-order decision `live_entry_preparation_expired`. Exit parts
  remain retryable because they may still be needed to reduce owned exposure.
- A market already reserved by another source terminalizes a new entry as
  `live_market_reserved_by_other_source` before attribution recovery. Strict
  attribution proof is never weakened to force an order through.
- An exit from a source that does not own the market is recorded once as
  `live_exit_market_owned_by_other_source` without creating an order or retrying.
  Genuinely incomplete or conflicting lifecycle proof remains retryable as
  `live_source_attribution_ambiguous`.
- The time-sensitive copy path uses the most recent authoritative account
  reconciliation snapshot and does not run a full reconciliation before a new
  entry. Dedicated reconciliation remains responsible for refreshing exchange
  truth, and the existing snapshot-age gate still blocks entries when that
  truth is too old or incomplete.
- Live-copy decisions record the original observation, execution claim,
  processing start, and final decision as explicit wall-clock timestamps. The
  dashboard reports ingest, queue, preparation, work, and total decision
  latency from those timestamps rather than from a transaction-scoped database
  timestamp.
- If the cache is stale or missing a coin, paper copy falls back to HTTP
  `allMids`, then dex-specific `allMids`, then `metaAndAssetCtxs`.
- Paper fills and live entries are skipped when adverse live mid price drift
  from the source fill price is above `trading_copy_max_price_drift_bps`, which
  defaults to 100 bps. Favorable drift in the execution direction counts as
  0 bps. Live reduce-only exits are never blocked by the entry drift guard.
- Recent paper fill rows show source price, live mid, adverse drift bps, the
  per-fill drift limit, and min-order adjustment markers when execution details
  are available.
- Open paper position rows show entry execution delay in milliseconds, measured
  from the source fill timestamp to when the paper position was created. Open
  live position rows show the same source-to-open delay when a matching live
  fill and source wallet fill are available. Realtime live-copy execution runs
  before paper-copy simulation so live orders do not wait for paper latency or
  paper-only bookkeeping.
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
- Live Copy Decisions remain a separate diagnostic list. Correlated orders show
  logical status plus the latest exchange attempt CLOID, reject message, submit
  attempts, and status lookups. Stale no-order rows
  show their realtime or recovery origin, source time, first observed time,
  ingest lag, total source-to-decision age, and processing lag when available. They never
  appear in Recent Execution Activity because no `TradingOrder` was created.
- Closed live trade history includes copied source trades and manual exchange
  position closes. If only an exchange close fill is available locally, the
  backend shows a close-only row and estimates the entry price from Hyperliquid
  realized PnL.
- Copy source selection is shared across paper and live execution, but live mode
  reserves realtime capacity only for open live exposure and current eligible
  candidates. Paper-only retained sources are excluded from Live Copy Sources
  and remain managed by historical fill import and paper recovery. Each mode
  renders its own exposure, PnL, activity, and execution status. Live-only
  historical sources from the complete source-attributed live fill ledger remain
  visible through Wallet PnL history. Recent orders remain visible there and in
  Recent Execution Activity.
- The Accounts page stores the last selected account in the browser and
  defaults to that account on the next visit, otherwise the first synced account
  is selected. Its compact trading workspace shows paper and live account KPIs,
  cumulative or per-trade performance, profit factor, drawdown, expectancy,
  payoff, win and loss streaks, allocation, leverage, long and short exposure,
  market concentration, source performance, positions, closed trades, activity,
  balances, reconciliation, and routing details. The interactive performance
  chart supports 25, 50, 100, or all loaded trades and pointer or keyboard
  inspection. Trading open-position rows show local opening day and time in the
  existing Execution detail line without adding row height. Live-account
  diagnostics include a compact transaction ledger
  with every automatically reconciled external deposit, withdrawal, and
  account-to-account transfer, including signed amounts, fees, timestamps, and
  totals. The panel has no separate reconciliation control.
  The live Reconciled card includes a manual refresh icon that posts to
  `POST /trading/accounts/{account_key}/reconcile` and refreshes the selected
  account snapshot. Operators can pass `lookback_minutes` to backfill older
  live fills when a previous reconciliation window missed exchange history.
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
- The Accounts page shows whether `LIVE_TRADING_ENABLED` is active and displays
  the effective risk limits.
- The Accounts page can start, stop, verify flat and disable, or close all live
  positions. Starting requires `LIVE_TRADING_ENABLED=true` and performs a fresh
  complete reconciliation. Stopping changes the account to `exit_only`.
  Disable succeeds only after a complete flat reconciliation. Close all uses the
  resumable durable dispatcher and disables only after the exchange is confirmed
  flat.
- Paper delete removes local account positions, fills, allocations, and history.
  Live delete is an archive operation. It requires a disabled account, fresh
  complete reconciliation, no open positions, and no pending work, then preserves
  all financial and audit history.
- The dashboard shows paper accounts, monitored sources, currently trading
  sources, open positions, wallet PnL history, closed trade history, and recent
  fills as compact lists without horizontal scrolling.
- Source, position, wallet-history, closed-trade, and fill rows show the wallet
  label when available and fall back to the short address.
- Source rows split source PnL into realized and unrealized values, with total
  PnL shown as supporting context.
- Wallet PnL history rows use `monitored` only while the source has a confirmed
  realtime subscription and `history` otherwise.
- Wallet PnL history, closed trade history, and recent execution activity show
  10 rows per page with pagination controls.
- Closed trade history rows show compact close time and duration when the
  original paper position open time is available.
- Account rows include a reset action that restores that account's starting
  balance, cash balance, equity, realized PnL, and fee counters while leaving
  open positions, copied fills, and closed trade history intact.
- Source rows show a primary monitor status and a source substatus. Primary
  status is `monitored` only after Hyperliquid acknowledges that wallet's
  `userFills` subscription. An assigned but unconfirmed source is `connecting`,
  an assigned source without a current worker connection is `offline`, and a
  source without an assigned slot is `waiting`. `hasRealtimeSlot` represents
  allocator intent while `isRealtimeMonitored` represents confirmed runtime
  truth. Substatus is `trading`, `retained`, `entries paused`, `waiting for
  trades`, or `waiting for slot`. With live trading enabled, sources outside the
  current top 10 retain slot intent only while they have open live exposure.
  Paper-only retained sources do not consume a live realtime slot or appear in
  Live Copy Sources.
- Live Copy Sources always includes the worker's current desired and confirmed
  realtime wallets, independently of live-account entry readiness. Allocation
  and open-position sources supplement that authoritative runtime set.
- A source with open exposure and a current realtime slot is `trading`, even
  when new entries are paused. `Retained` is reserved for open exposure that no
  longer owns a current slot. A slotted source without exposure is `entries
  paused` when no account accepts new entries and `waiting for trades` when it
  is entry-ready.
- The Sources summary and Copy Sources header count `trading`, `monitored`,
  `connecting`, `offline`, and `waiting for slot` from the same current source
  rows that render the badges. Exchange aggregate display positions and
  historical monitoring duration are not used for those counters.
- Source rows display `pool #` from the wallet score pool rank, not the realtime
  monitor slot or retained-source order. Retained rows are labeled `reduce only`
  and also show the blocking
  reason, such as outside copy top 10, drawdown blocked, paper account disabled,
  cooldown, or missing score.
- Copy Sources is sorted by allocation-used percentage descending. Used USD,
  realized PnL, source status, pool rank, and wallet address provide deterministic
  tie-breakers. Wallet PnL History is sorted by realized PnL descending, followed
  by total PnL, pool rank, and wallet address.
- Open Positions is sorted by durable opening time ascending with deterministic
  position identity tie-breakers, so price updates cannot move existing rows.
- Trading uses compact responsive cards and list rows. Open Positions shows the
  owning source as a clickable name and keeps the full wallet address in the
  link tooltip instead of printing it in every row.
- Closed trade history comes from paper `close` and `flip_close` executions.
  Raw fills and skip rows remain available in the API for diagnostics, but they
  are not shown as trade history. Closed trade rows show a liquidation tag when
  the source close fill was marked as a liquidation by Hyperliquid.
- Open position rows include a manual close action. Manual closes use the same
  live mark, adverse slippage, and fee model as automated paper closes, then
  record a normal `close` row in `paper_copy_fills`.
- Successful manual live closes immediately reconcile the live account so the
  exchange fill and closed trade row can appear without waiting for the next
  worker loop.
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
- Live order dispatch, account reconciliation, and margin-setting
  synchronization share the renewable `live_execution:{account_key}` Postgres
  fence. Reconciliation and margin sync revalidate after acquiring it. Periodic
  margin sync first compares the local exchange and source-attributed rows; when
  both already match, it updates local metadata without taking the fence or
  calling the exchange. Actual exchange changes still acquire and revalidate
  under the fence. The order and outbox row are committed before an exchange
  request, and no account or order row lock is held while Hyperliquid is called.
- New entry finalization reruns intent freshness and risk checks under the
  per-account execution lock immediately before dispatch.
- A lost exchange response is stored as `uncertain`, not `failed`. The trading
  worker queries Hyperliquid by that attempt's deterministic CLOID before any retry
  decision, so a possibly accepted order is not submitted blindly a second time.
- Live close-all first moves the account to `exit_only`, persists an operation
  and one item per exchange position, then submits each reduce-only close through
  the same durable dispatcher. Worker reconciliation resumes unfinished
  operations after restart and disables the account only after the exchange is
  confirmed flat.
- Live reconciliation treats Hyperliquid as authoritative only for components
  that returned a complete snapshot. Default perps and every HIP-3 dex are
  separate scopes. A failed scope preserves its exchange and source positions,
  while complete empty scopes remove stale local positions.
- Fill pagination, order status, perp catalog, each perp dex, spot state, and
  account abstraction report independent completeness. Partial attempts retain
  the last known capital for failed components, do not advance the last complete
  reconciliation timestamp, and block new live entries until a complete attempt
  succeeds. Reduce-only exits remain available.
- Every live reconciliation attempt is stored in
  `trading_reconciliation_runs`. The Accounts dashboard distinguishes complete,
  partial, failed, and never-reconciled accounts and marks stale capital scopes.
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
- Trading-worker startup recovery runs in the background and prioritizes live
  copy before paper copy, so realtime subscriptions can start without waiting
  for historical recovery to finish.

Notes:

- Live trading can place Hyperliquid orders only when
  `LIVE_TRADING_ENABLED=true`, credentials are configured, and the live account
  is enabled. Paper execution remains the default simulation layer.
- Live copy reserves one source per live account and market while exposure or a
  nonterminal entry order exists. Another source opening the same market is
  skipped until the market is free, even if it is the same side, because
  Hyperliquid nets the exchange position and leverage at account level.
  Matching exits and adds from the already reserved source are still allowed.
- Full old history is not imported into fresh paper accounts. Recovery only
  replays fills for current allocation sources or sources with open paper
  positions.
- Existing paper account balances are not reset when `starting_balance_usd` is
  edited. Use the dashboard account reset action to apply configured starting
  capital to an existing paper account.

## Local Development

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
request settings.

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
copy: source ranking limits, allocation pockets, total allocation cap, copy
minimum notional, optional min-order adjustment, max entry age, price drift
guard, and the live mid-price cache settings.

`backend/config/paper_trading.json` owns paper-only simulation settings:
paper copy enablement, simulated fee rate, simulated slippage, simulated latency,
and paper recovery cadence.

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
API. A short custom password is accepted, although a longer unique password is
recommended. Production requires a non-empty username and rejects the literal
default `change-me`. Backend auth is enabled by default and protects every route
except `/health` and `/ready`. The dashboard also enforces Basic Auth before serving pages or
`/api/backend` proxy routes. Backend credentials are attached by the Next.js
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

Run the regular backend and frontend suites without external test services:

```powershell
.venv\Scripts\python.exe -m pytest backend\tests -q
cd frontend
npm run test:run
```

Run the real Postgres, Redis, Alembic, and FastAPI integration suite on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File infra\run-integration-tests.ps1
```

The integration runner starts the disposable services from
`docker-compose.test.yml`, migrates an empty Postgres database to Alembic head,
runs tests marked `integration`, and removes the test volumes afterward. Use
`-KeepServices` only when the disposable database and Redis instance are needed
for debugging. The same unit, integration, frontend, lint, compile, and build
checks run in `.github/workflows/ci.yml`.

## VPS Deployment

Use `docker-compose.vps.yml` for a Linux VPS. It exposes only Caddy on ports 80
and 443, keeps backend, frontend, Postgres, and Redis on the internal Docker
network, and persists Postgres and Redis data in Docker volumes.

`DASHBOARD_DOMAIN` supports two deployment modes. Set it to `:80` to access the
dashboard temporarily through `http://VPS_IP`, or set it to a DNS hostname such
as `copy.example.com` to let Caddy provision HTTPS automatically. IP-only HTTP
is supported but sends Basic Auth without TLS, so prefer a DNS hostname for any
long-running or internet-exposed deployment.

Required first-time flow:

```bash
cp .env.example .env
# Edit POSTGRES_PASSWORD, DASHBOARD_AUTH_PASSWORD, and DASHBOARD_DOMAIN.
docker compose -f docker-compose.vps.yml build
docker compose -f docker-compose.vps.yml up -d postgres redis
docker compose -f docker-compose.vps.yml run --rm backend python -m alembic upgrade head
docker compose -f docker-compose.vps.yml run --rm backend python -m alembic current
docker compose -f docker-compose.vps.yml up -d
```

The `current` command must report `b3d5f7a9c1e2` before the backend or workers
start.

The application images run as non-root users. Backend, frontend, and worker
containers use read-only root filesystems, writable `tmpfs` mounts only where
needed, `no-new-privileges`, and all Linux capabilities dropped. Redis has an
explicit healthcheck, but it is not a startup dependency for the backend or
workers. Redis only serves presentation events. A Redis outage leaves
`/health` and `/ready` degraded while they return HTTP 200 if Postgres is
healthy, so trading and maintenance can continue. Caddy adds HSTS on the VPS
and sets content-type, frame, referrer, and permissions security headers. Keep
state and backups in the declared volumes rather than writing into an
application container.

Local VPS Postgres data lives in the `postgres_data` Docker volume. Do not run
`docker compose -f docker-compose.vps.yml down -v` unless you intentionally want
to delete the database. The `postgres-backup` service runs `pg_dump` every 24
hours by default and writes dumps to `backups/postgres`. `/ops` checks that
folder when `backup_status_enabled=true` in `backend/config/app.json`. Set
`POSTGRES_DB`, `POSTGRES_USER`,
and `POSTGRES_PASSWORD` before the first Postgres start because changing them
later does not alter an existing database volume.

Full guide: [docs/deployment.md](docs/deployment.md)

## Live Trading Activation

The repository uses mainnet market data for monitoring and paper trading while
live execution defaults to disabled:

```dotenv
LIVE_TRADING_ENABLED=false
```

Set it to `true` and restart the backend and trading worker to activate live
execution and automatic live copy. Startup then requires
`HYPERLIQUID_PRIVATE_KEY` and `HYPERLIQUID_WALLET_ADDRESS`. There are no
acknowledgement flags, time-based arming values, global database gate, coin
allowlist, or coin blocklist. The market-policy function currently allows every
coin. The private key is not stored in Postgres.

Default live risk limits are 50 percent weekly loss against reconstructed
start-of-week account equity and 50 orders per minute, plus a 90-second maximum
reconciliation snapshot age and a 30-second entry intent TTL. Order notional,
account open notional, position count, daily loss, and source leverage have no
local maximum. Shared copy allocation still caps total open copied margin at 80
percent by default in `backend/config/trading.json`.

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
submission, the live adapter routes prefixed HIP-3 markets such as `xyz:SNDK`
through the matching SDK `perp_dexs` metadata and resolves whichever SDK order
coin name is present for that market, either `SNDK` or `xyz:SNDK`. It then
normalizes order size and limit price to Hyperliquid perp tick and lot precision
from that market metadata. Markets still missing from the live SDK metadata are
rejected before submission, so SDK lookup misses are stored as actionable live
order errors instead of raw Python key errors. If lot rounding
would push an adjusted entry below the configured minimum notional, the adapter
rounds to the next valid lot only when min-order adjustment is enabled. Live
entries also add `live_trading_min_order_notional_buffer_usd` before wire
rounding so orders near the exchange minimum do not fall back under the limit
after tick and lot normalization.
Live entries use IOC-limit orders with `live_trading_limit_slippage_bps` applied
to the live mid-price. The default is 20 bps so copied entries are more likely
to cross resting liquidity, while `live_trading_max_slippage_bps` remains the
hard guard against overly aggressive prices. If this value is too tight,
Hyperliquid can reject the order with "Order could not immediately match
against any resting orders."
Partial live reduce and close copy orders below the configured minimum notional
are skipped locally with `live_close_below_min_order_notional`. When current
source state remains open, later partial closes aggregate earlier below-min
skips for the same live account, source, coin, and side until the combined
reduce can be submitted. When current source state shows the source is flat or
on the opposite side, live copy treats that close fill as final and closes the
full remaining copied source position. If that final close is below the
configured minimum, the backend submits a reduce-only dust close with wire size
adjusted up to the minimum notional.
Reduce-only dust closes are adjusted independently from the small-entry
adjustment toggle because exits must be able to clear residual exposure.
Recovery can retry older local `live_close_below_min_order_notional` skip rows
so existing dust positions are not left open after this final-close check.
If a copied source-position row is missing, recovery reconstructs the current
executed market lifecycle before restoring attribution. Historical existence
alone is insufficient. Unexplained manual or competing exposure defers the
source fill without submitting a reduce-only order.
Enabled and exit-only live accounts reconcile on
`live_trading_reconciliation_interval_seconds`. Live copy also refreshes a stale
account snapshot before sizing a new fill, so deposits are picked up before copy
order sizing if the background loop is late.
Complete reconciliation also imports Hyperliquid non-funding ledger updates.
External deposits and withdrawals are stored separately from trading PnL, while
transfers between spot and perp capital inside the same account remain internal.
USDC sent in from another Hyperliquid address is treated as an external deposit,
and USDC sent out to another address is treated as an external withdrawal.
Each live account performs one versioned all-time cash-flow backfill before
switching to incremental reconciliation. This imports deposits and withdrawals
that happened before the account was added to the application.
The first complete reconciliation after the performance migration requests
Hyperliquid `allTime` account-value history and the matching historical external
cash flows. It replaces the temporary zero baseline with chain-linked,
cash-flow-adjusted historical snapshots when both histories are complete. If
the first external deposit predates the first positive account-value point, the
backfill anchors at zero immediately before that deposit and ignores leading
zero-value samples. This preserves the complete initial capital instead of
mistaking the first partial balance for trading profit. If
backfill data is unavailable, the previous stored complete exchange snapshot is
used as a safe baseline and backfill is retried later. Later equity snapshots
continue the same performance index, so adding capital changes sizing without
diluting earlier account performance. The Accounts page labels the tracking
start and keeps cash-flow-adjusted PnL and net external flows in account details.
Its headline Total PnL and Realized metrics use the same net definitions as the
Trading page. Open margin is shown before notional with its share of equity,
while notional carries average leverage.
`GET /trading/accounts/{account_key}/cash-flows` returns the selected live
account's complete external cash-flow ledger without attaching the full history
to the frequently refreshed account summary response. The account's normal
automatic reconciliation imports deposits, withdrawals, and external transfers;
the panel reloads when the account reconciliation timestamp advances.
Manual reconciliation accepts `lookback_minutes` for historical live fill
backfills, for example `POST /trading/accounts/{account_key}/reconcile?lookback_minutes=4320`.

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
