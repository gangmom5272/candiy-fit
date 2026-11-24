"""진단 정보는 실제 출력과 LogRecord 모두에서 허용 목록만 남긴다."""

import io
import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.health.diagnostics import emit_health_event, health_logging
from tests.health.test_router import health_api as health_api


def test_safe_output_and_repeated_initialization():
    stream = io.StringIO()
    request_id = str(uuid4())
    with health_logging(stream), health_logging(stream):
        emit_health_event(
            "daily_message_read_finished",
            request_id=request_id,
            metric="steps",
            block_seconds=0.1,
            original_kind="RuntimeError-secret",
            sql="secret SQL",
            token="secret token",
        )
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload == {
        "event": "daily_message_read_finished",
        "request_id": request_id,
        "metric": "steps",
        "block_seconds": 0.1,
        "original_kind": "unexpected",
    }
    assert "secret" not in stream.getvalue()


def test_untrusted_id_and_nonfinite_numbers_are_not_logged(caplog):
    with caplog.at_level(logging.INFO, logger="app.health.diagnostics"):
        emit_health_event(
            "member_sync",
            request_id="secret\nforged",
            result="secret",
            seconds=float("nan"),
            body="secret",
        )
    record = caplog.records[-1]
    assert "secret" not in repr(record.__dict__)
    assert record.result == "unknown"


def test_formatter_drops_arbitrary_messages_and_exception_data():
    stream = io.StringIO()
    with health_logging(stream):
        logger = logging.getLogger("app.health.diagnostics")
        logger.info("secret %s", "token")
        try:
            raise ValueError("secret SQL")
        except ValueError:
            logger.exception(
                "member_sync", extra={"result": "SYNCED", "body": "secret"}
            )
    assert "secret" not in stream.getvalue()
    assert [json.loads(line) for line in stream.getvalue().splitlines()] == [
        {"event": "member_sync", "result": "SYNCED"}
    ]


def test_output_failure_does_not_raise_or_print_original(capsys):
    class BrokenStream(io.StringIO):
        def write(self, value):
            raise OSError("secret stream failure")

    with health_logging(BrokenStream()):
        emit_health_event("member_sync", result="SYNCED")
    assert "secret" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "event,field,expected",
    [
        ("member_sync", "request_id", {}),
        ("daily_message_read_finished", "metric", {"metric": "unknown"}),
        ("daily_message_score_unavailable", "reason", {"reason": "unknown"}),
        ("member_sync", "result", {"result": "unknown"}),
    ],
)
def test_objects_are_not_stringified(event, field, expected):
    calls = []

    class Dangerous:
        def __str__(self):
            calls.append("str")
            raise AssertionError("must not stringify input")

        def __repr__(self):
            calls.append("repr")
            raise AssertionError("must not stringify input")

    stream = io.StringIO()
    with health_logging(stream):
        emit_health_event(event, **{field: Dangerous()})
    assert calls == []
    assert json.loads(stream.getvalue()) == {"event": event, **expected}


