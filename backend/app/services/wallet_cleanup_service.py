import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaperPosition, WalletScore, WatchedWallet
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.wallet_cleanup import (
    CurrentDrawdownPruneResponse,
    CurrentDrawdownWalletCandidate,
    HighFillLowScorePruneResponse,
    HighFillLowScoreWalletCandidate,
    MaxDrawdownPruneResponse,
    MaxDrawdownWalletCandidate,
    MinClosedTradesPruneResponse,
    MinClosedTradesWalletCandidate,
    OrphanFillPruneResponse,
    OrphanFillWalletCandidate,
    WalletPruneAllResponse,
    WalletPruneCandidate,
    WalletPruneRuleResult,
    ZeroFillWalletCandidate,
    ZeroFillWalletPruneResponse,
)
from app.services.job_lock_service import job_lock
from app.services.wallet_current_state_service import (
    load_known_wallet_perp_dexes_for_addresses,
    load_wallet_perp_clearinghouse_states,
    summarize_perp_clearinghouse_states,
)
from app.services.wallet_ignore_service import add_ignored_wallet_addresses

logger = logging.getLogger(__name__)
ZERO = Decimal("0")


@dataclass(frozen=True)
class CurrentDrawdownScanWallet:
    address: str
    label: str | None
    score: Decimal | None
    perp_dexes: tuple[str, ...]


async def prune_all_wallets(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    high_fill_min_fills: int = 5000,
    high_fill_score_threshold: Decimal = Decimal("0"),
    high_fill_score_operator: str = "lte",
    min_closed_trades: int = 1,
    max_drawdown_threshold_pct: Decimal = Decimal("0.60"),
    current_drawdown_threshold_ratio: Decimal = Decimal("0.40"),
    current_drawdown_concurrency: int = 8,
    limit: int = 1000,
    use_lock: bool = True,
) -> WalletPruneAllResponse:
    if use_lock:
        async with job_lock(session, key="wallet_prune", ttl_seconds=4 * 60 * 60):
            return await prune_all_wallets(
                session,
                dry_run=dry_run,
                high_fill_min_fills=high_fill_min_fills,
                high_fill_score_threshold=high_fill_score_threshold,
                high_fill_score_operator=high_fill_score_operator,
                min_closed_trades=min_closed_trades,
                max_drawdown_threshold_pct=max_drawdown_threshold_pct,
                current_drawdown_threshold_ratio=current_drawdown_threshold_ratio,
                current_drawdown_concurrency=current_drawdown_concurrency,
                limit=limit,
                use_lock=False,
            )

    orphan_fill_result = await prune_orphan_fill_wallets(
        session,
        dry_run=dry_run,
        limit=limit,
        use_lock=False,
    )
    zero_fill_result = await prune_zero_fill_wallets(
        session,
        dry_run=dry_run,
        limit=limit,
        use_lock=False,
    )
    min_closed_trades_result = await prune_min_closed_trades_wallets(
        session,
        dry_run=dry_run,
        min_closed_trades=min_closed_trades,
        limit=limit,
        use_lock=False,
    )
    max_drawdown_result = await prune_max_drawdown_wallets(
        session,
        dry_run=dry_run,
        threshold_pct=max_drawdown_threshold_pct,
        limit=limit,
        use_lock=False,
    )
    high_fill_result = await prune_high_fill_low_score_wallets(
        session,
        dry_run=dry_run,
        min_fills=high_fill_min_fills,
        score_threshold=high_fill_score_threshold,
        score_operator=high_fill_score_operator,
        limit=limit,
        use_lock=False,
    )
    current_drawdown_result = await prune_current_drawdown_wallets(
        session,
        dry_run=dry_run,
        threshold_ratio=current_drawdown_threshold_ratio,
        limit=limit,
        concurrency=current_drawdown_concurrency,
        use_lock=False,
    )
    rules = [
        orphan_fill_rule_result(orphan_fill_result),
        zero_fill_rule_result(zero_fill_result),
        min_closed_trades_rule_result(min_closed_trades_result),
        max_drawdown_rule_result(max_drawdown_result),
        high_fill_low_score_rule_result(high_fill_result),
        current_drawdown_rule_result(current_drawdown_result),
    ]

    return WalletPruneAllResponse(
        dry_run=dry_run,
        scanned_wallets=sum(rule.scanned_wallets for rule in rules),
        candidate_wallets=sum(rule.candidate_wallets for rule in rules),
        errored_wallets=sum(rule.errored_wallets for rule in rules),
        deleted_wallets=sum(rule.deleted_wallets for rule in rules),
        deleted_fills=sum(rule.deleted_fills for rule in rules),
        rules=rules,
    )


