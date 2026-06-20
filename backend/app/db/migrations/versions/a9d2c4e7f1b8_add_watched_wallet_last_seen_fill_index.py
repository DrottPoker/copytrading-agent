"""add watched wallet last seen fill index

Revision ID: a9d2c4e7f1b8
Revises: f8b5c7d1a2e4
Create Date: 2026-06-21 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a9d2c4e7f1b8"
down_revision: str | None = "f8b5c7d1a2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_watched_wallets_last_seen_fill_at",
        "watched_wallets",
        ["last_seen_fill_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_watched_wallets_last_seen_fill_at",
        table_name="watched_wallets",
    )
