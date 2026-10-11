"""cache account modes and subaccount checks for maintenance jobs

Revision ID: b7c3e5a9d1f2
Revises: e8a2c4f6d1b3
Create Date: 2026-10-11 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c3e5a9d1f2"
down_revision: str | None = "e8a2c4f6d1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Scoring reuses the account mode instead of a heavy request per wallet and run.
    op.add_column("watched_wallets", sa.Column("user_abstraction", sa.Text(), nullable=True))
    op.add_column(
        "watched_wallets",
        sa.Column("user_abstraction_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Discovery lists a leaderboard master's subaccounts again only after an interval.
    op.create_table(
        "discovery_subaccount_checks",
        sa.Column("master_address", sa.Text(), nullable=False),
        sa.Column("subaccount_count", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("master_address"),
    )


def downgrade() -> None:
    op.drop_table("discovery_subaccount_checks")
    op.drop_column("watched_wallets", "user_abstraction_checked_at")
    op.drop_column("watched_wallets", "user_abstraction")
