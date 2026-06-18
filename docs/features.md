# Features

This document lists the current product features and the intended near-term
features that are already represented in the architecture.

## Implemented

### Health Check

Endpoint: `GET /health`

Shows service status, environment, current mode, Hyperliquid network, and dependency
status for Postgres and Redis.

Behavior:

- Returns `status: "ok"` only when required dependencies are healthy.
- Returns `status: "degraded"` with HTTP 503 when Postgres or Redis is unavailable.
- `GET /ready` returns the same readiness payload.

### Dashboard API Auth

Backend routes are protected by Basic Auth by default.

Behavior:

- `GET /health` and `GET /ready` remain unauthenticated for uptime checks.
- All other backend routes require `DASHBOARD_AUTH_USERNAME` and
  `DASHBOARD_AUTH_PASSWORD`.
- Production startup fails if auth is disabled or the password is still
  `change-me`.
- The Next.js dashboard proxies browser API calls through `/api/backend` and adds
  backend auth on the server side.
- Server-side dashboard data fetches also add backend auth from environment
  variables.

Config:

- `DASHBOARD_AUTH_ENABLED`
- `DASHBOARD_AUTH_USERNAME`
- `DASHBOARD_AUTH_PASSWORD`

### Wallet Pool Management

Endpoints:

- `GET /wallets`
- `POST /wallets`
- `GET /wallets/{address}`
- `PATCH /wallets/{address}`
- `DELETE /wallets/{address}`

Dashboard page:

- `/wallets`

What it does:

- Adds watched Hyperliquid wallets.
- Enables or disables wallets.
- Forces cooldown state.
- Stores labels and notes.
- Shows last seen fill time.
- Deleting a wallet removes its fills, scores, positions, copy rows, and source
  links so removed pool wallets do not leave orphan fill data behind.

### Discovery Sources

Endpoints:

- `GET /discovery/sources`
- `POST /discovery/import`
- `GET /discovery/candidates`
- `GET /discovery/runs`
- `POST /discovery/prefilter`
- `POST /discovery/backfill`
- `POST /discovery/promote`

What it does:

- Collects wallet candidates before quality-controlled pool admission.
- Skips wallet addresses that already exist in discovery candidates or in the
  wallet pool, so previously processed wallets are not reprocessed by new imports.
- Tags every candidate with the source that found it, source rank, cohort/label,
  `source_account_value_usd`, `source_pnl_usd`, `source_roi_pct`, copy score
  when available, and account role.
- Stores discovery import runs so source quality can be measured over time.
- Supports Hyperliquid 1D, 7D, 30D, and all-time leaderboard sources.
- Defaults to Hyperliquid 7D and 30D leaderboard discovery.
- Can optionally include leaderboard subaccounts as candidates.
- Includes configurable Hyperdash source adapters, but they require stable
  `discovery_hyperdash_*_url` config values before use.
- Runs a configurable source-metadata prefilter after import by default.
- Keeps discovery tunables in organized `backend/config/discovery.json` sections:
  discovery sources, discovery import, prefilter, candidate backfill, quality,
  and promotion.
- Keeps pool reimport and shared fill import tunables in
  `backend/config/pool_fill_import.json`.
- Marks candidates as `accepted` or `rejected` and stores a machine-readable
  `fail_reason` such as `account_value_too_large`, `source_pnl_not_positive`,
  or `source_roi_below_min`.
- Keeps prefilter rules in `backend/config/discovery.json`.
- Candidate backfill imports configured perp fills, reconstructs source trades,
  stores fill/trade metrics on the candidate, and applies trade-quality rules.
- Hyperliquid 429 responses are retried with backoff; if rate limits persist, the
  current backfill batch stops cleanly instead of failing the full API request.
- Candidates that pass backfill quality checks are inserted directly into the
  wallet pool and marked as `promoted`.
- Trade-quality reject reasons include `no_perp_fills`, `too_few_closed_trades`,
  `net_pnl_not_positive`, `profit_factor_below_min`,
  `max_drawdown_too_high`, and `too_many_ignored_fills`.

### Historical Fill Import

Endpoint:

- `POST /wallets/{address}/fills/import`
- `POST /wallets/fills/import-pool`

What it does:

