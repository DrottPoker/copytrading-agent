from collections.abc import AsyncIterator

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker


async def db_session() -> AsyncIterator[AsyncSession]:
    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured.",
        )

    async with sessionmaker() as session:
        yield session
