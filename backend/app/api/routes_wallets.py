from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.integrations.hyperliquid_client import HyperliquidClientError
from app.schemas.fill import (
    WalletFillImportRequest,
    WalletFillImportResponse,
    WalletFillListResponse,
)
from app.schemas.pool_import import PoolFillImportResponse
from app.schemas.source_trade import SourceTradeListResponse
from app.schemas.trade import CopyTradeListResponse
from app.schemas.wallet import WalletCreate, WalletListResponse, WalletRead, WalletUpdate
from app.schemas.wallet_cleanup import (
    CurrentDrawdownPruneResponse,
    HighFillLowScorePruneResponse,
    NonPerpWalletPruneResponse,
    WalletPruneAllResponse,
    ZeroFillWalletPruneResponse,
)
from app.schemas.wallet_stats import WalletStatsResponse
from app.services.fill_import_service import (
    FillImportStorageLimitError,
    import_wallet_fills,
    list_wallet_fills,
)
from app.services.pool_fill_import_service import import_due_pool_wallet_fills
from app.services.source_trade_reconstruction_service import list_reconstructed_source_trades
from app.services.wallet_cleanup_service import (
    prune_all_wallets,
    prune_current_drawdown_wallets,
    prune_high_fill_low_score_wallets,
    prune_non_perp_wallets,
    prune_zero_fill_wallets,
)
from app.services.wallet_service import (
    WalletNotFoundError,
    create_wallet,
    delete_wallet,
    get_wallet,
    list_wallets,
    update_wallet,
)
from app.services.wallet_stats_service import get_wallet_stats, list_wallet_copy_trades


def wallet_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found.")

router = APIRouter(prefix="/wallets", tags=["wallets"])


@router.get("", response_model=WalletListResponse)
async def list_wallets_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    enabled: Annotated[bool | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WalletListResponse:
    wallets, total = await list_wallets(
        session=session,
        enabled=enabled,
        query=q,
        limit=limit,
        offset=offset,
    )
    return WalletListResponse(items=wallets, total=total, limit=limit, offset=offset)


@router.post("/fills/import-pool", response_model=PoolFillImportResponse)
async def import_due_pool_wallet_fills_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    max_batches: Annotated[int | None, Query(ge=1, le=1000)] = None,
    include_items: Annotated[bool, Query()] = True,
    force: Annotated[bool, Query()] = True,
) -> PoolFillImportResponse:
    response = await import_due_pool_wallet_fills(
        session,
        limit=limit or settings.pool_fill_import_batch_size,
        days=settings.pool_fill_import_days,
        max_pages=settings.pool_fill_import_max_pages,
        min_wallet_interval_seconds=settings.pool_fill_import_min_wallet_interval_seconds,
        overlap_seconds=settings.pool_fill_import_overlap_seconds,
        max_batches=max_batches or settings.pool_fill_import_max_batches,
        force=force,
    )
    if not include_items:
        response.items = []
    return response


@router.post("/prune-non-perp", response_model=NonPerpWalletPruneResponse)
async def prune_non_perp_wallets_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    dry_run: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> NonPerpWalletPruneResponse:
    return await prune_non_perp_wallets(session, dry_run=dry_run, limit=limit)


@router.post("/prune-zero-fill", response_model=ZeroFillWalletPruneResponse)
async def prune_zero_fill_wallets_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    dry_run: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=1000)] = 250,
) -> ZeroFillWalletPruneResponse:
    return await prune_zero_fill_wallets(session, dry_run=dry_run, limit=limit)


@router.post("/prune-all", response_model=WalletPruneAllResponse)
async def prune_all_wallets_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    dry_run: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    concurrency: Annotated[int | None, Query(ge=1, le=25)] = None,
) -> WalletPruneAllResponse:
    return await prune_all_wallets(
        session,
        dry_run=dry_run,
        high_fill_min_fills=settings.wallet_prune_low_score_min_fills,
        high_fill_score_threshold=settings.wallet_prune_low_score_threshold,
        high_fill_score_operator=settings.wallet_prune_low_score_operator,
        min_closed_trades=settings.wallet_prune_min_closed_trades,
        max_drawdown_threshold_pct=settings.wallet_prune_max_drawdown_pct,
        current_drawdown_threshold_ratio=settings.wallet_prune_unrealized_loss_ratio,
        current_drawdown_concurrency=(
            concurrency or settings.wallet_prune_current_state_concurrency
        ),
        limit=limit,
    )


