"""add live funding payment ledger

Revision ID: a2c4e6f8b0d1
Revises: f9a1c5d2e7b4
Create Date: 2026-07-21 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a2c4e6f8b0d1"
down_revision: str | None = "f9a1c5d2e7b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_funding_payments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column(
            "account_type",
            sa.Text(),
            server_default=sa.text("'live'"),
            nullable=False,
        ),
        sa.Column("exchange_event_id", sa.Text(), nullable=False),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(), nullable=False),
        sa.Column("funding_rate", sa.Numeric()),
        sa.Column("position_size", sa.Numeric()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "account_type = 'live'",
            name="ck_trading_funding_payments_live",
        ),
        sa.ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_trading_funding_payments_account_key_type_trading_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_key",
            "exchange_event_id",
            name="ux_trading_funding_payments_account_event",
        ),
    )
    op.create_index(
        "ix_trading_funding_payments_account_occurred",
        "trading_funding_payments",
        ["account_key", "occurred_at"],
    )
    op.create_index(
        "ix_trading_funding_payments_account_coin_occurred",
        "trading_funding_payments",
        ["account_key", "coin", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trading_funding_payments_account_coin_occurred",
        table_name="trading_funding_payments",
    )
    op.drop_index(
        "ix_trading_funding_payments_account_occurred",
        table_name="trading_funding_payments",
    )
    op.drop_table("trading_funding_payments")
