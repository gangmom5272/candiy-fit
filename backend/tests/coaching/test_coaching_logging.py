"""app.coaching 로거에만 INFO 표준 출력을 켜는 설정을 검사한다."""

import logging
import sys
from collections.abc import Iterator
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

from app.coaching.agent import CoachAgent
from app.coaching.lifecycle import (
    COACHING_LOGGER,
    coaching_lifespan,
    configure_coaching_logging,
)
from app.coaching.runner import CoachingRunner
from app.core.config import Settings
from tests.coaching.fakes import FakeSessionFactory

HANDLER_NAME = "coaching-stdout"
OTHER_LOGGERS = ("app.auth.x", "app.health.x")


def _state(name: str | None) -> tuple[int, list[logging.Handler]]:
    """로거의 레벨과 핸들러 목록 사본을 돌려준다."""
    target = logging.getLogger(name)
    return target.level, list(target.handlers)


def _named(name: str) -> list[logging.Handler]:
    """app.coaching 로거에 붙은 해당 이름의 핸들러 목록을 돌려준다."""
    return [
        h for h in logging.getLogger(COACHING_LOGGER).handlers if h.get_name() == name
    ]


@pytest.fixture(autouse=True)
def restore_loggers() -> Iterator[None]:
    """테스트 전 로거 상태를 저장하고 coaching-stdout 핸들러를 치운 뒤, 끝나면 되돌린다."""
    names = [None, COACHING_LOGGER, *OTHER_LOGGERS]
    saved = {n: _state(n) for n in names}
    coaching = logging.getLogger(COACHING_LOGGER)
    coaching.handlers[:] = [
        h for h in coaching.handlers if h.get_name() != HANDLER_NAME
    ]
    yield
    for name, (level, handlers) in saved.items():
        target = logging.getLogger(name)
        target.setLevel(level)
        target.handlers[:] = handlers


def test_configure_sets_info_level_and_one_handler() -> None:
    """G1 한 번 호출 → app.coaching 레벨 INFO, coaching-stdout 핸들러 1개."""
    configure_coaching_logging()
    assert logging.getLogger(COACHING_LOGGER).level == logging.INFO
    assert len(_named(HANDLER_NAME)) == 1


def test_configure_twice_keeps_single_handler() -> None:
    """G2 두 번 호출 → 핸들러는 여전히 1개."""
    configure_coaching_logging()
    configure_coaching_logging()
    assert len(_named(HANDLER_NAME)) == 1


def test_coaching_child_logger_info_goes_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """G3 app.coaching.runner INFO → 표준 출력에 한 번 나온다."""
    configure_coaching_logging()
    (handler,) = _named(HANDLER_NAME)
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(sys.stdout)  # capsys가 바꾼 stdout을 잡게 한다.
    logging.getLogger("app.coaching.runner").info("합성 메시지")
    out = capsys.readouterr().out
    assert out.count("INFO app.coaching.runner 합성 메시지") == 1


def test_other_domains_are_not_changed(capsys: pytest.CaptureFixture[str]) -> None:
    """G4 app.auth·app.health INFO → 출력 없음, 그 로거 설정 불변."""
    before = {n: _state(n) for n in OTHER_LOGGERS}
    configure_coaching_logging()
    for name in OTHER_LOGGERS:
        logging.getLogger(name).info("다른 도메인")
    assert "다른 도메인" not in capsys.readouterr().out
    assert {n: _state(n) for n in OTHER_LOGGERS} == before


def test_root_logger_is_not_changed() -> None:
    """G5 루트 로거 → 호출 전후 레벨·핸들러 동일."""
    before = _state(None)
    configure_coaching_logging()
    assert _state(None) == before


async def test_lifespan_entry_attaches_handler(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G6 coaching_lifespan 진입 → coaching-stdout 핸들러가 붙어 있다."""
    runner = Mock(spec=CoachingRunner)
    runner.aclose = AsyncMock()
    agent = Mock(spec=CoachAgent)
    agent.aclose = AsyncMock()
    monkeypatch.setattr(
        "app.coaching.lifecycle.CoachingRunner", Mock(return_value=runner)
    )
    assert _named(HANDLER_NAME) == []
    async with coaching_lifespan(
        FastAPI(),
        settings,
        FakeSessionFactory(),
        agent=agent,
        cleanup=AsyncMock(return_value=0),
    ):
        assert len(_named(HANDLER_NAME)) == 1
