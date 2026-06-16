"""add discovery backfill metrics

Revision ID: b2c41d9e7a63
Revises: 8e54f3a7c2b1
Create Date: 2026-06-15 18:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c41d9e7a63"
down_revision: str | None = "8e54f3a7c2b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "discovery_wallet_candidates",
        sa.Column(
            "backfill_status",
            sa.Text(),
            server_default=sa.text("'not_started'"),
            nullable=False,
        ),
    )
    op.add_column("discovery_wallet_candidates", sa.Column("backfill_error", sa.Text()))
    op.add_column(
        "discovery_wallet_candidates",
        sa.Column("last_backfilled_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "discovery_wallet_candidates",
        sa.Column(
            "backfill_fetched_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "discovery_wallet_candidates",
        sa.Column(
            "backfill_inserted_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "discovery_wallet_candidates",
        sa.Column(
            "backfill_duplicate_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "discovery_wallet_candidates",
        sa.Column("fill_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "discovery_wallet_candidates",
        sa.Column("closed_trade_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "discovery_wallet_candidates",
        sa.Column("open_trade_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "discovery_wallet_candidates",
        sa.Column("ignored_fill_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("discovery_wallet_candidates", sa.Column("net_pnl_usd", sa.Numeric()))
    op.add_column("discovery_wallet_candidates", sa.Column("profit_factor", sa.Numeric()))
    op.add_column("discovery_wallet_candidates", sa.Column("win_rate", sa.Numeric()))
    op.add_column("discovery_wallet_candidates", sa.Column("max_drawdown_pct", sa.Numeric()))
    op.add_column(
        "discovery_wallet_candidates",
        sa.Column("average_trade_notional_usd", sa.Numeric()),
    )
    op.add_column("discovery_wallet_candidates", sa.Column("last_trade_time_ms", sa.BigInteger()))
    op.create_check_constraint(
        "ck_discovery_wallet_candidates_backfill_status",
        "discovery_wallet_candidates",
        "backfill_status in ('not_started', 'running', 'succeeded', 'failed')",
    )
    op.create_index(
        "ix_discovery_candidates_backfill_status",
        "discovery_wallet_candidates",
        ["backfill_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_discovery_candidates_backfill_status",
        table_name="discovery_wallet_candidates",
    )
    op.drop_constraint(
        "ck_discovery_wallet_candidates_backfill_status",
        "discovery_wallet_candidates",
        type_="check",
    )
    op.drop_column("discovery_wallet_candidates", "last_trade_time_ms")
    op.drop_column("discovery_wallet_candidates", "average_trade_notional_usd")
    op.drop_column("discovery_wallet_candidates", "max_drawdown_pct")
    op.drop_column("discovery_wallet_candidates", "win_rate")
    op.drop_column("discovery_wallet_candidates", "profit_factor")
    op.drop_column("discovery_wallet_candidates", "net_pnl_usd")
    op.drop_column("discovery_wallet_candidates", "ignored_fill_count")
    op.drop_column("discovery_wallet_candidates", "open_trade_count")
    op.drop_column("discovery_wallet_candidates", "closed_trade_count")
    op.drop_column("discovery_wallet_candidates", "fill_count")
    op.drop_column("discovery_wallet_candidates", "backfill_duplicate_count")
    op.drop_column("discovery_wallet_candidates", "backfill_inserted_count")
    op.drop_column("discovery_wallet_candidates", "backfill_fetched_count")
    op.drop_column("discovery_wallet_candidates", "last_backfilled_at")
    op.drop_column("discovery_wallet_candidates", "backfill_error")
    op.drop_column("discovery_wallet_candidates", "backfill_status")
