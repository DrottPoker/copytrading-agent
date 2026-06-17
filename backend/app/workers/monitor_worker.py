import asyncio
import logging
import signal
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, desc, func, select

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.models import PaperPosition, WalletScore, WatchedWallet
from app.db.session import get_sessionmaker
from app.integrations.hyperliquid_ws_client import HyperliquidWebSocketError, stream_user_fills
from app.integrations.redis_client import get_redis
from app.services.discovery_service import run_discovery_import
from app.services.job_lock_service import JobLockAlreadyHeldError, job_lock
from app.services.operation_status_service import (
    mark_operation_failed,
    mark_operation_started,
    mark_operation_succeeded,
)
from app.services.paper_trading_service import (
    PaperCopyBatchResult,
    process_paper_copy_fills,
    process_paper_copy_recovery,
)
from app.services.pool_fill_import_service import import_due_pool_wallet_fills
from app.services.realtime_event_service import publish_event
from app.services.realtime_fill_service import store_realtime_fills
from app.services.wallet_cleanup_service import prune_all_wallets
from app.services.wallet_score_service import recalculate_wallet_scores

logger = logging.getLogger(__name__)
TRADING_WORKER_ROLES = {"all", "trading"}
MAINTENANCE_WORKER_ROLES = {"all", "maintenance"}


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop_event = asyncio.Event()

    def request_stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    logger.info(
        "monitor worker started env=%s mode=%s role=%s live_trading=%s",
        settings.app_env,
        settings.system_mode,
        settings.worker_role,
        settings.live_trading_enabled,
    )
    sessionmaker = get_sessionmaker(settings)
    redis = get_redis(settings.redis_url)
    if sessionmaker is None:
        logger.error(
            "monitor worker cannot start realtime subscriptions: database is not configured"
        )
        return

    await run_monitor_services(
        sessionmaker=sessionmaker,
        redis=redis,
        stop_event=stop_event,
        settings=settings,
    )
    logger.info("monitor worker stopped")


