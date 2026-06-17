"""add current drawdown to wallet scores

Revision ID: f4b9a2c8d1e5
Revises: e7a1c9d4f6b2
Create Date: 2026-06-17 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4b9a2c8d1e5"
down_revision: str | None = "e7a1c9d4f6b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("wallet_scores", sa.Column("current_drawdown_pct", sa.Numeric()))


def downgrade() -> None:
    op.drop_column("wallet_scores", "current_drawdown_pct")
