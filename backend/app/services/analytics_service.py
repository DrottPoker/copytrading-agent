from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    DiscoveryWalletCandidate,
    PaperCopyAllocation,
    PaperCopyFill,
    PaperPosition,
    SourceTrade,
    WalletFill,
    WalletPosition,
    WalletScore,
    WatchedWallet,
)
from app.schemas.analytics import (
    AnalyticsBucket,
    AnalyticsCoinPerformanceRow,
    AnalyticsDiscoverySourceRow,
    AnalyticsFreshness,
    AnalyticsOverview,
    AnalyticsPaperSourceRow,
    AnalyticsResponse,
    AnalyticsScoreAverages,
    AnalyticsSkipReasonRow,
    AnalyticsSourcePerformanceRow,
    AnalyticsWalletRow,
)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
MS_PER_HOUR = Decimal(60 * 60 * 1000)


async def get_analytics(session: AsyncSession) -> AnalyticsResponse:
    generated_at = datetime.now(UTC)
    cutoff_30d_ms = int((generated_at - timedelta(days=30)).timestamp() * 1000)

    overview = await load_overview(session)
    score_averages = await load_score_averages(session)
    score_buckets = await load_score_buckets(session)
    drawdown_status_buckets = await load_drawdown_status_buckets(session)
    opportunity_wallets = await load_opportunity_wallets(session)
    risk_watchlist = await load_risk_watchlist(session)
    source_performance = await load_source_performance(session, cutoff_ms=cutoff_30d_ms)
    coin_performance = await load_coin_performance(session, cutoff_ms=cutoff_30d_ms)
    paper_sources = await load_paper_sources(session)
    skip_reasons = await load_skip_reasons(session)
    discovery_sources = await load_discovery_sources(session)
    freshness = await load_freshness(session, generated_at=generated_at)

    return AnalyticsResponse(
        overview=overview,
        score_averages=score_averages,
        score_buckets=score_buckets,
        drawdown_status_buckets=drawdown_status_buckets,
        opportunity_wallets=opportunity_wallets,
        risk_watchlist=risk_watchlist,
        source_performance=source_performance,
        coin_performance=coin_performance,
        paper_sources=paper_sources,
        skip_reasons=skip_reasons,
        discovery_sources=discovery_sources,
        freshness=freshness,
    )


async def load_overview(session: AsyncSession) -> AnalyticsOverview:
    wallet_row = (
        (
            await session.execute(
                select(
                    func.count(WatchedWallet.id).label("pool_wallet_count"),
                    func.count(WatchedWallet.id)
                    .filter(WatchedWallet.enabled.is_(True))
                    .label("enabled_wallet_count"),
                )
            )
        )
        .mappings()
        .one()
    )
    score_row = (
        (
            await session.execute(
                select(
                    func.count(WalletScore.id).label("scored_wallet_count"),
                    func.avg(WalletScore.score).label("average_score"),
                )
            )
        )
        .mappings()
        .one()
    )
    allocation_row = (
        (
            await session.execute(
                select(
                    func.count(func.distinct(PaperCopyAllocation.source_wallet))
                    .filter(PaperCopyAllocation.active.is_(True))
                    .label("active_source_count"),
                )
            )
        )
        .mappings()
        .one()
    )
    position_row = (
        (
            await session.execute(
                select(
                    func.count(PaperPosition.id).label("open_paper_position_count"),
                    func.count(func.distinct(PaperPosition.source_wallet)).label(
                        "open_paper_source_count"
                    ),
                    func.coalesce(func.sum(PaperPosition.margin_usd), ZERO).label(
                        "paper_open_margin_usd"
                    ),
                )
            )
        )
        .mappings()
        .one()
    )
    fill_row = (
        (
            await session.execute(
                select(
                    func.count(PaperCopyFill.id).label("fill_count"),
                    func.count(PaperCopyFill.id)
                    .filter(PaperCopyFill.action == "skip")
                    .label("skipped_fill_count"),
                    func.coalesce(func.sum(PaperCopyFill.realized_pnl_usd), ZERO).label(
                        "paper_realized_pnl_usd"
                    ),
                    func.coalesce(func.sum(PaperCopyFill.fee_usd), ZERO).label("paper_fee_usd"),
                )
            )
        )
        .mappings()
        .one()
    )

    enabled_wallet_count = int(wallet_row["enabled_wallet_count"] or 0)
    scored_wallet_count = int(score_row["scored_wallet_count"] or 0)
    fill_count = int(fill_row["fill_count"] or 0)
    skipped_fill_count = int(fill_row["skipped_fill_count"] or 0)

    return AnalyticsOverview(
        pool_wallet_count=int(wallet_row["pool_wallet_count"] or 0),
        enabled_wallet_count=enabled_wallet_count,
        scored_wallet_count=scored_wallet_count,
        scoring_coverage_pct=ratio(scored_wallet_count, enabled_wallet_count),
        average_score=decimal_or_none(score_row["average_score"]),
        active_source_count=int(allocation_row["active_source_count"] or 0),
        open_paper_source_count=int(position_row["open_paper_source_count"] or 0),
        open_paper_position_count=int(position_row["open_paper_position_count"] or 0),
        paper_realized_pnl_usd=decimal_or_zero(fill_row["paper_realized_pnl_usd"]),
        paper_fee_usd=decimal_or_zero(fill_row["paper_fee_usd"]),
        paper_open_margin_usd=decimal_or_zero(position_row["paper_open_margin_usd"]),
        paper_skip_rate_pct=ratio(skipped_fill_count, fill_count),
    )


