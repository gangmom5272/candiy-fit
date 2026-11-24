import asyncio
import logging
import threading
from contextlib import contextmanager
from unittest.mock import Mock
from uuid import uuid4

import httpx
import psycopg
import pytest
from candiy_pipeline.metrics import METRICS
from candiy_pipeline.sync import MemberSyncResult
from pydantic import SecretStr

from app.health import sync_repository, sync_service
from app.health.errors import HealthDataNotLinkedError
from app.health.sync_service import HealthSyncError, MemberSyncRunner, run_member_sync


@pytest.fixture
def enabled(settings):
    return settings.model_copy(
        update={
            "pipeline_enabled": True,
            "pipeline_include_members": True,
        }
    )


@pytest.fixture
def worker_io(enabled, monkeypatch):
    events = []
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = ("synthetic-person",)

    @contextmanager
    def connect(**kwargs):
        assert kwargs["autocommit"]
        assert kwargs["connect_timeout"] == 3
        events.append("db_open")
        try:
            yield conn
        finally:
            events.append("db_closed")

    monkeypatch.setattr(sync_service.psycopg, "connect", connect)
    fetch = Mock(return_value=MemberSyncResult("synced"))
    monkeypatch.setattr(sync_service, "sync_member", fetch)
    return conn, fetch, events


@pytest.mark.parametrize(
    "enabled,members", [(False, False), (False, True), (True, False)]
)
def test_disabled_does_not_open_db_or_require_credentials(
    settings, monkeypatch, enabled, members
):
    settings = settings.model_copy(
        update={"pipeline_enabled": enabled, "pipeline_include_members": members}
    )
    monkeypatch.setattr(
        sync_service.psycopg, "connect", Mock(side_effect=AssertionError)
    )
    assert run_member_sync(settings, uuid4()) == "DISABLED"


def test_synthetic_without_candiy_keys_closes_db(enabled, worker_io):
    _, fetch, events = worker_io
    assert run_member_sync(enabled, uuid4()) == "SYNTHETIC"
    fetch.assert_not_called()
    assert events == ["db_open", "db_closed"]


@pytest.mark.parametrize(
    ("status", "failed", "expected"),
    [
        ("synced", [], "SYNCED"),
        ("skipped_recent", [], "SKIPPED_RECENT"),
        ("busy", [], "BUSY"),
        ("auth_failed", [], "CANDIY_AUTH_FAILED"),
        ("aggregate_failed", ["aggregate:private"], "AGGREGATE_FAILED"),
        ("synced", ["steps:http_503", "private:secret"], "METRIC_FAILED"),
        (
            "synced",
            [f"{metric}:http_404" for metric in METRICS],
            "CANDIY_USER_NOT_LINKED",
        ),
        ("synced", ["steps:http_404"], "METRIC_FAILED"),
        (
            "synced",
            [f"{metric}:http_404" for metric in METRICS if metric != "steps"]
            + ["steps:http_503"],
            "METRIC_FAILED",
        ),
    ],
)
def test_worker_maps_results_and_closes_connections(
    enabled, worker_io, monkeypatch, status, failed, expected
):
    conn, fetch, events = worker_io
    conn.execute.return_value.fetchone.return_value = ("ordinary-member",)
    make_client = Mock()
    monkeypatch.setattr(sync_service, "CandiyClient", make_client)
    # 테스트 값도 SecretStr로 감싸 실제 설정과 같은 접근을 검증한다.
    configured = type(enabled)(
        _env_file=None,
        **(
            enabled.model_dump()
            | {
                "candiy_base_url": "http://localhost:4010/candiy",
                "candiy_client_id": "synthetic-client",
                "candiy_client_secret": "synthetic-secret",
            }
        ),
    )
    owner = uuid4()
    fetch.return_value = MemberSyncResult(status, failed_metrics=failed)
    if expected in ("SYNCED", "SKIPPED_RECENT", "BUSY"):
        assert run_member_sync(configured, owner) == expected
    else:
        error_type = (
            HealthDataNotLinkedError
            if expected == "CANDIY_USER_NOT_LINKED"
            else HealthSyncError
        )
        with pytest.raises(error_type) as caught:
            run_member_sync(configured, owner)
        if isinstance(caught.value, HealthSyncError):
            assert caught.value.reason == expected
        else:
            assert caught.value.code == expected
        assert caught.value.metrics == (
            tuple(
                dict.fromkeys(
                    item.split(":", 1)[0]
                    for item in failed
                    if item.split(":", 1)[0] in METRICS
                )
            )
            if expected == "METRIC_FAILED"
            else ()
        )
        assert "private" not in str(caught.value)
    assert fetch.call_args.args[2].member_id == owner
    assert fetch.call_args.kwargs["include_members"] is True
    assert make_client.call_args.kwargs["http"].is_closed
    assert make_client.call_args.kwargs["http"].timeout.read == 5
    assert make_client.call_args.kwargs["max_retries"] == 0
    assert events == ["db_open", "db_closed"]


