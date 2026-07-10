"""remove global live entry gate

Revision ID: a7d3e9f1c5b2
Revises: f6b8d0e2a4c1
Create Date: 2026-07-10 03:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3e9f1c5b2"
down_revision: str | None = "f6b8d0e2a4c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        update trading_accounts
        set
          status = 'enabled',
          lifecycle_version = lifecycle_version + 1,
          status_changed_at = now(),
          status_reason = 'global_live_entry_gate_removed'
        where account_type = 'live'
          and status = 'exit_only'
          and status_reason = 'phase_6_global_entry_control_default_paused'
          and archived_at is null
        """
    )
    op.drop_table("live_entry_safety_controls")


def downgrade() -> None:
    op.create_table(
        "live_entry_safety_controls",
        sa.Column(
            "id",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "entry_state",
            sa.Text(),
            server_default=sa.text("'enabled'"),
            nullable=False,
        ),
        sa.Column(
            "revision",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "changed_by",
            sa.Text(),
            server_default=sa.text("'migration'"),
            nullable=False,
        ),
        sa.Column(
            "changed_at",
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
            "id = 1",
            name="ck_live_entry_safety_controls_singleton",
        ),
        sa.CheckConstraint(
            "entry_state in ('enabled', 'paused', 'killed')",
            name="ck_live_entry_safety_controls_state",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_live_entry_safety_controls_revision",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        """
        insert into live_entry_safety_controls (
          id,
          entry_state,
          revision,
          reason,
          changed_by
        )
        values (
          1,
          'enabled',
          0,
          'Compatibility control recreated by migration downgrade.',
          'migration'
        )
        """
    )
