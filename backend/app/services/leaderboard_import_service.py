import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import Setting, WatchedWallet
from app.integrations.hyperliquid_client import HyperliquidClient
from app.integrations.hyperliquid_leaderboard_client import HyperliquidLeaderboardClient
from app.schemas.fill import WalletFillImportRequest
from app.schemas.leaderboard import (
    LeaderboardFillImport,
    LeaderboardImportResponse,
    LeaderboardWalletImport,
)
from app.schemas.wallet import normalize_wallet_address
from app.services.fill_import_service import (
    FillImportStorageLimitError,
    import_wallet_fills,
    target_fills_for_pages,
)
from app.services.operation_status_service import (
    mark_operation_failed,
    mark_operation_started,
    mark_operation_succeeded,
)
from app.services.wallet_cleanup_service import delete_wallet_related_rows

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeaderboardFillImportTarget:
    address: str
    start_time_ms: int | None = None


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
    notes: str


async def import_top_leaderboard_wallets(
    session: AsyncSession,
    *,
    limit: int = 100,
    settings: Settings | None = None,
    client: HyperliquidLeaderboardClient | None = None,
) -> LeaderboardImportResponse:
    resolved_settings = settings or get_settings()
    await mark_operation_started(
        session,
        key="leaderboard_import",
        payload={
            "limit": limit,
            "window": resolved_settings.leaderboard_import_window,
            "sortMetric": resolved_settings.leaderboard_import_sort_metric,
        },
    )
    try:
        result = await _import_top_leaderboard_wallets(
            session,
            limit=limit,
            settings=resolved_settings,
            client=client,
        )
    except Exception as exc:
        await session.rollback()
        await mark_operation_failed(
            session,
            key="leaderboard_import",
            error=str(exc) or exc.__class__.__name__,
            payload={
                "limit": limit,
                "window": resolved_settings.leaderboard_import_window,
                "sortMetric": resolved_settings.leaderboard_import_sort_metric,
            },
        )
        raise

    await mark_operation_succeeded(
        session,
        key="leaderboard_import",
        payload={
            "fetched": result.fetched,
            "valid": result.valid,
            "inserted": result.inserted,
            "duplicate": result.duplicate,
            "skipped": result.skipped,
            "limit": result.limit,
            "window": result.window,
            "sortMetric": result.sort_metric,
            "fillImportWallets": result.fill_import_wallets,
            "fillImportInserted": result.fill_import_inserted,
            "fillImportDuplicate": result.fill_import_duplicate,
            "fillImportFailed": result.fill_import_failed,
            "prunedNonPerpWallets": result.pruned_non_perp_wallets,
        },
    )
    return result


