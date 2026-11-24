"""연결 확인 스크립트의 실행 거부 조건을 DB·LLM·.env 없이 검사한다."""

import os

import pytest
from langsmith import tracing_context
from langsmith.utils import get_env_var

from scripts.coach_health_check import (
    check_llm_base_url,
    check_tracing_disabled,
    checked_database_url,
)

TRACING_VARIABLES = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)
# "", "0", "false", "False" 밖의 값은 모두 추적 켬으로 판정한다
TRACING_ON_VALUES = ("true", "True", "1", "yes", "no", "off", "FALSE")
REJECTED = "LangSmith 추적이 켜져 있어 실행하지 않습니다: "


@pytest.fixture
def clean_tracing_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("LANGSMITH_", "LANGCHAIN_")):
            monkeypatch.delenv(name)
    # langsmith 는 환경 변수 조회 결과를 캐시하므로 테스트 앞뒤로 비운다
    get_env_var.cache_clear()
    yield
    get_env_var.cache_clear()


@pytest.mark.parametrize("value", TRACING_ON_VALUES)
@pytest.mark.parametrize("name", TRACING_VARIABLES)
def test_check_tracing_disabled_on_value_raises_system_exit(
    clean_tracing_env, monkeypatch, name, value
):
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as exc:
        check_tracing_disabled()
    assert exc.value.code == REJECTED + name


def test_check_tracing_disabled_several_on_lists_all_names(
    clean_tracing_env, monkeypatch
):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "True")
    monkeypatch.setenv("LANGSMITH_TRACING_V2", "no")
    with pytest.raises(SystemExit) as exc:
        check_tracing_disabled()
    assert exc.value.code == REJECTED + "LANGSMITH_TRACING_V2, LANGCHAIN_TRACING_V2"


@pytest.mark.parametrize(
    "environ",
    [
        {},
        dict.fromkeys(TRACING_VARIABLES, "false"),
        {
            "LANGSMITH_TRACING": "False",
            "LANGSMITH_TRACING_V2": "0",
            "LANGCHAIN_TRACING": "",
            "LANGCHAIN_TRACING_V2": "false",
        },
    ],
)
def test_check_tracing_disabled_off_or_unset_passes(
    clean_tracing_env, monkeypatch, environ
):
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    check_tracing_disabled()


def test_check_tracing_disabled_library_judgment_on_raises_system_exit(
    clean_tracing_env,
):
    with tracing_context(enabled=True), pytest.raises(SystemExit) as exc:
        check_tracing_disabled()
    assert exc.value.code == REJECTED + "langsmith tracing_is_enabled()"


@pytest.mark.parametrize(
    "raw_url",
    [
        None,
        "",
        "postgresql+asyncpg://synthetic:synthetic@10.0.0.5:5432/candiy_health_test",
        "postgresql+asyncpg://synthetic:synthetic@localhost:5432/candiy",
        "postgresql://synthetic:synthetic@localhost:5432/candiy_health_test",
    ],
)
def test_checked_database_url_not_dedicated_raises_system_exit(raw_url):
    with pytest.raises(SystemExit):
        checked_database_url(raw_url)


def test_checked_database_url_dedicated_returns_url():
    url = checked_database_url(
        "postgresql+asyncpg://synthetic:synthetic@127.0.0.1:55463/candiy_health_test"
    )
    assert (url.host, url.database) == ("127.0.0.1", "candiy_health_test")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example.com/v1",
        "https://llm.example.invalid/v1",
        "https://llm.example.invalid.example.com/v1",
    ],
)
def test_check_llm_base_url_not_internal_raises_system_exit(base_url):
    with pytest.raises(SystemExit):
        check_llm_base_url(base_url)


def test_check_llm_base_url_internal_host_passes():
    check_llm_base_url("https://llm.example.invalid/v1")
