import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.schemas.discovery import (
    DiscoveryBackfillResponse,
    DiscoveryCandidateListResponse,
    DiscoveryImportResponse,
    DiscoveryImportRunListResponse,
    DiscoveryPrefilterResponse,
    DiscoveryPromoteResponse,
    DiscoverySourceListResponse,
)
from app.schemas.operation import OperationStatusRead
from app.services.discovery_service import (
    UnknownDiscoverySourceError,
    list_discovery_candidates,
    list_discovery_runs,
    list_discovery_sources,
    run_discovery_candidate_backfill,
    run_discovery_candidate_promotion,
    run_discovery_import,
    run_discovery_prefilter,
)
from app.services.operation_status_service import (
    OperationCanceledError,
    get_operation_status,
    mark_operation_started,
    new_operation_run_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.get("/sources", response_model=DiscoverySourceListResponse)
async def list_discovery_sources_route(
    settings: Annotated[Settings, Depends(get_settings)],
) -> DiscoverySourceListResponse:
    return await list_discovery_sources(settings=settings)


@router.post("/import", response_model=DiscoveryImportResponse)
async def run_discovery_import_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    sources: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    run_pipeline: Annotated[bool, Query()] = True,
    include_candidates: Annotated[bool, Query()] = True,
    include_backfill_items: Annotated[bool, Query()] = True,
) -> DiscoveryImportResponse:
    try:
        response = await run_discovery_import(
            session,
            sources=sources,
            limit=limit,
            run_pipeline=run_pipeline,
            settings=settings,
        )
        if not include_candidates:
            response.candidates = []
            if response.prefilter is not None:
                response.prefilter.candidates = []
        if not include_backfill_items and response.backfill is not None:
            response.backfill.items = []
        return response
    except OperationCanceledError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except UnknownDiscoverySourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/import/start", response_model=OperationStatusRead)
async def start_discovery_import_route(
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    sources: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    run_pipeline: Annotated[bool, Query()] = True,
) -> OperationStatusRead:
    current_status = await get_operation_status(session, "discovery_import")
    if current_status.status == "running":
        return current_status

    operation_run_id = new_operation_run_id()
    payload = {
        "runId": operation_run_id,
        "sources": sources or settings.discovery_default_sources,
        "limit": limit or settings.discovery_import_limit,
        "runPipeline": run_pipeline,
        "stage": "queued",
        "stageLabel": "Queued",
        "stageDetail": "Discovery pipeline is waiting for the backend worker task to start.",
        "progressPercent": 0,
        "progressCurrent": 0,
        "progressTotal": 100,
    }
    await mark_operation_started(session, key="discovery_import", payload=payload)
    background_tasks.add_task(
        run_discovery_import_background,
        sources,
        limit,
        run_pipeline,
        settings,
        operation_run_id,
    )
    return await get_operation_status(session, "discovery_import")


async def run_discovery_import_background(
    sources: list[str] | None,
    limit: int | None,
    run_pipeline: bool,
    settings: Settings,
    operation_run_id: str,
) -> None:
    sessionmaker = get_sessionmaker(settings)
    if sessionmaker is None:
        logger.error("cannot start discovery import background task without database_url")
        return
    async with sessionmaker() as session:
        try:
            await run_discovery_import(
                session,
                sources=sources,
                limit=limit,
                run_pipeline=run_pipeline,
                settings=settings,
                operation_run_id=operation_run_id,
            )
        except OperationCanceledError:
            logger.info("discovery import background task canceled")
        except Exception:
            logger.exception("discovery import background task failed")


@router.get("/candidates", response_model=DiscoveryCandidateListResponse)
async def list_discovery_candidates_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    source: Annotated[str | None, Query()] = None,
    candidate_status: Annotated[str | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DiscoveryCandidateListResponse:
    return await list_discovery_candidates(
        session,
        source=source,
        status=candidate_status,
        query=q,
        limit=limit,
        offset=offset,
    )


@router.post("/prefilter", response_model=DiscoveryPrefilterResponse)
async def run_discovery_prefilter_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    source: Annotated[str | None, Query()] = None,
    candidate_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    include_candidates: Annotated[bool, Query()] = True,
) -> DiscoveryPrefilterResponse:
    response = await run_discovery_prefilter(
        session,
        source=source,
        status=candidate_status,
        limit=limit,
        settings=settings,
    )
    if not include_candidates:
        response.candidates = []
    return response


@router.post("/backfill", response_model=DiscoveryBackfillResponse)
async def run_discovery_backfill_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    source: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    retry_failed: Annotated[bool, Query()] = False,
    include_items: Annotated[bool, Query()] = True,
) -> DiscoveryBackfillResponse:
    response = await run_discovery_candidate_backfill(
        session,
        source=source,
        limit=limit,
        retry_failed=retry_failed,
        settings=settings,
    )
    if not include_items:
        response.items = []
    return response


@router.post("/promote", response_model=DiscoveryPromoteResponse)
async def run_discovery_promote_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    source: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=250)] = None,
    include_unbackfilled: Annotated[bool, Query()] = False,
    run_all: Annotated[bool, Query()] = False,
    max_batches: Annotated[int, Query(ge=1, le=1000)] = 1,
    include_items: Annotated[bool, Query()] = True,
) -> DiscoveryPromoteResponse:
    response = await run_discovery_candidate_promotion(
        session,
        source=source,
        limit=limit,
        include_unbackfilled=include_unbackfilled,
        run_all=run_all,
        max_batches=max_batches,
        settings=settings,
    )
    if not include_items:
        response.items = []
    return response


@router.get("/runs", response_model=DiscoveryImportRunListResponse)
async def list_discovery_runs_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    source: Annotated[str | None, Query()] = None,
    run_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DiscoveryImportRunListResponse:
    return await list_discovery_runs(
        session,
        source=source,
        status=run_status,
        limit=limit,
        offset=offset,
    )
