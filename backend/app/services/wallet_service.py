from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WalletScore, WatchedWallet
from app.schemas.wallet import WalletCreate, WalletUpdate, normalize_wallet_address
from app.services.wallet_cleanup_service import delete_wallet_related_rows


class WalletNotFoundError(Exception):
    pass


async def list_wallets(
    session: AsyncSession,
    *,
    enabled: bool | None = None,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[WatchedWallet], int]:
    filters = []
    if enabled is not None:
        filters.append(WatchedWallet.enabled.is_(enabled))
    if query:
        like_query = f"%{query.strip().lower()}%"
        filters.append(
            or_(
                func.lower(WatchedWallet.address).like(like_query),
                func.lower(WatchedWallet.label).like(like_query),
            )
        )

    ranked_scores = wallet_pool_rank_cte()
    base_query = (
        select(WatchedWallet, WalletScore, ranked_scores.c.pool_rank)
        .outerjoin(
            WalletScore,
            WalletScore.wallet_address == WatchedWallet.address,
        )
        .outerjoin(ranked_scores, ranked_scores.c.wallet_address == WatchedWallet.address)
    )
    count_query = select(func.count()).select_from(WatchedWallet)
    for condition in filters:
        base_query = base_query.where(condition)
        count_query = count_query.where(condition)

    result = await session.execute(
        base_query.order_by(
            WalletScore.score.desc().nulls_last(),
            WatchedWallet.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    total = await session.scalar(count_query)
    wallets: list[WatchedWallet] = []
    for wallet, score, pool_rank in result.all():
        wallet.score = score
        wallet.pool_rank = int(pool_rank) if pool_rank is not None else None
        wallets.append(wallet)
    return wallets, int(total or 0)


async def get_wallet(session: AsyncSession, address: str) -> WatchedWallet:
    normalized_address = normalize_wallet_address(address)
    ranked_scores = wallet_pool_rank_cte()
    result = await session.execute(
        select(WatchedWallet, WalletScore, ranked_scores.c.pool_rank)
        .outerjoin(WalletScore, WalletScore.wallet_address == WatchedWallet.address)
        .outerjoin(ranked_scores, ranked_scores.c.wallet_address == WatchedWallet.address)
        .where(WatchedWallet.address == normalized_address)
    )
    row = result.one_or_none()
    wallet = row[0] if row is not None else None
    if wallet is None:
        raise WalletNotFoundError(normalized_address)
    wallet.score = row[1]
    wallet.pool_rank = int(row[2]) if row[2] is not None else None
    return wallet


def wallet_pool_rank_cte():
    return (
        select(
            WalletScore.wallet_address,
            func.row_number()
            .over(
                order_by=(
                    WalletScore.score.desc(),
                    WalletScore.updated_at.desc(),
                    WalletScore.wallet_address.asc(),
                )
            )
            .label("pool_rank"),
        )
        .where(WalletScore.score.is_not(None))
        .cte("wallet_pool_rank")
    )


async def create_wallet(session: AsyncSession, payload: WalletCreate) -> WatchedWallet:
    wallet = WatchedWallet(
        address=payload.address,
        label=payload.label,
        enabled=payload.enabled,
        notes=payload.notes,
    )
    session.add(wallet)
    await session.commit()
    await session.refresh(wallet)
    return wallet


async def update_wallet(
    session: AsyncSession,
    address: str,
    payload: WalletUpdate,
) -> WatchedWallet:
    wallet = await get_wallet(session, address)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(wallet, key, value)

    await session.commit()
    await session.refresh(wallet)
    return wallet


async def delete_wallet(session: AsyncSession, address: str) -> None:
    wallet = await get_wallet(session, address)
    await delete_wallet_related_rows(
        session,
        addresses=[wallet.address],
        strict_protection=True,
    )
    await session.commit()
