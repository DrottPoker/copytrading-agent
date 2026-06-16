from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.schemas.paper_trading import PaperTradingSummaryResponse
from app.services.paper_trading_service import get_paper_trading_summary

router = APIRouter(prefix="/paper-trading", tags=["paper-trading"])


@router.get("", response_model=PaperTradingSummaryResponse)
async def get_paper_trading_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PaperTradingSummaryResponse:
    return await get_paper_trading_summary(session, settings=settings)
