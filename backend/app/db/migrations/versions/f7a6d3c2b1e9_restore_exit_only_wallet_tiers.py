"""restore paper exit only wallet tiers

Revision ID: f7a6d3c2b1e9
Revises: e2b4a6c9d8f0
Create Date: 2026-06-18 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f7a6d3c2b1e9"
down_revision: str | None = "e2b4a6c9d8f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        update watched_wallets
        set polling_tier = 'pool'
        where polling_tier = 'exit_only'
        """
    )
    op.drop_constraint(
        "ck_watched_wallets_polling_tier",
        "watched_wallets",
        type_="check",
    )
    op.create_check_constraint(
        "ck_watched_wallets_polling_tier",
        "watched_wallets",
        "polling_tier in ('pool', 'candidate', 'active', 'cooldown')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_watched_wallets_polling_tier",
        "watched_wallets",
        type_="check",
    )
    op.create_check_constraint(
        "ck_watched_wallets_polling_tier",
        "watched_wallets",
        "polling_tier in ('pool', 'candidate', 'active', 'exit_only', 'cooldown')",
    )
