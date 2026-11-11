"""add loss limit restart time to trading accounts

Revision ID: d2a4c6e8f0b3
Revises: c5e7a9b1d3f6
Create Date: 2026-11-12 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2a4c6e8f0b3"
down_revision: str | None = "c5e7a9b1d3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Starting an account whose loss limit is breached restarts the limits from
    # that moment, so the same old loss cannot stop the account again.
    for table in ("trading_accounts", "paper_trading_accounts"):
        op.add_column(
            table,
            sa.Column("loss_limits_reset_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    for table in ("paper_trading_accounts", "trading_accounts"):
        op.drop_column(table, "loss_limits_reset_at")
