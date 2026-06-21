"""ensure wallet fill notional column

Revision ID: d3f5a7c9e1b2
Revises: b4e6c8a2d9f1
Create Date: 2026-06-22 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d3f5a7c9e1b2"
down_revision: str | None = "b4e6c8a2d9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("alter table wallet_fills add column if not exists notional_usd numeric")
    op.execute(
        """
        update wallet_fills
        set notional_usd = price * size
        where notional_usd is null
          and price is not null
          and size is not null
        """
    )
    op.execute("update source_trade_sync_states set fill_count = -1")


def downgrade() -> None:
    pass
