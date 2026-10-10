# Changelog

Notable changes, newest first. Dates are UTC. The historical development phases
at the end describe how the system was built and keep the detailed notes that
used to live in the README.

## 2026-10-11

### Dashboard redesign

- The dashboard has a new design and structure. Seven pages replace nine:
  Overview, Trading, Activity, Wallets, Discovery, Analytics, and System. The
  old routes redirect: `/accounts` and `/paper-trading` to `/trading`,
  `/live-feed` to `/activity`, and `/ops` and `/database` to `/system`.
- A status bar on every page shows the trading mode, network, API, Postgres,
  and Redis health, realtime wallet slots, and running jobs with progress.
- The Overview explains the current state in a sentence and shows accounts, the
  wallet pipeline from discovered candidates to wallets followed live, a list of
  problems that need attention, background jobs, top ranked wallets, and recent
  events.
- Trading merges the former Accounts and Trading pages into one view per
  account, with an account switcher, headline figures, performance and risk
  panels, open positions, copy sources, history tabs, and account details.
  Destructive actions ask for confirmation in a dialog that explains the
  consequence, and starting a live account now asks for confirmation too.
- Wallets can be filtered, searched, and sorted on every score metric, show
  their score components as small bars, and archived wallets have their own tab
  with a Readmit action. The wallet page explains each score component and links
  to the Hyperliquid explorer.
- Discovery shows exact funnel counts per source from the analytics endpoint,
  why candidates are rejected, and pages through all candidates instead of the
  first 500.
- Activity filters events by category, searches payloads, expands raw payloads,
  and can pause the stream.
- System combines health, database statistics, and the cleanup tools in tabs.
- Light and dark themes follow the operating system and can be pinned from the
  sidebar. Type is IBM Plex Sans and Mono, self-hosted from npm. Numbers use
  en-US formatting with a real minus sign, and times show as relative times
  with the full timestamp on hover.
- Business logic moved out of components into tested modules under
  `frontend/src/lib`.

### Larger text

- Text is larger across the dashboard: tables and body text 14px instead of
  13px, and secondary lines, table headers, and status text 13px instead of
  11px. Muted text has more contrast in both themes.
- A text size setting in the sidebar makes the whole interface about 9% or 19%
  larger. It is stored in the browser.
- Figure labels and sub lines wrap to two lines instead of being cut off.

### Fixes

- A canceled background job showed "Stopping" forever because the cancel flag
  stayed in its payload. It now shows Canceled.
- The paper total PnL on the Trading page was gross of fees and funding. Paper
  and live figures now use the same net semantics everywhere.
- The wallet page rendered up to 5,000 rebuilt trades at once; it now pages
  through them.
- The live event stream closed every five seconds, so the feed kept falling back
  to polling. redis-py 8 times out socket reads after five seconds by default,
  and the stream blocked for ten. It now blocks for four seconds and sends a
  keepalive.

## 2026-10-10

### Ten copied sources

- The top 10 ranked wallets are copied, each with 20% of the account, a 200%
  allocation budget. The exposure caps (3x a source's allocation and 3x the
  account) still bound the total size, and the budget may not exceed the total
  exposure cap.

### Faster discovery backfill

- Discovery reads each candidate's equity curve (one light request) before its
  fills, and rejects candidates with an equity drawdown above 40% or, with the
  new `quality.require_positive_equity_return` setting, without a positive
  return, before any fill is fetched. Fills cost Hyperliquid rate-limit weight
  per row returned, and in a measured run more than half of the fetched rows
  belonged to candidates that were rejected afterwards. The curve is reused for
  scoring, so no candidate costs more than before.
- Fill imports no longer lose fills that share the last millisecond of a full
  page; the next page starts at that millisecond and skips fills already read.
- A discovery run now finishes candidates an interrupted earlier run left
  behind. Before, a deploy, restart, or rate-limit pause during backfill left
  the remaining candidates unprocessed for good, because later imports skip
  existing candidates and only backfilled their own.

### Review fixes

- An exit that the order book could not fill in paper is now retried by paper
  recovery, as live retries an IOC exit without a match. Before, the paper
  position stayed larger than the source's until the source closed fully.
- A paper account's total PnL and return on the Accounts page are net of fees,
  like the Trading page and live accounts. The Accounts page also shows when a
  paper account is exit-only.
- The paper drawdown limit measures from the account's peak equity since the
  last reset, stored on the account, instead of the highest snapshot in the
  last 35 days. Migration `e8a2c4f6d1b3` adds the column and fills it from the
  existing snapshots.
- Manual paper closes lock the account before the position, the order the copy
  engine and the paper risk loop use, so they cannot deadlock. The copy engine
  no longer reads prices from Hyperliquid while it holds account locks.
- Postgres, Redis, and Caddy are pinned to their latest patch releases.