- Pulls historical fills from Hyperliquid `userFillsByTime`.
- Stores them in Postgres.
- Defaults to storing only perp fills, based on Hyperliquid fill direction such as
  `Open Long`, `Close Short`, or flip directions.
- Paginates in 2k Hyperliquid chunks until it reaches the requested `targetFills`
  count after filtering, with an API cap of 10k stored perp fills per import call.
- Deduplicates fills by wallet and external fill ID.
- Stores only configured raw payload fields, default `dir`, `liquidation`, `startPosition`, and `twapId`.
- Updates wallet last poll and last seen fill timestamps.
- Returns fetched, inserted, and duplicate counts.
- Returns raw fetched and page counts so spot-heavy wallets are visible during import.
- The pool importer works through all enabled wallets in configured batches.
- First-time wallets get the full configured backfill window; already-polled wallets refresh incrementally.
- Manual pool reimport uses `force=true` by default, so it refreshes the full
  enabled pool regardless of `last_polled_at`.
- Worker pool maintenance runs every 30 minutes by default and uses the same
  batch settings as manual reimport.
- After maintenance worker pool reimport, wallet scoring runs immediately, then configured
  prune rules run automatically.
- Fill imports stop early when the database is near its configured storage limit.
- Pool import uses a database-backed job lock and row-level `SKIP LOCKED`
  selection to avoid duplicate refresh work across API and worker processes.

Config:

- `backend/config/pool_fill_import.json`

### Manual Wallet Pruning

Endpoint:

- `POST /wallets/prune-all`

What it does:

- Runs all active cleanup rules in one operation.
- Orphan-fill cleanup removes stored fill data for addresses that are no longer
  present in the wallet pool.
- Zero-fill cleanup removes polled wallets with exactly zero stored fills.
- Minimum closed-trades cleanup removes polled, scored wallets below the configured
  reconstructed closed-trade threshold.
- Realized drawdown cleanup removes polled, scored wallets whose reconstructed
  closed-trade drawdown is at or above the configured threshold.
- High-fill low-score cleanup removes polled wallets whose final score matches
  the configured cutoff in `backend/config/prune.json`.
- Current drawdown cleanup removes wallets whose live unrealized loss breaches
  the configured account-value threshold.
- Excludes copy-enabled, active, exit-only, and open paper-position source
  wallets from cleanup candidates. Orphan-fill cleanup also keeps fill history
  for sources that still have open paper positions.
- Runs as a dry run by default and supports `dry_run=false` for deletion.
- Returns totals and per-rule results for review in the Database dashboard.
- Reports current drawdown fetch errors separately. Those wallets are shown in
  the response but are not counted as delete candidates.
- Uses a shared `wallet_prune` job lock, so manual pruning and scheduled maintenance worker
  pruning cannot run concurrently.

Purpose:

- Keep the research pool focused on perp traders.
- Avoid managing several overlapping cleanup buttons for the same pruning pass.

### Current Drawdown Wallet Cleanup

Individual endpoint:

- `POST /wallets/prune-current-drawdown`

Normal manual pruning runs this through `POST /wallets/prune-all`.

What it does:

- Fetches live Hyperliquid `clearinghouseState` for enabled pool wallets across
  default perp and known perp dex prefixes from stored fills.
- Deletes wallets whose total open perp unrealized loss is at least the configured
  share of perp equity. Default threshold is `0.40`, meaning unrealized PnL is
  `<= -40%` of perp equity.
- Excludes copy-enabled, active, and exit-only wallets from cleanup candidates.
- Runs as a dry run by default and supports `dry_run=false` for deletion.
- Adds deleted addresses to the discovery ignore list so they are not imported
  back into the pool immediately.
- Reports Hyperliquid fetch errors per wallet and never deletes wallets whose
  current state could not be fetched.

Config:

- `wallet_prune_unrealized_loss_ratio`
- `wallet_prune_current_state_concurrency`

Purpose:

- Remove wallets carrying severe current open-position losses.
- Avoid promoting leaderboard accounts whose recent ranking is driven by realized
  history while their live account state is currently distressed.

### Realized Drawdown Cleanup

Normal manual pruning runs this through `POST /wallets/prune-all`.

What it does:

