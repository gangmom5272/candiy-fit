from __future__ import annotations

from collections.abc import Sequence

from langchain_core.utils.env import env_var_is_set
from langsmith.utils import tracing_is_enabled

# langsmith 의 tracing_is_enabled 가 읽는 변수. 라이브러리는 먼저 찾은 값 하나로 판정하므로 넷을 각각 검사한다
TRACING_VARIABLES = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2")


class TracingEnabledError(RuntimeError):
    """LangSmith 추적이 켜진 설정. 문구에는 켜진 변수 이름만 넣고 값은 넣지 않는다."""

    def __init__(self, sources: Sequence[str]) -> None:
        self.sources = tuple(sources)
        super().__init__(f"LangSmith 추적이 켜져 있어 실행을 거부한다: {', '.join(self.sources)}")


def ensure_tracing_disabled() -> None:
    """추적 변수가 하나라도 켜져 있거나 langsmith 판정이 켜짐이면 TracingEnabledError 를 발생시킨다.

    langsmith 는 소문자 "true" 만 켬으로 판정하므로 변수 값은 langchain-core 의 env_var_is_set 기준을 따른다
    ("", "0", "false", "False" 밖의 값은 켬이다).
    """
    sources = [name for name in TRACING_VARIABLES if env_var_is_set(name)]
    if not sources and tracing_is_enabled():
        sources = ["langsmith tracing_is_enabled()"]
    if sources:
        raise TracingEnabledError(sources)