def zero_fill_rule_result(
    result: ZeroFillWalletPruneResponse,
) -> WalletPruneRuleResult:
    return WalletPruneRuleResult(
        key="zero_fill",
        label="Polled zero-fill",
        dry_run=result.dry_run,
        scanned_wallets=result.scanned_wallets,
        candidate_wallets=result.candidate_wallets,
        deleted_wallets=result.deleted_wallets,
        deleted_fills=result.deleted_fills,
        rule="polled, 0 fills",
        items=[
            WalletPruneCandidate(
                address=item.address,
                label=item.label,
                fill_count=item.fill_count,
                score=item.score,
                last_polled_at=item.last_polled_at,
                last_seen_fill_at=item.last_seen_fill_at,
            )
            for item in result.items
        ],
    )


def orphan_fill_rule_result(
    result: OrphanFillPruneResponse,
) -> WalletPruneRuleResult:
    return WalletPruneRuleResult(
        key="orphan_fills",
        label="Orphan fills",
        dry_run=result.dry_run,
        scanned_wallets=result.scanned_wallets,
        candidate_wallets=result.candidate_wallets,
        deleted_wallets=result.deleted_wallets,
        deleted_fills=result.deleted_fills,
        rule="stored fills for wallets not in pool",
        items=[
            WalletPruneCandidate(
                address=item.address,
                label=item.label,
                fill_count=item.fill_count,
                score=item.score,
                last_seen_fill_at=item.last_seen_fill_at,
                detail="Fill data exists, but wallet is not in the active pool.",
            )
            for item in result.items
        ],
    )


def high_fill_low_score_rule_result(
    result: HighFillLowScorePruneResponse,
) -> WalletPruneRuleResult:
    score_operator_label = "<=" if result.score_operator == "lte" else ">="
    return WalletPruneRuleResult(
        key="high_fill_low_score",
        label="High-fill low-score",
        dry_run=result.dry_run,
        scanned_wallets=result.scanned_wallets,
        candidate_wallets=result.candidate_wallets,
        deleted_wallets=result.deleted_wallets,
        deleted_fills=result.deleted_fills,
        rule=f"{result.min_fills}+ fills, score {score_operator_label} {result.score_threshold}",
        items=[
            WalletPruneCandidate(
                address=item.address,
                label=item.label,
                fill_count=item.fill_count,
                score=item.score,
                last_polled_at=item.last_polled_at,
                last_seen_fill_at=item.last_seen_fill_at,
            )
            for item in result.items
        ],
    )


def min_closed_trades_rule_result(
    result: MinClosedTradesPruneResponse,
) -> WalletPruneRuleResult:
    return WalletPruneRuleResult(
        key="min_closed_trades",
        label="Minimum closed trades",
        dry_run=result.dry_run,
        scanned_wallets=result.scanned_wallets,
        candidate_wallets=result.candidate_wallets,
        deleted_wallets=result.deleted_wallets,
        deleted_fills=result.deleted_fills,
        rule=f"closed trades < {result.min_closed_trades}",
        items=[
            WalletPruneCandidate(
                address=item.address,
                label=item.label,
                fill_count=item.fill_count,
                closed_trade_count=item.closed_trade_count,
                score=item.score,
                last_polled_at=item.last_polled_at,
                last_seen_fill_at=item.last_seen_fill_at,
                detail=(
                    f"{item.closed_trade_count} closed trades, "
                    f"{item.fill_count} fills."
                ),
            )
            for item in result.items
        ],
    )


def max_drawdown_rule_result(
    result: MaxDrawdownPruneResponse,
) -> WalletPruneRuleResult:
    return WalletPruneRuleResult(
        key="max_drawdown",
        label="Realized drawdown",
        dry_run=result.dry_run,
        scanned_wallets=result.scanned_wallets,
        candidate_wallets=result.candidate_wallets,
        deleted_wallets=result.deleted_wallets,
        deleted_fills=result.deleted_fills,
        rule=f"realized drawdown >= {result.threshold_pct}",
        items=[
            WalletPruneCandidate(
                address=item.address,
                label=item.label,
                fill_count=item.fill_count,
                closed_trade_count=item.closed_trade_count,
                score=item.score,
                max_drawdown_pct=item.max_drawdown_pct,
                last_polled_at=item.last_polled_at,
                last_seen_fill_at=item.last_seen_fill_at,
                detail=(
                    f"{item.closed_trade_count} closed trades, "
                    f"{item.fill_count} fills."
                ),
            )
            for item in result.items
        ],
    )