- Uses stored `wallet_scores.max_drawdown_pct` from reconstructed closed perp
  trades. The UI labels this as realized drawdown because it does not include
  intratrade open-position drawdown.
- Deletes polled, scored wallets whose realized drawdown is at least the
  configured threshold. Default is `0.60`, meaning `>= 60%`.
- Excludes copy-enabled, active, exit-only, never-polled, and unscored wallets.
- Runs as a dry run by default and supports `dry_run=false` for deletion.

Config:

- `backend/config/prune.json`
- `wallet_prune_max_drawdown_pct`

Purpose:

- Remove wallets whose realized trade history shows unacceptable drawdown even if
  their current open-position state looks acceptable.

### High-Fill Low-Score Cleanup

Individual endpoint:

- `POST /wallets/prune-high-fill-low-score`

Normal manual pruning runs this through `POST /wallets/prune-all`.

What it does:

- Finds polled wallets with a stored final score and a fill count at or above the
  configured minimum.
- Compares final score with the configured threshold using `lte` or `gte`.
- Excludes copy-enabled, active, exit-only, and never-polled wallets.
- Runs as a dry run by default and supports `dry_run=false` for deletion.
- Adds deleted addresses to the discovery ignore list so they are not imported
  back into the pool immediately.

Config:

- `backend/config/prune.json`
- `wallet_prune_low_score_min_fills`
- `wallet_prune_min_closed_trades`
- `wallet_prune_max_drawdown_pct`
- `wallet_prune_low_score_threshold`
- `wallet_prune_low_score_operator`

Purpose:

- Remove high-history wallets that still score at or below the configured cutoff.
- Keep unpolled wallets in the pool until they have been evaluated.

### Source Trade Reconstruction

Endpoint:

- `GET /wallets/{address}/source-trades`

What it does:

- Reconstructs source perp trades from imported fills.
- Shows closed and currently open reconstructed source trades.
- Displays entry price, exit price, size, notional, realized PnL, fees, net PnL,
  duration, and entry/close fill counts.
- Reports ignored close-only fills and adds to positions that already existed before the observed window.

Purpose:

- Inspect the actual trades behind a wallet score.
- Separate copyable trades from historical close-only PnL.

### Wallet Current State

Endpoint:

- `GET /wallets/{address}/stats`

What it does:

- Queries Hyperliquid `perpDexs`, then fetches `clearinghouseState` for default
  perp plus each known perp dex so wallets with HIP-3 positions are shown
  correctly.
- Aggregates perp equity, margin, open positions, and unrealized PnL across
  those venues. Spot balances are shown separately and are not counted as perp
  equity.
- Shows current unrealized drawdown as the current open perp loss divided by
  perp equity. This is separate from realized score drawdown.
- For isolated HIP-3 positions, Hyperliquid `marginSummary.accountValue` can be
  isolated position equity and move together with `totalMarginUsed`. It is not
  a stable wallet cash balance.
- Fetches `spotClearinghouseState` for spot token balances and entry notional exposure.
- Syncs open perp positions into `wallet_positions` only when every requested
  perp state fetch succeeds.
- Stores Hyperliquid open position value in `wallet_positions.position_value_usd`.
- Keeps current state separate from historical realized PnL based on fills.

### Fill Browsing

Endpoint:

- `GET /wallets/{address}/fills`
- `GET /wallets/{address}/stats`
- `GET /wallets/{address}/copy-trades`

Dashboard page:

- `/wallets/[address]`

What it does:

- Shows wallet-level statistics.
- Shows total fills, notional, PnL, fees, win rate, latency, and realtime/snapshot split.
- Shows 24h, 7d, and 30d windows with PnL, net ROI, notional, fees, and fills.
- Shows top traded coins.
- Shows reconstructed source trades for the wallet.
- Shows copy trades associated with the source wallet when paper/live trades exist.
- Shows recent imported fills for a specific wallet.
- Displays time, coin, side, price, size, PnL, and fee.

### Realtime Fill Monitor

Worker:

- Runs automatically inside the backend when `worker_run_in_api_process` is `true`.
- Can run as `python -m app.workers.monitor_worker` when the in-API worker is disabled.
- Docker Compose runs separate `trading-worker` and `maintenance-worker`
  services and disables the in-API worker through `WORKER_RUN_IN_API_PROCESS=false`.
