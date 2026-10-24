"""Per-account paper performance: round trip trades, equity history, and source PnL.

Statistics count only activity after the account's last balance reset, because a
reset zeroes the account totals but keeps the fill history.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    PaperAccountEquitySnapshot,
    PaperCopyFill,
    PaperFundingPayment,
    PaperTradingAccount,
    WalletFill,
)

ZERO = Decimal("0")
PAPER_TRADE_END_ACTIONS = ("close", "flip_close", "liquidation")
# The equity curve keeps the low and high of each bucket, so drops stay visible.
EQUITY_CURVE_MAX_POINTS = 600

# One round trip per position lifecycle: an open or flip_open starts it, adds and
# reduces belong to it, and a close, flip_close, or liquidation ends it. Fees of
# every fill and the funding paid while it was open count toward its net PnL.
ROUND_TRIPS_SQL = """
with lifecycle_fills as (
    select
        fill.id,
        fill.source_wallet,
        fill.coin,
        fill.side,
        fill.action,
        fill.size,
        fill.notional_usd,
        fill.fee_usd,
        fill.realized_pnl_usd,
        fill.filled_at,
        sum(case when fill.action in ('open', 'flip_open') then 1 else 0 end) over (
            partition by fill.source_wallet, fill.coin, fill.side
            order by fill.filled_at, fill.created_at, fill.sequence_index
            rows between unbounded preceding and current row
        ) as lifecycle
    from paper_copy_fills as fill
    where fill.account_key = :account_key
      and fill.action in (
          'open', 'flip_open', 'add', 'reduce', 'close', 'flip_close', 'liquidation'
      )
      {since_clause}
),
round_trips as (
    select
        source_wallet,
        coin,
        side,
        min(filled_at) filter (where action in ('open', 'flip_open')) as opened_at,
        max(filled_at) filter (
            where action in ('close', 'flip_close', 'liquidation')
        ) as closed_at,
        (array_agg(id order by filled_at desc) filter (
            where action in ('close', 'flip_close', 'liquidation')
        ))[1] as end_fill_id,
        coalesce(sum(size) filter (where action in ('open', 'flip_open', 'add')), 0)
            as entry_size,
        coalesce(sum(notional_usd) filter (where action in ('open', 'flip_open', 'add')), 0)
            as entry_notional_usd,
        coalesce(sum(size) filter (where action not in ('open', 'flip_open', 'add')), 0)
            as exit_size,
        coalesce(sum(notional_usd) filter (where action not in ('open', 'flip_open', 'add')), 0)
            as exit_notional_usd,
        coalesce(sum(fee_usd), 0) as fee_usd,
        coalesce(sum(realized_pnl_usd), 0) as realized_pnl_usd,
        count(*) filter (where action in ('open', 'flip_open', 'add')) as open_fill_count,
        count(*) filter (where action not in ('open', 'flip_open', 'add')) as close_fill_count
    from lifecycle_fills
    group by source_wallet, coin, side, lifecycle
    having count(*) filter (where action in ('close', 'flip_close', 'liquidation')) > 0
)
select
    round_trip.source_wallet,
    round_trip.coin,
    round_trip.side,
    round_trip.opened_at,
    round_trip.closed_at,
    round_trip.end_fill_id,
    round_trip.entry_size,
    round_trip.entry_notional_usd,
    round_trip.exit_size,
    round_trip.exit_notional_usd,
    round_trip.fee_usd,
    round_trip.realized_pnl_usd,
    round_trip.open_fill_count,
    round_trip.close_fill_count,
    end_fill.source_fill_id,
    end_fill.action as close_type,
    end_fill.leverage,
    end_fill.margin_usd,
    end_fill.created_at,
    coalesce(funding.funding_usd, 0) as funding_usd
