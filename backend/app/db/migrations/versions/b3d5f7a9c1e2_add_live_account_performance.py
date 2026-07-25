"""add live account cash flow and performance ledgers

Revision ID: b3d5f7a9c1e2
Revises: a2c4e6f8b0d1
Create Date: 2026-07-25 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3d5f7a9c1e2"
down_revision: str | None = "a2c4e6f8b0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_account_cash_flows",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column(
            "account_type",
            sa.Text(),
            server_default=sa.text("'live'"),
            nullable=False,
        ),
        sa.Column("exchange_event_id", sa.Text(), nullable=False),
        sa.Column("flow_type", sa.Text(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(), nullable=False),
        sa.Column(
            "fee_usd",
            sa.Numeric(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "account_type = 'live'",
            name="ck_trading_account_cash_flows_live",
        ),
        sa.CheckConstraint(
            "amount_usd <> 0",
            name="ck_trading_account_cash_flows_nonzero",
        ),
        sa.ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_trading_account_cash_flows_account_key_type_trading_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_key",
            "exchange_event_id",
            name="ux_trading_account_cash_flows_account_event",
        ),
    )
    op.create_index(
        "ix_trading_account_cash_flows_account_occurred",
        "trading_account_cash_flows",
        ["account_key", "occurred_at"],
    )

    op.create_table(
        "trading_account_performance_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column(
            "account_type",
            sa.Text(),
            server_default=sa.text("'live'"),
            nullable=False,
        ),
        sa.Column("equity_usd", sa.Numeric(), nullable=False),
        sa.Column(
            "period_external_flow_usd",
            sa.Numeric(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "net_external_flows_usd",
            sa.Numeric(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "trading_pnl_usd",
            sa.Numeric(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("period_return_pct", sa.Numeric()),
        sa.Column(
            "time_weighted_return_pct",
            sa.Numeric(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "performance_index",
            sa.Numeric(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("tracking_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_baseline",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "account_type = 'live'",
            name="ck_trading_account_performance_snapshots_live",
        ),
        sa.CheckConstraint(
            "equity_usd >= 0",
            name="ck_trading_account_performance_snapshots_equity",
        ),
        sa.CheckConstraint(
            "performance_index >= 0",
            name="ck_trading_account_performance_snapshots_index",
        ),
        sa.ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_trading_account_performance_account_type",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_key",
            "recorded_at",
            name="ux_trading_account_performance_snapshots_account_recorded",
        ),
    )
    op.create_index(
        "ix_trading_account_performance_snapshots_account_recorded",
        "trading_account_performance_snapshots",
        ["account_key", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trading_account_performance_snapshots_account_recorded",
        table_name="trading_account_performance_snapshots",
    )
    op.drop_table("trading_account_performance_snapshots")
    op.drop_index(
        "ix_trading_account_cash_flows_account_occurred",
        table_name="trading_account_cash_flows",
    )
    op.drop_table("trading_account_cash_flows")
