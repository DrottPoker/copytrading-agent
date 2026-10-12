# Risk Model

This document explains how the system limits position size and losses, why
each limit exists, and what the loss incident taught us. The limits apply to
live trading and, with the same rules, to paper accounts.

## Principles

- **Copy in proportion, never bigger.** A copied position should carry the
  same share of risk for our account as the source position carries for the
  source. Rounding, minimum orders, or leverage must never make the copy
  riskier than the original.
- **Entries are gated, exits are not.** Every limit stops new exposure. No
  limit ever blocks closing or reducing a position, because a guard that traps
  us in a losing position is worse than no guard.
- **Losses are measured on equity, not trades.** Limits use account value
  including open positions, adjusted for deposits and withdrawals, so a large
  unrealized loss counts before it is realized.
- **Paper first.** Paper accounts run the same rules, so a strategy change is
  tested on paper before it can cost money.

## The Incident

The first live run copied ten wallets with a margin-based allocation per
source. It grew an account from about 800 USD to about 1,100 USD in a month.
Then one source, a very large wallet, scaled into a position with many small
fills while the price moved against it. The wallet was down 1 to 2% on the
trade. Our account was down 30% from that single trade, which erased the
month's profit, and live trading was stopped.

What went wrong:

1. **Every small fill became a full minimum order.** The copy was sized fill
   by fill. A 5,000 USD fill from a wallet with millions in equity maps to
   less than a dollar at our size, and every one of them was rounded up to
   Hyperliquid's 10 USD minimum. A wallet that scales in with a hundred small
   fills therefore built a position for us that was many times its
   proportional size.
2. **The caps limited margin, not size.** Allocation was a share of margin, so
   a source running high leverage could carry a much larger notional position
   than its share of the account suggested.
3. **The loss check was too slow and too loose.** The only account limit was
   a 50% loss per calendar week, which reset every week and allowed half the
   account to go before anything stopped.
4. **Nothing limited a single source.** One wallet's losing trade could keep
   adding exposure while the rest of the account paid for it.
5. **Scoring did not ask whether a wallet could be copied at our size.** The
   wallet looked excellent on its own terms; its trades were simply too small
   to copy faithfully with our capital.

What changed, one fix per cause:

1. Entries are sized against a target position, so many small source fills
   add up to one proportional position instead of many minimum orders.
2. Exposure caps limit notional size, so leverage cannot inflate a copy.
3. Rolling 24 hour, 7 day, and drawdown-from-peak limits replace the weekly
   check.
4. A per-source loss limit stops entries from a source that is losing.
5. Scoring measures copyability at our size and caps wallets whose trades
   mostly fall below the minimum order.

Later reviews added two more lessons. Paper had drifted from live: it blocked
exits on price moves and lacked the loss limits, so paper results looked
different from what live would have done. Both engines now share one decision
module. And paper filled every order at the mid price, which hid how large
orders fare in a thin market; it now fills against the real order book.

## Position Sizing

Each of the ten copy sources gets 20% of the account: a 200% budget
(`trading_copy_max_total_allocation_pct`) split equally over
`trading_copy_top_wallet_count`. The shares add up to twice the account, so
the exposure caps below, not the shares, bound the total size. When many
sources are positioned at once, the total cap is reached first and later
entries are trimmed or skipped. For a source, the **target position** is:

```text
target notional = allocation / source account value * source position notional
```

The source position is read from the fill itself (`startPosition` plus the
fill size), so the target follows the source exactly even if we missed earlier
fills.

Each entry buys only the gap between the target and our current position:

- A new position opens with at least one minimum order (10 USD).
- Later adds round to the nearest minimum order: a gap of at least half a
  minimum order buys one, a smaller gap is skipped and catches up later.
- An add never buys more than its own proportional fill plus one minimum
  order, so exposure missed earlier is not chased at a worse price.
- An add without a known source position (`source_position_unknown`) is
  skipped rather than guessed.

Example with a 1,000 USD account: each source gets 200 USD. A source with
2,000,000 USD of equity that builds a 300,000 USD position (15% of its equity)
in 60 fills of 5,000 USD has a target of 30 USD for us, and our position stays
within one minimum order of it. The old fill-by-fill rule would have bought 60
minimum orders, 600 USD. A 10% price move then costs the source 1.5% and us
about 3 USD instead of 60 USD.

Exits follow the source's close ratio: if the source closes 40% of its
position, we close 40% of ours. When the source has no position left, we close
ours completely. Partial closes below the minimum order are combined with the
next ones until they add up to a valid order.

## Exposure Caps

Caps apply to notional size, not margin:

| Cap | Default | Setting |
| --- | --- | --- |
| One source | 3x its allocation | `trading_copy_max_source_exposure_multiple` |
| Whole account | 3x account equity | `trading_copy_max_total_exposure_multiple` |

An opening that only partly fits is trimmed to the remaining room if that room
still holds a minimum order. Rounding never pushes an order past a cap. Live
re-checks both caps under the account lock just before sending, counting
attributed positions, exchange positions, and open orders.

## Entry Filters

