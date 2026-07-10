from dataclasses import dataclass
from typing import Literal

WalletDataDisposition = Literal[
    "root",
    "owned",
    "execution",
    "audit",
    "discovery",
    "account",
    "pointer",
]


@dataclass(frozen=True)
class WalletDataDependency:
    table_name: str
    address_column: str
    disposition: WalletDataDisposition
    delete_order: int | None = None
    protection_predicate: str | None = None
    protection_reason: str | None = None


WALLET_DATA_DEPENDENCIES = (
    WalletDataDependency("watched_wallets", "address", "root"),
    WalletDataDependency(
        "realtime_execution_inbox",
        "wallet_address",
        "owned",
        delete_order=140,
        protection_predicate="status in ('pending', 'processing')",
        protection_reason="pending_realtime_execution",
    ),
    WalletDataDependency("wallet_fills", "wallet_address", "owned", delete_order=130),
    WalletDataDependency(
        "wallet_positions",
        "wallet_address",
        "owned",
        delete_order=120,
        protection_predicate="side <> 'flat' and size <> 0",
        protection_reason="open_source_position",
    ),
    WalletDataDependency("wallet_scores", "wallet_address", "owned", delete_order=110),
    WalletDataDependency(
        "wallet_score_snapshots",
        "wallet_address",
        "owned",
        delete_order=100,
    ),
    WalletDataDependency(
        "wallet_monitoring_stats",
        "wallet_address",
        "owned",
        delete_order=90,
    ),
    WalletDataDependency(
        "active_copy_wallets",
        "wallet_address",
        "owned",
        delete_order=80,
        protection_predicate=(
            "has_realtime_slot is true or status in ('active', 'exit_only', 'promotion_pending')"
        ),
        protection_reason="active_copy_state",
    ),
    WalletDataDependency("active_copy_wallets", "blocked_by_wallet_address", "pointer"),
    WalletDataDependency("copy_signals", "source_wallet", "owned", delete_order=70),
    WalletDataDependency(
        "copy_trades",
        "source_wallet",
        "owned",
        delete_order=60,
        protection_predicate="status in ('open', 'closing')",
        protection_reason="open_legacy_copy_trade",
    ),
    WalletDataDependency("source_trade_links", "source_wallet", "owned", delete_order=50),
    WalletDataDependency("source_trades", "wallet_address", "owned", delete_order=40),
    WalletDataDependency(
        "source_trade_sync_states",
        "wallet_address",
        "owned",
        delete_order=30,
    ),
    WalletDataDependency(
        "source_trade_ignored_fills",
        "wallet_address",
        "owned",
        delete_order=20,
    ),
    WalletDataDependency("discovery_wallet_candidates", "wallet_address", "discovery"),
    WalletDataDependency("discovery_wallet_candidates", "parent_address", "discovery"),
    WalletDataDependency("trading_accounts", "wallet_address", "account"),
    WalletDataDependency("trading_accounts", "vault_address", "account"),
    WalletDataDependency(
        "trading_positions",
        "source_wallet",
        "execution",
        protection_predicate="size <> 0",
        protection_reason="open_trading_position",
    ),
    WalletDataDependency(
        "trading_orders",
        "source_wallet",
        "audit",
        protection_predicate=(
            "status in ("
            "'planned', 'ready', 'submitting', 'uncertain', 'submitted', "
            "'accepted', 'partially_filled'"
            ")"
        ),
        protection_reason="in_flight_trading_order",
    ),
    WalletDataDependency("trading_fills", "source_wallet", "audit"),
    WalletDataDependency(
        "paper_copy_allocations",
        "source_wallet",
        "owned",
        delete_order=10,
        protection_predicate="active is true",
        protection_reason="active_paper_allocation",
    ),
    WalletDataDependency(
        "paper_positions",
        "source_wallet",
        "execution",
        protection_predicate="size <> 0",
        protection_reason="open_paper_position",
    ),
    WalletDataDependency("paper_copy_fills", "source_wallet", "audit"),
)


WATCHED_WALLET_PROTECTION_RULES = (
    (
        "watched_wallets",
        "address",
        "copy_enabled is true or polling_tier = 'active'",
        "active_watched_wallet",
    ),
)


def wallet_owned_dependencies() -> tuple[WalletDataDependency, ...]:
    return tuple(
        sorted(
            (
                dependency
                for dependency in WALLET_DATA_DEPENDENCIES
                if dependency.disposition == "owned"
            ),
            key=lambda dependency: dependency.delete_order or 0,
        )
    )


def wallet_protection_rules() -> tuple[tuple[str, str, str, str], ...]:
    dependency_rules = tuple(
        (
            dependency.table_name,
            dependency.address_column,
            dependency.protection_predicate,
            dependency.protection_reason,
        )
        for dependency in WALLET_DATA_DEPENDENCIES
        if dependency.protection_predicate is not None and dependency.protection_reason is not None
    )
    return (*WATCHED_WALLET_PROTECTION_RULES, *dependency_rules)


def protected_wallets_select_sql(*, include_reasons: bool = False) -> str:
    selects: list[str] = []
    for table_name, address_column, predicate, reason in wallet_protection_rules():
        reason_column = f", '{reason}' as protection_reason" if include_reasons else ""
        selects.append(
            f"select {address_column} as wallet_address{reason_column} "
            f"from {table_name} where {predicate}"
        )
    return "\nunion\n".join(selects)


def protected_wallets_cte(*, include_top_scores: bool = False) -> str:
    selects = [protected_wallets_select_sql()]
    if include_top_scores:
        selects.append(
            """
            select wallet_address
            from (
              select wallet_address
              from wallet_scores
              where score is not null
              order by score desc, updated_at desc, wallet_address asc
              limit :protect_top_score_wallets
            ) top_scores
            """.strip()
        )
    return f"protected_wallets as ({' union '.join(selects)})"


def wallet_not_protected_sql(address_expression: str) -> str:
    return (
        "not exists ("
        "select 1 from ("
        f"{protected_wallets_select_sql()}"
        ") protected_wallets "
        f"where protected_wallets.wallet_address = {address_expression}"
        ")"
    )
