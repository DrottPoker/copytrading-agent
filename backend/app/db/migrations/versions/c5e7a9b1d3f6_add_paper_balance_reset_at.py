"""add paper account balance reset time

Revision ID: c5e7a9b1d3f6
Revises: b7c3e5a9d1f2
Create Date: 2026-10-24 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e7a9b1d3f6"
down_revision: str | None = "b7c3e5a9d1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A reset keeps the fill history, so account statistics start at the last reset.
    op.add_column(
        "paper_trading_accounts",
        sa.Column("balance_reset_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        update paper_trading_accounts as account
        set balance_reset_at = resets.reset_at
        from (
            select payload ->> 'accountKey' as account_key, max(created_at) as reset_at
            from audit_logs
            where action = 'paper_account.balance_reset'
            group by payload ->> 'accountKey'
        ) as resets
        where account.key = resets.account_key
        """
    )


def downgrade() -> None:
    op.drop_column("paper_trading_accounts", "balance_reset_at")
