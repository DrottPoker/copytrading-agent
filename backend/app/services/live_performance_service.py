"""Time-weighted performance of a live account and the loss state measured on it."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    TradingAccount,
    TradingAccountCashFlow,
    TradingAccountPerformanceSnapshot,
)
from app.services.copy_risk_rules import (
    AccountLossState,
    account_loss_breach,
    performance_loss_pct,
)
from app.services.live_account_state import (
    LIVE_PERFORMANCE_BACKFILL_VERSION,
    live_performance_backfill_version,
)
from app.services.live_exchange_fetch import (
    LiveCashFlowFetchResult,
    LivePortfolioHistoryResult,
)
from app.services.live_exchange_parsing import (
    decimal_or_none,
    merge_raw_payload,
    parse_live_account_cash_flow,
)

ZERO = Decimal("0")


# Performance snapshots keep every reconciliation for the loss limit windows and
# are thinned to the hour after that.
LIVE_PERFORMANCE_SNAPSHOT_FULL_RESOLUTION_DAYS = 8


async def restart_breached_live_loss_limits(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    now: datetime,
) -> dict[str, str] | None:
    """Restart the loss limits when an operator starts a breached account.

    A breach leaves the account exit-only until someone starts it again. That
    start is the review, so the limits measure from it; otherwise the same old
    loss would stop the account at its first new entry. An account inside its
    limits keeps its windows. Returns the breach that was acknowledged.
    """

    breach = account_loss_breach(
        await live_account_loss_state(
            session,
            account_key=account.key,
            now=now,
            since=account.loss_limits_reset_at,
        ),
        settings=settings,
    )
    if breach is None:
        return None
    account.loss_limits_reset_at = now
    return {
        "rule": breach.rule,
        "observed": str(breach.observed),
        "limit": str(breach.limit),
        "restartedAt": now.isoformat(),
    }


async def live_account_loss_state(
    session: AsyncSession,
    *,
    account_key: str,
    now: datetime,
    since: datetime | None = None,
) -> AccountLossState | None:
    """Rolling losses and drawdown from the time-weighted performance index.

    The index excludes deposits and withdrawals, so cash flows never look like
    trading losses. Windows that start before tracking use the baseline snapshot.
    With `since`, the account's loss limit restart, only snapshots from then on
    count, so losses an operator already reviewed cannot trip the limits again.
    """

    snapshot = TradingAccountPerformanceSnapshot
    in_scope = [snapshot.account_key == account_key]
    if since is not None:
        in_scope.append(snapshot.recorded_at >= since)
    latest = await session.scalar(
        select(snapshot.performance_index)
        .where(*in_scope)
        .order_by(snapshot.recorded_at.desc())
        .limit(1)
    )
    if latest is None:
        return None
    baseline = await session.scalar(
        select(snapshot.performance_index)
        .where(*in_scope)
        .order_by(snapshot.recorded_at.asc())
        .limit(1)
    )
    peak = await session.scalar(select(func.max(snapshot.performance_index)).where(*in_scope))

    async def index_at(moment: datetime) -> Decimal:
        value = await session.scalar(
            select(snapshot.performance_index)
            .where(*in_scope, snapshot.recorded_at <= moment)
            .order_by(snapshot.recorded_at.desc())
            .limit(1)
        )
        return decimal_or_none(value) or decimal_or_none(baseline) or ZERO

    current = decimal_or_none(latest) or ZERO
    return AccountLossState(
        daily_loss_pct=performance_loss_pct(current, await index_at(now - timedelta(days=1))),
        weekly_loss_pct=performance_loss_pct(current, await index_at(now - timedelta(days=7))),
        drawdown_pct=performance_loss_pct(current, decimal_or_none(peak) or ZERO),
    )


async def record_live_account_performance_snapshot(
    session: AsyncSession,
    *,
    account: TradingAccount,
    recorded_at: datetime,
    equity_usd: Decimal | None = None,
) -> TradingAccountPerformanceSnapshot | None:
    end_equity = equity_usd if equity_usd is not None else decimal_or_none(account.equity_usd)
    if end_equity is None or end_equity < ZERO:
        return None
    previous = await session.scalar(
        select(TradingAccountPerformanceSnapshot)
        .where(TradingAccountPerformanceSnapshot.account_key == account.key)
        .order_by(TradingAccountPerformanceSnapshot.recorded_at.desc())
        .limit(1)
        .with_for_update()
    )
    if previous is None:
        snapshot = TradingAccountPerformanceSnapshot(
            account_key=account.key,
            account_type="live",
            equity_usd=end_equity,
            period_external_flow_usd=ZERO,
            net_external_flows_usd=ZERO,
            trading_pnl_usd=ZERO,
            period_return_pct=ZERO,
            time_weighted_return_pct=ZERO,
            performance_index=Decimal("1"),
            tracking_started_at=recorded_at,
            recorded_at=recorded_at,
            is_baseline=True,
        )
    else:
        flow_result = await session.scalars(
            select(TradingAccountCashFlow)
            .where(
                TradingAccountCashFlow.account_key == account.key,
                TradingAccountCashFlow.occurred_at > previous.recorded_at,
                TradingAccountCashFlow.occurred_at <= recorded_at,
            )
            .order_by(
                TradingAccountCashFlow.occurred_at.asc(),
                TradingAccountCashFlow.id.asc(),
            )
        )
        flows = list(flow_result.all())
        period_external_flow = sum((flow.amount_usd for flow in flows), ZERO)
        period_trading_pnl = end_equity - previous.equity_usd - period_external_flow
        period_return = cash_flow_adjusted_period_return(
            start_equity=previous.equity_usd,
            end_equity=end_equity,
            cash_flows=[(flow.occurred_at, flow.amount_usd) for flow in flows],
            started_at=previous.recorded_at,
            ended_at=recorded_at,
        )
        performance_index = previous.performance_index
        if period_return is not None:
            performance_index = max(
                previous.performance_index * (Decimal("1") + period_return),
                ZERO,
            )
        snapshot = TradingAccountPerformanceSnapshot(
            account_key=account.key,
            account_type="live",
            equity_usd=end_equity,
            period_external_flow_usd=period_external_flow,
            net_external_flows_usd=(previous.net_external_flows_usd + period_external_flow),
            trading_pnl_usd=previous.trading_pnl_usd + period_trading_pnl,
            period_return_pct=period_return,
            time_weighted_return_pct=performance_index - Decimal("1"),
            performance_index=performance_index,
            tracking_started_at=previous.tracking_started_at,
            recorded_at=recorded_at,
            is_baseline=False,
        )
    session.add(snapshot)
    existing_payload = account.config_payload if isinstance(account.config_payload, dict) else {}
    existing_performance = existing_payload.get("performanceTracking")
    backfill_version = (
        live_performance_backfill_version(account) if isinstance(existing_performance, dict) else 0
    )
    account.config_payload = merge_raw_payload(
        account.config_payload,
        {
            "performanceTracking": {
                "historicalBackfillVersion": backfill_version,
                "netExternalFlowsUsd": str(snapshot.net_external_flows_usd),
                "recordedAt": snapshot.recorded_at.isoformat(),
                "timeWeightedReturnPct": str(snapshot.time_weighted_return_pct),
                "trackingStartedAt": snapshot.tracking_started_at.isoformat(),
                "tradingPnlUsd": str(snapshot.trading_pnl_usd),
            }
        },
    )
    await session.flush()
    return snapshot


async def backfill_live_account_performance_history(
    session: AsyncSession,
    *,
    account: TradingAccount,
    user_address: str,
    portfolio_history: LivePortfolioHistoryResult,
    cash_flow_history: LiveCashFlowFetchResult | None,
    reconciled_at: datetime,
) -> bool:
    if live_performance_backfill_version(account) >= LIVE_PERFORMANCE_BACKFILL_VERSION:
        return False
    if (
        not portfolio_history.complete
        or len(portfolio_history.account_values) < 2
        or cash_flow_history is None
        or not cash_flow_history.complete
    ):
        return False
    account_values = live_performance_backfill_account_values(
        account_key=account.key,
        user_address=user_address,
        account_values=[
            (recorded_at, equity)
            for recorded_at, equity in portfolio_history.account_values
            if recorded_at < reconciled_at
        ],
        cash_flow_updates=cash_flow_history.updates,
    )
    if len(account_values) < 2:
        return False

    await session.execute(
        delete(TradingAccountPerformanceSnapshot).where(
            TradingAccountPerformanceSnapshot.account_key == account.key
        )
    )
    payload = account.config_payload if isinstance(account.config_payload, dict) else {}
    performance = payload.get("performanceTracking")
    performance_payload = dict(performance) if isinstance(performance, dict) else {}
    performance_payload["historicalBackfillVersion"] = LIVE_PERFORMANCE_BACKFILL_VERSION
    account.config_payload = merge_raw_payload(
        account.config_payload,
        {"performanceTracking": performance_payload},
    )
    await session.flush()

    for recorded_at, equity in account_values:
        await record_live_account_performance_snapshot(
            session,
            account=account,
            recorded_at=recorded_at,
            equity_usd=equity,
        )
    return True


def live_performance_backfill_account_values(
    *,
    account_key: str,
    user_address: str,
    account_values: list[tuple[datetime, Decimal]],
    cash_flow_updates: tuple[dict[str, Any], ...],
) -> list[tuple[datetime, Decimal]]:
    positive_index = next(
        (index for index, (_, equity) in enumerate(account_values) if equity > ZERO),
        None,
    )
    if positive_index is None:
        return account_values

    positive_values = account_values[positive_index:]
    first_positive_at = positive_values[0][0]
    initial_deposit_times = [
        parsed["occurred_at"]
        for update in cash_flow_updates
        for parsed in [
            parse_live_account_cash_flow(
                update,
                account_key=account_key,
                user_address=user_address,
            )
        ]
        if parsed is not None
        and parsed["amount_usd"] > ZERO
        and parsed["occurred_at"] <= first_positive_at
    ]
    if not initial_deposit_times:
        return account_values

    initial_anchor_at = min(initial_deposit_times) - timedelta(microseconds=1)
    return [(initial_anchor_at, ZERO), *positive_values]


async def ensure_live_account_performance_baseline(
    session: AsyncSession,
    *,
    account: TradingAccount,
) -> TradingAccountPerformanceSnapshot | None:
    if account.last_reconciled_at is None or account.equity_usd is None:
        return None
    existing_snapshot_id = await session.scalar(
        select(TradingAccountPerformanceSnapshot.id)
        .where(TradingAccountPerformanceSnapshot.account_key == account.key)
        .limit(1)
    )
    if existing_snapshot_id is not None:
        return None
    return await record_live_account_performance_snapshot(
        session,
        account=account,
        recorded_at=account.last_reconciled_at,
    )


def cash_flow_adjusted_period_return(
    *,
    start_equity: Decimal,
    end_equity: Decimal,
    cash_flows: list[tuple[datetime, Decimal]],
    started_at: datetime,
    ended_at: datetime,
) -> Decimal | None:
    total_seconds = Decimal(str(max((ended_at - started_at).total_seconds(), 0)))
    external_flow = sum((amount for _, amount in cash_flows), ZERO)
    trading_pnl = end_equity - start_equity - external_flow
    if total_seconds == ZERO:
        denominator = start_equity
    else:
        weighted_flows = ZERO
        for occurred_at, amount in cash_flows:
            remaining_seconds = Decimal(
                str(max(min((ended_at - occurred_at).total_seconds(), float(total_seconds)), 0))
            )
            weighted_flows += amount * (remaining_seconds / total_seconds)
        denominator = start_equity + weighted_flows
    if denominator <= ZERO:
        return ZERO if trading_pnl == ZERO else None
    return trading_pnl / denominator


async def thin_live_account_performance_snapshots(
    session: AsyncSession,
    *,
    account_key: str,
    now: datetime | None = None,
) -> None:
    """Thin snapshots older than the loss limit windows to two per hour.

    A snapshot is recorded at every reconciliation. The rolling 24 hour and
    7 day limits need that resolution only inside their windows. Older hours
    keep their last snapshot and the one with the highest index, so the index
    history and the drawdown peak stay exact. Baselines are always kept.
    """

    cutoff = (now or datetime.now(UTC)) - timedelta(
        days=LIVE_PERFORMANCE_SNAPSHOT_FULL_RESOLUTION_DAYS
    )
    await session.execute(
        text(
            """
            delete from trading_account_performance_snapshots as snapshot
            using (
              select
                id,
                row_number() over (
                  partition by date_trunc('hour', recorded_at)
                  order by recorded_at desc
                ) as recency_rank,
                row_number() over (
                  partition by date_trunc('hour', recorded_at)
                  order by performance_index desc, recorded_at desc
                ) as peak_rank
              from trading_account_performance_snapshots
              where account_key = :account_key
                and recorded_at < :cutoff
                and is_baseline is false
            ) as ranked
            where snapshot.id = ranked.id
              and ranked.recency_rank > 1
              and ranked.peak_rank > 1
            """
        ),
        {"account_key": account_key, "cutoff": cutoff},
    )
