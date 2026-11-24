"""포트 조회의 실패와 기록 상태를 도구 결과로 바꾸는 공통 함수. 문장은 지시 없이 사실만 쓴다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from coach_ai.agent.executor import ToolOutcome
from coach_ai.ports import HealthDataLimitError, HealthDataTargetNotFoundError

LIMIT_CANNOT_NARROW_MESSAGE = "기록이 많아 이 조회는 할 수 없다"
TARGET_NOT_FOUND_MESSAGE = "조회할 기록을 찾지 못했다"
DETAIL_FAILED_MESSAGE = "상세를 불러오지 못했다"
# 한도 단위(limit_unit)는 연동 쪽 코드 값 그대로 받고, 결과 문장에서만 조회 종류(source)와 단위에 맞는 이름표로 쓴다.
# 이름표가 없는 단위는 숫자 뒤에 단위 값을 그대로 붙인다
LIMIT_LABELS: dict[tuple[str, str], str] = {("sleep_sessions", "sessions"): "수면 기록 {limit}개"}

# 새 도구가 결과에 적는 기록 상태. 기존 도구의 결측 표시(handlers.MISSING_LABELS)는 이 표를 쓰지 않는다
RecordState = Literal["기록 없음", "실제 0", "동기화 전", "준비 중", "연동 전", "조회 실패"]


@dataclass(frozen=True, slots=True)
class StateFact:
    # 도구 호출 기록의 result_status 와 같은 값. 준비 중·조회 실패는 포트 오류의 code 다
    status: str
    fact: str


STATE_FACTS: dict[RecordState, StateFact] = {
    "기록 없음": StateFact("no_record", "기록이 없다"),
    "실제 0": StateFact("ok", "0으로 기록되어 있다. 기록이 없다는 뜻이 아니다"),
    "동기화 전": StateFact("unsynced", "아직 동기화되지 않아 기록이 있는지 알 수 없다"),
    "준비 중": StateFact("data_not_ready", "건강 기록을 아직 준비하고 있다"),
    "연동 전": StateFact("before_link", "연동하기 전이라 받은 기록이 없다"),
    "조회 실패": StateFact("data_unavailable", "기록을 불러오지 못했다"),
}


def state_fact(state: RecordState) -> StateFact:
    """기록 상태의 결과 상태와 사실 문장."""
    return STATE_FACTS[state]


def limit_exceeded(tool: str, error: HealthDataLimitError) -> ToolOutcome:
    """한도 초과. 기간을 줄이면 풀릴 수 있으면 그 사실과 아는 한도를, 아니면 이 조회를 할 수 없다는 사실을 쓴다."""
    if error.can_narrow:
        limit = ""
        if error.limit is not None:
            count = f"{error.limit:,}"
            label = LIMIT_LABELS.get((error.source, error.limit_unit or ""))
            limit = f"({count}{error.limit_unit or ''})" if label is None else f"({label.format(limit=count)})"
        message = f"조회 범위의 기록이 한도{limit}를 넘었다. 기간을 줄이면 조회할 수 있다"
    else:
        message = LIMIT_CANNOT_NARROW_MESSAGE
    return ToolOutcome("limit_exceeded", {"tool": tool, "message": message})


def target_not_found(tool: str) -> ToolOutcome:
    """조회할 기록이 없다. 없는 기록과 다른 회원의 기록을 같은 문장으로 쓴다."""
    return ToolOutcome("target_not_found", {"tool": tool, "message": TARGET_NOT_FOUND_MESSAGE})


def lookup_failure(tool: str, error: HealthDataLimitError | HealthDataTargetNotFoundError) -> ToolOutcome:
    """요청을 끝내지 않는 포트 오류를 도구 결과로 바꾼다."""
    if isinstance(error, HealthDataLimitError):
        return limit_exceeded(tool, error)
    return target_not_found(tool)


def detail_failed(base: ToolOutcome) -> ToolOutcome:
    """기본 조회는 성공하고 상세 조회만 실패한 결과. 기본 결과와 근거는 그대로 두고 상세 실패 사실을 붙인다.

    상세 기록이 없는 결과와 상태(detail_failed)와 문장이 다르다.
    """
    if base.status != "ok":
        raise ValueError("상세 실패는 정상인 기본 결과에만 붙인다")
    detail = {"error": "detail_failed", "message": DETAIL_FAILED_MESSAGE}
    return ToolOutcome("detail_failed", base.payload, base.evidence, detail=detail)
