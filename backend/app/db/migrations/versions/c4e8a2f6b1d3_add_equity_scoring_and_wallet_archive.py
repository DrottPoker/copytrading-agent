"""add equity scoring inputs, daily score snapshots, and wallet archiving

Revision ID: c4e8a2f6b1d3
Revises: f3b7d9a1c5e8
Create Date: 2026-10-10 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a2f6b1d3"
down_revision: str | None = "f3b7d9a1c5e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "watched_wallets",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("watched_wallets", sa.Column("archive_reason", sa.Text(), nullable=True))
    op.create_index("ix_watched_wallets_archived_at", "watched_wallets", ["archived_at"])

    # max_drawdown_pct becomes the equity-curve drawdown; the old closed-trade
    # ratio moves to realized_drawdown_pct until the next scoring run.
    op.add_column("wallet_scores", sa.Column("realized_drawdown_pct", sa.Numeric(), nullable=True))
    op.add_column("wallet_scores", sa.Column("equity_return_pct", sa.Numeric(), nullable=True))
    op.add_column("wallet_scores", sa.Column("copy_size_ratio", sa.Numeric(), nullable=True))
    op.add_column("wallet_scores", sa.Column("underwater_add_ratio", sa.Numeric(), nullable=True))
    op.execute(
        "update wallet_scores set realized_drawdown_pct = max_drawdown_pct, max_drawdown_pct = null"
    )

    op.add_column(
        "wallet_score_snapshots",
        sa.Column("snapshot_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "wallet_score_snapshots",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute("update wallet_score_snapshots set snapshot_date = (created_at at time zone 'utc')")
    op.execute(
        """
        delete from wallet_score_snapshots older
        using wallet_score_snapshots newer
        where older.wallet_address = newer.wallet_address
          and older.snapshot_date = newer.snapshot_date
          and (older.created_at, older.id) < (newer.created_at, newer.id)
        """
    )
    op.alter_column("wallet_score_snapshots", "snapshot_date", nullable=False)
    op.drop_index(
        "ix_wallet_score_snapshots_wallet_created",
        table_name="wallet_score_snapshots",
    )
    op.create_unique_constraint(
        "ux_wallet_score_snapshots_wallet_date",
        "wallet_score_snapshots",
        ["wallet_address", "snapshot_date"],
    )
    op.create_index(
        "ix_wallet_score_snapshots_date",
        "wallet_score_snapshots",
        ["snapshot_date"],
    )

    op.create_table(
        "wallet_equity_metrics",
        sa.Column("wallet_address", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=True),
        sa.Column("window_start_ms", sa.BigInteger(), nullable=True),
        sa.Column("window_end_ms", sa.BigInteger(), nullable=True),
        sa.Column("return_pct", sa.Numeric(), nullable=True),
        sa.Column("max_drawdown_pct", sa.Numeric(), nullable=True),
        sa.Column("average_account_value_usd", sa.Numeric(), nullable=True),
        sa.Column("latest_account_value_usd", sa.Numeric(), nullable=True),
        sa.Column("point_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('ok', 'insufficient', 'unavailable')",
            name="ck_wallet_equity_metrics_status",
        ),
        sa.CheckConstraint(
            "basis is null or basis in ('perp', 'total')",
            name="ck_wallet_equity_metrics_basis",
        ),
        sa.PrimaryKeyConstraint("wallet_address"),
    )
    op.create_index(
        "ix_wallet_equity_metrics_fetched_at",
        "wallet_equity_metrics",
        ["fetched_at"],
    )

    op.add_column(
        "source_trades",
        sa.Column(
            "underwater_add_notional_usd",
            sa.Numeric(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    # Rebuild every materialized trade set so the new column is populated.
    op.execute("update source_trade_sync_states set fill_revision = -1")


def downgrade() -> None:
    op.drop_column("source_trades", "underwater_add_notional_usd")
    op.execute("update source_trade_sync_states set fill_revision = -1")

    op.drop_index("ix_wallet_equity_metrics_fetched_at", table_name="wallet_equity_metrics")
    op.drop_table("wallet_equity_metrics")

    op.drop_index("ix_wallet_score_snapshots_date", table_name="wallet_score_snapshots")
    op.drop_constraint(
        "ux_wallet_score_snapshots_wallet_date",
        "wallet_score_snapshots",
        type_="unique",
    )
    op.create_index(
        "ix_wallet_score_snapshots_wallet_created",
        "wallet_score_snapshots",
        ["wallet_address", "created_at"],
    )
    op.drop_column("wallet_score_snapshots", "updated_at")
    op.drop_column("wallet_score_snapshots", "snapshot_date")

    op.execute(
        "update wallet_scores set max_drawdown_pct = realized_drawdown_pct "
        "where realized_drawdown_pct is not null"
    )
    op.drop_column("wallet_scores", "underwater_add_ratio")
    op.drop_column("wallet_scores", "copy_size_ratio")
    op.drop_column("wallet_scores", "equity_return_pct")
    op.drop_column("wallet_scores", "realized_drawdown_pct")

    op.drop_index("ix_watched_wallets_archived_at", table_name="watched_wallets")
    op.drop_column("watched_wallets", "archive_reason")
    op.drop_column("watched_wallets", "archived_at")