async def run_monitor_services(
    *,
    sessionmaker: Any,
    redis: Any,
    stop_event: asyncio.Event,
    settings: Any,
) -> None:
    runs_trading = worker_runs_trading(settings)
    runs_maintenance = worker_runs_maintenance(settings)
    await publish_event(
        redis,
        event_type="system",
        channel="events:system",
        message=f"Monitor worker started with role {settings.worker_role}.",
        payload={
            "workerRole": settings.worker_role,
            "tradingLoops": runs_trading,
            "maintenanceLoops": runs_maintenance,
            "network": settings.hyperliquid_network,
            "maxRealtimeWallets": settings.max_realtime_wallets,
        },
    )
    if runs_trading and settings.paper_trading_enabled and settings.paper_copy_enabled:
        await run_paper_copy_recovery_once(
            sessionmaker=sessionmaker,
            redis=redis,
            settings=settings,
            source_wallet=None,
        )

    tasks: list[asyncio.Task[None]] = []
    if runs_maintenance and settings.discovery_enabled:
        tasks.append(
            asyncio.create_task(
                run_discovery_import_loop(
                    sessionmaker=sessionmaker,
                    redis=redis,
                    stop_event=stop_event,
                    settings=settings,
                    interval_seconds=settings.discovery_import_interval_seconds,
                    run_on_start=settings.discovery_import_on_worker_start,
                )
            )
        )

    if runs_maintenance and settings.pool_fill_import_enabled:
        tasks.append(
            asyncio.create_task(
                run_pool_fill_import_loop(
                    sessionmaker=sessionmaker,
                    redis=redis,
                    stop_event=stop_event,
                    settings=settings,
                )
            )
        )

    if (
        runs_maintenance
        and settings.scoring_enabled
        and not settings.pool_fill_import_enabled
    ):
        tasks.append(
            asyncio.create_task(
                run_scoring_loop(
                    sessionmaker=sessionmaker,
                    redis=redis,
                    stop_event=stop_event,
                    settings=settings,
                )
            )
        )

    if runs_trading and settings.paper_trading_enabled and settings.paper_copy_enabled:
        tasks.append(
            asyncio.create_task(
                run_paper_copy_recovery_loop(
                    sessionmaker=sessionmaker,
                    redis=redis,
                    stop_event=stop_event,
                    settings=settings,
                )
            )
        )

    if runs_trading:
        tasks.append(
            asyncio.create_task(
                run_realtime_monitor_loop(
                    sessionmaker=sessionmaker,
                    redis=redis,
                    stop_event=stop_event,
                    settings=settings,
                )
            )
        )

    if not tasks:
        logger.warning("monitor worker role %s has no enabled loops", settings.worker_role)

    try:
        await stop_event.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_realtime_monitor_loop(
    *,
    sessionmaker: Any,
    redis: Any,
    stop_event: asyncio.Event,
    settings: Any,
) -> None:
    while not stop_event.is_set():
        wallet_addresses = await load_realtime_wallets(
            sessionmaker=sessionmaker,
            max_wallets=settings.max_realtime_wallets,
        )
        if not wallet_addresses:
            logger.info("no enabled wallets available for realtime monitoring")
            await publish_event(
                redis,
                event_type="system",
                channel="events:system",
                message="No enabled wallets available for realtime monitoring.",
                payload={},
            )
            await sleep_until_stop(stop_event, settings.realtime_subscription_refresh_seconds)
            continue

        logger.info("subscribing to realtime fills wallets=%s", ",".join(wallet_addresses))
        await publish_event(
            redis,
            event_type="system",
            channel="events:system",
            message=f"Subscribed to {len(wallet_addresses)} realtime wallet fills.",
            payload={"walletAddresses": wallet_addresses},
        )

        subscribed_wallet_addresses = wallet_addresses.copy()

        async def handle_message(
            message: dict[str, Any],
            wallet_addresses_for_subscription: list[str] = subscribed_wallet_addresses,
        ) -> None:
            await handle_websocket_message(
                message,
                sessionmaker=sessionmaker,
                redis=redis,
                wallet_addresses=wallet_addresses_for_subscription,
                settings=settings,
            )

        try:
            await asyncio.wait_for(
                stream_user_fills(
                    settings=settings,
                    wallet_addresses=wallet_addresses,
                    on_message=handle_message,
                    stop_event=stop_event,
                ),
                timeout=settings.realtime_subscription_refresh_seconds,
            )
        except TimeoutError:
            logger.info("refreshing realtime wallet subscriptions")
        except HyperliquidWebSocketError as exc:
            logger.warning("realtime websocket disconnected: %s", exc)
            await publish_event(
                redis,
                event_type="system",
                channel="events:system",
                message="Realtime WebSocket disconnected.",
                payload={"error": str(exc)},
            )
            await sleep_until_stop(stop_event, settings.realtime_reconnect_seconds)


def worker_runs_trading(settings: Any) -> bool:
    return settings.worker_role in TRADING_WORKER_ROLES


def worker_runs_maintenance(settings: Any) -> bool:
    return settings.worker_role in MAINTENANCE_WORKER_ROLES


def main() -> None:
    asyncio.run(run_worker())


async def load_realtime_wallets(*, sessionmaker: Any, max_wallets: int) -> list[str]:
    if max_wallets <= 0:
        return []

    tier_priority = case(
        (WatchedWallet.polling_tier == "active", 0),
        (WatchedWallet.polling_tier == "exit_only", 1),
        (WatchedWallet.copy_enabled.is_(True), 2),
        (WatchedWallet.polling_tier == "candidate", 3),
        else_=4,
    )
    async with sessionmaker() as session:
        retained_result = await session.execute(
            select(PaperPosition.source_wallet)
            .outerjoin(WalletScore, WalletScore.wallet_address == PaperPosition.source_wallet)
            .where(PaperPosition.source_wallet != "")
            .group_by(PaperPosition.source_wallet)
            .order_by(
                func.max(WalletScore.score).desc().nulls_last(),
                PaperPosition.source_wallet.asc(),
            )
            .limit(max_wallets)
        )
        retained_addresses = [
            str(address).lower()
            for address in retained_result.scalars().all()
            if address
        ]
        remaining_slots = max_wallets - len(retained_addresses)
        if remaining_slots <= 0:
            return retained_addresses

        candidate_query = (
            select(WatchedWallet.address)
            .outerjoin(WalletScore, WalletScore.wallet_address == WatchedWallet.address)
            .where(
                WatchedWallet.enabled.is_(True),
                WatchedWallet.polling_tier != "cooldown",
                (
                    (WalletScore.score > 0)
                    | WatchedWallet.polling_tier.in_(["active", "exit_only", "candidate"])
                    | WatchedWallet.copy_enabled.is_(True)
                ),
            )
            .order_by(
                WalletScore.score.desc().nulls_last(),
                tier_priority,
                desc(WatchedWallet.last_seen_fill_at).nulls_last(),
            )
            .limit(remaining_slots)
        )
        if retained_addresses:
            candidate_query = candidate_query.where(
                ~WatchedWallet.address.in_(retained_addresses)
            )

        result = await session.execute(candidate_query)
        candidate_addresses = [
            str(address).lower()
            for address in result.scalars().all()
            if address
        ]
        return retained_addresses + candidate_addresses