async def load_score_averages(session: AsyncSession) -> AnalyticsScoreAverages:
    row = (
        (
            await session.execute(
                select(
                    func.avg(WalletScore.score).label("score"),
                    func.avg(WalletScore.pnl_score).label("profitability_score"),
                    func.avg(WalletScore.consistency_score).label("consistency_score"),
                    func.avg(WalletScore.risk_score).label("risk_score"),
                    func.avg(WalletScore.copyability_score).label("copyability_score"),
                    func.avg(WalletScore.recency_score).label("recency_score"),
                    func.avg(WalletScore.penalty_score).label("penalty_score"),
                ).where(WalletScore.score > ZERO)
            )
        )
        .mappings()
        .one()
    )
    return AnalyticsScoreAverages(
        score=decimal_or_none(row["score"]),
        profitability_score=decimal_or_none(row["profitability_score"]),
        consistency_score=decimal_or_none(row["consistency_score"]),
        risk_score=decimal_or_none(row["risk_score"]),
        copyability_score=decimal_or_none(row["copyability_score"]),
        recency_score=decimal_or_none(row["recency_score"]),
        penalty_score=decimal_or_none(row["penalty_score"]),
    )


async def load_score_buckets(session: AsyncSession) -> list[AnalyticsBucket]:
    bucket_expr = case(
        (WalletScore.score >= Decimal("90"), "90-100"),
        (WalletScore.score >= Decimal("80"), "80-89"),
        (WalletScore.score >= Decimal("70"), "70-79"),
        (WalletScore.score >= Decimal("60"), "60-69"),
        (WalletScore.score > ZERO, "1-59"),
        else_="0",
    ).label("bucket")
    rows = (
        (
            await session.execute(
                select(bucket_expr, func.count(WalletScore.id).label("count"))
                .group_by(bucket_expr)
                .order_by(bucket_expr)
            )
        )
        .mappings()
        .all()
    )
    order = ["90-100", "80-89", "70-79", "60-69", "1-59", "0"]
    counts = {str(row["bucket"]): int(row["count"] or 0) for row in rows}
    total = sum(counts.values())
    return [
        AnalyticsBucket(
            label=label,
            count=counts.get(label, 0),
            pct=ratio(counts.get(label, 0), total),
        )
        for label in order
    ]


async def load_drawdown_status_buckets(session: AsyncSession) -> list[AnalyticsBucket]:
    rows = (
        (
            await session.execute(
                select(
                    WalletScore.current_drawdown_status.label("status"),
                    func.count(WalletScore.id).label("count"),
                ).group_by(WalletScore.current_drawdown_status)
            )
        )
        .mappings()
        .all()
    )
    order = ["ok", "unavailable", "zero_equity", "disabled"]
    counts = {str(row["status"]): int(row["count"] or 0) for row in rows}
    total = sum(counts.values())
    return [
        AnalyticsBucket(
            label=label,
            count=counts.get(label, 0),
            pct=ratio(counts.get(label, 0), total),
        )
        for label in order
    ]