An entry is skipped when:

- it was first observed more than 20 seconds after the source traded
  (`trading_copy_max_entry_age_seconds`), so restarts and recoveries never open
  stale positions;
- the price has moved more than 30 bps against us since the source's fill
  (`trading_copy_max_price_drift_bps`);
- the fill adds to a source position that we never copied, for example one
  the source opened before we started following it;
- we hold the opposite side in that market;
- another source already holds a copied position in that coin on the same
  account and the two cannot share it. Hyperliquid keeps one net position and
  one leverage and margin setting per account and coin. Live gives each coin
  to one source until that source is flat. Paper lets sources share a coin on
  the same side in cross margin, see Paper Parity;
- the source's allocation is inactive, for example a retained source that has
  dropped out of the top ten.

None of these filters apply to exits.

## Account Loss Limits

| Limit | Default | Setting |
| --- | --- | --- |
| Loss over a rolling 24 hours | 10% | `live_trading_max_daily_loss_pct` |
| Loss over a rolling 7 days | 20% | `live_trading_max_weekly_loss_pct` |
| Drawdown from the peak | 25% | `live_trading_max_drawdown_pct` |

Live accounts measure these on the time-weighted performance index, so
deposits and withdrawals never look like gains or losses. Paper accounts have
no external cash flows and measure them on equity snapshots taken every five
minutes, with the drawdown measured from the highest equity since the account
was last reset.

Both engines check the limits at the entry gate. When a limit is hit, the
account becomes **exit-only**: it keeps reducing and closing positions but
opens nothing new. Live also cancels unsent entry orders
and records a critical risk event. The account stays exit-only until someone
starts it again, so a breach always gets a human look.

## Source Loss Limit

New entries from a source stop when that source has lost 5% of account equity
over the last 7 days (`live_trading_max_source_loss_pct`), counting realized
net PnL from its closed copies plus the unrealized PnL of its open copies. Its
exits are still copied. The block lifts by itself as the losses age out of the
7 day window or the open positions recover.

## Scoring as a Risk Control

Scoring decides which wallets we copy, so it is the first line of defense:

- Copyability is measured at our size: the share of a wallet's trades that
  reach the minimum order after scaling to our allocation, and how far above
  the minimum the typical copied trade lands. When fewer than half of its
  trades reach the minimum, the wallet's score is capped, down to zero.
- Averaging down into losing positions is penalized.
- Return and drawdown come from the wallet's own equity curve, including
  unrealized losses, so a wallet that hides losses in open positions does not
  look safe.
- Wallets with few trades are capped, and scores are pulled toward neutral
  until 40 closed trades, so a short lucky streak cannot reach the top.
- A wallet with a large open drawdown right now is penalized and capped, and
  allocation only selects wallets whose current drawdown state is `ok`.

## Execution Safety

- `LIVE_TRADING_ENABLED=false` is the default and the only global switch.
- In production the signing key must be a Hyperliquid API wallet, which can
  trade but cannot withdraw. A main wallet key on the server would let anyone
  who reads it empty the account, so live accounts with such a key cannot start
  and are made exit-only.
- Live accounts start disabled and need a complete exchange reconciliation no
  older than 90 seconds before an entry can be sent.
- Orders are IOC limit orders 20 bps beyond the execution mid
  (`live_trading_limit_slippage_bps`), so an order never fills far from the
  expected price; it fills partly or not at all instead.
- An entry intent expires 30 seconds after it is built and is never renewed.
- At most 50 orders per minute per account.

## Paper Parity

Paper accounts apply all of the rules above through the same code as live,
and add the parts of reality that paper would otherwise skip:

- orders fill against the real order book within the live limit, so large
  orders in thin markets fill partly or not at all;
- open positions pay and receive hourly funding;
- positions are liquidated with Hyperliquid's isolated and cross margin rules;
- the account loss limits and the source loss limit make paper accounts
  exit-only exactly like live ones.

A strategy that only works on paper because paper ignores one of these is not
a strategy we want live.

One difference is deliberate. Reserving a coin for one source can block every
other source from BTC or ETH for weeks, so paper tests sharing a coin before
live gets it. Sources share only on the same side and in cross margin, where
the positions add up to one exchange position and the single leverage setting
does not change size, PnL, or liquidation. The source that opened the coin
sets the leverage. An opposite side or isolated margin still blocks the entry.
Live keeps one source per coin until the paper results justify the change.

## What the Limits Do Not Cover

- **Correlated sources.** Ten sources that go long coins that move together,
  or on paper the same coin, count as ten separate allocations. The total
  exposure cap bounds the sum, but there is no per-coin or correlation limit
  across sources.
- **Gaps.** Limits act on the next check. A price gap or an exchange outage can
  move an account past a limit before it trips.
- **Leverage.** The copy uses the source's leverage setting. Exposure caps
  bound the size, but a highly leveraged copy can still be liquidated on a move
  the account could have survived at lower leverage.
- **Strategy decay.** A wallet that stops being good is caught only as its
  score falls or its losses reach the source limit.

Review these risks before raising capital or loosening any default.