from round_trips as round_trip
join paper_copy_fills as end_fill on end_fill.id = round_trip.end_fill_id
left join lateral (
    select sum(payment.amount_usd) as funding_usd
    from paper_funding_payments as payment
    where payment.account_key = :account_key
      and payment.source_wallet = round_trip.source_wallet
      and payment.coin = round_trip.coin
      and payment.side = round_trip.side
      and payment.funding_time >= coalesce(round_trip.opened_at, round_trip.closed_at)
      and payment.funding_time <= round_trip.closed_at
) as funding on true
order by round_trip.closed_at desc, end_fill.created_at desc
"""


@dataclass(frozen=True)
class PaperRoundTrip:
    end_fill_id: Any
    account_key: str
    source_wallet: str
    source_fill_id: str
    coin: str
    side: str
    close_type: str
    opened_at: datetime | None
    closed_at: datetime
    entry_size: Decimal
    entry_notional_usd: Decimal
    exit_size: Decimal
    exit_notional_usd: Decimal
    fee_usd: Decimal
    funding_usd: Decimal
    realized_pnl_usd: Decimal
    leverage: Decimal | None
    margin_usd: Decimal | None
    open_fill_count: int
    close_fill_count: int
    created_at: datetime

    @property
    def net_pnl_usd(self) -> Decimal:
        return self.realized_pnl_usd - self.fee_usd + self.funding_usd


@dataclass(frozen=True)
class PaperTradeStats:
    """Sums and streaks of round trip net PnL; ratios are derived by the client."""

    trade_count: int
    win_count: int
    loss_count: int
    gross_profit_usd: Decimal
    gross_loss_usd: Decimal
    net_pnl_usd: Decimal
    best_trade_usd: Decimal
    worst_trade_usd: Decimal
    longest_win_streak: int
    longest_loss_streak: int
    current_streak_count: int
    current_streak_kind: str | None


@dataclass(frozen=True)
class EquityDrawdown:
    max_drawdown_usd: Decimal
    max_drawdown_pct: Decimal | None
    peak_at: datetime | None
    trough_at: datetime | None


def paper_stats_since(account: PaperTradingAccount) -> datetime | None:
    return account.balance_reset_at


async def load_paper_round_trips(
    session: AsyncSession,
    *,
    account_key: str,
    since: datetime | None,
) -> list[PaperRoundTrip]:
    """Every completed round trip of one account after `since`, newest first."""

    since_clause = "and fill.filled_at >= :since" if since is not None else ""
    params: dict[str, Any] = {"account_key": account_key}
    if since is not None:
        params["since"] = since
    result = await session.execute(text(ROUND_TRIPS_SQL.format(since_clause=since_clause)), params)
    return [
        PaperRoundTrip(
            end_fill_id=row["end_fill_id"],
            account_key=account_key,
            source_wallet=str(row["source_wallet"]),
            source_fill_id=str(row["source_fill_id"]),
            coin=str(row["coin"]),
            side=str(row["side"]),
            close_type=str(row["close_type"]),
            opened_at=row["opened_at"],
            closed_at=row["closed_at"],
            entry_size=Decimal(row["entry_size"]),
            entry_notional_usd=Decimal(row["entry_notional_usd"]),
            exit_size=Decimal(row["exit_size"]),
            exit_notional_usd=Decimal(row["exit_notional_usd"]),
            fee_usd=Decimal(row["fee_usd"]),
            funding_usd=Decimal(row["funding_usd"]),
            realized_pnl_usd=Decimal(row["realized_pnl_usd"]),
            leverage=row["leverage"],
            margin_usd=row["margin_usd"],
            open_fill_count=int(row["open_fill_count"]),
            close_fill_count=int(row["close_fill_count"]),
            created_at=row["created_at"],
        )
        for row in result.mappings().all()
    ]


def paper_trade_stats(trades: Iterable[PaperRoundTrip]) -> PaperTradeStats:
    """Win and loss sums and streaks over round trips in close order."""

    ordered = sorted(trades, key=lambda trade: (trade.closed_at, trade.created_at))
    values = [trade.net_pnl_usd for trade in ordered]
    wins = [value for value in values if value > ZERO]
    losses = [value for value in values if value < ZERO]
    direction = 0
    streak = 0
    longest_win_streak = 0
    longest_loss_streak = 0
    for value in values:
        current = 1 if value > ZERO else -1 if value < ZERO else 0
        if current == 0:
            continue
        streak = streak + 1 if current == direction else 1
        direction = current
        if current > 0:
            longest_win_streak = max(longest_win_streak, streak)
        else:
            longest_loss_streak = max(longest_loss_streak, streak)
    return PaperTradeStats(
        trade_count=len(values),
        win_count=len(wins),
        loss_count=len(losses),
        gross_profit_usd=sum(wins, ZERO),
        gross_loss_usd=-sum(losses, ZERO),
        net_pnl_usd=sum(values, ZERO),
        best_trade_usd=max(values, default=ZERO),
        worst_trade_usd=min(values, default=ZERO),
        longest_win_streak=longest_win_streak,
        longest_loss_streak=longest_loss_streak,
        current_streak_count=streak if direction != 0 else 0,
        current_streak_kind="win" if direction > 0 else "loss" if direction < 0 else None,
    )


async def load_paper_equity_snapshots(
    session: AsyncSession,
    *,
    account_key: str,
) -> list[tuple[datetime, Decimal]]:
    result = await session.execute(
        select(PaperAccountEquitySnapshot.recorded_at, PaperAccountEquitySnapshot.equity_usd)
        .where(PaperAccountEquitySnapshot.account_key == account_key)
        .order_by(PaperAccountEquitySnapshot.recorded_at.asc())
    )
    return [(row.recorded_at, Decimal(row.equity_usd)) for row in result.all()]


def equity_max_drawdown(points: Sequence[tuple[datetime, Decimal]]) -> EquityDrawdown:
    """Largest fall from a running peak, measured in percent of that peak."""

    peak: Decimal | None = None
    peak_at: datetime | None = None
    best = EquityDrawdown(ZERO, None, None, None)
    for recorded_at, equity in points:
        if peak is None or equity > peak:
            peak, peak_at = equity, recorded_at
            continue
        drawdown = peak - equity
        if drawdown <= ZERO or peak <= ZERO:
            continue
        drawdown_pct = drawdown / peak
        if best.max_drawdown_pct is None or drawdown_pct > best.max_drawdown_pct:
            best = EquityDrawdown(drawdown, drawdown_pct, peak_at, recorded_at)
    return best


def downsample_equity(
    points: Sequence[tuple[datetime, Decimal]],
    *,
    max_points: int = EQUITY_CURVE_MAX_POINTS,
) -> list[tuple[datetime, Decimal]]:
    """Keeps the first and last point and the low and high of each bucket."""

    if len(points) <= max_points:
        return list(points)
    bucket_count = max(1, (max_points - 2) // 2)
    bucket_size = (len(points) - 2) / bucket_count
    kept = {0, len(points) - 1}
    for bucket in range(bucket_count):
        start = 1 + int(bucket * bucket_size)
        end = 1 + int((bucket + 1) * bucket_size)
        indexes = range(start, min(end, len(points) - 1))
        if not indexes:
            continue
        kept.add(min(indexes, key=lambda index: points[index][1]))
        kept.add(max(indexes, key=lambda index: points[index][1]))
    return [points[index] for index in sorted(kept)]


async def load_liquidation_source_fill_ids(
    session: AsyncSession,
    *,
    pairs: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Source fills, by wallet and fill id, that were liquidations of the source."""

    if not pairs:
        return set()
    result = await session.execute(
        select(WalletFill.wallet_address, WalletFill.external_fill_id).where(
            tuple_(WalletFill.wallet_address, WalletFill.external_fill_id).in_(pairs),
            WalletFill.raw_json.has_key("liquidation"),
        )
    )
    return {(str(row.wallet_address).lower(), str(row.external_fill_id)) for row in result.all()}