async def load_opportunity_wallets(session: AsyncSession) -> list[AnalyticsWalletRow]:
    ranked = ranked_wallet_scores_cte()
    rows = (
        (
            await session.execute(
                select(
                    ranked.c.wallet_address,
                    ranked.c.pool_rank,
                    WatchedWallet.label,
                    WalletScore.score,
                    WalletScore.trade_count,
                    WalletScore.copyable_pnl_usd,
                    WalletScore.win_rate,
                    WalletScore.profit_factor,
                    WalletScore.max_drawdown_pct,
                    WalletScore.current_drawdown_pct,
                    WalletScore.open_position_stress_pct,
                    WalletScore.current_drawdown_status,
                    WatchedWallet.last_seen_fill_at,
                )
                .join(WalletScore, WalletScore.wallet_address == ranked.c.wallet_address)
                .join(WatchedWallet, WatchedWallet.address == ranked.c.wallet_address)
                .where(
                    WatchedWallet.enabled.is_(True),
                    WalletScore.score > ZERO,
                    WalletScore.trade_count >= 5,
                    WalletScore.current_drawdown_status == "ok",
                )
                .order_by(WalletScore.score.desc(), WalletScore.updated_at.desc())
                .limit(12)
            )
        )
        .mappings()
        .all()
    )
    return [wallet_row(row) for row in rows]


async def load_risk_watchlist(session: AsyncSession) -> list[AnalyticsWalletRow]:
    ranked = ranked_wallet_scores_cte()
    rows = (
        (
            await session.execute(
                select(
                    ranked.c.wallet_address,
                    ranked.c.pool_rank,
                    WatchedWallet.label,
                    WalletScore.score,
                    WalletScore.trade_count,
                    WalletScore.copyable_pnl_usd,
                    WalletScore.win_rate,
                    WalletScore.profit_factor,
                    WalletScore.max_drawdown_pct,
                    WalletScore.current_drawdown_pct,
                    WalletScore.open_position_stress_pct,
                    WalletScore.current_drawdown_status,
                    WatchedWallet.last_seen_fill_at,
                )
                .join(WalletScore, WalletScore.wallet_address == ranked.c.wallet_address)
                .join(WatchedWallet, WatchedWallet.address == ranked.c.wallet_address)
                .where(
                    WatchedWallet.enabled.is_(True),
                    WalletScore.score > ZERO,
                    or_(
                        WalletScore.current_drawdown_status != "ok",
                        WalletScore.current_drawdown_pct > ZERO,
                        WalletScore.open_position_stress_pct > ZERO,
                        WalletScore.max_drawdown_pct > Decimal("0.10"),
                    ),
                )
                .order_by(
                    case((WalletScore.current_drawdown_status == "ok", 1), else_=0),
                    WalletScore.current_drawdown_pct.desc().nulls_last(),
                    WalletScore.open_position_stress_pct.desc().nulls_last(),
                    WalletScore.max_drawdown_pct.desc().nulls_last(),
                    WalletScore.score.desc(),
                )
                .limit(12)
            )
        )
        .mappings()
        .all()
    )
    return [wallet_row(row) for row in rows]


async def load_source_performance(
    session: AsyncSession,
    *,
    cutoff_ms: int,
) -> list[AnalyticsSourcePerformanceRow]:
    ranked = ranked_wallet_scores_cte()
    rows = (
        (
            await session.execute(
                select(
                    SourceTrade.wallet_address.label("source_wallet"),
                    WatchedWallet.label.label("source_label"),
                    ranked.c.pool_rank,
                    WalletScore.score,
                    func.count(SourceTrade.id).label("closed_trade_count"),
                    func.coalesce(func.sum(SourceTrade.net_pnl_usd), ZERO).label("net_pnl_usd"),
                    func.coalesce(func.sum(SourceTrade.fee_usd), ZERO).label("fee_usd"),
                    func.coalesce(func.sum(SourceTrade.entry_notional_usd), ZERO).label(
                        "entry_notional_usd"
                    ),
                    func.sum(case((SourceTrade.net_pnl_usd > ZERO, 1), else_=0)).label("wins"),
                    func.avg(SourceTrade.duration_ms).label("average_duration_ms"),
                    func.max(SourceTrade.closed_at_ms).label("last_closed_at_ms"),
                )
                .outerjoin(WatchedWallet, WatchedWallet.address == SourceTrade.wallet_address)
                .outerjoin(WalletScore, WalletScore.wallet_address == SourceTrade.wallet_address)
                .outerjoin(ranked, ranked.c.wallet_address == SourceTrade.wallet_address)
                .where(SourceTrade.status == "closed", SourceTrade.closed_at_ms >= cutoff_ms)
                .group_by(
                    SourceTrade.wallet_address,
                    WatchedWallet.label,
                    ranked.c.pool_rank,
                    WalletScore.score,
                )
                .order_by(func.sum(SourceTrade.net_pnl_usd).desc())
                .limit(12)
            )
        )
        .mappings()
        .all()
    )
    return [
        AnalyticsSourcePerformanceRow(
            source_wallet=str(row["source_wallet"]),
            source_label=row["source_label"],
            pool_rank=int_or_none(row["pool_rank"]),
            score=decimal_or_none(row["score"]),
            closed_trade_count=int(row["closed_trade_count"] or 0),
            win_rate=ratio(int(row["wins"] or 0), int(row["closed_trade_count"] or 0)),
            net_pnl_usd=decimal_or_zero(row["net_pnl_usd"]),
            fee_usd=decimal_or_zero(row["fee_usd"]),
            entry_notional_usd=decimal_or_zero(row["entry_notional_usd"]),
            roi_pct=ratio_decimal(
                decimal_or_zero(row["net_pnl_usd"]),
                decimal_or_zero(row["entry_notional_usd"]),
            ),
            average_duration_hours=duration_hours(row["average_duration_ms"]),
            last_closed_at=datetime_from_ms(row["last_closed_at_ms"]),
        )
        for row in rows
    ]