@router.post("/prune-current-drawdown", response_model=CurrentDrawdownPruneResponse)
async def prune_current_drawdown_wallets_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    dry_run: Annotated[bool, Query()] = True,
    threshold_ratio: Annotated[Decimal | None, Query(ge=0, le=1)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 250,
    concurrency: Annotated[int | None, Query(ge=1, le=25)] = None,
) -> CurrentDrawdownPruneResponse:
    return await prune_current_drawdown_wallets(
        session,
        dry_run=dry_run,
        threshold_ratio=threshold_ratio or settings.wallet_prune_unrealized_loss_ratio,
        limit=limit,
        concurrency=concurrency or settings.wallet_prune_current_state_concurrency,
    )


@router.post("/prune-high-fill-low-score", response_model=HighFillLowScorePruneResponse)
async def prune_high_fill_low_score_wallets_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    dry_run: Annotated[bool, Query()] = True,
    min_fills: Annotated[int | None, Query(ge=0)] = None,
    score_threshold: Annotated[Decimal | None, Query(ge=0, le=100)] = None,
    score_operator: Annotated[str | None, Query(pattern="^(lte|gte)$")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 250,
) -> HighFillLowScorePruneResponse:
    return await prune_high_fill_low_score_wallets(
        session,
        dry_run=dry_run,
        min_fills=min_fills
        if min_fills is not None
        else settings.wallet_prune_low_score_min_fills,
        score_threshold=(
            score_threshold
            if score_threshold is not None
            else settings.wallet_prune_low_score_threshold
        ),
        score_operator=score_operator or settings.wallet_prune_low_score_operator,
        limit=limit,
    )


@router.post("", response_model=WalletRead, status_code=status.HTTP_201_CREATED)
async def create_wallet_route(
    payload: WalletCreate,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> WalletRead:
    try:
        wallet = await create_wallet(session, payload)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Wallet already exists.",
        ) from exc

    return WalletRead.model_validate(wallet)


@router.get("/{address}", response_model=WalletRead)
async def get_wallet_route(
    address: str,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> WalletRead:
    try:
        wallet = await get_wallet(session, address)
    except (ValueError, WalletNotFoundError) as exc:
        raise wallet_error(exc) from exc

    return WalletRead.model_validate(wallet)


@router.get("/{address}/stats", response_model=WalletStatsResponse)
async def get_wallet_stats_route(
    address: str,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> WalletStatsResponse:
    try:
        await get_wallet(session, address)
        return await get_wallet_stats(session, address=address)
    except (ValueError, WalletNotFoundError) as exc:
        raise wallet_error(exc) from exc


@router.get("/{address}/fills", response_model=WalletFillListResponse)
async def list_wallet_fills_route(
    address: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WalletFillListResponse:
    try:
        await get_wallet(session, address)
        fills, total = await list_wallet_fills(
            session=session,
            address=address,
            limit=limit,
            offset=offset,
        )
    except (ValueError, WalletNotFoundError) as exc:
        raise wallet_error(exc) from exc

    return WalletFillListResponse(items=fills, total=total, limit=limit, offset=offset)


@router.get("/{address}/copy-trades", response_model=CopyTradeListResponse)
async def list_wallet_copy_trades_route(
    address: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CopyTradeListResponse:
    try:
        await get_wallet(session, address)
        return await list_wallet_copy_trades(
            session=session,
            address=address,
            limit=limit,
            offset=offset,
        )
    except (ValueError, WalletNotFoundError) as exc:
        raise wallet_error(exc) from exc


@router.get("/{address}/source-trades", response_model=SourceTradeListResponse)
async def list_wallet_source_trades_route(
    address: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    days: Annotated[int | None, Query(ge=1, le=365)] = None,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SourceTradeListResponse:
    try:
        await get_wallet(session, address)
        return await list_reconstructed_source_trades(
            session,
            address=address,
            days=days or settings.scoring_window_days,
            limit=limit,
            offset=offset,
        )
    except (ValueError, WalletNotFoundError) as exc:
        raise wallet_error(exc) from exc


@router.post("/{address}/fills/import", response_model=WalletFillImportResponse)
async def import_wallet_fills_route(
    address: str,
    payload: WalletFillImportRequest,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> WalletFillImportResponse:
    try:
        return await import_wallet_fills(session=session, address=address, payload=payload)
    except (ValueError, WalletNotFoundError) as exc:
        raise wallet_error(exc) from exc
    except FillImportStorageLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail=str(exc),
        ) from exc
    except HyperliquidClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except SQLAlchemyError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database import failed.",
        ) from exc


@router.patch("/{address}", response_model=WalletRead)
async def update_wallet_route(
    address: str,
    payload: WalletUpdate,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> WalletRead:
    try:
        wallet = await update_wallet(session, address, payload)
    except (ValueError, WalletNotFoundError) as exc:
        raise wallet_error(exc) from exc

    return WalletRead.model_validate(wallet)


@router.delete("/{address}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_wallet_route(
    address: str,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> None:
    try:
        await delete_wallet(session, address)
    except (ValueError, WalletNotFoundError) as exc:
        raise wallet_error(exc) from exc