- `WORKER_ROLE=trading` starts realtime monitoring and paper-copy recovery only.
- `WORKER_ROLE=maintenance` starts discovery, pool reimport, scoring, and pruning only.

What it does:

- Opens a Hyperliquid WebSocket connection.
- Subscribes to `userFills` for up to `max_realtime_wallets`.
- Retains source wallets with open paper positions first, then selects positive
  scored wallets and active, exit-only, candidate, or copy-enabled fallback
  wallets for remaining slots.
- Processes initial snapshot messages safely.
- Stores realtime fills in Postgres with the same dedupe logic as historical import.
- Publishes system and fill events to Redis.

Purpose:

- Active wallet monitoring.
- Future open position management.
- Future paper/live copy decisions.

### Paper Copytrading

Endpoint:

- `GET /paper-trading`
- `POST /paper-trading/positions/{position_id}/close`

Dashboard page:

- `/paper-trading`

What it does:

- Syncs configured paper trading accounts from `backend/config/paper_trading.json`.
- Defaults to two paper accounts, 1,000 USD and 10,000 USD.
- Builds allocations from the top 10 positive wallet scores in the enabled wallet pool.
- When current drawdown scoring is enabled, allocation only uses wallets whose
  latest score has `current_drawdown_status = "ok"`.
- Keeps source wallets with open paper positions subscribed until all copied
  positions from that source are closed, even if the source falls out of the top 10.
- Makes newly promoted top 10 wallets wait when all realtime slots are occupied
  by retained open-position sources.
- Allocation refresh follows the realtime slot model: open paper-position
  sources reserve slots first, then remaining slots go to highest scored
  candidates. Top candidates without a slot are marked as waiting and cannot
  open new paper positions until a realtime slot is available.
- Gives all top 10 ranks a 20% account pocket each.
- Caps total open copied margin at 80% of each paper account equity.
- Converts new non-snapshot realtime source fills into simulated paper fills.
- Sizes an open by `source fill notional / source perp equity`, scaled inside
  that wallet's paper allocation pocket.
- Requires valid source perp equity for opens and adds, but not for exits. Close,
  reduce, and flip-close parts are still processed against existing paper
  positions if Hyperliquid reports zero or unavailable source equity after the
  source position is already closed.
- Stores the source perp equity snapshot for each paper fill in
  `paper_copy_fills.source_perp_equity_usd`.
- Fetches source perp equity from Hyperliquid `clearinghouseState`, which is
  perp account state. Spot balances are not used for paper copy sizing.
- Fetches `clearinghouseState` per perp dex when fills use prefixed coins such
  as `dex:COIN`, so perp equity, leverage, and open positions are read from
  the matching perp venue.
- Isolated HIP-3 perp equity can equal isolated margin used. In that case an
  all-in isolated source position can fill the whole paper pocket, and later
  adds are skipped once the fixed paper pocket is full.
- Reads source per-coin leverage from Hyperliquid `clearinghouseState` and uses
  it for paper margin accounting. If leverage is unavailable for a coin, paper
  falls back to 1x.
- Resolves common Hyperliquid coin aliases for live mids and leverage, including
  matching `dex:COIN` fills against `COIN` market keys when the exact key is not
  present.
- Skips opens below `paper_copy_min_order_notional_usd` before any paper position
  is created.
- Applies the configured paper fee rate to opens and closes.
- Waits `paper_copy_latency_ms` before pricing paper execution.
- Uses live Hyperliquid mids after latency when `paper_copy_use_live_mid_price`
  is enabled.
- Falls back to dex-specific `allMids`, then Hyperliquid `metaAndAssetCtxs`, for
  the fill's perp dex when default `allMids` does not contain a `dex:COIN`
  market key.
- Applies adverse slippage from `paper_copy_slippage_bps` to the observed price.
- Skips paper fills when the observed price has moved more than
  `paper_copy_max_price_drift_bps` from the source fill price.
- Tracks open paper positions by account, source wallet, and coin.
- Computes live mark price, current notional, unrealized PnL, and ROE for open
  paper positions in the paper summary API when Hyperliquid market data is
  available.
