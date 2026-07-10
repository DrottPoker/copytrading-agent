"""enforce cleanup relationships

Revision ID: d4f9a2b7c6e1
Revises: c3e8a1f5d7b2
Create Date: 2026-07-09 23:45:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4f9a2b7c6e1"
down_revision: str | None = "c3e8a1f5d7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        delete from source_trade_links link
        where not exists (
          select 1
          from copy_trades trade
          where trade.id = link.copy_trade_id
        )
        """
    )
    op.execute(
        """
        update copy_trades trade
        set entry_signal_id = null
        where entry_signal_id is not null
          and not exists (
            select 1
            from copy_signals signal
            where signal.id = trade.entry_signal_id
          )
        """
    )
    op.execute(
        """
        update copy_trades trade
        set exit_signal_id = null
        where exit_signal_id is not null
          and not exists (
            select 1
            from copy_signals signal
            where signal.id = trade.exit_signal_id
          )
        """
    )
    op.execute(
        """
        update trading_fills fill
        set order_id = null
        where order_id is not null
          and not exists (
            select 1
            from trading_orders orders
            where orders.id = fill.order_id
          )
        """
    )

    op.create_foreign_key(
        "fk_copy_trades_entry_signal_id_copy_signals",
        "copy_trades",
        "copy_signals",
        ["entry_signal_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_copy_trades_exit_signal_id_copy_signals",
        "copy_trades",
        "copy_signals",
        ["exit_signal_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_source_trade_links_copy_trade_id_copy_trades",
        "source_trade_links",
        "copy_trades",
        ["copy_trade_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_trading_fills_order_id_trading_orders",
        "trading_fills",
        "trading_orders",
        ["order_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_trading_fills_order_id_trading_orders",
        "trading_fills",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_source_trade_links_copy_trade_id_copy_trades",
        "source_trade_links",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_copy_trades_exit_signal_id_copy_signals",
        "copy_trades",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_copy_trades_entry_signal_id_copy_signals",
        "copy_trades",
        type_="foreignkey",
    )
