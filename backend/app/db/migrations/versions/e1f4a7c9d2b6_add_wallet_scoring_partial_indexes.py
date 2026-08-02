"""add wallet scoring partial indexes

Revision ID: e1f4a7c9d2b6
Revises: d6a8c1e4f9b2
Create Date: 2026-08-02 14:10:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e1f4a7c9d2b6"
down_revision: str | None = "d6a8c1e4f9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "create index concurrently if not exists "
            "ix_wallet_fills_liquidation_wallet_timestamp "
            "on wallet_fills (wallet_address, timestamp_ms, id) "
            "where raw_json ? 'liquidation'"
        )
        op.execute(
            "create index concurrently if not exists "
            "ix_wallet_fills_nonliquidation_wallet_timestamp "
            "on wallet_fills (wallet_address, timestamp_ms) "
            "where not (raw_json ? 'liquidation')"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "drop index concurrently if exists ix_wallet_fills_nonliquidation_wallet_timestamp"
        )
        op.execute("drop index concurrently if exists ix_wallet_fills_liquidation_wallet_timestamp")
