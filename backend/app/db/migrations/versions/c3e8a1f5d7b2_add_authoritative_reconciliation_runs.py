"""add authoritative reconciliation runs

Revision ID: c3e8a1f5d7b2
Revises: f2d7c9a4b6e1
Create Date: 2026-07-09 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3e8a1f5d7b2"
down_revision: str | None = "f2d7c9a4b6e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_reconciliation_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("fetched_fills", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("inserted_fills", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_orders", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("open_positions", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("removed_positions", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
            "status in ('running', 'complete', 'partial', 'failed')",
            name="ck_trading_reconciliation_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["account_key"],
            ["trading_accounts.key"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trading_reconciliation_runs_account_started",
        "trading_reconciliation_runs",
        ["account_key", "started_at"],
    )
    op.create_index(
        "ix_trading_reconciliation_runs_status",
        "trading_reconciliation_runs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trading_reconciliation_runs_status",
        table_name="trading_reconciliation_runs",
    )
    op.drop_index(
        "ix_trading_reconciliation_runs_account_started",
        table_name="trading_reconciliation_runs",
    )
    op.drop_table("trading_reconciliation_runs")
