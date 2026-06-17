from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
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
            "polling_tier in ('pool', 'candidate', 'active', 'exit_only', 'cooldown')",
            name="ck_watched_wallets_polling_tier",
        ),
        Index("ix_watched_wallets_enabled_eligible", "enabled", "eligible"),
        Index("ix_watched_wallets_polling_tier", "polling_tier"),
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
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    ingest_latency_ms: Mapped[int | None] = mapped_column(Integer)
    is_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


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
    entry_signal_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    exit_signal_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
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
    copy_trade_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
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


class SourceTradeSyncState(Base):
    __tablename__ = "source_trade_sync_states"

    wallet_address: Mapped[str] = mapped_column(Text, primary_key=True)
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
    candidate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
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
            "account_role in ('master', 'subaccount', 'unknown')",
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
