import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WatchedWallet
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.fill import WalletFillImportRequest
from app.schemas.pool_import import PoolFillImportItem, PoolFillImportResponse
from app.services.fill_import_service import (
    FillImportStorageLimitError,
    import_wallet_fills,
    target_fills_for_pages,
)
from app.services.job_lock_service import job_lock
from app.services.operation_status_service import (
    mark_operation_failed,
    mark_operation_progress,
    mark_operation_started,
    mark_operation_succeeded,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PoolFillImportTarget:
    address: str
    last_polled_at: datetime | None


async def import_due_pool_wallet_fills(
    session: AsyncSession,
    *,
    limit: int,
    days: int,
    max_pages: int,
    min_wallet_interval_seconds: int,
    overlap_seconds: int,
    max_batches: int = 1,
    force: bool = False,
    client: HyperliquidClient | None = None,
) -> PoolFillImportResponse:
    async with job_lock(session, key="pool_fill_import", ttl_seconds=12 * 60 * 60):
        return await _import_due_pool_wallet_fills_locked(
            session=session,
            limit=limit,
            days=days,
            max_pages=max_pages,
            min_wallet_interval_seconds=min_wallet_interval_seconds,
            overlap_seconds=overlap_seconds,
            max_batches=max_batches,
            force=force,
            client=client,
        )


async def _import_due_pool_wallet_fills_locked(
    *,
    session: AsyncSession,
    limit: int,
    days: int,
    max_pages: int,
    min_wallet_interval_seconds: int,
    overlap_seconds: int,
    max_batches: int = 1,
    force: bool = False,
    client: HyperliquidClient | None = None,
) -> PoolFillImportResponse:
    payload = {
        "limit": limit,
        "days": days,
        "maxPages": max_pages,
        "minWalletIntervalSeconds": min_wallet_interval_seconds,
        "overlapSeconds": overlap_seconds,
        "maxBatches": max_batches,
        "force": force,
    }
    await mark_operation_started(session, key="pool_fill_import", payload=payload)
    try:
        result = await _import_due_pool_wallet_fill_batches(
            session=session,
            limit=limit,
            days=days,
            max_pages=max_pages,
            min_wallet_interval_seconds=min_wallet_interval_seconds,
            overlap_seconds=overlap_seconds,
            max_batches=max_batches,
            force=force,
            base_payload=payload,
            client=client,
        )
    except Exception as exc:
        await session.rollback()
        await mark_operation_failed(
            session,
            key="pool_fill_import",
            error=str(exc) or exc.__class__.__name__,
            payload=payload,
        )
        raise

    await mark_operation_succeeded(
        session,
        key="pool_fill_import",
        payload={
            **payload,
            "scanned": result.scanned,
            "importedWallets": result.imported_wallets,
            "fetched": result.fetched,
            "inserted": result.inserted,
            "duplicate": result.duplicate,
            "failed": result.failed,
        },
    )
    return result


async def _import_due_pool_wallet_fills(
    session: AsyncSession,
    *,
    limit: int,
    days: int,
    max_pages: int,
    min_wallet_interval_seconds: int,
    overlap_seconds: int,
    force: bool,
    exclude_addresses: set[str] | None = None,
    client: HyperliquidClient | None = None,
) -> PoolFillImportResponse:
    if client is None:
        async with HyperliquidClient() as hyperliquid_client:
            return await _import_due_pool_wallet_fills(
                session=session,
                limit=limit,
                days=days,
                max_pages=max_pages,
                min_wallet_interval_seconds=min_wallet_interval_seconds,
                overlap_seconds=overlap_seconds,
                force=force,
                exclude_addresses=exclude_addresses,
                client=hyperliquid_client,
            )

    targets = await load_due_pool_fill_targets(
        session,
        limit=limit,
        min_wallet_interval_seconds=min_wallet_interval_seconds,
        force=force,
        exclude_addresses=exclude_addresses or set(),
    )
    items: list[PoolFillImportItem] = []

    for target in targets:
        request = WalletFillImportRequest(
            days=days,
            max_pages=max_pages,
            target_fills=target_fills_for_pages(max_pages),
            start_time_ms=(
                poll_overlap_start_ms(target.last_polled_at, overlap_seconds=overlap_seconds)
                if target.last_polled_at is not None
                else None
            ),
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
            logger.warning("pool fill import stopped wallet=%s error=%s", target.address, exc)
            items.append(
                PoolFillImportItem(
                    wallet_address=target.address,
                    error=str(exc) or exc.__class__.__name__,
                )
            )
            break
        except Exception as exc:
            await session.rollback()
            logger.warning("pool fill import failed wallet=%s error=%s", target.address, exc)
            items.append(
                PoolFillImportItem(
                    wallet_address=target.address,
                    error=str(exc) or exc.__class__.__name__,
                )
            )
            continue

        items.append(
            PoolFillImportItem(
                wallet_address=result.wallet_address,
                fetched=result.fetched,
                inserted=result.inserted,
                duplicate=result.duplicate,
            )
        )

    failed = sum(1 for item in items if item.error is not None)
    return PoolFillImportResponse(
        scanned=len(targets),
        imported_wallets=len(items),
        fetched=sum(item.fetched for item in items),
        inserted=sum(item.inserted for item in items),
        duplicate=sum(item.duplicate for item in items),
        failed=failed,
        limit=limit,
        items=items,
    )


async def _import_due_pool_wallet_fill_batches(
    *,
    session: AsyncSession,
    limit: int,
    days: int,
    max_pages: int,
    min_wallet_interval_seconds: int,
    overlap_seconds: int,
    max_batches: int,
    force: bool,
    base_payload: dict[str, Any],
    client: HyperliquidClient | None,
) -> PoolFillImportResponse:
    totals = PoolFillImportResponse(
        scanned=0,
        imported_wallets=0,
        fetched=0,
        inserted=0,
        duplicate=0,
        failed=0,
        limit=limit,
        items=[],
    )

    if client is None:
        async with HyperliquidClient() as hyperliquid_client:
            return await _import_due_pool_wallet_fill_batches(
                session=session,
                limit=limit,
                days=days,
                max_pages=max_pages,
                min_wallet_interval_seconds=min_wallet_interval_seconds,
                overlap_seconds=overlap_seconds,
                max_batches=max_batches,
                force=force,
                base_payload=base_payload,
                client=hyperliquid_client,
            )

    processed_addresses: set[str] = set()
    for batch_number in range(1, max_batches + 1):
        batch = await _import_due_pool_wallet_fills(
            session=session,
            limit=limit,
            days=days,
            max_pages=max_pages,
            min_wallet_interval_seconds=min_wallet_interval_seconds,
            overlap_seconds=overlap_seconds,
            force=force,
            exclude_addresses=processed_addresses,
            client=client,
        )
        totals.scanned += batch.scanned
        totals.imported_wallets += batch.imported_wallets
        totals.fetched += batch.fetched
        totals.inserted += batch.inserted
        totals.duplicate += batch.duplicate
        totals.failed += batch.failed
        totals.items.extend(batch.items)
        processed_addresses.update(item.wallet_address for item in batch.items)

        await mark_operation_progress(
            session,
            key="pool_fill_import",
            payload={
                **base_payload,
                "batch": batch_number,
                "scanned": totals.scanned,
                "importedWallets": totals.imported_wallets,
                "fetched": totals.fetched,
                "inserted": totals.inserted,
                "duplicate": totals.duplicate,
                "failed": totals.failed,
            },
        )

        successful_wallets = batch.imported_wallets - batch.failed
        if batch.scanned == 0:
            break
        if batch.failed > 0 and successful_wallets <= 0:
            break
        if batch.scanned < limit:
            break

    return totals


async def load_due_pool_fill_targets(
    session: AsyncSession,
    *,
    limit: int,
    min_wallet_interval_seconds: int,
    force: bool = False,
    exclude_addresses: set[str] | None = None,
) -> list[PoolFillImportTarget]:
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=min_wallet_interval_seconds)
    unpolled_priority = case((WatchedWallet.last_polled_at.is_(None), 0), else_=1)
    due_condition = (
        WatchedWallet.enabled.is_(True)
        if force
        else (
            WatchedWallet.enabled.is_(True)
            & (
                WatchedWallet.last_polled_at.is_(None)
                | (WatchedWallet.last_polled_at <= stale_before)
            )
        )
    )
    statement = (
        select(WatchedWallet.address, WatchedWallet.last_polled_at)
        .where(due_condition)
        .order_by(unpolled_priority, WatchedWallet.last_polled_at.asc().nulls_first())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if exclude_addresses:
        statement = statement.where(WatchedWallet.address.not_in(exclude_addresses))
    result = await session.execute(statement)
    return [
        PoolFillImportTarget(address=row.address, last_polled_at=row.last_polled_at)
        for row in result
    ]


def poll_overlap_start_ms(last_polled_at: datetime, *, overlap_seconds: int) -> int:
    if last_polled_at.tzinfo is None:
        last_polled_at = last_polled_at.replace(tzinfo=UTC)
    start = last_polled_at - timedelta(seconds=overlap_seconds)
    return max(0, int(start.timestamp() * 1000))
