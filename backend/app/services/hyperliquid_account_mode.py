"""How a Hyperliquid account holds its collateral.

Hyperliquid calls this the account abstraction mode. A standard account keeps
perp collateral per perp dex, so its perp clearinghouse state is its equity. A
unified or portfolio margin account keeps every balance in the spot
clearinghouse state: its spot USDC total is marked to market and is the equity,
and the perp clearinghouse account value is not meaningful.

Kept free of other service imports so the copy engines, scoring, and live
trading share one reading of the mode.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")
UNIFIED_USER_ABSTRACTION_KEYS = {
    "portfolio",
    "portfolioaccount",
    "portfoliomargin",
    "unified",
    "unifiedaccount",
}
STANDARD_USER_ABSTRACTION_KEYS = {
    "classic",
    "default",
    "disabled",
    "none",
    "standard",
    "standardaccount",
}


def normalize_user_abstraction(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, dict):
        for key in (
            "accountAbstraction",
            "abstraction",
            "mode",
            "type",
            "userAbstraction",
            "value",
        ):
            nested = normalize_user_abstraction(value.get(key))
            if nested is not None:
                return nested
        return None
    normalized = str(value).strip()
    return normalized or None


def user_abstraction_key(value: Any) -> str | None:
    normalized = normalize_user_abstraction(value)
    if normalized is None:
        return None
    key = re.sub(r"[^a-z0-9]", "", normalized.lower())
    return key or None


def user_abstraction_is_unified(value: Any) -> bool:
    key = user_abstraction_key(value)
    return key in UNIFIED_USER_ABSTRACTION_KEYS if key is not None else False


def user_abstraction_is_standard(value: Any) -> bool:
    key = user_abstraction_key(value)
    return key in STANDARD_USER_ABSTRACTION_KEYS if key is not None else False


def live_spot_usdc_total(spot_state: dict[str, Any]) -> Decimal:
    balances = spot_state.get("balances")
    if not isinstance(balances, list):
        return ZERO
    total = ZERO
    for item in balances:
        if not isinstance(item, dict):
            continue
        if str(item.get("coin") or "").upper() == "USDC":
            total += _decimal_or_none(item.get("total")) or ZERO
    return total


def live_spot_usdc_available(spot_state: dict[str, Any]) -> Decimal:
    values = spot_state.get("tokenToAvailableAfterMaintenance")
    if isinstance(values, list):
        total = ZERO
        matched_usdc = False
        for item in values:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            token_id = _decimal_or_none(item[0])
            if token_id == ZERO:
                matched_usdc = True
                total += _decimal_or_none(item[1]) or ZERO
        if matched_usdc:
            return total

    balances = spot_state.get("balances")
    if not isinstance(balances, list):
        return ZERO
    total = ZERO
    for item in balances:
        if not isinstance(item, dict):
            continue
        if str(item.get("coin") or "").upper() != "USDC":
            continue
        balance_total = _decimal_or_none(item.get("total")) or ZERO
        hold = _decimal_or_none(item.get("hold")) or ZERO
        total += max(balance_total - hold, ZERO)
    return total


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation, TypeError, ValueError:
        return None