async def load_coin_performance(
    session: AsyncSession,
    *,
    cutoff_ms: int,
) -> list[AnalyticsCoinPerformanceRow]:
    rows = (
        (
            await session.execute(
                select(
                    SourceTrade.coin,
                    func.count(SourceTrade.id).label("closed_trade_count"),
                    func.coalesce(func.sum(SourceTrade.net_pnl_usd), ZERO).label("net_pnl_usd"),
                    func.coalesce(func.sum(SourceTrade.fee_usd), ZERO).label("fee_usd"),
                    func.coalesce(func.sum(SourceTrade.entry_notional_usd), ZERO).label(
                        "entry_notional_usd"
                    ),
                    func.sum(case((SourceTrade.net_pnl_usd > ZERO, 1), else_=0)).label("wins"),
                    func.avg(SourceTrade.duration_ms).label("average_duration_ms"),
                )
                .where(SourceTrade.status == "closed", SourceTrade.closed_at_ms >= cutoff_ms)
                .group_by(SourceTrade.coin)
                .order_by(
                    func.count(SourceTrade.id).desc(), func.sum(SourceTrade.net_pnl_usd).desc()
                )
                .limit(14)
            )
        )
        .mappings()
        .all()
    )
    return [
        AnalyticsCoinPerformanceRow(
            coin=str(row["coin"]),
            closed_trade_count=int(row["closed_trade_count"] or 0),
            win_rate=ratio(int(row["wins"] or 0), int(row["closed_trade_count"] or 0)),
            net_pnl_usd=decimal_or_zero(row["net_pnl_usd"]),
            fee_usd=decimal_or_zero(row["fee_usd"]),
            entry_notional_usd=decimal_or_zero(row["entry_notional_usd"]),
            roi_pct=ratio_decimal(
                decimal_or_zero(row["net_pnl_usd"]),
                decimal_or_zero(row["entry_notional_usd"]),
            ),
            average_duration_hours=duration_hours(row["average_duration_ms"]),
        )
        for row in rows
    ]


