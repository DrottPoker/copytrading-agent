from app.db import models as _models  # noqa: F401
from app.db.base import Base
from app.services.wallet_data_policy import (
    WALLET_DATA_DEPENDENCIES,
    protected_wallets_select_sql,
    wallet_owned_dependencies,
)


def test_wallet_data_policy_classifies_every_structured_wallet_reference() -> None:
    model_references = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if "address" in column.name or column.name == "source_wallet"
    }
    policy_references = {
        (dependency.table_name, dependency.address_column)
        for dependency in WALLET_DATA_DEPENDENCIES
    }

    assert policy_references == model_references


def test_wallet_owned_dependencies_have_a_unique_delete_order() -> None:
    owned_dependencies = wallet_owned_dependencies()
    delete_orders = [dependency.delete_order for dependency in owned_dependencies]

    assert all(delete_order is not None for delete_order in delete_orders)
    assert delete_orders == sorted(delete_orders)
    assert len(delete_orders) == len(set(delete_orders))
    assert all(
        dependency.table_name != "live_copy_source_states" for dependency in owned_dependencies
    )


def test_live_copy_audit_state_cannot_cascade_delete_fill_history() -> None:
    fill_state_table = Base.metadata.tables["live_copy_fill_states"]
    source_state_foreign_key = next(
        constraint
        for constraint in fill_state_table.foreign_key_constraints
        if constraint.name == "fk_live_copy_fill_states_source_state"
    )

    assert source_state_foreign_key.ondelete == "RESTRICT"


def test_wallet_protection_policy_covers_execution_state() -> None:
    sql = protected_wallets_select_sql()

    assert "from trading_positions" in sql
    assert "from trading_orders" in sql
    assert "from live_copy_fill_states" in sql
    assert "from paper_positions" in sql
    assert "from paper_copy_allocations" in sql
    assert "from active_copy_wallets" in sql


def test_wallet_protection_retains_filled_orders_until_exchange_fills_materialize() -> None:
    sql = protected_wallets_select_sql()

    assert "status = 'filled'" in sql
    assert "not exists (select 1 from trading_fills" in sql
    assert "sum(trading_fills.size)" in sql
    assert "trading_fills.order_id = trading_orders.id" in sql
