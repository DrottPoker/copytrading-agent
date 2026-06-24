from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.schemas.trading import TradingAccountsResponse
from app.services.trading_account_service import list_trading_accounts

router = APIRouter(prefix="/trading", tags=["trading"])


@router.get("/accounts", response_model=TradingAccountsResponse)
async def list_trading_accounts_route(
    session: Annotated[AsyncSession, Depends(db_session)],
) -> TradingAccountsResponse:
    accounts = await list_trading_accounts(session)
    return TradingAccountsResponse(accounts=accounts, updated_at=datetime.now(UTC))