def current_drawdown_rule_result(
    result: CurrentDrawdownPruneResponse,
) -> WalletPruneRuleResult:
    return WalletPruneRuleResult(
        key="current_drawdown",
        label="Current drawdown",
        dry_run=result.dry_run,
        scanned_wallets=result.scanned_wallets,
        candidate_wallets=result.candidate_wallets,
        errored_wallets=result.errored_wallets,
        deleted_wallets=result.deleted_wallets,
        deleted_fills=result.deleted_fills,
        rule=f"unrealized loss >= {result.threshold_ratio} of perp equity",
        items=[
            WalletPruneCandidate(
                address=item.address,
                label=item.label,
                score=item.score,
                perp_equity_usd=item.perp_equity_usd,
                account_value_usd=item.account_value_usd,
                total_unrealized_pnl_usd=item.total_unrealized_pnl_usd,
                detail=(
                    f"Fetch failed: {item.error}"
                    if item.error
                    else (
                        f"{item.open_position_count} open positions, "
                        f"loss ratio {item.unrealized_loss_ratio}"
                    )
                ),
                error=item.error,
            )
            for item in result.items
        ],
    )


async def prune_zero_fill_wallets(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    limit: int = 250,
    use_lock: bool = True,
) -> ZeroFillWalletPruneResponse:
    if use_lock:
        async with job_lock(session, key="wallet_prune", ttl_seconds=4 * 60 * 60):
            return await prune_zero_fill_wallets(
                session,
                dry_run=dry_run,
                limit=limit,
                use_lock=False,
            )

    scanned_wallets = await count_zero_fill_scan_wallets(session)
    candidates = await load_zero_fill_wallet_candidates(session, limit=limit)
    addresses = [candidate.address for candidate in candidates]
    deleted_fills = 0
    deleted_wallets = 0

    if not dry_run and addresses:
        deleted_fills, deleted_wallets = await delete_wallet_related_rows(
            session,
            addresses=addresses,
        )
        await add_ignored_wallet_addresses(
            session,
            addresses=addresses,
            reason="zero_fill_prune",
        )
        await session.commit()

    return ZeroFillWalletPruneResponse(
        dry_run=dry_run,
        scanned_wallets=scanned_wallets,
        candidate_wallets=len(candidates),
        deleted_wallets=deleted_wallets,
        deleted_fills=deleted_fills,
        items=candidates,
    )


async def prune_orphan_fill_wallets(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    limit: int = 250,
    use_lock: bool = True,
) -> OrphanFillPruneResponse:
    if use_lock:
        async with job_lock(session, key="wallet_prune", ttl_seconds=4 * 60 * 60):
            return await prune_orphan_fill_wallets(
                session,
                dry_run=dry_run,
                limit=limit,
                use_lock=False,
            )

    scanned_wallets = await count_orphan_fill_wallets(session)
    candidates = await load_orphan_fill_wallet_candidates(session, limit=limit)
    addresses = [candidate.address for candidate in candidates]
    deleted_fills = 0

    if not dry_run and addresses:
        deleted_fills = await delete_wallet_data_rows(session, addresses=addresses)
        await session.commit()

    return OrphanFillPruneResponse(
        dry_run=dry_run,
        scanned_wallets=scanned_wallets,
        candidate_wallets=len(candidates),
        deleted_wallets=0,
        deleted_fills=deleted_fills,
        items=candidates,
    )


async def load_orphan_fill_wallet_candidates(
    session: AsyncSession,
    *,
    limit: int,
) -> list[OrphanFillWalletCandidate]:
    result = await session.execute(
        text(
            """
            with orphan_fill_wallets as (
              select
                wf.wallet_address as address,
                count(wf.id) as fill_count,
                to_timestamp(max(wf.timestamp_ms) / 1000.0) as last_seen_fill_at
              from wallet_fills wf
              left join watched_wallets ww on ww.address = wf.wallet_address
              where ww.address is null
                and not exists (
                  select 1
                  from paper_positions pp
                  where pp.source_wallet = wf.wallet_address
                )
              group by wf.wallet_address
            )
            select
              ofw.address,
              (
                select dc.source_label
                from discovery_wallet_candidates dc
                where dc.wallet_address = ofw.address
                order by dc.updated_at desc
                limit 1
              ) as label,
              ws.score,
              ofw.fill_count,
              ofw.last_seen_fill_at
            from orphan_fill_wallets ofw
            left join wallet_scores ws on ws.wallet_address = ofw.address
            order by ofw.fill_count desc, ofw.address asc
            limit :limit
            """
        ),
        {"limit": limit},
    )
    return [
        OrphanFillWalletCandidate(
            address=str(row["address"]),
            label=row["label"],
            fill_count=int(row["fill_count"] or 0),
            score=str(row["score"]) if row["score"] is not None else None,
            last_seen_fill_at=(
                str(row["last_seen_fill_at"]) if row["last_seen_fill_at"] else None
            ),
        )
        for row in result.mappings().all()
    ]


