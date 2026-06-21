import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.schemas.operation import OperationStatusRead
from app.schemas.score import (
    WalletScoreDetailResponse,
    WalletScoreListResponse,
    WalletScoreRunResponse,
)
from app.services.operation_status_service import (
    get_operation_status,
    mark_operation_failed,
    mark_operation_started,
)
from app.services.wallet_score_service import (
    WalletScoreDetailNotFoundError,
    get_wallet_score_detail,
    list_wallet_scores,
    recalculate_wallet_scores,
)

router = APIRouter(prefix="/scores", tags=["scores"])
logger = logging.getLogger(__name__)


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


@router.post("/recalculate/start", response_model=OperationStatusRead)
async def start_recalculate_scores_route(
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    include_disabled: Annotated[bool, Query()] = False,
) -> OperationStatusRead:
    current_status = await get_operation_status(session, "wallet_scoring")
    if current_status.status == "running":
        return current_status

    payload = {
        "windowDays": settings.scoring_window_days,
        "includeDisabled": include_disabled,
        "stage": "queued",
        "stageLabel": "Queued",
        "stageDetail": "Wallet scoring is waiting for the backend task to start.",
        "progressPercent": 0,
    }
    await mark_operation_started(session, key="wallet_scoring", payload=payload)
    background_tasks.add_task(
        recalculate_scores_background,
        include_disabled,
        settings,
    )
    return await get_operation_status(session, "wallet_scoring")


async def recalculate_scores_background(
    include_disabled: bool,
    settings: Settings,
) -> None:
    sessionmaker = get_sessionmaker(settings)
    if sessionmaker is None:
        logger.error("cannot start wallet scoring background task without database_url")
        return
    async with sessionmaker() as session:
        try:
            await recalculate_wallet_scores(
                session,
                settings=settings,
                include_disabled=include_disabled,
            )
        except Exception as exc:
            await session.rollback()
            await mark_operation_failed(
                session,
                key="wallet_scoring",
                error=str(exc) or exc.__class__.__name__,
                payload={
                    "windowDays": settings.scoring_window_days,
                    "includeDisabled": include_disabled,
                    "stage": "failed",
                    "stageLabel": "Failed",
                    "stageDetail": str(exc) or exc.__class__.__name__,
                },
            )
            logger.exception("wallet scoring background task failed")