def test_missing_config_is_safe_and_closes_db(enabled, worker_io):
    conn, fetch, events = worker_io
    conn.execute.return_value.fetchone.return_value = ("ordinary-member",)
    with pytest.raises(HealthSyncError, match="CONFIGURATION_ERROR"):
        run_member_sync(enabled, uuid4())
    fetch.assert_not_called()
    assert events[-1] == "db_closed"


def test_removed_member_does_not_start_external_call(enabled, worker_io):
    conn, fetch, _ = worker_io
    conn.execute.return_value.fetchone.return_value = None
    with pytest.raises(sync_repository.MemberRemovedError):
        run_member_sync(enabled, uuid4())
    fetch.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "https://[broken",
        "https://test:invalid",
        "https://test:99999",
        "https://user:secret@test",
    ],
)
def test_invalid_candiy_url_is_configuration_failure(enabled, worker_io, url):
    conn, fetch, events = worker_io
    conn.execute.return_value.fetchone.return_value = ("ordinary-member",)
    configured = enabled.model_copy(
        update={
            "candiy_base_url": url,
            "candiy_client_id": SecretStr("synthetic-client"),
            "candiy_client_secret": SecretStr("synthetic-secret"),
        }
    )
    with pytest.raises(HealthSyncError, match="CONFIGURATION_ERROR"):
        run_member_sync(configured, uuid4())
    fetch.assert_not_called()
    assert events[-1] == "db_closed"


def test_db_failure_is_classified_without_message(enabled, monkeypatch):
    monkeypatch.setattr(
        sync_service.psycopg,
        "connect",
        Mock(side_effect=psycopg.OperationalError("private")),
    )
    with pytest.raises(HealthSyncError, match="STORAGE_UNAVAILABLE"):
        run_member_sync(enabled, uuid4())


def test_programming_error_is_not_reported_as_temporary_storage(enabled, monkeypatch):
    monkeypatch.setattr(
        sync_service.psycopg,
        "connect",
        Mock(side_effect=psycopg.ProgrammingError("private")),
    )
    with pytest.raises(psycopg.ProgrammingError):
        run_member_sync(enabled, uuid4())


def test_httpx_member_url_is_not_logged(caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info(
            "HTTP Request: %s %s",
            "GET",
            httpx.URL("https://test/api/steps?end_user_id=private"),
        )
    assert "private" not in caplog.text


async def test_timeout_and_cancel_keep_worker_owned_until_cleanup(enabled, monkeypatch):
    monkeypatch.setattr(sync_service, "RESPONSE_WAIT_SECONDS", 0.02)
    release = threading.Event()
    entered = threading.Event()
    finished = threading.Event()

    def worker(settings, owner):
        entered.set()
        try:
            assert release.wait(3)
            return "SYNCED"
        finally:
            finished.set()

    runner = MemberSyncRunner(enabled, worker=worker)
    owner = uuid4()
    try:
        with pytest.raises(HealthSyncError, match="TIMEOUT"):
            await runner.sync(owner, "synthetic-request")
        assert entered.is_set() and not finished.is_set()
        assert await runner.sync(owner, "duplicate") == "BUSY"
        shutdown = asyncio.create_task(runner.aclose())
        await asyncio.sleep(0)
        shutdown.cancel()
        await asyncio.sleep(0)
        assert not shutdown.done()
        with pytest.raises(HealthSyncError, match="SHUTTING_DOWN"):
            await runner.sync(uuid4(), "late")
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    assert finished.is_set() and not runner._tasks


async def test_request_cancel_retains_capacity_and_does_not_block_event_loop(enabled):
    release = threading.Event()

    def worker(settings, owner):
        assert release.wait(3)
        return "SYNCED"

    runner = MemberSyncRunner(enabled, worker=worker)
    first = asyncio.create_task(runner.sync(uuid4(), "one"))
    second = asyncio.create_task(runner.sync(uuid4(), "two"))
    try:
        await asyncio.sleep(0.01)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(HealthSyncError, match="CAPACITY_EXCEEDED"):
            await runner.sync(uuid4(), "three")
        assert len(runner._tasks) == 2
    finally:
        release.set()
        await runner.aclose()
        await second


async def test_unobserved_worker_failure_is_retrieved_and_slot_reused(
    enabled, monkeypatch, caplog
):
    monkeypatch.setattr(sync_service, "RESPONSE_WAIT_SECONDS", 0.001)
    release = threading.Event()

    def worker(settings, owner):
        assert release.wait(3)
        raise RuntimeError("private-body")

    runner = MemberSyncRunner(enabled, worker=worker)
    with pytest.raises(HealthSyncError, match="TIMEOUT"):
        await runner.sync(uuid4(), "safe-request-id")
    release.set()
    await runner.aclose()
    assert not runner._tasks
    assert "private-body" not in caplog.text