- Aggregates paper PnL by source wallet so the dashboard can show which copied
  wallets made or lost money across accounts.
- Returns a closed trade history built from paper `close` and `flip_close`
  executions, separate from the raw recent fill and skip log.
- Supports manual paper-position closes from the dashboard. Manual closes price
  from the current simulated market price, apply configured adverse slippage and
  fee, update the paper account, delete the open paper position, and record a
  normal `close` row in `paper_copy_fills`.
- Uses source `startPosition` to reduce or close paper positions proportionally.
- Splits source flip fills into a close part and an open part when the source
  payload provides enough information.
- Orders paper-copy processing for same-timestamp fills with close and
  flip-close fills first by descending source `startPosition`, so large source
  exits split across many fills reduce paper positions in a stable order.
- Persists paper accounts, positions, allocations, and copied fill IDs in
  Postgres so Docker restarts do not reset paper trading state.
- Runs paper-copy recovery on trading-worker start, WebSocket snapshots, and the
  configured periodic recovery interval.
  Recovery replays fills from the oldest open paper position when a source still
  has copied exposure, with a small overlap, and relies on copied fill IDs to
  avoid duplicate paper fills.
- Uses a `paper_copy_recovery` job lock so startup, snapshot, and periodic
  recovery cannot run over each other.
- Recovery can retry earlier exit skip rows caused by unavailable source state
  or unavailable execution price, so a close is not permanently blocked by a
  transient paper-copy data issue.
- Recovery also compares open paper positions with the source wallet's live perp
  state. If the source no longer has the same coin and side, paper closes the
  position at the current simulated market price with normal fee and slippage.
- Retains allocation records for source wallets with open paper positions so
  add, reduce, close, and flip fills can continue after the source falls out of
  the current top 10.
- Retained sources outside the current top 10 can add to an existing matching
  paper position and can reduce or close it, but cannot open a completely new
  paper position. New entries are skipped with
  `retained_source_new_position_blocked`.
- Restores any source with open paper positions into `watched_wallets` as
  `exit_only` if it was removed earlier, so pool imports and realtime slot
  retention can continue until the paper exposure is closed.
- Shows allocation pocket usage as current open paper margin divided by that
  account/source wallet pocket.
- Records skip rows when a fill cannot be copied safely, such as no matching
  paper position, missing source perp equity, preexisting source position,
  minimum notional, source allocation cap exhaustion, or total account cap
  exhaustion.
- Publishes `paper_copy` events to the live feed when realtime fills are simulated.
- The paper trading dashboard polls the summary API and shows account PnL,
  monitored sources, currently trading sources, open position PnL, wallet PnL
  history, and closed trade history without a full page refresh.
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
- The dashboard separates total, realized, and unrealized PnL and uses compact
  responsive list rows instead of wide tables or large cards.

Config:

- `backend/config/paper_trading.json`
- `paper_copy_enabled`
- `paper_copy_accounts`
- `paper_copy_top_wallet_count`
- `paper_copy_top_tier_wallet_count`
- `paper_copy_top_tier_allocation_pct`
- `paper_copy_standard_allocation_pct`
- `paper_copy_max_total_allocation_pct`
- `paper_copy_min_order_notional_usd`
- `paper_copy_fee_rate`
- `paper_copy_slippage_bps`
- `paper_copy_latency_ms`
- `paper_copy_max_price_drift_bps`
- `paper_copy_use_live_mid_price`
- `paper_copy_recovery_interval_seconds`

Current limitations:

- This is paper money only. It never places Hyperliquid orders.
- The execution model is still deterministic: it uses live mids, configured
  latency, configured adverse slippage, and a max drift guard, but it does not
  simulate order book depth or partial fills yet.
- Paper accounts are not backfilled from historical fills. They start recording
  only from new non-snapshot realtime fills after the migration is applied.
- Editing `starting_balance_usd` does not reset existing account state. A reset
  workflow should be added before serious experiment runs.

### Live Feed

Endpoints:

- `GET /events/recent`
- `GET /events`

Dashboard page:

- `/live-feed`

What it does:

- Shows recent system and fill events.
- Uses Server-Sent Events when available.
- Falls back to polling recent events.
- Reads from Redis, not directly from Postgres.
- If SSE fails after connection, the dashboard falls back to polling
  `/events/recent`.

