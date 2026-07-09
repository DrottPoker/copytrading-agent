from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.database import (
    DatabaseConnectionStats,
    DatabaseCopyTradeStats,
    DatabaseFillStats,
    DatabaseIndexStats,
    DatabaseOperationalStats,
    DatabaseScoreStats,
    DatabaseSignalStats,
    DatabaseStatsResponse,
    DatabaseTableStats,
    DatabaseWalletStats,
)

ZERO = Decimal("0")

GROUPED_COUNT_QUERIES = {
    ("watched_wallets", "polling_tier"): """
        select polling_tier as key, count(*) as value
        from watched_wallets
        group by polling_tier
        order by polling_tier
    """,
    ("copy_trades", "status"): """
        select status as key, count(*) as value
        from copy_trades
        group by status
        order by status
    """,
    ("copy_trades", "mode"): """
        select mode as key, count(*) as value
        from copy_trades
        group by mode
        order by mode
    """,
    ("active_copy_wallets", "status"): """
        select status as key, count(*) as value
        from active_copy_wallets
        group by status
        order by status
    """,
}


async def get_database_stats(
    session: AsyncSession,
    *,
    exact_fill_stats: bool = False,
) -> DatabaseStatsResponse:
    overview = await one_mapping(
        session,
        """
        select
          now() as measured_at,
          current_database() as database_name,
          pg_database_size(current_database()) as database_size_bytes,
          pg_size_pretty(pg_database_size(current_database())) as database_size_pretty
        """,
    )
    table_rows = await all_mappings(
        session,
        """
        select
          relname as name,
          n_live_tup as estimated_rows,
          n_dead_tup as dead_rows,
          pg_relation_size(relid) as table_size_bytes,
          pg_indexes_size(relid) as index_size_bytes,
          pg_total_relation_size(relid) as total_size_bytes,
          seq_scan as seq_scan_count,
          idx_scan as index_scan_count,
          last_vacuum as last_vacuum_at,
          last_autovacuum as last_autovacuum_at,
          last_analyze as last_analyze_at,
          last_autoanalyze as last_autoanalyze_at
        from pg_stat_user_tables
        where schemaname = 'public'
        order by pg_total_relation_size(relid) desc, relname asc
        """,
    )

    return DatabaseStatsResponse(
        measured_at=datetime_value(overview["measured_at"]),
        database_name=str(overview["database_name"]),
        database_size_bytes=int_value(overview["database_size_bytes"]),
        database_size_pretty=str(overview["database_size_pretty"]),
        table_count=len(table_rows),
        connections=await get_connection_stats(session),
        wallets=await get_wallet_stats(session),
        fills=await get_fill_stats(
            session,
            table_rows=table_rows,
            exact=exact_fill_stats,
        ),
        scores=await get_score_stats(session),
        copy_trades=await get_copy_trade_stats(session),
        signals=await get_signal_stats(session),
        operational=await get_operational_stats(session),
        tables=[table_stats(row) for row in table_rows],
        indexes=await get_index_stats(session),
    )


async def get_index_stats(session: AsyncSession) -> list[DatabaseIndexStats]:
    rows = await all_mappings(
        session,
        """
        select
          table_class.relname as table_name,
          index_class.relname as index_name,
          pg_relation_size(index_class.oid) as index_size_bytes,
          coalesce(stat.idx_scan, 0) as index_scan_count,
          coalesce(stat.idx_tup_read, 0) as tuples_read,
          coalesce(stat.idx_tup_fetch, 0) as tuples_fetched,
          pg_index.indisunique as is_unique,
          pg_index.indisprimary as is_primary
        from pg_class table_class
        join pg_namespace namespace on namespace.oid = table_class.relnamespace
        join pg_index on pg_index.indrelid = table_class.oid
        join pg_class index_class on index_class.oid = pg_index.indexrelid
        left join pg_stat_user_indexes stat on stat.indexrelid = index_class.oid
        where namespace.nspname = 'public'
          and table_class.relkind in ('r', 'p')
        order by pg_relation_size(index_class.oid) desc, index_class.relname asc
        """,
    )
    return [
        DatabaseIndexStats(
            table_name=str(row["table_name"]),
            index_name=str(row["index_name"]),
            index_size_bytes=int_value(row["index_size_bytes"]),
            index_scan_count=int_value(row["index_scan_count"]),
            tuples_read=int_value(row["tuples_read"]),
            tuples_fetched=int_value(row["tuples_fetched"]),
            is_unique=bool(row["is_unique"]),
            is_primary=bool(row["is_primary"]),
        )
        for row in rows
    ]


