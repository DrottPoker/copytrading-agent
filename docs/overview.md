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
