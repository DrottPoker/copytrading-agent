import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import WalletFill, WatchedWallet
from app.db.session import affected_row_count
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.fill import WalletFillImportRequest, WalletFillImportResponse
from app.schemas.wallet import normalize_wallet_address
from app.services.db_storage_service import check_database_storage_budget
from app.services.perp_filter_service import is_perp_fill
from app.services.wallet_service import get_wallet

logger = logging.getLogger(__name__)

HYPERLIQUID_FILL_PAGE_SIZE = 2000
INSERT_BATCH_SIZE = 1000
MAX_IMPORT_TARGET_FILLS = 10000
# Windows read while walking back from the newest fill aim below a full page,
# so most of them take one request.
FILL_WINDOW_TARGET_ROWS = 1600
MIN_FILL_WINDOW_MS = 60_000


class FillImportStorageLimitError(RuntimeError):
    pass


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation, ValueError:
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

    reader = FillPageReader(
        client=client or HyperliquidClient(),
        user=normalized_address,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        max_pages=payload.max_pages,
        aggregate_by_time=payload.aggregate_by_time,
        settings=settings,
    )
    window = await read_fill_window(
        reader,
        target_fills=payload.target_fills,
        # The newest page can only stand in for "until now".
        newest_page_available=payload.end_time_ms is None,
    )
    records = reader.records_between(window.history_start_ms, window.history_end_ms)
    if not window.complete:
        logger.info(
            "fill import stored part of the window wallet=%s history_start_ms=%s "
            "history_end_ms=%s requested_start_ms=%s pages=%s",
            normalized_address,
            window.history_start_ms,
            window.history_end_ms,
            start_time_ms,
            reader.pages,
        )

    latest_fill_time_ms = max((record["timestamp_ms"] for record in records), default=None)
    inserted_count = await insert_fill_records(session, records)

    if wallet is not None:
        # The next poll continues where the stored history ends.
        wallet.last_polled_at = (
            now
            if window.history_end_ms >= end_time_ms
            else datetime.fromtimestamp(window.history_end_ms / 1000, tz=UTC)
        )
        if latest_fill_time_ms is not None:
            wallet.last_seen_fill_at = datetime.fromtimestamp(latest_fill_time_ms / 1000, tz=UTC)
    await session.commit()

    return WalletFillImportResponse(
        wallet_address=normalized_address,
        fetched=len(records),
        raw_fetched=reader.raw_rows,
        pages_fetched=reader.pages,
        inserted=inserted_count,
        duplicate=len(records) - inserted_count,
        target_fills=payload.target_fills,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        latest_fill_time_ms=latest_fill_time_ms,
        history_complete=window.complete,
        history_start_ms=window.history_start_ms,
        history_end_ms=window.history_end_ms,
    )


async def insert_fill_records(session: AsyncSession, records: list[dict[str, Any]]) -> int:
    inserted_count = 0
    for start in range(0, len(records), INSERT_BATCH_SIZE):
        batch = records[start : start + INSERT_BATCH_SIZE]
        stmt = (
            insert(WalletFill)
            .values(batch)
            .on_conflict_do_nothing(index_elements=["wallet_address", "external_fill_id"])
        )
        result = await session.execute(stmt)
        inserted_count += affected_row_count(result)
    return inserted_count


