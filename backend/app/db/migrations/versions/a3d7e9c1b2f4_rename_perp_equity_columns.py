"""rename perp equity columns

Revision ID: a3d7e9c1b2f4
Revises: f4b9a2c8d1e5
Create Date: 2026-06-17 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a3d7e9c1b2f4"
down_revision: str | None = "f4b9a2c8d1e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    rename_column_if_present(
        table_name="discovery_wallet_candidates",
        old_column_name="account_value",
        new_column_name="source_account_value_usd",
    )
    rename_column_if_present(
        table_name="discovery_wallet_candidates",
        old_column_name="source_pnl",
        new_column_name="source_pnl_usd",
    )
    rename_column_if_present(
        table_name="discovery_wallet_candidates",
        old_column_name="source_roi",
        new_column_name="source_roi_pct",
    )
    rename_column_if_present(
        table_name="wallet_positions",
        old_column_name="notional_usd",
        new_column_name="position_value_usd",
    )
    rename_column_if_present(
        table_name="paper_copy_fills",
        old_column_name="source_account_value_usd",
        new_column_name="source_perp_equity_usd",
    )


def downgrade() -> None:
    rename_column_if_present(
        table_name="paper_copy_fills",
        old_column_name="source_perp_equity_usd",
        new_column_name="source_account_value_usd",
    )
    rename_column_if_present(
        table_name="wallet_positions",
        old_column_name="position_value_usd",
        new_column_name="notional_usd",
    )
    rename_column_if_present(
        table_name="discovery_wallet_candidates",
        old_column_name="source_roi_pct",
        new_column_name="source_roi",
    )
    rename_column_if_present(
        table_name="discovery_wallet_candidates",
        old_column_name="source_pnl_usd",
        new_column_name="source_pnl",
    )
    rename_column_if_present(
        table_name="discovery_wallet_candidates",
        old_column_name="source_account_value_usd",
        new_column_name="account_value",
    )


def rename_column_if_present(
    *,
    table_name: str,
    old_column_name: str,
    new_column_name: str,
) -> None:
    op.execute(
        f"""
        do $$
        begin
            if exists (
                select 1
                from information_schema.columns
                where table_name = '{table_name}'
                  and column_name = '{old_column_name}'
            )
            and not exists (
                select 1
                from information_schema.columns
                where table_name = '{table_name}'
                  and column_name = '{new_column_name}'
            ) then
                alter table {table_name}
                rename column {old_column_name} to {new_column_name};
            end if;
        end $$;
        """
    )
