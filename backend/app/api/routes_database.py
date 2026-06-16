from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.schemas.database import DatabaseStatsResponse, FillRawJsonCompactResponse
from app.services.database_stats_service import get_database_stats
from app.services.fill_compaction_service import compact_wallet_fill_raw_json

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
