from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RealtimeExecutionInbox, WalletFill, WatchedWallet
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
    inbox_id: str | None = None

    def execution_payload(self) -> dict[str, Any]:
        return {
            "walletAddress": self.wallet_address,
            "fetched": self.fetched,
            "inserted": self.inserted,
            "duplicate": self.duplicate,
            "isSnapshot": self.is_snapshot,
            "latestFillTimeMs": self.latest_fill_time_ms,
            "insertedRows": self.inserted_rows,
        }

    @classmethod
    def from_execution_payload(
        cls,
        payload: dict[str, Any],
        *,
        inbox_id: str,
    ) -> "StoredRealtimeFills":
        wallet_address = payload.get("walletAddress")
        inserted_rows = payload.get("insertedRows")
        is_snapshot = payload.get("isSnapshot")
        latest_fill_time_ms = payload.get("latestFillTimeMs")
        if not isinstance(wallet_address, str) or not wallet_address:
            raise ValueError("Realtime execution payload is missing walletAddress.")
        if not isinstance(inserted_rows, list) or not all(
            isinstance(row, dict) for row in inserted_rows
        ):
            raise ValueError("Realtime execution payload has invalid insertedRows.")
        if not isinstance(is_snapshot, bool):
            raise ValueError("Realtime execution payload has invalid isSnapshot.")
        if latest_fill_time_ms is not None and not isinstance(latest_fill_time_ms, int):
            raise ValueError("Realtime execution payload has invalid latestFillTimeMs.")
        return cls(
            wallet_address=wallet_address,
            fetched=payload_int(payload, "fetched"),
            inserted=payload_int(payload, "inserted"),
            duplicate=payload_int(payload, "duplicate"),
            is_snapshot=is_snapshot,
            latest_fill_time_ms=latest_fill_time_ms,
            inserted_rows=inserted_rows,
            inbox_id=inbox_id,
        )


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
                WalletFill.notional_usd,
                WalletFill.fee_usd,
                WalletFill.pnl_usd,
                WalletFill.timestamp_ms,
                WalletFill.source_timestamp_ms,
                WalletFill.ingest_latency_ms,
                WalletFill.raw_json,
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

    stored = StoredRealtimeFills(
        wallet_address=normalized_address,
        fetched=len(records),
        inserted=len(inserted_rows),
        duplicate=len(records) - len(inserted_rows),
        is_snapshot=is_snapshot,
        latest_fill_time_ms=latest_fill_time_ms,
        inserted_rows=inserted_rows,
    )
    if is_snapshot or inserted_rows:
        inbox = RealtimeExecutionInbox(
            wallet_address=normalized_address,
            payload=stored.execution_payload(),
        )
        session.add(inbox)
        await session.flush()
        stored.inbox_id = str(inbox.id)

    await session.commit()
    return stored


def _row_to_dict(row: Row[Any]) -> dict[str, Any]:
    mapping = row._mapping
    return {
        "id": str(mapping["id"]),
        "externalFillId": mapping["external_fill_id"],
        "coin": mapping["coin"],
        "side": mapping["side"],
        "price": str(mapping["price"]),
        "size": str(mapping["size"]),
        "notionalUsd": (
            str(mapping["notional_usd"]) if mapping["notional_usd"] is not None else None
        ),
        "feeUsd": str(mapping["fee_usd"]) if mapping["fee_usd"] is not None else None,
        "pnlUsd": str(mapping["pnl_usd"]) if mapping["pnl_usd"] is not None else None,
        "timestampMs": mapping["timestamp_ms"],
        "sourceTimestampMs": mapping["source_timestamp_ms"],
        "ingestLatencyMs": mapping["ingest_latency_ms"],
        "rawJson": mapping["raw_json"],
    }


def payload_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Realtime execution payload has invalid {key}.")
    return value