async def load_paper_sources(session: AsyncSession) -> list[AnalyticsPaperSourceRow]:
    fill_rows = (
        (
            await session.execute(
                select(
                    PaperCopyFill.source_wallet,
                    func.count(PaperCopyFill.id)
                    .filter(PaperCopyFill.action != "skip")
                    .label("copied_fill_count"),
                    func.count(PaperCopyFill.id)
                    .filter(PaperCopyFill.action == "skip")
                    .label("skipped_fill_count"),
                    func.coalesce(func.sum(PaperCopyFill.realized_pnl_usd), ZERO).label(
                        "realized_pnl_usd"
                    ),
                    func.coalesce(func.sum(PaperCopyFill.fee_usd), ZERO).label("fee_usd"),
                    func.max(PaperCopyFill.filled_at).label("last_fill_at"),
                )
                .where(PaperCopyFill.source_wallet != "")
                .group_by(PaperCopyFill.source_wallet)
            )
        )
        .mappings()
        .all()
    )
    position_rows = (
        (
            await session.execute(
                select(
                    PaperPosition.source_wallet,
                    func.count(PaperPosition.id).label("open_position_count"),
                    func.coalesce(func.sum(PaperPosition.margin_usd), ZERO).label(
                        "open_margin_usd"
                    ),
                )
                .where(PaperPosition.source_wallet != "")
                .group_by(PaperPosition.source_wallet)
            )
        )
        .mappings()
        .all()
    )

    positions_by_source = {
        str(row["source_wallet"]).lower(): row for row in position_rows if row["source_wallet"]
    }
    fills_by_source = {
        str(row["source_wallet"]).lower(): row for row in fill_rows if row["source_wallet"]
    }
    sources = sorted(set(positions_by_source) | set(fills_by_source))
    labels = await load_source_labels(session, sources)
    rows: list[AnalyticsPaperSourceRow] = []
    for source in sources:
        fill_row = fills_by_source.get(source, {})
        position_row = positions_by_source.get(source, {})
        copied_fill_count = int(fill_row.get("copied_fill_count") or 0)
        skipped_fill_count = int(fill_row.get("skipped_fill_count") or 0)
        total_fill_count = copied_fill_count + skipped_fill_count
        rows.append(
            AnalyticsPaperSourceRow(
                source_wallet=source,
                source_label=labels.get(source),
                copied_fill_count=copied_fill_count,
                skipped_fill_count=skipped_fill_count,
                skip_rate_pct=ratio(skipped_fill_count, total_fill_count),
                realized_pnl_usd=decimal_or_zero(fill_row.get("realized_pnl_usd")),
                fee_usd=decimal_or_zero(fill_row.get("fee_usd")),
                open_position_count=int(position_row.get("open_position_count") or 0),
                open_margin_usd=decimal_or_zero(position_row.get("open_margin_usd")),
                last_fill_at=fill_row.get("last_fill_at"),
            )
        )
    return sorted(
        rows,
        key=lambda row: (
            row.open_position_count <= 0,
            -(row.open_margin_usd.copy_abs() + row.realized_pnl_usd.copy_abs()),
        ),
    )[:14]


async def load_skip_reasons(session: AsyncSession) -> list[AnalyticsSkipReasonRow]:
    total = int(
        await session.scalar(
            select(func.count(PaperCopyFill.id)).where(PaperCopyFill.action == "skip")
        )
        or 0
    )
    rows = (
        (
            await session.execute(
                select(
                    PaperCopyFill.skipped_reason,
                    func.count(PaperCopyFill.id).label("count"),
                    func.max(PaperCopyFill.filled_at).label("last_seen_at"),
                )
                .where(PaperCopyFill.action == "skip")
                .group_by(PaperCopyFill.skipped_reason)
                .order_by(func.count(PaperCopyFill.id).desc())
                .limit(12)
            )
        )
        .mappings()
        .all()
    )
    return [
        AnalyticsSkipReasonRow(
            reason=str(row["skipped_reason"] or "unknown"),
            count=int(row["count"] or 0),
            pct=ratio(int(row["count"] or 0), total),
            last_seen_at=row["last_seen_at"],
        )
        for row in rows
    ]


async def load_discovery_sources(session: AsyncSession) -> list[AnalyticsDiscoverySourceRow]:
    rows = (
        (
            await session.execute(
                select(
                    DiscoveryWalletCandidate.source,
                    func.count(DiscoveryWalletCandidate.id).label("total"),
                    func.count(DiscoveryWalletCandidate.id)
                    .filter(DiscoveryWalletCandidate.status == "discovered")
                    .label("discovered"),
                    func.count(DiscoveryWalletCandidate.id)
                    .filter(DiscoveryWalletCandidate.status == "accepted")
                    .label("accepted"),
                    func.count(DiscoveryWalletCandidate.id)
                    .filter(DiscoveryWalletCandidate.status == "rejected")
                    .label("rejected"),
                    func.count(DiscoveryWalletCandidate.id)
                    .filter(DiscoveryWalletCandidate.status == "promoted")
                    .label("promoted"),
                    func.count(DiscoveryWalletCandidate.id)
                    .filter(DiscoveryWalletCandidate.backfill_status == "succeeded")
                    .label("backfill_succeeded"),
                    func.avg(DiscoveryWalletCandidate.source_roi_pct).label("average_roi_pct"),
                    func.avg(DiscoveryWalletCandidate.source_account_value_usd).label(
                        "average_account_value_usd"
                    ),
                    func.max(DiscoveryWalletCandidate.last_seen_at).label("last_seen_at"),
                )
                .group_by(DiscoveryWalletCandidate.source)
                .order_by(func.count(DiscoveryWalletCandidate.id).desc())
                .limit(12)
            )
        )
        .mappings()
        .all()
    )
    return [
        AnalyticsDiscoverySourceRow(
            source=str(row["source"]),
            total=int(row["total"] or 0),
            discovered=int(row["discovered"] or 0),
            accepted=int(row["accepted"] or 0),
            rejected=int(row["rejected"] or 0),
            promoted=int(row["promoted"] or 0),
            backfill_succeeded=int(row["backfill_succeeded"] or 0),
            average_roi_pct=decimal_or_none(row["average_roi_pct"]),
            average_account_value_usd=decimal_or_none(row["average_account_value_usd"]),
            last_seen_at=row["last_seen_at"],
        )
        for row in rows
    ]


