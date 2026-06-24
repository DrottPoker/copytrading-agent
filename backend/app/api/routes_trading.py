from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.db.models import TradingAccount
from app.schemas.trading import (
    LiveCloseAllResponse,
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
    close_all_live_account_positions,
    create_live_trading_account,
    delete_live_trading_account,
    load_live_account_for_update,
    reconcile_live_trading_account,
    set_live_trading_account_status,
    submit_live_trade_intent,
    validate_live_account_can_start,
    validate_live_trading_configuration,
)
from app.services.trading_account_service import list_trading_accounts

router = APIRouter(prefix="/trading", tags=["trading"])


async def trading_account_read(
    session: AsyncSession,
    account: TradingAccount,
) -> TradingAccountRead:
    await session.flush()
    await session.refresh(account)
    return TradingAccountRead.model_validate(account)


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
) -> TradingAccountRead:
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
        await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
        )
        response = await trading_account_read(session, account)
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/accounts/{account_key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_live_account_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> None:
    try:
        await delete_live_trading_account(
            session,
            account_key=account_key,
        )
        await session.commit()
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch("/accounts/{account_key}/status", response_model=TradingAccountRead)
async def set_trading_account_status_route(
    account_key: str,
    payload: TradingAccountStatusRequest,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TradingAccountRead:
    try:
        if payload.status == "enabled":
            validate_live_trading_configuration(settings)
        account = await set_live_trading_account_status(
            session,
            account_key=account_key,
            status=payload.status,
        )
        if account.status == "enabled":
            await reconcile_live_trading_account(
                session,
                account=account,
                settings=settings,
            )
            validate_live_account_can_start(account)
        response = await trading_account_read(session, account)
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/accounts/{account_key}/start", response_model=TradingAccountRead)
async def start_live_account_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TradingAccountRead:
    try:
        validate_live_trading_configuration(settings)
        account = await set_live_trading_account_status(
            session,
            account_key=account_key,
            status="enabled",
        )
        await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
        )
        validate_live_account_can_start(account)
        response = await trading_account_read(session, account)
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/accounts/{account_key}/stop", response_model=TradingAccountRead)
async def stop_live_account_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> TradingAccountRead:
    try:
        account = await set_live_trading_account_status(
            session,
            account_key=account_key,
            status="exit_only",
        )
        response = await trading_account_read(session, account)
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/accounts/{account_key}/close-all-and-stop", response_model=LiveCloseAllResponse)
async def close_all_and_stop_live_account_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LiveCloseAllResponse:
    try:
        account = await load_live_account_for_update(session, account_key=account_key)
        result = await close_all_live_account_positions(
            session,
            account=account,
            settings=settings,
        )
        await session.commit()
        return LiveCloseAllResponse(
            account_key=result.account_key,
            submitted_orders=result.submitted_orders,
            failed_orders=result.failed_orders,
            status=result.status,
            updated_at=datetime.now(UTC),
        )
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