async def get_connection_stats(session: AsyncSession) -> DatabaseConnectionStats:
    row = await one_mapping(
        session,
        """
        select
          count(*) filter (where datname = current_database()) as total,
          count(*) filter (
            where datname = current_database() and state = 'active'
          ) as active,
          count(*) filter (
            where datname = current_database() and state = 'idle'
          ) as idle,
          count(*) filter (
            where datname = current_database() and state = 'idle in transaction'
          ) as idle_in_transaction,
          (
            select setting::int
            from pg_settings
            where name = 'max_connections'
          ) as max_connections
        from pg_stat_activity
        """,
    )
    total = int_value(row["total"])
    max_connections = optional_int(row["max_connections"])
    return DatabaseConnectionStats(
        total=total,
        active=int_value(row["active"]),
        idle=int_value(row["idle"]),
        idle_in_transaction=int_value(row["idle_in_transaction"]),
        max_connections=max_connections,
        usage_pct=(
            Decimal(total) / Decimal(max_connections)
            if max_connections is not None and max_connections > 0
            else None
        ),
    )


async def get_wallet_stats(session: AsyncSession) -> DatabaseWalletStats:
    row = await one_mapping(
        session,
        """
        select
          count(*) as total,
          count(*) filter (where enabled is true) as enabled,
          count(*) filter (where eligible is true) as eligible,
          count(*) filter (where copy_enabled is true) as copy_enabled,
          count(*) filter (where last_polled_at is null) as unpolled,
          count(*) filter (
            where last_polled_at is null or last_polled_at < now() - interval '24 hours'
          ) as stale_24h,
          max(last_polled_at) as last_polled_at,
          max(last_seen_fill_at) as last_seen_fill_at
        from watched_wallets
        """,
    )
    return DatabaseWalletStats(
        total=int_value(row["total"]),
        enabled=int_value(row["enabled"]),
        eligible=int_value(row["eligible"]),
        copy_enabled=int_value(row["copy_enabled"]),
        unpolled=int_value(row["unpolled"]),
        stale_24h=int_value(row["stale_24h"]),
        last_polled_at=optional_datetime(row["last_polled_at"]),
        last_seen_fill_at=optional_datetime(row["last_seen_fill_at"]),
        tiers=await grouped_counts(session, "watched_wallets", "polling_tier"),
    )


async def get_fill_stats(
    session: AsyncSession,
    *,
    table_rows: list[dict[str, Any]] | None = None,
    exact: bool = False,
) -> DatabaseFillStats:
    if exact:
        return await get_exact_fill_stats(session)

    table_row = table_row_by_name(table_rows or [], "wallet_fills")
    estimated_total = int_value(table_row["estimated_rows"]) if table_row else 0
    sync_row = await one_mapping(
        session,
        """
        select
          coalesce(sum(sts.fill_count), 0) as synced_total,
          count(*) filter (where sts.fill_count > 0) as wallet_count,
          count(*) filter (
            where sts.fill_count > 0 and ww.address is not null
          ) as pool_wallet_count,
          count(*) filter (
            where sts.fill_count > 0 and ww.address is null
          ) as orphan_wallet_count,
          min(sts.last_fill_timestamp_ms) filter (
            where sts.fill_count > 0
          ) as first_fill_time_ms,
          max(sts.last_fill_timestamp_ms) filter (
            where sts.fill_count > 0
          ) as last_fill_time_ms,
          max(sts.synced_at) as last_inserted_at
        from source_trade_sync_states sts
        left join watched_wallets ww on ww.address = sts.wallet_address
        """,
    )
    trade_row = await one_mapping(
        session,
        """
        select
          count(distinct coin) as coin_count,
          coalesce(sum(entry_notional_usd), 0) as total_notional_usd,
          coalesce(sum(fee_usd), 0) as total_fee_usd,
          coalesce(sum(net_pnl_usd), 0) as total_pnl_usd
        from source_trades
        """,
    )
    synced_total = int_value(sync_row["synced_total"])
    total = max(estimated_total, synced_total)
    wallet_count = int_value(sync_row["wallet_count"])
    pool_wallet_count = int_value(sync_row["pool_wallet_count"])
    orphan_wallet_count = int_value(sync_row["orphan_wallet_count"])
    return DatabaseFillStats(
        exact=False,
        total=total,
        snapshot=0,
        realtime=0,
        wallet_count=wallet_count,
        pool_wallet_count=pool_wallet_count,
        orphan_wallet_count=orphan_wallet_count,
        coin_count=int_value(trade_row["coin_count"]),
        total_notional_usd=decimal_value(trade_row["total_notional_usd"]),
        total_fee_usd=decimal_value(trade_row["total_fee_usd"]),
        total_pnl_usd=decimal_value(trade_row["total_pnl_usd"]),
        first_fill_time_ms=optional_int(sync_row["first_fill_time_ms"]),
        last_fill_time_ms=optional_int(sync_row["last_fill_time_ms"]),
        last_inserted_at=optional_datetime(sync_row["last_inserted_at"]),
    )


