"""expand wallet fill ingest latency

Revision ID: b8e4f0a2d6c1
Revises: a7d3e9f1c5b2
Create Date: 2026-07-10 04:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4f0a2d6c1"
down_revision: str | None = "a7d3e9f1c5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "wallet_fills",
        "ingest_latency_ms",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (
            select 1
            from wallet_fills
            where ingest_latency_ms < -2147483648
               or ingest_latency_ms > 2147483647
          ) then
            raise exception
              'Cannot downgrade ingest_latency_ms while values exceed the INTEGER range.';
          end if;
        end
        $$
        """
    )
    op.alter_column(
        "wallet_fills",
        "ingest_latency_ms",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="ingest_latency_ms::integer",
    )