def paper_round_trip_read(
    trade: PaperRoundTrip,
    *,
    source_label: str | None,
    is_source_liquidation: bool,
) -> dict[str, Any]:
    return {
        "id": trade.end_fill_id,
        "account_key": trade.account_key,
        "source_wallet": trade.source_wallet,
        "source_label": source_label,
        "source_fill_id": trade.source_fill_id,
        "coin": trade.coin,
        "close_type": trade.close_type,
        "side": trade.side,
        "entry_price": (
            trade.entry_notional_usd / trade.entry_size if trade.entry_size > ZERO else None
        ),
        "exit_price": (
            trade.exit_notional_usd / trade.exit_size if trade.exit_size > ZERO else None
        ),
        "size": trade.exit_size,
        "entry_notional_usd": trade.entry_notional_usd,
        "notional_usd": trade.exit_notional_usd,
        "leverage": trade.leverage,
        "margin_usd": trade.margin_usd,
        "fee_usd": trade.fee_usd,
        "funding_usd": trade.funding_usd,
        "realized_pnl_usd": trade.realized_pnl_usd,
        "net_pnl_usd": trade.net_pnl_usd,
        "open_fill_count": trade.open_fill_count,
        "close_fill_count": trade.close_fill_count,
        "is_source_liquidation": is_source_liquidation,
        "is_paper_liquidation": trade.close_type == "liquidation",
        "opened_at": trade.opened_at,
        "closed_at": trade.closed_at,
        "duration_ms": (
            max(0, int((trade.closed_at - trade.opened_at).total_seconds() * 1000))
            if trade.opened_at is not None
            else None
        ),
        "created_at": trade.created_at,
    }


