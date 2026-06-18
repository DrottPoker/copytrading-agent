from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.schemas.database import (
    DatabaseStatsResponse,
    FillRawJsonCompactResponse,
    FillRetentionCleanupResponse,
)
from app.services.database_stats_service import get_database_stats
from app.services.fill_compaction_service import compact_wallet_fill_raw_json
from app.services.fill_retention_service import cleanup_wallet_fill_retention

router = APIRouter(prefix="/database", tags=["database"])


@router.get("/stats", response_model=DatabaseStatsResponse)
async def get_database_stats_route(
    session: Annotated[AsyncSession, Depends(db_session)],
) -> DatabaseStatsResponse:
    return await get_database_stats(session)


@router.post("/fills/compact-raw-json", response_model=FillRawJsonCompactResponse)
async def compact_fill_raw_json_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    dry_run: Annotated[bool, Query()] = True,
    batch_size: Annotated[int, Query(ge=100, le=25000)] = 5000,
    max_rows: Annotated[int, Query(ge=100, le=250000)] = 50000,
) -> FillRawJsonCompactResponse:
    return await compact_wallet_fill_raw_json(
        session,
        dry_run=dry_run,
        batch_size=batch_size,
        max_rows=max_rows,
        settings=settings,
    )


@router.post("/fills/retention-cleanup", response_model=FillRetentionCleanupResponse)
async def cleanup_fill_retention_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    dry_run: Annotated[bool, Query()] = True,
    retention_days: Annotated[int | None, Query(ge=61, le=730)] = None,
    batch_size: Annotated[int | None, Query(ge=100, le=25000)] = None,
    max_rows: Annotated[int | None, Query(ge=100, le=250000)] = None,
    protect_top_score_wallets: Annotated[int | None, Query(ge=0, le=1000)] = None,
) -> FillRetentionCleanupResponse:
    return await cleanup_wallet_fill_retention(
        session,
        dry_run=dry_run,
        retention_days=retention_days or settings.fill_retention_days,
        batch_size=batch_size or settings.fill_retention_batch_size,
        max_rows=max_rows or settings.fill_retention_max_rows,
        protect_top_score_wallets=(
            protect_top_score_wallets
            if protect_top_score_wallets is not None
            else settings.fill_retention_protect_top_score_wallets
        ),
    )