async def get_exact_fill_stats(session: AsyncSession) -> DatabaseFillStats:
    row = await one_mapping(
        session,
        """
        select
          count(wf.*) as total,
          count(wf.*) filter (where wf.is_snapshot is true) as snapshot,
          count(wf.*) filter (where wf.is_snapshot is false) as realtime,
          count(distinct wf.wallet_address) as wallet_count,
          count(distinct wf.wallet_address) filter (
            where ww.address is not null
          ) as pool_wallet_count,
          count(distinct wf.wallet_address) filter (
            where ww.address is null
          ) as orphan_wallet_count,
          count(distinct wf.coin) as coin_count,
          coalesce(sum(wf.notional_usd), 0) as total_notional_usd,
          coalesce(sum(wf.fee_usd), 0) as total_fee_usd,
          coalesce(sum(wf.pnl_usd), 0) as total_pnl_usd,
          min(wf.timestamp_ms) as first_fill_time_ms,
          max(wf.timestamp_ms) as last_fill_time_ms,
          max(wf.created_at) as last_inserted_at
        from wallet_fills wf
        left join watched_wallets ww on ww.address = wf.wallet_address
        """,
    )
    return DatabaseFillStats(
        exact=True,
        total=int_value(row["total"]),
        snapshot=int_value(row["snapshot"]),
        realtime=int_value(row["realtime"]),
        wallet_count=int_value(row["wallet_count"]),
        pool_wallet_count=int_value(row["pool_wallet_count"]),
        orphan_wallet_count=int_value(row["orphan_wallet_count"]),
        coin_count=int_value(row["coin_count"]),
        total_notional_usd=decimal_value(row["total_notional_usd"]),
        total_fee_usd=decimal_value(row["total_fee_usd"]),
        total_pnl_usd=decimal_value(row["total_pnl_usd"]),
        first_fill_time_ms=optional_int(row["first_fill_time_ms"]),
        last_fill_time_ms=optional_int(row["last_fill_time_ms"]),
        last_inserted_at=optional_datetime(row["last_inserted_at"]),
    )


async def get_database_summary_stats(session: AsyncSession) -> dict[str, Any]:
    overview = await one_mapping(
        session,
        """
        select
          now() as measured_at,
          current_database() as database_name,
          pg_database_size(current_database()) as database_size_bytes,
          pg_size_pretty(pg_database_size(current_database())) as database_size_pretty
        """,
    )
    table_rows = await all_mappings(
        session,
        """
        select
          relname as name,
          n_live_tup as estimated_rows,
          pg_total_relation_size(relid) as total_size_bytes
        from pg_stat_user_tables
        where schemaname = 'public'
        order by pg_total_relation_size(relid) desc, relname asc
        """,
    )
    return {
        "overview": overview,
        "table_rows": table_rows,
        "connections": await get_connection_stats(session),
        "fill_count": int_value(
            (table_row_by_name(table_rows, "wallet_fills") or {}).get("estimated_rows")
        ),
    }


async def get_score_stats(session: AsyncSession) -> DatabaseScoreStats:
    row = await one_mapping(
        session,
        """
        select
          count(*) as scored_wallets,
          avg(ws.score) as average_score,
          max(ws.score) as best_score,
          count(*) filter (where ws.score <= 0) as zero_or_negative,
          count(*) filter (where ws.score >= 70) as above_70,
          max(ws.updated_at) as last_scored_at
        from wallet_scores ws
        join watched_wallets ww on ww.address = ws.wallet_address
        """,
    )
    return DatabaseScoreStats(
        scored_wallets=int_value(row["scored_wallets"]),
        average_score=optional_decimal(row["average_score"]),
        best_score=optional_decimal(row["best_score"]),
        zero_or_negative=int_value(row["zero_or_negative"]),
        above_70=int_value(row["above_70"]),
        last_scored_at=optional_datetime(row["last_scored_at"]),
    )


