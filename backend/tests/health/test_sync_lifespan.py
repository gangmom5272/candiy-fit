import asyncio
import threading
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from app import main


async def test_shutdown_waits_for_sync_cleanup_before_disposing_db(
    settings, monkeypatch
):
    events = []
    release = threading.Event()
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    engine = Mock(
        dispose=AsyncMock(side_effect=lambda: events.append("engine_disposed"))
    )
    monkeypatch.setattr(main, "create_engine", lambda settings: engine)
    monkeypatch.setattr(main, "create_sessionmaker", lambda engine: Mock())

    @asynccontextmanager
    async def coaching(app, settings, factory, *, engine):
        assert engine is app.state.engine
        assert factory is app.state.sessionmaker
        yield

    monkeypatch.setattr(main, "coaching_lifespan", coaching)
    application = main.create_app(
        settings.model_copy(
            update={"pipeline_enabled": True, "pipeline_include_members": True}
        )
    )

    def worker(settings, owner):
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(3)
            return "SYNCED"
        finally:
            events.append("sync_closed")

    application.state.member_sync._worker = worker
    lifespan = application.router.lifespan_context(application)
    await lifespan.__aenter__()
    request = asyncio.create_task(
        application.state.member_sync.sync(uuid4(), "test-id")
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await asyncio.sleep(0)
        assert not closing.done()
        assert not events
    finally:
        release.set()
    await closing
    await request
    assert events == ["sync_closed", "engine_disposed"]