async def count_orphan_fill_wallets(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            text(
                """
                select count(*)
                from (
                  select wf.wallet_address
                  from wallet_fills wf
                  left join watched_wallets ww on ww.address = wf.wallet_address
                  where ww.address is null
                    and not exists (
                      select 1
                      from paper_positions pp
                      where pp.source_wallet = wf.wallet_address
                    )
                  group by wf.wallet_address
                ) orphan_wallets
                """
            )
        )
        or 0
    )


async def prune_min_closed_trades_wallets(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    min_closed_trades: int = 1,
    limit: int = 250,
    use_lock: bool = True,
) -> MinClosedTradesPruneResponse:
    if use_lock:
        async with job_lock(session, key="wallet_prune", ttl_seconds=4 * 60 * 60):
            return await prune_min_closed_trades_wallets(
                session,
                dry_run=dry_run,
                min_closed_trades=min_closed_trades,
                limit=limit,
                use_lock=False,
            )

    scanned_wallets = await count_min_closed_trades_scan_wallets(session)
    candidates = await load_min_closed_trades_wallet_candidates(
        session,
        min_closed_trades=min_closed_trades,
        limit=limit,
    )
    addresses = [candidate.address for candidate in candidates]
    deleted_fills = 0
    deleted_wallets = 0

    if not dry_run and addresses:
        deleted_fills, deleted_wallets = await delete_wallet_related_rows(
            session,
            addresses=addresses,
        )
        await add_ignored_wallet_addresses(
            session,
            addresses=addresses,
            reason="min_closed_trades_prune",
        )
        await session.commit()

    return MinClosedTradesPruneResponse(
        dry_run=dry_run,
        scanned_wallets=scanned_wallets,
        candidate_wallets=len(candidates),
        deleted_wallets=deleted_wallets,
        deleted_fills=deleted_fills,
        min_closed_trades=min_closed_trades,
        items=candidates,
    )


async def load_min_closed_trades_wallet_candidates(
    session: AsyncSession,
    *,
    min_closed_trades: int,
    limit: int,
) -> list[MinClosedTradesWalletCandidate]:
    result = await session.execute(
        text(
            """
            select
              ww.address,
              ww.label,
              ww.last_polled_at,
              ww.last_seen_fill_at,
              ws.score,
              ws.trade_count as closed_trade_count,
              count(wf.id) as fill_count
            from watched_wallets ww
            join wallet_scores ws on ws.wallet_address = ww.address
            left join wallet_fills wf on wf.wallet_address = ww.address
            where ww.last_polled_at is not null
              and ww.copy_enabled is false
              and ww.polling_tier <> 'active'
              and not exists (
                select 1
                from paper_positions pp
                where pp.source_wallet = ww.address
              )
              and ws.trade_count < :min_closed_trades
            group by
              ww.address,
              ww.label,
              ww.last_polled_at,
              ww.last_seen_fill_at,
              ws.score,
              ws.trade_count
            order by ws.trade_count asc, ws.score asc, fill_count desc, ww.address asc
            limit :limit
            """
        ),
        {
            "limit": limit,
            "min_closed_trades": min_closed_trades,
        },
    )
    return [
        MinClosedTradesWalletCandidate(
            address=str(row["address"]),
            label=row["label"],
            fill_count=int(row["fill_count"] or 0),
            closed_trade_count=int(row["closed_trade_count"] or 0),
            score=str(row["score"]) if row["score"] is not None else None,
            last_polled_at=str(row["last_polled_at"]) if row["last_polled_at"] else None,
            last_seen_fill_at=str(row["last_seen_fill_at"]) if row["last_seen_fill_at"] else None,
        )
        for row in result.mappings().all()
    ]


async def count_min_closed_trades_scan_wallets(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            text(
                """
                select count(*)
                from watched_wallets ww
                join wallet_scores ws on ws.wallet_address = ww.address
                where ww.last_polled_at is not null
                  and ww.copy_enabled is false
                  and ww.polling_tier <> 'active'
                  and not exists (
                    select 1
                    from paper_positions pp
                    where pp.source_wallet = ww.address
                  )
                """
            )
        )
        or 0
    )


