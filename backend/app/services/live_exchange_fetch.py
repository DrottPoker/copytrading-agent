"""Paged reads of a live account's fills, funding, ledger, portfolio, and clearinghouse state."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.integrations.hyperliquid_client import HyperliquidClient
from app.services.live_exchange_parsing import (
    decimal_or_none,
    live_cash_flow_pagination_key,
    live_fill_pagination_key,
    live_funding_pagination_key,
    ms_to_datetime,
)

ZERO = Decimal("0")

MAX_LIVE_FILL_RECONCILIATION_PAGES = 10


MAX_LIVE_FILL_HISTORY = 10_000


MAX_LIVE_FUNDING_RECONCILIATION_PAGES = 10


MAX_LIVE_CASH_FLOW_RECONCILIATION_PAGES = 10


@dataclass(frozen=True)
class LivePerpState:
    dex: str
    payload: dict[str, Any]
    complete: bool = True
    error: str | None = None


@dataclass(frozen=True)
class LivePerpSnapshot:
    states: tuple[LivePerpState, ...]
    requested_dexes: tuple[str, ...]
    catalog_complete: bool = True
    catalog_error: str | None = None

    @property
    def complete(self) -> bool:
        return self.catalog_complete and all(state.complete for state in self.states)

    @property
    def incomplete_dexes(self) -> tuple[str, ...]:
        return tuple(state.dex for state in self.states if not state.complete)

    @property
    def component_errors(self) -> dict[str, str]:
        errors = {
            f"perp:{state.dex or 'default'}": state.error for state in self.states if state.error
        }
        if self.catalog_error:
            errors["perp_catalog"] = self.catalog_error
        return errors


@dataclass(frozen=True)
class LiveFillFetchResult:
    fills: tuple[dict[str, Any], ...]
    complete: bool
    pages: int
    next_start_time_ms: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class LiveFundingFetchResult:
    payments: tuple[dict[str, Any], ...]
    complete: bool
    pages: int
    next_start_time_ms: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class LiveCashFlowFetchResult:
    updates: tuple[dict[str, Any], ...]
    complete: bool
    pages: int
    next_start_time_ms: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class LivePortfolioHistoryResult:
    account_values: tuple[tuple[datetime, Decimal], ...]
    complete: bool
    error: str | None = None


def remote_state_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    return str(error.get("message") or error.get("type") or "Remote state request failed.")


async def fetch_live_fills_by_time(
    client: HyperliquidClient,
    *,
    user: str,
    start_time_ms: int,
    max_pages: int = MAX_LIVE_FILL_RECONCILIATION_PAGES,
) -> LiveFillFetchResult:
    fills: list[dict[str, Any]] = []
    seen_fill_keys: set[tuple[str, ...]] = set()
    next_start_time_ms = start_time_ms
    pages = 0
    for _ in range(max_pages):
        try:
            batch = await client.user_fills_by_time(
                user=user,
                start_time_ms=next_start_time_ms,
                aggregate_by_time=False,
            )
        except Exception as exc:
            return LiveFillFetchResult(
                fills=tuple(fills),
                complete=False,
                pages=pages,
                next_start_time_ms=next_start_time_ms,
                error=str(exc) or exc.__class__.__name__,
            )
        pages += 1
        if not batch:
            return LiveFillFetchResult(fills=tuple(fills), complete=True, pages=pages)
        new_fills = []
        for fill in batch:
            fill_key = live_fill_pagination_key(fill)
            if fill_key in seen_fill_keys:
                continue
            seen_fill_keys.add(fill_key)
            new_fills.append(fill)
        fills.extend(new_fills)
        timestamps = [
            int(timestamp)
            for fill in batch
            for timestamp in [decimal_or_none(fill.get("time") or fill.get("timestamp"))]
            if timestamp is not None
        ]
        if len(fills) >= MAX_LIVE_FILL_HISTORY:
            return LiveFillFetchResult(
                fills=tuple(fills),
                complete=False,
                pages=pages,
                next_start_time_ms=max(timestamps) if timestamps else next_start_time_ms,
                error="Hyperliquid live fill history reached the 10000-fill availability limit.",
            )
        if len(batch) < 500 or not timestamps:
            return LiveFillFetchResult(fills=tuple(fills), complete=True, pages=pages)
        next_page_start_time_ms = max(timestamps)
        if next_page_start_time_ms < next_start_time_ms or not new_fills:
            return LiveFillFetchResult(
                fills=tuple(fills),
                complete=False,
                pages=pages,
                next_start_time_ms=next_start_time_ms,
                error="Fill reconciliation pagination did not advance.",
            )
        next_start_time_ms = next_page_start_time_ms
    return LiveFillFetchResult(
        fills=tuple(fills),
        complete=False,
        pages=pages,
        next_start_time_ms=next_start_time_ms,
        error=f"Fill reconciliation reached the {max_pages}-page safety limit.",
    )


async def fetch_live_funding_by_time(
    client: HyperliquidClient,
    *,
    user: str,
    start_time_ms: int,
    max_pages: int = MAX_LIVE_FUNDING_RECONCILIATION_PAGES,
) -> LiveFundingFetchResult:
    payments: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, ...]] = set()
    next_start_time_ms = start_time_ms
    pages = 0
    for _ in range(max_pages):
        try:
            batch = await client.user_funding(
                user=user,
                start_time_ms=next_start_time_ms,
            )
        except Exception as exc:
            return LiveFundingFetchResult(
                payments=tuple(payments),
                complete=False,
                pages=pages,
                next_start_time_ms=next_start_time_ms,
                error=str(exc) or exc.__class__.__name__,
            )
        pages += 1
        if not batch:
            return LiveFundingFetchResult(payments=tuple(payments), complete=True, pages=pages)
        new_payments = []
        for payment in batch:
            key = live_funding_pagination_key(payment)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            new_payments.append(payment)
        payments.extend(new_payments)
        timestamps = [
            int(timestamp)
            for payment in batch
            for timestamp in [decimal_or_none(payment.get("time"))]
            if timestamp is not None
        ]
        if len(batch) < 500 or not timestamps:
            return LiveFundingFetchResult(payments=tuple(payments), complete=True, pages=pages)
        next_page_start_time_ms = max(timestamps)
        if next_page_start_time_ms < next_start_time_ms or not new_payments:
            return LiveFundingFetchResult(
                payments=tuple(payments),
                complete=False,
                pages=pages,
                next_start_time_ms=next_start_time_ms,
                error="Funding reconciliation pagination did not advance.",
            )
        next_start_time_ms = next_page_start_time_ms
    return LiveFundingFetchResult(
        payments=tuple(payments),
        complete=False,
        pages=pages,
        next_start_time_ms=next_start_time_ms,
        error=f"Funding reconciliation reached the {max_pages}-page safety limit.",
    )


async def fetch_live_portfolio_history(
    client: HyperliquidClient,
    *,
    user: str,
) -> LivePortfolioHistoryResult:
    try:
        payload = await client.portfolio(user=user)
    except Exception as exc:
        return LivePortfolioHistoryResult(
            account_values=(),
            complete=False,
            error=str(exc) or exc.__class__.__name__,
        )
    return parse_live_portfolio_history(payload)


def parse_live_portfolio_history(payload: list[Any]) -> LivePortfolioHistoryResult:
    all_time_payload: dict[str, Any] | None = None
    for item in payload:
        if (
            isinstance(item, list | tuple)
            and len(item) >= 2
            and str(item[0]) == "allTime"
            and isinstance(item[1], dict)
        ):
            all_time_payload = item[1]
            break
    if all_time_payload is None:
        return LivePortfolioHistoryResult(
            account_values=(),
            complete=False,
            error="Hyperliquid portfolio did not include allTime account history.",
        )
    raw_history = all_time_payload.get("accountValueHistory")
    if not isinstance(raw_history, list):
        return LivePortfolioHistoryResult(
            account_values=(),
            complete=False,
            error="Hyperliquid allTime portfolio history has an unexpected shape.",
        )

    values_by_time: dict[datetime, Decimal] = {}
    for item in raw_history:
        if not isinstance(item, list | tuple) or len(item) < 2:
            continue
        recorded_at = ms_to_datetime(decimal_or_none(item[0]))
        equity = decimal_or_none(item[1])
        if recorded_at is None or equity is None or equity < ZERO:
            continue
        values_by_time[recorded_at] = equity
    account_values = tuple(sorted(values_by_time.items(), key=lambda item: item[0]))
    if not account_values:
        return LivePortfolioHistoryResult(
            account_values=(),
            complete=False,
            error="Hyperliquid allTime portfolio history did not contain valid account values.",
        )
    return LivePortfolioHistoryResult(account_values=account_values, complete=True)


async def fetch_live_cash_flows_by_time(
    client: HyperliquidClient,
    *,
    user: str,
    start_time_ms: int,
    end_time_ms: int,
    max_pages: int = MAX_LIVE_CASH_FLOW_RECONCILIATION_PAGES,
) -> LiveCashFlowFetchResult:
    updates: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, ...]] = set()
    next_start_time_ms = start_time_ms
    pages = 0
    for _ in range(max_pages):
        try:
            batch = await client.user_non_funding_ledger_updates(
                user=user,
                start_time_ms=next_start_time_ms,
                end_time_ms=end_time_ms,
            )
        except Exception as exc:
            return LiveCashFlowFetchResult(
                updates=tuple(updates),
                complete=False,
                pages=pages,
                next_start_time_ms=next_start_time_ms,
                error=str(exc) or exc.__class__.__name__,
            )
        pages += 1
        if not batch:
            return LiveCashFlowFetchResult(updates=tuple(updates), complete=True, pages=pages)
        new_updates = []
        for update in batch:
            key = live_cash_flow_pagination_key(update)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            new_updates.append(update)
        updates.extend(new_updates)
        timestamps = [
            int(timestamp)
            for update in batch
            for timestamp in [decimal_or_none(update.get("time"))]
            if timestamp is not None
        ]
        if len(batch) < 500 or not timestamps:
            return LiveCashFlowFetchResult(updates=tuple(updates), complete=True, pages=pages)
        next_page_start_time_ms = max(timestamps)
        if next_page_start_time_ms < next_start_time_ms or not new_updates:
            return LiveCashFlowFetchResult(
                updates=tuple(updates),
                complete=False,
                pages=pages,
                next_start_time_ms=next_start_time_ms,
                error="Cash flow reconciliation pagination did not advance.",
            )
        next_start_time_ms = next_page_start_time_ms
    return LiveCashFlowFetchResult(
        updates=tuple(updates),
        complete=False,
        pages=pages,
        next_start_time_ms=next_start_time_ms,
        error=f"Cash flow reconciliation reached the {max_pages}-page safety limit.",
    )


async def fetch_live_perp_states(
    client: HyperliquidClient,
    *,
    user_address: str,
) -> LivePerpSnapshot:
    states: list[LivePerpState] = []
    try:
        default_payload = await client.clearinghouse_state(user=user_address)
    except Exception as exc:
        states.append(
            LivePerpState(
                dex="",
                payload={},
                complete=False,
                error=str(exc) or exc.__class__.__name__,
            )
        )
    else:
        states.append(LivePerpState(dex="", payload=default_payload))

    dex_names, catalog_complete, catalog_error = await fetch_live_perp_dex_catalog(client)
    for dex in dex_names:
        try:
            payload = await client.clearinghouse_state(user=user_address, dex=dex)
        except Exception as exc:
            states.append(
                LivePerpState(
                    dex=dex,
                    payload={},
                    complete=False,
                    error=str(exc) or exc.__class__.__name__,
                )
            )
        else:
            states.append(LivePerpState(dex=dex, payload=payload))
    return LivePerpSnapshot(
        states=tuple(states),
        requested_dexes=("", *dex_names),
        catalog_complete=catalog_complete,
        catalog_error=catalog_error,
    )


async def fetch_live_perp_dex_catalog(
    client: HyperliquidClient,
) -> tuple[list[str], bool, str | None]:
    try:
        payload = await client.perp_dexs()
    except Exception as exc:
        return [], False, str(exc) or exc.__class__.__name__
    names: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return sorted(set(names)), True, None


async def fetch_live_perp_dex_names(client: HyperliquidClient) -> list[str]:
    names, _, _ = await fetch_live_perp_dex_catalog(client)
    return names


async def fetch_live_spot_state(
    client: HyperliquidClient,
    *,
    user_address: str,
) -> dict[str, Any]:
    try:
        return await client.spot_clearinghouse_state(user=user_address)
    except Exception as exc:
        return {"error": {"message": str(exc), "type": exc.__class__.__name__}}


async def fetch_live_user_abstraction(
    client: HyperliquidClient,
    *,
    user_address: str,
) -> Any:
    try:
        return await client.user_abstraction(user=user_address)
    except Exception as exc:
        return {"error": {"message": str(exc), "type": exc.__class__.__name__}}


def normalize_live_perp_snapshot(
    perp_states: LivePerpSnapshot | list[LivePerpState],
) -> LivePerpSnapshot:
    if isinstance(perp_states, LivePerpSnapshot):
        return perp_states
    states = tuple(perp_states)
    return LivePerpSnapshot(
        states=states,
        requested_dexes=tuple(state.dex for state in states),
        catalog_complete=True,
    )


def normalized_live_asset_positions(raw_positions: Any, *, dex: str) -> list[dict[str, Any]]:
    if not isinstance(raw_positions, list):
        return []

    positions: list[dict[str, Any]] = []
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        raw_position = item.get("position")
        if not isinstance(raw_position, dict):
            positions.append(item)
            continue

        coin = str(raw_position.get("coin") or "")
        normalized_coin = live_coin_with_dex(coin=coin, dex=dex)
        if normalized_coin == coin and not dex:
            positions.append(item)
            continue

        normalized_position = {**raw_position, "coin": normalized_coin}
        normalized_item = {**item, "position": normalized_position}
        if dex:
            normalized_item["dex"] = dex
        positions.append(normalized_item)
    return positions


def live_coin_with_dex(*, coin: str, dex: str) -> str:
    resolved_coin = str(coin or "").strip()
    resolved_dex = str(dex or "").strip()
    if not resolved_coin or not resolved_dex or ":" in resolved_coin:
        return resolved_coin
    return f"{resolved_dex}:{resolved_coin}"


def live_dex_from_coin(coin: str) -> str:
    value = str(coin or "").strip()
    if ":" not in value:
        return ""
    return value.split(":", maxsplit=1)[0].strip()
