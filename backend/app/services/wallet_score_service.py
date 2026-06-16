from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import WalletScore
from app.schemas.score import (
    WalletScoreDetailResponse,
    WalletScoreListResponse,
    WalletScorePenaltyItem,
    WalletScoreRunResponse,
)
from app.schemas.wallet import normalize_wallet_address
from app.services.operation_status_service import (
    mark_operation_failed,
    mark_operation_started,
    mark_operation_succeeded,
)
from app.services.source_trade_reconstruction_service import (
    ReconstructedWalletTrades,
    reconstruct_wallet_trades,
)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
SCORE_QUANT = Decimal("0.01")
RATIO_QUANT = Decimal("0.0001")


class WalletScoreDetailNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class WalletScoreMetrics:
    wallet_address: str
    fill_count: int
    trade_count: int
    ignored_fill_count: int
    open_trade_count: int
    unique_coin_count: int
    active_days: int
    total_notional_usd: Decimal
    average_trade_notional_usd: Decimal
    total_pnl_usd: Decimal
    total_fee_usd: Decimal
    net_pnl_usd: Decimal
    gross_profit_usd: Decimal
    gross_loss_usd: Decimal
    profitable_trade_count: int
    losing_trade_count: int
    liquidation_fill_count: int
    liquidation_event_count: int
    max_coin_notional_usd: Decimal
    max_drawdown_usd: Decimal
    first_trade_time_ms: int | None
    last_trade_time_ms: int | None
    trades_24h: int
    notional_24h: Decimal
    net_pnl_24h: Decimal
    trades_7d: int
    notional_7d: Decimal
    net_pnl_7d: Decimal

    @property
    def liquidation_count(self) -> int:
        return self.liquidation_event_count


@dataclass(frozen=True)
class WalletScoreBreakdown:
    score: Decimal
    pnl_score: Decimal
    copyability_score: Decimal
    risk_score: Decimal
    consistency_score: Decimal
    recency_score: Decimal
    penalty_score: Decimal
    copyable_pnl_usd: Decimal
    win_rate: Decimal | None
    profit_factor: Decimal | None
    max_drawdown_pct: Decimal | None
    trade_count: int
    last_24h_score: Decimal | None
    last_7d_score: Decimal | None
    last_30d_score: Decimal | None


async def recalculate_wallet_scores(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    include_disabled: bool = False,
) -> WalletScoreRunResponse:
    resolved_settings = settings or get_settings()
    payload = {
        "windowDays": resolved_settings.scoring_window_days,
        "includeDisabled": include_disabled,
    }
    await mark_operation_started(session, key="wallet_scoring", payload=payload)
    try:
        result = await _recalculate_wallet_scores(
            session,
            settings=resolved_settings,
            include_disabled=include_disabled,
        )
    except Exception as exc:
        await session.rollback()
        await mark_operation_failed(
            session,
            key="wallet_scoring",
            error=str(exc) or exc.__class__.__name__,
            payload=payload,
        )
        raise

    await mark_operation_succeeded(
        session,
        key="wallet_scoring",
        payload={
            **payload,
            "totalWallets": result.total_wallets,
            "scoredWallets": result.scored_wallets,
            "skippedWallets": result.skipped_wallets,
            "minFills": result.min_fills,
            "minTrades": result.min_trades,
        },
    )
    return result