async def prune_max_drawdown_wallets(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    threshold_pct: Decimal = Decimal("0.60"),
    limit: int = 250,
    use_lock: bool = True,
) -> MaxDrawdownPruneResponse:
    if use_lock:
        async with job_lock(session, key="wallet_prune", ttl_seconds=4 * 60 * 60):
            return await prune_max_drawdown_wallets(
                session,
                dry_run=dry_run,
                threshold_pct=threshold_pct,
                limit=limit,
                use_lock=False,
            )

    scanned_wallets = await count_max_drawdown_scan_wallets(session)
    candidates = await load_max_drawdown_wallet_candidates(
        session,
        threshold_pct=threshold_pct,
        limit=limit,
    )
    addresses = [candidate.address for candidate in candidates]
    deleted_fills = 0
    deleted_wallets = 0

    if not dry_run and addresses:
        deleted_fills, deleted_wallets = await delete_wallet_related_rows(
            session,
            addresses=addresses,
        )
        await add_ignored_wallet_addresses(
            session,
            addresses=addresses,
            reason="max_drawdown_prune",
        )
        await session.commit()

    return MaxDrawdownPruneResponse(
        dry_run=dry_run,
        scanned_wallets=scanned_wallets,
        candidate_wallets=len(candidates),
        deleted_wallets=deleted_wallets,
        deleted_fills=deleted_fills,
        threshold_pct=str(threshold_pct),
        items=candidates,
    )


async def load_max_drawdown_wallet_candidates(
    session: AsyncSession,
    *,
    threshold_pct: Decimal,
    limit: int,
) -> list[MaxDrawdownWalletCandidate]:
    result = await session.execute(
        text(
            """
            select
              ww.address,
              ww.label,
              ww.last_polled_at,
              ww.last_seen_fill_at,
              ws.score,
              ws.max_drawdown_pct,
              ws.trade_count as closed_trade_count,
              count(wf.id) as fill_count
            from watched_wallets ww
            join wallet_scores ws on ws.wallet_address = ww.address
            left join wallet_fills wf on wf.wallet_address = ww.address
            where ww.last_polled_at is not null
              and ww.copy_enabled is false
              and ww.polling_tier <> 'active'
              and not exists (
                select 1
                from paper_positions pp
                where pp.source_wallet = ww.address
              )
              and ws.max_drawdown_pct is not null
              and ws.max_drawdown_pct >= :threshold_pct
            group by
              ww.address,
              ww.label,
              ww.last_polled_at,
              ww.last_seen_fill_at,
              ws.score,
              ws.max_drawdown_pct,
              ws.trade_count
            order by ws.max_drawdown_pct desc, ws.score asc, fill_count desc, ww.address asc
            limit :limit
            """
        ),
        {
            "limit": limit,
            "threshold_pct": threshold_pct,
        },
    )
    return [
        MaxDrawdownWalletCandidate(
            address=str(row["address"]),
            label=row["label"],
            fill_count=int(row["fill_count"] or 0),
            closed_trade_count=int(row["closed_trade_count"] or 0),
            score=str(row["score"]) if row["score"] is not None else None,
            max_drawdown_pct=str(row["max_drawdown_pct"]),
            last_polled_at=str(row["last_polled_at"]) if row["last_polled_at"] else None,
            last_seen_fill_at=str(row["last_seen_fill_at"]) if row["last_seen_fill_at"] else None,
        )
        for row in result.mappings().all()
    ]


async def count_max_drawdown_scan_wallets(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            text(
                """
                select count(*)
                from watched_wallets ww
                join wallet_scores ws on ws.wallet_address = ww.address
                where ww.last_polled_at is not null
                  and ww.copy_enabled is false
                  and ww.polling_tier <> 'active'
                  and not exists (
                    select 1
                    from paper_positions pp
                    where pp.source_wallet = ww.address
                  )
                  and ws.max_drawdown_pct is not null
                """
            )
        )
        or 0
    )


