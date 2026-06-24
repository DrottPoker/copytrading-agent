import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.wallet import normalize_wallet_address
from app.services.wallet_current_state_service import (
    WalletPerpClearinghouseState,
    load_wallet_account_value_summary,
    summarize_perp_clearinghouse_states,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeaderboardWalletCandidate:
    rank: int
    address: str
    display_name: str | None
    account_value: str | None
    window_pnl: str | None
    window_roi: str | None
    account_role: str
    parent_address: str | None
    subaccount_name: str | None
    label: str


def select_ranked_rows(
    rows: list[Any],
    *,
    limit: int,
    window: str,
    sort_metric: str,
) -> list[dict[str, Any]]:
    dict_rows = [row for row in rows if isinstance(row, dict)]
    return sorted(
        dict_rows,
        key=lambda row: leaderboard_sort_value(row, window=window, metric=sort_metric),
        reverse=True,
    )[:limit]


def leaderboard_sort_value(row: dict[str, Any], *, window: str, metric: str) -> Decimal:
    performance = get_window(row, window)
    if performance is None:
        return Decimal("0")
    return decimal_value(performance.get(metric))


async def load_subaccount_wallet_candidates(
    *,
    client: HyperliquidClient,
    master_address: str,
    rank: int,
    display_name: str | None,
    row: dict[str, Any],
    window: str,
    max_subaccounts: int,
) -> list[LeaderboardWalletCandidate]:
    if max_subaccounts <= 0:
        return []

    try:
        subaccounts = await client.post_info({"type": "subAccounts", "user": master_address})
    except Exception as exc:
        logger.warning("subaccount lookup failed master=%s error=%s", master_address, exc)
        return []
    if not isinstance(subaccounts, list):
        return []

    candidates: list[LeaderboardWalletCandidate] = []
    for subaccount in subaccounts[:max_subaccounts]:
        if not isinstance(subaccount, dict):
            continue
        raw_subaccount_address = subaccount.get("subAccountUser")
        if not isinstance(raw_subaccount_address, str):
            continue
        try:
            subaccount_address = normalize_wallet_address(raw_subaccount_address)
        except ValueError:
            continue

        subaccount_name = (
            subaccount.get("name") if isinstance(subaccount.get("name"), str) else None
        )
        subaccount_value = subaccount_account_value(subaccount)
        if decimal_value(subaccount_value) <= Decimal("0"):
            subaccount_value = await resolve_subaccount_account_value(
                client=client,
                subaccount=subaccount,
                subaccount_address=subaccount_address,
            )
        window_performance = get_window(row, window) or {}
        candidates.append(
            LeaderboardWalletCandidate(
                rank=rank,
                address=subaccount_address,
                display_name=display_name,
                account_value=subaccount_value,
                window_pnl=string_or_none(window_performance.get("pnl")),
                window_roi=string_or_none(window_performance.get("roi")),
                account_role="subaccount",
                parent_address=master_address,
                subaccount_name=subaccount_name,
                label=build_subaccount_label(
                    rank=rank,
                    window=window,
                    parent_display_name=display_name,
                    subaccount_name=subaccount_name,
                ),
            )
        )
    return candidates


def subaccount_account_value(subaccount: dict[str, Any]) -> str | None:
    clearinghouse_state = subaccount.get("clearinghouseState")
    if not isinstance(clearinghouse_state, dict):
        return None
    margin_summary = clearinghouse_state.get("marginSummary")
    if not isinstance(margin_summary, dict):
        return None
    value = margin_summary.get("accountValue")
    return str(value) if value is not None else None


async def resolve_subaccount_account_value(
    *,
    client: HyperliquidClient,
    subaccount: dict[str, Any],
    subaccount_address: str,
) -> str | None:
    fallback = subaccount_account_value(subaccount)
    clearinghouse_state = subaccount.get("clearinghouseState")
    if not isinstance(clearinghouse_state, dict):
        return fallback
    perp_summary = summarize_perp_clearinghouse_states(
        [WalletPerpClearinghouseState(dex="", payload=clearinghouse_state)]
    )
    account_summary = await load_wallet_account_value_summary(
        client=client,
        address=subaccount_address,
        perp_summary=perp_summary,
    )
    if account_summary.error is not None:
        return fallback
    if account_summary.account_value_usd > Decimal("0"):
        return str(account_summary.account_value_usd)
    return fallback


def build_subaccount_label(
    *,
    rank: int,
    window: str,
    parent_display_name: str | None,
    subaccount_name: str | None,
) -> str:
    parent = parent_display_name or f"HL {window_label(window)} #{rank}"
    if subaccount_name:
        return f"{parent} / {subaccount_name}"
    return f"{parent} / subaccount"


def get_window(row: dict[str, Any], window: str) -> dict[str, Any] | None:
    windows = row.get("windowPerformances")
    if not isinstance(windows, list):
        return None
    for item in windows:
        if (
            isinstance(item, list)
            and len(item) == 2
            and item[0] == window
            and isinstance(item[1], dict)
        ):
            return item[1]
    return None


def decimal_value(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def window_label(window: str) -> str:
    return {
        "day": "24H",
        "week": "7D",
        "month": "30D",
        "allTime": "All-time",
    }.get(window, window)
