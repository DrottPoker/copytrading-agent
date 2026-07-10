"""add phase 6 safety schema

Revision ID: e5a1c7d9b3f2
Revises: d4f9a2b7c6e1
Create Date: 2026-07-10 01:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a1c7d9b3f2"
down_revision: str | None = "d4f9a2b7c6e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
            server_default=sa.text("'paused'"),
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
            server_default=sa.text("'system'"),
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
          'paused',
          0,
          'Fail-closed default created by the Phase 6 schema migration.',
          'migration'
        )
        """
    )

    op.add_column(
        "trading_accounts",
        sa.Column(
            "lifecycle_version",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("status_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        update trading_accounts
        set status_changed_at = coalesce(updated_at, created_at, now())
        where status_changed_at is null
        """
    )
    op.alter_column(
        "trading_accounts",
        "status_changed_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    op.create_check_constraint(
        "ck_trading_accounts_lifecycle_version",
        "trading_accounts",
        "lifecycle_version >= 0",
    )
    op.execute(
        """
        update trading_accounts
        set
          status = 'exit_only',
          lifecycle_version = lifecycle_version + 1,
          status_changed_at = now(),
          status_reason = 'phase_6_global_entry_control_default_paused'
        where account_type = 'live'
          and status = 'enabled'
        """
    )

    # Financial and idempotency child rows are never deleted by this migration.
    # A type mismatch is ambiguous, so the migration stops for manual review.
    op.execute(
        """
        do $$
        begin
          if exists (
            select 1
            from (
              select account_key, account_type from trading_positions
              union all
              select account_key, account_type from trading_orders
              union all
              select account_key, account_type from trading_fills
            ) child
            join trading_accounts account on account.key = child.account_key
            where account.account_type <> child.account_type
          ) then
            raise exception
              'Phase 6 account integrity migration found child/account type mismatches.';
          end if;

          if exists (
            with child_references as (
              select account_key, account_type from trading_positions
              union all
              select account_key, account_type from trading_orders
              union all
              select account_key, account_type from trading_fills
            ),
            missing_parents as (
              select child.account_key, child.account_type
              from child_references child
              left join trading_accounts account on account.key = child.account_key
              where account.key is null
            )
            select 1
            from missing_parents
            group by account_key
            having count(distinct account_type) > 1
          ) then
            raise exception
              'Phase 6 account integrity migration found ambiguous orphan account types.';
          end if;
        end
        $$
        """
    )

    # Missing parents are recovered as disabled and archived placeholders. This
    # preserves financial history while preventing the recovered account from
    # becoming eligible for live entry execution.
    op.execute(
        """
        with child_references as (
          select account_key, account_type from trading_positions
          union
          select account_key, account_type from trading_orders
          union
          select account_key, account_type from trading_fills
        ),
        missing_parents as (
          select child.account_key, child.account_type
          from child_references child
          left join trading_accounts account on account.key = child.account_key
          where account.key is null
        )
        insert into trading_accounts (
          key,
          account_type,
          label,
          status,
          network,
          lifecycle_version,
          status_changed_at,
          status_reason,
          archived_at,
          config_payload,
          created_at,
          updated_at
        )
        select
          account_key,
          account_type,
          'Recovered orphan account ' || account_key,
          'disabled',
          'testnet',
          0,
          now(),
          'phase_6_orphan_parent_recovery',
          now(),
          jsonb_build_object('source', 'phase_6_orphan_parent_recovery'),
          now(),
          now()
        from missing_parents
        on conflict (key) do nothing
        """
    )

    op.create_unique_constraint(
        "ux_trading_accounts_key_type",
        "trading_accounts",
        ["key", "account_type"],
    )

    # Route deduplication is intentionally narrow. The canonical row is chosen
    # deterministically by retained history, active status, creation time, and
    # key. A duplicate is deleted only when it is disabled and has no dependent
    # records. Any ambiguous duplicate stops the migration for manual review.
    op.execute(
        """
        do $$
        begin
          if exists (
            with route_candidates as (
              select
                account.key,
                account.network,
                account.wallet_address,
                account.vault_address,
                account.status,
                account.created_at,
                (
                  exists (
                    select 1 from trading_positions child
                    where child.account_key = account.key
                  )
                  or exists (
                    select 1 from trading_orders child
                    where child.account_key = account.key
                  )
                  or exists (
                    select 1 from trading_fills child
                    where child.account_key = account.key
                  )
                  or exists (
                    select 1 from trading_order_dispatches child
                    where child.account_key = account.key
                  )
                  or exists (
                    select 1 from trading_reconciliation_runs child
                    where child.account_key = account.key
                  )
                  or exists (
                    select 1 from trading_close_all_operations child
                    where child.account_key = account.key
                  )
                ) as has_records
              from trading_accounts account
              where account.account_type = 'live'
                and account.archived_at is null
                and account.wallet_address is not null
                and btrim(account.wallet_address) <> ''
            ),
            ranked_routes as (
              select
                candidate.*,
                row_number() over (
                  partition by
                    candidate.network,
                    lower(btrim(candidate.wallet_address)),
                    coalesce(lower(btrim(candidate.vault_address)), '')
                  order by
                    candidate.has_records desc,
                    case candidate.status
                      when 'enabled' then 0
                      when 'exit_only' then 1
                      else 2
                    end,
                    candidate.created_at asc,
                    candidate.key asc
                ) as route_rank
              from route_candidates candidate
            )
            select 1
            from ranked_routes
            where route_rank > 1
              and (status <> 'disabled' or has_records)
          ) then
            raise exception
              'Phase 6 route migration found duplicate live routes with active state or history.';
          end if;
        end
        $$
        """
    )
    op.execute(
        """
        with route_candidates as (
          select
            account.key,
            account.network,
            account.wallet_address,
            account.vault_address,
            account.status,
            account.created_at,
            (
              exists (
                select 1 from trading_positions child
                where child.account_key = account.key
              )
              or exists (
                select 1 from trading_orders child
                where child.account_key = account.key
              )
              or exists (
                select 1 from trading_fills child
                where child.account_key = account.key
              )
              or exists (
                select 1 from trading_order_dispatches child
                where child.account_key = account.key
              )
              or exists (
                select 1 from trading_reconciliation_runs child
                where child.account_key = account.key
              )
              or exists (
                select 1 from trading_close_all_operations child
                where child.account_key = account.key
              )
            ) as has_records
          from trading_accounts account
          where account.account_type = 'live'
            and account.archived_at is null
            and account.wallet_address is not null
            and btrim(account.wallet_address) <> ''
        ),
        ranked_routes as (
          select
            candidate.*,
            row_number() over (
              partition by
                candidate.network,
                lower(btrim(candidate.wallet_address)),
                coalesce(lower(btrim(candidate.vault_address)), '')
              order by
                candidate.has_records desc,
                case candidate.status
                  when 'enabled' then 0
                  when 'exit_only' then 1
                  else 2
                end,
                candidate.created_at asc,
                candidate.key asc
            ) as route_rank
          from route_candidates candidate
        )
        delete from trading_accounts account
        using ranked_routes duplicate
        where account.key = duplicate.key
          and duplicate.route_rank > 1
          and duplicate.status = 'disabled'
          and not duplicate.has_records
        """
    )
    op.create_index(
        "ux_trading_accounts_live_active_route",
        "trading_accounts",
        [
            "network",
            sa.text("lower(btrim(wallet_address))"),
            sa.text("coalesce(lower(btrim(vault_address)), '')"),
        ],
        unique=True,
        postgresql_where=sa.text(
            "account_type = 'live' and archived_at is null "
            "and wallet_address is not null and btrim(wallet_address) <> ''"
        ),
    )

    op.create_foreign_key(
        "fk_trading_positions_account_key_type_trading_accounts",
        "trading_positions",
        "trading_accounts",
        ["account_key", "account_type"],
        ["key", "account_type"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_trading_orders_account_key_type_trading_accounts",
        "trading_orders",
        "trading_accounts",
        ["account_key", "account_type"],
        ["key", "account_type"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_trading_fills_account_key_type_trading_accounts",
        "trading_fills",
        "trading_accounts",
        ["account_key", "account_type"],
        ["key", "account_type"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_trading_fills_account_key_type_trading_accounts",
        "trading_fills",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_trading_orders_account_key_type_trading_accounts",
        "trading_orders",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_trading_positions_account_key_type_trading_accounts",
        "trading_positions",
        type_="foreignkey",
    )
    op.drop_index(
        "ux_trading_accounts_live_active_route",
        table_name="trading_accounts",
    )
    op.drop_constraint(
        "ux_trading_accounts_key_type",
        "trading_accounts",
        type_="unique",
    )
    op.drop_constraint(
        "ck_trading_accounts_lifecycle_version",
        "trading_accounts",
        type_="check",
    )
    op.drop_column("trading_accounts", "archived_at")
    op.drop_column("trading_accounts", "status_reason")
    op.drop_column("trading_accounts", "status_changed_at")
    op.drop_column("trading_accounts", "lifecycle_version")
    op.drop_table("live_entry_safety_controls")
