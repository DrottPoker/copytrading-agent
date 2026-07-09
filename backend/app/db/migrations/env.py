import asyncio
from logging.config import fileConfig
from urllib.parse import urlsplit, urlunsplit

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import (  # noqa: F401
    ActiveCopyWallet,
    AuditLog,
    CopySignal,
    CopyTrade,
    JobLock,
    PaperCopyAllocation,
    PaperCopyFill,
    PaperPosition,
    PaperTradingAccount,
    RiskEvent,
    Setting,
    SourceTrade,
    SourceTradeIgnoredFill,
    SourceTradeLink,
    SourceTradeSyncState,
    TradingAccount,
    TradingCloseAllItem,
    TradingCloseAllOperation,
    TradingFill,
    TradingOrder,
    TradingOrderDispatch,
    TradingPosition,
    WalletFill,
    WalletPosition,
    WalletScore,
    WalletScoreSnapshot,
    WatchedWallet,
)
from app.db.session import normalize_asyncpg_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def migration_database_url() -> str:
    settings = get_settings()
    database_url = settings.database_url_direct or settings.database_url
    if not database_url:
        raise RuntimeError("DATABASE_URL_DIRECT or DATABASE_URL must be configured for migrations.")

    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    normalized_url = normalize_asyncpg_url(database_url)
    if normalized_url is None:
        raise RuntimeError("Migration database URL could not be resolved.")
    return normalized_url


def render_database_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def run_migrations_offline() -> None:
    context.configure(
        url=render_database_url(migration_database_url()),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = migration_database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
