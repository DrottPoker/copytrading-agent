"""add paper trading tables

Revision ID: d8c2f4a1b7e3
Revises: c6f49a8d2e11
Create Date: 2026-06-16 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8c2f4a1b7e3"
down_revision: str | None = "c6f49a8d2e11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_trading_accounts",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("starting_balance_usd", sa.Numeric(), nullable=False),
        sa.Column("cash_balance_usd", sa.Numeric(), nullable=False),
        sa.Column("equity_usd", sa.Numeric(), nullable=False),
        sa.Column("realized_pnl_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("fee_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("config_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "paper_copy_allocations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("source_wallet", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(), nullable=True),
        sa.Column("allocation_pct", sa.Numeric(), nullable=False),
        sa.Column("allocation_usd", sa.Numeric(), nullable=False),
        sa.Column("max_total_allocation_pct", sa.Numeric(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_key",
            "source_wallet",
            name="ux_paper_copy_allocations_account_source",
        ),
    )
    op.create_index(
        "ix_paper_copy_allocations_account_rank",
        "paper_copy_allocations",
        ["account_key", "rank"],
    )
    op.create_index(
        "ix_paper_copy_allocations_source",
        "paper_copy_allocations",
        ["source_wallet"],
    )

    op.create_table(
        "paper_positions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("source_wallet", sa.Text(), nullable=False),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("size", sa.Numeric(), nullable=False),
        sa.Column("entry_price", sa.Numeric(), nullable=False),
        sa.Column("notional_usd", sa.Numeric(), nullable=False),
        sa.Column("realized_pnl_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("fee_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("side in ('long', 'short')", name="ck_paper_positions_side"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_key",
            "source_wallet",
            "coin",
            name="ux_paper_positions_account_source_coin",
        ),
    )
    op.create_index("ix_paper_positions_account", "paper_positions", ["account_key"])
    op.create_index("ix_paper_positions_source", "paper_positions", ["source_wallet"])

    op.create_table(
        "paper_copy_fills",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("source_wallet", sa.Text(), nullable=False),
        sa.Column("source_fill_id", sa.Text(), nullable=False),
        sa.Column("sequence_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(), nullable=True),
        sa.Column("size", sa.Numeric(), nullable=True),
        sa.Column("notional_usd", sa.Numeric(), nullable=True),
        sa.Column("fee_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("realized_pnl_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("source_price", sa.Numeric(), nullable=True),
        sa.Column("source_size", sa.Numeric(), nullable=True),
        sa.Column("source_notional_usd", sa.Numeric(), nullable=True),
        sa.Column("source_account_value_usd", sa.Numeric(), nullable=True),
        sa.Column("source_exposure_pct", sa.Numeric(), nullable=True),
        sa.Column("allocation_pct", sa.Numeric(), nullable=True),
        sa.Column("allocation_usd", sa.Numeric(), nullable=True),
        sa.Column("skipped_reason", sa.Text(), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action in ('open', 'add', 'reduce', 'close', 'flip_close', 'flip_open', 'skip')",
            name="ck_paper_copy_fills_action",
        ),
        sa.CheckConstraint("side in ('long', 'short')", name="ck_paper_copy_fills_side"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_key",
            "source_wallet",
            "source_fill_id",
            "sequence_index",
            name="ux_paper_copy_fills_account_source_fill_sequence",
        ),
    )
    op.create_index(
        "ix_paper_copy_fills_account_filled",
        "paper_copy_fills",
        ["account_key", "filled_at"],
    )
    op.create_index(
        "ix_paper_copy_fills_source_filled",
        "paper_copy_fills",
        ["source_wallet", "filled_at"],
    )
    op.create_index(
        "ix_paper_copy_fills_skipped_reason",
        "paper_copy_fills",
        ["skipped_reason"],
    )


def downgrade() -> None:
    op.drop_index("ix_paper_copy_fills_skipped_reason", table_name="paper_copy_fills")
    op.drop_index("ix_paper_copy_fills_source_filled", table_name="paper_copy_fills")
    op.drop_index("ix_paper_copy_fills_account_filled", table_name="paper_copy_fills")
    op.drop_table("paper_copy_fills")
    op.drop_index("ix_paper_positions_source", table_name="paper_positions")
    op.drop_index("ix_paper_positions_account", table_name="paper_positions")
    op.drop_table("paper_positions")
    op.drop_index("ix_paper_copy_allocations_source", table_name="paper_copy_allocations")
    op.drop_index(
        "ix_paper_copy_allocations_account_rank",
        table_name="paper_copy_allocations",
    )
    op.drop_table("paper_copy_allocations")
    op.drop_table("paper_trading_accounts")
