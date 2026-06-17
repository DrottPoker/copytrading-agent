"""add discovery sources

Revision ID: 8e54f3a7c2b1
Revises: 167f3a9d2fff
Create Date: 2026-06-15 18:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8e54f3a7c2b1"
down_revision: str | None = "167f3a9d2fff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discovery_import_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("fetched_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("candidate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("inserted_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('running', 'succeeded', 'failed')",
            name="ck_discovery_import_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_discovery_import_runs_source_started",
        "discovery_import_runs",
        ["source", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_discovery_import_runs_status_started",
        "discovery_import_runs",
        ["status", "started_at"],
        unique=False,
    )

    op.create_table(
        "discovery_wallet_candidates",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("wallet_address", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_rank", sa.Integer(), nullable=True),
        sa.Column("source_label", sa.Text(), nullable=True),
        sa.Column("source_cohort", sa.Text(), nullable=True),
        sa.Column("source_account_value_usd", sa.Numeric(), nullable=True),
        sa.Column("source_pnl_usd", sa.Numeric(), nullable=True),
        sa.Column("source_roi_pct", sa.Numeric(), nullable=True),
        sa.Column("source_copy_score", sa.Numeric(), nullable=True),
        sa.Column("account_role", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("parent_address", sa.Text(), nullable=True),
        sa.Column("subaccount_name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'discovered'"), nullable=False),
        sa.Column("fail_reason", sa.Text(), nullable=True),
        sa.Column("last_import_run_id", sa.UUID(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
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
            "account_role in ('master', 'subaccount', 'unknown')",
            name="ck_discovery_wallet_candidates_account_role",
        ),
        sa.CheckConstraint(
            "status in ('discovered', 'accepted', 'rejected', 'promoted', 'ignored')",
            name="ck_discovery_wallet_candidates_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "wallet_address",
            name="ux_discovery_candidates_source_wallet",
        ),
    )
    op.create_index(
        "ix_discovery_candidates_source_rank",
        "discovery_wallet_candidates",
        ["source", "source_rank"],
        unique=False,
    )
    op.create_index(
        "ix_discovery_candidates_source_status",
        "discovery_wallet_candidates",
        ["source", "status"],
        unique=False,
    )
    op.create_index(
        "ix_discovery_candidates_status_last_seen",
        "discovery_wallet_candidates",
        ["status", "last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_discovery_candidates_wallet",
        "discovery_wallet_candidates",
        ["wallet_address"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_discovery_candidates_wallet", table_name="discovery_wallet_candidates")
    op.drop_index(
        "ix_discovery_candidates_status_last_seen", table_name="discovery_wallet_candidates"
    )
    op.drop_index("ix_discovery_candidates_source_status", table_name="discovery_wallet_candidates")
    op.drop_index("ix_discovery_candidates_source_rank", table_name="discovery_wallet_candidates")
    op.drop_table("discovery_wallet_candidates")
    op.drop_index(
        "ix_discovery_import_runs_status_started", table_name="discovery_import_runs"
    )
    op.drop_index(
        "ix_discovery_import_runs_source_started", table_name="discovery_import_runs"
    )
    op.drop_table("discovery_import_runs")
