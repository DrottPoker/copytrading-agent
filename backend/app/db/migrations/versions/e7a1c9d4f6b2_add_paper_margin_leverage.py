"""add paper margin leverage

Revision ID: e7a1c9d4f6b2
Revises: d8c2f4a1b7e3
Create Date: 2026-06-16 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1c9d4f6b2"
down_revision: str | None = "d8c2f4a1b7e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "paper_positions",
        sa.Column("leverage", sa.Numeric(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "paper_positions",
        sa.Column("margin_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
    )
    op.execute("update paper_positions set margin_usd = notional_usd where margin_usd = 0")

    op.add_column("paper_copy_fills", sa.Column("leverage", sa.Numeric(), nullable=True))
    op.add_column("paper_copy_fills", sa.Column("margin_usd", sa.Numeric(), nullable=True))
    op.execute(
        """
        update paper_copy_fills
        set leverage = 1,
            margin_usd = notional_usd
        where action <> 'skip'
          and notional_usd is not null
        """
    )


def downgrade() -> None:
    op.drop_column("paper_copy_fills", "margin_usd")
    op.drop_column("paper_copy_fills", "leverage")
    op.drop_column("paper_positions", "margin_usd")
    op.drop_column("paper_positions", "leverage")