async def load_zero_fill_wallet_candidates(
    session: AsyncSession,
    *,
    limit: int,
) -> list[ZeroFillWalletCandidate]:
    result = await session.execute(
        text(
            """
            select
              ww.address,
              ww.label,
              ww.last_polled_at,
              ww.last_seen_fill_at,
              ws.score,
              0 as fill_count
            from watched_wallets ww
            left join wallet_scores ws on ws.wallet_address = ww.address
            where ww.last_polled_at is not null
              and ww.copy_enabled is false
              and ww.polling_tier <> 'active'
              and not exists (
                select 1
                from paper_positions pp
                where pp.source_wallet = ww.address
              )
              and not exists (
                select 1
                from wallet_fills wf
                where wf.wallet_address = ww.address
              )
            order by ww.last_polled_at asc, ww.address asc
            limit :limit
            """
        ),
        {"limit": limit},
    )
    return [
        ZeroFillWalletCandidate(
            address=str(row["address"]),
            label=row["label"],
            fill_count=int(row["fill_count"] or 0),
            score=str(row["score"]) if row["score"] is not None else None,
            last_polled_at=str(row["last_polled_at"]) if row["last_polled_at"] else None,
            last_seen_fill_at=str(row["last_seen_fill_at"]) if row["last_seen_fill_at"] else None,
        )
        for row in result.mappings().all()
    ]


async def count_zero_fill_scan_wallets(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            text(
                """
                select count(*)
                from watched_wallets ww
                where ww.last_polled_at is not null
                  and ww.copy_enabled is false
                  and ww.polling_tier <> 'active'
                  and not exists (
                    select 1
                    from paper_positions pp
                    where pp.source_wallet = ww.address
                  )
                """
            )
        )
        or 0
    )


async def delete_wallet_related_rows(
    session: AsyncSession,
    *,
    addresses: list[str],
) -> tuple[int, int]:
    parameters = {"addresses": addresses}
    deleted_fills = await delete_wallet_data_rows(session, addresses=addresses)

    wallets_result = await session.execute(
        address_list_statement("delete from watched_wallets where address in :addresses"),
        parameters,
    )
    return deleted_fills, max(0, wallets_result.rowcount or 0)


async def delete_wallet_data_rows(
    session: AsyncSession,
    *,
    addresses: list[str],
) -> int:
    parameters = {"addresses": addresses}
    fills_result = await session.execute(
        address_list_statement("delete from wallet_fills where wallet_address in :addresses"),
        parameters,
    )
    await session.execute(
        address_list_statement("delete from wallet_scores where wallet_address in :addresses"),
        parameters,
    )
    await session.execute(
        address_list_statement(
            "delete from wallet_score_snapshots where wallet_address in :addresses"
        ),
        parameters,
    )
    await session.execute(
        address_list_statement("delete from wallet_positions where wallet_address in :addresses"),
        parameters,
    )
    await session.execute(
        address_list_statement(
            "delete from active_copy_wallets where wallet_address in :addresses"
        ),
        parameters,
    )
    await session.execute(
        address_list_statement("delete from copy_signals where source_wallet in :addresses"),
        parameters,
    )
    await session.execute(
        address_list_statement("delete from copy_trades where source_wallet in :addresses"),
        parameters,
    )
    await session.execute(
        address_list_statement("delete from source_trade_links where source_wallet in :addresses"),
        parameters,
    )
    return max(0, fills_result.rowcount or 0)


def address_list_statement(sql: str):
    return text(sql).bindparams(bindparam("addresses", expanding=True))


async def prune_current_drawdown_wallets(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    threshold_ratio: Decimal = Decimal("0.40"),
    limit: int = 250,
    concurrency: int = 8,
    use_lock: bool = True,
) -> CurrentDrawdownPruneResponse:
    if use_lock:
        async with job_lock(session, key="wallet_prune", ttl_seconds=4 * 60 * 60):
            return await prune_current_drawdown_wallets(
                session,
                dry_run=dry_run,
                threshold_ratio=threshold_ratio,
                limit=limit,
                concurrency=concurrency,
                use_lock=False,
            )

    wallets = await load_current_drawdown_scan_wallets(session, limit=limit)
    client = HyperliquidClient()
    semaphore = asyncio.Semaphore(concurrency)

    async def candidate_for_wallet(
        wallet: CurrentDrawdownScanWallet,
    ) -> CurrentDrawdownWalletCandidate | None:
        async with semaphore:
            return await load_current_drawdown_candidate(
                client=client,
                address=wallet.address,
                label=wallet.label,
                score=wallet.score,
                perp_dexes=wallet.perp_dexes,
                threshold_ratio=threshold_ratio,
            )

    checked_candidates = await asyncio.gather(
        *(candidate_for_wallet(wallet) for wallet in wallets)
    )
    candidates = [
        candidate
        for candidate in checked_candidates
        if candidate is not None and candidate.error is None
    ]
    errored_candidates = [
        candidate
        for candidate in checked_candidates
        if candidate is not None and candidate.error is not None
    ]

    candidates.sort(
        key=lambda candidate: Decimal(candidate.unrealized_loss_ratio or "0"),
        reverse=True,
    )

    errored_wallets = len(errored_candidates)
    if errored_wallets:
        logger.info(
            "current drawdown prune skipped %s wallets with fetch errors",
            errored_wallets,
        )

    deleted_fills = 0
    deleted_wallets = 0
    addresses = [candidate.address for candidate in candidates]
    if not dry_run and addresses:
        deleted_fills, deleted_wallets = await delete_wallet_related_rows(
            session,
            addresses=addresses,
        )
        await add_ignored_wallet_addresses(
            session,
            addresses=addresses,
            reason="current_unrealized_loss_prune",
        )
        await session.commit()

    return CurrentDrawdownPruneResponse(
        dry_run=dry_run,
        scanned_wallets=len(wallets),
        candidate_wallets=len(candidates),
        errored_wallets=errored_wallets,
        deleted_wallets=deleted_wallets,
        deleted_fills=deleted_fills,
        threshold_ratio=str(threshold_ratio),
        items=[*candidates, *errored_candidates],
    )


