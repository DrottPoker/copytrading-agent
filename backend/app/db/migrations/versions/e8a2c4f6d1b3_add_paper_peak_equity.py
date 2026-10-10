"""add paper account peak equity

Revision ID: e8a2c4f6d1b3
Revises: d7f1b3c5e9a2
Create Date: 2026-10-10 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8a2c4f6d1b3"
down_revision: str | None = "d7f1b3c5e9a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Equity snapshots are pruned, so the drawdown peak is kept on the account.
    op.add_column(
        "paper_trading_accounts",
        sa.Column("peak_equity_usd", sa.Numeric(), nullable=True),
    )
    op.execute(
        """
        update paper_trading_accounts as account
        set peak_equity_usd = snapshots.peak_equity_usd
        from (
            select account_key, max(equity_usd) as peak_equity_usd
            from paper_account_equity_snapshots
            group by account_key
        ) as snapshots
        where account.key = snapshots.account_key
        """
    )


def downgrade() -> None:
    op.drop_column("paper_trading_accounts", "peak_equity_usd")
