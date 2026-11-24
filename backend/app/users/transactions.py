"""회원 서비스가 소유하는 트랜잭션의 대기 제한."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.transactions import bounded_transaction

MEMBER_DB_TIMEOUT_SECONDS = 5.0
MEMBER_LOCK_TIMEOUT_MS = 1000
MEMBER_STATEMENT_TIMEOUT_MS = 3000


@asynccontextmanager
async def member_transaction(db: AsyncSession) -> AsyncIterator[None]:
    async with bounded_transaction(
        db,
        seconds=MEMBER_DB_TIMEOUT_SECONDS,
        lock_ms=MEMBER_LOCK_TIMEOUT_MS,
        statement_ms=MEMBER_STATEMENT_TIMEOUT_MS,
    ):
        yield
