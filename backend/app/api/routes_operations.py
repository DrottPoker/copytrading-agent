from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.schemas.operation import OperationStatusListResponse, OperationStatusRead
from app.services.operation_status_service import (
    OperationNotRunningError,
    list_operation_statuses,
    request_operation_cancellation,
)

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/status", response_model=OperationStatusListResponse)
async def list_operation_statuses_route(
    session: Annotated[AsyncSession, Depends(db_session)],
) -> OperationStatusListResponse:
    return await list_operation_statuses(session)


@router.post("/{key}/cancel", response_model=OperationStatusRead)
async def cancel_operation_route(
    key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> OperationStatusRead:
    try:
        return await request_operation_cancellation(session, key=key)
    except OperationNotRunningError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
