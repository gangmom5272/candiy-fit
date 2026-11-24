import json
import os
from datetime import UTC, datetime

import pytest
import respx
from langsmith import tracing_context
from langsmith import utils as ls_utils

from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.graph import INTERNAL_MESSAGE, run_coach
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.security.tracing import TracingEnabledError
from tests.fakes import FakeHealthData
from tests.llm_fakes import CHAT_URL, text_response

NOW = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)
CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
# langsmith 의 utils.tracing_is_enabled 가 추적 여부를 판정할 때 읽는 변수
VARIABLES = ["LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2"]
ON_VALUES = ["true", "True", "1", "yes"]
# 합성 LangSmith 키. 예외 문구와 이벤트에 나오면 안 된다
SYNTHETIC_KEY = "lsv2_pt_synthetic_0000"
LOCAL_ENDPOINT = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def langsmith_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("LANGSMITH_", "LANGCHAIN_")):
            monkeypatch.delenv(name)
    # 검사가 빠져 추적이 시작돼도 전송이 외부로 나가지 않게 주소를 로컬의 닫힌 포트로 둔다
    monkeypatch.setenv("LANGSMITH_ENDPOINT", LOCAL_ENDPOINT)
    # langsmith 는 환경 변수 조회 결과를 캐시하므로 테스트 앞뒤로 비운다
    ls_utils.get_env_var.cache_clear()
    yield
    ls_utils.get_env_var.cache_clear()


def make_model(settings):
    return create_chat_model(settings, recorder=MetricsRecorder())


async def run(model):
    sink = ListSink()
    result = await run_coach(CTX, "지난 7일 걸음 수 알려줘", port=FakeHealthData(), model=model, sink=sink, now=NOW)
    return result, sink.events


@pytest.mark.parametrize("name", VARIABLES)
def test_each_variable_turns_on_langsmith_tracing(monkeypatch, name):
    monkeypatch.setenv(name, "true")
    assert ls_utils.tracing_is_enabled() is True


@pytest.mark.parametrize("value", ON_VALUES)
@pytest.mark.parametrize("name", VARIABLES)
def test_tracing_on_rejects_model_creation(settings, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(TracingEnabledError) as exc:
        make_model(settings)
    assert exc.value.sources == (name,)


@pytest.mark.parametrize("value", ON_VALUES)
@pytest.mark.parametrize("name", VARIABLES)
@respx.mock
async def test_tracing_on_ends_run_coach_with_internal_error(settings, monkeypatch, name, value):
    model = make_model(settings)
    monkeypatch.setenv(name, value)
    route = respx.post(CHAT_URL).mock(return_value=text_response("x"))
    result, events = await run(model)

    assert [e["type"] for e in events] == ["error"]
    assert (events[0]["code"], events[0]["message"]) == ("internal", INTERNAL_MESSAGE)
    assert result.finish_reason == "error" and result.error_code == "internal"
    assert isinstance(result.exception, TracingEnabledError) and result.exception.sources == (name,)
    assert route.call_count == 0


@pytest.mark.parametrize(
    "environ",
    [
        {},
        dict.fromkeys(VARIABLES, "false"),
        {
            "LANGSMITH_TRACING": "False",
            "LANGSMITH_TRACING_V2": "0",
            "LANGCHAIN_TRACING": "",
            "LANGCHAIN_TRACING_V2": "false",
        },
    ],
)
@respx.mock
async def test_tracing_off_or_unset_keeps_current_behavior(settings, monkeypatch, environ):
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    route = respx.post(CHAT_URL).mock(return_value=text_response("오늘은 ", "가볍게 걸어 보세요."))
    result, events = await run(make_model(settings))

    assert ls_utils.tracing_is_enabled() is False
    assert result.finish_reason == "stop" and result.answer == "오늘은 가볍게 걸어 보세요."
    assert events[-1]["type"] == "done" and route.call_count == 1


@respx.mock
async def test_key_without_tracing_variable_is_allowed_like_library(settings, monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setenv("LANGCHAIN_API_KEY", SYNTHETIC_KEY)
    route = respx.post(CHAT_URL).mock(return_value=text_response("확인"))
    result, events = await run(make_model(settings))

    assert ls_utils.tracing_is_enabled() is False
    assert result.finish_reason == "stop" and events[-1]["type"] == "done" and route.call_count == 1


@pytest.mark.parametrize(
    ("environ", "enabled"),
    [
        ({"LANGSMITH_TRACING_V2": "false", "LANGCHAIN_TRACING_V2": "true"}, "LANGCHAIN_TRACING_V2"),
        ({"LANGSMITH_TRACING_V2": "false", "LANGSMITH_TRACING": "true"}, "LANGSMITH_TRACING"),
    ],
)
def test_variable_hidden_by_library_precedence_is_rejected(settings, monkeypatch, environ, enabled):
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    # langsmith 는 먼저 찾은 LANGSMITH_TRACING_V2 의 false 로 판정하고 나머지 변수는 읽지 않는다
    assert ls_utils.tracing_is_enabled() is False
    with pytest.raises(TracingEnabledError) as exc:
        make_model(settings)
    assert exc.value.sources == (enabled,)


@respx.mock
async def test_library_judgment_on_is_rejected_without_variables(settings):
    model = make_model(settings)
    route = respx.post(CHAT_URL).mock(return_value=text_response("x"))
    with tracing_context(enabled=True):
        with pytest.raises(TracingEnabledError) as exc:
            make_model(settings)
        result, events = await run(model)

    assert exc.value.sources == ("langsmith tracing_is_enabled()",)
    assert [e["type"] for e in events] == ["error"] and events[0]["code"] == "internal"
    assert isinstance(result.exception, TracingEnabledError)
    assert route.call_count == 0


@respx.mock
async def test_rejection_names_variables_without_values(settings, monkeypatch):
    model = make_model(settings)
    monkeypatch.setenv("LANGSMITH_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setenv("LANGCHAIN_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setenv("LANGSMITH_TRACING", "1")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    respx.post(CHAT_URL).mock(return_value=text_response("x"))
    with pytest.raises(TracingEnabledError) as exc:
        make_model(settings)
    result, events = await run(model)

    message = str(exc.value)
    assert set(exc.value.sources) == {"LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"}
    assert "LANGSMITH_TRACING" in message and "LANGCHAIN_TRACING_V2" in message
    for text in (message, str(result.exception), json.dumps(events, ensure_ascii=False)):
        assert SYNTHETIC_KEY not in text and LOCAL_ENDPOINT not in text
