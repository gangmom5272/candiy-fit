"""인증 DB 작업의 대기만 제한한다. 공개 오류와 커밋 불확실성은 호출자가 분류한다."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.transactions import bounded_transaction

AUTH_DB_TIMEOUT_SECONDS = 5.0
AUTH_LOCK_TIMEOUT_MS = 1000
AUTH_STATEMENT_TIMEOUT_MS = 3000


@asynccontextmanager
async def auth_transaction(db: AsyncSession) -> AsyncIterator[None]:
    async with bounded_transaction(
        db,
        seconds=AUTH_DB_TIMEOUT_SECONDS,
        lock_ms=AUTH_LOCK_TIMEOUT_MS,
        statement_ms=AUTH_STATEMENT_TIMEOUT_MS,
    ):
        yield
