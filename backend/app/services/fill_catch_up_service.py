"""Fetch the fills a userFills snapshot leaves out after a reconnect.

A snapshot holds only a wallet's newest fills. After a worker restart or a
dropped connection, fills newer than the last complete poll but older than the
snapshot would stay missing, so an exit made during the downtime could be
missed. `WatchedWallet.last_polled_at` marks how far the stored history is
complete, and only a proven complete read moves it.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import WatchedWallet
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.fill import WalletFillImportRequest
from app.services.fill_import_service import import_address_fills, target_fills_for_pages
from app.services.pool_fill_import_service import poll_overlap_start_ms

FillCatchUpStatus = Literal["not_watched", "never_polled", "covered", "imported"]


@dataclass(frozen=True)
class FillCatchUpResult:
    status: FillCatchUpStatus
    inserted: int = 0
    history_complete: bool = True
    start_time_ms: int | None = None


async def catch_up_fills_after_snapshot(
    session: AsyncSession,
    *,
    wallet_address: str,
    oldest_snapshot_fill_ms: int | None,
    observed_at: datetime,
    settings: Settings,
    client: HyperliquidClient,
) -> FillCatchUpResult:
    wallet = await session.scalar(
        select(WatchedWallet).where(WatchedWallet.address == wallet_address)
    )
    if wallet is None:
        return FillCatchUpResult(status="not_watched")
    if wallet.last_polled_at is None:
        # The first pool import backfills the wallet's whole history window.
        return FillCatchUpResult(status="never_polled")

    complete_until = wallet.last_polled_at
    if complete_until.tzinfo is None:
        complete_until = complete_until.replace(tzinfo=UTC)
    complete_until_ms = int(complete_until.timestamp() * 1000)
    if oldest_snapshot_fill_ms is not None and oldest_snapshot_fill_ms < complete_until_ms:
        # The snapshot is the newest run of fills without holes and reaches back
        # into the complete history, so every fill until the snapshot is stored.
        if observed_at > complete_until:
            wallet.last_polled_at = observed_at
        await session.commit()
        return FillCatchUpResult(status="covered")

    start_time_ms = poll_overlap_start_ms(
        complete_until,
        overlap_seconds=settings.pool_fill_import_overlap_seconds,
    )
    max_pages = settings.pool_fill_import_max_pages
    result = await import_address_fills(
        session,
        address=wallet.address,
        payload=WalletFillImportRequest(
            start_time_ms=start_time_ms,
            days=settings.pool_fill_import_days,
            max_pages=max_pages,
            target_fills=target_fills_for_pages(max_pages),
        ),
        client=client,
        wallet=wallet,
    )
    return FillCatchUpResult(
        status="imported",
        inserted=result.inserted,
        history_complete=result.history_complete,
        start_time_ms=start_time_ms,
    )