async def run_discovery_import_loop(
    *,
    sessionmaker: Any,
    redis: Any,
    stop_event: asyncio.Event,
    settings: Any,
    interval_seconds: int,
    run_on_start: bool,
) -> None:
    if not run_on_start:
        await sleep_until_stop(stop_event, interval_seconds)

    while not stop_event.is_set():
        try:
            async with sessionmaker() as session:
                result = await run_discovery_import(session, settings=settings)
            logger.info(
                "discovery import completed sources=%s fetched=%s candidates=%s "
                "inserted=%s skipped=%s failed_sources=%s backfilled=%s pool_inserted=%s",
                ",".join(result.requested_sources),
                result.fetched,
                result.candidate_count,
                result.inserted,
                result.skipped,
                result.failed_sources,
                result.backfill.backfilled if result.backfill else 0,
                result.backfill.pool_inserted if result.backfill else 0,
            )
            await publish_event(
                redis,
                event_type="discovery_import",
                channel="events:system",
                message=(
                    "Discovery import completed: "
                    f"{result.candidate_count} candidates, {result.inserted} inserted, "
                    f"{result.backfill.pool_inserted if result.backfill else 0} added to pool."
                ),
                payload={
                    "sources": result.requested_sources,
                    "fetched": result.fetched,
                    "candidateCount": result.candidate_count,
                    "inserted": result.inserted,
                    "updated": result.updated,
                    "skipped": result.skipped,
                    "failedSources": result.failed_sources,
                    "prefilterAccepted": result.prefilter.accepted if result.prefilter else 0,
                    "prefilterRejected": result.prefilter.rejected if result.prefilter else 0,
                    "backfilled": result.backfill.backfilled if result.backfill else 0,
                    "poolInserted": result.backfill.pool_inserted if result.backfill else 0,
                    "poolDuplicate": result.backfill.pool_duplicate if result.backfill else 0,
                    "backfillFailed": result.backfill.failed if result.backfill else 0,
                    "limit": result.limit,
                },
            )
        except JobLockAlreadyHeldError as exc:
            logger.info("discovery import skipped: %s", exc)
        except Exception as exc:
            logger.exception("discovery import failed")
            await publish_event(
                redis,
                event_type="discovery_import_error",
                channel="events:system",
                message="Discovery import failed.",
                payload={"error": str(exc)},
            )

        await sleep_until_stop(stop_event, interval_seconds)