### Data and infrastructure

- Production live trading signs only with a Hyperliquid API wallet, which can
  trade but cannot withdraw. The signing key is checked with Hyperliquid
  `userRole` during reconciliation and on every start; a main wallet key makes
  the account exit-only. The Ops page shows the Live Signing Key per account.
- A one-off `migrate` Compose service applies migrations before the backend
  and workers start, and `infra/deploy-vps.sh` runs the whole VPS update:
  backup, pull, build, migrate, head check, and health check.
- Docker logs rotate at 10 MB with five files per service. Postgres, Redis,
  and Caddy images are pinned and updated by Dependabot. Local Compose ports
  are published on `127.0.0.1` only.
- The backend runs on Python 3.14 everywhere: Docker, CI, and local
  development through `.python-version`.
- Documented how to copy backups off the VPS, what to do after a suspected
  compromise, and Hyperliquid's S3 fill history.

### Paper simulation shares the live decision rules

- Paper and live copy now call one decision module, `copy_decision`, for entry
  rules, target-position sizing, exit sizing, final-close detection, and the
  combination of small partial closes. The shared reading of source fills,
  source account state, and market prices moved out of the paper service into
  `copy_signal_service`, and the account and source loss rules into
  `copy_risk_rules`.
- Fixes found by sharing the rules: paper exits are no longer blocked by price
  drift, a final source close now closes the whole paper position at once, and
  paper combines partial closes below the minimum order like live does.
- Paper accounts apply the live loss limits: 10% over a rolling 24 hours, 20%
  over 7 days, and 25% drawdown from the peak, measured on marked-to-market
  equity snapshots, plus the 5% per-source loss limit. A breach makes the paper
  account exit-only until it is started again.
- Paper market simulation: entries and exits fill against the Hyperliquid order
  book like a live IOC order within the live limit slippage, so they can fill
  partially or not at all. Open positions pay or receive hourly funding from
  Hyperliquid funding history, and positions are liquidated with isolated or
  cross margin rules using maintenance margin from exchange metadata. Paper
  positions copy the source's margin mode.
- Migration `d7f1b3c5e9a2` adds paper margin mode, funding, exit-only state,
  equity snapshots, funding payments, and the `liquidation` paper fill action.

### Documentation

- Short README, this changelog, a plain-language overview, a development guide,
  and a risk model document that records the lessons from the loss incident.
- Removed the outdated Codex model routing document.

### Strategy and scoring

- Wallets are ranked on the return and drawdown of their own equity curve from
  Hyperliquid portfolio history, with averaging down penalized and
  copyability measured at our copy size. Wallets whose trades fall below the
  minimum order at our size are capped.
- Minimum 20 closed trades and shrinkage toward a neutral score until 40.
- Daily score snapshots and the `python -m app.cli.score_backtest` tool.
- Pruning archives wallets instead of deleting them; discovery readmits them
  after 30 days. Ops reports discovery source health.
- Migration `c4e8a2f6b1d3`.

### Execution latency

- Shared pooled info client, cached allocation snapshots, lane synchronization
  only on change, cached source account state with a warm-up loop, cached SDK
  exchange objects, and a weight-based Hyperliquid rate limiter that serves copy
  execution first. WebSockets reconnect when a heartbeat goes unanswered.

### Sizing and live risk

- Copied entries are sized against a target position instead of rounding each
  small source fill up to the minimum order. Notional exposure caps of 3x the
  source allocation and 3x account equity, equal-weight allocation over five
  sources, rolling account loss limits, and a per-source loss limit.

### Platform and security

- Dashboard on Node.js 24 LTS and Tailwind CSS v4; dependency updates through
  Dependabot; SQLAlchemy 2.1 typing.
- Hardened dashboard authentication, login throttling, Next.js security
  updates, and a green CI with lint, format, type checks, dependency audits,
  coverage, and Hypothesis property tests.

## 2026-07-10 to 2026-08-06

- Live copy lifecycle, durable live work queue, cash-flow-adjusted account
  performance, the live funding ledger, the accounts page, scoring
  optimizations, operation progress and cancellation, and recovery of abandoned
  job locks after worker restarts.

## Development phases (2026-06 to 2026-07-10)

### Phase 1

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

### Phase 2

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
cash-flow-adjusted live performance snapshots.

