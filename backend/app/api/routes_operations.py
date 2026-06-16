from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.schemas.operation import OperationStatusListResponse
from app.services.operation_status_service import list_operation_statuses

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/status", response_model=OperationStatusListResponse)
async def list_operation_statuses_route(
    session: Annotated[AsyncSession, Depends(db_session)],
) -> OperationStatusListResponse:
    return await list_operation_statuses(session)