async def run_pool_fill_import_loop(
    *,
    sessionmaker: Any,
    redis: Any,
    stop_event: asyncio.Event,
    settings: Any,
) -> None:
    if not settings.pool_fill_import_run_on_worker_start:
        await sleep_until_stop(stop_event, settings.pool_fill_import_interval_seconds)
    elif settings.pool_fill_import_start_delay_seconds > 0:
        await sleep_until_stop(stop_event, settings.pool_fill_import_start_delay_seconds)

    while not stop_event.is_set():
        try:
            async with sessionmaker() as session:
                result = await import_due_pool_wallet_fills(
                    session,
                    limit=settings.pool_fill_import_batch_size,
                    days=settings.pool_fill_import_days,
                    max_pages=settings.pool_fill_import_max_pages,
                    min_wallet_interval_seconds=(
                        settings.pool_fill_import_min_wallet_interval_seconds
                    ),
                    overlap_seconds=settings.pool_fill_import_overlap_seconds,
                    max_batches=settings.pool_fill_import_max_batches,
                )
            logger.info(
                "pool fill import completed scanned=%s inserted=%s duplicate=%s failed=%s",
                result.scanned,
                result.inserted,
                result.duplicate,
                result.failed,
            )
            await publish_event(
                redis,
                event_type="pool_fill_import",
                channel="events:system",
                message=(
                    "Pool fill import completed: "
                    f"{result.imported_wallets} wallets, {result.inserted} new fills."
                ),
                payload={
                    "scanned": result.scanned,
                    "importedWallets": result.imported_wallets,
                    "fetched": result.fetched,
                    "inserted": result.inserted,
                    "duplicate": result.duplicate,
                    "failed": result.failed,
                    "limit": result.limit,
                },
            )
            if (
                worker_runs_trading(settings)
                and settings.paper_trading_enabled
                and settings.paper_copy_enabled
            ):
                await run_paper_copy_recovery_once(
                    sessionmaker=sessionmaker,
                    redis=redis,
                    settings=settings,
                    source_wallet=None,
                )
            if settings.scoring_enabled:
                await run_wallet_scoring_once(
                    sessionmaker=sessionmaker,
                    redis=redis,
                    settings=settings,
                )
            if settings.wallet_prune_after_pool_import_enabled:
                await run_wallet_prune_once(
                    sessionmaker=sessionmaker,
                    redis=redis,
                    settings=settings,
                )
        except JobLockAlreadyHeldError as exc:
            logger.info("pool fill import skipped: %s", exc)
        except Exception as exc:
            logger.exception("pool fill import failed")
            await publish_event(
                redis,
                event_type="pool_fill_import_error",
                channel="events:system",
                message="Pool fill import failed.",
                payload={"error": str(exc)},
            )

        await sleep_until_stop(stop_event, settings.pool_fill_import_interval_seconds)


async def run_scoring_loop(
    *,
    sessionmaker: Any,
    redis: Any,
    stop_event: asyncio.Event,
    settings: Any,
) -> None:
    if not settings.scoring_run_on_worker_start:
        await sleep_until_stop(stop_event, settings.scoring_interval_seconds)

    while not stop_event.is_set():
        await run_wallet_scoring_once(
            sessionmaker=sessionmaker,
            redis=redis,
            settings=settings,
        )
        await sleep_until_stop(stop_event, settings.scoring_interval_seconds)


async def run_wallet_scoring_once(
    *,
    sessionmaker: Any,
    redis: Any,
    settings: Any,
) -> None:
    try:
        async with sessionmaker() as session:
            result = await recalculate_wallet_scores(session, settings=settings)
        logger.info(
            "wallet scoring completed total=%s scored=%s window_days=%s",
            result.total_wallets,
            result.scored_wallets,
            result.window_days,
        )
        await publish_event(
            redis,
            event_type="wallet_scoring",
            channel="events:system",
            message=(
                "Wallet scoring completed: "
                f"{result.scored_wallets} wallets over {result.window_days}d."
            ),
            payload=result.model_dump(mode="json"),
        )
    except JobLockAlreadyHeldError as exc:
        logger.info("wallet scoring skipped: %s", exc)
    except Exception as exc:
        logger.exception("wallet scoring failed")
        await publish_event(
            redis,
            event_type="wallet_scoring_error",
            channel="events:system",
            message="Wallet scoring failed.",
            payload={"error": str(exc)},
        )


async def run_paper_copy_recovery_loop(
    *,
    sessionmaker: Any,
    redis: Any,
    stop_event: asyncio.Event,
    settings: Any,
) -> None:
    while not stop_event.is_set():
        await sleep_until_stop(stop_event, settings.paper_copy_recovery_interval_seconds)
        if stop_event.is_set():
            return
        await run_paper_copy_recovery_once(
            sessionmaker=sessionmaker,
            redis=redis,
            settings=settings,
            source_wallet=None,
        )