Migration `d6a8c1e4f9b2` adds transactional wallet fill revisions so scoring
refreshes only changed wallets. Migration `e1f4a7c9d2b6` adds partial raw-fill
indexes for scoring. Migration `f3b7d9a1c5e8` adds the ordered source-trade
stream index. Migration `c4e8a2f6b1d3` adds wallet equity metrics, daily score
snapshots, the averaging-down trade column, new score columns, and wallet
archiving; it marks every materialized trade set for one rebuild on the next
scoring run. Migration `d7f1b3c5e9a2` adds the paper market simulation and
paper risk state. The current head is listed in
[docs/deployment.md](docs/deployment.md#updating-an-existing-vps).

```bash
python -m alembic current
```

The command must show the current head. Do not start the backend or workers
until that revision is current.

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

### Phase 3

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

### Phase 4

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
  only when the wallet's database-maintained `fill_revision` changes. Insert and
  delete statement triggers increment the revision in the same transaction as
  the fill mutation, including concurrent realtime imports.
  Changed wallets are rebuilt through a server-side fill stream in bounded
  batches. Progress and cancellation are checked every 25,000 relevant fills,
  and each completed wallet is committed as a durable checkpoint so a later
  failure does not restart already-finished wallets.
- Recurring scoring does not recalculate trade PnL, ROI, concentration, or
  realized drawdown from millions of raw fills. Those metrics come from the
  smaller materialized `source_trades` set. The raw-fill query only keeps the
  exact score-window fill count, first fill, latest non-liquidation activity,
  and liquidation event summary.
- Partial raw-fill indexes support non-liquidation recency, liquidation event
  ordering, and source-trade stream ordering without blocking normal fill
  ingestion while the indexes are built.
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
- The score ranks wallets by their own history. Copies are sized from the
  source's equity, so the score uses the wallet's return and drawdown on its own
  equity, read from the Hyperliquid `portfolio` equity curve and stored in
  `wallet_equity_metrics`. Deposits and withdrawals never count as returns.
- Profitability combines the 30 day time-weighted equity return (50%, full
  score at 10%), capped average trade ROI (20%), and median trade ROI (30%).
- Risk uses the equity-curve drawdown, which includes unrealized losses, and
  penalizes adds into losing positions (averaging down). The closed-trade
  drawdown is only a fallback when equity history is missing.
- Copyability scales each closed trade to our per-source allocation, based on
  the live account equity or `copyability.reference_equity_usd`, and scores how
  many trades reach the minimum order. When fewer than half do, the final score
  is capped, so very large wallets whose trades map to cents at our size cannot
  enter the copy set.
- Wallets need 20 closed trades, and profitability, consistency, and risk are
  pulled toward a neutral 50 until a wallet has 40 closed trades.
- Every scoring run writes a daily `wallet_score_snapshots` row per wallet for
  later backtests. Run `python -m app.cli.score_backtest` to check whether the
  score predicted later wallet performance against random selections.
- Score rows are kept only for active pool wallets, so archived wallets do not
  remain rankable through stale scores.
- Wallet detail pages show reconstructed source trades from `GET /wallets/{address}/source-trades`.

### Phase 5

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

The copy execution hot path avoids repeated network and database work. Both
execution consumers share one pooled, execution-priority Hyperliquid info client
and one live trading client that reuses the SDK exchange per account and skips
unchanged leverage updates. Execution reuses the allocation snapshot from the
latest realtime refresh, skips live lane synchronization while the active source
set and account lifecycles are unchanged, and reuses recent source account state
for pure entries on coins the source already holds. Exits, flips, and new coins
always read fresh source state. A `source-state-warmup` loop keeps the state of
active sources with open positions fresh at background priority. A per-process,
weight-based limiter follows Hyperliquid's documented info request weights and
serves copy execution before background requests, and every WebSocket
reconnects when a heartbeat ping goes unanswered.

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
  Archived wallets are evaluated again 30 days after archiving.
- Discovery prefilters new candidates, then backfills accepted candidates.
- Candidates that pass backfill quality checks, including at least 20 closed
  trades and at most 40% equity drawdown, are inserted directly into the wallet
  pool.
- Several discovery sources are unofficial endpoints. `/ops` shows each source's
  last success and consecutive failures and warns when one breaks or returns no
  wallets. Check each source's terms of use before relying on it.
- Discovery retries Hyperliquid 429 responses with backoff and stops the current
  backfill batch cleanly if rate limits persist.
- Manual pruning runs through `POST /wallets/prune-all`, which applies orphan-fill,
  zero-fill, stale-fill, minimum closed-trades, equity drawdown, and low-score
  cleanup in one reviewed operation. Wallet rules archive instead of deleting:
  archived wallets leave the pool, scoring, and copying, but keep their
  reconstructed trades, score snapshots, and equity history for backtests.
  `GET /wallets?archived=true` lists them.
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
- Manual wallet deletion removes research and materialized source data,
  including fills, scores, equity metrics, monitoring state, reconstructed
  source trades, ignored-fill state, inactive allocations, and legacy copy
  links. Discovery records and completed paper or live execution history remain
  available for audit.
- Wallet risk scoring can include current open perp drawdown from Hyperliquid.
  It also calculates open position stress from live unrealized loss, margin
  usage, and notional exposure. Unified wallets use unified account value from
  `spotClearinghouseState` as the denominator; standard wallets use perps
  account value. `backend/config/scoring.json` controls whether live state is
  enabled, fetch concurrency, missing-state penalty, max risk penalties, and
  current-drawdown final score caps. By default, current drawdown penalty scales
  from 5% to 75% drawdown, and the final score cap scales from 25% to 100%
  drawdown.
- Live current-drawdown enrichment has a five-minute run budget and checks
  the strongest history-based candidates first. Wallets not completed inside
  that budget still receive refreshed history scores with live risk marked
  unavailable, so scoring and score-dependent prune cannot remain blocked.
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
- The maintenance worker runs pool reimport every 30 minutes by default.
  Scoring and configured prune rules run after that import only when it persisted
  new fills.
- Worker pruning is intentionally sharp by default. `backend/config/prune.json`
  sets `wallet_prune_worker_dry_run` to `false`, so scheduled pruning deletes
  matching wallets and related rows after pool import.
- Discovery import, pool import, scoring, pruning, and paper-copy recovery use
  database-backed job locks so worker services and manual dashboard actions do
  not run the same long job concurrently.
  Job owners renew these leases at least every 30 seconds. A row without a
  renewal for 90 seconds is stale and can be taken over even when its long
  safety TTL has not expired.
- The home dashboard shows live stage text and compact progress bars for
  discovery import, pool reimport, and scoring.
- `POST /operations/{key}/cancel` records a run-specific cancellation request
  for those three jobs. The active job acknowledges it at a safe checkpoint,
  rolls back uncommitted work, and finishes with `canceled` operation status.
- Status polling reconciles stored `running` or `Stopping` state with the durable
  job lock. A status without an active worker becomes `interrupted` or
  `canceled` instead of remaining stuck.
- Pool maintenance prunes changed data only after scoring reports success. A
  failed or lock-skipped scoring run cannot silently feed stale scores into prune.
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

### Phase 6

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
- Every new live entry must pass a fresh reconciliation check, intent TTL, the
  rolling 24 hour, rolling 7 day, drawdown, and per-source loss limits, the 50
  orders-per-minute limit, and price safety checks. A breached stateful
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
- Account loss limits use the time-weighted performance index from each
  reconciliation, so deposits and withdrawals never count as losses: 10% over a
  rolling 24 hours, 20% over a rolling 7 days, and 25% drawdown from the peak. A
  breach leaves the account exit-only until it is started again. A single source
  stops opening new positions once its 7 day realized loss plus current
  unrealized loss reaches 5% of account equity.
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
- `backend/config/trading.json` owns shared copy allocation, exposure caps,
  min-order rounding, price drift, and mid-price cache policy.
- `backend/config/paper_trading.json` owns paper simulation settings only, not
  account creation.

Sizing policy:

- The top 5 scored wallets (`trading_copy_top_wallet_count`) are eligible for
  copy allocation.
- Open paper-position sources have realtime priority in paper-only mode. When
  live trading is enabled, open live exposure and the current top 10 own the
  realtime slots while paper-only exposure is maintained by fill import and
  recovery.
- Retained sources outside the current top 10 can add to existing matching paper
  positions and can reduce or close them, but cannot open completely new paper
  positions.
- Each source receives an equal share of the allocation budget, 20% each for
  five sources.
- Entries follow a target position: allocation divided by source perp equity,
  times the source position after the fill. Orders are placed at the source
  fill, so the copied entry price stays close to the source, and each order only
  buys the gap to the target.
- A new position always opens with at least the 10 USD minimum at the source's
  first fill price. Later adds round to the nearest minimum step, and an add
  never buys more than its own fill plus one minimum order. The copied position
  stays within one minimum order of the target, instead of growing by 10 USD for
  every small source fill.
- Notional exposure is capped at 3x the source allocation per source and 3x
  account equity in total. Exits close the same share of the copied position as
  the source closes of its own.
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
- Rounding to the 10 USD minimum never exceeds an exposure cap. The minimum
  matches Hyperliquid's live perp minimum order value.
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
- Skip reasons distinguish a reached target position, a gap below half the
  minimum order, the source and total exposure caps, missing matching positions,
  and price safety guards.
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
  realized PnL. Unmatched exchange closes inherit the source wallet only when
  the current source position or the complete source-attributed fill inventory
  proves one owner with enough remaining size. Historical unattributed closes
  are repaired during normal account reconciliation. Ambiguous closes remain
  explicitly unattributed instead of guessing a wallet.
- Live fills and reconstructed closed live trades expose a liquidation flag
  only when the raw Hyperliquid fill contains its `liquidation` marker. The
  Trading page shows a compact red `liquidated` badge and a forced-liquidation
  result while retaining the attributed wallet link when ownership is proven.
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
