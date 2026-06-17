"""rename discovery ignore setting

Revision ID: e2b4a6c9d8f0
Revises: c9f4a1b7e2d8
Create Date: 2026-06-18 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e2b4a6c9d8f0"
down_revision: str | None = "c9f4a1b7e2d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        update settings
        set key = 'discovery_ignored_wallet_addresses'
        where key = 'leaderboard_ignored_wallet_addresses'
          and not exists (
            select 1
            from settings
            where key = 'discovery_ignored_wallet_addresses'
          )
        """
    )
    op.execute(
        """
        delete from settings
        where key = 'leaderboard_ignored_wallet_addresses'
          and exists (
            select 1
            from settings
            where key = 'discovery_ignored_wallet_addresses'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        update settings
        set key = 'leaderboard_ignored_wallet_addresses'
        where key = 'discovery_ignored_wallet_addresses'
          and not exists (
            select 1
            from settings
            where key = 'leaderboard_ignored_wallet_addresses'
          )
        """
    )
    op.execute(
        """
        delete from settings
        where key = 'discovery_ignored_wallet_addresses'
          and exists (
            select 1
            from settings
            where key = 'leaderboard_ignored_wallet_addresses'
          )
        """
    )
