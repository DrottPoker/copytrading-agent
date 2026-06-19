from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.schemas.ops import OpsHealthResponse
from app.services.ops_monitoring_service import get_ops_health

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/health", response_model=OpsHealthResponse)
async def get_ops_health_route(
    settings: Annotated[Settings, Depends(get_settings)],
) -> OpsHealthResponse:
    return await get_ops_health(settings=settings)
