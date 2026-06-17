"""materialize source trades and score status

Revision ID: b6e2c4f9a8d1
Revises: a3d7e9c1b2f4
Create Date: 2026-06-17 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6e2c4f9a8d1"
down_revision: str | None = "a3d7e9c1b2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wallet_scores",
        sa.Column(
            "current_drawdown_status",
            sa.Text(),
            server_default=sa.text("'disabled'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_wallet_scores_current_drawdown_status",
        "wallet_scores",
        "current_drawdown_status in ('ok', 'unavailable', 'zero_equity', 'disabled')",
    )

    op.create_table(
        "source_trades",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("trade_key", sa.Text(), nullable=False),
        sa.Column("wallet_address", sa.Text(), nullable=False),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("opened_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("closed_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("entry_size", sa.Numeric(), nullable=False),
        sa.Column("closed_size", sa.Numeric(), nullable=False),
        sa.Column("remaining_size", sa.Numeric(), nullable=False),
        sa.Column("entry_notional_usd", sa.Numeric(), nullable=False),
        sa.Column("close_notional_usd", sa.Numeric(), nullable=False),
        sa.Column("average_entry_price", sa.Numeric(), nullable=True),
        sa.Column("average_exit_price", sa.Numeric(), nullable=True),
        sa.Column("realized_pnl_usd", sa.Numeric(), nullable=False),
        sa.Column("fee_usd", sa.Numeric(), nullable=False),
        sa.Column("net_pnl_usd", sa.Numeric(), nullable=False),
        sa.Column("entry_fill_count", sa.Integer(), nullable=False),
        sa.Column("close_fill_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("side in ('long', 'short')", name="ck_source_trades_side"),
        sa.CheckConstraint("status in ('open', 'closed')", name="ck_source_trades_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_key", name="ux_source_trades_trade_key"),
    )
    op.create_index(
        "ix_source_trades_wallet_closed",
        "source_trades",
        ["wallet_address", "closed_at_ms"],
    )
    op.create_index(
        "ix_source_trades_wallet_coin",
        "source_trades",
        ["wallet_address", "coin"],
    )
    op.create_index(
        "ix_source_trades_wallet_status",
        "source_trades",
        ["wallet_address", "status"],
    )

    op.create_table(
        "source_trade_sync_states",
        sa.Column("wallet_address", sa.Text(), nullable=False),
        sa.Column("fill_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_fill_timestamp_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "unmatched_close_fill_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "preexisting_open_fill_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("wallet_address"),
    )

    op.create_table(
        "source_trade_ignored_fills",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("wallet_address", sa.Text(), nullable=False),
        sa.Column("external_fill_id", sa.Text(), nullable=False),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "reason in ('unmatched_close', 'preexisting_open')",
            name="ck_source_trade_ignored_fills_reason",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "wallet_address",
            "external_fill_id",
            "reason",
            name="ux_source_trade_ignored_fills_wallet_external_reason",
        ),
    )
    op.create_index(
        "ix_source_trade_ignored_fills_reason",
        "source_trade_ignored_fills",
        ["reason"],
    )
    op.create_index(
        "ix_source_trade_ignored_fills_wallet_timestamp",
        "source_trade_ignored_fills",
        ["wallet_address", "timestamp_ms"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_source_trade_ignored_fills_wallet_timestamp",
        table_name="source_trade_ignored_fills",
    )
    op.drop_index(
        "ix_source_trade_ignored_fills_reason",
        table_name="source_trade_ignored_fills",
    )
    op.drop_table("source_trade_ignored_fills")
    op.drop_table("source_trade_sync_states")
    op.drop_index("ix_source_trades_wallet_status", table_name="source_trades")
    op.drop_index("ix_source_trades_wallet_coin", table_name="source_trades")
    op.drop_index("ix_source_trades_wallet_closed", table_name="source_trades")
    op.drop_table("source_trades")
    op.drop_constraint(
        "ck_wallet_scores_current_drawdown_status",
        "wallet_scores",
        type_="check",
    )
    op.drop_column("wallet_scores", "current_drawdown_status")