async def run_paper_copy_recovery_once(
    *,
    sessionmaker: Any,
    redis: Any,
    settings: Any,
    source_wallet: str | None,
) -> PaperCopyBatchResult:
    try:
        async with sessionmaker() as session:
            async with job_lock(
                session,
                key="paper_copy_recovery",
                ttl_seconds=max(settings.paper_copy_recovery_interval_seconds * 3, 300),
            ):
                result = await process_paper_copy_recovery(
                    session,
                    source_wallet=source_wallet,
                    settings=settings,
                )
        if result.processed_fills > 0 or result.skipped_fills > 0:
            logger.info(
                "paper copy recovery completed source_wallet=%s processed=%s skipped=%s",
                source_wallet or "all",
                result.processed_fills,
                result.skipped_fills,
            )
            await publish_event(
                redis,
                event_type="paper_copy_recovery",
                channel="events:fills",
                message=(
                    "Paper copy recovery completed: "
                    f"{result.processed_fills} processed, {result.skipped_fills} skipped."
                ),
                payload={
                    "sourceWallet": source_wallet,
                    "processedFills": result.processed_fills,
                    "skippedFills": result.skipped_fills,
                    "accountsUpdated": result.accounts_updated,
                    "realizedPnlUsd": str(result.realized_pnl_usd),
                    "feeUsd": str(result.fee_usd),
                },
            )
        return result
    except JobLockAlreadyHeldError as exc:
        logger.info("paper copy recovery skipped: %s", exc)
        return PaperCopyBatchResult()
    except Exception as exc:
        logger.exception("paper copy recovery failed source_wallet=%s", source_wallet or "all")
        await publish_event(
            redis,
            event_type="paper_copy_recovery_error",
            channel="events:system",
            message="Paper copy recovery failed.",
            payload={"sourceWallet": source_wallet, "error": str(exc)},
        )
        return PaperCopyBatchResult()


async def run_wallet_prune_once(
    *,
    sessionmaker: Any,
    redis: Any,
    settings: Any,
) -> None:
    payload = {
        "dryRun": settings.wallet_prune_worker_dry_run,
        "limit": settings.wallet_prune_worker_limit,
    }
    try:
        async with sessionmaker() as session:
            async with job_lock(session, key="wallet_prune", ttl_seconds=4 * 60 * 60):
                await mark_operation_started(session, key="wallet_prune", payload=payload)
                result = await prune_all_wallets(
                    session,
                    dry_run=settings.wallet_prune_worker_dry_run,
                    high_fill_min_fills=settings.wallet_prune_low_score_min_fills,
                    high_fill_score_threshold=settings.wallet_prune_low_score_threshold,
                    high_fill_score_operator=settings.wallet_prune_low_score_operator,
                    min_closed_trades=settings.wallet_prune_min_closed_trades,
                    max_drawdown_threshold_pct=settings.wallet_prune_max_drawdown_pct,
                    current_drawdown_threshold_ratio=settings.wallet_prune_unrealized_loss_ratio,
                    current_drawdown_concurrency=settings.wallet_prune_current_state_concurrency,
                    limit=settings.wallet_prune_worker_limit,
                    use_lock=False,
                )
                await mark_operation_succeeded(
                    session,
                    key="wallet_prune",
                    payload={
                        **payload,
                        "scannedWallets": result.scanned_wallets,
                        "candidateWallets": result.candidate_wallets,
                        "erroredWallets": result.errored_wallets,
                        "deletedWallets": result.deleted_wallets,
                        "deletedFills": result.deleted_fills,
                    },
                )
        logger.info(
            "wallet prune completed scanned=%s candidates=%s deleted_wallets=%s deleted_fills=%s",
            result.scanned_wallets,
            result.candidate_wallets,
            result.deleted_wallets,
            result.deleted_fills,
        )
        await publish_event(
            redis,
            event_type="wallet_prune",
            channel="events:system",
            message=(
                "Wallet prune completed: "
                f"{result.deleted_wallets} wallets deleted, "
                f"{result.candidate_wallets} candidates."
            ),
            payload={
                **payload,
                "scannedWallets": result.scanned_wallets,
                "candidateWallets": result.candidate_wallets,
                "erroredWallets": result.errored_wallets,
                "deletedWallets": result.deleted_wallets,
                "deletedFills": result.deleted_fills,
            },
        )
    except JobLockAlreadyHeldError as exc:
        logger.info("wallet prune skipped: %s", exc)
    except Exception as exc:
        logger.exception("wallet prune failed")
        async with sessionmaker() as session:
            await mark_operation_failed(
                session,
                key="wallet_prune",
                error=str(exc) or exc.__class__.__name__,
                payload=payload,
            )
        await publish_event(
            redis,
            event_type="wallet_prune_error",
            channel="events:system",
            message="Wallet prune failed.",
            payload={"error": str(exc)},
        )


