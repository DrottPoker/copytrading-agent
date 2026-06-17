"""add open position stress to wallet scores

Revision ID: c9f4a1b7e2d8
Revises: b6e2c4f9a8d1
Create Date: 2026-06-17 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9f4a1b7e2d8"
down_revision: str | None = "b6e2c4f9a8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("wallet_scores", sa.Column("open_position_stress_pct", sa.Numeric()))


def downgrade() -> None:
    op.drop_column("wallet_scores", "open_position_stress_pct")