async def load_freshness(
    session: AsyncSession,
    *,
    generated_at: datetime,
) -> AnalyticsFreshness:
    stale_cutoff = generated_at - timedelta(hours=24)
    latest_wallet_fill_at = await session.scalar(select(func.max(WalletFill.received_at)))
    latest_scoring_at = await session.scalar(select(func.max(WalletScore.updated_at)))
    latest_position_snapshot_at = await session.scalar(select(func.max(WalletPosition.updated_at)))
    stale_enabled_wallet_count = int(
        await session.scalar(
            select(func.count(WatchedWallet.id)).where(
                WatchedWallet.enabled.is_(True),
                or_(
                    WatchedWallet.last_seen_fill_at.is_(None),
                    WatchedWallet.last_seen_fill_at < stale_cutoff,
                ),
            )
        )
        or 0
    )
    current_drawdown_unavailable_count = int(
        await session.scalar(
            select(func.count(WalletScore.id)).where(
                WalletScore.current_drawdown_status.in_(["unavailable", "zero_equity"])
            )
        )
        or 0
    )
    return AnalyticsFreshness(
        latest_wallet_fill_at=latest_wallet_fill_at,
        latest_scoring_at=latest_scoring_at,
        latest_position_snapshot_at=latest_position_snapshot_at,
        stale_enabled_wallet_count=stale_enabled_wallet_count,
        current_drawdown_unavailable_count=current_drawdown_unavailable_count,
        generated_at=generated_at,
    )


def ranked_wallet_scores_cte() -> Any:
    return (
        select(
            WalletScore.wallet_address,
            func.row_number()
            .over(
                order_by=(
                    WalletScore.score.desc(),
                    WalletScore.updated_at.desc(),
                    WalletScore.wallet_address.asc(),
                )
            )
            .label("pool_rank"),
        )
        .where(WalletScore.score > ZERO)
        .cte("analytics_ranked_wallet_scores")
    )


def wallet_row(row: Any) -> AnalyticsWalletRow:
    return AnalyticsWalletRow(
        wallet_address=str(row["wallet_address"]),
        label=row["label"],
        pool_rank=int_or_none(row["pool_rank"]),
        score=decimal_or_none(row["score"]),
        trade_count=int(row["trade_count"] or 0),
        copyable_pnl_usd=decimal_or_zero(row["copyable_pnl_usd"]),
        win_rate=decimal_or_none(row["win_rate"]),
        profit_factor=decimal_or_none(row["profit_factor"]),
        max_drawdown_pct=decimal_or_none(row["max_drawdown_pct"]),
        current_drawdown_pct=decimal_or_none(row["current_drawdown_pct"]),
        margin_stress_pct=decimal_or_none(row["open_position_stress_pct"]),
        current_drawdown_status=str(row["current_drawdown_status"] or "unknown"),
        last_seen_fill_at=row["last_seen_fill_at"],
    )


async def load_source_labels(
    session: AsyncSession,
    sources: list[str],
) -> dict[str, str]:
    if not sources:
        return {}
    result = await session.execute(
        select(WatchedWallet.address, WatchedWallet.label).where(WatchedWallet.address.in_(sources))
    )
    return {
        str(address).lower(): str(label) for address, label in result.all() if address and label
    }


def ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return Decimal(numerator) / Decimal(denominator)


def ratio_decimal(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= ZERO:
        return None
    return numerator / denominator


def duration_hours(value: Any) -> Decimal | None:
    parsed = decimal_or_none(value)
    if parsed is None:
        return None
    return parsed / MS_PER_HOUR


def datetime_from_ms(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return datetime.fromtimestamp(parsed / 1000, tz=UTC)


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def decimal_or_zero(value: Any) -> Decimal:
    return decimal_or_none(value) or ZERO


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
