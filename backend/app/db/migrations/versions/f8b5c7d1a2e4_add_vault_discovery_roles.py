"""add vault discovery account roles

Revision ID: f8b5c7d1a2e4
Revises: f7a6d3c2b1e9
Create Date: 2026-06-18 11:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f8b5c7d1a2e4"
down_revision: str | None = "f7a6d3c2b1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_discovery_wallet_candidates_account_role",
        "discovery_wallet_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_discovery_wallet_candidates_account_role",
        "discovery_wallet_candidates",
        "account_role in ('master', 'subaccount', 'vault', 'vault_leader', 'unknown')",
    )


def downgrade() -> None:
    op.execute(
        """
        update discovery_wallet_candidates
        set account_role = 'unknown'
        where account_role in ('vault', 'vault_leader')
        """
    )
    op.drop_constraint(
        "ck_discovery_wallet_candidates_account_role",
        "discovery_wallet_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_discovery_wallet_candidates_account_role",
        "discovery_wallet_candidates",
        "account_role in ('master', 'subaccount', 'unknown')",
    )
