from datetime import UTC, datetime
from decimal import Decimal
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
    TradingCapitalBalanceRead,
)
from app.services.live_trading_service import (
    LiveTradingServiceError,
    account_last_reconciliation,
    build_testnet_live_trade_intent,
    close_all_live_account_positions,
    create_live_trading_account,
    decimal_or_none,
    delete_live_trading_account,
    live_capital_mode,
    live_perp_equity_usd,
    live_spot_available_usd,
    live_spot_balance_usd,
    live_tradable_equity_usd,
    live_unified_available_usd,
    live_unified_equity_usd,
    load_live_account_for_update,
    normalize_user_abstraction,
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
    settings: Settings,
) -> TradingAccountRead:
    await session.flush()
    await session.refresh(account)
    return enriched_trading_account_read(account, settings=settings)


def enriched_trading_account_read(
    account: TradingAccount,
    *,
    settings: Settings,
) -> TradingAccountRead:
    read = TradingAccountRead.model_validate(account)
    if account.account_type != "live":
        return read

    last_reconciliation = account_last_reconciliation(account)
    mode = live_capital_mode(settings)
    return read.model_copy(
        update={
            "capital_mode": mode,
            "user_abstraction": normalize_user_abstraction(
                last_reconciliation.get("userAbstraction")
                or last_reconciliation.get("userAbstractionRaw")
            ),
            "tradable_equity_usd": live_tradable_equity_usd(account, settings=settings),
            "perp_equity_usd": live_perp_equity_usd(account),
            "spot_usdc_balance_usd": live_spot_balance_usd(account),
            "spot_usdc_available_usd": live_spot_available_usd(account),
            "capital_balances": live_capital_balance_rows(account, settings=settings),
        }
    )


def live_capital_balance_rows(
    account: TradingAccount,
    *,
    settings: Settings,
) -> list[TradingCapitalBalanceRead]:
    last_reconciliation = account_last_reconciliation(account)
    mode = live_capital_mode(settings)
    if mode == "unified":
        return [
            TradingCapitalBalanceRead(
                key="unified",
                label="Unified USDC",
                equity_usd=live_unified_equity_usd(account),
                available_usd=live_unified_available_usd(account),
                tradable=True,
            )
        ]

    rows: list[TradingCapitalBalanceRead] = []
    states = last_reconciliation.get("perpStates")
    if isinstance(states, list):
        for state in states:
            if not isinstance(state, dict):
                continue
            key = str(state.get("dex") or "default")
            equity = decimal_or_none(state.get("accountValue")) or Decimal("0")
            available = decimal_or_none(state.get("withdrawable"))
            rows.append(
                TradingCapitalBalanceRead(
                    key=key,
                    label="Default perps" if key == "default" else f"{key} perps",
                    equity_usd=equity,
                    available_usd=available,
                    tradable=True,
                )
            )
    spot_balance = live_spot_balance_usd(account)
    spot_available = live_spot_available_usd(account)
    if spot_balance > Decimal("0") or spot_available > Decimal("0"):
        rows.append(
            TradingCapitalBalanceRead(
                key="spot",
                label="Spot USDC",
                equity_usd=spot_balance,
                available_usd=spot_available,
                tradable=False,
            )
        )
    return rows


@router.get("/accounts", response_model=TradingAccountsResponse)
async def list_trading_accounts_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TradingAccountsResponse:
    accounts = await list_trading_accounts(session)
    return TradingAccountsResponse(
        accounts=[
            enriched_trading_account_read(account, settings=settings)
            for account in accounts
        ],
        updated_at=datetime.now(UTC),
    )


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
        response = await trading_account_read(session, account, settings)
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
            validate_live_account_can_start(account, settings=settings)
        response = await trading_account_read(session, account, settings)
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
        validate_live_account_can_start(account, settings=settings)
        response = await trading_account_read(session, account, settings)
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/accounts/{account_key}/stop", response_model=TradingAccountRead)
async def stop_live_account_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TradingAccountRead:
    try:
        account = await set_live_trading_account_status(
            session,
            account_key=account_key,
            status="exit_only",
        )
        response = await trading_account_read(session, account, settings)
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
