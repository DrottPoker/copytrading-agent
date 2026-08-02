import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import WalletScore, WatchedWallet
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.score import (
    WalletScoreComponentDetail,
    WalletScoreDetailItem,
    WalletScoreDetailResponse,
    WalletScoreListResponse,
    WalletScorePenaltyItem,
    WalletScoreRunResponse,
)
from app.schemas.wallet import normalize_wallet_address
from app.services.job_lock_service import job_lock
from app.services.operation_status_service import (
    OperationCanceledError,
    mark_operation_canceled,
    mark_operation_failed,
    mark_operation_progress,
    mark_operation_started,
    mark_operation_succeeded,
    new_operation_run_id,
    raise_if_operation_cancellation_requested,
)
from app.services.source_trade_reconstruction_service import (
    ReconstructedWalletTrades,
    load_materialized_wallet_trades,
    realized_entry_notional_for_trade,
    sync_materialized_source_trades,
)
from app.services.wallet_current_state_service import (
    load_known_wallet_perp_dexes_for_addresses,
    load_wallet_account_value_summary,
    load_wallet_perp_clearinghouse_states,
    summarize_perp_clearinghouse_states,
)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
SCORE_QUANT = Decimal("0.01")
RATIO_QUANT = Decimal("0.0001")
CURRENT_DRAWDOWN_WALLET_TIMEOUT_SECONDS = 45
logger = logging.getLogger(__name__)


class WalletScoreDetailNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class WalletScoreMetrics:
    wallet_address: str
    fill_count: int
    trade_count: int
    ignored_fill_count: int
    open_trade_count: int
    close_fill_count: int
    unique_coin_count: int
    active_days: int
    total_notional_usd: Decimal
    average_trade_notional_usd: Decimal
    median_trade_notional_usd: Decimal | None
    p25_trade_notional_usd: Decimal | None
    copyable_trade_ratio: Decimal | None
    average_fills_per_trade: Decimal | None
    average_trade_roi: Decimal | None
    median_trade_roi: Decimal | None
    total_pnl_usd: Decimal
    total_fee_usd: Decimal
    net_pnl_usd: Decimal
    gross_profit_usd: Decimal
    gross_loss_usd: Decimal
    profitable_trade_count: int
    losing_trade_count: int
    effective_winning_trade_count: Decimal | None
    largest_win_profit_share: Decimal | None
    trade_roi_stddev: Decimal | None
    downside_trade_roi_stddev: Decimal | None
    max_inactive_gap_days: int | None
    liquidation_fill_count: int
    liquidation_event_count: int
    liquidation_trade_count: int
    liquidation_close_fill_count: int
    liquidation_notional_usd: Decimal
    max_coin_notional_usd: Decimal
    max_drawdown_usd: Decimal
    current_perp_equity_usd: Decimal | None
    current_account_value_usd: Decimal | None
    current_unrealized_pnl_usd: Decimal | None
    current_drawdown_pct: Decimal | None
    current_margin_usage_pct: Decimal | None
    current_notional_exposure_pct: Decimal | None
    open_position_stress_pct: Decimal | None
    current_drawdown_status: str
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
    realized_drawdown_pct: Decimal | None
    current_drawdown_pct: Decimal | None
    open_position_stress_pct: Decimal | None
    live_risk_score_cap: Decimal | None
    current_drawdown_status: str
    trade_count: int
    last_24h_score: Decimal | None
    last_7d_score: Decimal | None
    last_30d_score: Decimal | None


@dataclass(frozen=True)
class WalletScoreExplanation:
    gross_score: Decimal
    final_score_before_cap: Decimal
    sample_cap: Decimal | None
    component_details: list[WalletScoreComponentDetail]


