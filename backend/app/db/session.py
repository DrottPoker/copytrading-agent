import asyncio
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings


@lru_cache
def get_engine(database_url: str | None = None) -> AsyncEngine | None:
    url = normalize_asyncpg_url(database_url or get_settings().database_url)
    if not url:
        return None
    return create_async_engine(url, pool_pre_ping=True)


def normalize_asyncpg_url(database_url: str | None) -> str | None:
    if not database_url or not database_url.startswith("postgresql+asyncpg://"):
        return database_url

    parsed = urlsplit(database_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)

    if sslmode in {"require", "verify-ca", "verify-full"} and "ssl" not in query:
        query["ssl"] = sslmode

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )


def get_sessionmaker(settings: Settings | None = None) -> async_sessionmaker | None:
    resolved_settings = settings or get_settings()
    engine = get_engine(resolved_settings.database_url)
    if engine is None:
        return None
    return async_sessionmaker(engine, expire_on_commit=False)


async def check_postgres(settings: Settings | None = None) -> dict[str, Any]:
    resolved_settings = settings or get_settings()
    if not resolved_settings.database_url:
        return {"status": "not_configured"}

    try:
        engine = get_engine(resolved_settings.database_url)
        if engine is None:
            return {"status": "not_configured"}

        async def ping() -> None:
            async with engine.connect() as conn:
                await conn.execute(text("select 1"))

        await asyncio.wait_for(ping(), timeout=3)
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "detail": exc.__class__.__name__}
