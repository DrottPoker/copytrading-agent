from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import WalletFill, WatchedWallet
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.fill import WalletFillImportRequest, WalletFillImportResponse
from app.schemas.wallet import normalize_wallet_address
from app.services.db_storage_service import check_database_storage_budget
from app.services.perp_filter_service import is_perp_fill
from app.services.wallet_service import get_wallet

HYPERLIQUID_FILL_PAGE_SIZE = 2000
INSERT_BATCH_SIZE = 1000
MAX_IMPORT_TARGET_FILLS = 10000


class FillImportStorageLimitError(RuntimeError):
    pass


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def map_hyperliquid_side(value: Any) -> str | None:
    side = str(value or "").upper()
    if side == "B":
        return "buy"
    if side == "A":
        return "sell"
    if side in {"BUY", "SELL"}:
        return side.lower()
    return None


def external_fill_id(fill: dict[str, Any]) -> str:
    if fill.get("tid") is not None:
        return str(fill["tid"])
    parts = [
        str(fill.get("hash") or ""),
        str(fill.get("oid") or ""),
        str(fill.get("time") or ""),
        str(fill.get("coin") or ""),
        str(fill.get("side") or ""),
        str(fill.get("px") or ""),
        str(fill.get("sz") or ""),
    ]
    return ":".join(parts)


def fill_timestamp_ms(fill: dict[str, Any]) -> int:
    timestamp = fill.get("time")
    if timestamp is None:
        return 0
    return int(timestamp)


