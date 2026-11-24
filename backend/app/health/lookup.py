"""건강 조회 예산과 알려진 조회 오류 분류. 자원 정리 경계에는 적용하지 않는다."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from time import monotonic

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.health.errors import HealthDataLookupError, InvalidHealthData
from app.health.schemas import HealthMetric


@asynccontextmanager
async def lookup_boundary(
    metrics: tuple[HealthMetric, ...],
    *,
    seconds: float = 1.0,
    clock: Callable[[], float] = monotonic,
) -> AsyncIterator[None]:
    started = clock()
    try:
        async with asyncio.timeout(seconds):
            yield
            if clock() - started > seconds:
                raise TimeoutError
    except (
        TimeoutError,
        PoolTimeoutError,
        OSError,
        InvalidHealthData,
        ValidationError,
        PydanticSerializationError,
    ) as exc:
        raise HealthDataLookupError(metrics) from exc
    except (OperationalError, InterfaceError) as exc:
        raise HealthDataLookupError(metrics) from exc
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", "") or ""
        if (
            exc.connection_invalidated
            or sqlstate.startswith("08")
            or sqlstate in ("57014", "53300", "57P01", "57P02", "57P03")
        ):
            raise HealthDataLookupError(metrics) from exc
        raise
