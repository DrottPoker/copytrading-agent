from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.paper_trading import (
    PaperTradingAccountCreateRequest,
    PaperTradingSummaryResponse,
)
from app.services.paper_trading_service import (
    PaperAccountControlError,
    PaperAccountCreateError,
    PaperAccountDeleteError,
    PaperAccountResetError,
    PaperPositionCloseError,
    close_paper_account_positions_manually,
    close_paper_position_manually,
    close_paper_source_positions_manually,
    create_paper_trading_account,
    delete_paper_trading_account,
    get_paper_trading_summary,
    reset_paper_trading_account_balance,
    set_paper_trading_account_enabled,
)

router = APIRouter(prefix="/paper-trading", tags=["paper-trading"])


@router.get("", response_model=PaperTradingSummaryResponse)
async def get_paper_trading_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    recent_fill_limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    closed_trade_limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> PaperTradingSummaryResponse:
    return await get_paper_trading_summary(
        session,
        settings=settings,
        recent_fill_limit=recent_fill_limit,
        closed_trade_limit=closed_trade_limit,
    )


@router.post("/accounts", response_model=PaperTradingSummaryResponse)
async def create_paper_account_route(
    payload: PaperTradingAccountCreateRequest,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PaperTradingSummaryResponse:
    try:
        await create_paper_trading_account(
            session,
            account_type=payload.account_type,
            starting_balance_usd=payload.starting_balance_usd,
            settings=settings,
        )
        await session.commit()
    except PaperAccountCreateError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    async with HyperliquidClient(settings) as client:
        return await get_paper_trading_summary(session, settings=settings, client=client)


@router.delete("/accounts/{account_key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_paper_account_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    try:
        await delete_paper_trading_account(
            session,
            account_key=account_key,
            settings=settings,
        )
        await session.commit()
    except PaperAccountDeleteError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/positions/{position_id}/close", response_model=PaperTradingSummaryResponse)
async def close_paper_position_route(
    position_id: UUID,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PaperTradingSummaryResponse:
    async with HyperliquidClient(settings) as client:
        try:
            await close_paper_position_manually(
                session,
                position_id=position_id,
                settings=settings,
                client=client,
            )
            await session.commit()
        except PaperPositionCloseError as exc:
            await session.rollback()
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        return await get_paper_trading_summary(session, settings=settings, client=client)


@router.post("/sources/{source_wallet}/close", response_model=PaperTradingSummaryResponse)
async def close_paper_source_positions_route(
    source_wallet: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PaperTradingSummaryResponse:
    async with HyperliquidClient(settings) as client:
        try:
            await close_paper_source_positions_manually(
                session,
                source_wallet=source_wallet,
                settings=settings,
                client=client,
            )
            await session.commit()
        except PaperPositionCloseError as exc:
            await session.rollback()
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        return await get_paper_trading_summary(session, settings=settings, client=client)


@router.post("/accounts/{account_key}/reset", response_model=PaperTradingSummaryResponse)
async def reset_paper_account_balance_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PaperTradingSummaryResponse:
    try:
        await reset_paper_trading_account_balance(
            session,
            account_key=account_key,
            settings=settings,
        )
        await session.commit()
    except PaperAccountResetError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    async with HyperliquidClient(settings) as client:
        return await get_paper_trading_summary(session, settings=settings, client=client)


@router.post("/accounts/{account_key}/start", response_model=PaperTradingSummaryResponse)
async def start_paper_account_trading_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PaperTradingSummaryResponse:
    try:
        await set_paper_trading_account_enabled(
            session,
            account_key=account_key,
            enabled=True,
            settings=settings,
        )
        await session.commit()
    except PaperAccountControlError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    async with HyperliquidClient(settings) as client:
        return await get_paper_trading_summary(session, settings=settings, client=client)


@router.post("/accounts/{account_key}/stop", response_model=PaperTradingSummaryResponse)
async def stop_paper_account_trading_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PaperTradingSummaryResponse:
    try:
        await set_paper_trading_account_enabled(
            session,
            account_key=account_key,
            enabled=False,
            settings=settings,
        )
        await session.commit()
    except PaperAccountControlError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    async with HyperliquidClient(settings) as client:
        return await get_paper_trading_summary(session, settings=settings, client=client)


@router.post(
    "/accounts/{account_key}/close-all-and-stop",
    response_model=PaperTradingSummaryResponse,
)
async def close_all_and_stop_paper_account_trading_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PaperTradingSummaryResponse:
    try:
        await set_paper_trading_account_enabled(
            session,
            account_key=account_key,
            enabled=False,
            settings=settings,
        )
        await session.commit()
    except PaperAccountControlError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    async with HyperliquidClient(settings) as client:
        try:
            await close_paper_account_positions_manually(
                session,
                account_key=account_key,
                settings=settings,
                client=client,
            )
            await session.commit()
        except PaperPositionCloseError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=exc.status_code,
                detail=f"Account trading was stopped, but position close failed: {exc.detail}",
            ) from exc

        return await get_paper_trading_summary(session, settings=settings, client=client)
