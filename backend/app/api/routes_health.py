import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.db.session import check_postgres
from app.integrations.redis_client import check_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    postgres_status, redis_status = await asyncio.gather(
        check_postgres(settings),
        check_redis(settings),
    )

    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "mode": settings.system_mode,
        "paperTradingEnabled": settings.paper_trading_enabled,
        "liveTradingEnabled": settings.live_trading_enabled,
        "workerRunInApiProcess": settings.worker_run_in_api_process,
        "hyperliquidNetwork": settings.hyperliquid_network,
        "activeCopyWallets": settings.active_copy_wallets,
        "maxRealtimeWallets": settings.max_realtime_wallets,
        "dependencies": {
            "postgres": postgres_status,
            "redis": redis_status,
        },
    }
