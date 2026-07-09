import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db import models as _models  # noqa: F401
from app.db.base import Base


def required_test_url(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"{name} is required for integration tests.")
    return value


async def truncate_application_tables(engine: AsyncEngine) -> None:
    table_names = [table.name for table in Base.metadata.sorted_tables]
    if not table_names:
        return
    quoted_names = ", ".join(f'"{name}"' for name in table_names)
    async with engine.begin() as connection:
        await connection.execute(text(f"truncate table {quoted_names} restart identity cascade"))


@pytest_asyncio.fixture
async def integration_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(required_test_url("TEST_DATABASE_URL"), pool_pre_ping=True)
    await truncate_application_tables(engine)
    try:
        yield engine
    finally:
        await truncate_application_tables(engine)
        await engine.dispose()


@pytest_asyncio.fixture
async def integration_sessionmaker(
    integration_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(integration_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def integration_redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(required_test_url("TEST_REDIS_URL"), decode_responses=True)
    await client.ping()
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()
