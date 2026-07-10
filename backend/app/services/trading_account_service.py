from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaperTradingAccount, TradingAccount


async def list_trading_accounts(session: AsyncSession) -> list[TradingAccount]:
    result = await session.scalars(
        select(TradingAccount)
        .where(TradingAccount.archived_at.is_(None))
        .order_by(
            TradingAccount.account_type.asc(),
            TradingAccount.created_at.asc(),
            TradingAccount.key.asc(),
        )
    )
    return list(result.all())


async def sync_paper_trading_account_mirrors(
    session: AsyncSession,
    *,
    accounts: Sequence[PaperTradingAccount],
    network: str,
) -> None:
    for account in accounts:
        values = {
            "key": account.key,
            "account_type": "paper",
            "label": account.label,
            "status": "enabled" if account.enabled else "exit_only",
            "network": network,
            "wallet_address": None,
            "vault_address": None,
            "starting_balance_usd": account.starting_balance_usd,
            "cash_balance_usd": account.cash_balance_usd,
            "equity_usd": account.equity_usd,
            "realized_pnl_usd": account.realized_pnl_usd,
            "fee_usd": account.fee_usd,
            "config_payload": account.config_payload,
        }
        stmt = insert(TradingAccount).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={
                "account_type": stmt.excluded.account_type,
                "label": stmt.excluded.label,
                "status": stmt.excluded.status,
                "network": stmt.excluded.network,
                "wallet_address": stmt.excluded.wallet_address,
                "vault_address": stmt.excluded.vault_address,
                "starting_balance_usd": stmt.excluded.starting_balance_usd,
                "cash_balance_usd": stmt.excluded.cash_balance_usd,
                "equity_usd": stmt.excluded.equity_usd,
                "realized_pnl_usd": stmt.excluded.realized_pnl_usd,
                "fee_usd": stmt.excluded.fee_usd,
                "config_payload": stmt.excluded.config_payload,
            },
        )
        await session.execute(stmt)


def paper_account_status(account: PaperTradingAccount) -> str:
    return "enabled" if account.enabled else "exit_only"
