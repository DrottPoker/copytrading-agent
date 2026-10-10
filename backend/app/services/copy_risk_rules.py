"""Account and source loss limits shared by paper and live.

Kept free of service imports so both engines, including the live trading
service, can use the same rules without import cycles.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.core.config import Settings

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class AccountLossState:
    daily_loss_pct: Decimal
    weekly_loss_pct: Decimal
    drawdown_pct: Decimal


@dataclass(frozen=True)
class AccountLossBreach:
    rule: str
    label: str
    observed: Decimal
    limit: Decimal


def source_loss_limit_usd(*, equity_usd: Decimal, settings: Settings) -> Decimal | None:
    """Loss on one source that stops new entries from it, or None when disabled."""

    if settings.live_trading_max_source_loss_pct <= ZERO:
        return None
    return settings.live_trading_max_source_loss_pct * equity_usd


def account_loss_breach(
    state: AccountLossState | None,
    *,
    settings: Settings,
) -> AccountLossBreach | None:
    """First breached account loss limit: rolling 24 hours, 7 days, or drawdown."""

    if state is None:
        return None
    for rule, label, observed, limit in (
        (
            "max_daily_loss",
            "rolling 24 hour loss",
            state.daily_loss_pct,
            settings.live_trading_max_daily_loss_pct,
        ),
        (
            "max_weekly_loss",
            "rolling 7 day loss",
            state.weekly_loss_pct,
            settings.live_trading_max_weekly_loss_pct,
        ),
        (
            "max_drawdown",
            "drawdown from peak",
            state.drawdown_pct,
            settings.live_trading_max_drawdown_pct,
        ),
    ):
        if limit > ZERO and observed >= limit:
            return AccountLossBreach(rule=rule, label=label, observed=observed, limit=limit)
    return None


def performance_loss_pct(current_index: Decimal, reference_index: Decimal) -> Decimal:
    if reference_index <= ZERO:
        return ZERO
    return max(ONE - current_index / reference_index, ZERO)