def build_fill_record(
    wallet_address: str,
    fill: dict[str, Any],
    *,
    is_snapshot: bool = True,
    received_at: datetime | None = None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    resolved_settings = settings or get_settings()
    if resolved_settings.fill_import_market_filter == "perp" and not is_perp_fill(fill):
        return None

    price = decimal_or_none(fill.get("px")) or Decimal("0")
    size = decimal_or_none(fill.get("sz")) or Decimal("0")
    fee = decimal_or_none(fill.get("fee"))
    closed_pnl = decimal_or_none(fill.get("closedPnl"))
    timestamp_ms = fill_timestamp_ms(fill)
    side = map_hyperliquid_side(fill.get("side"))
    coin = str(fill.get("coin") or "")
    if not coin or side is None or timestamp_ms <= 0:
        return None

    record: dict[str, Any] = {
        "wallet_address": wallet_address,
        "external_fill_id": external_fill_id(fill),
        "coin": coin,
        "side": side,
        "price": price,
        "size": size,
        "notional_usd": price * size,
        "fee_usd": fee,
        "pnl_usd": closed_pnl,
        "timestamp_ms": timestamp_ms,
        "source_timestamp_ms": timestamp_ms,
        "is_snapshot": is_snapshot,
        "raw_json": compact_fill_payload(
            fill,
            keys_to_keep=resolved_settings.fill_import_raw_json_fields,
        ),
    }
    if received_at is not None:
        record["received_at"] = received_at
        record["ingest_latency_ms"] = max(0, int(received_at.timestamp() * 1000) - timestamp_ms)
    return record


async def import_wallet_fills(
    session: AsyncSession,
    *,
    address: str,
    payload: WalletFillImportRequest,
    client: HyperliquidClient | None = None,
) -> WalletFillImportResponse:
    wallet = await get_wallet(session, address)
    return await import_address_fills(
        session=session,
        address=wallet.address,
        payload=payload,
        client=client,
        wallet=wallet,
    )


async def import_address_fills(
    session: AsyncSession,
    *,
    address: str,
    payload: WalletFillImportRequest,
    client: HyperliquidClient | None = None,
    wallet: WatchedWallet | None = None,
) -> WalletFillImportResponse:
    normalized_address = normalize_wallet_address(address)
    settings = get_settings()
    if settings.fill_import_storage_guard_enabled:
        budget = await check_database_storage_budget(
            session,
            min_free_mb=settings.fill_import_min_free_database_mb,
        )
        if not budget.has_budget:
            free_mb = (budget.free_bytes or 0) / 1024 / 1024
            limit_mb = (budget.limit_bytes or 0) / 1024 / 1024
            raise FillImportStorageLimitError(
                "Database storage is too low for fill import "
                f"({free_mb:.1f} MB free of {limit_mb:.0f} MB limit; "
                f"requires {settings.fill_import_min_free_database_mb} MB free)."
            )

    now = datetime.now(UTC)
    end_time_ms = payload.end_time_ms or int(now.timestamp() * 1000)
    start_time_ms = payload.start_time_ms or int(
        (now - timedelta(days=payload.days)).timestamp() * 1000
    )

    hyperliquid_client = client or HyperliquidClient()
    records: list[dict[str, Any]] = []
    raw_fetched_count = 0
    pages_fetched = 0
    next_start_time_ms = start_time_ms

    for _ in range(payload.max_pages):
        page = await hyperliquid_client.user_fills_by_time(
            user=normalized_address,
            start_time_ms=next_start_time_ms,
            end_time_ms=end_time_ms,
            aggregate_by_time=payload.aggregate_by_time,
        )
        if not page:
            break

        page_observed_at = datetime.now(UTC)
        pages_fetched += 1
        raw_fetched_count += len(page)
        for fill in page:
            if len(records) >= payload.target_fills:
                break
            record = build_fill_record(
                normalized_address,
                fill,
                settings=settings,
                received_at=page_observed_at,
            )
            if record is not None:
                records.append(record)

        max_timestamp_ms = max(fill_timestamp_ms(fill) for fill in page)
        if (
            len(records) >= payload.target_fills
            or len(page) < HYPERLIQUID_FILL_PAGE_SIZE
            or max_timestamp_ms >= end_time_ms
        ):
            break
        next_start_time_ms = max_timestamp_ms + 1

    inserted_count = 0
    latest_fill_time_ms = max((record["timestamp_ms"] for record in records), default=None)

    if records:
        for start in range(0, len(records), INSERT_BATCH_SIZE):
            batch = records[start : start + INSERT_BATCH_SIZE]
            stmt = (
                insert(WalletFill)
                .values(batch)
                .on_conflict_do_nothing(index_elements=["wallet_address", "external_fill_id"])
            )
            result = await session.execute(stmt)
            inserted_count += max(0, result.rowcount or 0)

    if wallet is not None:
        wallet.last_polled_at = now
        if latest_fill_time_ms is not None:
            wallet.last_seen_fill_at = datetime.fromtimestamp(latest_fill_time_ms / 1000, tz=UTC)
    await session.commit()

    return WalletFillImportResponse(
        wallet_address=normalized_address,
        fetched=len(records),
        raw_fetched=raw_fetched_count,
        pages_fetched=pages_fetched,
        inserted=inserted_count,
        duplicate=len(records) - inserted_count,
        target_fills=payload.target_fills,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        latest_fill_time_ms=latest_fill_time_ms,
    )


async def list_wallet_fills(
    session: AsyncSession,
    *,
    address: str,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[WalletFill], int]:
    normalized_address = normalize_wallet_address(address)
    filters = [WalletFill.wallet_address == normalized_address]
    base_query: Select[tuple[WalletFill]] = select(WalletFill)
    count_query = select(func.count()).select_from(WalletFill)
    for condition in filters:
        base_query = base_query.where(condition)
        count_query = count_query.where(condition)

    result = await session.execute(
        base_query.order_by(WalletFill.timestamp_ms.desc()).limit(limit).offset(offset)
    )
    total = await session.scalar(count_query)
    return list(result.scalars().all()), int(total or 0)


async def ensure_wallet_exists(session: AsyncSession, address: str) -> WatchedWallet:
    return await get_wallet(session, address)


def compact_fill_payload(
    fill: dict[str, Any],
    *,
    keys_to_keep: list[str] | None = None,
) -> dict[str, Any]:
    keys = keys_to_keep or get_settings().fill_import_raw_json_fields
    return {key: fill[key] for key in keys if key in fill and fill[key] is not None}


def target_fills_for_pages(max_pages: int) -> int:
    return min(max_pages * HYPERLIQUID_FILL_PAGE_SIZE, MAX_IMPORT_TARGET_FILLS)
