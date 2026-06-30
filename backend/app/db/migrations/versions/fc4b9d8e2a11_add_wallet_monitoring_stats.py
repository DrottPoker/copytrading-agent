"""add wallet monitoring stats

Revision ID: fc4b9d8e2a11
Revises: a4c8d2e9f3b1
Create Date: 2026-06-30 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fc4b9d8e2a11"
down_revision: str | None = "a4c8d2e9f3b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wallet_monitoring_stats",
        sa.Column("wallet_address", sa.Text(), nullable=False),
        sa.Column("first_monitored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_monitoring_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_monitored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "total_monitored_seconds",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
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
            "total_monitored_seconds >= 0",
            name="ck_wallet_monitoring_stats_total_non_negative",
        ),
        sa.PrimaryKeyConstraint("wallet_address"),
    )
    op.create_index(
        "ix_wallet_monitoring_stats_current_started",
        "wallet_monitoring_stats",
        ["current_monitoring_started_at"],
    )
    op.create_index(
        "ix_wallet_monitoring_stats_last_monitored",
        "wallet_monitoring_stats",
        ["last_monitored_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wallet_monitoring_stats_last_monitored",
        table_name="wallet_monitoring_stats",
    )
    op.drop_index(
        "ix_wallet_monitoring_stats_current_started",
        table_name="wallet_monitoring_stats",
    )
    op.drop_table("wallet_monitoring_stats")
