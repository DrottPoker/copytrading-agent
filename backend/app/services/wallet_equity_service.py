"""Equity-curve return and drawdown from Hyperliquid portfolio history.

Copies are sized from the source wallet's equity, so the wallet's return and
drawdown on its own equity describe how a proportional copy behaves. Portfolio
PnL history excludes deposits and withdrawals, so a time-weighted return built
from PnL changes divided by the preceding account value is not distorted by
transfers, and the curve includes unrealized PnL at each point.
"""

import asyncio
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import WalletEquityMetric
from app.integrations.hyperliquid_client import HyperliquidClient

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
ONE = Decimal("1")
RATIO_QUANT = Decimal("0.000001")
USD_QUANT = Decimal("0.01")
DAY_MS = 86_400_000
# A failed portfolio read is retried sooner than the regular refresh interval.
UNAVAILABLE_RETRY_HOURS = 1

EquityStatus = Literal["ok", "insufficient", "unavailable"]
EquityBasis = Literal["perp", "total"]
# Month history gives the window, week history adds resolution to the latest days.
PORTFOLIO_PERIODS: dict[EquityBasis, tuple[str, str]] = {
    "perp": ("perpMonth", "perpWeek"),
    "total": ("month", "week"),
}


@dataclass(frozen=True)
class EquityPoint:
    time_ms: int
    account_value_usd: Decimal
    pnl_usd: Decimal


@dataclass(frozen=True)
class WalletEquityMetrics:
    status: EquityStatus
    basis: EquityBasis | None = None
    window_start_ms: int | None = None
    window_end_ms: int | None = None
    return_pct: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    average_account_value_usd: Decimal | None = None
    latest_account_value_usd: Decimal | None = None
    point_count: int = 0
    error: str | None = None

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


def portfolio_period_points(payload: Any, period: str) -> list[EquityPoint]:
    """Pair account value and cumulative PnL points of one portfolio period."""

    if not isinstance(payload, list):
        return []
    period_payload: dict[str, Any] | None = None
    for item in payload:
        if (
            isinstance(item, list | tuple)
            and len(item) >= 2
            and str(item[0]) == period
            and isinstance(item[1], dict)
        ):
            period_payload = item[1]
            break
    if period_payload is None:
        return []
    account_values = history_values(period_payload.get("accountValueHistory"))
    pnl_values = history_values(period_payload.get("pnlHistory"))
    return [
        EquityPoint(
            time_ms=time_ms,
            account_value_usd=account_value,
            pnl_usd=pnl_values[time_ms],
        )
        for time_ms, account_value in sorted(account_values.items())
        if time_ms in pnl_values and account_value >= ZERO
    ]


def history_values(raw_history: Any) -> dict[int, Decimal]:
    values: dict[int, Decimal] = {}
    if not isinstance(raw_history, list):
        return values
    for item in raw_history:
        if not isinstance(item, list | tuple) or len(item) < 2:
            continue
        try:
            time_ms = int(item[0])
            value = Decimal(str(item[1]))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if value.is_finite():
            values[time_ms] = value
    return values


def interval_returns(
    points: Sequence[EquityPoint],
    *,
    min_account_value_usd: Decimal,
) -> list[tuple[int, int, Decimal]]:
    """Return (start_ms, end_ms, return) per interval between consecutive points.

    An interval that starts below the minimum account value is skipped, because
    a return on a near-empty account says nothing about the trader.
    """

    returns: list[tuple[int, int, Decimal]] = []
    for previous, current in zip(points, points[1:], strict=False):
        if previous.account_value_usd < min_account_value_usd:
            continue
        change = (current.pnl_usd - previous.pnl_usd) / previous.account_value_usd
        returns.append((previous.time_ms, current.time_ms, max(change, -ONE)))
    return returns


