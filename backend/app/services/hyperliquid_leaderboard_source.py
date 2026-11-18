import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.wallet import normalize_wallet_address
from app.services.hyperliquid_account_mode import live_spot_usdc_total
from app.services.wallet_current_state_service import (
    WalletPerpClearinghouseState,
    load_wallet_account_value_summary,
    summarize_perp_clearinghouse_states,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MasterSubaccount:
    address: str
    name: str | None
    account_value: str | None


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


@dataclass(frozen=True)
class LeaderboardScreen:
    """Thresholds a leaderboard row must meet to become a screened candidate."""

    min_account_value_usd: Decimal
    max_account_value_usd: Decimal
    min_month_pnl_usd: Decimal
    min_month_roi: Decimal
    max_month_roi: Decimal
    # Month volume divided by account value.
    min_month_turnover: Decimal
    max_month_turnover: Decimal
    min_all_time_to_month_pnl_ratio: Decimal
    require_positive_week_pnl: bool


def screen_leaderboard_rows(rows: list[Any], *, screen: LeaderboardScreen) -> list[dict[str, Any]]:
    """Rows passing the screen, highest month ROI first.

    The screen reads only what the leaderboard download already holds, so it can
    look at every listed wallet without spending any Hyperliquid request budget.
    """

    passing: list[tuple[Decimal, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        month_roi = leaderboard_screen_month_roi(row, screen=screen)
        if month_roi is not None:
            passing.append((month_roi, row))
    passing.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in passing]


def leaderboard_screen_month_roi(
    row: dict[str, Any],
    *,
    screen: LeaderboardScreen,
) -> Decimal | None:
    """The row's month ROI when it passes the screen, otherwise None."""

    account_value = decimal_value(row.get("accountValue"))
    if account_value <= Decimal("0"):
        return None
    if not screen.min_account_value_usd <= account_value <= screen.max_account_value_usd:
        return None
    month = get_window(row, "month") or {}
    month_pnl = decimal_value(month.get("pnl"))
    if month_pnl <= Decimal("0") or month_pnl < screen.min_month_pnl_usd:
        return None
    month_roi = decimal_value(month.get("roi"))
    if not screen.min_month_roi <= month_roi <= screen.max_month_roi:
        return None
    # Too little volume is a held position, too much is a market maker.
    month_turnover = decimal_value(month.get("vlm")) / account_value
    if not screen.min_month_turnover <= month_turnover <= screen.max_month_turnover:
        return None
    if screen.require_positive_week_pnl:
        week = get_window(row, "week") or {}
        if decimal_value(week.get("pnl")) <= Decimal("0"):
            return None
    # A month that outweighs the whole history is one good month, not a record.
    all_time = get_window(row, "allTime") or {}
    if decimal_value(all_time.get("pnl")) < month_pnl * screen.min_all_time_to_month_pnl_ratio:
        return None
    return month_roi


def leaderboard_sort_value(row: dict[str, Any], *, window: str, metric: str) -> Decimal:
    performance = get_window(row, window)
    if performance is None:
        return Decimal("0")
    return decimal_value(performance.get(metric))


async def fetch_master_subaccounts(
    *,
    client: HyperliquidClient,
    master_address: str,
    max_subaccounts: int,
) -> list[MasterSubaccount] | None:
    """Subaccounts of one master wallet, or None when the lookup failed."""

    if max_subaccounts <= 0:
        return []

    try:
        payload = await client.post_info({"type": "subAccounts", "user": master_address})
    except Exception as exc:
        logger.warning("subaccount lookup failed master=%s error=%s", master_address, exc)
        return None
    if not isinstance(payload, list):
        return []

    subaccounts: list[MasterSubaccount] = []
    for subaccount in payload[:max_subaccounts]:
        if not isinstance(subaccount, dict):
            continue
        raw_subaccount_address = subaccount.get("subAccountUser")
        if not isinstance(raw_subaccount_address, str):
            continue
        try:
            subaccount_address = normalize_wallet_address(raw_subaccount_address)
        except ValueError:
            continue

        subaccount_value = subaccount_account_value(subaccount)
        if decimal_value(subaccount_value) <= Decimal("0"):
            subaccount_value = await resolve_subaccount_account_value(
                client=client,
                subaccount=subaccount,
                subaccount_address=subaccount_address,
            )
        subaccounts.append(
            MasterSubaccount(
                address=subaccount_address,
                name=subaccount.get("name") if isinstance(subaccount.get("name"), str) else None,
                account_value=subaccount_value,
            )
        )
    return subaccounts


def subaccount_wallet_candidates(
    subaccounts: Sequence[MasterSubaccount],
    *,
    master_address: str,
    rank: int,
    display_name: str | None,
    row: dict[str, Any],
    window: str,
) -> list[LeaderboardWalletCandidate]:
    window_performance = get_window(row, window) or {}
    return [
        LeaderboardWalletCandidate(
            rank=rank,
            address=subaccount.address,
            display_name=display_name,
            account_value=subaccount.account_value,
            window_pnl=string_or_none(window_performance.get("pnl")),
            window_roi=string_or_none(window_performance.get("roi")),
            account_role="subaccount",
            parent_address=master_address,
            subaccount_name=subaccount.name,
            label=build_subaccount_label(
                rank=rank,
                window=window,
                parent_display_name=display_name,
                subaccount_name=subaccount.name,
            ),
        )
        for subaccount in subaccounts
    ]


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
    # The lookup already carries the spot balances. A unified account is worth its
    # spot USDC, so without any the account mode cannot change the value.
    spot_state = subaccount.get("spotState")
    known_spot_state = spot_state if isinstance(spot_state, dict) else None
    if known_spot_state is not None and live_spot_usdc_total(known_spot_state) <= Decimal("0"):
        return fallback
    perp_summary = summarize_perp_clearinghouse_states(
        [WalletPerpClearinghouseState(dex="", payload=clearinghouse_state)]
    )
    account_summary = await load_wallet_account_value_summary(
        client=client,
        address=subaccount_address,
        perp_summary=perp_summary,
        spot_state=known_spot_state,
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
    except InvalidOperation, TypeError, ValueError:
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
