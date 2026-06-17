from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import DiscoveryImportRun, DiscoveryWalletCandidate, WalletFill, WatchedWallet
from app.integrations.hyperliquid_client import HyperliquidClient, HyperliquidRateLimitError
from app.integrations.hyperliquid_leaderboard_client import HyperliquidLeaderboardClient
from app.schemas.discovery import (
    DiscoveryBackfillItem,
    DiscoveryBackfillResponse,
    DiscoveryCandidateListResponse,
    DiscoveryImportResponse,
    DiscoveryImportRunListResponse,
    DiscoveryPrefilterResponse,
    DiscoveryPromoteItem,
    DiscoveryPromoteResponse,
    DiscoverySourceListResponse,
    DiscoverySourceRead,
)
from app.schemas.fill import WalletFillImportRequest
from app.schemas.wallet import normalize_wallet_address
from app.services.fill_import_service import FillImportStorageLimitError, import_address_fills
from app.services.hyperliquid_leaderboard_source import (
    get_window,
    load_subaccount_wallet_candidates,
    select_ranked_rows,
    string_or_none,
)
from app.services.hyperliquid_leaderboard_source import (
    window_label as leaderboard_window_label,
)
from app.services.job_lock_service import job_lock
from app.services.operation_status_service import (
    mark_operation_failed,
    mark_operation_progress,
    mark_operation_started,
    mark_operation_succeeded,
)
from app.services.source_trade_reconstruction_service import (
    ReconstructedWalletTrades,
    reconstruct_wallet_trades,
)
from app.services.wallet_cleanup_service import delete_wallet_data_rows
from app.services.wallet_ignore_service import load_ignored_wallet_addresses

HYPERLIQUID_LEADERBOARD_SOURCES = {
    "hyperliquid_leaderboard_day": "day",
    "hyperliquid_leaderboard_week": "week",
    "hyperliquid_leaderboard_month": "month",
    "hyperliquid_leaderboard_all_time": "allTime",
}

HYPERDASH_URL_SETTINGS = {
    "hyperdash_copytrading": "discovery_hyperdash_copytrading_url",
    "hyperdash_cohorts": "discovery_hyperdash_cohorts_url",
    "hyperdash_tagged": "discovery_hyperdash_tagged_url",
}

HYPERDASH_GRAPHQL_URL = "https://api.hyperdash.com/graphql"
HYPERDASH_SYSTEM_GROUP_IDS = {
    "hyperdash_copytrading": "copytraders",
    "hyperdash_tagged": "tagged",
}
HYPERDASH_DEFAULT_COHORT_ID = "profitable"
HYPERDASH_GRAPHQL_PAGE_SIZE = 100
HYPERDASH_DEFAULT_COHORT_SORT = {"field": "copyScore", "order": "desc"}

HYPERDASH_SYSTEM_GROUP_QUERY = """
query GetSystemGroupTraders($groupId: ID!) {
  getSystemGroupTraders(groupId: $groupId) {
    address
    label
    displayName
    avatar
    twitter
    lastTradeAt
    lastFillAt
    pnl
    perpsEquity
    winrate
    pnlCohort
    sizeCohort
    totalTrades
    totalLongTrades
    totalShortTrades
    totalWinningTrades
    totalLosingTrades
    sharpe
    drawdown
    copyScore
    tag
    topAssets {
      coin
      volume
      pnl
    }
  }
}
"""

HYPERDASH_PNL_COHORT_QUERY = """
query GetPnlCohort($id: String!, $limit: Int!, $offset: Int!, $sortBy: CohortTraderSortInput) {
  analytics {
    pnlCohort(id: $id) {
      cohortInfo {
        id
        label
        range
        emoji
      }
      totalTraders
      totalAccountValue
      longNotional
      shortNotional
      profitTraders
      lossTraders
      topTraders(limit: $limit, offset: $offset, sortBy: $sortBy) {
        totalCount
        hasMore
        traders {
          address
          accountValue
          perpPnl
          copyScore
          displayName
          tag
          label
          verified
          totalNotional
          longNotional
          shortNotional
          lastTradeAt
          positions {
            coin
            size
            notionalSize
            unrealizedPnl
            entryPrice
          }
        }
      }
    }
  }
}
"""

SOURCE_LABELS = {
    "hyperliquid_leaderboard_day": "Hyperliquid 1D leaderboard",
    "hyperliquid_leaderboard_week": "Hyperliquid 7D leaderboard",
    "hyperliquid_leaderboard_month": "Hyperliquid 30D leaderboard",
    "hyperliquid_leaderboard_all_time": "Hyperliquid all-time leaderboard",
    "hyperdash_copytrading": "Hyperdash copytrading",
    "hyperdash_cohorts": "Hyperdash cohorts",
    "hyperdash_tagged": "Hyperdash tagged traders",
}

KNOWN_DISCOVERY_SOURCES = tuple(SOURCE_LABELS.keys())


class DiscoverySourceUnavailableError(RuntimeError):
    pass


class UnknownDiscoverySourceError(ValueError):
    pass


@dataclass(frozen=True)
class DiscoveryCandidate:
    wallet_address: str
    source: str
    source_rank: int | None = None
    source_label: str | None = None
    source_cohort: str | None = None
    account_value: Decimal | None = None
    source_pnl: Decimal | None = None
    source_roi: Decimal | None = None
    source_copy_score: Decimal | None = None
    account_role: str = "unknown"
    parent_address: str | None = None
    subaccount_name: str | None = None
    raw_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class DiscoverySourceResult:
    fetched: int
    skipped: int
    skip_reasons: dict[str, int]
    candidates: list[DiscoveryCandidate]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DiscoveryUpsertResult:
    inserted: int
    updated: int
    skipped: int
    skip_reasons: dict[str, int]


@dataclass(frozen=True)
class PrefilterDecision:
    status: str
    fail_reason: str | None = None


@dataclass(frozen=True)
class CandidateTradeMetrics:
    fill_count: int
    closed_trade_count: int
    open_trade_count: int
    ignored_fill_count: int
    net_pnl_usd: Decimal
    profit_factor: Decimal | None
    win_rate: Decimal | None
    max_drawdown_pct: Decimal | None
    average_trade_notional_usd: Decimal
    last_trade_time_ms: int | None


async def list_discovery_sources(
    *,
    settings: Settings | None = None,
) -> DiscoverySourceListResponse:
    resolved_settings = settings or get_settings()
    default_sources = set(resolved_settings.discovery_default_sources)
    items: list[DiscoverySourceRead] = []

    for source in KNOWN_DISCOVERY_SOURCES:
        provider = "hyperliquid" if source in HYPERLIQUID_LEADERBOARD_SOURCES else "hyperdash"
        configured = source in HYPERLIQUID_LEADERBOARD_SOURCES or bool(
            hyperdash_url_for_source(source, resolved_settings)
        )
        notes = None
        if source in HYPERDASH_URL_SETTINGS and not configured:
            notes = "Set the matching discovery_hyperdash_*_url config before running this source."
        items.append(
            DiscoverySourceRead(
                key=source,
                label=SOURCE_LABELS[source],
                provider=provider,
                enabled=resolved_settings.discovery_enabled and source in default_sources,
                configured=configured,
                notes=notes,
            )
        )

    return DiscoverySourceListResponse(items=items)


async def run_discovery_import(
    session: AsyncSession,
    *,
    sources: list[str] | None = None,
    limit: int | None = None,
    run_pipeline: bool = True,
    settings: Settings | None = None,
    use_lock: bool = True,
) -> DiscoveryImportResponse:
    if use_lock:
        async with job_lock(session, key="discovery_import", ttl_seconds=12 * 60 * 60):
            return await run_discovery_import(
                session,
                sources=sources,
                limit=limit,
                run_pipeline=run_pipeline,
                settings=settings,
                use_lock=False,
            )

    resolved_settings = settings or get_settings()
    requested_sources = normalize_requested_sources(
        sources or resolved_settings.discovery_default_sources
    )
    requested_limit = limit or resolved_settings.discovery_import_limit
    payload = discovery_operation_payload(
        sources=requested_sources,
        limit=requested_limit,
        run_pipeline=run_pipeline,
        stage="starting",
        stage_label="Starting discovery",
        stage_detail="Preparing discovery sources.",
        progress_percent=0,
        progress_current=0,
        progress_total=100,
    )

    await mark_operation_started(session, key="discovery_import", payload=payload)
    try:
        response = await _run_discovery_import(
            session,
            sources=requested_sources,
            limit=requested_limit,
            run_pipeline=run_pipeline,
            operation_payload=payload,
            settings=resolved_settings,
        )
    except Exception as exc:
        await session.rollback()
        await mark_operation_failed(
            session,
            key="discovery_import",
            error=str(exc) or exc.__class__.__name__,
            payload=payload,
        )
        raise

    await mark_operation_succeeded(
        session,
        key="discovery_import",
        payload={
            **payload,
            "stage": "complete",
            "stageLabel": "Discovery complete",
            "stageDetail": "Discovery import, prefilter, backfill and pool insert checks finished.",
            "progressPercent": 100,
            "progressCurrent": 100,
            "progressTotal": 100,
            "fetched": response.fetched,
            "candidates": response.candidate_count,
            "inserted": response.inserted,
            "updated": response.updated,
            "skipped": response.skipped,
            "skipReasons": response.skip_reasons,
            "failedSources": response.failed_sources,
            "prefilterAccepted": response.prefilter.accepted if response.prefilter else 0,
            "prefilterRejected": response.prefilter.rejected if response.prefilter else 0,
            "backfilled": response.backfill.backfilled if response.backfill else 0,
            "poolInserted": response.backfill.pool_inserted if response.backfill else 0,
            "poolDuplicate": response.backfill.pool_duplicate if response.backfill else 0,
            "backfillFailed": response.backfill.failed if response.backfill else 0,
        },
    )
    return response


