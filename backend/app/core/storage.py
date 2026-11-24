"""저장소 연결 장애 판정만 공유한다. 트랜잭션·공개 오류 코드는 호출자가 결정한다."""

from socket import gaierror

from asyncpg.exceptions import PostgresError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

STORAGE_ERRORS = (
    ConnectionError,
    TimeoutError,
    PoolTimeoutError,
    gaierror,
    DBAPIError,
    PostgresError,
)


def is_temporary_storage_error(error: Exception) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError, PoolTimeoutError, gaierror)):
        return True
    if isinstance(error, DBAPIError) and error.connection_invalidated:
        return True
    original = error.orig if isinstance(error, DBAPIError) else error
    sqlstate = getattr(original, "sqlstate", None)
    return (
        isinstance(error, (DBAPIError, PostgresError))
        and isinstance(sqlstate, str)
        and (
            sqlstate.startswith("08")
            or sqlstate in {"57P01", "57P02", "57P03", "53300"}
        )
    )
