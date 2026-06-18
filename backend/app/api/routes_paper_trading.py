from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.paper_trading import PaperTradingSummaryResponse
from app.services.paper_trading_service import (
    PaperPositionCloseError,
    close_paper_position_manually,
    get_paper_trading_summary,
)

router = APIRouter(prefix="/paper-trading", tags=["paper-trading"])


@router.get("", response_model=PaperTradingSummaryResponse)
async def get_paper_trading_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PaperTradingSummaryResponse:
    return await get_paper_trading_summary(session, settings=settings)


@router.post("/positions/{position_id}/close", response_model=PaperTradingSummaryResponse)
async def close_paper_position_route(
    position_id: UUID,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PaperTradingSummaryResponse:
    async with HyperliquidClient(settings) as client:
        try:
            await close_paper_position_manually(
                session,
                position_id=position_id,
                settings=settings,
                client=client,
            )
            await session.commit()
        except PaperPositionCloseError as exc:
            await session.rollback()
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        return await get_paper_trading_summary(session, settings=settings, client=client)
