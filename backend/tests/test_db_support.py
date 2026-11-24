import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.db_support import connect_test_database


def connection_timeout():
    error = OSError("synthetic connection timeout")
    error.winerror = 121
    return error


async def test_initial_connection_retries_windows_timeout_only():
    engine = AsyncMock()
    connection = AsyncMock()
    engine.connect.side_effect = [connection_timeout(), connection]
    await connect_test_database(engine)
    assert engine.connect.await_count == 2
    engine.dispose.assert_awaited_once()
    connection.close.assert_awaited_once()


async def test_initial_connection_retry_is_bounded():
    engine = AsyncMock()
    engine.connect.side_effect = connection_timeout()
    with pytest.raises(OSError):
        await connect_test_database(engine)
    assert engine.connect.await_count == 3
    assert engine.dispose.await_count == 2


@pytest.mark.parametrize("error", [OSError("other"), RuntimeError("sql failure")])
async def test_other_errors_are_not_retried(error):
    engine = AsyncMock()
    engine.connect.side_effect = error
    with pytest.raises(type(error)):
        await connect_test_database(engine)
    engine.connect.assert_awaited_once()
    engine.dispose.assert_not_awaited()


async def test_close_failure_is_not_retried():
    engine = AsyncMock()
    engine.connect.return_value.close.side_effect = connection_timeout()
    with pytest.raises(OSError):
        await connect_test_database(engine)
    engine.connect.assert_awaited_once()


async def test_connection_cancellation_is_not_retried():
    engine = AsyncMock()
    engine.connect.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await connect_test_database(engine)
    engine.connect.assert_awaited_once()
    engine.dispose.assert_not_awaited()