async def test_app_lifespan_enables_output_and_restores_logger(
    app, monkeypatch, capsys
):
    from app import main

    engine = SimpleNamespace(dispose=AsyncMock())

    @asynccontextmanager
    async def coaching(*args, **kwargs):
        yield

    monkeypatch.setattr(main, "create_engine", lambda _: engine)
    monkeypatch.setattr(main, "create_sessionmaker", lambda _: object())
    monkeypatch.setattr(main, "coaching_lifespan", coaching)
    logger = logging.getLogger("app.health.diagnostics")
    before = (logger.level, logger.propagate, list(logger.handlers))
    async with app.router.lifespan_context(app):
        emit_health_event("member_sync", result="SYNCED")
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line) for line in lines] == [
        {"event": "member_sync", "result": "SYNCED"}
    ]
    assert (logger.level, logger.propagate, logger.handlers) == before
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("kind", ["sql", "validation", "serialization", "unexpected"])
async def test_error_response_and_final_output_do_not_expose_canaries(
    client, health_api, kind, caplog
):
    from pydantic import BaseModel, ValidationError
    from pydantic_core import PydanticSerializationError
    from sqlalchemy.exc import OperationalError

    from app.health.lookup import lookup_boundary

    canary = "SYNTHETIC-PRIVATE-SQL-TOKEN-HEALTH"
    errors = {
        "sql": OperationalError(
            f"select '{canary}'", {"token": canary}, OSError(canary)
        ),
        "serialization": PydanticSerializationError(canary),
        "unexpected": RuntimeError(canary),
    }

    class Input(BaseModel):
        value: int

    try:
        Input(value=canary)
    except ValidationError as error:
        errors["validation"] = error

    async def fail(*args, **kwargs):
        emit_health_event(
            "daily_message_score_unavailable",
            reason="MISMATCH",
            raw=canary,
            token=canary,
            user_id=str(health_api.owner),
        )
        async with lookup_boundary(("steps",)):
            raise errors[kind]

    health_api.lookup.side_effect = fail
    stream = io.StringIO()
    with health_logging(stream):
        response = await client.get(
            "/api/v1/health/metrics/steps?start=2026-10-16&end=2026-10-22",
            headers=health_api.headers
            | {"Cookie": f"synthetic={canary}", "X-Request-Id": canary},
        )
        invalid = await client.get(
            f"/api/v1/health/metrics/steps?start={canary}&end=2026-10-22",
            headers=health_api.headers,
        )
    assert response.status_code == (500 if kind == "unexpected" else 503)
    if kind != "unexpected":
        assert response.json() == {
            "error": {
                "code": "HEALTH_DATA_UNAVAILABLE",
                "message": "건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                "context": {"reason": "LOOKUP_FAILED", "metrics": ["steps"]},
            }
        }
    assert invalid.status_code == 422
    output = (
        response.text
        + str(response.headers)
        + invalid.text
        + stream.getvalue()
        + caplog.text
    )
    assert canary not in output
    assert health_api.headers["Authorization"] not in output
    assert str(health_api.owner) not in output
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(events) == (1 if kind == "unexpected" else 2)
    assert events[0] == {
        "event": "daily_message_score_unavailable",
        "reason": "MISMATCH",
    }


async def test_sync_completion_keeps_only_server_id_and_fixed_error(settings):
    import asyncio
    from time import monotonic

    from app.health.sync_service import MemberSyncRunner

    runner = MemberSyncRunner(settings)
    owner = uuid4()
    request_id = str(uuid4())
    # Even exception class names can contain uncontrolled text.
    private_error = type("PRIVATE-CLASS", (RuntimeError,), {})
    task = asyncio.get_running_loop().create_future()
    task.set_exception(private_error("PRIVATE-BODY"))
    runner._tasks[owner] = task
    stream = io.StringIO()
    with health_logging(stream):
        runner._finished(owner, request_id, monotonic(), task)
    payload = json.loads(stream.getvalue())
    assert payload["request_id"] == request_id
    assert payload["result"] == "unexpected"
    assert "PRIVATE" not in stream.getvalue()
    assert str(owner) not in stream.getvalue()
    assert not runner._tasks


def test_overlapping_lifespans_and_root_handler_do_not_duplicate():
    stream, root_output = io.StringIO(), io.StringIO()
    root = logging.getLogger()
    handler = logging.StreamHandler(root_output)
    root.addHandler(handler)
    first, second = health_logging(stream), health_logging(stream)
    try:
        first.__enter__()
        second.__enter__()
        first.__exit__(None, None, None)
        emit_health_event("member_sync", result="SYNCED")
        second.__exit__(None, None, None)
    finally:
        root.removeHandler(handler)
        handler.close()
    assert len(stream.getvalue().splitlines()) == 1
    assert root_output.getvalue() == ""
