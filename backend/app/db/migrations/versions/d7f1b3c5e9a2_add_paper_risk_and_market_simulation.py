"""add paper loss limits, funding, liquidation, and margin mode

Revision ID: d7f1b3c5e9a2
Revises: c4e8a2f6b1d3
Create Date: 2026-10-11 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7f1b3c5e9a2"
down_revision: str | None = "c4e8a2f6b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = "action in ('open', 'add', 'reduce', 'close', 'flip_close', 'flip_open', 'skip')"
NEW_ACTIONS = (
    "action in ('open', 'add', 'reduce', 'close', 'flip_close', 'flip_open', 'skip', 'liquidation')"
)


def upgrade() -> None:
    op.add_column(
        "paper_trading_accounts",
        sa.Column("funding_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "paper_trading_accounts",
        sa.Column("exit_only_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("paper_trading_accounts", sa.Column("exit_only_reason", sa.Text(), nullable=True))

    op.add_column(
        "paper_positions",
        sa.Column("margin_mode", sa.Text(), server_default=sa.text("'cross'"), nullable=False),
    )
    op.add_column(
        "paper_positions",
        sa.Column("funding_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint(
        "ck_paper_positions_margin_mode",
        "paper_positions",
        "margin_mode in ('cross', 'isolated')",
    )

    op.drop_constraint("ck_paper_copy_fills_action", "paper_copy_fills", type_="check")
    op.create_check_constraint("ck_paper_copy_fills_action", "paper_copy_fills", NEW_ACTIONS)

    op.create_table(
        "paper_account_equity_snapshots",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("equity_usd", sa.Numeric(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("equity_usd >= 0", name="ck_paper_account_equity_snapshots_equity"),
        sa.ForeignKeyConstraint(
            ["account_key"],
            ["paper_trading_accounts.key"],
            ondelete="CASCADE",
            name="fk_paper_account_equity_snapshots_account",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_paper_account_equity_snapshots_account_recorded",
        "paper_account_equity_snapshots",
        ["account_key", "recorded_at"],
    )

    op.create_table(
        "paper_funding_payments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("position_id", sa.UUID(), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("source_wallet", sa.Text(), nullable=False),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("funding_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("funding_rate", sa.Numeric(), nullable=False),
        sa.Column("size", sa.Numeric(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "position_id",
            "funding_time",
            name="ux_paper_funding_payments_position_time",
        ),
    )
    op.create_index(
        "ix_paper_funding_payments_account_time",
        "paper_funding_payments",
        ["account_key", "funding_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_paper_funding_payments_account_time", table_name="paper_funding_payments")
    op.drop_table("paper_funding_payments")
    op.drop_index(
        "ix_paper_account_equity_snapshots_account_recorded",
        table_name="paper_account_equity_snapshots",
    )
    op.drop_table("paper_account_equity_snapshots")

    op.execute("delete from paper_copy_fills where action = 'liquidation'")
    op.drop_constraint("ck_paper_copy_fills_action", "paper_copy_fills", type_="check")
    op.create_check_constraint("ck_paper_copy_fills_action", "paper_copy_fills", OLD_ACTIONS)

    op.drop_constraint("ck_paper_positions_margin_mode", "paper_positions", type_="check")
    op.drop_column("paper_positions", "funding_usd")
    op.drop_column("paper_positions", "margin_mode")

    op.drop_column("paper_trading_accounts", "exit_only_reason")
    op.drop_column("paper_trading_accounts", "exit_only_at")
    op.drop_column("paper_trading_accounts", "funding_usd")
