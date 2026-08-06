from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UpdatedAtMixin:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WatchedWallet(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "watched_wallets"
    __table_args__ = (
        CheckConstraint(
            "polling_tier in ('pool', 'candidate', 'active', 'cooldown')",
            name="ck_watched_wallets_polling_tier",
        ),
        Index("ix_watched_wallets_enabled_eligible", "enabled", "eligible"),
        Index("ix_watched_wallets_polling_tier", "polling_tier"),
        Index("ix_watched_wallets_last_seen_fill_at", "last_seen_fill_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    address: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    copy_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    polling_tier: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pool'"))
    fill_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    copy_eligibility_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_fill_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class WalletFill(Base, TimestampMixin):
    __tablename__ = "wallet_fills"
    __table_args__ = (
        CheckConstraint("side in ('buy', 'sell', 'long', 'short')", name="ck_wallet_fills_side"),
        Index("ux_wallet_fills_wallet_external", "wallet_address", "external_fill_id", unique=True),
        Index("ix_wallet_fills_wallet_timestamp", "wallet_address", "timestamp_ms"),
        Index("ix_wallet_fills_wallet_coin_timestamp", "wallet_address", "coin", "timestamp_ms"),
        Index("ix_wallet_fills_timestamp", "timestamp_ms"),
        Index(
            "ix_wallet_fills_liquidation_wallet_timestamp",
            "wallet_address",
            "timestamp_ms",
            "id",
            postgresql_where=text("raw_json ? 'liquidation'"),
        ),
        Index(
            "ix_wallet_fills_nonliquidation_wallet_timestamp",
            "wallet_address",
            "timestamp_ms",
            postgresql_where=text("not (raw_json ? 'liquidation')"),
        ),
        Index(
            "ix_wallet_fills_source_trade_order",
            "wallet_address",
            "timestamp_ms",
            "external_fill_id",
            postgresql_where=text(
                "(raw_json->>'dir') in ('Open Long', 'Close Long', 'Open Short', "
                "'Close Short', 'Long > Short', 'Short > Long')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False)
    external_fill_id: Mapped[str] = mapped_column(Text, nullable=False)
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    notional_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    fee_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_timestamp_ms: Mapped[int | None] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingest_latency_ms: Mapped[int | None] = mapped_column(BigInteger)
    is_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class LiveCopyWork(Base, TimestampMixin, UpdatedAtMixin):
    """One durable live-copy execution item per imported source fill."""

    __tablename__ = "live_copy_work"
    __table_args__ = (
        CheckConstraint(
            "origin in ('realtime', 'snapshot_recovery', 'startup_recovery', 'periodic_recovery')",
            name="ck_live_copy_work_origin",
        ),
        CheckConstraint(
            "status in ('pending', 'processing', 'completed')",
            name="ck_live_copy_work_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_live_copy_work_attempt_count",
        ),
        UniqueConstraint("wallet_fill_id", name="ux_live_copy_work_wallet_fill"),
        UniqueConstraint(
            "wallet_address",
            "source_fill_id",
            name="ux_live_copy_work_wallet_source_fill",
        ),
        Index(
            "ix_live_copy_work_claim",
            "status",
            "available_at",
            "source_timestamp_ms",
        ),
        Index(
            "ix_live_copy_work_wallet_order",
            "wallet_address",
            "status",
            "source_timestamp_ms",
            "coin",
            "source_order_direction_rank",
            "source_order_position",
            "source_fill_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    wallet_fill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("wallet_fills.id", ondelete="CASCADE"),
        nullable=False,
    )
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False)
    source_fill_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    source_order_direction_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    source_order_position: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    source_order_fill_id_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(Text)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class RealtimeExecutionInbox(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "realtime_execution_inbox"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'processing')",
            name="ck_realtime_execution_inbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_realtime_execution_inbox_attempt_count",
        ),
        Index(
            "ix_realtime_execution_inbox_claim",
            "status",
            "available_at",
            "created_at",
        ),
        Index(
            "ix_realtime_execution_inbox_wallet_created",
            "wallet_address",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)


class WalletPosition(Base):
    __tablename__ = "wallet_positions"
    __table_args__ = (
        CheckConstraint("side in ('long', 'short', 'flat')", name="ck_wallet_positions_side"),
        Index("ux_wallet_positions_wallet_coin", "wallet_address", "coin", unique=True),
        Index("ix_wallet_positions_updated_at", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False)
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric)
    position_value_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    unrealized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    liquidation_price: Mapped[Decimal | None] = mapped_column(Numeric)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def notional_usd(self) -> Decimal | None:
        return self.position_value_usd

    @notional_usd.setter
    def notional_usd(self, value: Decimal | None) -> None:
        self.position_value_usd = value


class WalletScore(Base):
    __tablename__ = "wallet_scores"
    __table_args__ = (
        CheckConstraint(
            "current_drawdown_status in ('ok', 'unavailable', 'zero_equity', 'disabled')",
            name="ck_wallet_scores_current_drawdown_status",
        ),
        Index("ix_wallet_scores_score", "score"),
        Index("ix_wallet_scores_updated_at", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    score: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    pnl_score: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    copyability_score: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    risk_score: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    consistency_score: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    recency_score: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    penalty_score: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    copyable_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric)
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric)
    max_drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    current_drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    open_position_stress_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    current_drawdown_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'disabled'")
    )
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_24h_score: Mapped[Decimal | None] = mapped_column(Numeric)
    last_7d_score: Mapped[Decimal | None] = mapped_column(Numeric)
    last_30d_score: Mapped[Decimal | None] = mapped_column(Numeric)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def realized_drawdown_pct(self) -> Decimal | None:
        return self.max_drawdown_pct

    @realized_drawdown_pct.setter
    def realized_drawdown_pct(self, value: Decimal | None) -> None:
        self.max_drawdown_pct = value


class WalletScoreSnapshot(Base, TimestampMixin):
    __tablename__ = "wallet_score_snapshots"
    __table_args__ = (
        Index("ix_wallet_score_snapshots_wallet_created", "wallet_address", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    score_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class WalletMonitoringStat(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "wallet_monitoring_stats"
    __table_args__ = (
        CheckConstraint(
            "total_monitored_seconds >= 0",
            name="ck_wallet_monitoring_stats_total_non_negative",
        ),
        Index(
            "ix_wallet_monitoring_stats_current_started",
            "current_monitoring_started_at",
        ),
        Index("ix_wallet_monitoring_stats_last_monitored", "last_monitored_at"),
    )

    wallet_address: Mapped[str] = mapped_column(Text, primary_key=True)
    first_monitored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_monitoring_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_monitored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_monitored_seconds: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )


class ActiveCopyWallet(Base, TimestampMixin):
    __tablename__ = "active_copy_wallets"
    __table_args__ = (
        CheckConstraint(
            "status in ('active', 'exit_only', 'promotion_pending', 'inactive')",
            name="ck_active_copy_wallets_status",
        ),
        Index("ix_active_copy_wallets_status_rank", "status", "rank"),
        Index("ix_active_copy_wallets_has_realtime_slot", "has_realtime_slot"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    desired_rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    has_realtime_slot: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    blocked_by_wallet_address: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CopySignal(Base, TimestampMixin):
    __tablename__ = "copy_signals"
    __table_args__ = (
        CheckConstraint(
            "action in ('open', 'add', 'reduce', 'close', 'flip', 'unknown')",
            name="ck_copy_signals_action",
        ),
        CheckConstraint(
            "decision in ('copy', 'skip', 'exit', 'observe')", name="ck_copy_signals_decision"
        ),
        CheckConstraint(
            "mode in ('monitor', 'paper', 'live_small', 'full_live')",
            name="ck_copy_signals_mode",
        ),
        Index("ix_copy_signals_source_created", "source_wallet", "created_at"),
        Index("ix_copy_signals_decision_created", "decision", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_wallet: Mapped[str] = mapped_column(Text, nullable=False)
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str | None] = mapped_column(Text)
    source_price: Mapped[Decimal | None] = mapped_column(Numeric)
    observed_price: Mapped[Decimal | None] = mapped_column(Numeric)
    price_drift_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    raw_event_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    source_timestamp_ms: Mapped[int | None] = mapped_column(BigInteger)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decision_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class CopyTrade(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "copy_trades"
    __table_args__ = (
        CheckConstraint("mode in ('paper', 'live_small', 'full_live')", name="ck_copy_trades_mode"),
        CheckConstraint(
            "status in ('open', 'closing', 'closed', 'cancelled', 'error')",
            name="ck_copy_trades_status",
        ),
        CheckConstraint("side in ('long', 'short')", name="ck_copy_trades_side"),
        Index("ix_copy_trades_status_opened", "status", "opened_at"),
        Index("ix_copy_trades_source_status", "source_wallet", "status"),
        Index("ix_copy_trades_coin_status", "coin", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    source_wallet: Mapped[str] = mapped_column(Text, nullable=False)
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_entry_price: Mapped[Decimal | None] = mapped_column(Numeric)
    our_entry_price: Mapped[Decimal | None] = mapped_column(Numeric)
    source_exit_price: Mapped[Decimal | None] = mapped_column(Numeric)
    our_exit_price: Mapped[Decimal | None] = mapped_column(Numeric)
    size_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    risk_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    entry_signal_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("copy_signals.id", ondelete="SET NULL"),
    )
    exit_signal_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("copy_signals.id", ondelete="SET NULL"),
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceTradeLink(Base, TimestampMixin):
    __tablename__ = "source_trade_links"
    __table_args__ = (
        CheckConstraint(
            "link_type in ('entry', 'add', 'reduce', 'close', 'flip')",
            name="ck_source_trade_links_link_type",
        ),
        Index("ix_source_trade_links_source_fill", "source_wallet", "source_fill_id"),
        Index("ix_source_trade_links_copy_trade_id", "copy_trade_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_wallet: Mapped[str] = mapped_column(Text, nullable=False)
    source_fill_id: Mapped[str | None] = mapped_column(Text)
    copy_trade_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("copy_trades.id", ondelete="CASCADE"),
        nullable=False,
    )
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    link_type: Mapped[str] = mapped_column(Text, nullable=False)


class SourceTrade(Base, UpdatedAtMixin):
    __tablename__ = "source_trades"
    __table_args__ = (
        CheckConstraint(
            "status in ('open', 'closed')",
            name="ck_source_trades_status",
        ),
        CheckConstraint("side in ('long', 'short')", name="ck_source_trades_side"),
        UniqueConstraint("trade_key", name="ux_source_trades_trade_key"),
        Index("ix_source_trades_wallet_closed", "wallet_address", "closed_at_ms"),
        Index("ix_source_trades_wallet_status", "wallet_address", "status"),
        Index("ix_source_trades_wallet_coin", "wallet_address", "coin"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    trade_key: Mapped[str] = mapped_column(Text, nullable=False)
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False)
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    opened_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    closed_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    entry_size: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    closed_size: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    remaining_size: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    entry_notional_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    close_notional_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    average_entry_price: Mapped[Decimal | None] = mapped_column(Numeric)
    average_exit_price: Mapped[Decimal | None] = mapped_column(Numeric)
    realized_pnl_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fee_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    net_pnl_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    entry_fill_count: Mapped[int] = mapped_column(Integer, nullable=False)
    close_fill_count: Mapped[int] = mapped_column(Integer, nullable=False)
    has_liquidation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    liquidation_fill_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    liquidation_notional_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )


class SourceTradeSyncState(Base):
    __tablename__ = "source_trade_sync_states"

    wallet_address: Mapped[str] = mapped_column(Text, primary_key=True)
    fill_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    fill_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_fill_timestamp_ms: Mapped[int | None] = mapped_column(BigInteger)
    unmatched_close_fill_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    preexisting_open_fill_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SourceTradeIgnoredFill(Base):
    __tablename__ = "source_trade_ignored_fills"
    __table_args__ = (
        CheckConstraint(
            "reason in ('unmatched_close', 'preexisting_open')",
            name="ck_source_trade_ignored_fills_reason",
        ),
        UniqueConstraint(
            "wallet_address",
            "external_fill_id",
            "reason",
            name="ux_source_trade_ignored_fills_wallet_external_reason",
        ),
        Index("ix_source_trade_ignored_fills_wallet_timestamp", "wallet_address", "timestamp_ms"),
        Index("ix_source_trade_ignored_fills_reason", "reason"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False)
    external_fill_id: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class RiskEvent(Base, TimestampMixin):
    __tablename__ = "risk_events"
    __table_args__ = (
        CheckConstraint(
            "severity in ('info', 'warning', 'critical')", name="ck_risk_events_severity"
        ),
        Index("ix_risk_events_type_created", "event_type", "created_at"),
        Index("ix_risk_events_severity_created", "severity", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class DiscoveryImportRun(Base):
    __tablename__ = "discovery_import_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'succeeded', 'failed')",
            name="ck_discovery_import_runs_status",
        ),
        Index("ix_discovery_import_runs_source_started", "source", "started_at"),
        Index("ix_discovery_import_runs_status_started", "status", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    run_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DiscoveryWalletCandidate(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "discovery_wallet_candidates"
    __table_args__ = (
        CheckConstraint(
            "status in ('discovered', 'accepted', 'rejected', 'promoted', 'ignored')",
            name="ck_discovery_wallet_candidates_status",
        ),
        CheckConstraint(
            "account_role in ('master', 'subaccount', 'vault', 'vault_leader', 'unknown')",
            name="ck_discovery_wallet_candidates_account_role",
        ),
        CheckConstraint(
            "backfill_status in ('not_started', 'running', 'succeeded', 'failed')",
            name="ck_discovery_wallet_candidates_backfill_status",
        ),
        UniqueConstraint("source", "wallet_address", name="ux_discovery_candidates_source_wallet"),
        Index("ix_discovery_candidates_wallet", "wallet_address"),
        Index("ix_discovery_candidates_source_rank", "source", "source_rank"),
        Index("ix_discovery_candidates_source_status", "source", "status"),
        Index("ix_discovery_candidates_status_last_seen", "status", "last_seen_at"),
        Index("ix_discovery_candidates_backfill_status", "backfill_status"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_rank: Mapped[int | None] = mapped_column(Integer)
    source_label: Mapped[str | None] = mapped_column(Text)
    source_cohort: Mapped[str | None] = mapped_column(Text)
    source_account_value_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    source_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    source_roi_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    source_copy_score: Mapped[Decimal | None] = mapped_column(Numeric)
    account_role: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unknown'")
    )
    parent_address: Mapped[str | None] = mapped_column(Text)
    subaccount_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'discovered'"))
    fail_reason: Mapped[str | None] = mapped_column(Text)
    last_import_run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    backfill_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'not_started'")
    )
    backfill_error: Mapped[str | None] = mapped_column(Text)
    last_backfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backfill_fetched_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    backfill_inserted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    backfill_duplicate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    fill_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    closed_trade_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    open_trade_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    ignored_fill_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    net_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric)
    max_drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    average_trade_notional_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    last_trade_time_ms: Mapped[int | None] = mapped_column(BigInteger)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def account_value(self) -> Decimal | None:
        return self.source_account_value_usd

    @account_value.setter
    def account_value(self, value: Decimal | None) -> None:
        self.source_account_value_usd = value

    @property
    def source_pnl(self) -> Decimal | None:
        return self.source_pnl_usd

    @source_pnl.setter
    def source_pnl(self, value: Decimal | None) -> None:
        self.source_pnl_usd = value

    @property
    def source_roi(self) -> Decimal | None:
        return self.source_roi_pct

    @source_roi.setter
    def source_roi(self, value: Decimal | None) -> None:
        self.source_roi_pct = value


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class JobLock(Base):
    __tablename__ = "job_locks"
    __table_args__ = (Index("ix_job_locks_locked_until", "locked_until"),)

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    locked_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TradingAccount(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "trading_accounts"
    __table_args__ = (
        CheckConstraint("account_type in ('paper', 'live')", name="ck_trading_accounts_type"),
        CheckConstraint(
            "status in ('disabled', 'enabled', 'exit_only')",
            name="ck_trading_accounts_status",
        ),
        CheckConstraint(
            "network in ('mainnet', 'testnet')",
            name="ck_trading_accounts_network",
        ),
        CheckConstraint(
            "lifecycle_version >= 0",
            name="ck_trading_accounts_lifecycle_version",
        ),
        UniqueConstraint(
            "key",
            "account_type",
            name="ux_trading_accounts_key_type",
        ),
        Index("ix_trading_accounts_type_status", "account_type", "status"),
    )

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    account_type: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'disabled'"))
    network: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'testnet'"))
    wallet_address: Mapped[str | None] = mapped_column(Text)
    vault_address: Mapped[str | None] = mapped_column(Text)
    starting_balance_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    cash_balance_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    equity_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    realized_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    fee_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lifecycle_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    status_reason: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


Index(
    "ux_trading_accounts_live_active_route",
    TradingAccount.network,
    func.lower(func.btrim(TradingAccount.wallet_address)),
    func.coalesce(func.lower(func.btrim(TradingAccount.vault_address)), text("''")),
    unique=True,
    postgresql_where=text(
        "account_type = 'live' and archived_at is null "
        "and wallet_address is not null and btrim(wallet_address) <> ''"
    ),
)


class TradingReconciliationRun(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "trading_reconciliation_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'complete', 'partial', 'failed')",
            name="ck_trading_reconciliation_runs_status",
        ),
        Index(
            "ix_trading_reconciliation_runs_account_started",
            "account_key",
            "started_at",
        ),
        Index("ix_trading_reconciliation_runs_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_key: Mapped[str] = mapped_column(
        Text,
        ForeignKey("trading_accounts.key", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    components: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    fetched_fills: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    inserted_fills: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_orders: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    removed_positions: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    error: Mapped[str | None] = mapped_column(Text)


class TradingPosition(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "trading_positions"
    __table_args__ = (
        CheckConstraint("account_type in ('paper', 'live')", name="ck_trading_positions_type"),
        CheckConstraint("side in ('long', 'short')", name="ck_trading_positions_side"),
        CheckConstraint(
            "margin_mode in ('cross', 'isolated')",
            name="ck_trading_positions_margin_mode",
        ),
        ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_trading_positions_account_key_type_trading_accounts",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "account_key",
            "source_wallet",
            "coin",
            name="ux_trading_positions_account_source_coin",
        ),
        Index("ix_trading_positions_account", "account_key"),
        Index("ix_trading_positions_source", "source_wallet"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_wallet: Mapped[str] = mapped_column(Text, nullable=False)
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    notional_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    leverage: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("1"))
    margin_mode: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'cross'"),
    )
    margin_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    realized_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    fee_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    source_lifecycle_timestamp_ms: Mapped[int | None] = mapped_column(BigInteger)
    source_lifecycle_direction_rank: Mapped[int | None] = mapped_column(Integer)
    source_lifecycle_position: Mapped[Decimal | None] = mapped_column(Numeric)
    source_lifecycle_fill_id_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    source_lifecycle_fill_id: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TradingOrder(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "trading_orders"
    __table_args__ = (
        CheckConstraint("account_type in ('paper', 'live')", name="ck_trading_orders_type"),
        ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_trading_orders_account_key_type_trading_accounts",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "action in ('open', 'add', 'reduce', 'close', 'flip_close', 'flip_open')",
            name="ck_trading_orders_action",
        ),
        CheckConstraint("side in ('long', 'short')", name="ck_trading_orders_side"),
        CheckConstraint(
            "margin_mode in ('cross', 'isolated')",
            name="ck_trading_orders_margin_mode",
        ),
        CheckConstraint(
            "status in ("
            "'planned', 'ready', 'submitting', 'uncertain', 'submitted', 'accepted', "
            "'rejected', 'partially_filled', 'filled', 'canceled', 'failed'"
            ")",
            name="ck_trading_orders_status",
        ),
        UniqueConstraint("client_order_id", name="ux_trading_orders_client_order_id"),
        UniqueConstraint(
            "account_key",
            "source_wallet",
            "source_fill_id",
            "sequence_index",
            name="ux_trading_orders_account_source_fill_sequence",
        ),
        Index("ix_trading_orders_account_created", "account_key", "created_at"),
        Index("ix_trading_orders_source_created", "source_wallet", "created_at"),
        Index("ix_trading_orders_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_wallet: Mapped[str] = mapped_column(Text, nullable=False)
    source_fill_id: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    client_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(Text)
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    is_buy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False)
    order_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ioc'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'planned'"))
    requested_size: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    requested_notional_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    margin_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    leverage: Mapped[Decimal | None] = mapped_column(Numeric)
    margin_mode: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'cross'"),
    )
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric)
    average_fill_price: Mapped[Decimal | None] = mapped_column(Numeric)
    filled_size: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    filled_notional_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    fee_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LiveCopySourceState(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "live_copy_source_states"
    __table_args__ = (
        CheckConstraint(
            "account_type = 'live'",
            name="ck_live_copy_source_states_account_type",
        ),
        CheckConstraint(
            "status in ('baseline_pending', 'active', 'inactive')",
            name="ck_live_copy_source_states_status",
        ),
        ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_live_copy_source_states_account_key_type",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_live_copy_source_states_status_baseline",
            "status",
            "baseline_completed_at",
        ),
    )

    account_key: Mapped[str] = mapped_column(Text, primary_key=True)
    source_wallet: Mapped[str] = mapped_column(Text, primary_key=True)
    account_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'live'"),
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'baseline_pending'"),
    )
    entry_eligible: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    baseline_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    baseline_source_timestamp_ms: Mapped[int | None] = mapped_column(BigInteger)
    baseline_fill_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    scan_high_water_timestamp_ms: Mapped[int | None] = mapped_column(BigInteger)
    scan_high_water_coin: Mapped[str | None] = mapped_column(Text)
    scan_high_water_direction_rank: Mapped[int | None] = mapped_column(Integer)
    scan_high_water_position: Mapped[Decimal | None] = mapped_column(Numeric)
    scan_high_water_fill_id_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    scan_high_water_fill_id: Mapped[str | None] = mapped_column(Text)
    preexisting_markets: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )


class LiveCopyFillState(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "live_copy_fill_states"
    __table_args__ = (
        CheckConstraint(
            "account_type = 'live'",
            name="ck_live_copy_fill_states_account_type",
        ),
        CheckConstraint(
            "action in ('open', 'add', 'reduce', 'close', 'flip_close', 'flip_open')",
            name="ck_live_copy_fill_states_action",
        ),
        CheckConstraint(
            "side in ('long', 'short')",
            name="ck_live_copy_fill_states_side",
        ),
        CheckConstraint(
            "origin in ('realtime', 'snapshot_recovery', 'startup_recovery', 'periodic_recovery')",
            name="ck_live_copy_fill_states_origin",
        ),
        CheckConstraint(
            "outcome in ('pending', 'retryable', 'order', 'terminal_skip', 'baseline_ignored')",
            name="ck_live_copy_fill_states_outcome",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_live_copy_fill_states_attempt_count",
        ),
        CheckConstraint(
            "expected_part_count > 0",
            name="ck_live_copy_fill_states_expected_part_count",
        ),
        CheckConstraint(
            "plan_version > 0",
            name="ck_live_copy_fill_states_plan_version",
        ),
        CheckConstraint(
            "not fill_complete or outcome in ('order', 'terminal_skip', 'baseline_ignored')",
            name="ck_live_copy_fill_states_complete_outcome",
        ),
        ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_live_copy_fill_states_account_key_type",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["account_key", "source_wallet"],
            ["live_copy_source_states.account_key", "live_copy_source_states.source_wallet"],
            name="fk_live_copy_fill_states_source_state",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["trading_order_id"],
            ["trading_orders.id"],
            name="fk_live_copy_fill_states_trading_order",
            ondelete="SET NULL",
        ),
        UniqueConstraint(
            "account_key",
            "source_wallet",
            "source_fill_id",
            "sequence_index",
            name="ux_live_copy_fill_states_account_source_fill_sequence",
        ),
        Index(
            "ix_live_copy_fill_states_source_recovery",
            "account_key",
            "source_wallet",
            "fill_complete",
            "next_attempt_at",
            "source_timestamp_ms",
            "source_fill_id",
        ),
        Index(
            "ix_live_copy_fill_states_due",
            "outcome",
            "next_attempt_at",
        ),
        Index("ix_live_copy_fill_states_recent_updated", "updated_at"),
        Index("ix_live_copy_fill_states_trading_order", "trading_order_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'live'"),
    )
    source_wallet: Mapped[str] = mapped_column(Text, nullable=False)
    source_fill_id: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    expected_part_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    plan_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    source_timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_order_direction_rank: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    source_order_position: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
        server_default=text("0"),
    )
    source_order_fill_id_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'"),
    )
    reason: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fill_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    trading_order_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class TradingOrderDispatch(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "trading_order_dispatches"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'dispatching', 'uncertain', 'completed', 'canceled')",
            name="ck_trading_order_dispatches_status",
        ),
        CheckConstraint(
            "attempt_number > 0",
            name="ck_trading_order_dispatches_attempt_number",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_trading_order_dispatches_attempt_count",
        ),
        CheckConstraint(
            "status_lookup_count >= 0",
            name="ck_trading_order_dispatches_status_lookup_count",
        ),
        UniqueConstraint(
            "order_id",
            "attempt_number",
            name="ux_trading_order_dispatches_order_attempt",
        ),
        UniqueConstraint(
            "client_order_id",
            name="ux_trading_order_dispatches_client_order_id",
        ),
        Index(
            "ix_trading_order_dispatches_status_available",
            "status",
            "available_at",
        ),
        Index(
            "ix_trading_order_dispatches_account_created",
            "account_key",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trading_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    client_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dispatch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    exchange_status: Mapped[str | None] = mapped_column(Text)
    exchange_error_code: Mapped[str | None] = mapped_column(Text)
    exchange_error_message: Mapped[str | None] = mapped_column(Text)
    exchange_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status_lookup_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    last_status_lookup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_lookup_error: Mapped[str | None] = mapped_column(Text)
    last_status_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class TradingCloseAllOperation(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "trading_close_all_operations"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'running', 'partially_completed', 'completed', 'failed')",
            name="ck_trading_close_all_operations_status",
        ),
        Index(
            "ix_trading_close_all_operations_account_created",
            "account_key",
            "created_at",
        ),
        Index("ix_trading_close_all_operations_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_key: Mapped[str] = mapped_column(
        Text,
        ForeignKey("trading_accounts.key", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class TradingCloseAllItem(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "trading_close_all_items"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'submitting', 'uncertain', 'completed', 'failed', 'skipped')",
            name="ck_trading_close_all_items_status",
        ),
        UniqueConstraint(
            "operation_id",
            "position_id",
            name="ux_trading_close_all_items_operation_position",
        ),
        Index("ix_trading_close_all_items_operation", "operation_id"),
        Index("ix_trading_close_all_items_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    operation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trading_close_all_operations.id", ondelete="CASCADE"),
        nullable=False,
    )
    position_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trading_orders.id", ondelete="SET NULL"),
    )
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)


class TradingFill(Base, TimestampMixin):
    __tablename__ = "trading_fills"
    __table_args__ = (
        CheckConstraint("account_type in ('paper', 'live')", name="ck_trading_fills_type"),
        ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_trading_fills_account_key_type_trading_accounts",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "action in ('open', 'add', 'reduce', 'close', 'flip_close', 'flip_open')",
            name="ck_trading_fills_action",
        ),
        CheckConstraint("side in ('long', 'short')", name="ck_trading_fills_side"),
        UniqueConstraint("exchange_fill_id", name="ux_trading_fills_exchange_fill_id"),
        Index("ix_trading_fills_account_filled", "account_key", "filled_at"),
        Index("ix_trading_fills_source_filled", "source_wallet", "filled_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trading_orders.id", ondelete="SET NULL"),
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_wallet: Mapped[str] = mapped_column(Text, nullable=False)
    source_fill_id: Mapped[str | None] = mapped_column(Text)
    sequence_index: Mapped[int | None] = mapped_column(Integer)
    exchange_fill_id: Mapped[str | None] = mapped_column(Text)
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    notional_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fee_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    realized_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def is_liquidation(self) -> bool:
        return isinstance(self.raw_payload, dict) and "liquidation" in self.raw_payload


class TradingFundingPayment(Base, TimestampMixin):
    __tablename__ = "trading_funding_payments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_trading_funding_payments_account_key_type_trading_accounts",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "account_type = 'live'",
            name="ck_trading_funding_payments_live",
        ),
        UniqueConstraint(
            "account_key",
            "exchange_event_id",
            name="ux_trading_funding_payments_account_event",
        ),
        Index(
            "ix_trading_funding_payments_account_occurred",
            "account_key",
            "occurred_at",
        ),
        Index(
            "ix_trading_funding_payments_account_coin_occurred",
            "account_key",
            "coin",
            "occurred_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'live'")
    )
    exchange_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric)
    position_size: Mapped[Decimal | None] = mapped_column(Numeric)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class TradingAccountCashFlow(Base, TimestampMixin):
    __tablename__ = "trading_account_cash_flows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_trading_account_cash_flows_account_key_type_trading_accounts",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "account_type = 'live'",
            name="ck_trading_account_cash_flows_live",
        ),
        CheckConstraint(
            "amount_usd <> 0",
            name="ck_trading_account_cash_flows_nonzero",
        ),
        UniqueConstraint(
            "account_key",
            "exchange_event_id",
            name="ux_trading_account_cash_flows_account_event",
        ),
        Index(
            "ix_trading_account_cash_flows_account_occurred",
            "account_key",
            "occurred_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'live'")
    )
    exchange_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    flow_type: Mapped[str] = mapped_column(Text, nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fee_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class TradingAccountPerformanceSnapshot(Base, TimestampMixin):
    __tablename__ = "trading_account_performance_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_trading_account_performance_account_type",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "account_type = 'live'",
            name="ck_trading_account_performance_snapshots_live",
        ),
        CheckConstraint(
            "equity_usd >= 0",
            name="ck_trading_account_performance_snapshots_equity",
        ),
        CheckConstraint(
            "performance_index >= 0",
            name="ck_trading_account_performance_snapshots_index",
        ),
        UniqueConstraint(
            "account_key",
            "recorded_at",
            name="ux_trading_account_performance_snapshots_account_recorded",
        ),
        Index(
            "ix_trading_account_performance_snapshots_account_recorded",
            "account_key",
            "recorded_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'live'")
    )
    equity_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    period_external_flow_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    net_external_flows_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    trading_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    period_return_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    time_weighted_return_pct: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    performance_index: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("1")
    )
    tracking_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_baseline: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class PaperTradingAccount(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "paper_trading_accounts"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    starting_balance_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    cash_balance_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    equity_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    realized_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    fee_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    config_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class PaperCopyAllocation(Base, UpdatedAtMixin):
    __tablename__ = "paper_copy_allocations"
    __table_args__ = (
        UniqueConstraint(
            "account_key",
            "source_wallet",
            name="ux_paper_copy_allocations_account_source",
        ),
        Index("ix_paper_copy_allocations_account_rank", "account_key", "rank"),
        Index("ix_paper_copy_allocations_source", "source_wallet"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_wallet: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric)
    allocation_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    allocation_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    max_total_allocation_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class PaperPosition(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "paper_positions"
    __table_args__ = (
        CheckConstraint("side in ('long', 'short')", name="ck_paper_positions_side"),
        UniqueConstraint(
            "account_key",
            "source_wallet",
            "coin",
            name="ux_paper_positions_account_source_coin",
        ),
        Index("ix_paper_positions_account", "account_key"),
        Index("ix_paper_positions_source", "source_wallet"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_wallet: Mapped[str] = mapped_column(Text, nullable=False)
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    notional_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    leverage: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("1"))
    margin_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    realized_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    fee_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperCopyFill(Base, TimestampMixin):
    __tablename__ = "paper_copy_fills"
    __table_args__ = (
        CheckConstraint(
            "action in ('open', 'add', 'reduce', 'close', 'flip_close', 'flip_open', 'skip')",
            name="ck_paper_copy_fills_action",
        ),
        CheckConstraint("side in ('long', 'short')", name="ck_paper_copy_fills_side"),
        UniqueConstraint(
            "account_key",
            "source_wallet",
            "source_fill_id",
            "sequence_index",
            name="ux_paper_copy_fills_account_source_fill_sequence",
        ),
        Index("ix_paper_copy_fills_account_filled", "account_key", "filled_at"),
        Index("ix_paper_copy_fills_source_filled", "source_wallet", "filled_at"),
        Index("ix_paper_copy_fills_skipped_reason", "skipped_reason"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_wallet: Mapped[str] = mapped_column(Text, nullable=False)
    source_fill_id: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Numeric)
    size: Mapped[Decimal | None] = mapped_column(Numeric)
    notional_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    leverage: Mapped[Decimal | None] = mapped_column(Numeric)
    margin_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    fee_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    realized_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    source_price: Mapped[Decimal | None] = mapped_column(Numeric)
    source_size: Mapped[Decimal | None] = mapped_column(Numeric)
    source_notional_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    source_perp_equity_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    source_exposure_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    allocation_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    allocation_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    skipped_reason: Mapped[str | None] = mapped_column(Text)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    @property
    def source_account_value_usd(self) -> Decimal | None:
        return self.source_perp_equity_usd

    @source_account_value_usd.setter
    def source_account_value_usd(self, value: Decimal | None) -> None:
        self.source_perp_equity_usd = value


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_actor_created", "actor", "created_at"),
        Index("ix_audit_logs_action_created", "action", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