async def build_paper_account_performance(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    settings: Settings,
    round_trips: list[PaperRoundTrip],
    source_labels: dict[str, str],
    closed_trade_limit: int,
) -> dict[str, Any]:
    """Equity curve, equity drawdown, and round trip statistics for one account.

    `round_trips` are all round trips since the last reset, newest first. The
    statistics cover all of them; only the newest `closed_trade_limit` are listed.
    """

    snapshots = await load_paper_equity_snapshots(session, account_key=account.key)
    drawdown = equity_max_drawdown(snapshots)
    snapshot_peak = max((equity for _, equity in snapshots), default=None)
    peak_candidates = [
        value for value in (account.peak_equity_usd, snapshot_peak) if value is not None
    ]
    stats = paper_trade_stats(round_trips)
    recent_trades = round_trips[:closed_trade_limit]
    liquidation_ids = await load_liquidation_source_fill_ids(
        session,
        pairs={
            (trade.source_wallet.lower(), trade.source_fill_id)
            for trade in recent_trades
            if trade.close_type != "liquidation"
        },
    )
    return {
        "account_key": account.key,
        "starting_balance_usd": account.starting_balance_usd,
        "stats_started_at": paper_stats_since(account) or account.created_at,
        "snapshot_interval_seconds": settings.paper_copy_equity_snapshot_seconds,
        "equity_points": [
            {"recorded_at": recorded_at, "equity_usd": equity}
            for recorded_at, equity in downsample_equity(snapshots)
        ],
        "equity_snapshot_count": len(snapshots),
        "peak_equity_usd": max(peak_candidates) if peak_candidates else None,
        "max_drawdown_usd": drawdown.max_drawdown_usd,
        "max_drawdown_pct": drawdown.max_drawdown_pct,
        "max_drawdown_peak_at": drawdown.peak_at,
        "max_drawdown_trough_at": drawdown.trough_at,
        "trade_stats": {
            "trade_count": stats.trade_count,
            "win_count": stats.win_count,
            "loss_count": stats.loss_count,
            "gross_profit_usd": stats.gross_profit_usd,
            "gross_loss_usd": stats.gross_loss_usd,
            "net_pnl_usd": stats.net_pnl_usd,
            "best_trade_usd": stats.best_trade_usd,
            "worst_trade_usd": stats.worst_trade_usd,
            "longest_win_streak": stats.longest_win_streak,
            "longest_loss_streak": stats.longest_loss_streak,
            "current_streak_count": stats.current_streak_count,
            "current_streak_kind": stats.current_streak_kind,
        },
        "closed_trades": [
            paper_round_trip_read(
                trade,
                source_label=source_labels.get(trade.source_wallet.lower()),
                is_source_liquidation=(trade.source_wallet.lower(), trade.source_fill_id)
                in liquidation_ids,
            )
            for trade in recent_trades
        ],
        "updated_at": datetime.now(UTC),
    }