async def prune_high_fill_low_score_wallets(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    min_fills: int = 5000,
    score_threshold: Decimal = Decimal("0"),
    score_operator: str = "lte",
    limit: int = 250,
    use_lock: bool = True,
) -> HighFillLowScorePruneResponse:
    if use_lock:
        async with job_lock(session, key="wallet_prune", ttl_seconds=4 * 60 * 60):
            return await prune_high_fill_low_score_wallets(
                session,
                dry_run=dry_run,
                min_fills=min_fills,
                score_threshold=score_threshold,
                score_operator=score_operator,
                limit=limit,
                use_lock=False,
            )

    normalized_operator = normalize_score_operator(score_operator)
    scanned_wallets = await count_high_fill_low_score_scan_wallets(session)
    candidates = await load_high_fill_low_score_wallet_candidates(
        session,
        min_fills=min_fills,
        score_threshold=score_threshold,
        score_operator=normalized_operator,
        limit=limit,
    )
    addresses = [candidate.address for candidate in candidates]
    deleted_fills = 0
    deleted_wallets = 0

    if not dry_run and addresses:
        deleted_fills, deleted_wallets = await delete_wallet_related_rows(
            session,
            addresses=addresses,
        )
        await add_ignored_wallet_addresses(
            session,
            addresses=addresses,
            reason="high_fill_low_score_prune",
        )
        await session.commit()

    return HighFillLowScorePruneResponse(
        dry_run=dry_run,
        scanned_wallets=scanned_wallets,
        candidate_wallets=len(candidates),
        deleted_wallets=deleted_wallets,
        deleted_fills=deleted_fills,
        min_fills=min_fills,
        score_threshold=str(score_threshold),
        score_operator=normalized_operator,
        items=candidates,
    )


async def load_high_fill_low_score_wallet_candidates(
    session: AsyncSession,
    *,
    min_fills: int,
    score_threshold: Decimal,
    score_operator: str,
    limit: int,
) -> list[HighFillLowScoreWalletCandidate]:
    result = await session.execute(
        high_fill_low_score_statement(
            """
            select *
            from scored_wallets
            where fill_count >= :min_fills
              and {score_condition}
            order by score asc, fill_count desc, address asc
            limit :limit
            """,
            score_operator=score_operator,
        ),
        {
            "limit": limit,
            "min_fills": min_fills,
            "score_threshold": score_threshold,
        },
    )
    return [
        HighFillLowScoreWalletCandidate(
            address=str(row["address"]),
            label=row["label"],
            fill_count=int(row["fill_count"] or 0),
            score=str(row["score"]),
            last_polled_at=str(row["last_polled_at"]) if row["last_polled_at"] else None,
            last_seen_fill_at=str(row["last_seen_fill_at"]) if row["last_seen_fill_at"] else None,
        )
        for row in result.mappings().all()
    ]


async def count_high_fill_low_score_scan_wallets(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            high_fill_low_score_statement(
                "select count(*) from scored_wallets",
                score_operator="lte",
            )
        )
        or 0
    )


async def count_high_fill_low_score_wallet_candidates(
    session: AsyncSession,
    *,
    min_fills: int,
    score_threshold: Decimal,
    score_operator: str,
) -> int:
    return int(
        await session.scalar(
            high_fill_low_score_statement(
                """
                select count(*)
                from scored_wallets
                where fill_count >= :min_fills
                  and {score_condition}
                """,
                score_operator=score_operator,
            ),
            {
                "min_fills": min_fills,
                "score_threshold": score_threshold,
            },
        )
        or 0
    )


