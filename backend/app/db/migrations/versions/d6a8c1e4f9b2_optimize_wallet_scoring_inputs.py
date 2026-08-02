"""optimize wallet scoring inputs

Revision ID: d6a8c1e4f9b2
Revises: b3d5f7a9c1e2
Create Date: 2026-08-02 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6a8c1e4f9b2"
down_revision: str | None = "b3d5f7a9c1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "watched_wallets",
        sa.Column(
            "fill_revision",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "source_trade_sync_states",
        sa.Column(
            "fill_revision",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )

    op.execute(
        """
        update watched_wallets ww
        set fill_revision = fill_state.fill_count
        from (
          select wallet_address, count(*)::bigint as fill_count
          from wallet_fills
          group by wallet_address
        ) fill_state
        where ww.address = fill_state.wallet_address
        """
    )
    op.execute(
        """
        update source_trade_sync_states
        set fill_revision = fill_count::bigint
        """
    )

    op.execute(
        """
        create function bump_wallet_fill_revision_after_insert()
        returns trigger
        language plpgsql
        as $$
        begin
          update watched_wallets ww
          set fill_revision = ww.fill_revision + inserted.fill_count
          from (
            select wallet_address, count(*)::bigint as fill_count
            from inserted_wallet_fills
            group by wallet_address
          ) inserted
          where ww.address = inserted.wallet_address;
          return null;
        end;
        $$
        """
    )
    op.execute(
        """
        create trigger trg_wallet_fills_revision_insert
        after insert on wallet_fills
        referencing new table as inserted_wallet_fills
        for each statement
        execute function bump_wallet_fill_revision_after_insert()
        """
    )
    op.execute(
        """
        create function bump_wallet_fill_revision_after_delete()
        returns trigger
        language plpgsql
        as $$
        begin
          update watched_wallets ww
          set fill_revision = ww.fill_revision + deleted.fill_count
          from (
            select wallet_address, count(*)::bigint as fill_count
            from deleted_wallet_fills
            group by wallet_address
          ) deleted
          where ww.address = deleted.wallet_address;
          return null;
        end;
        $$
        """
    )
    op.execute(
        """
        create trigger trg_wallet_fills_revision_delete
        after delete on wallet_fills
        referencing old table as deleted_wallet_fills
        for each statement
        execute function bump_wallet_fill_revision_after_delete()
        """
    )


def downgrade() -> None:
    op.execute("drop trigger if exists trg_wallet_fills_revision_delete on wallet_fills")
    op.execute("drop function if exists bump_wallet_fill_revision_after_delete()")
    op.execute("drop trigger if exists trg_wallet_fills_revision_insert on wallet_fills")
    op.execute("drop function if exists bump_wallet_fill_revision_after_insert()")
    op.drop_column("source_trade_sync_states", "fill_revision")
    op.drop_column("watched_wallets", "fill_revision")
