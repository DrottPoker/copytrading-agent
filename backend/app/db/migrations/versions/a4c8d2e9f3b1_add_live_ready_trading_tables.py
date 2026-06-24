"""add live ready trading tables

Revision ID: a4c8d2e9f3b1
Revises: d3f5a7c9e1b2
Create Date: 2026-06-24 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4c8d2e9f3b1"
down_revision: str | None = "d3f5a7c9e1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_accounts",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("account_type", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'disabled'"), nullable=False),
        sa.Column("network", sa.Text(), server_default=sa.text("'testnet'"), nullable=False),
        sa.Column("wallet_address", sa.Text(), nullable=True),
        sa.Column("vault_address", sa.Text(), nullable=True),
        sa.Column("starting_balance_usd", sa.Numeric(), nullable=True),
        sa.Column("cash_balance_usd", sa.Numeric(), nullable=True),
        sa.Column("equity_usd", sa.Numeric(), nullable=True),
        sa.Column("realized_pnl_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("fee_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("account_type in ('paper', 'live')", name="ck_trading_accounts_type"),
        sa.CheckConstraint(
            "status in ('disabled', 'enabled', 'exit_only')",
            name="ck_trading_accounts_status",
        ),
        sa.CheckConstraint(
            "network in ('mainnet', 'testnet')",
            name="ck_trading_accounts_network",
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        "ix_trading_accounts_type_status",
        "trading_accounts",
        ["account_type", "status"],
    )

    op.create_table(
        "trading_positions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("account_type", sa.Text(), nullable=False),
        sa.Column("source_wallet", sa.Text(), nullable=False),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("size", sa.Numeric(), nullable=False),
        sa.Column("entry_price", sa.Numeric(), nullable=False),
        sa.Column("notional_usd", sa.Numeric(), nullable=False),
        sa.Column("leverage", sa.Numeric(), server_default=sa.text("1"), nullable=False),
        sa.Column("margin_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("realized_pnl_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("fee_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "account_type in ('paper', 'live')",
            name="ck_trading_positions_type",
        ),
        sa.CheckConstraint("side in ('long', 'short')", name="ck_trading_positions_side"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_key",
            "source_wallet",
            "coin",
            name="ux_trading_positions_account_source_coin",
        ),
    )
    op.create_index("ix_trading_positions_account", "trading_positions", ["account_key"])
    op.create_index("ix_trading_positions_source", "trading_positions", ["source_wallet"])

    op.create_table(
        "trading_orders",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("account_type", sa.Text(), nullable=False),
        sa.Column("source_wallet", sa.Text(), nullable=False),
        sa.Column("source_fill_id", sa.Text(), nullable=False),
        sa.Column("sequence_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("client_order_id", sa.Text(), nullable=False),
        sa.Column("exchange_order_id", sa.Text(), nullable=True),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("is_buy", sa.Boolean(), nullable=False),
        sa.Column("reduce_only", sa.Boolean(), nullable=False),
        sa.Column("order_type", sa.Text(), server_default=sa.text("'ioc'"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'planned'"), nullable=False),
        sa.Column("requested_size", sa.Numeric(), nullable=False),
        sa.Column("requested_notional_usd", sa.Numeric(), nullable=False),
        sa.Column("margin_usd", sa.Numeric(), nullable=True),
        sa.Column("leverage", sa.Numeric(), nullable=True),
        sa.Column("limit_price", sa.Numeric(), nullable=True),
        sa.Column("average_fill_price", sa.Numeric(), nullable=True),
        sa.Column("filled_size", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("filled_notional_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("fee_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("account_type in ('paper', 'live')", name="ck_trading_orders_type"),
        sa.CheckConstraint(
            "action in ('open', 'add', 'reduce', 'close', 'flip_close', 'flip_open')",
            name="ck_trading_orders_action",
        ),
        sa.CheckConstraint("side in ('long', 'short')", name="ck_trading_orders_side"),
        sa.CheckConstraint(
            "status in ("
            "'planned', 'submitted', 'accepted', 'rejected', 'partially_filled', "
            "'filled', 'canceled', 'failed'"
            ")",
            name="ck_trading_orders_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_order_id", name="ux_trading_orders_client_order_id"),
        sa.UniqueConstraint(
            "account_key",
            "source_wallet",
            "source_fill_id",
            "sequence_index",
            name="ux_trading_orders_account_source_fill_sequence",
        ),
    )
    op.create_index(
        "ix_trading_orders_account_created",
        "trading_orders",
        ["account_key", "created_at"],
    )
    op.create_index(
        "ix_trading_orders_source_created",
        "trading_orders",
        ["source_wallet", "created_at"],
    )
    op.create_index("ix_trading_orders_status", "trading_orders", ["status"])

    op.create_table(
        "trading_fills",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("account_type", sa.Text(), nullable=False),
        sa.Column("source_wallet", sa.Text(), nullable=False),
        sa.Column("source_fill_id", sa.Text(), nullable=True),
        sa.Column("sequence_index", sa.Integer(), nullable=True),
        sa.Column("exchange_fill_id", sa.Text(), nullable=True),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("size", sa.Numeric(), nullable=False),
        sa.Column("notional_usd", sa.Numeric(), nullable=False),
        sa.Column("fee_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("realized_pnl_usd", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("account_type in ('paper', 'live')", name="ck_trading_fills_type"),
        sa.CheckConstraint(
            "action in ('open', 'add', 'reduce', 'close', 'flip_close', 'flip_open')",
            name="ck_trading_fills_action",
        ),
        sa.CheckConstraint("side in ('long', 'short')", name="ck_trading_fills_side"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exchange_fill_id", name="ux_trading_fills_exchange_fill_id"),
    )
    op.create_index(
        "ix_trading_fills_account_filled",
        "trading_fills",
        ["account_key", "filled_at"],
    )
    op.create_index(
        "ix_trading_fills_source_filled",
        "trading_fills",
        ["source_wallet", "filled_at"],
    )

    op.execute(
        """
        insert into trading_accounts (
            key,
            account_type,
            label,
            status,
            network,
            starting_balance_usd,
            cash_balance_usd,
            equity_usd,
            realized_pnl_usd,
            fee_usd,
            config_payload,
            created_at,
            updated_at
        )
        select
            key,
            'paper',
            label,
            case when enabled then 'enabled' else 'exit_only' end,
            'testnet',
            starting_balance_usd,
            cash_balance_usd,
            equity_usd,
            realized_pnl_usd,
            fee_usd,
            config_payload,
            created_at,
            updated_at
        from paper_trading_accounts
        on conflict (key) do nothing
        """
    )


def downgrade() -> None:
    op.drop_index("ix_trading_fills_source_filled", table_name="trading_fills")
    op.drop_index("ix_trading_fills_account_filled", table_name="trading_fills")
    op.drop_table("trading_fills")
    op.drop_index("ix_trading_orders_status", table_name="trading_orders")
    op.drop_index("ix_trading_orders_source_created", table_name="trading_orders")
    op.drop_index("ix_trading_orders_account_created", table_name="trading_orders")
    op.drop_table("trading_orders")
    op.drop_index("ix_trading_positions_source", table_name="trading_positions")
    op.drop_index("ix_trading_positions_account", table_name="trading_positions")
    op.drop_table("trading_positions")
    op.drop_index("ix_trading_accounts_type_status", table_name="trading_accounts")
    op.drop_table("trading_accounts")
