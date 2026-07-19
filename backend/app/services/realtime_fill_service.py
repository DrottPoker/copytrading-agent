from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RealtimeExecutionInbox, WalletFill, WatchedWallet
from app.schemas.wallet import normalize_wallet_address
from app.services.fill_import_service import build_fill_record
from app.services.live_copy_state_service import LIVE_COPY_ORIGIN_REALTIME
from app.services.live_copy_work_service import enqueue_live_copy_work_for_wallet_fills


@dataclass(slots=True)
class StoredRealtimeFills:
    wallet_address: str
    fetched: int
    inserted: int
    duplicate: int
    is_snapshot: bool
    latest_fill_time_ms: int | None
    inserted_rows: list[dict[str, Any]]
    execution_rows: list[dict[str, Any]] | None = None
    inbox_id: str | None = None
    observed_at: datetime | None = None
    live_copy_work_enqueued: int = 0

    @property
    def rows_for_execution(self) -> list[dict[str, Any]]:
        if self.execution_rows is not None:
            return self.execution_rows
        return self.inserted_rows

    def execution_payload(self) -> dict[str, Any]:
        return {
            "walletAddress": self.wallet_address,
            "fetched": self.fetched,
            "inserted": self.inserted,
            "duplicate": self.duplicate,
            "isSnapshot": self.is_snapshot,
            "latestFillTimeMs": self.latest_fill_time_ms,
            "insertedRows": self.inserted_rows,
            "executionRows": self.rows_for_execution,
            "observedAt": self.observed_at.isoformat() if self.observed_at is not None else None,
        }

    @classmethod
    def from_execution_payload(
        cls,
        payload: dict[str, Any],
        *,
        inbox_id: str,
        fallback_observed_at: datetime | None = None,
    ) -> "StoredRealtimeFills":
        wallet_address = payload.get("walletAddress")
        inserted_rows = payload.get("insertedRows")
        execution_rows = payload.get("executionRows", inserted_rows)
        is_snapshot = payload.get("isSnapshot")
        latest_fill_time_ms = payload.get("latestFillTimeMs")
        observed_at = parse_execution_observed_at(
            payload.get("observedAt"),
            fallback=fallback_observed_at,
        )
        if not isinstance(wallet_address, str) or not wallet_address:
            raise ValueError("Realtime execution payload is missing walletAddress.")
        if not isinstance(inserted_rows, list) or not all(
            isinstance(row, dict) for row in inserted_rows
        ):
            raise ValueError("Realtime execution payload has invalid insertedRows.")
        if not isinstance(execution_rows, list) or not all(
            isinstance(row, dict) for row in execution_rows
        ):
            raise ValueError("Realtime execution payload has invalid executionRows.")
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
            execution_rows=execution_rows,
            inbox_id=inbox_id,
            observed_at=observed_at,
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
    execution_rows = [_record_to_execution_dict(record) for record in records]
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
        returned_rows = result.all()
        inserted_rows = [_row_to_dict(row) for row in returned_rows]

    live_copy_work_enqueued = 0
    if records and not is_snapshot:
        source_fill_ids = [str(record["external_fill_id"]) for record in records]
        received_result = await session.scalars(
            select(WalletFill).where(
                WalletFill.wallet_address == normalized_address,
                WalletFill.external_fill_id.in_(source_fill_ids),
            )
        )
        live_copy_work_enqueued = await enqueue_live_copy_work_for_wallet_fills(
            session,
            fills=received_result.all(),
            origin=LIVE_COPY_ORIGIN_REALTIME,
        )

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
        execution_rows=execution_rows,
        observed_at=observed_at,
        live_copy_work_enqueued=live_copy_work_enqueued,
    )
    if is_snapshot or execution_rows:
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


def _record_to_execution_dict(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "externalFillId": record["external_fill_id"],
        "coin": record["coin"],
        "side": record["side"],
        "price": str(record["price"]),
        "size": str(record["size"]),
        "notionalUsd": (
            str(record["notional_usd"]) if record["notional_usd"] is not None else None
        ),
        "feeUsd": str(record["fee_usd"]) if record["fee_usd"] is not None else None,
        "pnlUsd": str(record["pnl_usd"]) if record["pnl_usd"] is not None else None,
        "timestampMs": record["timestamp_ms"],
        "sourceTimestampMs": record["source_timestamp_ms"],
        "ingestLatencyMs": record.get("ingest_latency_ms"),
        "rawJson": record["raw_json"],
    }


def payload_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Realtime execution payload has invalid {key}.")
    return value


def parse_execution_observed_at(
    value: object,
    *,
    fallback: datetime | None,
) -> datetime | None:
    if value is None:
        return ensure_utc(fallback) if fallback is not None else None
    if not isinstance(value, str) or not value:
        raise ValueError("Realtime execution payload has invalid observedAt.")
    try:
        return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise ValueError("Realtime execution payload has invalid observedAt.") from exc


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
