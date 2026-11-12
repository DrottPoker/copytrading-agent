"""Load a live account and its orders and positions, and check it matches the configured wallet."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    TradingAccount,
    TradingCloseAllOperation,
    TradingOrder,
    TradingPosition,
)
from app.services.live_execution_state import TERMINAL_ORDER_STATUSES
from app.services.live_trading_errors import (
    LiveAccountNotFoundError,
    LiveReconciliationError,
    LiveTradingServiceError,
)


def validate_live_account_identity(account: TradingAccount, *, settings: Settings) -> None:
    if account.archived_at is not None:
        raise LiveTradingServiceError("Live account is archived.", status_code=409)
    if account.network != settings.hyperliquid_network:
        raise LiveTradingServiceError(
            "Live account network does not match the configured network.",
            status_code=409,
        )


async def load_live_order_by_client_order_id(
    session: AsyncSession,
    *,
    client_order_id: str,
) -> TradingOrder | None:
    return await session.scalar(
        select(TradingOrder)
        .where(
            TradingOrder.account_type == "live",
            TradingOrder.client_order_id == client_order_id,
        )
        .execution_options(populate_existing=True)
    )


async def get_live_position(
    session: AsyncSession,
    *,
    position_id: UUID,
) -> TradingPosition | None:
    return await session.scalar(
        select(TradingPosition).where(
            TradingPosition.id == position_id,
            TradingPosition.account_type == "live",
        )
    )


async def load_live_position(
    session: AsyncSession,
    *,
    position_id: UUID,
) -> TradingPosition:
    position = await get_live_position(session, position_id=position_id)
    if position is None:
        raise LiveTradingServiceError("Live position was not found.", status_code=404)
    return position


async def load_live_account_for_update(
    session: AsyncSession,
    *,
    account_key: str,
) -> TradingAccount:
    account = await session.scalar(
        select(TradingAccount)
        .where(
            TradingAccount.key == account_key,
            TradingAccount.account_type == "live",
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if account is None:
        raise LiveAccountNotFoundError()
    return account


async def load_live_account(
    session: AsyncSession,
    *,
    account_key: str,
) -> TradingAccount:
    account = await session.scalar(
        select(TradingAccount).where(
            TradingAccount.key == account_key,
            TradingAccount.account_type == "live",
        )
    )
    if account is None:
        raise LiveAccountNotFoundError()
    return account


def live_account_user_address(account: TradingAccount, *, settings: Settings) -> str:
    user_address = account.wallet_address or settings.hyperliquid_wallet_address
    if not user_address:
        raise LiveReconciliationError(
            "Live account requires wallet_address or HYPERLIQUID_WALLET_ADDRESS."
        )
    return user_address.lower()


async def live_account_nonterminal_order_count(
    session: AsyncSession,
    *,
    account_key: str,
) -> int:
    value = await session.scalar(
        select(func.count(TradingOrder.id)).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
            TradingOrder.status.not_in(TERMINAL_ORDER_STATUSES),
        )
    )
    return int(value or 0)


async def live_account_has_incomplete_close_operation(
    session: AsyncSession,
    *,
    account_key: str,
) -> bool:
    value = await session.scalar(
        select(func.count(TradingCloseAllOperation.id)).where(
            TradingCloseAllOperation.account_key == account_key,
            TradingCloseAllOperation.status.in_(
                ["pending", "running", "partially_completed", "failed"]
            ),
        )
    )
    return int(value or 0) > 0