async def load_paper_source_performance_rows(session: AsyncSession) -> list[dict[str, Any]]:
    """Copy activity per account and source after each account's last reset."""

    fill_reset_filter = or_(
        PaperTradingAccount.balance_reset_at.is_(None),
        PaperCopyFill.filled_at >= PaperTradingAccount.balance_reset_at,
    )
    fill_result = await session.execute(
        select(
            PaperCopyFill.account_key,
            PaperCopyFill.source_wallet,
            func.count(PaperCopyFill.id)
            .filter(PaperCopyFill.action != "skip")
            .label("copied_fill_count"),
            func.count(PaperCopyFill.id)
            .filter(PaperCopyFill.action == "skip")
            .label("skipped_fill_count"),
            func.count(PaperCopyFill.id)
            .filter(PaperCopyFill.action.in_(PAPER_TRADE_END_ACTIONS))
            .label("closed_trade_count"),
            func.coalesce(func.sum(PaperCopyFill.realized_pnl_usd), ZERO).label("realized_pnl_usd"),
            func.coalesce(func.sum(PaperCopyFill.fee_usd), ZERO).label("fee_usd"),
            func.max(PaperCopyFill.filled_at).label("last_fill_at"),
        )
        .join(PaperTradingAccount, PaperTradingAccount.key == PaperCopyFill.account_key)
        .where(fill_reset_filter)
        .group_by(PaperCopyFill.account_key, PaperCopyFill.source_wallet)
    )
    funding_reset_filter = or_(
        PaperTradingAccount.balance_reset_at.is_(None),
        PaperFundingPayment.funding_time >= PaperTradingAccount.balance_reset_at,
    )
    funding_result = await session.execute(
        select(
            PaperFundingPayment.account_key,
            PaperFundingPayment.source_wallet,
            func.coalesce(func.sum(PaperFundingPayment.amount_usd), ZERO).label("funding_usd"),
        )
        .join(PaperTradingAccount, PaperTradingAccount.key == PaperFundingPayment.account_key)
        .where(funding_reset_filter)
        .group_by(PaperFundingPayment.account_key, PaperFundingPayment.source_wallet)
    )

    rows: dict[tuple[str, str], dict[str, Any]] = {}

    def row_for(account_key: str, source_wallet: str) -> dict[str, Any]:
        key = (account_key, source_wallet.lower())
        if key not in rows:
            rows[key] = {
                "account_key": account_key,
                "source_wallet": source_wallet.lower(),
                "copied_fill_count": 0,
                "skipped_fill_count": 0,
                "closed_trade_count": 0,
                "realized_pnl_usd": ZERO,
                "fee_usd": ZERO,
                "funding_usd": ZERO,
                "last_fill_at": None,
            }
        return rows[key]

    for fill_row in fill_result.mappings().all():
        if not fill_row["source_wallet"]:
            continue
        row = row_for(str(fill_row["account_key"]), str(fill_row["source_wallet"]))
        row["copied_fill_count"] += int(fill_row["copied_fill_count"] or 0)
        row["skipped_fill_count"] += int(fill_row["skipped_fill_count"] or 0)
        row["closed_trade_count"] += int(fill_row["closed_trade_count"] or 0)
        row["realized_pnl_usd"] += Decimal(fill_row["realized_pnl_usd"] or ZERO)
        row["fee_usd"] += Decimal(fill_row["fee_usd"] or ZERO)
        last_fill_at = fill_row["last_fill_at"]
        if last_fill_at is not None and (
            row["last_fill_at"] is None or last_fill_at > row["last_fill_at"]
        ):
            row["last_fill_at"] = last_fill_at
    for funding_row in funding_result.mappings().all():
        if not funding_row["source_wallet"]:
            continue
        row = row_for(str(funding_row["account_key"]), str(funding_row["source_wallet"]))
        row["funding_usd"] += Decimal(funding_row["funding_usd"] or ZERO)
    return list(rows.values())
