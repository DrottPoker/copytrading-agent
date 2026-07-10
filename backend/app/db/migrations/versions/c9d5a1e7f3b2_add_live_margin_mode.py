"""add live margin mode

Revision ID: c9d5a1e7f3b2
Revises: b8e4f0a2d6c1
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d5a1e7f3b2"
down_revision: str | None = "b8e4f0a2d6c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trading_positions",
        sa.Column(
            "margin_mode",
            sa.Text(),
            server_default=sa.text("'cross'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_trading_positions_margin_mode",
        "trading_positions",
        "margin_mode in ('cross', 'isolated')",
    )
    op.add_column(
        "trading_orders",
        sa.Column(
            "margin_mode",
            sa.Text(),
            server_default=sa.text("'cross'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_trading_orders_margin_mode",
        "trading_orders",
        "margin_mode in ('cross', 'isolated')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_trading_orders_margin_mode",
        "trading_orders",
        type_="check",
    )
    op.drop_column("trading_orders", "margin_mode")
    op.drop_constraint(
        "ck_trading_positions_margin_mode",
        "trading_positions",
        type_="check",
    )
    op.drop_column("trading_positions", "margin_mode")
