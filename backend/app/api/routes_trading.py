from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.db.models import TradingAccount
from app.schemas.trading import (
    LiveOrderSubmitResponse,
    LiveReconciliationResponse,
    LiveTradingAccountCreateRequest,
    TestnetLiveOrderRequest,
    TradingAccountRead,
    TradingAccountsResponse,
    TradingAccountStatusRequest,
)
from app.services.live_trading_service import (
    LiveTradingServiceError,
    build_testnet_live_trade_intent,
    create_live_trading_account,
    load_live_account_for_update,
    reconcile_live_trading_account,
    set_live_trading_account_status,
    submit_live_trade_intent,
)
from app.services.trading_account_service import list_trading_accounts

router = APIRouter(prefix="/trading", tags=["trading"])


@router.get("/accounts", response_model=TradingAccountsResponse)
async def list_trading_accounts_route(
    session: Annotated[AsyncSession, Depends(db_session)],
) -> TradingAccountsResponse:
    accounts = await list_trading_accounts(session)
    return TradingAccountsResponse(accounts=accounts, updated_at=datetime.now(UTC))


@router.post("/accounts/live", response_model=TradingAccountRead)
async def create_live_account_route(
    payload: LiveTradingAccountCreateRequest,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TradingAccount:
    try:
        account = await create_live_trading_account(
            session,
            key=payload.key,
            label=payload.label,
            wallet_address=payload.wallet_address,
            vault_address=payload.vault_address,
            status=payload.status,
            settings=settings,
        )
        await session.commit()
        return account
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch("/accounts/{account_key}/status", response_model=TradingAccountRead)
async def set_trading_account_status_route(
    account_key: str,
    payload: TradingAccountStatusRequest,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> TradingAccount:
    try:
        account = await set_live_trading_account_status(
            session,
            account_key=account_key,
            status=payload.status,
        )
        await session.commit()
        return account
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/accounts/{account_key}/reconcile", response_model=LiveReconciliationResponse)
async def reconcile_live_account_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LiveReconciliationResponse:
    try:
        account = await load_live_account_for_update(session, account_key=account_key)
        result = await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
        )
        await session.commit()
        return LiveReconciliationResponse.model_validate(result)
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/testnet/orders", response_model=LiveOrderSubmitResponse)
async def submit_testnet_live_order_route(
    payload: TestnetLiveOrderRequest,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LiveOrderSubmitResponse:
    if settings.hyperliquid_network != "testnet":
        raise HTTPException(
            status_code=403,
            detail="Manual test orders are only available when hyperliquid_network is testnet.",
        )

    try:
        account = await load_live_account_for_update(session, account_key=payload.account_key)
        if account.network != "testnet":
            raise LiveTradingServiceError("Live account is not a testnet account.")
        intent = build_testnet_live_trade_intent(
            account=account,
            coin=payload.coin,
            side=payload.side,
            notional_usd=payload.notional_usd,
            limit_price=payload.limit_price,
            leverage=payload.leverage,
            reduce_only=payload.reduce_only,
        )
        result = await submit_live_trade_intent(
            session,
            account=account,
            intent=intent,
            settings=settings,
        )
        await session.commit()
        return LiveOrderSubmitResponse(
            order=result.order,
            submitted=result.submitted,
            updated_at=datetime.now(UTC),
        )
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
