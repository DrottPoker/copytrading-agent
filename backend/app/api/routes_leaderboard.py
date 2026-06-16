from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.integrations.hyperliquid_leaderboard_client import HyperliquidLeaderboardError
from app.schemas.leaderboard import LeaderboardImportResponse
from app.services.leaderboard_import_service import import_top_leaderboard_wallets

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.post("/import", response_model=LeaderboardImportResponse)
async def import_leaderboard_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> LeaderboardImportResponse:
    try:
        return await import_top_leaderboard_wallets(
            session,
            limit=limit or settings.leaderboard_import_limit,
            settings=settings,
        )
    except HyperliquidLeaderboardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
