"""add source trade liquidation flags

Revision ID: b4e6c8a2d9f1
Revises: a9d2c4e7f1b8
Create Date: 2026-06-22 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e6c8a2d9f1"
down_revision: str | None = "a9d2c4e7f1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_trades",
        sa.Column("has_liquidation", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "source_trades",
        sa.Column(
            "liquidation_fill_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "source_trades",
        sa.Column(
            "liquidation_notional_usd",
            sa.Numeric(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.execute("update source_trade_sync_states set fill_count = -1")


def downgrade() -> None:
    op.drop_column("source_trades", "liquidation_notional_usd")
    op.drop_column("source_trades", "liquidation_fill_count")
    op.drop_column("source_trades", "has_liquidation")