async def handle_websocket_message(
    message: dict[str, Any],
    *,
    sessionmaker: Any,
    redis: Any,
    wallet_addresses: list[str],
    settings: Any,
) -> None:
    channel = message.get("channel")
    if channel in {"subscriptionResponse", "pong"}:
        return
    if channel != "userFills":
        logger.debug("ignored websocket channel=%s", channel)
        return

    data = message.get("data")
    if not isinstance(data, dict):
        return

    raw_wallet_address = data.get("user")
    fills = data.get("fills")
    if not isinstance(raw_wallet_address, str) or not isinstance(fills, list):
        return

    wallet_address = raw_wallet_address.lower()
    if wallet_address not in wallet_addresses:
        return

    fill_payloads = [fill for fill in fills if isinstance(fill, dict)]
    if not fill_payloads:
        return

    is_snapshot = bool(data.get("isSnapshot"))
    received_at = datetime.now(UTC)
    async with sessionmaker() as session:
        stored = await store_realtime_fills(
            session,
            wallet_address=wallet_address,
            fills=fill_payloads,
            is_snapshot=is_snapshot,
            received_at=received_at,
        )

    if is_snapshot:
        if settings.paper_trading_enabled and settings.paper_copy_enabled:
            await run_paper_copy_recovery_once(
                sessionmaker=sessionmaker,
                redis=redis,
                settings=settings,
                source_wallet=stored.wallet_address,
            )
        await publish_event(
            redis,
            event_type="fill_snapshot",
            channel="events:fills",
            message=(
                f"Snapshot processed for {short_address(stored.wallet_address)}: "
                f"{stored.inserted} new, {stored.duplicate} duplicate."
            ),
            payload={
                "walletAddress": stored.wallet_address,
                "fetched": stored.fetched,
                "inserted": stored.inserted,
                "duplicate": stored.duplicate,
            },
        )
        return

    for fill in stored.inserted_rows:
        await publish_event(
            redis,
            event_type="fill",
            channel="events:fills",
            message=(
                f"{short_address(stored.wallet_address)} {fill['side']} "
                f"{fill['coin']} @ {fill['price']}"
            ),
            payload={
                "walletAddress": stored.wallet_address,
                "fill": fill,
            },
        )

    if (
        settings.paper_trading_enabled
        and settings.paper_copy_enabled
        and stored.inserted_rows
    ):
        try:
            async with sessionmaker() as session:
                paper_result = await process_paper_copy_fills(
                    session,
                    source_wallet=stored.wallet_address,
                    fills=stored.inserted_rows,
                    settings=settings,
                )
            if paper_result.processed_fills > 0 or paper_result.skipped_fills > 0:
                await publish_event(
                    redis,
                    event_type="paper_copy",
                    channel="events:fills",
                    message=(
                        f"Paper copied {paper_result.processed_fills} fills from "
                        f"{short_address(stored.wallet_address)}."
                    ),
                    payload={
                        "walletAddress": stored.wallet_address,
                        "processedFills": paper_result.processed_fills,
                        "skippedFills": paper_result.skipped_fills,
                        "accountsUpdated": paper_result.accounts_updated,
                        "realizedPnlUsd": str(paper_result.realized_pnl_usd),
                        "feeUsd": str(paper_result.fee_usd),
                    },
                )
        except Exception as exc:
            logger.exception("paper copy processing failed wallet=%s", stored.wallet_address)
            await publish_event(
                redis,
                event_type="paper_copy_error",
                channel="events:system",
                message="Paper copy processing failed.",
                payload={"walletAddress": stored.wallet_address, "error": str(exc)},
            )


async def sleep_until_stop(stop_event: asyncio.Event, seconds: int) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


def short_address(address: str) -> str:
    return f"{address[:8]}...{address[-6:]}"


if __name__ == "__main__":
    main()
