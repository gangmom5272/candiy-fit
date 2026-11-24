"""호출자가 소유한 DB 트랜잭션의 대기 제한. 공개 오류 정책은 도메인이 정한다."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asyncpg.exceptions import PostgresError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def bounded_transaction(
    db: AsyncSession, *, seconds: float, lock_ms: int, statement_ms: int
) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(seconds), db.begin():
            # 트랜잭션 범위 설정이므로 풀의 다음 사용자에게 남지 않는다.
            await db.execute(
                text(
                    "SELECT set_config('lock_timeout', :lock_timeout, true), "
                    "set_config('statement_timeout', :statement_timeout, true)"
                ),
                {
                    "lock_timeout": f"{lock_ms}ms",
                    "statement_timeout": f"{statement_ms}ms",
                },
            )
            yield
    except (DBAPIError, PostgresError) as error:
        original = error.orig if isinstance(error, DBAPIError) else error
        if getattr(original, "sqlstate", None) in {"57014", "55P03"}:
            raise TimeoutError("database_wait_timeout") from None
        raise