async def run_discovery_prefilter(
    session: AsyncSession,
    *,
    source: str | None = None,
    status: str | None = None,
    limit: int = 500,
    settings: Settings | None = None,
    use_lock: bool = True,
) -> DiscoveryPrefilterResponse:
    if use_lock:
        async with job_lock(session, key="discovery_prefilter", ttl_seconds=2 * 60 * 60):
            return await run_discovery_prefilter(
                session,
                source=source,
                status=status,
                limit=limit,
                settings=settings,
                use_lock=False,
            )

    resolved_settings = settings or get_settings()
    payload = {"source": source, "status": status, "limit": limit}
    await mark_operation_started(session, key="discovery_prefilter", payload=payload)
    try:
        response = await prefilter_discovery_candidates(
            session,
            source=source,
            status=status,
            limit=limit,
            settings=resolved_settings,
            commit=True,
        )
    except Exception as exc:
        await session.rollback()
        await mark_operation_failed(
            session,
            key="discovery_prefilter",
            error=str(exc) or exc.__class__.__name__,
            payload=payload,
        )
        raise

    await mark_operation_succeeded(
        session,
        key="discovery_prefilter",
        payload={
            **payload,
            "evaluated": response.evaluated,
            "accepted": response.accepted,
            "rejected": response.rejected,
            "unchanged": response.unchanged,
        },
    )
    return response


async def run_discovery_candidate_backfill(
    session: AsyncSession,
    *,
    source: str | None = None,
    limit: int | None = None,
    retry_failed: bool = False,
    settings: Settings | None = None,
    use_lock: bool = True,
) -> DiscoveryBackfillResponse:
    if use_lock:
        async with job_lock(session, key="discovery_backfill", ttl_seconds=12 * 60 * 60):
            return await run_discovery_candidate_backfill(
                session,
                source=source,
                limit=limit,
                retry_failed=retry_failed,
                settings=settings,
                use_lock=False,
            )

    resolved_settings = settings or get_settings()
    batch_limit = limit or resolved_settings.discovery_candidate_backfill_batch_size
    payload = {
        "source": source,
        "limit": batch_limit,
        "days": resolved_settings.discovery_candidate_backfill_days,
        "targetFills": resolved_settings.discovery_candidate_backfill_target_fills,
        "retryFailed": retry_failed,
    }
    await mark_operation_started(session, key="discovery_backfill", payload=payload)
    try:
        response = await backfill_discovery_candidates(
            session,
            source=source,
            run_ids=None,
            limit=batch_limit,
            retry_failed=retry_failed,
            settings=resolved_settings,
        )
    except Exception as exc:
        await session.rollback()
        await mark_operation_failed(
            session,
            key="discovery_backfill",
            error=str(exc) or exc.__class__.__name__,
            payload=payload,
        )
        raise

    await mark_operation_succeeded(
        session,
        key="discovery_backfill",
        payload={
            **payload,
            "scanned": response.scanned,
            "backfilled": response.backfilled,
            "accepted": response.accepted,
            "rejected": response.rejected,
            "promoted": response.promoted,
            "poolInserted": response.pool_inserted,
            "poolDuplicate": response.pool_duplicate,
            "failed": response.failed,
            "skipped": response.skipped,
        },
    )
    return response


async def run_discovery_candidate_promotion(
    session: AsyncSession,
    *,
    source: str | None = None,
    limit: int | None = None,
    include_unbackfilled: bool = False,
    run_all: bool = False,
    max_batches: int = 1,
    settings: Settings | None = None,
    use_lock: bool = True,
) -> DiscoveryPromoteResponse:
    if use_lock:
        async with job_lock(session, key="discovery_promotion", ttl_seconds=2 * 60 * 60):
            return await run_discovery_candidate_promotion(
                session,
                source=source,
                limit=limit,
                include_unbackfilled=include_unbackfilled,
                run_all=run_all,
                max_batches=max_batches,
                settings=settings,
                use_lock=False,
            )

    resolved_settings = settings or get_settings()
    batch_limit = limit or resolved_settings.discovery_promotion_batch_size
    require_backfill = (
        resolved_settings.discovery_promotion_require_backfill and not include_unbackfilled
    )
    payload = {
        "source": source,
        "limit": batch_limit,
        "requireBackfill": require_backfill,
        "runAll": run_all,
        "maxBatches": max_batches,
    }
    await mark_operation_started(session, key="discovery_promotion", payload=payload)
    try:
        if run_all:
            response = await promote_all_discovery_candidates(
                session,
                source=source,
                limit=batch_limit,
                require_backfill=require_backfill,
                max_batches=max_batches,
            )
        else:
            response = await promote_discovery_candidates(
                session,
                source=source,
                limit=batch_limit,
                require_backfill=require_backfill,
            )
    except Exception as exc:
        await session.rollback()
        await mark_operation_failed(
            session,
            key="discovery_promotion",
            error=str(exc) or exc.__class__.__name__,
            payload=payload,
        )
        raise

    await mark_operation_succeeded(
        session,
        key="discovery_promotion",
        payload={
            **payload,
            "scanned": response.scanned,
            "promoted": response.promoted,
            "inserted": response.inserted,
            "duplicate": response.duplicate,
            "skipped": response.skipped,
        },
    )
    return response


async def _run_discovery_import(
    session: AsyncSession,
    *,
    sources: list[str],
    limit: int,
    run_pipeline: bool,
    operation_payload: dict[str, Any],
    settings: Settings,
) -> DiscoveryImportResponse:
    runs: list[DiscoveryImportRun] = []
    candidate_models: list[DiscoveryWalletCandidate] = []
    prefilter_response: DiscoveryPrefilterResponse | None = None
    backfill_response: DiscoveryBackfillResponse | None = None
    skip_reasons: dict[str, int] = {}

    source_count = max(1, len(sources))
    for source_index, source in enumerate(sources, start=1):
        await update_discovery_operation_progress(
            session,
            operation_payload,
            stage="source_import",
            stage_label="Importing candidates",
            stage_detail=f"Fetching source {source_index} of {source_count}: {source}.",
            progress_percent=progress_between(
                start=0,
                end=25,
                current=source_index - 1,
                total=source_count,
            ),
            extra={
                "currentSource": source,
                "sourceIndex": source_index,
                "sourceCount": source_count,
            },
        )
        run = DiscoveryImportRun(
            id=uuid4(),
            source=source,
            status="running",
            requested_limit=limit,
        )
        session.add(run)
        await session.flush()

        try:
            source_result = await fetch_discovery_source(
                source,
                limit=limit,
                settings=settings,
            )
            upsert_result = await upsert_discovery_candidates(
                session,
                run_id=run.id,
                candidates=source_result.candidates,
            )
            merge_counts(skip_reasons, source_result.skip_reasons)
            merge_counts(skip_reasons, upsert_result.skip_reasons)
            run.status = "succeeded"
            run.fetched_count = source_result.fetched
            run.candidate_count = upsert_result.inserted + upsert_result.updated
            run.inserted_count = upsert_result.inserted
            run.updated_count = upsert_result.updated
            run.skipped_count = source_result.skipped + upsert_result.skipped
            run.finished_at = datetime.now(UTC)
            run_skip_reasons: dict[str, int] = {}
            merge_counts(run_skip_reasons, source_result.skip_reasons)
            merge_counts(run_skip_reasons, upsert_result.skip_reasons)
            run.run_metadata = {
                **source_result.metadata,
                "skipReasons": run_skip_reasons,
            }
            runs.append(run)
            await update_discovery_operation_progress(
                session,
                operation_payload,
                stage="source_import",
                stage_label="Importing candidates",
                stage_detail=(
                    f"Imported {source}: {upsert_result.inserted} new, "
                    f"{upsert_result.skipped} skipped."
                ),
                progress_percent=progress_between(
                    start=0,
                    end=25,
                    current=source_index,
                    total=source_count,
                ),
                extra=discovery_run_progress_counts(runs)
                | {"skipReasons": skip_reasons}
                | {
                    "currentSource": source,
                    "sourceIndex": source_index,
                    "sourceCount": source_count,
                },
            )
        except Exception as exc:
            await session.flush()
            run.status = "failed"
            run.error = str(exc) or exc.__class__.__name__
            run.finished_at = datetime.now(UTC)
            run.run_metadata = {"source": source}
            runs.append(run)
            await update_discovery_operation_progress(
                session,
                operation_payload,
                stage="source_import",
                stage_label="Importing candidates",
                stage_detail=f"Source {source} failed: {str(exc) or exc.__class__.__name__}.",
                progress_percent=progress_between(
                    start=0,
                    end=25,
                    current=source_index,
                    total=source_count,
                ),
                extra=discovery_run_progress_counts(runs)
                | {"skipReasons": skip_reasons}
                | {
                    "currentSource": source,
                    "sourceIndex": source_index,
                    "sourceCount": source_count,
                },
            )

    run_ids = [run.id for run in runs if run.status == "succeeded"]
    if (
        run_pipeline
        and settings.discovery_prefilter_enabled
        and settings.discovery_prefilter_run_after_import
    ):
        candidate_count = sum(run.candidate_count for run in runs if run.status == "succeeded")
        await update_discovery_operation_progress(
            session,
            operation_payload,
            stage="prefilter",
            stage_label="Filtering candidates",
            stage_detail=f"Running source-quality filters on {candidate_count} new candidates.",
            progress_percent=25,
            extra=discovery_run_progress_counts(runs)
            | {"skipReasons": skip_reasons, "prefilterTotal": candidate_count},
        )
        prefilter_response = await prefilter_discovery_candidates(
            session,
            run_ids=run_ids,
            limit=max(1, candidate_count),
            settings=settings,
            commit=False,
        )
        await session.flush()
        await update_discovery_operation_progress(
            session,
            operation_payload,
            stage="prefilter",
            stage_label="Filtering candidates",
            stage_detail=(
                f"Prefilter accepted {prefilter_response.accepted}, "
                f"rejected {prefilter_response.rejected}."
            ),
            progress_percent=35,
            extra=discovery_run_progress_counts(runs)
            | {"skipReasons": skip_reasons}
            | {
                "prefilterEvaluated": prefilter_response.evaluated,
                "prefilterAccepted": prefilter_response.accepted,
                "prefilterRejected": prefilter_response.rejected,
                "prefilterUnchanged": prefilter_response.unchanged,
            },
        )

        backfill_total = max(0, prefilter_response.accepted)
        backfill_processed = 0

        async def on_backfill_progress(event: dict[str, Any]) -> None:
            nonlocal backfill_processed
            if event.get("event") in {"candidate_finished", "candidate_failed"}:
                backfill_processed = min(backfill_total, backfill_processed + 1)
            current_wallet = str(event.get("walletAddress") or "")
            event_label = str(event.get("label") or "Backfilling accepted candidates")
            await update_discovery_operation_progress(
                session,
                operation_payload,
                stage="backfill",
                stage_label="Backfilling candidates",
                stage_detail=event_label,
                progress_percent=progress_between(
                    start=35,
                    end=95,
                    current=backfill_processed,
                    total=max(1, backfill_total),
                ),
                extra=discovery_run_progress_counts(runs)
                | {"skipReasons": skip_reasons}
                | {
                    "backfillProcessed": backfill_processed,
                    "backfillTotal": backfill_total,
                    "currentWallet": current_wallet or None,
                },
            )

        await update_discovery_operation_progress(
            session,
            operation_payload,
            stage="backfill",
            stage_label="Backfilling candidates",
            stage_detail=f"Backfilling {backfill_total} accepted candidates.",
            progress_percent=35,
            extra=discovery_run_progress_counts(runs)
            | {"skipReasons": skip_reasons}
            | {
                "backfillProcessed": 0,
                "backfillTotal": backfill_total,
            },
        )
        backfill_response = await backfill_all_discovery_candidates(
            session,
            run_ids=run_ids,
            settings=settings,
            progress_callback=on_backfill_progress,
        )
        await update_discovery_operation_progress(
            session,
            operation_payload,
            stage="finalizing",
            stage_label="Finalizing discovery",
            stage_detail=(
                f"Backfilled {backfill_response.backfilled}, "
                f"added {backfill_response.pool_inserted} new wallets to pool."
            ),
            progress_percent=95,
            extra=discovery_run_progress_counts(runs)
            | {"skipReasons": skip_reasons}
            | {
                "backfilled": backfill_response.backfilled,
                "accepted": backfill_response.accepted,
                "rejected": backfill_response.rejected,
                "promoted": backfill_response.promoted,
                "poolInserted": backfill_response.pool_inserted,
                "poolDuplicate": backfill_response.pool_duplicate,
                "backfillFailed": backfill_response.failed,
            },
        )

    await session.commit()

    if runs:
        run_ids = [run.id for run in runs]
        result = await session.execute(
            select(DiscoveryWalletCandidate)
            .where(DiscoveryWalletCandidate.last_import_run_id.in_(run_ids))
            .order_by(
                DiscoveryWalletCandidate.source.asc(),
                DiscoveryWalletCandidate.source_rank.asc().nulls_last(),
                DiscoveryWalletCandidate.last_seen_at.desc(),
            )
            .limit(250)
        )
        candidate_models = list(result.scalars().all())

    return DiscoveryImportResponse(
        requested_sources=sources,
        limit=limit,
        runs=runs,
        candidates=candidate_models,
        fetched=sum(run.fetched_count for run in runs),
        candidate_count=sum(run.candidate_count for run in runs),
        inserted=sum(run.inserted_count for run in runs),
        updated=sum(run.updated_count for run in runs),
        skipped=sum(run.skipped_count for run in runs),
        skip_reasons=skip_reasons,
        failed_sources=sum(1 for run in runs if run.status == "failed"),
        prefilter=prefilter_response,
        backfill=backfill_response,
    )