async def _recalculate_wallet_scores(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    include_disabled: bool = False,
) -> WalletScoreRunResponse:
    resolved_settings = settings or get_settings()
    now = datetime.now(UTC)
    metrics = await load_wallet_score_metrics(
        session,
        settings=resolved_settings,
        now=now,
        include_disabled=include_disabled,
    )

    records: list[dict[str, Any]] = []
    for metric in metrics:
        breakdown = calculate_wallet_score(metric, settings=resolved_settings, now=now)
        records.append(
            {
                "wallet_address": metric.wallet_address,
                "score": breakdown.score,
                "pnl_score": breakdown.pnl_score,
                "copyability_score": breakdown.copyability_score,
                "risk_score": breakdown.risk_score,
                "consistency_score": breakdown.consistency_score,
                "recency_score": breakdown.recency_score,
                "penalty_score": breakdown.penalty_score,
                "copyable_pnl_usd": breakdown.copyable_pnl_usd,
                "win_rate": breakdown.win_rate,
                "profit_factor": breakdown.profit_factor,
                "max_drawdown_pct": breakdown.max_drawdown_pct,
                "trade_count": breakdown.trade_count,
                "last_24h_score": breakdown.last_24h_score,
                "last_7d_score": breakdown.last_7d_score,
                "last_30d_score": breakdown.last_30d_score,
            }
        )

    if records:
        stmt = insert(WalletScore).values(records)
        update_columns = {
            key: getattr(stmt.excluded, key)
            for key in records[0]
            if key != "wallet_address"
        }
        update_columns["updated_at"] = func.now()
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["wallet_address"],
                set_=update_columns,
            )
        )
    await session.commit()

    return WalletScoreRunResponse(
        total_wallets=len(metrics),
        scored_wallets=len(records),
        skipped_wallets=0,
        window_days=resolved_settings.scoring_window_days,
        min_fills=resolved_settings.scoring_min_fills,
        min_trades=resolved_settings.scoring_min_trades,
        updated_at=now,
    )


