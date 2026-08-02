"""add source trade stream index

Revision ID: f3b7d9a1c5e8
Revises: e1f4a7c9d2b6
Create Date: 2026-08-02 15:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f3b7d9a1c5e8"
down_revision: str | None = "e1f4a7c9d2b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "create index concurrently if not exists "
            "ix_wallet_fills_source_trade_order "
            "on wallet_fills (wallet_address, timestamp_ms, external_fill_id) "
            "where (raw_json->>'dir') in ("
            "'Open Long', 'Close Long', 'Open Short', 'Close Short', "
            "'Long > Short', 'Short > Long'"
            ")"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("drop index concurrently if exists ix_wallet_fills_source_trade_order")
