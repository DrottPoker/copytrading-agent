"""add realtime execution inbox

Revision ID: f6b8d0e2a4c1
Revises: e5a1c7d9b3f2
Create Date: 2026-07-10 02:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6b8d0e2a4c1"
down_revision: str | None = "e5a1c7d9b3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "realtime_execution_inbox",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("wallet_address", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.Text(), nullable=True),
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
            "status in ('pending', 'processing')",
            name="ck_realtime_execution_inbox_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_realtime_execution_inbox_attempt_count",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_realtime_execution_inbox_claim",
        "realtime_execution_inbox",
        ["status", "available_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_realtime_execution_inbox_wallet_created",
        "realtime_execution_inbox",
        ["wallet_address", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_realtime_execution_inbox_wallet_created",
        table_name="realtime_execution_inbox",
    )
    op.drop_index(
        "ix_realtime_execution_inbox_claim",
        table_name="realtime_execution_inbox",
    )
    op.drop_table("realtime_execution_inbox")
