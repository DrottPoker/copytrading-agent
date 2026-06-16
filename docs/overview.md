# Project Overview

Hyperliquid Copy Agent is a paper-first wallet monitoring and copytrading research
system for Hyperliquid.

The project is designed to monitor a large pool of wallets, import their historical
fills, listen to realtime fills for a limited active set, and eventually rank wallets
by how copyable their behavior is after delay, fees, slippage, and risk controls.
The current research focus is perp trading, so spot-only wallet history is filtered
out of imports and can be pruned from the pool.

Live trading is intentionally disabled by default. The system is built to prove
edge in monitor and paper mode before any live execution is added.

## What It Does Today

- Manages a watched wallet pool.
- Imports historical Hyperliquid fills into Postgres.
- Stores perp fills by default and filters out spot-only history.
- Deduplicates fills by wallet and external fill ID.
- Monitors realtime Hyperliquid `userFills` for selected wallets.
- Stores realtime snapshots and new fills safely.
- Publishes system and fill events through Redis.
- Shows wallet data, imported fills, and live events in the dashboard.

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
- Paper trading is the default future execution path.
- Live trading is disabled unless explicitly enabled and acknowledged in config.

## Main Interfaces

- Backend API: `http://127.0.0.1:8000`
- Dashboard: `http://127.0.0.1:3000`
- Wallet Pool: `http://127.0.0.1:3000/wallets`
- Live Feed: `http://127.0.0.1:3000/live-feed`