async def list_wallet_scores(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
) -> WalletScoreListResponse:
    total = await session.scalar(select(func.count()).select_from(WalletScore))
    result = await session.execute(
        select(WalletScore)
        .order_by(WalletScore.score.desc(), WalletScore.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return WalletScoreListResponse(
        items=list(result.scalars().all()),
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


async def get_wallet_score_detail(
    session: AsyncSession,
    *,
    address: str,
    settings: Settings | None = None,
) -> WalletScoreDetailResponse:
    resolved_settings = settings or get_settings()
    normalized_address = normalize_wallet_address(address)
    now = datetime.now(UTC)
    metrics = await load_wallet_score_metrics(
        session,
        settings=resolved_settings,
        now=now,
        include_disabled=True,
        wallet_address=normalized_address,
    )
    if not metrics:
        raise WalletScoreDetailNotFoundError(normalized_address)

    wallet_metrics = metrics[0]
    breakdown = calculate_wallet_score(
        wallet_metrics,
        settings=resolved_settings,
        now=now,
    )
    penalty_items = calculate_penalty_items(
        metrics=wallet_metrics,
        min_trades=resolved_settings.scoring_min_trades,
        recency_score=breakdown.recency_score,
        liquidation_penalty_per_event=(
            resolved_settings.scoring_liquidation_penalty_per_event
        ),
        liquidation_penalty_max=resolved_settings.scoring_liquidation_penalty_max,
    )

    return WalletScoreDetailResponse(
        wallet_address=wallet_metrics.wallet_address,
        window_days=resolved_settings.scoring_window_days,
        min_trades=resolved_settings.scoring_min_trades,
        fill_count=wallet_metrics.fill_count,
        trade_count=wallet_metrics.trade_count,
        ignored_fill_count=wallet_metrics.ignored_fill_count,
        open_trade_count=wallet_metrics.open_trade_count,
        liquidation_count=wallet_metrics.liquidation_event_count,
        liquidation_fill_count=wallet_metrics.liquidation_fill_count,
        liquidation_event_count=wallet_metrics.liquidation_event_count,
        recency_score=breakdown.recency_score,
        net_pnl_usd=wallet_metrics.net_pnl_usd,
        gross_profit_usd=wallet_metrics.gross_profit_usd,
        penalty_score=breakdown.penalty_score,
        penalty_items=penalty_items,
    )


async def load_wallet_score_metrics(
    session: AsyncSession,
    *,
    settings: Settings,
    now: datetime,
    include_disabled: bool,
    wallet_address: str | None = None,
) -> list[WalletScoreMetrics]:
    window_start_ms = timestamp_ms(now - timedelta(days=settings.scoring_window_days))
    start_24h_ms = timestamp_ms(now - timedelta(days=1))
    start_7d_ms = timestamp_ms(now - timedelta(days=7))

    result = await session.execute(
        text(
            """
            with target_wallets as (
              select address
              from watched_wallets
              where (:include_disabled or enabled is true)
                and (
                  cast(:wallet_address as text) is null
                  or address = cast(:wallet_address as text)
                )
            ),
            fills as (
              select
                wallet_address,
                id,
                external_fill_id,
                coin,
                timestamp_ms,
                coalesce(notional_usd, 0) as notional_usd,
                coalesce(pnl_usd, 0) as pnl_usd,
                coalesce(fee_usd, 0) as fee_usd,
                coalesce(pnl_usd, 0) - coalesce(fee_usd, 0) as net_pnl_usd,
                raw_json,
                to_timestamp(timestamp_ms / 1000.0)::date as fill_day
              from wallet_fills
              where timestamp_ms >= :window_start_ms
            ),
            wallet_agg as (
              select
                tw.address as wallet_address,
                count(f.id) as fill_count,
                count(distinct f.coin) as unique_coin_count,
                count(distinct f.fill_day) as active_days,
                coalesce(sum(f.notional_usd), 0) as total_notional_usd,
                coalesce(avg(f.notional_usd), 0) as average_fill_notional_usd,
                coalesce(sum(f.pnl_usd), 0) as total_pnl_usd,
                coalesce(sum(f.fee_usd), 0) as total_fee_usd,
                coalesce(sum(f.net_pnl_usd), 0) as net_pnl_usd,
                coalesce(sum(case when f.net_pnl_usd > 0 then f.net_pnl_usd else 0 end), 0)
                  as gross_profit_usd,
                abs(coalesce(sum(case when f.net_pnl_usd < 0 then f.net_pnl_usd else 0 end), 0))
                  as gross_loss_usd,
                coalesce(sum(case when f.pnl_usd > 0 then 1 else 0 end), 0)
                  as profitable_fill_count,
                coalesce(sum(case when f.pnl_usd < 0 then 1 else 0 end), 0)
                  as losing_fill_count,
                min(f.timestamp_ms) as first_fill_time_ms,
                max(f.timestamp_ms) as last_fill_time_ms,
                count(f.id) filter (where f.timestamp_ms >= :start_24h_ms) as fills_24h,
                coalesce(sum(f.notional_usd) filter (where f.timestamp_ms >= :start_24h_ms), 0)
                  as notional_24h,
                coalesce(sum(f.net_pnl_usd) filter (where f.timestamp_ms >= :start_24h_ms), 0)
                  as net_pnl_24h,
                count(f.id) filter (where f.timestamp_ms >= :start_7d_ms) as fills_7d,
                coalesce(sum(f.notional_usd) filter (where f.timestamp_ms >= :start_7d_ms), 0)
                  as notional_7d,
                coalesce(sum(f.net_pnl_usd) filter (where f.timestamp_ms >= :start_7d_ms), 0)
                  as net_pnl_7d
              from target_wallets tw
              left join fills f on f.wallet_address = tw.address
              group by tw.address
            ),
            coin_notional as (
              select wallet_address, coin, sum(notional_usd) as coin_notional_usd
              from fills
              group by wallet_address, coin
            ),
            coin_agg as (
              select wallet_address, max(coin_notional_usd) as max_coin_notional_usd
              from coin_notional
              group by wallet_address
            ),
            ordered_fills as (
              select
                wallet_address,
                timestamp_ms,
                id,
                net_pnl_usd,
                sum(net_pnl_usd) over (
                  partition by wallet_address
                  order by timestamp_ms, id
                  rows between unbounded preceding and current row
                ) as cumulative_pnl
              from fills
            ),
            running_peaks as (
              select
                wallet_address,
                cumulative_pnl,
                max(cumulative_pnl) over (
                  partition by wallet_address
                  order by timestamp_ms, id
                  rows between unbounded preceding and current row
                ) as peak_pnl
              from ordered_fills
            ),
            drawdown_agg as (
              select
                wallet_address,
                abs(coalesce(min(cumulative_pnl - peak_pnl), 0)) as max_drawdown_usd
              from running_peaks
              group by wallet_address
            ),
            liquidation_fills as (
              select
                wallet_address,
                timestamp_ms,
                id
              from fills
              where raw_json ? 'liquidation'
            ),
            liquidation_ordered as (
              select
                wallet_address,
                timestamp_ms,
                lag(timestamp_ms) over (
                  partition by wallet_address
                  order by timestamp_ms, id
                ) as previous_timestamp_ms
              from liquidation_fills
            ),
            liquidation_agg as (
              select
                wallet_address,
                count(*) as liquidation_fill_count,
                coalesce(
                  sum(
                    case
                      when previous_timestamp_ms is null then 1
                      when timestamp_ms - previous_timestamp_ms > :liquidation_event_gap_ms then 1
                      else 0
                    end
                  ),
                  0
                ) as liquidation_event_count
              from liquidation_ordered
              group by wallet_address
            )
            select
              wa.*,
              coalesce(ca.max_coin_notional_usd, 0) as max_coin_notional_usd,
              coalesce(da.max_drawdown_usd, 0) as max_drawdown_usd,
              coalesce(la.liquidation_fill_count, 0) as liquidation_fill_count,
              coalesce(la.liquidation_event_count, 0) as liquidation_event_count
            from wallet_agg wa
            left join coin_agg ca on ca.wallet_address = wa.wallet_address
            left join drawdown_agg da on da.wallet_address = wa.wallet_address
            left join liquidation_agg la on la.wallet_address = wa.wallet_address
            order by wa.wallet_address
            """
        ),
        {
            "include_disabled": include_disabled,
            "wallet_address": wallet_address,
            "window_start_ms": window_start_ms,
            "start_24h_ms": start_24h_ms,
            "start_7d_ms": start_7d_ms,
            "liquidation_event_gap_ms": settings.scoring_liquidation_event_gap_seconds * 1000,
        },
    )

    base_metrics = [metrics_from_row(row) for row in result.mappings().all()]
    reconstructed_trades = await reconstruct_wallet_trades(
        session,
        window_start_ms=window_start_ms,
        start_24h_ms=start_24h_ms,
        start_7d_ms=start_7d_ms,
        include_disabled=include_disabled,
        wallet_address=wallet_address,
    )
    return [
        metrics_with_reconstructed_trades(
            metrics,
            reconstructed_trades.get(metrics.wallet_address),
        )
        for metrics in base_metrics
    ]


def calculate_wallet_score(
    metrics: WalletScoreMetrics,
    *,
    settings: Settings,
    now: datetime,
) -> WalletScoreBreakdown:
    if metrics.trade_count <= 0:
        return WalletScoreBreakdown(
            score=ZERO,
            pnl_score=ZERO,
            copyability_score=ZERO,
            risk_score=ZERO,
            consistency_score=ZERO,
            recency_score=ZERO,
            penalty_score=HUNDRED,
            copyable_pnl_usd=ZERO,
            win_rate=None,
            profit_factor=None,
            max_drawdown_pct=None,
            trade_count=0,
            last_24h_score=ZERO,
            last_7d_score=ZERO,
            last_30d_score=ZERO,
        )

    closed_trade_count = metrics.profitable_trade_count + metrics.losing_trade_count
    win_rate = (
        Decimal(metrics.profitable_trade_count) / Decimal(closed_trade_count)
        if closed_trade_count > 0
        else None
    )
    profit_factor = calculate_profit_factor(metrics.gross_profit_usd, metrics.gross_loss_usd)
    drawdown_base = max_decimal(metrics.gross_profit_usd, abs(metrics.net_pnl_usd), ONE)
    max_drawdown_pct = (metrics.max_drawdown_usd / drawdown_base).quantize(RATIO_QUANT)

    pnl_score = calculate_pnl_score(metrics.net_pnl_usd, metrics.total_notional_usd)
    consistency_score = calculate_consistency_score(
        win_rate=win_rate,
        profit_factor=profit_factor,
        active_days=metrics.active_days,
        target_active_days=settings.scoring_target_active_days,
    )
    risk_score = calculate_risk_score(
        metrics=metrics,
        closed_trade_count=closed_trade_count,
        drawdown_base=drawdown_base,
    )
    copyability_score = calculate_copyability_score(
        metrics=metrics,
        target_trades=settings.scoring_target_trades,
    )
    recency_score = calculate_recency_score(
        metrics.last_trade_time_ms,
        now=now,
        stale_days=settings.scoring_stale_days,
    )
    penalty_score = calculate_penalty_score(
        metrics=metrics,
        min_trades=settings.scoring_min_trades,
        recency_score=recency_score,
        liquidation_penalty_per_event=settings.scoring_liquidation_penalty_per_event,
        liquidation_penalty_max=settings.scoring_liquidation_penalty_max,
    )

    weight_sum = (
        settings.scoring_weight_pnl
        + settings.scoring_weight_consistency
        + settings.scoring_weight_risk
        + settings.scoring_weight_copyability
        + settings.scoring_weight_recency
    )
    if weight_sum <= ZERO:
        weight_sum = ONE

    gross_score = (
        pnl_score * settings.scoring_weight_pnl
        + consistency_score * settings.scoring_weight_consistency
        + risk_score * settings.scoring_weight_risk
        + copyability_score * settings.scoring_weight_copyability
        + recency_score * settings.scoring_weight_recency
    ) / weight_sum
    final_score = score_value(gross_score - penalty_score)
    if metrics.trade_count < settings.scoring_min_trades:
        sample_cap = (
            Decimal(metrics.trade_count)
            / Decimal(settings.scoring_min_trades)
            * Decimal("45")
        )
        final_score = min_decimal(final_score, score_value(sample_cap))

    return WalletScoreBreakdown(
        score=final_score,
        pnl_score=score_value(pnl_score),
        copyability_score=score_value(copyability_score),
        risk_score=score_value(risk_score),
        consistency_score=score_value(consistency_score),
        recency_score=score_value(recency_score),
        penalty_score=score_value(penalty_score),
        copyable_pnl_usd=metrics.net_pnl_usd,
        win_rate=win_rate.quantize(RATIO_QUANT) if win_rate is not None else None,
        profit_factor=profit_factor.quantize(SCORE_QUANT) if profit_factor is not None else None,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=metrics.trade_count,
        last_24h_score=calculate_window_score(
            trades=metrics.trades_24h,
            net_pnl_usd=metrics.net_pnl_24h,
            notional_usd=metrics.notional_24h,
        ),
        last_7d_score=calculate_window_score(
            trades=metrics.trades_7d,
            net_pnl_usd=metrics.net_pnl_7d,
            notional_usd=metrics.notional_7d,
        ),
        last_30d_score=calculate_window_score(
            trades=metrics.trade_count,
            net_pnl_usd=metrics.net_pnl_usd,
            notional_usd=metrics.total_notional_usd,
        ),
    )


def calculate_pnl_score(net_pnl_usd: Decimal, notional_usd: Decimal) -> Decimal:
    roi = net_pnl_usd / notional_usd if notional_usd > ZERO else ZERO
    roi_score = range_score(roi, Decimal("-0.05"), Decimal("0.05"))
    net_score = range_score(net_pnl_usd, Decimal("-5000"), Decimal("5000"))
    return roi_score * Decimal("0.70") + net_score * Decimal("0.30")


def calculate_consistency_score(
    *,
    win_rate: Decimal | None,
    profit_factor: Decimal | None,
    active_days: int,
    target_active_days: int,
) -> Decimal:
    win_score = (
        Decimal("50")
        if win_rate is None
        else range_score(win_rate, Decimal("0.35"), Decimal("0.65"))
    )
    pf_score = (
        Decimal("50")
        if profit_factor is None
        else range_score(profit_factor, ONE, Decimal("3"))
    )
    activity_score = score_value(Decimal(active_days) / Decimal(target_active_days) * HUNDRED)
    return (
        win_score * Decimal("0.45")
        + pf_score * Decimal("0.35")
        + activity_score * Decimal("0.20")
    )


def calculate_risk_score(
    *,
    metrics: WalletScoreMetrics,
    closed_trade_count: int,
    drawdown_base: Decimal,
) -> Decimal:
    loss_ratio = metrics.gross_loss_usd / max_decimal(metrics.gross_profit_usd, ONE)
    drawdown_ratio = metrics.max_drawdown_usd / drawdown_base
    losing_rate = (
        Decimal(metrics.losing_trade_count) / Decimal(closed_trade_count)
        if closed_trade_count > 0
        else ZERO
    )
    penalty = (
        min_decimal(loss_ratio * Decimal("40"), Decimal("40"))
        + min_decimal(drawdown_ratio * Decimal("35"), Decimal("35"))
        + losing_rate * Decimal("15")
    )
    return score_value(HUNDRED - penalty)


def calculate_copyability_score(
    *,
    metrics: WalletScoreMetrics,
    target_trades: int,
) -> Decimal:
    trade_score = score_value(Decimal(metrics.trade_count) / Decimal(target_trades) * HUNDRED)
    avg_notional_score = calculate_average_notional_score(metrics.average_trade_notional_usd)
    concentration = (
        metrics.max_coin_notional_usd / metrics.total_notional_usd
        if metrics.total_notional_usd > ZERO
        else ONE
    )
    concentration_score = score_value((ONE - concentration) / Decimal("0.70") * HUNDRED)
    unique_coin_score = score_value(
        Decimal(min(metrics.unique_coin_count, 4)) / Decimal("4") * HUNDRED
    )
    return (
        trade_score * Decimal("0.35")
        + avg_notional_score * Decimal("0.25")
        + concentration_score * Decimal("0.20")
        + unique_coin_score * Decimal("0.20")
    )


def calculate_average_notional_score(average_notional_usd: Decimal) -> Decimal:
    if average_notional_usd <= ZERO:
        return ZERO
    if average_notional_usd < Decimal("50"):
        return score_value(average_notional_usd / Decimal("50") * Decimal("70"))
    if average_notional_usd <= Decimal("250000"):
        return HUNDRED
    if average_notional_usd >= Decimal("1000000"):
        return Decimal("40")
    reduction = (average_notional_usd - Decimal("250000")) / Decimal("750000") * Decimal("60")
    return score_value(HUNDRED - reduction)


def calculate_recency_score(
    last_fill_time_ms: int | None,
    *,
    now: datetime,
    stale_days: int,
) -> Decimal:
    if last_fill_time_ms is None:
        return ZERO
    age_ms = max(0, timestamp_ms(now) - int(last_fill_time_ms))
    age_days = Decimal(age_ms) / Decimal(86_400_000)
    return score_value(HUNDRED - (age_days / Decimal(stale_days) * HUNDRED))


def calculate_penalty_score(
    *,
    metrics: WalletScoreMetrics,
    min_trades: int,
    recency_score: Decimal,
    liquidation_penalty_per_event: Decimal,
    liquidation_penalty_max: Decimal,
) -> Decimal:
    penalty = sum(
        (
            item.value
            for item in calculate_penalty_items(
                metrics=metrics,
                min_trades=min_trades,
                recency_score=recency_score,
                liquidation_penalty_per_event=liquidation_penalty_per_event,
                liquidation_penalty_max=liquidation_penalty_max,
            )
        ),
        ZERO,
    )
    return score_value(penalty)


def calculate_penalty_items(
    *,
    metrics: WalletScoreMetrics,
    min_trades: int,
    recency_score: Decimal,
    liquidation_penalty_per_event: Decimal,
    liquidation_penalty_max: Decimal,
) -> list[WalletScorePenaltyItem]:
    if metrics.trade_count <= 0:
        return [
            penalty_item(
                key="no_closed_trades",
                label="No closed trades",
                value=HUNDRED,
                max_value=HUNDRED,
                detail=(
                    "No closed source trades were reconstructed in the scoring window. "
                    f"Observed fills: {metrics.fill_count}, open trades: "
                    f"{metrics.open_trade_count}."
                ),
            )
        ]

    sample_size_penalty = ZERO
    if metrics.trade_count < min_trades:
        sample_size_penalty = (
            Decimal(min_trades - metrics.trade_count) / Decimal(min_trades)
        ) * Decimal("30")

    negative_pnl_penalty = ZERO
    if metrics.net_pnl_usd < ZERO:
        loss_base = abs(metrics.net_pnl_usd) + metrics.gross_profit_usd + ONE
        negative_pnl_penalty = min_decimal(
            abs(metrics.net_pnl_usd) / loss_base * Decimal("30"),
            Decimal("30"),
        )

    stale_penalty = Decimal("20") if recency_score <= ZERO else ZERO

    ignored_ratio = (
        Decimal(metrics.ignored_fill_count) / Decimal(metrics.fill_count)
        if metrics.fill_count > 0
        else ZERO
    )
    ignored_fill_penalty = min_decimal(ignored_ratio * Decimal("35"), Decimal("35"))

    open_only_penalty = (
        Decimal("10") if metrics.open_trade_count > 0 and metrics.trade_count == 0 else ZERO
    )
    liquidation_penalty = min_decimal(
        Decimal(metrics.liquidation_event_count) * liquidation_penalty_per_event,
        liquidation_penalty_max,
    )

    return [
        penalty_item(
            key="sample_size",
            label="Low trade sample",
            value=sample_size_penalty,
            max_value=Decimal("30"),
            detail=(
                f"{metrics.trade_count} closed trades reconstructed; "
                f"target minimum is {min_trades}."
            ),
        ),
        penalty_item(
            key="negative_pnl",
            label="Negative net PnL",
            value=negative_pnl_penalty,
            max_value=Decimal("30"),
            detail=(
                f"Net PnL is {metrics.net_pnl_usd}; gross profit is "
                f"{metrics.gross_profit_usd}."
            ),
        ),
        penalty_item(
            key="stale_recency",
            label="Stale trading",
            value=stale_penalty,
            max_value=Decimal("20"),
            detail=f"Recency score is {recency_score}.",
        ),
        penalty_item(
            key="ignored_fills",
            label="Ignored fills",
            value=ignored_fill_penalty,
            max_value=Decimal("35"),
            detail=(
                f"{metrics.ignored_fill_count} of {metrics.fill_count} fills were "
                "close-only or pre-existing-position adds."
            ),
        ),
        penalty_item(
            key="open_only",
            label="Open-only activity",
            value=open_only_penalty,
            max_value=Decimal("10"),
            detail=(
                f"{metrics.open_trade_count} open trades and "
                f"{metrics.trade_count} closed trades."
            ),
        ),
        penalty_item(
            key="liquidations",
            label="Liquidation events",
            value=liquidation_penalty,
            max_value=liquidation_penalty_max,
            detail=(
                f"{metrics.liquidation_event_count} liquidation events observed from "
                f"{metrics.liquidation_fill_count} liquidation fills; "
                f"{liquidation_penalty_per_event} penalty per event."
            ),
        ),
    ]


def penalty_item(
    *,
    key: str,
    label: str,
    value: Decimal,
    max_value: Decimal,
    detail: str,
) -> WalletScorePenaltyItem:
    return WalletScorePenaltyItem(
        key=key,
        label=label,
        value=score_value(value),
        max_value=max_value,
        active=value > ZERO,
        detail=detail,
    )


def calculate_window_score(
    *,
    trades: int,
    net_pnl_usd: Decimal,
    notional_usd: Decimal,
) -> Decimal:
    if trades <= 0:
        return ZERO
    activity_score = score_value(Decimal(min(trades, 10)) / Decimal("10") * HUNDRED)
    return score_value(
        calculate_pnl_score(net_pnl_usd, notional_usd) * Decimal("0.80")
        + activity_score * Decimal("0.20")
    )


def calculate_profit_factor(gross_profit_usd: Decimal, gross_loss_usd: Decimal) -> Decimal | None:
    if gross_profit_usd <= ZERO and gross_loss_usd <= ZERO:
        return None
    if gross_loss_usd <= ZERO:
        return Decimal("999") if gross_profit_usd > ZERO else None
    return gross_profit_usd / gross_loss_usd


def metrics_from_row(row: Any) -> WalletScoreMetrics:
    return WalletScoreMetrics(
        wallet_address=str(row["wallet_address"]),
        fill_count=int(row["fill_count"] or 0),
        trade_count=0,
        ignored_fill_count=0,
        open_trade_count=0,
        unique_coin_count=int(row["unique_coin_count"] or 0),
        active_days=int(row["active_days"] or 0),
        total_notional_usd=decimal_value(row["total_notional_usd"]),
        average_trade_notional_usd=decimal_value(row["average_fill_notional_usd"]),
        total_pnl_usd=decimal_value(row["total_pnl_usd"]),
        total_fee_usd=decimal_value(row["total_fee_usd"]),
        net_pnl_usd=decimal_value(row["net_pnl_usd"]),
        gross_profit_usd=decimal_value(row["gross_profit_usd"]),
        gross_loss_usd=decimal_value(row["gross_loss_usd"]),
        profitable_trade_count=int(row["profitable_fill_count"] or 0),
        losing_trade_count=int(row["losing_fill_count"] or 0),
        liquidation_fill_count=int(row["liquidation_fill_count"] or 0),
        liquidation_event_count=int(row["liquidation_event_count"] or 0),
        max_coin_notional_usd=decimal_value(row["max_coin_notional_usd"]),
        max_drawdown_usd=decimal_value(row["max_drawdown_usd"]),
        first_trade_time_ms=(
            int(row["first_fill_time_ms"]) if row["first_fill_time_ms"] is not None else None
        ),
        last_trade_time_ms=(
            int(row["last_fill_time_ms"]) if row["last_fill_time_ms"] is not None else None
        ),
        trades_24h=int(row["fills_24h"] or 0),
        notional_24h=decimal_value(row["notional_24h"]),
        net_pnl_24h=decimal_value(row["net_pnl_24h"]),
        trades_7d=int(row["fills_7d"] or 0),
        notional_7d=decimal_value(row["notional_7d"]),
        net_pnl_7d=decimal_value(row["net_pnl_7d"]),
    )


def metrics_with_reconstructed_trades(
    base_metrics: WalletScoreMetrics,
    trades: ReconstructedWalletTrades | None,
) -> WalletScoreMetrics:
    if trades is None:
        return WalletScoreMetrics(
            wallet_address=base_metrics.wallet_address,
            fill_count=base_metrics.fill_count,
            trade_count=0,
            ignored_fill_count=base_metrics.fill_count,
            open_trade_count=0,
            unique_coin_count=0,
            active_days=0,
            total_notional_usd=ZERO,
            average_trade_notional_usd=ZERO,
            total_pnl_usd=ZERO,
            total_fee_usd=ZERO,
            net_pnl_usd=ZERO,
            gross_profit_usd=ZERO,
            gross_loss_usd=ZERO,
            profitable_trade_count=0,
            losing_trade_count=0,
            liquidation_fill_count=base_metrics.liquidation_fill_count,
            liquidation_event_count=base_metrics.liquidation_event_count,
            max_coin_notional_usd=ZERO,
            max_drawdown_usd=ZERO,
            first_trade_time_ms=None,
            last_trade_time_ms=None,
            trades_24h=0,
            notional_24h=ZERO,
            net_pnl_24h=ZERO,
            trades_7d=0,
            notional_7d=ZERO,
            net_pnl_7d=ZERO,
        )

    ignored_fill_count = trades.unmatched_close_fill_count + trades.preexisting_open_fill_count
    return WalletScoreMetrics(
        wallet_address=base_metrics.wallet_address,
        fill_count=base_metrics.fill_count,
        trade_count=trades.closed_trade_count,
        ignored_fill_count=ignored_fill_count,
        open_trade_count=trades.open_trade_count,
        unique_coin_count=trades.unique_coin_count,
        active_days=trades.active_day_count,
        total_notional_usd=trades.total_entry_notional_usd,
        average_trade_notional_usd=trades.average_trade_notional_usd,
        total_pnl_usd=trades.realized_pnl_usd,
        total_fee_usd=trades.fee_usd,
        net_pnl_usd=trades.net_pnl_usd,
        gross_profit_usd=trades.gross_profit_usd,
        gross_loss_usd=trades.gross_loss_usd,
        profitable_trade_count=trades.winning_trade_count,
        losing_trade_count=trades.losing_trade_count,
        liquidation_fill_count=base_metrics.liquidation_fill_count,
        liquidation_event_count=base_metrics.liquidation_event_count,
        max_coin_notional_usd=trades.max_coin_notional_usd,
        max_drawdown_usd=trades.max_drawdown_usd,
        first_trade_time_ms=trades.first_trade_time_ms,
        last_trade_time_ms=trades.last_trade_time_ms,
        trades_24h=trades.trades_24h,
        notional_24h=trades.notional_24h,
        net_pnl_24h=trades.net_pnl_24h,
        trades_7d=trades.trades_7d,
        notional_7d=trades.notional_7d,
        net_pnl_7d=trades.net_pnl_7d,
    )


def range_score(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    if high <= low:
        return ZERO
    return score_value((value - low) / (high - low) * HUNDRED)


def score_value(value: Decimal) -> Decimal:
    return clamp_decimal(value, ZERO, HUNDRED).quantize(SCORE_QUANT)


def clamp_decimal(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max_decimal(low, min_decimal(value, high))


def min_decimal(left: Decimal, right: Decimal) -> Decimal:
    return left if left <= right else right


def max_decimal(*values: Decimal) -> Decimal:
    return max(values)


def decimal_value(value: Any) -> Decimal:
    if value is None:
        return ZERO
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return ZERO


def timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)