def discovery_operation_payload(
    *,
    sources: list[str],
    limit: int,
    run_pipeline: bool,
    stage: str,
    stage_label: str,
    stage_detail: str,
    progress_percent: int,
    progress_current: int,
    progress_total: int,
) -> dict[str, Any]:
    return {
        "sources": sources,
        "limit": limit,
        "runPipeline": run_pipeline,
        "stage": stage,
        "stageLabel": stage_label,
        "stageDetail": stage_detail,
        "progressPercent": clamp_progress(progress_percent),
        "progressCurrent": progress_current,
        "progressTotal": progress_total,
    }


async def update_discovery_operation_progress(
    session: AsyncSession,
    base_payload: dict[str, Any],
    *,
    stage: str,
    stage_label: str,
    stage_detail: str,
    progress_percent: int,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = {
        **base_payload,
        **(extra or {}),
        "stage": stage,
        "stageLabel": stage_label,
        "stageDetail": stage_detail,
        "progressPercent": clamp_progress(progress_percent),
        "progressCurrent": clamp_progress(progress_percent),
        "progressTotal": 100,
    }
    await mark_operation_progress(session, key="discovery_import", payload=payload)


def discovery_run_progress_counts(runs: list[DiscoveryImportRun]) -> dict[str, int]:
    return {
        "fetched": sum(run.fetched_count for run in runs),
        "candidates": sum(run.candidate_count for run in runs),
        "inserted": sum(run.inserted_count for run in runs),
        "updated": sum(run.updated_count for run in runs),
        "skipped": sum(run.skipped_count for run in runs),
        "failedSources": sum(1 for run in runs if run.status == "failed"),
    }


def add_count(counts: dict[str, int], key: str, amount: int = 1) -> None:
    if amount <= 0:
        return
    counts[key] = counts.get(key, 0) + amount


def merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        add_count(target, key, value)


def progress_between(*, start: int, end: int, current: int, total: int) -> int:
    if total <= 0:
        return clamp_progress(end)
    span = end - start
    return clamp_progress(round(start + (span * current / total)))


def clamp_progress(value: int | float) -> int:
    return max(0, min(100, int(round(value))))


async def fetch_discovery_source(
    source: str,
    *,
    limit: int,
    settings: Settings,
) -> DiscoverySourceResult:
    if source in HYPERLIQUID_LEADERBOARD_SOURCES:
        return await fetch_hyperliquid_leaderboard_source(source, limit=limit, settings=settings)
    if source in HYPERDASH_URL_SETTINGS:
        return await fetch_hyperdash_source(source, limit=limit, settings=settings)
    raise UnknownDiscoverySourceError(f"Unknown discovery source: {source}")


async def fetch_hyperliquid_leaderboard_source(
    source: str,
    *,
    limit: int,
    settings: Settings,
) -> DiscoverySourceResult:
    window = HYPERLIQUID_LEADERBOARD_SOURCES[source]
    leaderboard_client = HyperliquidLeaderboardClient(settings)
    payload = await leaderboard_client.get_leaderboard()
    rows = payload.get("leaderboardRows")
    if not isinstance(rows, list):
        rows = []

    selected_rows = select_ranked_rows(
        rows,
        limit=limit,
        window=window,
        sort_metric=settings.discovery_hyperliquid_sort_metric,
    )
    candidates: list[DiscoveryCandidate] = []
    skipped = 0
    skip_reasons: dict[str, int] = {}
    subaccount_client = (
        HyperliquidClient(settings) if settings.discovery_import_subaccounts_enabled else None
    )

    for rank, row in enumerate(selected_rows, start=1):
        if not isinstance(row, dict):
            skipped += 1
            add_count(skip_reasons, "invalid_source_row")
            continue
        raw_address = row.get("ethAddress")
        if not isinstance(raw_address, str):
            skipped += 1
            add_count(skip_reasons, "missing_address")
            continue
        try:
            address = normalize_wallet_address(raw_address)
        except ValueError:
            skipped += 1
            add_count(skip_reasons, "invalid_address")
            continue

        performance = get_window(row, window) or {}
        display_name = row.get("displayName") if isinstance(row.get("displayName"), str) else None
        account_value = decimal_or_none(row.get("accountValue"))
        candidates.append(
            DiscoveryCandidate(
                wallet_address=address,
                source=source,
                source_rank=rank,
                source_label=display_name or f"HL {leaderboard_window_label(window)} #{rank}",
                source_cohort=leaderboard_window_label(window),
                account_value=account_value,
                source_pnl=decimal_or_none(performance.get("pnl")),
                source_roi=decimal_or_none(performance.get("roi")),
                account_role="master",
                raw_payload=compact_leaderboard_payload(row, window=window),
            )
        )

        if subaccount_client is not None:
            subaccounts = await load_subaccount_wallet_candidates(
                client=subaccount_client,
                master_address=address,
                rank=rank,
                display_name=display_name,
                row=row,
                window=window,
                max_subaccounts=settings.discovery_import_max_subaccounts_per_wallet,
            )
            for subaccount in subaccounts:
                candidates.append(
                    DiscoveryCandidate(
                        wallet_address=subaccount.address,
                        source=source,
                        source_rank=rank,
                        source_label=subaccount.label,
                        source_cohort=f"{leaderboard_window_label(window)} subaccount",
                        account_value=decimal_or_none(subaccount.account_value),
                        source_pnl=decimal_or_none(subaccount.window_pnl),
                        source_roi=decimal_or_none(subaccount.window_roi),
                        account_role="subaccount",
                        parent_address=subaccount.parent_address,
                        subaccount_name=subaccount.subaccount_name,
                        raw_payload={
                            "parentAddress": subaccount.parent_address,
                            "subaccountName": subaccount.subaccount_name,
                            "sourceWindow": window,
                            "sourceRank": rank,
                        },
                    )
                )

    return DiscoverySourceResult(
        fetched=len(selected_rows),
        skipped=skipped,
        skip_reasons=skip_reasons,
        candidates=candidates,
        metadata={
            "provider": "hyperliquid",
            "window": window,
            "sortMetric": settings.discovery_hyperliquid_sort_metric,
            "subaccountsEnabled": settings.discovery_import_subaccounts_enabled,
        },
    )


async def fetch_hyperdash_source(
    source: str,
    *,
    limit: int,
    settings: Settings,
) -> DiscoverySourceResult:
    url = hyperdash_url_for_source(source, settings)
    if not url:
        raise DiscoverySourceUnavailableError(
            f"{source} is not configured. Set {HYPERDASH_URL_SETTINGS[source]} in backend config."
        )

    if should_use_hyperdash_graphql(url):
        return await fetch_hyperdash_graphql_source(source, url=url, limit=limit)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url)
    if response.status_code >= 400:
        raise DiscoverySourceUnavailableError(
            f"{source} request failed with status {response.status_code}."
        )

    payload = response.json()
    rows = extract_hyperdash_rows(payload)[:limit]
    candidates: list[DiscoveryCandidate] = []
    skipped = 0
    skip_reasons: dict[str, int] = {}

    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            skipped += 1
            add_count(skip_reasons, "invalid_source_row")
            continue
        address = extract_address(row)
        if address is None:
            skipped += 1
            add_count(skip_reasons, "missing_or_invalid_address")
            continue

        candidates.append(
            DiscoveryCandidate(
                wallet_address=address,
                source=source,
                source_rank=int_or_none(row.get("rank")) or rank,
                source_label=string_or_none(
                    row.get("displayName") or row.get("name") or row.get("label")
                ),
                source_cohort=string_or_none(row.get("cohort") or row.get("group") or source),
                account_value=decimal_or_none(
                    row.get("accountValue") or row.get("equity") or row.get("account_value")
                ),
                source_pnl=decimal_or_none(row.get("pnl") or row.get("roiUsd") or row.get("roi")),
                source_roi=decimal_or_none(row.get("pnlPct") or row.get("roiPct")),
                source_copy_score=decimal_or_none(row.get("copyScore") or row.get("copy_score")),
                account_role="master",
                raw_payload=row,
            )
        )

    return DiscoverySourceResult(
        fetched=len(rows),
        skipped=skipped,
        skip_reasons=skip_reasons,
        candidates=candidates,
        metadata={"provider": "hyperdash", "url": url},
    )