### Redis Runtime Event Store

What it does:

- Publishes events to channels such as `events:all`, `events:fills`, and
  `events:system`.
- Stores a short recent-event list in `events:recent`.
- Acts as runtime event infrastructure for dashboard updates.

### Config Separation

Files:

- `backend/config/app.json`
- `backend/config/discovery.json`
- `backend/config/paper_trading.json`
- `backend/config/pool_fill_import.json`
- `backend/config/prune.json`
- `backend/config/scoring.json`
- `frontend/config/app.json`
- `.env`

What it does:

- Keeps tweakable non-secret settings in JSON config files.
- Keeps secrets and connection strings in `.env`.
- Makes common system tuning possible without editing environment variables.
- Environment variables override JSON config. This allows compose and deployment
  environments to change runtime behavior without editing tracked config files.
- `backend/config/discovery.json` owns source discovery, candidate filtering,
  backfill quality checks, and promotion.
- `backend/config/pool_fill_import.json` owns scheduled pool reimport and shared
  fill import storage and market-filter settings.
- `backend/config/scoring.json` owns scoring schedule, score windows, weights,
  score curves, thresholds, and penalties.

### Job Locking

Table:

- `job_locks`

What it does:

- Prevents duplicate discovery imports, discovery backfills, discovery promotion,
  pool imports, scoring runs, and wallet pruning.
- Uses TTL-based Postgres rows so a crashed worker does not block the job forever.
- Returns HTTP 409 for manual API triggers when the same job is already running.

### Backend Dependency Constraints

File:

- `backend/constraints.txt`

What it does:

- Pins top-level backend package versions for Docker builds.
- Keeps local `pyproject.toml` readable while preventing silent dependency drift
  in container builds.

### Frontend Linting

Command:

- `npm run lint`

What it does:

- Runs ESLint through `eslint .`.
- Uses the Next.js flat ESLint config in `frontend/eslint.config.mjs`.

## Partially Implemented Foundations

### Database Schema

The schema already includes tables for:

- wallet fills
- wallet positions
- wallet scores
- wallet score snapshots
- active copy wallets
- copy signals
- copy trades
- source trade links
- source trades
- source trade ignored fills
- source trade sync states
- risk events
- settings
- job locks
- paper trading accounts
- paper copy allocations
- paper positions
- paper copy fills
- audit logs

Not all tables are fully used yet.

### Active Copy Set Shape

The database supports active and exit-only wallet states, realtime-slot tracking,
rank, score, and blocked promotion state.

The full rotation logic is not implemented yet.

## Planned

### Periodic Fill Polling

Purpose:

- Poll all enabled wallets over time.
- Import recent historical fills without requiring manual clicks.
- Feed scoring for the full wallet pool.

This is separate from realtime monitoring.

### Scoring

Endpoints:

- `GET /scores`
- `POST /scores/recalculate`

Purpose:

- Calculate wallet score, profitability score, copyability score, risk score,
  consistency score, recency score, and penalties.
- Rank wallets based on copyable performance, not just source-wallet PnL.

Phase A behavior:

- Uses imported fills from the configured scoring window, default 60 days.
- Stores the latest score in `wallet_scores`.
- Deletes stale `wallet_scores` rows for wallets that no longer exist in the
  watched wallet pool.
- Scores wallets with no fills as 0 so they are visible but not rankable.
- Reconstructs source perp trades from `raw_json.dir`, materializes them in
  `source_trades`, and refreshes a wallet's materialized trades only when its
  fill count or latest fill timestamp changes.
- Stores ignored source fills with timestamp and reason in
  `source_trade_ignored_fills`, so ignored-fill penalties stay scoped to the
  scoring window.
- Counts only trades where the opening fill was observed before the close.
- Ignores close-only PnL from positions opened before the imported window.
- Uses reconstructed trade PnL, fees, notional, active days, recency, realized
  drawdown, current drawdown, open-position stress, loss ratio, losing trade
  rate, profit distribution, and coin concentration.
- Keeps scoring tunables in organized `backend/config/scoring.json` sections:
  schedule, window, component weights, profitability, consistency, risk,
  copyability, recency, penalties, and window scores.