async def get_copy_trade_stats(session: AsyncSession) -> DatabaseCopyTradeStats:
    row = await one_mapping(
        session,
        """
        select
          count(*) as total,
          count(*) filter (where status = 'open') as open,
          count(*) filter (where status = 'closed') as closed,
          count(*) filter (where status = 'error') as error,
          coalesce(sum(size_usd), 0) as total_size_usd,
          coalesce(sum(pnl_usd), 0) as total_pnl_usd,
          max(created_at) as last_created_at
        from copy_trades
        """,
    )
    return DatabaseCopyTradeStats(
        total=int_value(row["total"]),
        open=int_value(row["open"]),
        closed=int_value(row["closed"]),
        error=int_value(row["error"]),
        total_size_usd=decimal_value(row["total_size_usd"]),
        total_pnl_usd=decimal_value(row["total_pnl_usd"]),
        last_created_at=optional_datetime(row["last_created_at"]),
        statuses=await grouped_counts(session, "copy_trades", "status"),
        modes=await grouped_counts(session, "copy_trades", "mode"),
    )


async def get_signal_stats(session: AsyncSession) -> DatabaseSignalStats:
    row = await one_mapping(
        session,
        """
        select
          count(*) as total,
          count(*) filter (where decision = 'copy') as copy,
          count(*) filter (where decision = 'skip') as skip,
          count(*) filter (where decision = 'exit') as exit,
          count(*) filter (where decision = 'observe') as observe,
          max(created_at) as last_created_at
        from copy_signals
        """,
    )
    return DatabaseSignalStats(
        total=int_value(row["total"]),
        copy_count=int_value(row["copy"]),
        skip=int_value(row["skip"]),
        exit=int_value(row["exit"]),
        observe=int_value(row["observe"]),
        last_created_at=optional_datetime(row["last_created_at"]),
    )


async def get_operational_stats(session: AsyncSession) -> DatabaseOperationalStats:
    row = await one_mapping(
        session,
        """
        select
          (select count(*) from active_copy_wallets) as active_copy_wallets,
          (
            select count(*)
            from active_copy_wallets
            where has_realtime_slot is true
          ) as realtime_slots_used,
          (select count(*) from source_trade_links) as source_trade_links,
          (select count(*) from risk_events) as risk_events,
          (select count(*) from audit_logs) as audit_logs,
          (select count(*) from settings) as settings
        """,
    )
    return DatabaseOperationalStats(
        active_copy_wallets=int_value(row["active_copy_wallets"]),
        realtime_slots_used=int_value(row["realtime_slots_used"]),
        active_copy_statuses=await grouped_counts(session, "active_copy_wallets", "status"),
        source_trade_links=int_value(row["source_trade_links"]),
        risk_events=int_value(row["risk_events"]),
        audit_logs=int_value(row["audit_logs"]),
        settings=int_value(row["settings"]),
    )


async def grouped_counts(
    session: AsyncSession,
    table_name: str,
    column_name: str,
) -> dict[str, int]:
    rows = await all_mappings(session, GROUPED_COUNT_QUERIES[(table_name, column_name)])
    return {str(row["key"]): int_value(row["value"]) for row in rows if row["key"] is not None}


def table_stats(row: dict[str, Any]) -> DatabaseTableStats:
    return DatabaseTableStats(
        name=str(row["name"]),
        estimated_rows=int_value(row["estimated_rows"]),
        dead_rows=int_value(row["dead_rows"]),
        table_size_bytes=int_value(row["table_size_bytes"]),
        index_size_bytes=int_value(row["index_size_bytes"]),
        total_size_bytes=int_value(row["total_size_bytes"]),
        seq_scan_count=int_value(row["seq_scan_count"]),
        index_scan_count=int_value(row["index_scan_count"]),
        last_vacuum_at=optional_datetime(row["last_vacuum_at"]),
        last_autovacuum_at=optional_datetime(row["last_autovacuum_at"]),
        last_analyze_at=optional_datetime(row["last_analyze_at"]),
        last_autoanalyze_at=optional_datetime(row["last_autoanalyze_at"]),
    )


def table_row_by_name(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((row for row in rows if row.get("name") == name), None)


async def one_mapping(session: AsyncSession, sql: str) -> dict[str, Any]:
    return dict((await session.execute(text(sql))).mappings().one())


async def all_mappings(session: AsyncSession, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in (await session.execute(text(sql))).mappings().all()]


def int_value(value: object) -> int:
    return int(value or 0)


def optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def decimal_value(value: object) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value))


def optional_decimal(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def datetime_value(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("Expected datetime value.")
    return value


def optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime_value(value)