async def fetch_hyperdash_graphql_source(
    source: str,
    *,
    url: str,
    limit: int,
) -> DiscoverySourceResult:
    if source in HYPERDASH_SYSTEM_GROUP_IDS:
        return await fetch_hyperdash_system_group_source(source, url=url, limit=limit)
    if source == "hyperdash_cohorts":
        return await fetch_hyperdash_pnl_cohort_source(source, url=url, limit=limit)
    raise UnknownDiscoverySourceError(f"Unknown Hyperdash source: {source}")


async def fetch_hyperdash_system_group_source(
    source: str,
    *,
    url: str,
    limit: int,
) -> DiscoverySourceResult:
    group_id = HYPERDASH_SYSTEM_GROUP_IDS[source]
    payload = await post_hyperdash_graphql(
        operation_name="GetSystemGroupTraders",
        query=HYPERDASH_SYSTEM_GROUP_QUERY,
        variables={"groupId": group_id},
        referer=url,
    )
    rows = extract_path(payload, ("data", "getSystemGroupTraders"))
    if not isinstance(rows, list):
        rows = []

    candidates: list[DiscoveryCandidate] = []
    skipped = 0
    skip_reasons: dict[str, int] = {}
    for rank, row in enumerate(rows[:limit], start=1):
        if not isinstance(row, dict):
            skipped += 1
            add_count(skip_reasons, "invalid_source_row")
            continue
        candidate = hyperdash_system_group_candidate(
            source=source,
            row=row,
            rank=rank,
            group_id=group_id,
        )
        if candidate is None:
            skipped += 1
            add_count(skip_reasons, "missing_or_invalid_address")
            continue
        candidates.append(candidate)

    return DiscoverySourceResult(
        fetched=min(len(rows), limit),
        skipped=skipped,
        skip_reasons=skip_reasons,
        candidates=candidates,
        metadata={
            "provider": "hyperdash",
            "url": url,
            "graphqlUrl": HYPERDASH_GRAPHQL_URL,
            "groupId": group_id,
            "sourceType": "system_group",
        },
    )


async def fetch_hyperdash_pnl_cohort_source(
    source: str,
    *,
    url: str,
    limit: int,
) -> DiscoverySourceResult:
    cohort_id = hyperdash_cohort_id_from_url(url) or HYPERDASH_DEFAULT_COHORT_ID
    rows: list[Any] = []
    total_count: int | None = None
    skipped = 0
    offset = 0

    while len(rows) < limit:
        page_limit = min(HYPERDASH_GRAPHQL_PAGE_SIZE, limit - len(rows))
        payload = await post_hyperdash_graphql(
            operation_name="GetPnlCohort",
            query=HYPERDASH_PNL_COHORT_QUERY,
            variables={
                "id": cohort_id,
                "limit": page_limit,
                "offset": offset,
                "sortBy": HYPERDASH_DEFAULT_COHORT_SORT,
            },
            referer=url,
        )
        top_traders = extract_path(payload, ("data", "analytics", "pnlCohort", "topTraders"))
        if not isinstance(top_traders, dict):
            break

        page_rows = top_traders.get("traders")
        if not isinstance(page_rows, list) or not page_rows:
            break

        rows.extend(page_rows)
        total_count = int_or_none(top_traders.get("totalCount")) or total_count
        offset += len(page_rows)
        if not top_traders.get("hasMore"):
            break

    candidates: list[DiscoveryCandidate] = []
    skip_reasons: dict[str, int] = {}
    for rank, row in enumerate(rows[:limit], start=1):
        if not isinstance(row, dict):
            skipped += 1
            add_count(skip_reasons, "invalid_source_row")
            continue
        candidate = hyperdash_pnl_cohort_candidate(
            source=source,
            row=row,
            rank=rank,
            cohort_id=cohort_id,
        )
        if candidate is None:
            skipped += 1
            add_count(skip_reasons, "missing_or_invalid_address")
            continue
        candidates.append(candidate)

    return DiscoverySourceResult(
        fetched=len(rows[:limit]),
        skipped=skipped,
        skip_reasons=skip_reasons,
        candidates=candidates,
        metadata={
            "provider": "hyperdash",
            "url": url,
            "graphqlUrl": HYPERDASH_GRAPHQL_URL,
            "cohortId": cohort_id,
            "totalCount": total_count,
            "sourceType": "pnl_cohort",
        },
    )


