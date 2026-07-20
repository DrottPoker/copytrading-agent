import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.config import Settings, get_settings
from app.db.session import check_postgres
from app.integrations.redis_client import check_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    postgres_status, redis_status = await asyncio.gather(
        check_postgres(settings),
        check_redis(settings),
    )
    service_status = dependency_status(postgres_status, redis_status)
    if postgres_status.get("status") != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": service_status,
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "mode": settings.system_mode,
        "paperTradingEnabled": settings.paper_trading_enabled,
        "liveTradingEnabled": settings.live_trading_enabled,
        "workerRunInApiProcess": settings.worker_run_in_api_process,
        "workerRole": settings.worker_role,
        "hyperliquidNetwork": settings.hyperliquid_network,
        "activeCopyWallets": settings.active_copy_wallets,
        "maxRealtimeWallets": settings.max_realtime_wallets,
        "dependencies": {
            "postgres": postgres_status,
            "redis": redis_status,
        },
    }


@router.get("/ready")
async def ready(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    payload = await health(response=response, settings=settings)
    return payload


def dependency_status(*dependencies: dict[str, object]) -> str:
    if all(dependency.get("status") == "ok" for dependency in dependencies):
        return "ok"
    return "degraded"