def combined_interval_returns(
    month_points: Sequence[EquityPoint],
    week_points: Sequence[EquityPoint],
    *,
    min_account_value_usd: Decimal,
) -> list[tuple[int, int, Decimal]]:
    """Use month intervals up to where week history starts, then week intervals."""

    month_returns = interval_returns(month_points, min_account_value_usd=min_account_value_usd)
    week_returns = interval_returns(week_points, min_account_value_usd=min_account_value_usd)
    if not week_points or not week_returns:
        return month_returns
    week_start_ms = week_points[0].time_ms
    return [interval for interval in month_returns if interval[1] <= week_start_ms] + week_returns


def compound_return_and_drawdown(
    returns: Iterable[tuple[int, int, Decimal]],
) -> tuple[Decimal, Decimal]:
    index = ONE
    peak = ONE
    max_drawdown = ZERO
    for _, _, change in returns:
        index *= ONE + change
        peak = max(peak, index)
        if peak > ZERO:
            max_drawdown = max(max_drawdown, ONE - index / peak)
    return index - ONE, max_drawdown


def equity_metrics_from_portfolio(
    payload: Any,
    *,
    min_points: int,
    min_coverage_days: int,
    min_account_value_usd: Decimal,
) -> WalletEquityMetrics:
    """Build equity metrics, preferring perp equity and falling back to total equity.

    Unified accounts keep collateral in spot, so their perp account value can be
    zero while the wallet trades perps actively.
    """

    best_point_count = 0
    for basis, (month_period, week_period) in PORTFOLIO_PERIODS.items():
        month_points = portfolio_period_points(payload, month_period)
        week_points = portfolio_period_points(payload, week_period)
        funded_points = [
            point for point in month_points if point.account_value_usd >= min_account_value_usd
        ]
        best_point_count = max(best_point_count, len(funded_points))
        if len(funded_points) < min_points:
            continue
        returns = combined_interval_returns(
            month_points,
            week_points,
            min_account_value_usd=min_account_value_usd,
        )
        if not returns:
            continue
        window_start_ms = returns[0][0]
        window_end_ms = returns[-1][1]
        if window_end_ms - window_start_ms < min_coverage_days * DAY_MS:
            continue
        total_return, max_drawdown = compound_return_and_drawdown(returns)
        latest_points = week_points or month_points
        return WalletEquityMetrics(
            status="ok",
            basis=basis,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
            return_pct=total_return.quantize(RATIO_QUANT),
            max_drawdown_pct=max_drawdown.quantize(RATIO_QUANT),
            average_account_value_usd=(
                sum((point.account_value_usd for point in funded_points), ZERO)
                / Decimal(len(funded_points))
            ).quantize(USD_QUANT),
            latest_account_value_usd=latest_points[-1].account_value_usd.quantize(USD_QUANT),
            point_count=len(funded_points),
        )
    return WalletEquityMetrics(
        status="insufficient",
        point_count=best_point_count,
        error="Portfolio history is too short or the account was not funded.",
    )


async def fetch_wallet_equity_metrics(
    client: HyperliquidClient,
    *,
    address: str,
    settings: Settings,
) -> WalletEquityMetrics:
    try:
        payload = await client.portfolio(user=address)
    except Exception as exc:
        logger.warning("wallet equity history fetch failed wallet=%s error=%s", address, exc)
        return WalletEquityMetrics(status="unavailable", error=str(exc) or type(exc).__name__)
    return equity_metrics_from_portfolio(
        payload,
        min_points=settings.scoring_equity_min_points,
        min_coverage_days=settings.scoring_equity_min_coverage_days,
        min_account_value_usd=settings.scoring_equity_min_account_value_usd,
    )