async def recalculate_wallet_scores(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    include_disabled: bool = False,
    use_lock: bool = True,
    operation_run_id: str | None = None,
) -> WalletScoreRunResponse:
    if use_lock:
        async with job_lock(session, key="wallet_scoring", ttl_seconds=30 * 60):
            return await recalculate_wallet_scores(
                session,
                settings=settings,
                include_disabled=include_disabled,
                use_lock=False,
                operation_run_id=operation_run_id,
            )

    resolved_settings = settings or get_settings()
    resolved_run_id = operation_run_id or new_operation_run_id()
    payload = {
        "runId": resolved_run_id,
        "windowDays": resolved_settings.scoring_window_days,
        "includeDisabled": include_disabled,
        "stage": "historical_metrics",
        "stageLabel": "Historical metrics",
        "stageDetail": "Aggregating fills and reconstructed source trades.",
        "progressPercent": 5,
    }
    await mark_operation_started(session, key="wallet_scoring", payload=payload)
    try:
        await raise_if_operation_cancellation_requested(
            session,
            key="wallet_scoring",
            run_id=resolved_run_id,
        )
        result = await _recalculate_wallet_scores(
            session,
            settings=resolved_settings,
            include_disabled=include_disabled,
            operation_run_id=resolved_run_id,
        )
        await raise_if_operation_cancellation_requested(
            session,
            key="wallet_scoring",
            run_id=resolved_run_id,
        )
    except OperationCanceledError:
        await session.rollback()
        await mark_operation_canceled(
            session,
            key="wallet_scoring",
            run_id=resolved_run_id,
        )
        raise
    except asyncio.CancelledError:
        await session.rollback()
        await mark_operation_failed(
            session,
            key="wallet_scoring",
            error="Wallet scoring was interrupted before completion.",
            payload={
                **payload,
                "stage": "interrupted",
                "stageLabel": "Interrupted",
                "stageDetail": "The scoring task stopped before scores were persisted.",
            },
        )
        raise
    except Exception as exc:
        await session.rollback()
        await mark_operation_failed(
            session,
            key="wallet_scoring",
            error=str(exc) or exc.__class__.__name__,
            payload={
                **payload,
                "stage": "failed",
                "stageLabel": "Failed",
                "stageDetail": str(exc) or exc.__class__.__name__,
            },
        )
        raise

    await mark_operation_succeeded(
        session,
        key="wallet_scoring",
        payload={
            **payload,
            "stage": "completed",
            "stageLabel": "Completed",
            "stageDetail": f"Persisted {result.scored_wallets} wallet scores.",
            "progressPercent": 100,
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
    operation_run_id: str,
) -> WalletScoreRunResponse:
    resolved_settings = settings or get_settings()
    now = datetime.now(UTC)
    metrics = await load_wallet_score_metrics(
        session,
        settings=resolved_settings,
        now=now,
        include_disabled=include_disabled,
        report_progress=True,
        operation_run_id=operation_run_id,
    )
    await raise_if_operation_cancellation_requested(
        session,
        key="wallet_scoring",
        run_id=operation_run_id,
    )

    await mark_operation_progress(
        session,
        key="wallet_scoring",
        payload={
            "windowDays": resolved_settings.scoring_window_days,
            "includeDisabled": include_disabled,
            "stage": "persisting_scores",
            "stageLabel": "Persisting scores",
            "stageDetail": f"Writing {len(metrics)} wallet scores.",
            "progressPercent": 95,
        },
    )

    records: list[dict[str, Any]] = []
    for index, metric in enumerate(metrics):
        if index % 50 == 0:
            await raise_if_operation_cancellation_requested(
                session,
                key="wallet_scoring",
                run_id=operation_run_id,
            )
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
                "current_drawdown_pct": breakdown.current_drawdown_pct,
                "open_position_stress_pct": breakdown.open_position_stress_pct,
                "current_drawdown_status": breakdown.current_drawdown_status,
                "trade_count": breakdown.trade_count,
                "last_24h_score": breakdown.last_24h_score,
                "last_7d_score": breakdown.last_7d_score,
                "last_30d_score": breakdown.last_30d_score,
            }
        )

    await raise_if_operation_cancellation_requested(
        session,
        key="wallet_scoring",
        run_id=operation_run_id,
    )
    if records:
        stmt = insert(WalletScore).values(records)
        update_columns = {
            key: getattr(stmt.excluded, key) for key in records[0] if key != "wallet_address"
        }
        update_columns["updated_at"] = func.now()
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["wallet_address"],
                set_=update_columns,
            )
        )
    await session.execute(
        delete(WalletScore).where(
            ~select(WatchedWallet.address)
            .where(WatchedWallet.address == WalletScore.wallet_address)
            .exists()
        )
    )
    await raise_if_operation_cancellation_requested(
        session,
        key="wallet_scoring",
        run_id=operation_run_id,
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
    total = await session.scalar(
        select(func.count())
        .select_from(WalletScore)
        .join(WatchedWallet, WatchedWallet.address == WalletScore.wallet_address)
    )
    result = await session.execute(
        select(WalletScore)
        .join(WatchedWallet, WatchedWallet.address == WalletScore.wallet_address)
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
        settings=resolved_settings,
    )
    explanation = calculate_score_explanation(
        metrics=wallet_metrics,
        breakdown=breakdown,
        settings=resolved_settings,
        now=now,
        penalty_items=penalty_items,
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
        current_perp_equity_usd=wallet_metrics.current_perp_equity_usd,
        current_account_value_usd=wallet_metrics.current_account_value_usd,
        current_unrealized_pnl_usd=wallet_metrics.current_unrealized_pnl_usd,
        current_margin_usage_pct=wallet_metrics.current_margin_usage_pct,
        current_notional_exposure_pct=wallet_metrics.current_notional_exposure_pct,
        realized_drawdown_pct=breakdown.realized_drawdown_pct,
        current_drawdown_pct=breakdown.current_drawdown_pct,
        open_position_stress_pct=breakdown.open_position_stress_pct,
        live_risk_score_cap=breakdown.live_risk_score_cap,
        current_drawdown_status=breakdown.current_drawdown_status,
        gross_score=explanation.gross_score,
        final_score_before_cap=explanation.final_score_before_cap,
        sample_cap=explanation.sample_cap,
        penalty_score=breakdown.penalty_score,
        penalty_items=penalty_items,
        component_details=explanation.component_details,
    )


async def report_wallet_scoring_progress(
    session: AsyncSession,
    *,
    enabled: bool,
    payload: dict[str, Any],
) -> None:
    if not enabled:
        return
    await mark_operation_progress(
        session,
        key="wallet_scoring",
        payload=payload,
    )


async def load_wallet_score_metrics(
    session: AsyncSession,
    *,
    settings: Settings,
    now: datetime,
    include_disabled: bool,
    wallet_address: str | None = None,
    report_progress: bool = False,
    operation_run_id: str | None = None,
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
            wallet_agg as (
              select
                tw.address as wallet_address,
                count(wf.timestamp_ms) as fill_count,
                min(wf.timestamp_ms) as first_fill_time_ms
              from target_wallets tw
              left join wallet_fills wf
                on wf.wallet_address = tw.address
               and wf.timestamp_ms >= :window_start_ms
              group by tw.address
            ),
            last_activity as (
              select
                tw.address as wallet_address,
                (
                  select max(wf.timestamp_ms)
                  from wallet_fills wf
                  where wf.wallet_address = tw.address
                    and wf.timestamp_ms >= :window_start_ms
                    and not (wf.raw_json ? 'liquidation')
                ) as last_activity_time_ms
              from target_wallets tw
            ),
            liquidation_agg as (
              select
                ordered.wallet_address,
                count(*) as liquidation_fill_count,
                coalesce(sum(ordered.notional_usd), 0) as liquidation_notional_usd,
                coalesce(
                  sum(
                    case
                      when ordered.previous_timestamp_ms is null then 1
                      when ordered.timestamp_ms - ordered.previous_timestamp_ms
                        > :liquidation_event_gap_ms then 1
                      else 0
                    end
                  ),
                  0
                ) as liquidation_event_count
              from (
                select
                  wf.wallet_address,
                  wf.timestamp_ms,
                  coalesce(wf.notional_usd, 0) as notional_usd,
                  lag(wf.timestamp_ms) over (
                    partition by wf.wallet_address
                    order by wf.timestamp_ms, wf.id
                  ) as previous_timestamp_ms
                from wallet_fills wf
                join target_wallets tw on tw.address = wf.wallet_address
                where wf.timestamp_ms >= :window_start_ms
                  and wf.raw_json ? 'liquidation'
              ) ordered
              group by ordered.wallet_address
            )
            select
              wa.*,
              activity.last_activity_time_ms,
              coalesce(la.liquidation_fill_count, 0) as liquidation_fill_count,
              coalesce(la.liquidation_event_count, 0) as liquidation_event_count,
              coalesce(la.liquidation_notional_usd, 0) as liquidation_notional_usd
            from wallet_agg wa
            left join last_activity activity on activity.wallet_address = wa.wallet_address
            left join liquidation_agg la on la.wallet_address = wa.wallet_address
            order by wa.wallet_address
            """
        ),
        {
            "include_disabled": include_disabled,
            "wallet_address": wallet_address,
            "window_start_ms": window_start_ms,
            "liquidation_event_gap_ms": settings.scoring_forced_exit_event_gap_seconds * 1000,
        },
    )

    base_metrics = [base_metrics_from_row(row) for row in result.mappings().all()]
    if operation_run_id is not None:
        await raise_if_operation_cancellation_requested(
            session,
            key="wallet_scoring",
            run_id=operation_run_id,
        )
    await report_wallet_scoring_progress(
        session,
        enabled=report_progress,
        payload={
            "windowDays": settings.scoring_window_days,
            "includeDisabled": include_disabled,
            "stage": "fill_summary",
            "stageLabel": "Fill summary",
            "stageDetail": f"Summarized raw fill activity for {len(base_metrics)} wallets.",
            "progressPercent": 15,
        },
    )
    await report_wallet_scoring_progress(
        session,
        enabled=report_progress,
        payload={
            "windowDays": settings.scoring_window_days,
            "includeDisabled": include_disabled,
            "stage": "source_trade_refresh",
            "stageLabel": "Source trades",
            "stageDetail": "Refreshing materialized trades for changed wallets only.",
            "progressPercent": 20,
        },
    )
    async def cancellation_checkpoint() -> None:
        if operation_run_id is not None:
            await raise_if_operation_cancellation_requested(
                session,
                key="wallet_scoring",
                run_id=operation_run_id,
            )

    await cancellation_checkpoint()
    refreshed_wallets = await sync_materialized_source_trades(
        session,
        include_disabled=include_disabled,
        wallet_address=wallet_address,
        cancellation_checkpoint=cancellation_checkpoint,
    )
    await cancellation_checkpoint()
    reconstructed_trades = await load_materialized_wallet_trades(
        session,
        window_start_ms=window_start_ms,
        start_24h_ms=start_24h_ms,
        start_7d_ms=start_7d_ms,
        include_disabled=include_disabled,
        wallet_address=wallet_address,
    )
    reconstructed_metrics = [
        metrics_with_reconstructed_trades(
            metrics,
            reconstructed_trades.get(metrics.wallet_address),
            settings=settings,
        )
        for metrics in base_metrics
    ]
    await report_wallet_scoring_progress(
        session,
        enabled=report_progress,
        payload={
            "windowDays": settings.scoring_window_days,
            "includeDisabled": include_disabled,
            "stage": "historical_metrics_complete",
            "stageLabel": "Historical metrics",
            "stageDetail": (
                f"Loaded reconstructed trades; refreshed {refreshed_wallets} changed wallets."
            ),
            "progressPercent": 35,
        },
    )
    if not settings.scoring_current_drawdown_enabled:
        return reconstructed_metrics

    return await metrics_with_current_drawdowns(
        session,
        metrics=reconstructed_metrics,
        settings=settings,
        include_disabled=include_disabled,
        report_progress=report_progress,
        operation_run_id=operation_run_id,
    )


async def metrics_with_current_drawdowns(
    session: AsyncSession,
    *,
    metrics: list[WalletScoreMetrics],
    settings: Settings,
    include_disabled: bool = False,
    report_progress: bool = False,
    operation_run_id: str | None = None,
) -> list[WalletScoreMetrics]:
    scorable_metrics = [metric for metric in metrics if metric.trade_count > 0]
    if not scorable_metrics:
        return metrics

    score_time = datetime.now(UTC)
    prioritized_metrics = sorted(
        scorable_metrics,
        key=lambda metric: (
            -calculate_wallet_score(metric, settings=settings, now=score_time).score,
            metric.wallet_address,
        ),
    )
    addresses = [metric.wallet_address for metric in scorable_metrics]
    known_dexes_by_address = await load_known_wallet_perp_dexes_for_addresses(
        session,
        addresses=addresses,
    )
    concurrency = settings.scoring_current_drawdown_concurrency
    timeout_seconds = settings.scoring_current_drawdown_run_timeout_seconds
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    resolved_metrics: dict[str, WalletScoreMetrics] = {}

    async with HyperliquidClient() as client:

        async def load_metric(metric: WalletScoreMetrics) -> WalletScoreMetrics:
            try:
                return await asyncio.wait_for(
                    metric_with_current_drawdown(
                        metric,
                        client=client,
                        known_dexes=known_dexes_by_address.get(metric.wallet_address.lower(), ()),
                        settings=settings,
                    ),
                    timeout=min(
                        timeout_seconds,
                        CURRENT_DRAWDOWN_WALLET_TIMEOUT_SECONDS,
                    ),
                )
            except TimeoutError:
                logger.warning(
                    "wallet current drawdown scoring timed out wallet=%s",
                    metric.wallet_address,
                )
                return unavailable_current_drawdown_metric(metric)
            except Exception:
                logger.exception(
                    "wallet current drawdown scoring failed wallet=%s",
                    metric.wallet_address,
                )
                return unavailable_current_drawdown_metric(metric)

        for start in range(0, len(prioritized_metrics), concurrency):
            if operation_run_id is not None:
                await raise_if_operation_cancellation_requested(
                    session,
                    key="wallet_scoring",
                    run_id=operation_run_id,
                )
            remaining_seconds = deadline - asyncio.get_running_loop().time()
            if remaining_seconds <= 0:
                break
            batch = prioritized_metrics[start : start + concurrency]
            tasks = [asyncio.create_task(load_metric(metric)) for metric in batch]
            try:
                done, pending = await asyncio.wait(tasks, timeout=remaining_seconds)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for task in done:
                metric = task.result()
                resolved_metrics[metric.wallet_address] = metric
            if pending:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                logger.warning(
                    "wallet current drawdown scoring reached runtime budget "
                    "processed=%s total=%s timeout_seconds=%s",
                    len(resolved_metrics),
                    len(prioritized_metrics),
                    timeout_seconds,
                )
            if operation_run_id is not None:
                await raise_if_operation_cancellation_requested(
                    session,
                    key="wallet_scoring",
                    run_id=operation_run_id,
                )

            processed = len(resolved_metrics)
            try:
                await report_wallet_scoring_progress(
                    session,
                    enabled=report_progress,
                    payload={
                        "windowDays": settings.scoring_window_days,
                        "includeDisabled": include_disabled,
                        "stage": "current_drawdown",
                        "stageLabel": "Live risk",
                        "stageDetail": (
                            f"Checked live current drawdown for {processed} of "
                            f"{len(prioritized_metrics)} wallets."
                        ),
                        "progressPercent": min(
                            90,
                            35 + round(processed / len(prioritized_metrics) * 55),
                        ),
                        "batchIndex": processed,
                        "batchSize": len(prioritized_metrics),
                    },
                )
            except Exception:
                logger.exception("failed to update wallet scoring progress")
            if pending:
                break

    return [
        (resolved_metrics.get(metric.wallet_address) or unavailable_current_drawdown_metric(metric))
        if metric.trade_count > 0
        else metric
        for metric in metrics
    ]


def unavailable_current_drawdown_metric(metric: WalletScoreMetrics) -> WalletScoreMetrics:
    return replace(
        metric,
        current_perp_equity_usd=None,
        current_account_value_usd=None,
        current_unrealized_pnl_usd=None,
        current_drawdown_pct=None,
        current_margin_usage_pct=None,
        current_notional_exposure_pct=None,
        open_position_stress_pct=None,
        current_drawdown_status="unavailable",
    )


async def metric_with_current_drawdown(
    metric: WalletScoreMetrics,
    *,
    client: HyperliquidClient,
    known_dexes: tuple[str, ...],
    settings: Settings,
) -> WalletScoreMetrics:
    perp_states, errors = await load_wallet_perp_clearinghouse_states(
        client=client,
        address=metric.wallet_address,
        dexes=known_dexes,
    )

    if errors or not perp_states:
        logger.debug(
            "wallet current drawdown scoring skipped wallet=%s errors=%s",
            metric.wallet_address,
            "; ".join(errors) or "Perp state unavailable.",
        )
        return replace(metric, current_drawdown_status="unavailable")

    perp_summary = summarize_perp_clearinghouse_states(perp_states)
    account_value_summary = await load_wallet_account_value_summary(
        client=client,
        address=metric.wallet_address,
        perp_summary=perp_summary,
    )
    if account_value_summary.error is not None:
        logger.debug(
            "wallet current drawdown scoring skipped wallet=%s error=%s",
            metric.wallet_address,
            account_value_summary.error,
        )
        return replace(metric, current_drawdown_status="unavailable")

    account_value = account_value_summary.account_value_usd
    current_drawdown_pct: Decimal | None = ZERO
    current_margin_usage_pct: Decimal | None = ZERO
    current_notional_exposure_pct: Decimal | None = ZERO
    open_position_stress_pct: Decimal | None = ZERO
    current_drawdown_status = "ok"
    if account_value <= ZERO:
        current_drawdown_pct = None
        current_margin_usage_pct = None
        current_notional_exposure_pct = None
        open_position_stress_pct = None
        current_drawdown_status = "zero_equity"
    else:
        if perp_summary.total_unrealized_pnl_usd < ZERO:
            current_drawdown_pct = (
                perp_summary.total_unrealized_pnl_usd.copy_abs() / account_value
            ).quantize(RATIO_QUANT)
        current_margin_usage_pct = (perp_summary.total_margin_used_usd / account_value).quantize(
            RATIO_QUANT
        )
        current_notional_exposure_pct = (
            perp_summary.total_position_notional_usd / account_value
        ).quantize(RATIO_QUANT)
        open_position_stress_pct = calculate_open_position_stress_pct(
            current_drawdown_pct=current_drawdown_pct,
            current_margin_usage_pct=current_margin_usage_pct,
            current_notional_exposure_pct=current_notional_exposure_pct,
            notional_full_ratio=settings.scoring_open_position_stress_notional_full_ratio,
        )

    return replace(
        metric,
        current_perp_equity_usd=perp_summary.account_value_usd,
        current_account_value_usd=account_value,
        current_unrealized_pnl_usd=perp_summary.total_unrealized_pnl_usd,
        current_drawdown_pct=current_drawdown_pct,
        current_margin_usage_pct=current_margin_usage_pct,
        current_notional_exposure_pct=current_notional_exposure_pct,
        open_position_stress_pct=open_position_stress_pct,
        current_drawdown_status=current_drawdown_status,
    )


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
            realized_drawdown_pct=None,
            current_drawdown_pct=metrics.current_drawdown_pct,
            open_position_stress_pct=metrics.open_position_stress_pct,
            live_risk_score_cap=None,
            current_drawdown_status=metrics.current_drawdown_status,
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

    pnl_score = calculate_profitability_score(metrics, settings=settings)
    consistency_score = calculate_consistency_score(
        metrics=metrics,
        settings=settings,
    )
    risk_score = calculate_risk_score(
        metrics=metrics,
        closed_trade_count=closed_trade_count,
        drawdown_base=drawdown_base,
        settings=settings,
    )
    copyability_score = calculate_copyability_score(
        metrics=metrics,
        settings=settings,
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
        settings=settings,
    )
    live_risk_score_cap = current_drawdown_score_cap(
        metrics.current_drawdown_pct,
        settings=settings,
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
    if live_risk_score_cap is not None:
        final_score = min_decimal(final_score, live_risk_score_cap)
    if metrics.trade_count < settings.scoring_min_trades:
        sample_cap = score_sample_cap(
            metrics.trade_count,
            settings.scoring_min_trades,
            max_score=settings.scoring_sample_cap_max_score,
        )
        final_score = min_decimal(final_score, score_value(sample_cap or ZERO))

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
        realized_drawdown_pct=max_drawdown_pct,
        current_drawdown_pct=metrics.current_drawdown_pct,
        open_position_stress_pct=metrics.open_position_stress_pct,
        live_risk_score_cap=live_risk_score_cap,
        current_drawdown_status=metrics.current_drawdown_status,
        trade_count=metrics.trade_count,
        last_24h_score=calculate_window_score(
            trades=metrics.trades_24h,
            net_pnl_usd=metrics.net_pnl_24h,
            notional_usd=metrics.notional_24h,
            settings=settings,
        ),
        last_7d_score=calculate_window_score(
            trades=metrics.trades_7d,
            net_pnl_usd=metrics.net_pnl_7d,
            notional_usd=metrics.notional_7d,
            settings=settings,
        ),
        last_30d_score=calculate_window_score(
            trades=metrics.trade_count,
            net_pnl_usd=metrics.net_pnl_usd,
            notional_usd=metrics.total_notional_usd,
            settings=settings,
        ),
    )


def calculate_score_explanation(
    *,
    metrics: WalletScoreMetrics,
    breakdown: WalletScoreBreakdown,
    settings: Settings,
    now: datetime,
    penalty_items: list[WalletScorePenaltyItem],
) -> WalletScoreExplanation:
    weight_sum = score_weight_sum(settings)
    weighted_components = [
        score_component_detail(
            key="pnl",
            label="Profitability",
            score=breakdown.pnl_score,
            weight=settings.scoring_weight_pnl,
            weight_sum=weight_sum,
            detail=("Profitability scores total, average, and median realized trade ROI."),
            items=profitability_score_items(metrics, settings=settings),
        ),
        score_component_detail(
            key="consistency",
            label="Consistency",
            score=breakdown.consistency_score,
            weight=settings.scoring_weight_consistency,
            weight_sum=weight_sum,
            detail=(
                "Consistency scores repeatability and evenness, not profitability or win rate."
            ),
            items=consistency_score_items(metrics, settings=settings),
        ),
        score_component_detail(
            key="risk",
            label="Risk",
            score=breakdown.risk_score,
            weight=settings.scoring_weight_risk,
            weight_sum=weight_sum,
            detail=(
                "Risk starts at 100 and subtracts realized and live-state risk "
                "penalties plus forced-exit severity. Severe current drawdown "
                "also caps the final score."
            ),
            items=risk_score_items(metrics, settings=settings),
        ),
        score_component_detail(
            key="copyability",
            label="Copyability",
            score=breakdown.copyability_score,
            weight=settings.scoring_weight_copyability,
            weight_sum=weight_sum,
            detail=(
                "Copyability scores whether the trade set is practical to follow, "
                "including forced-exit fill ratio."
            ),
            items=copyability_score_items(metrics, settings=settings),
        ),
        score_component_detail(
            key="recency",
            label="Recency",
            score=breakdown.recency_score,
            weight=settings.scoring_weight_recency,
            weight_sum=weight_sum,
            detail=("Recency decays as the latest non-liquidation trading fill gets older."),
            items=recency_score_items(metrics, now=now, settings=settings),
        ),
    ]
    gross_component_score = sum(
        ((component.weighted_score or ZERO) for component in weighted_components),
        ZERO,
    )
    gross_score = score_value(gross_component_score)
    final_score_before_cap = score_value(gross_score - breakdown.penalty_score)
    sample_cap = score_sample_cap(
        metrics.trade_count,
        settings.scoring_min_trades,
        max_score=settings.scoring_sample_cap_max_score,
    )
    components = [
        *weighted_components,
        WalletScoreComponentDetail(
            key="penalty",
            label="Penalty",
            score=breakdown.penalty_score,
            weight=None,
            weighted_score=None,
            detail="Penalty is subtracted from the weighted component score.",
            items=[
                WalletScoreDetailItem(
                    key=item.key,
                    label=item.label,
                    value=item.value,
                    value_kind="penalty",
                    score=None,
                    weight=None,
                    contribution=item.value,
                    effect="subtract",
                    detail=item.detail,
                )
                for item in penalty_items
            ],
        ),
    ]
    return WalletScoreExplanation(
        gross_score=gross_score,
        final_score_before_cap=final_score_before_cap,
        sample_cap=sample_cap,
        component_details=components,
    )


def profitability_score_items(
    metrics: WalletScoreMetrics,
    *,
    settings: Settings,
) -> list[WalletScoreDetailItem]:
    roi = profitability_roi(metrics)
    roi_score = profitability_ratio_score(
        roi,
        full_gain=settings.scoring_profitability_roi_full_score_at,
    )
    average_trade_roi_score = profitability_ratio_score(
        metrics.average_trade_roi or ZERO,
        full_gain=settings.scoring_profitability_roi_full_score_at,
    )
    median_trade_roi_score = profitability_ratio_score(
        metrics.median_trade_roi or ZERO,
        full_gain=settings.scoring_profitability_roi_full_score_at,
    )
    weight_sum = score_group_weight_sum(
        settings.scoring_profitability_weight_net_roi,
        settings.scoring_profitability_weight_average_trade_roi,
        settings.scoring_profitability_weight_median_trade_roi,
    )
    wallet_return = wallet_size_adjusted_return(metrics)
    wallet_return_detail = (
        "Reference only. Net realized PnL divided by current perp equity can be "
        "distorted by deposits or withdrawals, so it is not scored."
        if wallet_return is not None
        else (
            "Reference only. Current perp equity is missing or zero, and current "
            "equity is not used for historical profitability scoring."
        )
    )
    return [
        detail_item(
            key="roi",
            label="ROI",
            value=roi,
            value_kind="percent",
            score=roi_score,
            weight=score_group_weight(
                settings.scoring_profitability_weight_net_roi,
                weight_sum,
            ),
            detail=(
                "Net realized PnL divided by reconstructed realized entry notional, "
                "including realized partial closes from still-open trades. "
                "0% or lower gets zero score, "
                f"{settings.scoring_profitability_roi_full_score_at:.2%} reaches full score."
            ),
        ),
        detail_item(
            key="average_trade_roi",
            label="Average trade ROI",
            value=metrics.average_trade_roi,
            value_kind="percent",
            score=average_trade_roi_score,
            weight=score_group_weight(
                settings.scoring_profitability_weight_average_trade_roi,
                weight_sum,
            ),
            detail=(
                "Average per-trade ROI after applying the configured per-trade cap. "
                "This reduces outlier impact from tiny or abnormal trades. "
                "0% or lower gets zero score, "
                f"{settings.scoring_profitability_roi_full_score_at:.2%} reaches full score."
            ),
        ),
        detail_item(
            key="median_trade_roi",
            label="Median trade ROI",
            value=metrics.median_trade_roi,
            value_kind="percent",
            score=median_trade_roi_score,
            weight=score_group_weight(
                settings.scoring_profitability_weight_median_trade_roi,
                weight_sum,
            ),
            detail=(
                "Median per-trade ROI. This checks whether the typical trade is "
                "profitable. 0% or lower gets zero score, "
                f"{settings.scoring_profitability_roi_full_score_at:.2%} reaches full score."
            ),
        ),
        reference_detail_item(
            key="wallet_size_adjusted_return",
            label="Wallet-size adjusted return",
            value=wallet_return,
            value_kind="percent",
            detail=wallet_return_detail,
        ),
        reference_detail_item(
            key="net_pnl_reference",
            label="Net PnL",
            value=metrics.net_pnl_usd,
            value_kind="currency",
            detail=(
                "Reference only. Absolute dollar PnL is not scored. Includes realized "
                "partial closes from still-open trades."
            ),
        ),
    ]


def consistency_score_items(
    metrics: WalletScoreMetrics,
    *,
    settings: Settings,
) -> list[WalletScoreDetailItem]:
    distribution_ratio = profit_distribution_ratio(metrics)
    distribution_score = profit_distribution_consistency_score(
        distribution_ratio,
        settings=settings,
    )
    largest_win_score = largest_win_dependency_score(
        metrics.largest_win_profit_share,
        settings=settings,
    )
    trade_roi_stability_score = roi_stability_score(
        metrics.trade_roi_stddev,
        full_score_at_or_below=(
            settings.scoring_consistency_trade_roi_stddev_full_score_at_or_below
        ),
        zero_score_at_or_above=(
            settings.scoring_consistency_trade_roi_stddev_zero_score_at_or_above
        ),
    )
    downside_stability_score = roi_stability_score(
        metrics.downside_trade_roi_stddev,
        full_score_at_or_below=(
            settings.scoring_consistency_downside_stddev_full_score_at_or_below
        ),
        zero_score_at_or_above=(
            settings.scoring_consistency_downside_stddev_zero_score_at_or_above
        ),
    )
    active_day_score = active_day_regularity_score(metrics, settings=settings)
    max_gap_score = max_inactive_gap_score(metrics.max_inactive_gap_days, settings=settings)
    weight_sum = score_group_weight_sum(
        settings.scoring_consistency_weight_profit_distribution,
        settings.scoring_consistency_weight_largest_win_dependency,
        settings.scoring_consistency_weight_trade_roi_stability,
        settings.scoring_consistency_weight_downside_stability,
        settings.scoring_consistency_weight_active_day_regularity,
        settings.scoring_consistency_weight_max_inactive_gap,
    )
    return [
        detail_item(
            key="profit_distribution",
            label="Profit distribution",
            value=distribution_ratio,
            value_kind="percent",
            score=distribution_score,
            weight=score_group_weight(
                settings.scoring_consistency_weight_profit_distribution,
                weight_sum,
            ),
            detail=(
                "Effective winning trades divided by winning trades. "
                f"{settings.scoring_consistency_profit_distribution_full_score_ratio:.0%} "
                "reaches full score."
            ),
        ),
        detail_item(
            key="largest_win_dependency",
            label="Largest win dependency",
            value=metrics.largest_win_profit_share,
            value_kind="percent",
            score=largest_win_score,
            weight=score_group_weight(
                settings.scoring_consistency_weight_largest_win_dependency,
                weight_sum,
            ),
            detail="Largest winning trade as share of gross profit. Lower is better.",
        ),
        detail_item(
            key="trade_roi_stability",
            label="Trade ROI stability",
            value=metrics.trade_roi_stddev,
            value_kind="percent",
            score=trade_roi_stability_score,
            weight=score_group_weight(
                settings.scoring_consistency_weight_trade_roi_stability,
                weight_sum,
            ),
            detail="Population standard deviation of closed-trade ROI. Lower is better.",
        ),
        detail_item(
            key="downside_stability",
            label="Downside stability",
            value=metrics.downside_trade_roi_stddev,
            value_kind="percent",
            score=downside_stability_score,
            weight=score_group_weight(
                settings.scoring_consistency_weight_downside_stability,
                weight_sum,
            ),
            detail="Population standard deviation of losing closed-trade ROI. Lower is better.",
        ),
        detail_item(
            key="active_day_regularity",
            label="Active day regularity",
            value=active_day_ratio(metrics, settings=settings),
            value_kind="percent",
            score=active_day_score,
            weight=score_group_weight(
                settings.scoring_consistency_weight_active_day_regularity,
                weight_sum,
            ),
            detail=(
                "Closed-trade active days divided by scoring window. "
                f"{settings.scoring_consistency_active_day_full_score_ratio:.0%} "
                "reaches full score."
            ),
        ),
        detail_item(
            key="max_inactive_gap",
            label="Max inactive gap",
            value=(
                Decimal(metrics.max_inactive_gap_days)
                if metrics.max_inactive_gap_days is not None
                else None
            ),
            value_kind="days",
            score=max_gap_score,
            weight=score_group_weight(
                settings.scoring_consistency_weight_max_inactive_gap,
                weight_sum,
            ),
            detail=(
                "Largest gap between closed-trade active days. "
                f"{settings.scoring_consistency_max_inactive_gap_zero_score_days} "
                "days or more scores zero."
            ),
        ),
    ]


def risk_score_items(
    metrics: WalletScoreMetrics,
    *,
    settings: Settings,
) -> list[WalletScoreDetailItem]:
    closed_trade_count = metrics.profitable_trade_count + metrics.losing_trade_count
    drawdown_base = max_decimal(metrics.gross_profit_usd, abs(metrics.net_pnl_usd), ONE)
    loss_ratio = metrics.gross_loss_usd / max_decimal(metrics.gross_profit_usd, ONE)
    realized_drawdown = metrics.max_drawdown_usd / drawdown_base
    current_drawdown = metrics.current_drawdown_pct or ZERO
    current_drawdown_penalty = current_drawdown_risk_penalty(
        current_drawdown,
        settings=settings,
    )
    open_stress_penalty = min_decimal(
        (metrics.open_position_stress_pct or ZERO)
        * settings.scoring_open_position_stress_penalty_max,
        settings.scoring_open_position_stress_penalty_max,
    )
    live_penalty = max_decimal(current_drawdown_penalty, open_stress_penalty)
    forced_exit_penalty = forced_exit_severity_penalty(metrics, settings=settings)
    live_risk_cap = current_drawdown_score_cap(current_drawdown, settings=settings)
    losing_rate = (
        Decimal(metrics.losing_trade_count) / Decimal(closed_trade_count)
        if closed_trade_count > 0
        else ZERO
    )
    return [
        penalty_detail_item(
            key="loss_ratio",
            label="Loss ratio",
            value=loss_ratio,
            value_kind="percent",
            penalty=min_decimal(
                loss_ratio * settings.scoring_risk_loss_ratio_penalty_per_ratio,
                settings.scoring_risk_loss_ratio_penalty_max,
            ),
            detail="Gross loss divided by gross profit.",
        ),
        penalty_detail_item(
            key="realized_drawdown",
            label="Realized drawdown",
            value=realized_drawdown,
            value_kind="percent",
            penalty=min_decimal(
                realized_drawdown * settings.scoring_risk_realized_drawdown_penalty_per_ratio,
                settings.scoring_risk_realized_drawdown_penalty_max,
            ),
            detail="Closed-trade drawdown divided by realized drawdown base.",
        ),
        reference_detail_item(
            key="current_drawdown_candidate",
            label="Current drawdown candidate",
            value=metrics.current_drawdown_pct,
            value_kind="percent",
            detail=(
                "Candidate live penalty before comparing with margin stress. "
                "Penalty starts at "
                f"{settings.scoring_current_drawdown_penalty_start_ratio} and "
                "reaches max at "
                f"{settings.scoring_current_drawdown_full_penalty_ratio}: "
                f"{score_value(current_drawdown_penalty)} points."
            ),
        ),
        reference_detail_item(
            key="open_position_stress_candidate",
            label="Open-position stress candidate",
            value=metrics.open_position_stress_pct,
            value_kind="percent",
            detail=(
                "Candidate live penalty before comparing with current drawdown: "
                f"{score_value(open_stress_penalty)} points."
            ),
        ),
        penalty_detail_item(
            key="live_risk_penalty",
            label="Live risk penalty",
            value=live_penalty,
            value_kind="penalty",
            penalty=live_penalty,
            detail=(
                "Actual live-state risk penalty, using the larger candidate so the "
                "same open risk is not double-counted."
            ),
        ),
        penalty_detail_item(
            key="forced_exit_severity",
            label="Forced exit severity",
            value=forced_exit_severity_ratio(metrics),
            value_kind="percent",
            penalty=forced_exit_penalty,
            detail=(
                "Liquidation-tagged close notional divided by reconstructed entry "
                "notional. This counts forced exits as risk even when the "
                "reconstructed trade finished profitable."
            ),
        ),
        reference_detail_item(
            key="live_risk_score_cap",
            label="Live risk score cap",
            value=live_risk_cap,
            value_kind="score",
            detail=(
                "Caps final score when current drawdown is above "
                f"{settings.scoring_current_drawdown_score_cap_start_ratio}. "
                "The cap reaches zero at "
                f"{settings.scoring_current_drawdown_score_cap_zero_ratio}."
            ),
        ),
        penalty_detail_item(
            key="losing_rate",
            label="Losing trade rate",
            value=losing_rate,
            value_kind="percent",
            penalty=losing_rate * settings.scoring_risk_losing_trade_rate_penalty_per_ratio,
            detail="Losing closed trades divided by all closed trades.",
        ),
    ]


def copyability_score_items(
    metrics: WalletScoreMetrics,
    *,
    settings: Settings,
) -> list[WalletScoreDetailItem]:
    copyable_trade_score = copyable_trade_ratio_score(metrics.copyable_trade_ratio)
    median_notional_score = trade_notional_score(
        metrics.median_trade_notional_usd,
        settings=settings,
    )
    p25_notional_score = trade_notional_score(
        metrics.p25_trade_notional_usd,
        settings=settings,
    )
    execution_score = execution_simplicity_score(
        metrics.average_fills_per_trade,
        settings=settings,
    )
    forced_exit_score = forced_exit_fill_ratio_score(metrics, settings=settings)
    weight_sum = score_group_weight_sum(
        settings.scoring_copyability_weight_copyable_trade_ratio,
        settings.scoring_copyability_weight_median_trade_notional,
        settings.scoring_copyability_weight_p25_trade_notional,
        settings.scoring_copyability_weight_execution_simplicity,
        settings.scoring_copyability_weight_forced_exit_fill_ratio,
    )
    return [
        detail_item(
            key="copyable_trade_ratio",
            label="Copyable trade ratio",
            value=metrics.copyable_trade_ratio,
            value_kind="percent",
            score=copyable_trade_score,
            weight=score_group_weight(
                settings.scoring_copyability_weight_copyable_trade_ratio,
                weight_sum,
            ),
            detail=(
                "Share of closed trades with reconstructed entry notional at or above "
                f"{settings.scoring_copyability_copyable_trade_min_notional_usd} USD."
            ),
        ),
        detail_item(
            key="median_trade_notional",
            label="Median trade notional",
            value=metrics.median_trade_notional_usd,
            value_kind="currency",
            score=median_notional_score,
            weight=score_group_weight(
                settings.scoring_copyability_weight_median_trade_notional,
                weight_sum,
            ),
            detail="Median reconstructed entry notional per closed trade.",
        ),
        detail_item(
            key="p25_trade_notional",
            label="P25 trade notional",
            value=metrics.p25_trade_notional_usd,
            value_kind="currency",
            score=p25_notional_score,
            weight=score_group_weight(
                settings.scoring_copyability_weight_p25_trade_notional,
                weight_sum,
            ),
            detail="25th percentile reconstructed entry notional per closed trade.",
        ),
        detail_item(
            key="execution_simplicity",
            label="Execution simplicity",
            value=metrics.average_fills_per_trade,
            value_kind="number",
            score=execution_score,
            weight=score_group_weight(
                settings.scoring_copyability_weight_execution_simplicity,
                weight_sum,
            ),
            detail=("Average entry and close fills per closed trade. Lower is easier to follow."),
        ),
        detail_item(
            key="forced_exit_fill_ratio",
            label="Forced exit fill ratio",
            value=forced_exit_fill_ratio(metrics),
            value_kind="percent",
            score=forced_exit_score,
            weight=score_group_weight(
                settings.scoring_copyability_weight_forced_exit_fill_ratio,
                weight_sum,
            ),
            detail=(
                "Liquidation-tagged reconstructed close fills divided by all "
                "reconstructed close fills. "
                f"{settings.scoring_copyability_forced_exit_fill_ratio_zero_score_ratio:.0%} "
                "or more scores zero for this input."
            ),
        ),
    ]


def recency_score_items(
    metrics: WalletScoreMetrics,
    *,
    now: datetime,
    settings: Settings,
) -> list[WalletScoreDetailItem]:
    age_days: Decimal | None = None
    if metrics.last_trade_time_ms is not None:
        age_ms = max(0, timestamp_ms(now) - int(metrics.last_trade_time_ms))
        age_days = Decimal(age_ms) / Decimal(86_400_000)
    return [
        detail_item(
            key="latest_trade_age",
            label="Latest activity age",
            value=age_days,
            value_kind="days",
            score=calculate_recency_score(
                metrics.last_trade_time_ms,
                now=now,
                stale_days=settings.scoring_stale_days,
            ),
            weight=ONE,
            detail=(
                f"Decays to zero after {settings.scoring_stale_days} days without a "
                "non-liquidation trading fill."
            ),
        )
    ]


def score_component_detail(
    *,
    key: str,
    label: str,
    score: Decimal,
    weight: Decimal,
    weight_sum: Decimal,
    detail: str,
    items: list[WalletScoreDetailItem],
) -> WalletScoreComponentDetail:
    effective_weight = weight / weight_sum if weight_sum > ZERO else ZERO
    return WalletScoreComponentDetail(
        key=key,
        label=label,
        score=score_value(score),
        weight=effective_weight,
        weighted_score=score_value(score * effective_weight),
        detail=detail,
        items=items,
    )


def detail_item(
    *,
    key: str,
    label: str,
    value: Decimal | None,
    value_kind: str,
    score: Decimal,
    weight: Decimal,
    detail: str,
) -> WalletScoreDetailItem:
    return WalletScoreDetailItem(
        key=key,
        label=label,
        value=value,
        value_kind=value_kind,
        score=score_value(score),
        weight=weight,
        contribution=score_value(score * weight),
        effect="add",
        detail=detail,
    )


def penalty_detail_item(
    *,
    key: str,
    label: str,
    value: Decimal | None,
    value_kind: str,
    penalty: Decimal,
    detail: str,
) -> WalletScoreDetailItem:
    return WalletScoreDetailItem(
        key=key,
        label=label,
        value=value,
        value_kind=value_kind,
        score=None,
        weight=None,
        contribution=score_value(penalty),
        effect="subtract",
        detail=detail,
    )


def reference_detail_item(
    *,
    key: str,
    label: str,
    value: Decimal | None,
    value_kind: str,
    detail: str,
) -> WalletScoreDetailItem:
    return WalletScoreDetailItem(
        key=key,
        label=label,
        value=value,
        value_kind=value_kind,
        score=None,
        weight=None,
        contribution=None,
        effect="reference",
        detail=detail,
    )


def score_weight_sum(settings: Settings) -> Decimal:
    weight_sum = (
        settings.scoring_weight_pnl
        + settings.scoring_weight_consistency
        + settings.scoring_weight_risk
        + settings.scoring_weight_copyability
        + settings.scoring_weight_recency
    )
    return weight_sum if weight_sum > ZERO else ONE


def score_group_weight_sum(*weights: Decimal) -> Decimal:
    weight_sum = sum(weights, ZERO)
    return weight_sum if weight_sum > ZERO else ONE


def score_group_weight(weight: Decimal, weight_sum: Decimal) -> Decimal:
    if weight_sum <= ZERO:
        return ZERO
    return weight / weight_sum


def weighted_score(items: tuple[tuple[Decimal, Decimal], ...]) -> Decimal:
    weight_sum = score_group_weight_sum(*(weight for _, weight in items))
    return score_value(sum((score * weight for score, weight in items), ZERO) / weight_sum)


def score_sample_cap(
    trade_count: int,
    min_trades: int,
    *,
    max_score: Decimal,
) -> Decimal | None:
    if trade_count >= min_trades:
        return None
    min_trade_count = max(min_trades, 1)
    return score_value(Decimal(trade_count) / Decimal(min_trade_count) * max_score)


def calculate_profitability_score(
    metrics: WalletScoreMetrics,
    *,
    settings: Settings,
) -> Decimal:
    net_roi_score = profitability_ratio_score(
        profitability_roi(metrics),
        full_gain=settings.scoring_profitability_roi_full_score_at,
    )
    average_trade_roi_score = profitability_ratio_score(
        metrics.average_trade_roi or ZERO,
        full_gain=settings.scoring_profitability_roi_full_score_at,
    )
    median_trade_roi_score = profitability_ratio_score(
        metrics.median_trade_roi or ZERO,
        full_gain=settings.scoring_profitability_roi_full_score_at,
    )
    return weighted_score(
        (
            (net_roi_score, settings.scoring_profitability_weight_net_roi),
            (
                average_trade_roi_score,
                settings.scoring_profitability_weight_average_trade_roi,
            ),
            (median_trade_roi_score, settings.scoring_profitability_weight_median_trade_roi),
        )
    )


def calculate_pnl_score(
    net_pnl_usd: Decimal,
    notional_usd: Decimal,
    *,
    settings: Settings,
) -> Decimal:
    roi = net_pnl_usd / notional_usd if notional_usd > ZERO else ZERO
    return profitability_ratio_score(
        roi,
        full_gain=settings.scoring_profitability_roi_full_score_at,
    )


def profitability_roi(metrics: WalletScoreMetrics) -> Decimal:
    if metrics.total_notional_usd <= ZERO:
        return ZERO
    return metrics.net_pnl_usd / metrics.total_notional_usd


def wallet_size_adjusted_return(metrics: WalletScoreMetrics) -> Decimal | None:
    if metrics.current_account_value_usd is None or metrics.current_account_value_usd <= ZERO:
        return None
    return metrics.net_pnl_usd / metrics.current_account_value_usd


def source_trade_roi_values(trades: ReconstructedWalletTrades) -> list[Decimal]:
    return [
        item.net_pnl_usd / item.entry_notional_usd
        for item in trades.items
        if item.status == "closed" and item.entry_notional_usd > ZERO
    ]


def realized_source_trade_roi_values(trades: ReconstructedWalletTrades) -> list[Decimal]:
    values: list[Decimal] = []
    for item in trades.items:
        if item.status == "closed":
            if item.entry_notional_usd > ZERO:
                values.append(item.net_pnl_usd / item.entry_notional_usd)
            continue
        realized_entry_notional_usd = realized_entry_notional_for_trade(item)
        if item.close_fill_count > 0 and realized_entry_notional_usd > ZERO:
            values.append(item.net_pnl_usd / realized_entry_notional_usd)
    return values


def source_trade_notional_values(trades: ReconstructedWalletTrades) -> list[Decimal]:
    return [
        item.entry_notional_usd
        for item in trades.items
        if item.status == "closed" and item.entry_notional_usd > ZERO
    ]


def copyable_trade_ratio(
    trade_notional_values: list[Decimal],
    *,
    settings: Settings,
) -> Decimal | None:
    if not trade_notional_values:
        return None
    copyable_count = sum(
        1
        for notional_usd in trade_notional_values
        if notional_usd >= settings.scoring_copyability_copyable_trade_min_notional_usd
    )
    return Decimal(copyable_count) / Decimal(len(trade_notional_values))


def average_fills_per_trade(trades: ReconstructedWalletTrades) -> Decimal | None:
    closed_items = [item for item in trades.items if item.status == "closed"]
    if not closed_items:
        return None
    total_fills = sum(
        (item.entry_fill_count + item.close_fill_count for item in closed_items),
        0,
    )
    return Decimal(total_fills) / Decimal(len(closed_items))


def largest_win_profit_share(trades: ReconstructedWalletTrades) -> Decimal | None:
    if trades.gross_profit_usd <= ZERO or not trades.winning_trade_pnls:
        return None
    largest_win = max((value for value in trades.winning_trade_pnls if value > ZERO), default=ZERO)
    if largest_win <= ZERO:
        return None
    return min_decimal(largest_win / trades.gross_profit_usd, ONE)


def standard_deviation_decimal(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    if len(values) == 1:
        return ZERO
    mean = sum(values, ZERO) / Decimal(len(values))
    variance = sum(((value - mean) * (value - mean) for value in values), ZERO) / Decimal(
        len(values)
    )
    if variance <= ZERO:
        return ZERO
    return variance.sqrt()


def max_inactive_gap_days(active_days: set[date]) -> int | None:
    if len(active_days) < 2:
        return None
    ordered_days = sorted(active_days)
    gaps = [
        max(0, (current_day - previous_day).days - 1)
        for previous_day, current_day in zip(ordered_days, ordered_days[1:], strict=False)
    ]
    return max(gaps, default=0)


def capped_trade_roi_values(
    values: list[Decimal],
    *,
    settings: Settings,
) -> list[Decimal]:
    return [
        clamp_decimal(
            value,
            settings.scoring_profitability_average_trade_roi_cap_min,
            settings.scoring_profitability_average_trade_roi_cap_max,
        )
        for value in values
    ]


def average_decimal(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, ZERO) / Decimal(len(values))


def median_decimal(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")


def percentile_decimal(values: list[Decimal], percentile: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    clamped_percentile = clamp_decimal(percentile, ZERO, ONE)
    position = Decimal(len(ordered) - 1) * clamped_percentile
    lower_index = int(position.to_integral_value(rounding=ROUND_FLOOR))
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - Decimal(lower_index)
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def profitability_ratio_score(
    value: Decimal,
    *,
    full_gain: Decimal,
) -> Decimal:
    if value <= ZERO or full_gain <= ZERO:
        return ZERO
    return score_value(min_decimal(value / full_gain, ONE) * HUNDRED)


def calculate_consistency_score(
    *,
    metrics: WalletScoreMetrics,
    settings: Settings,
) -> Decimal:
    distribution_score = profit_distribution_consistency_score(
        profit_distribution_ratio(metrics),
        settings=settings,
    )
    largest_win_score = largest_win_dependency_score(
        metrics.largest_win_profit_share,
        settings=settings,
    )
    trade_roi_stability_score = roi_stability_score(
        metrics.trade_roi_stddev,
        full_score_at_or_below=(
            settings.scoring_consistency_trade_roi_stddev_full_score_at_or_below
        ),
        zero_score_at_or_above=(
            settings.scoring_consistency_trade_roi_stddev_zero_score_at_or_above
        ),
    )
    downside_stability_score = roi_stability_score(
        metrics.downside_trade_roi_stddev,
        full_score_at_or_below=(
            settings.scoring_consistency_downside_stddev_full_score_at_or_below
        ),
        zero_score_at_or_above=(
            settings.scoring_consistency_downside_stddev_zero_score_at_or_above
        ),
    )
    active_day_score = active_day_regularity_score(metrics, settings=settings)
    max_gap_score = max_inactive_gap_score(metrics.max_inactive_gap_days, settings=settings)
    return weighted_score(
        (
            (
                distribution_score,
                settings.scoring_consistency_weight_profit_distribution,
            ),
            (
                largest_win_score,
                settings.scoring_consistency_weight_largest_win_dependency,
            ),
            (
                trade_roi_stability_score,
                settings.scoring_consistency_weight_trade_roi_stability,
            ),
            (
                downside_stability_score,
                settings.scoring_consistency_weight_downside_stability,
            ),
            (
                active_day_score,
                settings.scoring_consistency_weight_active_day_regularity,
            ),
            (
                max_gap_score,
                settings.scoring_consistency_weight_max_inactive_gap,
            ),
        )
    )


def profit_distribution_ratio(metrics: WalletScoreMetrics) -> Decimal | None:
    if (
        metrics.effective_winning_trade_count is None
        or metrics.effective_winning_trade_count <= ZERO
        or metrics.profitable_trade_count <= 0
    ):
        return None
    return min_decimal(
        metrics.effective_winning_trade_count / Decimal(metrics.profitable_trade_count),
        ONE,
    )


def profit_distribution_consistency_score(
    distribution_ratio: Decimal | None,
    *,
    settings: Settings,
) -> Decimal:
    if distribution_ratio is None or distribution_ratio <= ZERO:
        return ZERO
    return score_value(
        min_decimal(
            distribution_ratio / settings.scoring_consistency_profit_distribution_full_score_ratio,
            ONE,
        )
        * HUNDRED
    )


def largest_win_dependency_score(
    largest_win_profit_share: Decimal | None,
    *,
    settings: Settings,
) -> Decimal:
    return inverse_range_score(
        largest_win_profit_share,
        settings.scoring_consistency_largest_win_full_score_at_or_below,
        settings.scoring_consistency_largest_win_zero_score_at_or_above,
    )


def roi_stability_score(
    roi_stddev: Decimal | None,
    *,
    full_score_at_or_below: Decimal,
    zero_score_at_or_above: Decimal,
) -> Decimal:
    return inverse_range_score(
        roi_stddev,
        full_score_at_or_below,
        zero_score_at_or_above,
    )


def active_day_ratio(metrics: WalletScoreMetrics, *, settings: Settings) -> Decimal:
    window_days = max(settings.scoring_window_days, 1)
    return min_decimal(Decimal(metrics.active_days) / Decimal(window_days), ONE)


def active_day_regularity_score(
    metrics: WalletScoreMetrics,
    *,
    settings: Settings,
) -> Decimal:
    return score_value(
        min_decimal(
            active_day_ratio(metrics, settings=settings)
            / settings.scoring_consistency_active_day_full_score_ratio,
            ONE,
        )
        * HUNDRED
    )


def max_inactive_gap_score(
    max_inactive_gap_days: int | None,
    *,
    settings: Settings,
) -> Decimal:
    if max_inactive_gap_days is None:
        return ZERO
    return inverse_range_score(
        Decimal(max_inactive_gap_days),
        Decimal(settings.scoring_consistency_max_inactive_gap_full_score_days),
        Decimal(settings.scoring_consistency_max_inactive_gap_zero_score_days),
    )


def inverse_range_score(
    value: Decimal | None,
    full_score_at_or_below: Decimal,
    zero_score_at_or_above: Decimal,
) -> Decimal:
    if value is None:
        return ZERO
    if value <= full_score_at_or_below:
        return HUNDRED
    if value >= zero_score_at_or_above:
        return ZERO
    span = zero_score_at_or_above - full_score_at_or_below
    if span <= ZERO:
        return ZERO
    return score_value((zero_score_at_or_above - value) / span * HUNDRED)


def calculate_open_position_stress_pct(
    *,
    current_drawdown_pct: Decimal | None,
    current_margin_usage_pct: Decimal | None,
    current_notional_exposure_pct: Decimal | None,
    notional_full_ratio: Decimal,
) -> Decimal:
    notional_stress = ZERO
    if notional_full_ratio > ZERO and current_notional_exposure_pct is not None:
        notional_stress = current_notional_exposure_pct / notional_full_ratio

    stress = max_decimal(
        current_drawdown_pct or ZERO,
        current_margin_usage_pct or ZERO,
        notional_stress,
    )
    return min_decimal(stress, ONE).quantize(RATIO_QUANT)


def calculate_risk_score(
    *,
    metrics: WalletScoreMetrics,
    closed_trade_count: int,
    drawdown_base: Decimal,
    settings: Settings,
) -> Decimal:
    loss_ratio = metrics.gross_loss_usd / max_decimal(metrics.gross_profit_usd, ONE)
    drawdown_ratio = metrics.max_drawdown_usd / drawdown_base
    current_drawdown_ratio = metrics.current_drawdown_pct or ZERO
    current_drawdown_penalty = current_drawdown_risk_penalty(
        current_drawdown_ratio,
        settings=settings,
    )
    open_position_stress_penalty = min_decimal(
        (metrics.open_position_stress_pct or ZERO)
        * settings.scoring_open_position_stress_penalty_max,
        settings.scoring_open_position_stress_penalty_max,
    )
    losing_rate = (
        Decimal(metrics.losing_trade_count) / Decimal(closed_trade_count)
        if closed_trade_count > 0
        else ZERO
    )
    penalty = (
        min_decimal(
            loss_ratio * settings.scoring_risk_loss_ratio_penalty_per_ratio,
            settings.scoring_risk_loss_ratio_penalty_max,
        )
        + min_decimal(
            drawdown_ratio * settings.scoring_risk_realized_drawdown_penalty_per_ratio,
            settings.scoring_risk_realized_drawdown_penalty_max,
        )
        + max_decimal(current_drawdown_penalty, open_position_stress_penalty)
        + forced_exit_severity_penalty(metrics, settings=settings)
        + losing_rate * settings.scoring_risk_losing_trade_rate_penalty_per_ratio
    )
    return score_value(HUNDRED - penalty)


def current_drawdown_risk_penalty(
    current_drawdown_pct: Decimal | None,
    *,
    settings: Settings,
) -> Decimal:
    if current_drawdown_pct is None:
        return ZERO
    start_ratio = settings.scoring_current_drawdown_penalty_start_ratio
    full_ratio = settings.scoring_current_drawdown_full_penalty_ratio
    if current_drawdown_pct <= start_ratio:
        return ZERO
    if current_drawdown_pct >= full_ratio:
        return settings.scoring_current_drawdown_penalty_max
    span = full_ratio - start_ratio
    if span <= ZERO:
        return settings.scoring_current_drawdown_penalty_max
    return (
        (current_drawdown_pct - start_ratio) / span * settings.scoring_current_drawdown_penalty_max
    )


def current_drawdown_score_cap(
    current_drawdown_pct: Decimal | None,
    *,
    settings: Settings,
) -> Decimal | None:
    if current_drawdown_pct is None:
        return None
    if current_drawdown_pct <= settings.scoring_current_drawdown_score_cap_start_ratio:
        return None
    return inverse_range_score(
        current_drawdown_pct,
        settings.scoring_current_drawdown_score_cap_start_ratio,
        settings.scoring_current_drawdown_score_cap_zero_ratio,
    )


def calculate_copyability_score(
    *,
    metrics: WalletScoreMetrics,
    settings: Settings,
) -> Decimal:
    copyable_trade_score = copyable_trade_ratio_score(metrics.copyable_trade_ratio)
    median_notional_score = trade_notional_score(
        metrics.median_trade_notional_usd,
        settings=settings,
    )
    p25_notional_score = trade_notional_score(
        metrics.p25_trade_notional_usd,
        settings=settings,
    )
    execution_score = execution_simplicity_score(
        metrics.average_fills_per_trade,
        settings=settings,
    )
    forced_exit_score = forced_exit_fill_ratio_score(metrics, settings=settings)
    return weighted_score(
        (
            (
                copyable_trade_score,
                settings.scoring_copyability_weight_copyable_trade_ratio,
            ),
            (
                median_notional_score,
                settings.scoring_copyability_weight_median_trade_notional,
            ),
            (
                p25_notional_score,
                settings.scoring_copyability_weight_p25_trade_notional,
            ),
            (
                execution_score,
                settings.scoring_copyability_weight_execution_simplicity,
            ),
            (
                forced_exit_score,
                settings.scoring_copyability_weight_forced_exit_fill_ratio,
            ),
        )
    )


def copyable_trade_ratio_score(copyable_ratio: Decimal | None) -> Decimal:
    if copyable_ratio is None:
        return ZERO
    return score_value(copyable_ratio * HUNDRED)


def trade_notional_score(
    notional_usd: Decimal | None,
    *,
    settings: Settings,
) -> Decimal:
    if notional_usd is None or notional_usd <= ZERO:
        return ZERO
    min_full = settings.scoring_copyability_trade_notional_min_full_score_usd
    max_full = settings.scoring_copyability_trade_notional_max_full_score_usd
    large_min_score_at = settings.scoring_copyability_trade_notional_too_large_min_score_usd
    if notional_usd < min_full:
        return score_value(
            notional_usd
            / min_full
            * settings.scoring_copyability_trade_notional_too_small_max_score
        )
    if notional_usd <= max_full:
        return HUNDRED
    if notional_usd >= large_min_score_at:
        return settings.scoring_copyability_trade_notional_too_large_min_score
    reduction = (
        (notional_usd - max_full)
        / (large_min_score_at - max_full)
        * (HUNDRED - settings.scoring_copyability_trade_notional_too_large_min_score)
    )
    return score_value(HUNDRED - reduction)


def execution_simplicity_score(
    average_fills_per_trade: Decimal | None,
    *,
    settings: Settings,
) -> Decimal:
    return inverse_range_score(
        average_fills_per_trade,
        settings.scoring_copyability_execution_full_score_fills_per_trade_at_or_below,
        settings.scoring_copyability_execution_zero_score_fills_per_trade_at_or_above,
    )


def forced_exit_fill_ratio(metrics: WalletScoreMetrics) -> Decimal | None:
    if metrics.close_fill_count <= 0:
        return None
    return Decimal(metrics.liquidation_close_fill_count) / Decimal(metrics.close_fill_count)


def forced_exit_fill_ratio_score(
    metrics: WalletScoreMetrics,
    *,
    settings: Settings,
) -> Decimal:
    return inverse_range_score(
        forced_exit_fill_ratio(metrics),
        ZERO,
        settings.scoring_copyability_forced_exit_fill_ratio_zero_score_ratio,
    )


def forced_exit_severity_ratio(metrics: WalletScoreMetrics) -> Decimal:
    if metrics.total_notional_usd <= ZERO:
        return ZERO
    return metrics.liquidation_notional_usd / metrics.total_notional_usd


def forced_exit_severity_penalty(
    metrics: WalletScoreMetrics,
    *,
    settings: Settings,
) -> Decimal:
    severity = forced_exit_severity_ratio(metrics)
    return min_decimal(
        severity
        / settings.scoring_forced_exit_notional_full_ratio
        * settings.scoring_forced_exit_penalty_max,
        settings.scoring_forced_exit_penalty_max,
    )


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
    stale_day_count = max(stale_days, 1)
    return score_value(HUNDRED - (age_days / Decimal(stale_day_count) * HUNDRED))


def calculate_penalty_score(
    *,
    metrics: WalletScoreMetrics,
    min_trades: int,
    recency_score: Decimal,
    settings: Settings,
) -> Decimal:
    penalty = sum(
        (
            item.value
            for item in calculate_penalty_items(
                metrics=metrics,
                min_trades=min_trades,
                recency_score=recency_score,
                settings=settings,
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
    settings: Settings,
) -> list[WalletScorePenaltyItem]:
    if metrics.trade_count <= 0:
        return [
            penalty_item(
                key="no_closed_trades",
                label="No closed trades",
                value=settings.scoring_penalty_no_closed_trades,
                max_value=settings.scoring_penalty_no_closed_trades,
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
        ) * settings.scoring_penalty_low_sample_max

    negative_pnl_penalty = ZERO
    if metrics.net_pnl_usd < ZERO:
        loss_base = abs(metrics.net_pnl_usd) + metrics.gross_profit_usd + ONE
        negative_pnl_penalty = min_decimal(
            abs(metrics.net_pnl_usd) / loss_base * settings.scoring_penalty_negative_pnl_max,
            settings.scoring_penalty_negative_pnl_max,
        )

    stale_penalty = settings.scoring_penalty_stale_recency if recency_score <= ZERO else ZERO

    open_only_penalty = (
        settings.scoring_penalty_open_only
        if metrics.open_trade_count > 0 and metrics.trade_count == 0
        else ZERO
    )
    confidence_trade_count = max(settings.scoring_confidence_target_trades, 1)
    confidence_ratio = min_decimal(
        Decimal(metrics.trade_count) / Decimal(confidence_trade_count),
        ONE,
    )
    confidence_penalty = (ONE - confidence_ratio) * settings.scoring_confidence_penalty_max
    current_drawdown_penalty = (
        settings.scoring_current_drawdown_missing_penalty
        if metrics.current_drawdown_status in {"unavailable", "zero_equity"}
        else ZERO
    )

    return [
        penalty_item(
            key="sample_size",
            label="Low trade sample",
            value=sample_size_penalty,
            max_value=settings.scoring_penalty_low_sample_max,
            detail=(
                f"{metrics.trade_count} closed trades reconstructed; "
                f"target minimum is {min_trades}."
            ),
        ),
        penalty_item(
            key="confidence",
            label="Low confidence",
            value=confidence_penalty,
            max_value=settings.scoring_confidence_penalty_max,
            detail=(
                f"{metrics.trade_count} closed trades reconstructed; confidence target is "
                f"{settings.scoring_confidence_target_trades}."
            ),
        ),
        penalty_item(
            key="current_drawdown_unavailable",
            label="Current drawdown unavailable",
            value=current_drawdown_penalty,
            max_value=settings.scoring_current_drawdown_missing_penalty,
            detail=("Live perp state was unavailable or had zero perp equity during scoring."),
        ),
        penalty_item(
            key="negative_pnl",
            label="Negative net PnL",
            value=negative_pnl_penalty,
            max_value=settings.scoring_penalty_negative_pnl_max,
            detail=(
                f"Net PnL is {metrics.net_pnl_usd}; gross profit is {metrics.gross_profit_usd}."
            ),
        ),
        penalty_item(
            key="stale_recency",
            label="Stale trading",
            value=stale_penalty,
            max_value=settings.scoring_penalty_stale_recency,
            detail=f"Recency score is {recency_score}.",
        ),
        penalty_item(
            key="open_only",
            label="Open-only activity",
            value=open_only_penalty,
            max_value=settings.scoring_penalty_open_only,
            detail=(
                f"{metrics.open_trade_count} open trades and {metrics.trade_count} closed trades."
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
    settings: Settings,
) -> Decimal:
    if trades <= 0:
        return ZERO
    activity_cap = max(settings.scoring_window_score_activity_trade_cap, 1)
    activity_score = score_value(
        Decimal(min(trades, activity_cap)) / Decimal(activity_cap) * HUNDRED
    )
    return weighted_score(
        (
            (
                calculate_pnl_score(net_pnl_usd, notional_usd, settings=settings),
                settings.scoring_window_score_weight_profitability,
            ),
            (activity_score, settings.scoring_window_score_weight_activity),
        )
    )


def calculate_profit_factor(gross_profit_usd: Decimal, gross_loss_usd: Decimal) -> Decimal | None:
    if gross_profit_usd <= ZERO and gross_loss_usd <= ZERO:
        return None
    if gross_loss_usd <= ZERO:
        return Decimal("999") if gross_profit_usd > ZERO else None
    return gross_profit_usd / gross_loss_usd


def base_metrics_from_row(row: Any) -> WalletScoreMetrics:
    return WalletScoreMetrics(
        wallet_address=str(row["wallet_address"]),
        fill_count=int(row["fill_count"] or 0),
        trade_count=0,
        ignored_fill_count=0,
        open_trade_count=0,
        close_fill_count=0,
        unique_coin_count=0,
        active_days=0,
        total_notional_usd=ZERO,
        average_trade_notional_usd=ZERO,
        median_trade_notional_usd=None,
        p25_trade_notional_usd=None,
        copyable_trade_ratio=None,
        average_fills_per_trade=None,
        average_trade_roi=None,
        median_trade_roi=None,
        total_pnl_usd=ZERO,
        total_fee_usd=ZERO,
        net_pnl_usd=ZERO,
        gross_profit_usd=ZERO,
        gross_loss_usd=ZERO,
        profitable_trade_count=0,
        losing_trade_count=0,
        effective_winning_trade_count=None,
        largest_win_profit_share=None,
        trade_roi_stddev=None,
        downside_trade_roi_stddev=None,
        max_inactive_gap_days=None,
        liquidation_fill_count=int(row["liquidation_fill_count"] or 0),
        liquidation_event_count=int(row["liquidation_event_count"] or 0),
        liquidation_trade_count=0,
        liquidation_close_fill_count=0,
        liquidation_notional_usd=decimal_value(row["liquidation_notional_usd"]),
        max_coin_notional_usd=ZERO,
        max_drawdown_usd=ZERO,
        current_perp_equity_usd=None,
        current_account_value_usd=None,
        current_unrealized_pnl_usd=None,
        current_drawdown_pct=None,
        current_margin_usage_pct=None,
        current_notional_exposure_pct=None,
        open_position_stress_pct=None,
        current_drawdown_status="disabled",
        first_trade_time_ms=(
            int(row["first_fill_time_ms"]) if row["first_fill_time_ms"] is not None else None
        ),
        last_trade_time_ms=(
            int(row["last_activity_time_ms"]) if row["last_activity_time_ms"] is not None else None
        ),
        trades_24h=0,
        notional_24h=ZERO,
        net_pnl_24h=ZERO,
        trades_7d=0,
        notional_7d=ZERO,
        net_pnl_7d=ZERO,
    )


def metrics_with_reconstructed_trades(
    base_metrics: WalletScoreMetrics,
    trades: ReconstructedWalletTrades | None,
    *,
    settings: Settings,
) -> WalletScoreMetrics:
    if trades is None:
        return WalletScoreMetrics(
            wallet_address=base_metrics.wallet_address,
            fill_count=base_metrics.fill_count,
            trade_count=0,
            ignored_fill_count=base_metrics.fill_count,
            open_trade_count=0,
            close_fill_count=0,
            unique_coin_count=0,
            active_days=0,
            total_notional_usd=ZERO,
            average_trade_notional_usd=ZERO,
            median_trade_notional_usd=None,
            p25_trade_notional_usd=None,
            copyable_trade_ratio=None,
            average_fills_per_trade=None,
            average_trade_roi=None,
            median_trade_roi=None,
            total_pnl_usd=ZERO,
            total_fee_usd=ZERO,
            net_pnl_usd=ZERO,
            gross_profit_usd=ZERO,
            gross_loss_usd=ZERO,
            profitable_trade_count=0,
            losing_trade_count=0,
            effective_winning_trade_count=None,
            largest_win_profit_share=None,
            trade_roi_stddev=None,
            downside_trade_roi_stddev=None,
            max_inactive_gap_days=None,
            liquidation_fill_count=base_metrics.liquidation_fill_count,
            liquidation_event_count=base_metrics.liquidation_event_count,
            liquidation_trade_count=0,
            liquidation_close_fill_count=0,
            liquidation_notional_usd=ZERO,
            max_coin_notional_usd=ZERO,
            max_drawdown_usd=ZERO,
            current_perp_equity_usd=base_metrics.current_perp_equity_usd,
            current_account_value_usd=base_metrics.current_account_value_usd,
            current_unrealized_pnl_usd=base_metrics.current_unrealized_pnl_usd,
            current_drawdown_pct=base_metrics.current_drawdown_pct,
            current_margin_usage_pct=base_metrics.current_margin_usage_pct,
            current_notional_exposure_pct=base_metrics.current_notional_exposure_pct,
            open_position_stress_pct=base_metrics.open_position_stress_pct,
            current_drawdown_status=base_metrics.current_drawdown_status,
            first_trade_time_ms=base_metrics.first_trade_time_ms,
            last_trade_time_ms=base_metrics.last_trade_time_ms,
            trades_24h=0,
            notional_24h=ZERO,
            net_pnl_24h=ZERO,
            trades_7d=0,
            notional_7d=ZERO,
            net_pnl_7d=ZERO,
        )

    ignored_fill_count = trades.unmatched_close_fill_count + trades.preexisting_open_fill_count
    trade_roi_values = source_trade_roi_values(trades)
    profitability_trade_roi_values = realized_source_trade_roi_values(trades)
    trade_notional_values = source_trade_notional_values(trades)
    downside_trade_roi_values = [value for value in trade_roi_values if value < ZERO]
    return WalletScoreMetrics(
        wallet_address=base_metrics.wallet_address,
        fill_count=base_metrics.fill_count,
        trade_count=trades.closed_trade_count,
        ignored_fill_count=ignored_fill_count,
        open_trade_count=trades.open_trade_count,
        close_fill_count=trades.close_fill_count,
        unique_coin_count=trades.unique_coin_count,
        active_days=trades.active_day_count,
        total_notional_usd=trades.realized_entry_notional_usd,
        average_trade_notional_usd=trades.average_trade_notional_usd,
        median_trade_notional_usd=median_decimal(trade_notional_values),
        p25_trade_notional_usd=percentile_decimal(trade_notional_values, Decimal("0.25")),
        copyable_trade_ratio=copyable_trade_ratio(trade_notional_values, settings=settings),
        average_fills_per_trade=average_fills_per_trade(trades),
        average_trade_roi=average_decimal(
            capped_trade_roi_values(profitability_trade_roi_values, settings=settings)
        ),
        median_trade_roi=median_decimal(profitability_trade_roi_values),
        total_pnl_usd=trades.realized_pnl_usd,
        total_fee_usd=trades.fee_usd,
        net_pnl_usd=trades.net_pnl_usd,
        gross_profit_usd=trades.gross_profit_usd,
        gross_loss_usd=trades.gross_loss_usd,
        profitable_trade_count=trades.winning_trade_count,
        losing_trade_count=trades.losing_trade_count,
        effective_winning_trade_count=trades.effective_winning_trade_count,
        largest_win_profit_share=largest_win_profit_share(trades),
        trade_roi_stddev=standard_deviation_decimal(trade_roi_values),
        downside_trade_roi_stddev=(
            standard_deviation_decimal(downside_trade_roi_values)
            if downside_trade_roi_values
            else ZERO
        ),
        max_inactive_gap_days=max_inactive_gap_days(trades.active_days),
        liquidation_fill_count=base_metrics.liquidation_fill_count,
        liquidation_event_count=base_metrics.liquidation_event_count,
        liquidation_trade_count=trades.liquidation_trade_count,
        liquidation_close_fill_count=trades.liquidation_close_fill_count,
        liquidation_notional_usd=trades.liquidation_notional_usd,
        max_coin_notional_usd=trades.max_coin_notional_usd,
        max_drawdown_usd=trades.max_drawdown_usd,
        current_perp_equity_usd=base_metrics.current_perp_equity_usd,
        current_account_value_usd=base_metrics.current_account_value_usd,
        current_unrealized_pnl_usd=base_metrics.current_unrealized_pnl_usd,
        current_drawdown_pct=base_metrics.current_drawdown_pct,
        current_margin_usage_pct=base_metrics.current_margin_usage_pct,
        current_notional_exposure_pct=base_metrics.current_notional_exposure_pct,
        open_position_stress_pct=base_metrics.open_position_stress_pct,
        current_drawdown_status=base_metrics.current_drawdown_status,
        first_trade_time_ms=trades.first_trade_time_ms,
        last_trade_time_ms=base_metrics.last_trade_time_ms,
        trades_24h=trades.trades_24h,
        notional_24h=trades.notional_24h,
        net_pnl_24h=trades.net_pnl_24h,
        trades_7d=trades.trades_7d,
        notional_7d=trades.notional_7d,
        net_pnl_7d=trades.net_pnl_7d,
    )


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
