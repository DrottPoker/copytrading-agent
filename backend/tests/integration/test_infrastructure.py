import importlib
import os
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db import models as _models  # noqa: F401
from app.db.base import Base
from app.db.models import Setting
from app.db.session import get_engine
from app.integrations.redis_client import get_redis

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_alembic_head_matches_fresh_database_schema(
    integration_engine: AsyncEngine,
) -> None:
    async with integration_engine.connect() as connection:
        table_names = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )
        database_revision = await connection.scalar(text("select version_num from alembic_version"))

    alembic_config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()

    assert set(Base.metadata.tables).issubset(table_names)
    assert database_revision == expected_revision


@pytest.mark.asyncio
async def test_cleanup_relationships_are_database_enforced(
    integration_engine: AsyncEngine,
) -> None:
    async with integration_engine.connect() as connection:
        constraints = set(
            (
                await connection.scalars(
                    text(
                        """
                        select conname
                        from pg_constraint
                        where contype = 'f'
                          and conname in (
                            'fk_copy_trades_entry_signal_id_copy_signals',
                            'fk_copy_trades_exit_signal_id_copy_signals',
                            'fk_source_trade_links_copy_trade_id_copy_trades',
                            'fk_trading_fills_order_id_trading_orders'
                          )
                        """
                    )
                )
            ).all()
        )

    assert constraints == {
        "fk_copy_trades_entry_signal_id_copy_signals",
        "fk_copy_trades_exit_signal_id_copy_signals",
        "fk_source_trade_links_copy_trade_id_copy_trades",
        "fk_trading_fills_order_id_trading_orders",
    }


@pytest.mark.asyncio
async def test_postgres_and_redis_round_trip(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
    integration_redis,
) -> None:
    async with integration_sessionmaker() as session:
        session.add(Setting(key="integration-test", value={"status": "ok"}))
        await session.commit()

    async with integration_sessionmaker() as session:
        stored = await session.scalar(select(Setting).where(Setting.key == "integration-test"))

    await integration_redis.set("integration-test", "ok")

    assert stored is not None
    assert stored.value == {"status": "ok"}
    assert await integration_redis.get("integration-test") == "ok"


@pytest.mark.asyncio
async def test_fastapi_health_and_wallet_crud_use_real_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    integration_engine: AsyncEngine,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    redis_url = os.environ["TEST_REDIS_URL"]
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATABASE_URL_DIRECT", database_url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("DASHBOARD_AUTH_ENABLED", "false")
    monkeypatch.setenv("WORKER_RUN_IN_API_PROCESS", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_redis.cache_clear()

    import app.main as main_module

    main_module = importlib.reload(main_module)
    transport = ASGITransport(app=main_module.app)
    address = "0x" + "a" * 40
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health_response = await client.get("/health")
        create_response = await client.post(
            "/wallets",
            json={"address": address, "label": "Integration wallet"},
        )
        read_response = await client.get(f"/wallets/{address}")

    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    assert create_response.status_code == 201
    assert read_response.status_code == 200
    assert read_response.json()["address"] == address

    cached_engine = get_engine(database_url)
    if cached_engine is not None:
        await cached_engine.dispose()
    await get_redis(redis_url).aclose()
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_redis.cache_clear()