async def store_wallet_equity_metrics(
    session: AsyncSession,
    *,
    address: str,
    metrics: WalletEquityMetrics,
    fetched_at: datetime,
) -> None:
    values = {
        "wallet_address": address,
        "status": metrics.status,
        "basis": metrics.basis,
        "window_start_ms": metrics.window_start_ms,
        "window_end_ms": metrics.window_end_ms,
        "return_pct": metrics.return_pct,
        "max_drawdown_pct": metrics.max_drawdown_pct,
        "average_account_value_usd": metrics.average_account_value_usd,
        "latest_account_value_usd": metrics.latest_account_value_usd,
        "point_count": metrics.point_count,
        "error": metrics.error,
        "fetched_at": fetched_at,
    }
    stmt = insert(WalletEquityMetric).values(values)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["wallet_address"],
            set_={key: getattr(stmt.excluded, key) for key in values if key != "wallet_address"},
        )
    )


async def load_wallet_equity_metrics(
    session: AsyncSession,
    *,
    addresses: Sequence[str] | None = None,
) -> dict[str, tuple[WalletEquityMetrics, datetime]]:
    query = select(WalletEquityMetric)
    if addresses is not None:
        if not addresses:
            return {}
        query = query.where(WalletEquityMetric.wallet_address.in_(list(addresses)))
    result = await session.execute(query)
    return {
        row.wallet_address: (equity_metrics_from_row(row), row.fetched_at)
        for row in result.scalars().all()
    }


def equity_metrics_from_row(row: WalletEquityMetric) -> WalletEquityMetrics:
    return WalletEquityMetrics(
        status=equity_status(row.status),
        basis=equity_basis(row.basis),
        window_start_ms=row.window_start_ms,
        window_end_ms=row.window_end_ms,
        return_pct=row.return_pct,
        max_drawdown_pct=row.max_drawdown_pct,
        average_account_value_usd=row.average_account_value_usd,
        latest_account_value_usd=row.latest_account_value_usd,
        point_count=row.point_count,
        error=row.error,
    )


def equity_status(value: str) -> EquityStatus:
    if value == "ok":
        return "ok"
    if value == "insufficient":
        return "insufficient"
    return "unavailable"


def equity_basis(value: str | None) -> EquityBasis | None:
    if value == "perp":
        return "perp"
    if value == "total":
        return "total"
    return None


def equity_refresh_due(
    existing: tuple[WalletEquityMetrics, datetime] | None,
    *,
    now: datetime,
    refresh_hours: int,
) -> bool:
    if existing is None:
        return True
    metrics, fetched_at = existing
    hours = (
        min(UNAVAILABLE_RETRY_HOURS, refresh_hours)
        if metrics.status == "unavailable"
        else refresh_hours
    )
    return now - fetched_at >= timedelta(hours=hours)


async def refresh_wallet_equity_metrics(
    session: AsyncSession,
    *,
    addresses_by_priority: Sequence[str],
    settings: Settings,
    now: datetime | None = None,
    client: HyperliquidClient | None = None,
) -> int:
    """Refresh stale equity metrics, highest priority first, within the run budget."""

    resolved_now = now or datetime.now(UTC)
    existing = await load_wallet_equity_metrics(session, addresses=addresses_by_priority)
    due = [
        address
        for address in addresses_by_priority
        if equity_refresh_due(
            existing.get(address),
            now=resolved_now,
            refresh_hours=settings.scoring_equity_refresh_hours,
        )
    ][: settings.scoring_equity_max_wallets_per_run]
    if not due:
        return 0
    if client is None:
        async with HyperliquidClient(settings) as owned_client:
            return await refresh_wallet_equity_metrics(
                session,
                addresses_by_priority=addresses_by_priority,
                settings=settings,
                now=resolved_now,
                client=owned_client,
            )

    semaphore = asyncio.Semaphore(settings.scoring_equity_concurrency)

    async def fetch(address: str) -> WalletEquityMetrics:
        async with semaphore:
            return await fetch_wallet_equity_metrics(client, address=address, settings=settings)

    fetched = await asyncio.gather(*(fetch(address) for address in due))
    for address, metrics in zip(due, fetched, strict=True):
        await store_wallet_equity_metrics(
            session,
            address=address,
            metrics=metrics,
            fetched_at=resolved_now,
        )
    await session.commit()
    return len(due)