- Profitability score is scale-invariant. It combines total net ROI against
  reconstructed entry notional, capped average trade ROI, and median trade ROI.
  The weights are 55/30/15, and each ROI subscore maps 0% or lower to 0 and +5%
  to 100. Current-equity return is shown as reference data only because deposits
  and withdrawals can distort it. Absolute dollar PnL is also reference data only
  and does not increase the score.
- Consistency score includes profit distribution across winning closed trades.
  It calculates effective winning trades as `1 / sum(profit_share^2)` and scores
  it against the configured profit-winner target, so wallets where most profit
  comes from one or two trades score lower than wallets with repeated independent
  wins. Consistency subweights, win-rate span, and profit-factor curve are
  configurable.
- The stored `max_drawdown_pct` is exposed to the UI as realized drawdown. It is
  based on reconstructed closed trades and does not include intratrade open
  unrealized PnL.
- When `scoring_current_drawdown_enabled` is true, scoring fetches live perp state
  from Hyperliquid for default perp and any perp dexes already observed in stored
  wallet fills, then stores `current_drawdown_pct` and
  `current_drawdown_status` on `wallet_scores`. Current drawdown is open
  unrealized perp loss divided by perp equity.
- It also stores `open_position_stress_pct`, a normalized live stress metric
  from unrealized loss, margin usage, and notional exposure. By default, notional
  exposure reaches full stress at 10x perp equity.
- Current drawdown and open-position stress reduce the risk component. Current
  drawdown can scale up to a 35 point risk penalty at 40 percent drawdown.
  Open-position stress can scale up to a 25 point risk penalty at full stress.
  The larger of those two live-state penalties is used so the same open loss is
  not double-counted.
- Risk loss-ratio, realized-drawdown, and losing-rate penalty multipliers and
  caps are configurable.
- `GET /scores/{address}/detail` returns component-level explanations for the
  wallet detail scoring modal. The response includes gross score, penalty,
  final score before sample cap, any sample cap, component weights, weighted
  scores, and the input-level subscores used inside profitability, consistency,
  risk, copyability, recency, and penalty calculations.
- If current perp state cannot be fetched completely or perp equity is zero, the
  wallet keeps a history-only risk component and receives the configured
  `scoring_current_drawdown_missing_penalty`.
- Adds a confidence penalty up to `scoring_confidence_penalty_max` until the
  wallet reaches `scoring_confidence_target_trades`.
- Groups liquidation fills into account-level liquidation events and keeps them as a
  separate final-score penalty instead of mixing them into the risk component.
- Applies liquidation penalties from `backend/config/scoring.json`, default 2 points
  per liquidation event capped at 10 points.
- Caps scores for wallets below the configured minimum trade count so tiny
  samples cannot rank high. The sample-cap max score is configurable.
- Runs after each maintenance worker pool reimport when pool maintenance is enabled.
- If pool maintenance is disabled, the standalone scoring loop uses
  `scoring_interval_seconds`.
- Can be triggered manually from the Wallet Pool page. Wallet detail pages expose
  a Detailed scoring modal beside the score header for inspecting how each score
  component was calculated.

Important limitation:

- Current scoring ranks observed source-wallet history and feeds the initial
  paper allocation set. Do not use the score as a live allocation engine until
  paper copy performance has been validated with latency, slippage, exits, and
  account-level risk controls.

### Active Copy Set Rotation

Purpose:

- Promote the best wallets into the active realtime set.
- Apply hysteresis to avoid churn.
- Keep exit-only wallets in realtime until copied positions are closed.

### Position Classification

Purpose:

- Convert fills into actions:
  - open
  - add
  - reduce
  - close
  - flip

Paper copy now has basic fill classification. A reusable classification layer is
still needed before live execution, richer analytics, and risk controls.

### Risk Engine

Purpose:

- Enforce max open trades.
- Enforce daily and weekly loss limits.
- Enforce price drift and exposure limits.
- Apply kill switch and emergency stop behavior.

### Settings and Control Panel

Purpose:

- Make safe runtime controls available in the dashboard.
- Support pause, resume, kill switch, and risk/config visibility.

### Live Small Mode

Purpose:

- Future live execution with very small risk and strict manual enablement.
- Not part of the current MVP implementation.