async def _import_top_leaderboard_wallets(
    session: AsyncSession,
    *,
    limit: int = 100,
    settings: Settings | None = None,
    client: HyperliquidLeaderboardClient | None = None,
) -> LeaderboardImportResponse:
    resolved_settings = settings or get_settings()
    window = resolved_settings.leaderboard_import_window
    sort_metric = resolved_settings.leaderboard_import_sort_metric
    leaderboard_client = client or HyperliquidLeaderboardClient(resolved_settings)
    payload = await leaderboard_client.get_leaderboard()
    rows = payload.get("leaderboardRows")
    if not isinstance(rows, list):
        rows = []

    selected_rows = select_ranked_rows(
        rows,
        limit=limit,
        window=window,
        sort_metric=sort_metric,
    )
    ignored_addresses = await load_ignored_wallet_addresses(session)
    info_client = HyperliquidClient(resolved_settings)
    records: list[dict[str, Any]] = []
    valid_wallets: list[LeaderboardWalletImport] = []
    seen_addresses: set[str] = set()
    skipped = 0

    for index, row in enumerate(selected_rows, start=1):
        if not isinstance(row, dict):
            skipped += 1
            continue
        raw_address = row.get("ethAddress")
        if not isinstance(raw_address, str):
            skipped += 1
            continue
        try:
            address = normalize_wallet_address(raw_address)
        except ValueError:
            skipped += 1
            continue

        display_name = row.get("displayName") if isinstance(row.get("displayName"), str) else None
        account_value = str(row["accountValue"]) if row.get("accountValue") is not None else None
        performance = get_window(row, window) or {}
        window_pnl = string_or_none(performance.get("pnl"))
        window_roi = string_or_none(performance.get("roi"))
        candidates = [
            LeaderboardWalletCandidate(
                rank=index,
                address=address,
                display_name=display_name,
                account_value=account_value,
                window_pnl=window_pnl,
                window_roi=window_roi,
                account_role="master",
                parent_address=None,
                subaccount_name=None,
                label=display_name or f"HL {window_label(window)} #{index}",
                notes=build_notes(rank=index, account_value=account_value, row=row, window=window),
            )
        ]
        if resolved_settings.leaderboard_import_subaccounts_enabled:
            candidates.extend(
                await load_subaccount_wallet_candidates(
                    client=info_client,
                    master_address=address,
                    rank=index,
                    display_name=display_name,
                    row=row,
                    window=window,
                    max_subaccounts=resolved_settings.leaderboard_import_max_subaccounts_per_wallet,
                )
            )

        for candidate in candidates:
            if candidate.address in ignored_addresses:
                skipped += 1
                continue
            if candidate.address in seen_addresses:
                skipped += 1
                continue
            seen_addresses.add(candidate.address)
            records.append(record_from_candidate(candidate))
            valid_wallets.append(import_from_candidate(candidate))

    inserted_addresses: list[str] = []
    if records:
        stmt = (
            insert(WatchedWallet)
            .values(records)
            .on_conflict_do_nothing(index_elements=["address"])
            .returning(WatchedWallet.address)
        )
        result = await session.execute(stmt)
        inserted_addresses = list(result.scalars().all())

    await session.commit()
    fill_imports: list[LeaderboardFillImport] = []
    if resolved_settings.leaderboard_auto_import_fills_enabled:
        fill_import_addresses = await load_fill_import_targets(
            session,
            valid_wallets=valid_wallets,
            inserted_addresses=inserted_addresses,
            include_unpolled_duplicates=(
                resolved_settings.leaderboard_auto_import_fills_for_unpolled_duplicates
            ),
            include_ranked_duplicates=(
                resolved_settings.leaderboard_auto_import_fills_for_ranked_wallets
            ),
            overlap_seconds=resolved_settings.leaderboard_auto_import_fills_overlap_seconds,
        )
        fill_imports = await import_leaderboard_wallet_fills(
            session,
            targets=fill_import_addresses,
            days=resolved_settings.leaderboard_auto_import_fills_days,
            max_pages=resolved_settings.leaderboard_auto_import_fills_max_pages,
        )

    pruned_non_perp_addresses = await prune_new_non_perp_leaderboard_wallets(
        session,
        inserted_addresses=inserted_addresses,
        fill_imports=fill_imports,
        settings=resolved_settings,
    )
    kept_inserted_addresses = [
        address for address in inserted_addresses if address not in set(pruned_non_perp_addresses)
    ]
    inserted_set = set(kept_inserted_addresses)
    imported = [wallet for wallet in valid_wallets if wallet.address in inserted_set]
    fill_import_failed = sum(1 for item in fill_imports if item.error is not None)

    return LeaderboardImportResponse(
        fetched=len(selected_rows),
        valid=len(valid_wallets),
        inserted=len(kept_inserted_addresses),
        duplicate=len(valid_wallets) - len(inserted_addresses),
        skipped=skipped,
        limit=limit,
        window=window,
        sort_metric=sort_metric,
        imported=imported,
        fill_imports=fill_imports,
        fill_import_wallets=len(fill_imports),
        fill_import_fetched=sum(item.fetched for item in fill_imports),
        fill_import_inserted=sum(item.inserted for item in fill_imports),
        fill_import_duplicate=sum(item.duplicate for item in fill_imports),
        fill_import_failed=fill_import_failed,
        pruned_non_perp_wallets=len(pruned_non_perp_addresses),
        pruned_non_perp_addresses=pruned_non_perp_addresses,
    )


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
        candidates.append(
            LeaderboardWalletCandidate(
                rank=rank,
                address=subaccount_address,
                display_name=display_name,
                account_value=subaccount_value,
                window_pnl=string_or_none((get_window(row, window) or {}).get("pnl")),
                window_roi=string_or_none((get_window(row, window) or {}).get("roi")),
                account_role="subaccount",
                parent_address=master_address,
                subaccount_name=subaccount_name,
                label=build_subaccount_label(
                    rank=rank,
                    window=window,
                    parent_display_name=display_name,
                    subaccount_name=subaccount_name,
                ),
                notes=build_notes(
                    rank=rank,
                    account_value=subaccount_value,
                    row=row,
                    window=window,
                    account_role="subaccount",
                    parent_address=master_address,
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


def record_from_candidate(candidate: LeaderboardWalletCandidate) -> dict[str, Any]:
    return {
        "address": candidate.address,
        "label": candidate.label,
        "enabled": True,
        "eligible": False,
        "copy_enabled": False,
        "polling_tier": "pool",
        "notes": candidate.notes,
    }


def import_from_candidate(candidate: LeaderboardWalletCandidate) -> LeaderboardWalletImport:
    return LeaderboardWalletImport(
        rank=candidate.rank,
        address=candidate.address,
        display_name=candidate.display_name,
        account_value=candidate.account_value,
        window_pnl=candidate.window_pnl,
        window_roi=candidate.window_roi,
        account_role=candidate.account_role,
        parent_address=candidate.parent_address,
        subaccount_name=candidate.subaccount_name,
    )


async def load_fill_import_targets(
    session: AsyncSession,
    *,
    valid_wallets: list[LeaderboardWalletImport],
    inserted_addresses: list[str],
    include_unpolled_duplicates: bool,
    include_ranked_duplicates: bool,
    overlap_seconds: int,
) -> list[LeaderboardFillImportTarget]:
    inserted_set = set(inserted_addresses)
    valid_addresses = [wallet.address for wallet in valid_wallets]
    wallet_poll_times: dict[str, datetime | None] = {}

    if valid_addresses:
        result = await session.execute(
            select(WatchedWallet.address, WatchedWallet.last_polled_at).where(
                WatchedWallet.address.in_(valid_addresses)
            )
        )
        wallet_poll_times = {row.address: row.last_polled_at for row in result}

    targets: list[LeaderboardFillImportTarget] = []
    for wallet in valid_wallets:
        last_polled_at = wallet_poll_times.get(wallet.address)
        is_new = wallet.address in inserted_set
        is_unpolled = last_polled_at is None
        should_import = (
            is_new
            or include_ranked_duplicates
            or (include_unpolled_duplicates and is_unpolled)
        )
        if not should_import:
            continue

        start_time_ms = None
        if last_polled_at is not None and not is_new:
            start_time_ms = poll_overlap_start_ms(last_polled_at, overlap_seconds=overlap_seconds)
        targets.append(
            LeaderboardFillImportTarget(
                address=wallet.address,
                start_time_ms=start_time_ms,
            )
        )

    return targets


async def import_leaderboard_wallet_fills(
    session: AsyncSession,
    *,
    targets: list[LeaderboardFillImportTarget],
    days: int,
    max_pages: int,
) -> list[LeaderboardFillImport]:
    client = HyperliquidClient()
    results: list[LeaderboardFillImport] = []

    for target in targets:
        request = WalletFillImportRequest(
            days=days,
            max_pages=max_pages,
            target_fills=target_fills_for_pages(max_pages),
            start_time_ms=target.start_time_ms,
        )
        try:
            result = await import_wallet_fills(
                session=session,
                address=target.address,
                payload=request,
                client=client,
            )
        except FillImportStorageLimitError as exc:
            await session.rollback()
            logger.warning(
                "leaderboard fill import stopped wallet=%s error=%s",
                target.address,
                exc,
            )
            results.append(
                LeaderboardFillImport(
                    wallet_address=target.address,
                    error=str(exc) or exc.__class__.__name__,
                )
            )
            break
        except Exception as exc:
            await session.rollback()
            logger.warning("leaderboard fill import failed wallet=%s error=%s", target.address, exc)
            results.append(
                LeaderboardFillImport(
                    wallet_address=target.address,
                    error=str(exc) or exc.__class__.__name__,
                )
            )
            continue

        results.append(
            LeaderboardFillImport(
                wallet_address=result.wallet_address,
                fetched=result.fetched,
                inserted=result.inserted,
                duplicate=result.duplicate,
            )
        )

    return results


async def prune_new_non_perp_leaderboard_wallets(
    session: AsyncSession,
    *,
    inserted_addresses: list[str],
    fill_imports: list[LeaderboardFillImport],
    settings: Settings,
) -> list[str]:
    if (
        not settings.leaderboard_prune_non_perp_wallets_enabled
        or settings.fill_import_market_filter != "perp"
        or not fill_imports
        or not inserted_addresses
    ):
        return []

    fill_import_by_address = {
        item.wallet_address: item for item in fill_imports if item.error is None
    }
    addresses_to_prune = [
        address
        for address in inserted_addresses
        if (fill_import := fill_import_by_address.get(address)) is not None
        and fill_import.fetched == 0
        and fill_import.inserted == 0
        and fill_import.duplicate == 0
    ]
    if not addresses_to_prune:
        return []

    _, deleted_wallets = await delete_wallet_related_rows(session, addresses=addresses_to_prune)
    await session.commit()
    logger.info(
        "pruned %s new leaderboard wallets without imported perp fills",
        deleted_wallets,
    )
    if deleted_wallets <= 0:
        return []
    return addresses_to_prune[:deleted_wallets]


def poll_overlap_start_ms(last_polled_at: datetime, *, overlap_seconds: int) -> int:
    if last_polled_at.tzinfo is None:
        last_polled_at = last_polled_at.replace(tzinfo=UTC)
    start = last_polled_at - timedelta(seconds=overlap_seconds)
    return max(0, int(start.timestamp() * 1000))


def build_notes(
    *,
    rank: int,
    account_value: str | None,
    row: dict[str, Any],
    window: str,
    account_role: str = "master",
    parent_address: str | None = None,
    subaccount_name: str | None = None,
) -> str:
    selected = get_window(row, window)
    month = get_window(row, "month")
    all_time = get_window(row, "allTime")
    if account_role == "subaccount":
        parts = [
            f"Imported from Hyperliquid {window_label(window)} leaderboard rank #{rank} "
            "subaccount."
        ]
        if parent_address is not None:
            parts.append(f"Parent master wallet: {parent_address}.")
        if subaccount_name:
            parts.append(f"Subaccount name: {subaccount_name}.")
    else:
        parts = [f"Imported from Hyperliquid {window_label(window)} leaderboard rank #{rank}."]
    if account_value is not None:
        parts.append(f"Account value: {account_value}.")
    if selected:
        parts.append(
            f"{window_label(window)} PnL: {selected.get('pnl', '-')}, "
            f"ROI: {selected.get('roi', '-')}."
        )
    if month:
        parts.append(f"Month PnL: {month.get('pnl', '-')}, ROI: {month.get('roi', '-')}.")
    if all_time and window != "allTime":
        parts.append(
            f"All-time PnL: {all_time.get('pnl', '-')}, ROI: {all_time.get('roi', '-')}."
        )
    return " ".join(parts)


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


async def load_ignored_wallet_addresses(session: AsyncSession) -> set[str]:
    setting = await session.get(Setting, "leaderboard_ignored_wallet_addresses")
    if setting is None or not isinstance(setting.value, dict):
        return set()

    raw_addresses = setting.value.get("addresses")
    if not isinstance(raw_addresses, list):
        return set()

    ignored_addresses: set[str] = set()
    for raw_address in raw_addresses:
        if not isinstance(raw_address, str):
            continue
        try:
            ignored_addresses.add(normalize_wallet_address(raw_address))
        except ValueError:
            continue
    return ignored_addresses


def window_label(window: str) -> str:
    return {
        "day": "24H",
        "week": "7D",
        "month": "30D",
        "allTime": "All-time",
    }.get(window, window)
