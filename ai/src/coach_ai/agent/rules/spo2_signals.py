"""산소포화도를 묻는 질문에서 생활 요인 비교와 함께 조회하는 이상 신호.

산소포화도가 오르내리는 이유는 기록으로 알 수 없다. 생활 요인 비교(factors)만으로 답하면 조건에 따른 차이가 원인처럼
읽히고, 자는 동안 90% 아래로 떨어진 밤이 되풀이된다는 이상 신호(signals)가 답에서 빠진다. 이런 질문의 첫 호출은 생활
요인 비교만인 일이 많고, 분석 칸 설명을 고치면 다른 질문의 도구 선택이 떨어졌다. 그래서 이번 질문에 산소포화도를
가리키는 말이 있고 수면 기록을 생활 요인 비교로 부른 호출이 있으면, 같은 기간의 이상 신호를 더해 함께 조회한다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from coach_ai.agent.executor import ToolCall

_SLEEP_RECORDS = "get_sleep_records"
# 산소포화도·산소 포화도는 "산소"로 함께 찾는다
_SPO2_WORDS = re.compile(r"산소|spo2", re.IGNORECASE)


def asks_spo2(question: str) -> bool:
    """글에 산소포화도를 가리키는 말(산소포화도, 산소 포화도, 산소, SpO2. 대소문자 무관)이 있는지 반환한다."""
    return _SPO2_WORDS.search(question) is not None


def _analysis(call: ToolCall) -> object:
    if call.name != _SLEEP_RECORDS or not isinstance(call.args, Mapping):
        return None
    return call.args.get("analysis")


def added_signals(calls: Sequence[ToolCall], question: str) -> list[ToolCall | None]:
    """calls 와 같은 자리에 둔, 생활 요인 비교 호출에 더할 이상 신호 호출. 더하지 않는 자리는 None 이다.

    calls 는 한 응답의 호출을 기간 보정(sleep_period)한 호출이고 question 은 이번 질문이다. 이번 질문에 산소포화도를
    가리키는 말이 없거나 같은 응답에 이상 신호 호출이 이미 있으면 모두 None 이다. 이상 신호 호출은 생활 요인 비교 호출의
    기간을 그대로 받고, 그 호출에 기간이 없으면 기간 없이(이상 신호의 기본 기간) 부른다. 한 답의 조회 횟수 한도와 중복은
    게이트웨이가 검사한다.
    """
    if not asks_spo2(question) or any(_analysis(call) == "signals" for call in calls):
        return [None] * len(calls)
    added: list[ToolCall | None] = []
    for index, call in enumerate(calls):
        if _analysis(call) != "factors" or not isinstance(call.args, Mapping):
            added.append(None)
            continue
        args: dict[str, object] = {"analysis": "signals"}
        if call.args.get("period") is not None:
            args["period"] = call.args["period"]
        added.append(ToolCall(f"added-{index}", _SLEEP_RECORDS, args))
    return added
