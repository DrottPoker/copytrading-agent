# Project Overview

Hyperliquid Copy Agent is a paper-first wallet monitoring and copytrading research
system for Hyperliquid.

The project is designed to monitor a large pool of wallets, import their historical
fills, listen to realtime fills for a limited active set, and eventually rank wallets
by how copyable their behavior is after delay, fees, slippage, and risk controls.
The current research focus is perp trading, so spot-only wallet history is filtered
out of imports and can be pruned from the pool.

Live trading is intentionally disabled by default. The system is built to prove
edge in monitor and paper mode before any live execution is activated.

## What It Does Today

- Manages a watched wallet pool.
- Imports historical Hyperliquid fills into Postgres.
- Stores perp fills by default and filters out spot-only history.
- Deduplicates fills by wallet and external fill ID.
- Monitors realtime Hyperliquid `userFills` for selected wallets.
- Stores realtime snapshots and new fills safely.
- Simulates paper copy fills for the top scored realtime wallets.
- Publishes system and fill events through Redis.
- Shows wallet data, imported fills, and live events in the dashboard.
- Shows paper accounts, allocations, positions, and recent paper fills.
- Runs paper copy by default and supports guarded live copy, durable live order
  recovery, and authoritative live reconciliation in the trading worker when
  intentionally enabled.
- `watched_wallets.copy_eligibility_started_at` is the global source-selection
  epoch. `LiveCopySourceState` is per live account/source. Normal entries need
  both an immutable first observation at or after `activated_at` and an
  authoritative source timestamp at or after activation. Baseline IDs only
  scope recovery candidates, so same-timestamp late arrivals are not
  automatically eligible. Only a narrowly proven same-side owned continuation
  can cross a fresh retained baseline.
- `LiveCopySourceState.entry_eligible` is the authoritative per-account routing
  truth. Current selection sets it to true. Owned-only retention keeps the lane
  active with the flag false, and reselection requires a fresh baseline before
  setting it true again.
- Temporary reconciliation and source-state prerequisites retry with bounded
  backoff instead of producing fake live order failures. Recovery retains
  nonzero positions and unresolved orders, including filled orders whose fills
  are not fully materialized. Completed dispositions are excluded before limits.
- Live source events use canonical numeric fill ordering with close-before-open
  sequencing. Lost attribution can be restored only from strict current
  executed-fill proof, excluding exchange and manual-test reserved sources.
  Every multipart plan is committed before exchange submission, with the final
  gate lock order preserved. Separate pipeline decisions remain visible without
  being called fills or orders when no `tradingOrderId` exists.
- Existing `TradingOrder` history is never hidden by lifecycle recovery, and
  `live_copy_source_states` remains audit state protected by `RESTRICT`, outside
  wallet cleanup ownership.
- Protects backend routes with dashboard Basic Auth by default.
- Coordinates long-running jobs with database-backed locks.
- Discovers new candidates from Hyperliquid leaderboards, leaderboard
  subaccounts, Hyperliquid 7D and 30D vault leaders, vault addresses, and
  HyperTracker PnL segments, plus the HyperTracker avg daily perp PnL
  leaderboard and configured Hyperdash profitable cohorts before backfill and
  scoring decide pool admission.
- Treats Hyperliquid vault addresses and vault leader addresses as separate
  candidates, because a vault can trade independently from the leader wallet.
- Skips vault candidates below the discovery `min_account_value_usd` threshold,
  then ranks remaining vaults by the vault source window's ROI first, 7D or 30D,
  and TVL second.

## Core Idea

There are two separate data flows:

- Historical polling is for scoring and research across the full wallet pool.
- Realtime monitoring is for active wallets and open position management.
- Raw `WalletFill` rows are the complete source audit record. Live execution
  decisions are tracked separately per account, source fill, and lifecycle part
  so retries, terminal decisions, and baseline history remain distinguishable.

This separation matters because Hyperliquid limits user-specific realtime WebSocket
subscriptions, while historical polling can cover many more wallets at a slower,
controlled pace.

## Current Mode

The current application runs in paper mode:

- Monitoring is enabled.
- Paper copy simulation is enabled by default.
- Live trading is disabled unless `LIVE_TRADING_ENABLED=true` is set in `.env`.
- Scheduled pruning is sharp by default and deletes matching non-active wallets
  after pool import when configured prune rules match. Sources with open paper
  positions are exempt until the copied exposure is closed.
- Current scoring feeds paper allocation for the top 10 monitored wallets, but it
  is still a research ranking signal until paper results are validated over time.

## Main Interfaces

- Backend API: `http://127.0.0.1:8000`
- Dashboard: `http://127.0.0.1:3000`
- Wallet Pool: `http://127.0.0.1:3000/wallets`
- Live Feed: `http://127.0.0.1:3000/live-feed`
- Ops Health: `http://127.0.0.1:3000/ops`
- Trading: `http://127.0.0.1:3000/trading`

Browser dashboard API calls use the Next.js proxy at `/api/backend`. Server-side
dashboard calls use `SERVER_API_BASE_URL`, defaulting to `http://127.0.0.1:8000`
locally. Docker Compose injects the fixed internal service address
`http://backend:8000`; it is not a user-tweakable `.env` setting.
