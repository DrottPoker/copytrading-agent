from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WalletFill, WatchedWallet
from app.schemas.wallet import normalize_wallet_address
from app.services.fill_import_service import build_fill_record


@dataclass(slots=True)
class StoredRealtimeFills:
    wallet_address: str
    fetched: int
    inserted: int
    duplicate: int
    is_snapshot: bool
    latest_fill_time_ms: int | None
    inserted_rows: list[dict[str, Any]]


async def store_realtime_fills(
    session: AsyncSession,
    *,
    wallet_address: str,
    fills: list[dict[str, Any]],
    is_snapshot: bool,
    received_at: datetime | None = None,
) -> StoredRealtimeFills:
    normalized_address = normalize_wallet_address(wallet_address)
    observed_at = received_at or datetime.now(UTC)
    records = [
        record
        for fill in fills
        if (
            record := build_fill_record(
                normalized_address,
                fill,
                is_snapshot=is_snapshot,
                received_at=observed_at,
            )
        )
        is not None
    ]

    inserted_rows: list[dict[str, Any]] = []
    latest_fill_time_ms = max((record["timestamp_ms"] for record in records), default=None)

    if records:
        stmt = (
            insert(WalletFill)
            .values(records)
            .on_conflict_do_nothing(index_elements=["wallet_address", "external_fill_id"])
            .returning(
                WalletFill.id,
                WalletFill.external_fill_id,
                WalletFill.coin,
                WalletFill.side,
                WalletFill.price,
                WalletFill.size,
                WalletFill.timestamp_ms,
                WalletFill.ingest_latency_ms,
            )
        )
        result = await session.execute(stmt)
        inserted_rows = [_row_to_dict(row) for row in result.all()]

    wallet = await session.scalar(
        select(WatchedWallet).where(WatchedWallet.address == normalized_address)
    )
    if wallet is not None:
        wallet.last_polled_at = observed_at
        if latest_fill_time_ms is not None:
            wallet.last_seen_fill_at = datetime.fromtimestamp(latest_fill_time_ms / 1000, tz=UTC)

    await session.commit()

    return StoredRealtimeFills(
        wallet_address=normalized_address,
        fetched=len(records),
        inserted=len(inserted_rows),
        duplicate=len(records) - len(inserted_rows),
        is_snapshot=is_snapshot,
        latest_fill_time_ms=latest_fill_time_ms,
        inserted_rows=inserted_rows,
    )


def _row_to_dict(row: Row[Any]) -> dict[str, Any]:
    mapping = row._mapping
    return {
        "id": str(mapping["id"]),
        "externalFillId": mapping["external_fill_id"],
        "coin": mapping["coin"],
        "side": mapping["side"],
        "price": str(mapping["price"]),
        "size": str(mapping["size"]),
        "timestampMs": mapping["timestamp_ms"],
        "ingestLatencyMs": mapping["ingest_latency_ms"],
    }
