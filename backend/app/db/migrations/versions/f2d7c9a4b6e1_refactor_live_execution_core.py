"""refactor live execution core

Revision ID: f2d7c9a4b6e1
Revises: fc4b9d8e2a11
Create Date: 2026-07-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2d7c9a4b6e1"
down_revision: str | None = "fc4b9d8e2a11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_trading_orders_status", "trading_orders", type_="check")
    op.create_check_constraint(
        "ck_trading_orders_status",
        "trading_orders",
        "status in ("
        "'planned', 'ready', 'submitting', 'uncertain', 'submitted', 'accepted', "
        "'rejected', 'partially_filled', 'filled', 'canceled', 'failed'"
        ")",
    )

    op.create_table(
        "trading_order_dispatches",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("client_order_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            "status in ('pending', 'dispatching', 'uncertain', 'completed', 'canceled')",
            name="ck_trading_order_dispatches_status",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["trading_orders.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "client_order_id",
            name="ux_trading_order_dispatches_client_order_id",
        ),
        sa.UniqueConstraint("order_id", name="ux_trading_order_dispatches_order"),
    )
    op.create_index(
        "ix_trading_order_dispatches_status_available",
        "trading_order_dispatches",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_trading_order_dispatches_account_created",
        "trading_order_dispatches",
        ["account_key", "created_at"],
    )

    op.create_table(
        "trading_close_all_operations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            "status in ('pending', 'running', 'partially_completed', 'completed', 'failed')",
            name="ck_trading_close_all_operations_status",
        ),
        sa.ForeignKeyConstraint(
            ["account_key"],
            ["trading_accounts.key"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trading_close_all_operations_account_created",
        "trading_close_all_operations",
        ["account_key", "created_at"],
    )
    op.create_index(
        "ix_trading_close_all_operations_status",
        "trading_close_all_operations",
        ["status"],
    )

    op.create_table(
        "trading_close_all_items",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("operation_id", sa.UUID(), nullable=False),
        sa.Column("position_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
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
            "status in ('pending', 'submitting', 'uncertain', 'completed', 'failed', 'skipped')",
            name="ck_trading_close_all_items_status",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["trading_close_all_operations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["trading_orders.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id",
            "position_id",
            name="ux_trading_close_all_items_operation_position",
        ),
    )
    op.create_index(
        "ix_trading_close_all_items_operation",
        "trading_close_all_items",
        ["operation_id"],
    )
    op.create_index(
        "ix_trading_close_all_items_status",
        "trading_close_all_items",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_trading_close_all_items_status", table_name="trading_close_all_items")
    op.drop_index("ix_trading_close_all_items_operation", table_name="trading_close_all_items")
    op.drop_table("trading_close_all_items")
    op.drop_index(
        "ix_trading_close_all_operations_status",
        table_name="trading_close_all_operations",
    )
    op.drop_index(
        "ix_trading_close_all_operations_account_created",
        table_name="trading_close_all_operations",
    )
    op.drop_table("trading_close_all_operations")
    op.drop_index(
        "ix_trading_order_dispatches_account_created",
        table_name="trading_order_dispatches",
    )
    op.drop_index(
        "ix_trading_order_dispatches_status_available",
        table_name="trading_order_dispatches",
    )
    op.drop_table("trading_order_dispatches")

    op.drop_constraint("ck_trading_orders_status", "trading_orders", type_="check")
    op.create_check_constraint(
        "ck_trading_orders_status",
        "trading_orders",
        "status in ("
        "'planned', 'submitted', 'accepted', 'rejected', 'partially_filled', "
        "'filled', 'canceled', 'failed'"
        ")",
    )