async def post_hyperdash_graphql(
    *,
    operation_name: str,
    query: str,
    variables: dict[str, Any],
    referer: str,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://hyperdash.com",
        "Referer": referer or "https://hyperdash.com/explore",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
    }
    body = {
        "operationName": operation_name,
        "query": query,
        "variables": variables,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(HYPERDASH_GRAPHQL_URL, json=body, headers=headers)
    if response.status_code >= 400:
        raise DiscoverySourceUnavailableError(
            f"Hyperdash GraphQL request failed with status {response.status_code}."
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise DiscoverySourceUnavailableError("Hyperdash GraphQL returned invalid JSON.") from exc
    if payload.get("errors"):
        raise DiscoverySourceUnavailableError(
            f"Hyperdash GraphQL returned errors: {payload.get('errors')}"
        )
    return payload


def hyperdash_system_group_candidate(
    *,
    source: str,
    row: dict[str, Any],
    rank: int,
    group_id: str,
) -> DiscoveryCandidate | None:
    address = extract_address(row)
    if address is None:
        return None
    account_value = decimal_or_none(first_present(row, ("perpsEquity", "accountValue", "equity")))
    source_pnl = decimal_or_none(first_present(row, ("pnl", "perpPnl")))
    label = string_or_none(row.get("displayName")) or string_or_none(row.get("label"))

    return DiscoveryCandidate(
        wallet_address=address,
        source=source,
        source_rank=rank,
        source_label=label or f"Hyperdash {group_id} #{rank}",
        source_cohort=group_id,
        account_value=account_value,
        source_pnl=source_pnl,
        source_roi=estimate_roi_percent(source_pnl, account_value),
        source_copy_score=decimal_or_none(row.get("copyScore")),
        account_role="master",
        raw_payload=compact_hyperdash_system_group_payload(row, group_id=group_id, rank=rank),
    )


def hyperdash_pnl_cohort_candidate(
    *,
    source: str,
    row: dict[str, Any],
    rank: int,
    cohort_id: str,
) -> DiscoveryCandidate | None:
    address = extract_address(row)
    if address is None:
        return None
    account_value = decimal_or_none(row.get("accountValue"))
    source_pnl = decimal_or_none(row.get("perpPnl"))
    label = string_or_none(row.get("displayName")) or string_or_none(row.get("label"))

    return DiscoveryCandidate(
        wallet_address=address,
        source=source,
        source_rank=rank,
        source_label=label or f"Hyperdash {cohort_id} #{rank}",
        source_cohort=cohort_id,
        account_value=account_value,
        source_pnl=source_pnl,
        source_roi=estimate_roi_percent(source_pnl, account_value),
        source_copy_score=decimal_or_none(row.get("copyScore")),
        account_role="master",
        raw_payload=compact_hyperdash_cohort_payload(row, cohort_id=cohort_id, rank=rank),
    )


async def upsert_discovery_candidates(
    session: AsyncSession,
    *,
    run_id: UUID,
    candidates: list[DiscoveryCandidate],
) -> DiscoveryUpsertResult:
    deduped = {candidate.wallet_address: candidate for candidate in candidates}
    duplicate_candidate_count = len(candidates) - len(deduped)
    skip_reasons: dict[str, int] = {}
    add_count(skip_reasons, "duplicate_in_source", duplicate_candidate_count)
    if not deduped:
        return DiscoveryUpsertResult(
            inserted=0,
            updated=0,
            skipped=duplicate_candidate_count,
            skip_reasons=skip_reasons,
        )

    addresses = list(deduped.keys())
    existing_candidate_result = await session.execute(
        select(DiscoveryWalletCandidate.wallet_address).where(
            DiscoveryWalletCandidate.wallet_address.in_(addresses),
        )
    )
    existing_candidate_addresses = set(existing_candidate_result.scalars().all())
    existing_pool_result = await session.execute(
        select(WatchedWallet.address).where(WatchedWallet.address.in_(addresses))
    )
    existing_pool_addresses = set(existing_pool_result.scalars().all())
    ignored_addresses = await load_ignored_wallet_addresses(session)
    address_set = set(addresses)
    already_pool = existing_pool_addresses & address_set
    already_candidate = (existing_candidate_addresses - existing_pool_addresses) & address_set
    ignored_candidates = ignored_addresses & address_set
    add_count(skip_reasons, "already_in_pool", len(already_pool))
    add_count(skip_reasons, "already_in_candidates", len(already_candidate))
    add_count(skip_reasons, "ignored_wallet", len(ignored_candidates))
    skipped_addresses = (
        existing_candidate_addresses
        | existing_pool_addresses
        | ignored_candidates
    )
    new_candidates = [
        candidate
        for address, candidate in deduped.items()
        if address not in skipped_addresses
    ]
    if not new_candidates:
        return DiscoveryUpsertResult(
            inserted=0,
            updated=0,
            skipped=sum(skip_reasons.values()),
            skip_reasons=skip_reasons,
        )
    skipped = sum(skip_reasons.values())

    records = [
        discovery_candidate_record(candidate, run_id=run_id)
        for candidate in new_candidates
    ]
    stmt = (
        insert(DiscoveryWalletCandidate)
        .values(records)
        .on_conflict_do_nothing(
            constraint="ux_discovery_candidates_source_wallet",
        )
        .returning(DiscoveryWalletCandidate.wallet_address)
    )
    result = await session.execute(stmt)
    inserted = len(result.scalars().all())

    insert_conflicts = len(new_candidates) - inserted
    add_count(skip_reasons, "insert_conflict", insert_conflicts)
    skipped += insert_conflicts
    updated = 0
    return DiscoveryUpsertResult(
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        skip_reasons=skip_reasons,
    )


async def list_discovery_candidates(
    session: AsyncSession,
    *,
    source: str | None = None,
    status: str | None = None,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> DiscoveryCandidateListResponse:
    filters = []
    if source:
        filters.append(DiscoveryWalletCandidate.source == source)
    if status:
        filters.append(DiscoveryWalletCandidate.status == status)
    if query:
        pattern = f"%{query.strip()}%"
        filters.append(
            or_(
                DiscoveryWalletCandidate.wallet_address.ilike(pattern),
                DiscoveryWalletCandidate.source_label.ilike(pattern),
                DiscoveryWalletCandidate.source_cohort.ilike(pattern),
            )
        )

    total = await session.scalar(
        select(func.count()).select_from(DiscoveryWalletCandidate).where(*filters)
    )
    result = await session.execute(
        select(DiscoveryWalletCandidate)
        .where(*filters)
        .order_by(
            DiscoveryWalletCandidate.last_seen_at.desc(),
            DiscoveryWalletCandidate.source.asc(),
            DiscoveryWalletCandidate.source_rank.asc().nulls_last(),
        )
        .limit(limit)
        .offset(offset)
    )
    return DiscoveryCandidateListResponse(
        items=list(result.scalars().all()),
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


async def list_discovery_runs(
    session: AsyncSession,
    *,
    source: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> DiscoveryImportRunListResponse:
    filters = []
    if source:
        filters.append(DiscoveryImportRun.source == source)
    if status:
        filters.append(DiscoveryImportRun.status == status)

    total = await session.scalar(
        select(func.count()).select_from(DiscoveryImportRun).where(*filters)
    )
    result = await session.execute(
        select(DiscoveryImportRun)
        .where(*filters)
        .order_by(DiscoveryImportRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return DiscoveryImportRunListResponse(
        items=list(result.scalars().all()),
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


async def backfill_discovery_candidates(
    session: AsyncSession,
    *,
    source: str | None,
    run_ids: list[UUID] | None = None,
    limit: int,
    retry_failed: bool,
    settings: Settings,
    progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> DiscoveryBackfillResponse:
    filters = [
        DiscoveryWalletCandidate.status == "accepted",
    ]
    if retry_failed:
        filters.append(DiscoveryWalletCandidate.backfill_status.in_(("not_started", "failed")))
    else:
        filters.append(DiscoveryWalletCandidate.backfill_status == "not_started")
    if source:
        filters.append(DiscoveryWalletCandidate.source == source)
    if run_ids is not None:
        if not run_ids:
            return DiscoveryBackfillResponse(
                scanned=0,
                backfilled=0,
                accepted=0,
                rejected=0,
                promoted=0,
                pool_inserted=0,
                pool_duplicate=0,
                failed=0,
                skipped=0,
                reject_reasons={},
                items=[],
            )
        filters.append(DiscoveryWalletCandidate.last_import_run_id.in_(run_ids))

    result = await session.execute(
        select(DiscoveryWalletCandidate)
        .where(*filters)
        .order_by(
            DiscoveryWalletCandidate.last_backfilled_at.asc().nulls_first(),
            DiscoveryWalletCandidate.last_seen_at.desc(),
            DiscoveryWalletCandidate.source_rank.asc().nulls_last(),
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    candidates = list(result.scalars().all())

    items: list[DiscoveryBackfillItem] = []
    reject_reasons: dict[str, int] = {}
    accepted = 0
    rejected = 0
    failed = 0
    backfilled = 0
    promoted = 0
    pool_inserted = 0
    pool_duplicate = 0

    async with HyperliquidClient(settings) as hyperliquid_client:
        for candidate in candidates:
            candidate_id = candidate.id
            candidate_error_item = backfill_error_item_from_candidate(candidate)
            if progress_callback is not None:
                await progress_callback(
                    {
                        "event": "candidate_started",
                        "walletAddress": candidate.wallet_address,
                        "label": f"Backfilling {short_wallet_address(candidate.wallet_address)}.",
                    }
                )
            candidate.backfill_status = "running"
            candidate.backfill_error = None
            await session.commit()

            try:
                import_result = await import_address_fills(
                    session=session,
                    address=candidate.wallet_address,
                    payload=WalletFillImportRequest(
                        days=settings.discovery_candidate_backfill_days,
                        max_pages=settings.discovery_candidate_backfill_max_pages,
                        target_fills=settings.discovery_candidate_backfill_target_fills,
                    ),
                    client=hyperliquid_client,
                )
                metrics = await load_candidate_trade_metrics(
                    session,
                    address=candidate.wallet_address,
                    days=settings.discovery_candidate_backfill_days,
                )
                decision = evaluate_candidate_trade_quality(metrics, settings=settings)
                update_candidate_backfill_state(
                    candidate,
                    metrics=metrics,
                    fetched=import_result.fetched,
                    inserted=import_result.inserted,
                    duplicate=import_result.duplicate,
                    decision=decision,
                )

                backfilled += 1
                pool_action: str | None = None
                if decision.status == "accepted":
                    accepted += 1
                    pool_action = await promote_backfilled_candidate_to_pool(session, candidate)
                    if pool_action in {"inserted", "existing_pool_wallet"}:
                        promoted += 1
                    if pool_action == "inserted":
                        pool_inserted += 1
                    elif pool_action == "existing_pool_wallet":
                        pool_duplicate += 1
                else:
                    rejected += 1
                    reason = decision.fail_reason or "unknown"
                    reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
                    await delete_rejected_candidate_data(session, candidate.wallet_address)
                await session.commit()
                items.append(
                    backfill_item_from_candidate(
                        candidate,
                        fetched=import_result.fetched,
                        inserted=import_result.inserted,
                        duplicate=import_result.duplicate,
                        pool_action=pool_action,
                    )
                )
                if progress_callback is not None:
                    await progress_callback(
                        {
                            "event": "candidate_finished",
                            "walletAddress": candidate.wallet_address,
                            "label": (
                                f"Finished {short_wallet_address(candidate.wallet_address)}: "
                                f"{import_result.inserted} new fills, "
                                f"{import_result.duplicate} duplicates."
                            ),
                        }
                    )
            except FillImportStorageLimitError as exc:
                await session.rollback()
                failed += 1
                await mark_candidate_backfill_failed(
                    session,
                    candidate_id=candidate_id,
                    error=str(exc) or exc.__class__.__name__,
                )
                failed_item = candidate_error_item(error=str(exc))
                items.append(failed_item)
                if progress_callback is not None:
                    await progress_callback(
                        {
                            "event": "candidate_failed",
                            "walletAddress": failed_item.wallet_address,
                            "label": "Backfill stopped because database storage is too low.",
                        }
                    )
                break
            except HyperliquidRateLimitError as exc:
                await session.rollback()
                failed += 1
                await mark_candidate_backfill_failed(
                    session,
                    candidate_id=candidate_id,
                    error=str(exc) or exc.__class__.__name__,
                )
                failed_item = candidate_error_item(error=str(exc))
                items.append(failed_item)
                if progress_callback is not None:
                    await progress_callback(
                        {
                            "event": "candidate_failed",
                            "walletAddress": failed_item.wallet_address,
                            "label": (
                                "Backfill paused because Hyperliquid rate limited "
                                "the request."
                            ),
                        }
                    )
                break
            except Exception as exc:
                await session.rollback()
                failed += 1
                await mark_candidate_backfill_failed(
                    session,
                    candidate_id=candidate_id,
                    error=str(exc) or exc.__class__.__name__,
                )
                failed_item = candidate_error_item(error=str(exc))
                items.append(failed_item)
                if progress_callback is not None:
                    await progress_callback(
                        {
                            "event": "candidate_failed",
                            "walletAddress": failed_item.wallet_address,
                            "label": (
                                f"Backfill failed for "
                                f"{short_wallet_address(failed_item.wallet_address)}."
                            ),
                        }
                    )

    return DiscoveryBackfillResponse(
        scanned=len(candidates),
        backfilled=backfilled,
        accepted=accepted,
        rejected=rejected,
        promoted=promoted,
        pool_inserted=pool_inserted,
        pool_duplicate=pool_duplicate,
        failed=failed,
        skipped=0,
        reject_reasons=reject_reasons,
        items=items,
    )


async def backfill_all_discovery_candidates(
    session: AsyncSession,
    *,
    run_ids: list[UUID],
    settings: Settings,
    progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> DiscoveryBackfillResponse:
    totals = DiscoveryBackfillResponse(
        scanned=0,
        backfilled=0,
        accepted=0,
        rejected=0,
        promoted=0,
        pool_inserted=0,
        pool_duplicate=0,
        failed=0,
        skipped=0,
        reject_reasons={},
        items=[],
    )
    batch_limit = settings.discovery_candidate_backfill_batch_size

    while True:
        batch = await backfill_discovery_candidates(
            session,
            source=None,
            run_ids=run_ids,
            limit=batch_limit,
            retry_failed=False,
            settings=settings,
            progress_callback=progress_callback,
        )
        totals.scanned += batch.scanned
        totals.backfilled += batch.backfilled
        totals.accepted += batch.accepted
        totals.rejected += batch.rejected
        totals.promoted += batch.promoted
        totals.pool_inserted += batch.pool_inserted
        totals.pool_duplicate += batch.pool_duplicate
        totals.failed += batch.failed
        totals.skipped += batch.skipped
        totals.items.extend(batch.items)
        for reason, count in batch.reject_reasons.items():
            totals.reject_reasons[reason] = totals.reject_reasons.get(reason, 0) + count

        if batch.scanned == 0:
            break
        if batch.failed > 0 and batch.backfilled == 0:
            break
        if batch.scanned < batch_limit:
            break

    return totals


async def promote_backfilled_candidate_to_pool(
    session: AsyncSession,
    candidate: DiscoveryWalletCandidate,
) -> str:
    stmt = (
        insert(WatchedWallet)
        .values([watched_wallet_record_from_candidate(candidate)])
        .on_conflict_do_nothing(index_elements=["address"])
        .returning(WatchedWallet.address)
    )
    result = await session.execute(stmt)
    inserted_address = result.scalar_one_or_none()
    pool_action = "inserted" if inserted_address else "existing_pool_wallet"
    if inserted_address is None:
        exists = await session.scalar(
            select(WatchedWallet.address).where(WatchedWallet.address == candidate.wallet_address)
        )
        if exists is None:
            return "insert_conflict"

    candidate.status = "promoted"
    candidate.fail_reason = None
    candidate.updated_at = datetime.now(UTC)
    return pool_action


async def delete_rejected_candidate_data(session: AsyncSession, wallet_address: str) -> int:
    exists = await session.scalar(
        select(WatchedWallet.address).where(WatchedWallet.address == wallet_address)
    )
    if exists is not None:
        return 0
    return await delete_wallet_data_rows(session, addresses=[wallet_address])


async def promote_discovery_candidates(
    session: AsyncSession,
    *,
    source: str | None,
    limit: int,
    require_backfill: bool,
) -> DiscoveryPromoteResponse:
    filters = [DiscoveryWalletCandidate.status == "accepted"]
    if source:
        filters.append(DiscoveryWalletCandidate.source == source)
    if require_backfill:
        filters.append(DiscoveryWalletCandidate.backfill_status == "succeeded")

    result = await session.execute(
        select(DiscoveryWalletCandidate)
        .where(*filters)
        .order_by(
            DiscoveryWalletCandidate.last_backfilled_at.desc().nulls_last(),
            DiscoveryWalletCandidate.last_seen_at.desc(),
            DiscoveryWalletCandidate.source_rank.asc().nulls_last(),
        )
        .limit(limit)
    )
    candidates = list(result.scalars().all())
    if not candidates:
        return DiscoveryPromoteResponse(
            scanned=0,
            promoted=0,
            inserted=0,
            duplicate=0,
            skipped=0,
            items=[],
        )

    addresses = [candidate.wallet_address for candidate in candidates]
    existing_result = await session.execute(
        select(WatchedWallet.address).where(WatchedWallet.address.in_(addresses))
    )
    existing_addresses = set(existing_result.scalars().all())

    records = [
        watched_wallet_record_from_candidate(candidate)
        for candidate in candidates
        if candidate.wallet_address not in existing_addresses
    ]
    inserted_addresses: set[str] = set()
    if records:
        stmt = (
            insert(WatchedWallet)
            .values(records)
            .on_conflict_do_nothing(index_elements=["address"])
            .returning(WatchedWallet.address)
        )
        insert_result = await session.execute(stmt)
        inserted_addresses = set(insert_result.scalars().all())

    promoted = 0
    duplicate = 0
    items: list[DiscoveryPromoteItem] = []
    now = datetime.now(UTC)

    for candidate in candidates:
        already_in_pool = candidate.wallet_address in existing_addresses
        was_inserted = candidate.wallet_address in inserted_addresses
        if already_in_pool or was_inserted:
            promoted += 1
            if already_in_pool:
                duplicate += 1
            candidate.status = "promoted"
            candidate.fail_reason = None
            candidate.updated_at = now
            items.append(
                DiscoveryPromoteItem(
                    wallet_address=candidate.wallet_address,
                    source=candidate.source,
                    action="existing_pool_wallet" if already_in_pool else "inserted",
                    label=wallet_label_from_candidate(candidate),
                    already_in_pool=already_in_pool,
                )
            )
        else:
            items.append(
                DiscoveryPromoteItem(
                    wallet_address=candidate.wallet_address,
                    source=candidate.source,
                    action="skipped",
                    label=wallet_label_from_candidate(candidate),
                    already_in_pool=False,
                    reason="insert_conflict",
                )
            )

    await session.commit()
    inserted = len(inserted_addresses)
    skipped = len(candidates) - promoted
    return DiscoveryPromoteResponse(
        scanned=len(candidates),
        promoted=promoted,
        inserted=inserted,
        duplicate=duplicate,
        skipped=skipped,
        items=items,
    )


async def promote_all_discovery_candidates(
    session: AsyncSession,
    *,
    source: str | None,
    limit: int,
    require_backfill: bool,
    max_batches: int,
) -> DiscoveryPromoteResponse:
    totals = DiscoveryPromoteResponse(
        scanned=0,
        promoted=0,
        inserted=0,
        duplicate=0,
        skipped=0,
        items=[],
    )

    for _ in range(max_batches):
        batch = await promote_discovery_candidates(
            session,
            source=source,
            limit=limit,
            require_backfill=require_backfill,
        )
        totals.scanned += batch.scanned
        totals.promoted += batch.promoted
        totals.inserted += batch.inserted
        totals.duplicate += batch.duplicate
        totals.skipped += batch.skipped
        totals.items.extend(batch.items)

        if batch.scanned == 0:
            break
        if batch.promoted <= 0:
            break
        if batch.scanned < limit:
            break

    return totals


def watched_wallet_record_from_candidate(candidate: DiscoveryWalletCandidate) -> dict[str, Any]:
    return {
        "address": candidate.wallet_address,
        "label": wallet_label_from_candidate(candidate),
        "enabled": True,
        "eligible": False,
        "copy_enabled": False,
        "polling_tier": "pool",
        "notes": wallet_notes_from_candidate(candidate),
    }


def wallet_label_from_candidate(candidate: DiscoveryWalletCandidate) -> str:
    if candidate.source_label:
        return candidate.source_label[:120]
    if candidate.source_rank is not None:
        return f"{candidate.source} #{candidate.source_rank}"[:120]
    return candidate.source[:120]


def wallet_notes_from_candidate(candidate: DiscoveryWalletCandidate) -> str:
    parts = [
        f"Promoted from discovery source {candidate.source}.",
    ]
    if candidate.source_rank is not None:
        parts.append(f"Source rank: {candidate.source_rank}.")
    if candidate.source_cohort:
        parts.append(f"Source cohort: {candidate.source_cohort}.")
    if candidate.account_role == "subaccount" and candidate.parent_address:
        parts.append(f"Parent master wallet: {candidate.parent_address}.")
    if candidate.subaccount_name:
        parts.append(f"Subaccount name: {candidate.subaccount_name}.")
    if candidate.source_pnl is not None:
        parts.append(f"Source PnL: {candidate.source_pnl}.")
    if candidate.source_roi is not None:
        parts.append(f"Source ROI: {candidate.source_roi}.")
    if candidate.source_copy_score is not None:
        parts.append(f"Source copy score: {candidate.source_copy_score}.")
    if candidate.closed_trade_count:
        parts.append(
            f"Discovery backfill: {candidate.closed_trade_count} closed trades, "
            f"net PnL {candidate.net_pnl_usd}."
        )
    return " ".join(parts)


async def load_candidate_trade_metrics(
    session: AsyncSession,
    *,
    address: str,
    days: int,
) -> CandidateTradeMetrics:
    now = datetime.now(UTC)
    window_start_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    start_24h_ms = int((now - timedelta(days=1)).timestamp() * 1000)
    start_7d_ms = int((now - timedelta(days=7)).timestamp() * 1000)
    fill_count = int(
        await session.scalar(
            select(func.count())
            .select_from(WalletFill)
            .where(
                WalletFill.wallet_address == address,
                WalletFill.timestamp_ms >= window_start_ms,
            )
        )
        or 0
    )
    trades_by_wallet = await reconstruct_wallet_trades(
        session,
        window_start_ms=window_start_ms,
        start_24h_ms=start_24h_ms,
        start_7d_ms=start_7d_ms,
        include_disabled=True,
        wallet_address=address,
    )
    trades = trades_by_wallet.get(address, ReconstructedWalletTrades(wallet_address=address))
    closed_trade_count = trades.closed_trade_count
    ignored_fill_count = trades.unmatched_close_fill_count + trades.preexisting_open_fill_count
    profit_factor = calculate_profit_factor(trades.gross_profit_usd, trades.gross_loss_usd)
    win_rate = (
        Decimal(trades.winning_trade_count) / Decimal(closed_trade_count)
        if closed_trade_count > 0
        else None
    )
    drawdown_base = max(trades.gross_profit_usd, abs(trades.net_pnl_usd), Decimal("1"))
    max_drawdown_pct = (
        trades.max_drawdown_usd / drawdown_base if closed_trade_count > 0 else None
    )
    return CandidateTradeMetrics(
        fill_count=fill_count,
        closed_trade_count=closed_trade_count,
        open_trade_count=trades.open_trade_count,
        ignored_fill_count=ignored_fill_count,
        net_pnl_usd=trades.net_pnl_usd,
        profit_factor=profit_factor,
        win_rate=win_rate,
        max_drawdown_pct=max_drawdown_pct,
        average_trade_notional_usd=trades.average_trade_notional_usd,
        last_trade_time_ms=trades.last_trade_time_ms,
    )


def update_candidate_backfill_state(
    candidate: DiscoveryWalletCandidate,
    *,
    metrics: CandidateTradeMetrics,
    fetched: int,
    inserted: int,
    duplicate: int,
    decision: PrefilterDecision,
) -> None:
    candidate.status = decision.status
    candidate.fail_reason = decision.fail_reason
    candidate.backfill_status = "succeeded"
    candidate.backfill_error = None
    candidate.last_backfilled_at = datetime.now(UTC)
    candidate.backfill_fetched_count = fetched
    candidate.backfill_inserted_count = inserted
    candidate.backfill_duplicate_count = duplicate
    candidate.fill_count = metrics.fill_count
    candidate.closed_trade_count = metrics.closed_trade_count
    candidate.open_trade_count = metrics.open_trade_count
    candidate.ignored_fill_count = metrics.ignored_fill_count
    candidate.net_pnl_usd = metrics.net_pnl_usd
    candidate.profit_factor = metrics.profit_factor
    candidate.win_rate = metrics.win_rate
    candidate.max_drawdown_pct = metrics.max_drawdown_pct
    candidate.average_trade_notional_usd = metrics.average_trade_notional_usd
    candidate.last_trade_time_ms = metrics.last_trade_time_ms
    candidate.updated_at = datetime.now(UTC)


async def mark_candidate_backfill_failed(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    error: str,
) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(DiscoveryWalletCandidate)
        .where(DiscoveryWalletCandidate.id == candidate_id)
        .values(
            backfill_status="failed",
            backfill_error=error,
            last_backfilled_at=now,
            updated_at=now,
        )
    )
    await session.commit()


def backfill_error_item_from_candidate(
    candidate: DiscoveryWalletCandidate,
):
    wallet_address = candidate.wallet_address
    source = candidate.source
    status = candidate.status
    fail_reason = candidate.fail_reason
    fill_count = candidate.fill_count
    closed_trade_count = candidate.closed_trade_count
    open_trade_count = candidate.open_trade_count
    ignored_fill_count = candidate.ignored_fill_count
    net_pnl_usd = candidate.net_pnl_usd
    profit_factor = candidate.profit_factor
    win_rate = candidate.win_rate
    max_drawdown_pct = candidate.max_drawdown_pct

    def build(*, error: str) -> DiscoveryBackfillItem:
        return DiscoveryBackfillItem(
            wallet_address=wallet_address,
            source=source,
            status=status,
            fail_reason=fail_reason,
            pool_action=None,
            fetched=0,
            inserted=0,
            duplicate=0,
            fill_count=fill_count,
            closed_trade_count=closed_trade_count,
            open_trade_count=open_trade_count,
            ignored_fill_count=ignored_fill_count,
            net_pnl_usd=net_pnl_usd,
            profit_factor=profit_factor,
            win_rate=win_rate,
            max_drawdown_pct=max_drawdown_pct,
            error=error,
        )

    return build


def short_wallet_address(address: str) -> str:
    if len(address) <= 14:
        return address
    return f"{address[:8]}...{address[-6:]}"


def backfill_item_from_candidate(
    candidate: DiscoveryWalletCandidate,
    *,
    fetched: int = 0,
    inserted: int = 0,
    duplicate: int = 0,
    pool_action: str | None = None,
    error: str | None = None,
) -> DiscoveryBackfillItem:
    return DiscoveryBackfillItem(
        wallet_address=candidate.wallet_address,
        source=candidate.source,
        status=candidate.status,
        fail_reason=candidate.fail_reason,
        pool_action=pool_action,
        fetched=fetched,
        inserted=inserted,
        duplicate=duplicate,
        fill_count=candidate.fill_count,
        closed_trade_count=candidate.closed_trade_count,
        open_trade_count=candidate.open_trade_count,
        ignored_fill_count=candidate.ignored_fill_count,
        net_pnl_usd=candidate.net_pnl_usd,
        profit_factor=candidate.profit_factor,
        win_rate=candidate.win_rate,
        max_drawdown_pct=candidate.max_drawdown_pct,
        error=error,
    )


def evaluate_candidate_trade_quality(
    metrics: CandidateTradeMetrics,
    *,
    settings: Settings,
) -> PrefilterDecision:
    if metrics.fill_count <= 0:
        return PrefilterDecision(status="rejected", fail_reason="no_perp_fills")
    if metrics.fill_count < settings.discovery_quality_min_fills:
        return PrefilterDecision(status="rejected", fail_reason="too_few_fills")
    if metrics.closed_trade_count < settings.discovery_quality_min_closed_trades:
        return PrefilterDecision(status="rejected", fail_reason="too_few_closed_trades")
    if settings.discovery_quality_require_positive_net_pnl and metrics.net_pnl_usd <= Decimal("0"):
        return PrefilterDecision(status="rejected", fail_reason="net_pnl_not_positive")
    if (
        settings.discovery_quality_min_profit_factor is not None
        and (
            metrics.profit_factor is None
            or metrics.profit_factor < settings.discovery_quality_min_profit_factor
        )
    ):
        return PrefilterDecision(status="rejected", fail_reason="profit_factor_below_min")
    if (
        settings.discovery_quality_min_win_rate is not None
        and (
            metrics.win_rate is None
            or metrics.win_rate < settings.discovery_quality_min_win_rate
        )
    ):
        return PrefilterDecision(status="rejected", fail_reason="win_rate_below_min")
    if (
        settings.discovery_quality_max_drawdown_pct is not None
        and metrics.max_drawdown_pct is not None
        and metrics.max_drawdown_pct > settings.discovery_quality_max_drawdown_pct
    ):
        return PrefilterDecision(status="rejected", fail_reason="max_drawdown_too_high")

    ignored_ratio = (
        Decimal(metrics.ignored_fill_count) / Decimal(metrics.fill_count)
        if metrics.fill_count > 0
        else Decimal("0")
    )
    if (
        settings.discovery_quality_max_ignored_fill_ratio is not None
        and ignored_ratio > settings.discovery_quality_max_ignored_fill_ratio
    ):
        return PrefilterDecision(status="rejected", fail_reason="too_many_ignored_fills")
    if (
        settings.discovery_quality_min_average_trade_notional_usd is not None
        and metrics.average_trade_notional_usd
        < settings.discovery_quality_min_average_trade_notional_usd
    ):
        return PrefilterDecision(status="rejected", fail_reason="avg_trade_notional_too_small")
    if (
        settings.discovery_quality_max_average_trade_notional_usd is not None
        and metrics.average_trade_notional_usd
        > settings.discovery_quality_max_average_trade_notional_usd
    ):
        return PrefilterDecision(status="rejected", fail_reason="avg_trade_notional_too_large")
    return PrefilterDecision(status="accepted")


def calculate_profit_factor(
    gross_profit_usd: Decimal,
    gross_loss_usd: Decimal,
) -> Decimal | None:
    if gross_profit_usd <= Decimal("0") and gross_loss_usd <= Decimal("0"):
        return None
    if gross_loss_usd <= Decimal("0"):
        return Decimal("999") if gross_profit_usd > Decimal("0") else None
    return gross_profit_usd / gross_loss_usd


async def prefilter_discovery_candidates(
    session: AsyncSession,
    *,
    settings: Settings,
    source: str | None = None,
    status: str | None = None,
    run_ids: list[UUID] | None = None,
    limit: int = 500,
    commit: bool,
) -> DiscoveryPrefilterResponse:
    if run_ids is not None and not run_ids:
        return DiscoveryPrefilterResponse(
            evaluated=0,
            accepted=0,
            rejected=0,
            unchanged=0,
            reject_reasons={},
            candidates=[],
        )

    filters = [
        DiscoveryWalletCandidate.status.not_in(("promoted", "ignored")),
    ]
    if source:
        filters.append(DiscoveryWalletCandidate.source == source)
    if status:
        filters.append(DiscoveryWalletCandidate.status == status)
    else:
        filters.append(DiscoveryWalletCandidate.status.in_(("discovered", "accepted", "rejected")))
    if run_ids is not None:
        filters.append(DiscoveryWalletCandidate.last_import_run_id.in_(run_ids))

    result = await session.execute(
        select(DiscoveryWalletCandidate)
        .where(*filters)
        .order_by(
            DiscoveryWalletCandidate.last_seen_at.desc(),
            DiscoveryWalletCandidate.source.asc(),
            DiscoveryWalletCandidate.source_rank.asc().nulls_last(),
        )
        .limit(limit)
    )
    candidates = list(result.scalars().all())

    accepted = 0
    rejected = 0
    unchanged = 0
    reject_reasons: dict[str, int] = {}

    for candidate in candidates:
        previous_status = candidate.status
        previous_reason = candidate.fail_reason
        decision = evaluate_discovery_candidate(candidate, settings=settings)
        candidate.status = decision.status
        candidate.fail_reason = decision.fail_reason
        candidate.updated_at = datetime.now(UTC)

        if decision.status == "accepted":
            accepted += 1
        elif decision.status == "rejected":
            rejected += 1
            reason = decision.fail_reason or "unknown"
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

        if previous_status == decision.status and previous_reason == decision.fail_reason:
            unchanged += 1

    if commit:
        await session.commit()
    else:
        await session.flush()

    return DiscoveryPrefilterResponse(
        evaluated=len(candidates),
        accepted=accepted,
        rejected=rejected,
        unchanged=unchanged,
        reject_reasons=reject_reasons,
        candidates=candidates,
    )


def evaluate_discovery_candidate(
    candidate: DiscoveryWalletCandidate,
    *,
    settings: Settings,
) -> PrefilterDecision:
    rank_decision = evaluate_optional_max_int(
        candidate.source_rank,
        settings.discovery_prefilter_max_source_rank,
        missing_reason="missing_source_rank",
        high_reason="source_rank_too_low",
        accept_if_missing=settings.discovery_prefilter_accept_if_metrics_missing,
    )
    if rank_decision is not None:
        return rank_decision

    pnl_decision = evaluate_source_pnl(candidate.source_pnl, settings=settings)
    if pnl_decision is not None:
        return pnl_decision

    roi_decision = evaluate_optional_min_decimal(
        candidate.source_roi,
        settings.discovery_prefilter_min_source_roi,
        missing_reason="missing_source_roi",
        low_reason="source_roi_below_min",
        accept_if_missing=settings.discovery_prefilter_accept_if_metrics_missing,
    )
    if roi_decision is not None:
        return roi_decision

    min_account_decision = evaluate_optional_min_decimal(
        candidate.account_value,
        settings.discovery_prefilter_min_account_value_usd,
        missing_reason="missing_account_value",
        low_reason="account_value_too_small",
        accept_if_missing=settings.discovery_prefilter_accept_if_metrics_missing,
    )
    if min_account_decision is not None:
        return min_account_decision

    max_account_decision = evaluate_optional_max_decimal(
        candidate.account_value,
        settings.discovery_prefilter_max_account_value_usd,
        missing_reason="missing_account_value",
        high_reason="account_value_too_large",
        accept_if_missing=settings.discovery_prefilter_accept_if_metrics_missing,
    )
    if max_account_decision is not None:
        return max_account_decision

    copy_score_decision = evaluate_optional_min_decimal(
        candidate.source_copy_score,
        settings.discovery_prefilter_min_copy_score,
        missing_reason="missing_copy_score",
        low_reason="copy_score_below_min",
        accept_if_missing=settings.discovery_prefilter_accept_if_metrics_missing,
    )
    if copy_score_decision is not None:
        return copy_score_decision

    return PrefilterDecision(status="accepted")


def evaluate_source_pnl(value: Decimal | None, *, settings: Settings) -> PrefilterDecision | None:
    if value is None:
        if settings.discovery_prefilter_reject_missing_source_pnl:
            return PrefilterDecision(status="rejected", fail_reason="missing_source_pnl")
        if (
            not settings.discovery_prefilter_accept_if_metrics_missing
            and (
                settings.discovery_prefilter_require_positive_source_pnl
                or settings.discovery_prefilter_min_source_pnl_usd is not None
            )
        ):
            return PrefilterDecision(status="rejected", fail_reason="missing_source_pnl")
        return None

    if settings.discovery_prefilter_require_positive_source_pnl and value <= Decimal("0"):
        return PrefilterDecision(status="rejected", fail_reason="source_pnl_not_positive")
    if (
        settings.discovery_prefilter_min_source_pnl_usd is not None
        and value < settings.discovery_prefilter_min_source_pnl_usd
    ):
        return PrefilterDecision(status="rejected", fail_reason="source_pnl_below_min")
    return None


def evaluate_optional_min_decimal(
    value: Decimal | None,
    minimum: Decimal | None,
    *,
    missing_reason: str,
    low_reason: str,
    accept_if_missing: bool,
) -> PrefilterDecision | None:
    if minimum is None:
        return None
    if value is None:
        if accept_if_missing:
            return None
        return PrefilterDecision(status="rejected", fail_reason=missing_reason)
    if value < minimum:
        return PrefilterDecision(status="rejected", fail_reason=low_reason)
    return None


def evaluate_optional_max_decimal(
    value: Decimal | None,
    maximum: Decimal | None,
    *,
    missing_reason: str,
    high_reason: str,
    accept_if_missing: bool,
) -> PrefilterDecision | None:
    if maximum is None:
        return None
    if value is None:
        if accept_if_missing:
            return None
        return PrefilterDecision(status="rejected", fail_reason=missing_reason)
    if value > maximum:
        return PrefilterDecision(status="rejected", fail_reason=high_reason)
    return None


def evaluate_optional_max_int(
    value: int | None,
    maximum: int | None,
    *,
    missing_reason: str,
    high_reason: str,
    accept_if_missing: bool,
) -> PrefilterDecision | None:
    if maximum is None:
        return None
    if value is None:
        if accept_if_missing:
            return None
        return PrefilterDecision(status="rejected", fail_reason=missing_reason)
    if value > maximum:
        return PrefilterDecision(status="rejected", fail_reason=high_reason)
    return None


def normalize_requested_sources(sources: list[str]) -> list[str]:
    normalized = []
    for source in sources:
        source_key = source.strip()
        if not source_key:
            continue
        if source_key not in KNOWN_DISCOVERY_SOURCES:
            raise UnknownDiscoverySourceError(f"Unknown discovery source: {source_key}")
        if source_key not in normalized:
            normalized.append(source_key)
    if not normalized:
        raise UnknownDiscoverySourceError("At least one discovery source is required.")
    return normalized


def discovery_candidate_record(candidate: DiscoveryCandidate, *, run_id: UUID) -> dict[str, Any]:
    return {
        "wallet_address": candidate.wallet_address,
        "source": candidate.source,
        "source_rank": candidate.source_rank,
        "source_label": candidate.source_label,
        "source_cohort": candidate.source_cohort,
        "source_account_value_usd": candidate.account_value,
        "source_pnl_usd": candidate.source_pnl,
        "source_roi_pct": candidate.source_roi,
        "source_copy_score": candidate.source_copy_score,
        "account_role": candidate.account_role,
        "parent_address": candidate.parent_address,
        "subaccount_name": candidate.subaccount_name,
        "status": "discovered",
        "fail_reason": None,
        "last_import_run_id": run_id,
        "raw_payload": candidate.raw_payload,
    }


def compact_leaderboard_payload(row: dict[str, Any], *, window: str) -> dict[str, Any]:
    return {
        "ethAddress": row.get("ethAddress"),
        "displayName": row.get("displayName"),
        "accountValue": row.get("accountValue"),
        "window": window,
        "windowPerformance": get_window(row, window),
        "monthPerformance": get_window(row, "month"),
        "allTimePerformance": get_window(row, "allTime"),
    }


def extract_hyperdash_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "traders", "wallets", "rows", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = extract_hyperdash_rows(value)
            if nested:
                return nested
    return []


def should_use_hyperdash_graphql(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host == "api.hyperdash.com" and parsed.path.rstrip("/") == "/graphql":
        return True
    return host in {"hyperdash.com", "www.hyperdash.com"} and parsed.path.startswith("/explore")


def hyperdash_cohort_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    try:
        cohorts_index = parts.index("cohorts")
    except ValueError:
        return None
    if cohorts_index + 1 >= len(parts):
        return None
    return parts[cohorts_index + 1]


def extract_path(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = payload
    for part in path:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def first_present(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def estimate_roi_percent(
    pnl: Decimal | None,
    account_value: Decimal | None,
) -> Decimal | None:
    if pnl is None or account_value is None:
        return None
    denominator = account_value - pnl
    if denominator <= Decimal("0"):
        return None
    return (pnl / denominator) * Decimal("100")


def compact_hyperdash_system_group_payload(
    row: dict[str, Any],
    *,
    group_id: str,
    rank: int,
) -> dict[str, Any]:
    return {
        "address": row.get("address"),
        "label": row.get("label"),
        "displayName": row.get("displayName"),
        "lastTradeAt": row.get("lastTradeAt"),
        "lastFillAt": row.get("lastFillAt"),
        "pnl": row.get("pnl"),
        "perpsEquity": row.get("perpsEquity"),
        "winrate": row.get("winrate"),
        "totalTrades": row.get("totalTrades"),
        "totalWinningTrades": row.get("totalWinningTrades"),
        "totalLosingTrades": row.get("totalLosingTrades"),
        "sharpe": row.get("sharpe"),
        "drawdown": row.get("drawdown"),
        "copyScore": row.get("copyScore"),
        "tag": row.get("tag"),
        "topAssets": row.get("topAssets"),
        "sourceGroupId": group_id,
        "sourceRank": rank,
    }


def compact_hyperdash_cohort_payload(
    row: dict[str, Any],
    *,
    cohort_id: str,
    rank: int,
) -> dict[str, Any]:
    return {
        "address": row.get("address"),
        "accountValue": row.get("accountValue"),
        "perpPnl": row.get("perpPnl"),
        "copyScore": row.get("copyScore"),
        "displayName": row.get("displayName"),
        "tag": row.get("tag"),
        "label": row.get("label"),
        "verified": row.get("verified"),
        "totalNotional": row.get("totalNotional"),
        "longNotional": row.get("longNotional"),
        "shortNotional": row.get("shortNotional"),
        "lastTradeAt": row.get("lastTradeAt"),
        "positions": row.get("positions"),
        "sourceCohortId": cohort_id,
        "sourceRank": rank,
    }


def extract_address(row: dict[str, Any]) -> str | None:
    for key in ("address", "walletAddress", "wallet_address", "ethAddress", "user", "account"):
        value = row.get(key)
        if not isinstance(value, str):
            continue
        try:
            return normalize_wallet_address(value)
        except ValueError:
            continue
    return None


def hyperdash_url_for_source(source: str, settings: Settings) -> str | None:
    setting_name = HYPERDASH_URL_SETTINGS.get(source)
    if not setting_name:
        return None
    value = getattr(settings, setting_name)
    return value if isinstance(value, str) and value.strip() else None


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip().replace("%", "").replace(",", "")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def window_label(window: str) -> str:
    labels = {
        "day": "1D",
        "week": "7D",
        "month": "30D",
        "allTime": "all-time",
    }
    return labels.get(window, window)
