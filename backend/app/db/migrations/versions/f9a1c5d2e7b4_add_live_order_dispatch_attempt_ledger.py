"""add live order dispatch attempt ledger

Revision ID: f9a1c5d2e7b4
Revises: e3b7f9d8c4a1
Create Date: 2026-07-21 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f9a1c5d2e7b4"
down_revision: str | None = "e3b7f9d8c4a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ux_trading_order_dispatches_order",
        "trading_order_dispatches",
        type_="unique",
    )
    op.add_column(
        "trading_order_dispatches",
        sa.Column("attempt_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column("trading_order_dispatches", sa.Column("exchange_status", sa.Text()))
    op.add_column("trading_order_dispatches", sa.Column("exchange_error_code", sa.Text()))
    op.add_column("trading_order_dispatches", sa.Column("exchange_error_message", sa.Text()))
    op.add_column(
        "trading_order_dispatches",
        sa.Column("exchange_response", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.add_column(
        "trading_order_dispatches",
        sa.Column("status_lookup_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "trading_order_dispatches",
        sa.Column("last_status_lookup_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "trading_order_dispatches",
        sa.Column("last_status_lookup_error", sa.Text()),
    )
    op.add_column(
        "trading_order_dispatches",
        sa.Column("last_status_response", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.create_check_constraint(
        "ck_trading_order_dispatches_attempt_number",
        "trading_order_dispatches",
        "attempt_number > 0",
    )
    op.create_check_constraint(
        "ck_trading_order_dispatches_attempt_count",
        "trading_order_dispatches",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_trading_order_dispatches_status_lookup_count",
        "trading_order_dispatches",
        "status_lookup_count >= 0",
    )
    op.create_unique_constraint(
        "ux_trading_order_dispatches_order_attempt",
        "trading_order_dispatches",
        ["order_id", "attempt_number"],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM trading_order_dispatches WHERE attempt_number > 1"))
    op.drop_constraint(
        "ux_trading_order_dispatches_order_attempt",
        "trading_order_dispatches",
        type_="unique",
    )
    op.drop_constraint(
        "ck_trading_order_dispatches_attempt_count",
        "trading_order_dispatches",
        type_="check",
    )
    op.drop_constraint(
        "ck_trading_order_dispatches_status_lookup_count",
        "trading_order_dispatches",
        type_="check",
    )
    op.drop_constraint(
        "ck_trading_order_dispatches_attempt_number",
        "trading_order_dispatches",
        type_="check",
    )
    op.drop_column("trading_order_dispatches", "last_status_response")
    op.drop_column("trading_order_dispatches", "last_status_lookup_error")
    op.drop_column("trading_order_dispatches", "last_status_lookup_at")
    op.drop_column("trading_order_dispatches", "status_lookup_count")
    op.drop_column("trading_order_dispatches", "exchange_response")
    op.drop_column("trading_order_dispatches", "exchange_error_message")
    op.drop_column("trading_order_dispatches", "exchange_error_code")
    op.drop_column("trading_order_dispatches", "exchange_status")
    op.drop_column("trading_order_dispatches", "attempt_number")
    op.create_unique_constraint(
        "ux_trading_order_dispatches_order",
        "trading_order_dispatches",
        ["order_id"],
    )
