"""add live copy lifecycle state

Revision ID: d1f6a9e4c2b3
Revises: c9d5a1e7f3b2
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1f6a9e4c2b3"
down_revision: str | None = "c9d5a1e7f3b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "watched_wallets",
        sa.Column("copy_eligibility_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        update watched_wallets
        set copy_eligibility_started_at = now()
        where copy_eligibility_started_at is null
          and exists (
              select 1
              from paper_copy_allocations
              where paper_copy_allocations.active
                and lower(paper_copy_allocations.source_wallet) = lower(watched_wallets.address)
          )
        """
    )
    op.add_column(
        "trading_positions",
        sa.Column("source_lifecycle_timestamp_ms", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "trading_positions",
        sa.Column("source_lifecycle_direction_rank", sa.Integer(), nullable=True),
    )
    op.add_column(
        "trading_positions",
        sa.Column("source_lifecycle_position", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "trading_positions",
        sa.Column("source_lifecycle_fill_id_numeric", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "trading_positions",
        sa.Column("source_lifecycle_fill_id", sa.Text(), nullable=True),
    )
    op.create_table(
        "live_copy_source_states",
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("source_wallet", sa.Text(), nullable=False),
        sa.Column(
            "account_type",
            sa.Text(),
            server_default=sa.text("'live'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'baseline_pending'"),
            nullable=False,
        ),
        sa.Column(
            "entry_eligible",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("baseline_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_source_timestamp_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "baseline_fill_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("scan_high_water_timestamp_ms", sa.BigInteger(), nullable=True),
        sa.Column("scan_high_water_coin", sa.Text(), nullable=True),
        sa.Column("scan_high_water_direction_rank", sa.Integer(), nullable=True),
        sa.Column("scan_high_water_position", sa.Numeric(), nullable=True),
        sa.Column("scan_high_water_fill_id_numeric", sa.Numeric(), nullable=True),
        sa.Column("scan_high_water_fill_id", sa.Text(), nullable=True),
        sa.Column(
            "preexisting_markets",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
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
            "account_type = 'live'",
            name="ck_live_copy_source_states_account_type",
        ),
        sa.CheckConstraint(
            "status in ('baseline_pending', 'active', 'inactive')",
            name="ck_live_copy_source_states_status",
        ),
        sa.ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_live_copy_source_states_account_key_type",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("account_key", "source_wallet"),
    )
    op.create_index(
        "ix_live_copy_source_states_status_baseline",
        "live_copy_source_states",
        ["status", "baseline_completed_at"],
    )

    op.create_table(
        "live_copy_fill_states",
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
        sa.Column("source_wallet", sa.Text(), nullable=False),
        sa.Column("source_fill_id", sa.Text(), nullable=False),
        sa.Column(
            "sequence_index",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "expected_part_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "plan_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("source_timestamp_ms", sa.BigInteger(), nullable=False),
        sa.Column(
            "source_order_direction_rank",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "source_order_position",
            sa.Numeric(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("source_order_fill_id_numeric", sa.Numeric(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column(
            "outcome",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fill_complete",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("trading_order_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            "account_type = 'live'",
            name="ck_live_copy_fill_states_account_type",
        ),
        sa.CheckConstraint(
            "action in ('open', 'add', 'reduce', 'close', 'flip_close', 'flip_open')",
            name="ck_live_copy_fill_states_action",
        ),
        sa.CheckConstraint(
            "side in ('long', 'short')",
            name="ck_live_copy_fill_states_side",
        ),
        sa.CheckConstraint(
            "origin in ('realtime', 'snapshot_recovery', 'startup_recovery', 'periodic_recovery')",
            name="ck_live_copy_fill_states_origin",
        ),
        sa.CheckConstraint(
            "outcome in ('pending', 'retryable', 'order', 'terminal_skip', 'baseline_ignored')",
            name="ck_live_copy_fill_states_outcome",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_live_copy_fill_states_attempt_count",
        ),
        sa.CheckConstraint(
            "expected_part_count > 0",
            name="ck_live_copy_fill_states_expected_part_count",
        ),
        sa.CheckConstraint(
            "plan_version > 0",
            name="ck_live_copy_fill_states_plan_version",
        ),
        sa.CheckConstraint(
            "not fill_complete or outcome in ('order', 'terminal_skip', 'baseline_ignored')",
            name="ck_live_copy_fill_states_complete_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["account_key", "account_type"],
            ["trading_accounts.key", "trading_accounts.account_type"],
            name="fk_live_copy_fill_states_account_key_type",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["account_key", "source_wallet"],
            ["live_copy_source_states.account_key", "live_copy_source_states.source_wallet"],
            name="fk_live_copy_fill_states_source_state",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trading_order_id"],
            ["trading_orders.id"],
            name="fk_live_copy_fill_states_trading_order",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_key",
            "source_wallet",
            "source_fill_id",
            "sequence_index",
            name="ux_live_copy_fill_states_account_source_fill_sequence",
        ),
    )
    op.create_index(
        "ix_live_copy_fill_states_source_recovery",
        "live_copy_fill_states",
        [
            "account_key",
            "source_wallet",
            "fill_complete",
            "next_attempt_at",
            "source_timestamp_ms",
            "source_fill_id",
        ],
    )
    op.create_index(
        "ix_live_copy_fill_states_due",
        "live_copy_fill_states",
        ["outcome", "next_attempt_at"],
    )
    op.create_index(
        "ix_live_copy_fill_states_recent_updated",
        "live_copy_fill_states",
        ["updated_at"],
    )
    op.create_index(
        "ix_live_copy_fill_states_trading_order",
        "live_copy_fill_states",
        ["trading_order_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_live_copy_fill_states_trading_order",
        table_name="live_copy_fill_states",
    )
    op.drop_index(
        "ix_live_copy_fill_states_recent_updated",
        table_name="live_copy_fill_states",
    )
    op.drop_index("ix_live_copy_fill_states_due", table_name="live_copy_fill_states")
    op.drop_index(
        "ix_live_copy_fill_states_source_recovery",
        table_name="live_copy_fill_states",
    )
    op.drop_table("live_copy_fill_states")
    op.drop_index(
        "ix_live_copy_source_states_status_baseline",
        table_name="live_copy_source_states",
    )
    op.drop_table("live_copy_source_states")
    op.drop_column("trading_positions", "source_lifecycle_fill_id")
    op.drop_column("trading_positions", "source_lifecycle_fill_id_numeric")
    op.drop_column("trading_positions", "source_lifecycle_position")
    op.drop_column("trading_positions", "source_lifecycle_direction_rank")
    op.drop_column("trading_positions", "source_lifecycle_timestamp_ms")
    op.drop_column("watched_wallets", "copy_eligibility_started_at")
