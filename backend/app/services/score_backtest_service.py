"""Backtest whether the wallet score predicts later wallet performance.

For each cutoff date the wallets are ranked with information available at that
time only: the stored daily score snapshot when one exists, otherwise a
recompute from reconstructed trades that closed before the cutoff. Each wallet
is then followed over the horizon, and the top-ranked wallets are compared with
random selections from the same eligible wallets.

The outcome is the wallet's realized net PnL over the horizon divided by its
equity at the cutoff, the return a proportional copy would follow before fees,
delay, and minimum-order effects.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import SourceTrade, WalletScoreSnapshot
from app.services.source_trade_reconstruction_service import (
    ReconstructedSourceTrade,
    ReconstructedWalletTrades,
)
from app.services.wallet_equity_service import load_wallet_equity_metrics
from app.services.wallet_score_service import (
    WalletScoreMetrics,
    base_metrics_from_row,
    calculate_wallet_score,
    metric_with_copy_scale,
    metrics_with_reconstructed_trades,
)

ZERO = Decimal("0")
ONE = Decimal("1")
DAY_MS = 86_400_000
RATIO_QUANT = Decimal("0.000001")
# A snapshot this close before the cutoff still describes the wallet at the cutoff.
SNAPSHOT_MAX_AGE_DAYS = 7

BacktestSource = Literal["auto", "snapshots", "recompute"]
EquityBasis = Literal["snapshot", "current_equity", "missing"]


@dataclass(frozen=True)
class BacktestConfig:
    horizon_days: int = 14
    step_days: int = 7
    top_k: int = 5
    random_samples: int = 1000
    seed: int = 7
    start: date | None = None
    end: date | None = None
    source: BacktestSource = "auto"


@dataclass(frozen=True)
class WalletObservation:
    wallet_address: str
    score: Decimal
    forward_net_pnl_usd: Decimal
    forward_return: Decimal | None
    equity_basis: EquityBasis


@dataclass(frozen=True)
class CutoffResult:
    cutoff: date
    source: Literal["snapshots", "recompute"]
    eligible_wallets: int
    evaluated_wallets: int
    top_wallets: list[str]
    top_mean_return: Decimal | None
    universe_mean_return: Decimal | None
    random_percentile: Decimal | None
    top_hit_rate: Decimal | None
    universe_hit_rate: Decimal | None
    rank_correlation: Decimal | None


@dataclass(frozen=True)
class BacktestReport:
    config: BacktestConfig
    cutoffs: list[CutoffResult] = field(default_factory=list)
    mean_top_return: Decimal | None = None
    mean_universe_return: Decimal | None = None
    mean_random_percentile: Decimal | None = None
    mean_rank_correlation: Decimal | None = None
    cutoffs_beating_median_random: int = 0


@dataclass(frozen=True)
class ScoreSnapshotPoint:
    score: Decimal
    payload: dict[str, Any]


def evaluate_cutoff(
    observations: Sequence[WalletObservation],
    *,
    cutoff: date,
    source: Literal["snapshots", "recompute"],
    top_k: int,
    random_samples: int,
    rng: random.Random,
) -> CutoffResult:
    """Compare the top-ranked wallets with random picks among evaluable wallets."""

    ranked = sorted(observations, key=lambda item: (-item.score, item.wallet_address))
    evaluable = [item for item in ranked if item.forward_return is not None]
    top = evaluable[:top_k]
    top_returns = [item.forward_return for item in top if item.forward_return is not None]
    universe_returns = [
        item.forward_return for item in evaluable if item.forward_return is not None
    ]
    top_mean = mean(top_returns)
    random_percentile: Decimal | None = None
    if top_mean is not None and len(universe_returns) > top_k and random_samples > 0:
        random_means = [
            sum(rng.sample(universe_returns, top_k), ZERO) / Decimal(top_k)
            for _ in range(random_samples)
        ]
        below = sum(1 for value in random_means if value < top_mean)
        ties = sum(1 for value in random_means if value == top_mean)
        random_percentile = (
            (Decimal(below) + Decimal(ties) / Decimal(2)) / Decimal(random_samples)
        ).quantize(RATIO_QUANT)
    return CutoffResult(
        cutoff=cutoff,
        source=source,
        eligible_wallets=len(ranked),
        evaluated_wallets=len(evaluable),
        top_wallets=[item.wallet_address for item in top],
        top_mean_return=top_mean,
        universe_mean_return=mean(universe_returns),
        random_percentile=random_percentile,
        top_hit_rate=hit_rate(top_returns),
        universe_hit_rate=hit_rate(universe_returns),
        rank_correlation=spearman_correlation(
            [item.score for item in evaluable],
            [item.forward_return for item in evaluable if item.forward_return is not None],
        ),
    )


def summarize_backtest(config: BacktestConfig, cutoffs: list[CutoffResult]) -> BacktestReport:
    return BacktestReport(
        config=config,
        cutoffs=cutoffs,
        mean_top_return=mean(
            [item.top_mean_return for item in cutoffs if item.top_mean_return is not None]
        ),
        mean_universe_return=mean(
            [item.universe_mean_return for item in cutoffs if item.universe_mean_return is not None]
        ),
        mean_random_percentile=mean(
            [item.random_percentile for item in cutoffs if item.random_percentile is not None]
        ),
        mean_rank_correlation=mean(
            [item.rank_correlation for item in cutoffs if item.rank_correlation is not None]
        ),
        cutoffs_beating_median_random=sum(
            1
            for item in cutoffs
            if item.random_percentile is not None and item.random_percentile > Decimal("0.5")
        ),
    )


def mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, ZERO) / Decimal(len(values))).quantize(RATIO_QUANT)


def hit_rate(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (Decimal(sum(1 for value in values if value > ZERO)) / Decimal(len(values))).quantize(
        RATIO_QUANT
    )


def average_ranks(values: Sequence[Decimal]) -> list[Decimal]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [ZERO] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        rank = Decimal(position + end) / Decimal(2) + ONE
        for index in order[position : end + 1]:
            ranks[index] = rank
        position = end + 1
    return ranks


def spearman_correlation(
    scores: Sequence[Decimal],
    returns: Sequence[Decimal],
) -> Decimal | None:
    """Rank correlation with average ranks for ties; None without variation."""

    if len(scores) != len(returns) or len(scores) < 3:
        return None
    score_ranks = average_ranks(scores)
    return_ranks = average_ranks(returns)
    score_mean = sum(score_ranks, ZERO) / Decimal(len(score_ranks))
    return_mean = sum(return_ranks, ZERO) / Decimal(len(return_ranks))
    covariance = sum(
        (
            (score_rank - score_mean) * (return_rank - return_mean)
            for score_rank, return_rank in zip(score_ranks, return_ranks, strict=True)
        ),
        ZERO,
    )
    score_variance = sum(((rank - score_mean) ** 2 for rank in score_ranks), ZERO)
    return_variance = sum(((rank - return_mean) ** 2 for rank in return_ranks), ZERO)
    if score_variance <= ZERO or return_variance <= ZERO:
        return None
    return (covariance / (score_variance * return_variance).sqrt()).quantize(RATIO_QUANT)


def cutoff_dates(
    *,
    first_data_date: date,
    last_data_date: date,
    config: BacktestConfig,
    window_days: int,
) -> list[date]:
    """Cutoffs with a full scoring window before and a full horizon after."""

    start = max(first_data_date + timedelta(days=window_days), config.start or date.min)
    end = min(last_data_date - timedelta(days=config.horizon_days), config.end or date.max)
    dates: list[date] = []
    current = start
    step = max(config.step_days, 1)
    while current <= end:
        dates.append(current)
        current += timedelta(days=step)
    return dates


def cutoff_ms(cutoff: date) -> int:
    return int(datetime.combine(cutoff, time.min, tzinfo=UTC).timestamp() * 1000)


def recompute_score_metrics(
    wallet_address: str,
    trades: Sequence[ReconstructedSourceTrade],
    *,
    cutoff: date,
    settings: Settings,
) -> WalletScoreMetrics | None:
    """Score inputs from trades that closed inside the window before the cutoff."""

    end_ms = cutoff_ms(cutoff)
    window_start_ms = end_ms - settings.scoring_window_days * DAY_MS
    closed = sorted(
        (
            item
            for item in trades
            if item.status == "closed"
            and item.closed_at_ms is not None
            and window_start_ms <= item.closed_at_ms < end_ms
        ),
        key=lambda item: (item.closed_at_ms or 0, item.id),
    )
    if not closed:
        return None
    wallet_trades = ReconstructedWalletTrades(wallet_address=wallet_address)
    for item in closed:
        wallet_trades.record_closed_trade_item(
            item,
            start_24h_ms=end_ms - DAY_MS,
            start_7d_ms=end_ms - 7 * DAY_MS,
        )
    base = base_metrics_from_row(
        {
            "wallet_address": wallet_address,
            "fill_count": sum(item.entry_fill_count + item.close_fill_count for item in closed),
            "first_fill_time_ms": min(item.opened_at_ms for item in closed),
            "last_activity_time_ms": max(item.closed_at_ms or 0 for item in closed),
            "liquidation_fill_count": sum(item.liquidation_fill_count for item in closed),
            "liquidation_event_count": sum(1 for item in closed if item.has_liquidation),
            "liquidation_notional_usd": sum(
                (item.liquidation_notional_usd for item in closed),
                ZERO,
            ),
        }
    )
    return metrics_with_reconstructed_trades(base, wallet_trades, settings=settings)


def payload_decimal(payload: dict[str, Any], key: str) -> Decimal | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation, ValueError:
        return None


def snapshot_equity(payload: dict[str, Any]) -> dict[str, Any]:
    """Equity fields of a snapshot payload as score metric overrides."""

    status = str(payload.get("equityStatus") or "missing")
    if status != "ok":
        return {"equity_status": status}
    return {
        "equity_status": "ok",
        "equity_return_pct": payload_decimal(payload, "equityReturnPct"),
        "equity_max_drawdown_pct": payload_decimal(payload, "equityMaxDrawdownPct"),
        "equity_average_account_value_usd": payload_decimal(payload, "accountValueUsd"),
    }


def latest_snapshot_before(
    snapshots_by_date: dict[date, dict[str, ScoreSnapshotPoint]],
    *,
    wallet_address: str,
    cutoff: date,
) -> ScoreSnapshotPoint | None:
    for offset in range(SNAPSHOT_MAX_AGE_DAYS + 1):
        point = snapshots_by_date.get(cutoff - timedelta(days=offset), {}).get(wallet_address)
        if point is not None:
            return point
    return None


async def run_score_backtest(
    session: AsyncSession,
    *,
    settings: Settings,
    config: BacktestConfig,
) -> BacktestReport:
    trades_by_wallet = await load_backtest_trades(session)
    snapshots_by_date = await load_score_snapshots(session)
    current_equity = {
        address: metrics.average_account_value_usd
        for address, (metrics, _) in (await load_wallet_equity_metrics(session)).items()
        if metrics.is_ok and metrics.average_account_value_usd
    }
    data_dates = [
        datetime.fromtimestamp(item.closed_at_ms / 1000, tz=UTC).date()
        for items in trades_by_wallet.values()
        for item in items
        if item.closed_at_ms is not None
    ] + list(snapshots_by_date)
    if not data_dates:
        return BacktestReport(config=config)
    dates = cutoff_dates(
        first_data_date=min(data_dates),
        last_data_date=max(data_dates),
        config=config,
        window_days=0 if config.source == "snapshots" else settings.scoring_window_days,
    )
    rng = random.Random(config.seed)
    results: list[CutoffResult] = []
    for cutoff in dates:
        use_snapshots = config.source == "snapshots" or (
            config.source == "auto" and bool(snapshots_by_date.get(cutoff))
        )
        if use_snapshots:
            scores = {
                address: point.score for address, point in snapshots_by_date.get(cutoff, {}).items()
            }
        else:
            scores = recompute_scores(
                trades_by_wallet,
                snapshots_by_date,
                cutoff=cutoff,
                settings=settings,
            )
        observations = [
            wallet_observation(
                address,
                score,
                trades_by_wallet.get(address, ()),
                snapshots_by_date=snapshots_by_date,
                current_equity=current_equity,
                cutoff=cutoff,
                horizon_days=config.horizon_days,
            )
            for address, score in scores.items()
        ]
        if not observations:
            continue
        results.append(
            evaluate_cutoff(
                observations,
                cutoff=cutoff,
                source="snapshots" if use_snapshots else "recompute",
                top_k=config.top_k,
                random_samples=config.random_samples,
                rng=rng,
            )
        )
    return summarize_backtest(config, results)


def recompute_scores(
    trades_by_wallet: dict[str, list[ReconstructedSourceTrade]],
    snapshots_by_date: dict[date, dict[str, ScoreSnapshotPoint]],
    *,
    cutoff: date,
    settings: Settings,
) -> dict[str, Decimal]:
    now = datetime.combine(cutoff, time.min, tzinfo=UTC)
    scores: dict[str, Decimal] = {}
    for address, trades in trades_by_wallet.items():
        metrics = recompute_score_metrics(address, trades, cutoff=cutoff, settings=settings)
        if metrics is None:
            continue
        snapshot = latest_snapshot_before(
            snapshots_by_date,
            wallet_address=address,
            cutoff=cutoff,
        )
        if snapshot is not None:
            metrics = replace(metrics, **snapshot_equity(snapshot.payload))
        # Historical live equity is unknown, so copy size uses the configured capital.
        metrics = metric_with_copy_scale(
            metrics,
            reference_equity_usd=settings.scoring_copyability_reference_equity_usd,
            settings=settings,
        )
        scores[address] = calculate_wallet_score(metrics, settings=settings, now=now).score
    return scores


def wallet_observation(
    wallet_address: str,
    score: Decimal,
    trades: Sequence[ReconstructedSourceTrade],
    *,
    snapshots_by_date: dict[date, dict[str, ScoreSnapshotPoint]],
    current_equity: dict[str, Decimal],
    cutoff: date,
    horizon_days: int,
) -> WalletObservation:
    start_ms = cutoff_ms(cutoff)
    end_ms = start_ms + horizon_days * DAY_MS
    forward_net_pnl = sum(
        (
            item.net_pnl_usd
            for item in trades
            if item.status == "closed"
            and item.closed_at_ms is not None
            and start_ms <= item.closed_at_ms < end_ms
        ),
        ZERO,
    )
    snapshot = latest_snapshot_before(
        snapshots_by_date,
        wallet_address=wallet_address,
        cutoff=cutoff,
    )
    snapshot_value = (
        payload_decimal(snapshot.payload, "accountValueUsd") if snapshot is not None else None
    )
    equity: Decimal | None
    basis: EquityBasis
    if snapshot_value is not None and snapshot_value > ZERO:
        equity, basis = snapshot_value, "snapshot"
    elif current_equity.get(wallet_address):
        equity, basis = current_equity[wallet_address], "current_equity"
    else:
        equity, basis = None, "missing"
    return WalletObservation(
        wallet_address=wallet_address,
        score=score,
        forward_net_pnl_usd=forward_net_pnl,
        forward_return=(forward_net_pnl / equity).quantize(RATIO_QUANT) if equity else None,
        equity_basis=basis,
    )


async def load_backtest_trades(session: AsyncSession) -> dict[str, list[ReconstructedSourceTrade]]:
    """Closed reconstructed trades of every wallet, including archived wallets."""

    result = await session.execute(
        select(SourceTrade)
        .where(SourceTrade.status == "closed")
        .order_by(SourceTrade.wallet_address, SourceTrade.closed_at_ms)
    )
    trades: dict[str, list[ReconstructedSourceTrade]] = {}
    for row in result.scalars().all():
        trades.setdefault(row.wallet_address, []).append(
            ReconstructedSourceTrade(
                id=row.trade_key,
                wallet_address=row.wallet_address,
                coin=row.coin,
                side=row.side,
                status=row.status,
                opened_at_ms=row.opened_at_ms,
                closed_at_ms=row.closed_at_ms,
                duration_ms=row.duration_ms,
                entry_size=row.entry_size,
                closed_size=row.closed_size,
                remaining_size=row.remaining_size,
                entry_notional_usd=row.entry_notional_usd,
                close_notional_usd=row.close_notional_usd,
                average_entry_price=row.average_entry_price,
                average_exit_price=row.average_exit_price,
                realized_pnl_usd=row.realized_pnl_usd,
                fee_usd=row.fee_usd,
                net_pnl_usd=row.net_pnl_usd,
                entry_fill_count=row.entry_fill_count,
                close_fill_count=row.close_fill_count,
                has_liquidation=row.has_liquidation,
                liquidation_fill_count=row.liquidation_fill_count,
                liquidation_notional_usd=row.liquidation_notional_usd,
                underwater_add_notional_usd=row.underwater_add_notional_usd,
            )
        )
    return trades


async def load_score_snapshots(
    session: AsyncSession,
) -> dict[date, dict[str, ScoreSnapshotPoint]]:
    result = await session.execute(select(WalletScoreSnapshot))
    snapshots: dict[date, dict[str, ScoreSnapshotPoint]] = {}
    for row in result.scalars().all():
        snapshots.setdefault(row.snapshot_date, {})[row.wallet_address] = ScoreSnapshotPoint(
            score=row.score,
            payload=row.score_payload or {},
        )
    return snapshots
