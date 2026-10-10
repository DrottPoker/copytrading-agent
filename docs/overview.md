# Project Overview

Hyperliquid Copy Agent looks for traders on Hyperliquid who make money in a way
we can copy, and copies their perpetual futures trades. It starts every idea in
a paper account with simulated money. Real orders are only possible after
someone deliberately turns live trading on.

This page explains the system in plain language. The details live in
[features.md](features.md), [architecture.md](architecture.md), and
[risk-model.md](risk-model.md).

## The Idea

Every trade on Hyperliquid is public. If a wallet has traded well for a long
time, we can follow it and open the same positions, scaled down to our account.
Two things decide whether that works:

- The trader has to be good over a long enough period, not just lucky once.
- Their trades have to survive copying: we see them a moment later, pay fees
  and slippage, and trade much smaller amounts. A trader whose edge disappears
  under those conditions is not worth copying, however good their own results
  look.

The system is built to answer both questions with data before real money is
involved.

## How It Works

1. **Find candidates.** The system collects wallets from Hyperliquid
   leaderboards and vaults, HyperTracker, and Hyperdash, and filters out
   accounts below 1,000 USD and wallets that trade only spot.
2. **Import their history.** For each wallet in the pool it downloads past
   trades and stores them, so wallets can be judged on what they actually did.
3. **Rank them.** Each wallet gets a score from its own history: the return
   and drawdown of its account value over time, how consistent and recent its
   trading is, whether it averages down into losers, and how well its trades
   would copy at our size. Wallets with fewer than 20 closed trades in the
   last 60 days are capped at a low score, and scores below 40 trades are
   pulled toward a neutral middle so a short lucky streak cannot top the list.
4. **Pick the sources.** The ten best ranked wallets become the copy sources.
   Each gets 20% of the account to copy with. Together that is twice the
   account, so the exposure caps, not the shares, limit the total size.
5. **Follow them live.** The system subscribes to those wallets' trades in
   realtime over WebSocket. Hyperliquid allows ten wallets this way, so the
   rest of the pool is refreshed by slower periodic polling. A wallet we still
   hold a position from keeps its slot until the position is closed, so the
   tenth ranked wallet can have to wait for a free slot.
6. **Copy the trades.** When a source opens or adds to a position, the system
   works out how big our position should be given our share and the source's
   account size, and buys the difference. When the source reduces or closes,
   we close the same share. Entries are skipped if they arrive too late or the
   price has already moved too far; exits are never skipped for those reasons.
7. **Limit the damage.** Exposure caps, loss limits, and per-source limits stop
   new entries when something goes wrong, while still letting existing
   positions close. See [risk-model.md](risk-model.md).
8. **Keep the pool fresh.** Wallets that stop trading, lose money, or score
   poorly are archived. Their history is kept so past selections can be
   checked, and they can be readmitted after 30 days.

## Paper and Live

Paper and live copy run the same decision rules on the same source trades.
The only difference is what happens at the end:

- **Paper** simulates the order. It fills against the real Hyperliquid order
  book, so large orders can fill partly or not at all, and it charges fees,
  hourly funding, and liquidates positions that run out of margin. Paper
  accounts also stop taking new entries when they hit the same loss limits as
  live accounts.
- **Live** sends a real order to Hyperliquid and then checks the exchange to
  confirm what actually happened. It refuses to trade if its picture of the
  exchange account is stale or incomplete.

Because the rules are shared, a paper account is a fair preview of what the
live account would do. Paper is on by default. Live trading is off until
`LIVE_TRADING_ENABLED=true` is set in `.env`, and each live account must then
be started on its own.

## Two Speeds of Data

Hyperliquid only allows a limited number of realtime subscriptions, so the
system works at two speeds:

- **Slow and wide:** periodic polling keeps the history of the whole wallet
  pool up to date for ranking and research.
- **Fast and narrow:** realtime subscriptions cover only the wallets being
  copied and wallets we still hold a copied position from, so their exits are
  followed as they happen. After a restart, recovery replays the trades that
  arrived while the system was down.

## What You See

The dashboard at http://localhost:3000 has these pages. A status bar at the top
of every page shows the trading mode, whether the API, Postgres, and Redis
respond, how many wallets are followed in realtime, and any job that is
running.

- **Overview:** what is happening right now. Account equity and PnL, the path
  from discovered wallets to wallets being copied, everything that needs
  attention, background jobs, the best ranked wallets, and recent events.
- **Trading:** one account at a time, paper or live. Performance, risk and
  exposure, open positions, the wallets being copied, and the history of trades,
  executions, and copy decisions. Accounts are created, started, stopped, reset,
  and closed here.
- **Activity:** trades, copies, jobs, and risk events as they happen, with
  filters and a pause button.
- **Wallets:** the wallet pool ranked by score, archived wallets, and a detail
  page per wallet that explains its score.
- **Discovery:** discovery runs, how many candidates each source delivers and
  why they are rejected, and the candidates themselves.
- **Analytics:** score distribution, promising and risky wallets, and
  performance by source and coin.
- **System:** worker and service health, backups, the database, and the
  cleanup tools.

## Words Used in This Project

- **Source wallet:** a trader we copy.
- **Allocation:** the share of our account set aside for one source.
- **Target position:** the position size we aim for when copying a source:
  our allocation divided by the source's account value, times the source's
  position.
- **Minimum order:** Hyperliquid does not accept orders below 10 USD. Small
  source trades often fall below this at our size, which is why sizing and
  scoring both account for it.
- **Exit-only:** an account that may close positions but not open new ones,
  usually because a loss limit was hit.
- **Reconciliation:** comparing our records with the exchange account and
  correcting our records to match it.

## Where to Go Next

- To run it locally: [development.md](development.md).
- To run it on a server or enable live trading: [deployment.md](deployment.md).
- To understand the safety limits: [risk-model.md](risk-model.md).
