import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes_database import router as database_router
from app.api.routes_discovery import router as discovery_router
from app.api.routes_events import router as events_router
from app.api.routes_health import router as health_router
from app.api.routes_operations import router as operations_router
from app.api.routes_ops import router as ops_router
from app.api.routes_paper_trading import router as paper_trading_router
from app.api.routes_scores import router as scores_router
from app.api.routes_wallets import router as wallets_router
from app.core.auth import DashboardAuthMiddleware
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_sessionmaker
from app.integrations.redis_client import get_redis
from app.services.job_lock_service import JobLockAlreadyHeldError
from app.workers.monitor_worker import run_monitor_services

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    stop_event: asyncio.Event | None = None
    background_task: asyncio.Task[None] | None = None

    if settings.worker_run_in_api_process:
        sessionmaker = get_sessionmaker(settings)
        if sessionmaker is None:
            logger.error("api background worker cannot start: database is not configured")
        else:
            stop_event = asyncio.Event()
            background_task = asyncio.create_task(
                run_monitor_services(
                    sessionmaker=sessionmaker,
                    redis=get_redis(settings.redis_url),
                    stop_event=stop_event,
                    settings=settings,
                )
            )
            logger.info("api background worker started")

    try:
        yield
    finally:
        if stop_event is not None:
            stop_event.set()
        if background_task is not None:
            try:
                await asyncio.wait_for(background_task, timeout=30)
            except TimeoutError:
                background_task.cancel()
                await asyncio.gather(background_task, return_exceptions=True)
            logger.info("api background worker stopped")

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url="/redoc" if settings.app_env != "production" else None,
    lifespan=lifespan,
)

app.add_middleware(DashboardAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(database_router)
app.include_router(discovery_router)
app.include_router(wallets_router)
app.include_router(events_router)
app.include_router(operations_router)
app.include_router(ops_router)
app.include_router(scores_router)
app.include_router(paper_trading_router)


@app.exception_handler(JobLockAlreadyHeldError)
async def job_lock_already_held_handler(
    _request: Request,
    exc: JobLockAlreadyHeldError,
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/")
async def root() -> dict[str, str | bool]:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "paperTradingEnabled": settings.paper_trading_enabled,
        "liveTradingEnabled": settings.live_trading_enabled,
    }
