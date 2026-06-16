"""add job locks and fill indexes

Revision ID: c6f49a8d2e11
Revises: b2c41d9e7a63
Create Date: 2026-06-16 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6f49a8d2e11"
down_revision: str | None = "b2c41d9e7a63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_locks",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        "ix_job_locks_locked_until",
        "job_locks",
        ["locked_until"],
        unique=False,
    )

    op.execute(
        """
        update wallet_fills
        set external_fill_id = id::text
        where external_fill_id is null
        """
    )
    op.alter_column(
        "wallet_fills",
        "external_fill_id",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.create_primary_key("pk_wallet_fills", "wallet_fills", ["id"])
    op.create_index(
        "ix_wallet_fills_timestamp",
        "wallet_fills",
        ["timestamp_ms"],
        unique=False,
    )
    op.create_index(
        "ix_wallet_fills_wallet_coin_timestamp",
        "wallet_fills",
        ["wallet_address", "coin", "timestamp_ms"],
        unique=False,
    )
    op.create_index(
        "ix_wallet_fills_wallet_timestamp",
        "wallet_fills",
        ["wallet_address", "timestamp_ms"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_wallet_fills_wallet_timestamp", table_name="wallet_fills")
    op.drop_index("ix_wallet_fills_wallet_coin_timestamp", table_name="wallet_fills")
    op.drop_index("ix_wallet_fills_timestamp", table_name="wallet_fills")
    op.drop_constraint("pk_wallet_fills", "wallet_fills", type_="primary")
    op.alter_column(
        "wallet_fills",
        "external_fill_id",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.drop_index("ix_job_locks_locked_until", table_name="job_locks")
    op.drop_table("job_locks")