@dataclass
class FillPageReader:
    """Reads one wallet's fill pages within a page budget, deduplicated by fill id."""

    client: HyperliquidClient
    user: str
    start_time_ms: int
    end_time_ms: int
    max_pages: int
    aggregate_by_time: bool
    settings: Settings
    pages: int = 0
    raw_rows: int = 0
    records: dict[str, dict[str, Any]] = field(default_factory=dict)

    def has_budget(self) -> bool:
        return self.pages < self.max_pages

    async def read_forward(
        self,
        start_ms: int,
        end_ms: int,
        *,
        page_limit: int | None = None,
    ) -> ForwardRead:
        """Read every fill in [start_ms, end_ms] oldest first, within the page budget."""

        next_start_ms = start_ms
        rows = 0
        first_ms: int | None = None
        last_ms: int | None = None
        pages_left = page_limit if page_limit is not None else self.max_pages
        while self.has_budget() and pages_left > 0:
            pages_left -= 1
            page = await self._fetch(
                self.client.user_fills_by_time(
                    user=self.user,
                    start_time_ms=next_start_ms,
                    end_time_ms=end_ms,
                    aggregate_by_time=self.aggregate_by_time,
                )
            )
            rows += len(page)
            if page:
                page_times = [fill_timestamp_ms(fill) for fill in page]
                first_ms = min(page_times) if first_ms is None else first_ms
                last_ms = max(page_times)
            if len(page) < HYPERLIQUID_FILL_PAGE_SIZE or last_ms is None or last_ms >= end_ms:
                return ForwardRead(complete=True, rows=rows, first_ms=first_ms, last_ms=last_ms)
            # A full page can end inside a burst of fills sharing one timestamp, so the
            # next page starts at that timestamp; fills already read are skipped.
            next_start_ms = last_ms if last_ms > next_start_ms else last_ms + 1
        return ForwardRead(complete=False, rows=rows, first_ms=first_ms, last_ms=last_ms)

    async def read_newest(self) -> list[dict[str, Any]]:
        """The wallet's newest page of fills, newest first."""

        return await self._fetch(
            self.client.user_fills(user=self.user, aggregate_by_time=self.aggregate_by_time)
        )

    def records_between(self, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        return sorted(
            (
                record
                for record in self.records.values()
                if start_ms <= record["timestamp_ms"] <= end_ms
            ),
            key=lambda record: (record["timestamp_ms"], record["external_fill_id"]),
        )

    def count_between(self, start_ms: int, end_ms: int) -> int:
        return sum(
            1 for record in self.records.values() if start_ms <= record["timestamp_ms"] <= end_ms
        )

    async def _fetch(self, request: Any) -> list[dict[str, Any]]:
        self.pages += 1
        page: list[dict[str, Any]] = await request
        observed_at = datetime.now(UTC)
        self.raw_rows += len(page)
        for fill in page:
            timestamp_ms = fill_timestamp_ms(fill)
            if timestamp_ms < self.start_time_ms or timestamp_ms > self.end_time_ms:
                continue
            record = build_fill_record(
                self.user,
                fill,
                settings=self.settings,
                received_at=observed_at,
            )
            if record is not None:
                self.records.setdefault(record["external_fill_id"], record)
        return page


@dataclass(frozen=True)
class ForwardRead:
    complete: bool
    rows: int
    first_ms: int | None
    last_ms: int | None


@dataclass(frozen=True)
class FillWindow:
    """The stretch of time whose fills are all stored, within the requested window."""

    complete: bool
    history_start_ms: int
    history_end_ms: int


async def read_fill_window(
    reader: FillPageReader,
    *,
    target_fills: int,
    newest_page_available: bool,
) -> FillWindow:
    """Read a wallet's fills so a page or fill limit cuts the oldest history, never the newest.

    Hyperliquid returns the oldest fills of a time range first. The first page
    covers most wallets on its own. With more fills than one page, reading goes
    back from the newest fill in windows sized by the fill rate, and only a
    contiguous run of the newest fills is kept, so no gap is left between them.
    """

    start_ms = reader.start_time_ms
    end_ms = reader.end_time_ms
    complete_window = FillWindow(complete=True, history_start_ms=start_ms, history_end_ms=end_ms)
    first = await reader.read_forward(start_ms, end_ms, page_limit=1)
    if first.complete or first.last_ms is None:
        return trim_to_target(reader, complete_window, target_fills)
    first_reached_ms = first.last_ms

    if not newest_page_available or not reader.has_budget():
        # Without the newest page as an anchor, reading continues oldest first and
        # reports where it stopped, so the next poll can continue from there.
        rest = await reader.read_forward(first_reached_ms, end_ms) if reader.has_budget() else first
        if rest.complete:
            return trim_to_target(reader, complete_window, target_fills)
        return trim_to_target(
            reader,
            FillWindow(
                complete=False,
                history_start_ms=start_ms,
                history_end_ms=rest.last_ms or first_reached_ms,
            ),
            target_fills,
        )

    newest = await reader.read_newest()
    if len(newest) < HYPERLIQUID_FILL_PAGE_SIZE:
        # The wallet's whole history fits in one page, so nothing is missing.
        return trim_to_target(reader, complete_window, target_fills)
    newest_times = [fill_timestamp_ms(fill) for fill in newest]
    # Every fill from here to now is read.
    covered_from_ms = min(max(min(newest_times), start_ms), end_ms)
    rows_per_ms = len(newest) / max(max(newest_times) - min(newest_times), 1)

    while (
        covered_from_ms > first_reached_ms
        and reader.has_budget()
        and reader.count_between(covered_from_ms, end_ms) < target_fills
    ):
        span_ms = min(
            max(int(FILL_WINDOW_TARGET_ROWS / max(rows_per_ms, 1e-9)), MIN_FILL_WINDOW_MS),
            covered_from_ms - first_reached_ms,
        )
        window_start_ms = covered_from_ms - span_ms
        window = await reader.read_forward(window_start_ms, covered_from_ms)
        if not window.complete:
            # The rows of this window would sit behind a gap, so they are left out.
            break
        rows_per_ms = max(window.rows, 1) / span_ms
        covered_from_ms = window_start_ms

    if covered_from_ms <= first_reached_ms:
        return trim_to_target(reader, complete_window, target_fills)
    # The oldest page and older windows would leave a gap, so only the newest run is kept.
    return trim_to_target(
        reader,
        FillWindow(complete=False, history_start_ms=covered_from_ms, history_end_ms=end_ms),
        target_fills,
    )


def trim_to_target(reader: FillPageReader, window: FillWindow, target_fills: int) -> FillWindow:
    """Keep at most the newest target_fills fills, cutting the oldest history."""

    records = reader.records_between(window.history_start_ms, window.history_end_ms)
    if len(records) <= target_fills:
        return window
    return FillWindow(
        complete=False,
        history_start_ms=records[-target_fills]["timestamp_ms"],
        history_end_ms=window.history_end_ms,
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
    base_query = select(WalletFill)
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
