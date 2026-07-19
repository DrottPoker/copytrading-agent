"""add unified live copy work queue

Revision ID: e3b7f9d8c4a1
Revises: d1f6a9e4c2b3
Create Date: 2026-07-19 14:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3b7f9d8c4a1"
down_revision: str | None = "d1f6a9e4c2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_copy_work",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("wallet_fill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wallet_address", sa.Text(), nullable=False),
        sa.Column("source_fill_id", sa.Text(), nullable=False),
        sa.Column("source_timestamp_ms", sa.BigInteger(), nullable=False),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("source_order_direction_rank", sa.Integer(), nullable=False),
        sa.Column("source_order_position", sa.Numeric(), nullable=False),
        sa.Column("source_order_fill_id_numeric", sa.Numeric(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=False),
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
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
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
            "origin in ('realtime', 'snapshot_recovery', 'startup_recovery', 'periodic_recovery')",
            name="ck_live_copy_work_origin",
        ),
        sa.CheckConstraint(
            "status in ('pending', 'processing', 'completed')",
            name="ck_live_copy_work_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_live_copy_work_attempt_count",
        ),
        sa.ForeignKeyConstraint(
            ["wallet_fill_id"],
            ["wallet_fills.id"],
            name="fk_live_copy_work_wallet_fill",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("wallet_fill_id", name="ux_live_copy_work_wallet_fill"),
        sa.UniqueConstraint(
            "wallet_address",
            "source_fill_id",
            name="ux_live_copy_work_wallet_source_fill",
        ),
    )
    op.create_index(
        "ix_live_copy_work_claim",
        "live_copy_work",
        ["status", "available_at", "source_timestamp_ms"],
        unique=False,
    )
    op.create_index(
        "ix_live_copy_work_wallet_order",
        "live_copy_work",
        [
            "wallet_address",
            "status",
            "source_timestamp_ms",
            "coin",
            "source_order_direction_rank",
            "source_order_position",
            "source_fill_id",
        ],
        unique=False,
    )
    # Preserve work accepted by an older worker immediately before this
    # migration.  Only pending durable inbox payloads are bridged, never the
    # historical wallet-fill table as a whole.
    op.execute(
        """
        insert into live_copy_work (
          wallet_fill_id,
          wallet_address,
          source_fill_id,
          source_timestamp_ms,
          coin,
          source_order_direction_rank,
          source_order_position,
          source_order_fill_id_numeric,
          origin
        )
        select
          wf.id,
          lower(wf.wallet_address),
          wf.external_fill_id,
          wf.timestamp_ms,
          wf.coin,
          case
            when coalesce(wf.raw_json ->> 'dir', '') in (
              'Close Long', 'Close Short', 'Long > Short', 'Short > Long'
            ) then 0
            else 1
          end,
          case
            when coalesce(wf.raw_json ->> 'startPosition', '') ~
              '^-?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)$'
              and coalesce(wf.raw_json ->> 'dir', '') in (
                'Close Long', 'Close Short', 'Long > Short', 'Short > Long'
              )
              then -abs((wf.raw_json ->> 'startPosition')::numeric)
            when coalesce(wf.raw_json ->> 'startPosition', '') ~
              '^-?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)$'
              then abs((wf.raw_json ->> 'startPosition')::numeric)
            else 0
          end,
          case
            when wf.external_fill_id ~ '^[0-9]+$' then wf.external_fill_id::numeric
            else null
          end,
          'realtime'
        from realtime_execution_inbox inbox
        cross join lateral jsonb_array_elements(
          case
            when jsonb_typeof(inbox.payload -> 'executionRows') = 'array'
              then inbox.payload -> 'executionRows'
            when jsonb_typeof(inbox.payload -> 'insertedRows') = 'array'
              then inbox.payload -> 'insertedRows'
            else '[]'::jsonb
          end
        ) as payload_fill
        join wallet_fills wf
          on lower(wf.wallet_address) = lower(inbox.wallet_address)
         and wf.external_fill_id = payload_fill.value ->> 'externalFillId'
        where inbox.status in ('pending', 'processing')
          and coalesce((inbox.payload ->> 'isSnapshot')::boolean, false) is false
        on conflict (wallet_fill_id) do nothing
        """
    )

    op.add_column(
        "live_copy_fill_states",
        sa.Column("execution_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "live_copy_fill_states",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "live_copy_fill_states",
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        update live_copy_fill_states
        set execution_claimed_at = coalesce(
            execution_claimed_at,
            processing_started_at,
            last_attempt_at,
            first_seen_at,
            created_at
        )
        where execution_claimed_at is null
        """
    )
    op.execute(
        """
        update live_copy_fill_states
        set processing_started_at = coalesce(
            processing_started_at,
            last_attempt_at,
            first_seen_at,
            created_at
        )
        where processing_started_at is null
        """
    )
    op.execute(
        """
        update live_copy_fill_states
        set decision_at = coalesce(
            decision_at,
            updated_at,
            last_attempt_at,
            first_seen_at,
            created_at
        )
        where decision_at is null
          and outcome in ('order', 'terminal_skip', 'baseline_ignored')
        """
    )

def downgrade() -> None:
    op.drop_column("live_copy_fill_states", "decision_at")
    op.drop_column("live_copy_fill_states", "processing_started_at")
    op.drop_column("live_copy_fill_states", "execution_claimed_at")
    op.drop_index("ix_live_copy_work_wallet_order", table_name="live_copy_work")
    op.drop_index("ix_live_copy_work_claim", table_name="live_copy_work")
    op.drop_table("live_copy_work")
