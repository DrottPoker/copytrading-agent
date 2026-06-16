from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.schemas.score import (
    WalletScoreDetailResponse,
    WalletScoreListResponse,
    WalletScoreRunResponse,
)
from app.services.wallet_score_service import (
    WalletScoreDetailNotFoundError,
    get_wallet_score_detail,
    list_wallet_scores,
    recalculate_wallet_scores,
)

router = APIRouter(prefix="/scores", tags=["scores"])


@router.get("", response_model=WalletScoreListResponse)
async def list_scores_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WalletScoreListResponse:
    return await list_wallet_scores(session, limit=limit, offset=offset)


@router.get("/{address}/detail", response_model=WalletScoreDetailResponse)
async def get_wallet_score_detail_route(
    address: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WalletScoreDetailResponse:
    try:
        return await get_wallet_score_detail(session, address=address, settings=settings)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except WalletScoreDetailNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found.",
        ) from exc


@router.post("/recalculate", response_model=WalletScoreRunResponse)
async def recalculate_scores_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    include_disabled: Annotated[bool, Query()] = False,
) -> WalletScoreRunResponse:
    return await recalculate_wallet_scores(
        session,
        settings=settings,
        include_disabled=include_disabled,
    )
