"""What the last exchange reconciliation says about a live account: mode, equity, and freshness."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.config import Settings
from app.db.models import (
    TradingAccount,
)
from app.services.live_exchange_parsing import (
    decimal_or_none,
)

ZERO = Decimal("0")

LIVE_CASH_FLOW_BACKFILL_VERSION = 1


LIVE_PERFORMANCE_BACKFILL_VERSION = 2


LIVE_CAPITAL_MODE_UNIFIED = "unified"


LIVE_CAPITAL_MODE_STANDARD_PER_DEX = "standard_per_dex"


LIVE_CAPITAL_MODES = {LIVE_CAPITAL_MODE_UNIFIED, LIVE_CAPITAL_MODE_STANDARD_PER_DEX}


def live_capital_mode(settings: Settings) -> str:
    value = str(settings.live_trading_capital_mode or "").strip()
    if value in LIVE_CAPITAL_MODES:
        return value
    return LIVE_CAPITAL_MODE_UNIFIED


def account_last_reconciliation(account: TradingAccount) -> dict[str, Any]:
    payload = account.config_payload if isinstance(account.config_payload, dict) else {}
    last_reconciliation = payload.get("lastReconciliation")
    return last_reconciliation if isinstance(last_reconciliation, dict) else {}


def account_last_reconciliation_attempt(account: TradingAccount) -> dict[str, Any]:
    payload = account.config_payload if isinstance(account.config_payload, dict) else {}
    attempt = payload.get("lastReconciliationAttempt")
    if isinstance(attempt, dict):
        return attempt
    return account_last_reconciliation(account)


def live_reconciliation_status(account: TradingAccount) -> str:
    attempt = account_last_reconciliation_attempt(account)
    status = str(attempt.get("status") or "").strip()
    if status in {"complete", "partial", "failed"}:
        return status
    return "complete" if account.last_reconciled_at is not None else "never"


def live_performance_backfill_version(account: TradingAccount) -> int:
    payload = account.config_payload if isinstance(account.config_payload, dict) else {}
    performance = payload.get("performanceTracking")
    if not isinstance(performance, dict):
        return 0
    try:
        return int(performance.get("historicalBackfillVersion") or 0)
    except TypeError, ValueError:
        return 0


def live_cash_flow_backfill_version(account: TradingAccount) -> int:
    payload = account.config_payload if isinstance(account.config_payload, dict) else {}
    cash_flow_tracking = payload.get("cashFlowTracking")
    if not isinstance(cash_flow_tracking, dict):
        return 0
    try:
        return int(cash_flow_tracking.get("historicalBackfillVersion") or 0)
    except TypeError, ValueError:
        return 0


def live_perp_equity_usd(account: TradingAccount, *, dex: str | None = None) -> Decimal:
    last_reconciliation = account_last_reconciliation(account)
    if dex is not None:
        normalized_dex = dex or "default"
        states = last_reconciliation.get("perpStates")
        if isinstance(states, list):
            for state in states:
                if not isinstance(state, dict):
                    continue
                if str(state.get("dex") or "default") == normalized_dex:
                    return decimal_or_none(state.get("accountValue")) or ZERO
        return ZERO
    if "perpEquityUsd" in last_reconciliation:
        return decimal_or_none(last_reconciliation.get("perpEquityUsd")) or ZERO
    return account.equity_usd or ZERO


def live_unified_equity_usd(account: TradingAccount) -> Decimal:
    last_reconciliation = account_last_reconciliation(account)
    return decimal_or_none(last_reconciliation.get("unifiedEquityUsd")) or ZERO


def live_unified_available_usd(account: TradingAccount) -> Decimal:
    last_reconciliation = account_last_reconciliation(account)
    return decimal_or_none(last_reconciliation.get("unifiedAvailableUsd")) or ZERO


def live_spot_balance_usd(account: TradingAccount) -> Decimal:
    last_reconciliation = account_last_reconciliation(account)
    return decimal_or_none(last_reconciliation.get("spotUsdcTotalUsd")) or ZERO


def live_spot_available_usd(account: TradingAccount) -> Decimal:
    last_reconciliation = account_last_reconciliation(account)
    return decimal_or_none(last_reconciliation.get("spotUsdcAvailableUsd")) or ZERO


def live_tradable_equity_usd(
    account: TradingAccount,
    *,
    settings: Settings,
    dex: str | None = None,
) -> Decimal:
    if live_capital_mode(settings) == LIVE_CAPITAL_MODE_UNIFIED:
        return live_unified_available_usd(account)
    return live_perp_equity_usd(account, dex=dex)


def live_reconciliation_is_fresh(
    account: TradingAccount,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    reconciled_at = account.last_reconciled_at
    if reconciled_at is None:
        return False
    if reconciled_at.tzinfo is None:
        reconciled_at = reconciled_at.replace(tzinfo=UTC)
    age = (now or datetime.now(UTC)) - reconciled_at.astimezone(UTC)
    return (
        timedelta(0)
        <= age
        <= timedelta(seconds=settings.live_trading_reconciliation_max_snapshot_age_seconds)
    )