async def load_current_drawdown_candidate(
    *,
    client: HyperliquidClient,
    address: str,
    label: str | None,
    score: Decimal | None,
    perp_dexes: tuple[str, ...],
    threshold_ratio: Decimal,
) -> CurrentDrawdownWalletCandidate | None:
    perp_states, errors = await load_wallet_perp_clearinghouse_states(
        client=client,
        address=address,
        dexes=perp_dexes,
    )
    if errors:
        return CurrentDrawdownWalletCandidate(
            address=address,
            label=label,
            score=str(score) if score is not None else None,
            error="; ".join(errors) or "Perp state unavailable.",
        )

    perp_summary = summarize_perp_clearinghouse_states(perp_states)
    positions = perp_summary.positions
    perp_equity = perp_summary.account_value_usd
    unrealized_pnl = perp_summary.total_unrealized_pnl_usd

    if perp_equity <= ZERO or unrealized_pnl >= ZERO:
        return None

    loss_ratio = unrealized_pnl.copy_abs() / perp_equity
    if loss_ratio < threshold_ratio:
        return None

    top_position = positions[0] if positions else None
    return CurrentDrawdownWalletCandidate(
        address=address,
        label=label,
        score=str(score) if score is not None else None,
        perp_equity_usd=str(perp_equity),
        account_value_usd=str(perp_equity),
        total_unrealized_pnl_usd=str(unrealized_pnl),
        unrealized_loss_ratio=str(loss_ratio),
        open_position_count=len(positions),
        top_position_coin=top_position.coin if top_position else None,
        top_position_unrealized_pnl_usd=(
            str(top_position.unrealized_pnl_usd)
            if top_position and top_position.unrealized_pnl_usd is not None
            else None
        ),
        top_position_value_usd=(
            str(top_position.position_value_usd)
            if top_position and top_position.position_value_usd is not None
            else None
        ),
    )


async def load_current_drawdown_scan_wallets(
    session: AsyncSession,
    *,
    limit: int,
) -> list[CurrentDrawdownScanWallet]:
    result = await session.execute(
        select(WatchedWallet.address, WatchedWallet.label, WalletScore.score)
        .outerjoin(WalletScore, WalletScore.wallet_address == WatchedWallet.address)
        .where(
            WatchedWallet.enabled.is_(True),
            WatchedWallet.copy_enabled.is_(False),
            WatchedWallet.polling_tier != "active",
            ~select(PaperPosition.id)
            .where(PaperPosition.source_wallet == WatchedWallet.address)
            .exists(),
        )
        .order_by(WatchedWallet.last_polled_at.asc().nulls_first(), WatchedWallet.address.asc())
        .limit(limit)
    )
    wallets = [
        CurrentDrawdownScanWallet(
            address=str(row["address"]),
            label=row["label"],
            score=row["score"],
            perp_dexes=(),
        )
        for row in result.mappings().all()
    ]
    dexes_by_address = await load_known_wallet_perp_dexes_for_addresses(
        session,
        addresses=[wallet.address for wallet in wallets],
    )
    return [
        CurrentDrawdownScanWallet(
            address=wallet.address,
            label=wallet.label,
            score=wallet.score,
            perp_dexes=dexes_by_address.get(wallet.address.lower(), ()),
        )
        for wallet in wallets
    ]


def high_fill_low_score_statement(sql: str, *, score_operator: str):
    score_condition = (
        "score <= :score_threshold"
        if score_operator == "lte"
        else "score >= :score_threshold"
    )
    return text(
        f"""
        with scored_wallets as (
          select
            ww.address,
            ww.label,
            ww.last_polled_at,
            ww.last_seen_fill_at,
            ws.score,
            count(wf.id) as fill_count
          from watched_wallets ww
          join wallet_scores ws on ws.wallet_address = ww.address
          left join wallet_fills wf on wf.wallet_address = ww.address
          where ww.copy_enabled is false
            and ww.polling_tier <> 'active'
            and ww.last_polled_at is not null
            and not exists (
              select 1
              from paper_positions pp
              where pp.source_wallet = ww.address
            )
          group by ww.address, ww.label, ww.last_polled_at, ww.last_seen_fill_at, ws.score
        )
        {sql.format(score_condition=score_condition)}
        """
    )


def normalize_score_operator(score_operator: str) -> str:
    if score_operator not in {"lte", "gte"}:
        raise ValueError("score_operator must be one of: lte, gte.")
    return score_operator
