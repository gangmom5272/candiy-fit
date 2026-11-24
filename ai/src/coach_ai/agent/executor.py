from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from langchain_core.utils.function_calling import convert_to_openai_tool

from coach_ai.agent.context import CoachContext
from coach_ai.agent.state import Evidence
from coach_ai.ports import HealthDataPort
from coach_ai.tools.periods import DateRange
from coach_ai.tools.results import PeriodSpan
from coach_ai.tools.schemas import TOOL_INPUTS, PublicMetric, ToolInput

if TYPE_CHECKING:
    from coach_ai.agent.evidence import KindEvidenceDraft

# invalid_periods 는 기간 비교의 두 기간이 겹치거나 순서가 뒤바뀌어, unavailable_period 는 조회 한도를 넘거나 없는
# 날짜라 조회하지 않았다는 안내다. limit_exceeded·target_not_found 는 포트의 한도 초과·대상 없음 안내
# (tools/outcomes.py), detail_failed 는 기본 결과는 있고 상세만 불러오지 못한 결과다
OutcomeStatus = Literal[
    "ok",
    "not_supported",
    "invalid_periods",
    "unavailable_period",
    "limit_exceeded",
    "target_not_found",
    "detail_failed",
]


@dataclass(frozen=True, slots=True)
class EvidenceDraft:
    """근거 후보. UUID 와 순서는 게이트웨이가 호출 순서대로 붙인다."""

    tool: str
    # 공개 지표 코드 (heart_rates 등)
    metric: PublicMetric
    period: PeriodSpan
    summary: str
    # 조회 결과의 공통 원형(API 명세 2.2) 그대로. LLM 에 전달한 결과(payload)와 따로 둔다
    snapshot: Mapping[str, Any]


# exercise_period 는 14일보다 짧은 기간의 생활 요인 비교에 따로 덧붙인 운동 조건 비교의 기간,
# extended_period 는 14일 이상 기간에서 운동 조건 비교가 안 돼 생활 요인 비교 전체를 다시 계산한 기간이다
ExtraPeriodName = Literal["exercise_period", "extended_period"]


@dataclass(frozen=True, slots=True)
class ExtraPeriod:
    """처리 함수가 인자의 기간 밖으로 계산에 쓴 기간. 호출 로그의 period_dates 에 name 으로 더한다."""

    name: ExtraPeriodName
    period: DateRange


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    status: OutcomeStatus
    payload: dict[str, Any]
    # 기간 비교·전체 요약은 조회한 기간·지표마다 근거를 하나씩 만든다. 근거 번호는 이 순서대로 붙는다
    evidence: tuple[EvidenceDraft | KindEvidenceDraft, ...] = ()
    # 상세 조회의 결과나 실패 사실. 기본 결과(payload)와 따로 전달한다
    detail: dict[str, Any] | None = None
    # 인자의 기간 밖으로 계산에 쓴 기간. 인자의 기간만 쓴 결과는 None 이다
    extra_period: ExtraPeriod | None = None
    # 근거 이벤트로만 보내는 근거. 도구 메시지의 근거 번호(evidence_ids)에 넣지 않아 LLM 에 전달하는 도구 메시지가
    # 바뀌지 않는다. 순서(position)는 evidence 뒤에 이어진다
    event_only_evidence: tuple[EvidenceDraft | KindEvidenceDraft, ...] = ()

    def to_message(self, evidence_refs: Sequence[str]) -> dict[str, Any]:
        """도구 메시지 본문. 결과에 근거 번호(E1 …)를 함께 넣어 답변에서 근거를 가리킬 수 있게 한다."""
        if self.status in ("ok", "detail_failed"):
            message = {"evidence_ids": list(evidence_refs), "result": self.payload}
            return message if self.detail is None else {**message, "detail": self.detail}
        return {"error": self.status, **self.payload}


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """도구 처리기가 받는 실행 정보. user_id·request_id 는 CoachContext 에서만 온다."""

    user_id: str
    today: dt.date
    port: HealthDataPort
    # 연동 쪽 조회 로그에 기록하는 코칭 요청 ID. 게이트웨이가 CoachContext.request_id 로 채운다
    request_id: str = ""
    # 기본 결과에 덧붙이는 값만 계산하는 보조 조회의 포트. 게이트웨이가 소유자만 확인하고 호출 로그의 결과 상태와
    # 조회한 날짜 수에는 넣지 않는 포트를 채운다. 없으면 port 로 조회한다
    aux_port: HealthDataPort | None = None
    # 이번 질문(이어진 대화면 바로 앞 사용자 질문까지)에 운동을 뜻하는 말이 있는지. 도구 노드가 정하고
    # (agent/rules/exercise_words.py), 짧은 기간의 생활 요인 비교가 최근 28일 운동 조건 비교를 덧붙일지에 쓴다
    asks_exercise: bool = False


ToolHandler = Callable[[ToolInput, ToolRequest], Awaitable[ToolOutcome]]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """LLM 응답에 든 도구 호출 하나. args 가 None 이면 인자가 JSON 객체가 아니었다."""

    id: str
    name: str
    args: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class AddedCalls:
    """모델이 낸 호출 하나에 에이전트가 더해 함께 실행한 호출. 도구 노드가 그 호출의 도구 메시지 artifact 로 넣는다.

    executed 는 모델이 낸 호출을 실행한 호출(인자를 바꿨으면 바꾼 호출)이고, added 는 더한 호출이다. 더한 호출의 결과
    본문은 그 도구 메시지 본문의 added 목록에 같은 순서로 들어간다.
    """

    executed: ToolCall
    added: tuple[ToolCall, ...]


@dataclass(frozen=True, slots=True)
class ExtendedRun:
    """처리 함수가 인자의 기간 밖 기간까지 계산한 호출. 도구 노드가 그 호출의 도구 메시지 artifact 로 넣는다.

    executed 는 실행한 호출, extra_period 는 인자 밖으로 계산에 쓴 기간, added 는 에이전트가 더해 함께 실행한
    호출(AddedCalls 와 같은 뜻)이다.
    """

    executed: ToolCall
    extra_period: ExtraPeriod
    added: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolReply:
    """LLM 에 도구 메시지로 전달할 본문과 그 호출에서 나온 근거, 인자 밖으로 계산에 쓴 기간."""

    call_id: str
    name: str
    content: str
    evidence: tuple[Evidence, ...] = ()
    extra_period: ExtraPeriod | None = None


class ToolExecutor(Protocol):
    async def execute(
        self,
        calls: Sequence[ToolCall],
        ctx: CoachContext,
        *,
        added: Sequence[ToolCall | None] = (),
        asks_exercise: bool = False,
    ) -> list[ToolReply]:
        """한 응답의 도구 호출을 실행하고 호출 순서대로 결과를 반환한다.

        added 는 에이전트가 calls 의 같은 자리 호출에 더한 호출이다 (더하지 않는 자리는 None, 비어 있으면 더한 호출이
        없다). 더한 호출은 그 자리 호출이 검사를 통과했을 때만 calls 다음에 같은 검사를 하고, 거부될 호출은 실행하지도
        기록하지도 않는다. 실행한 더한 호출의 결과는 calls 의 결과 뒤에 이어 반환한다.
        asks_exercise 는 에이전트가 정한 이번 질문의 운동 말 여부이고, 처리 함수는 ToolRequest 의 같은 칸으로 받는다.
        """
        ...


def not_supported(name: str) -> ToolOutcome:
    return ToolOutcome(
        "not_supported",
        {
            "tool": name,
            "message": "아직 지원하지 않는 도구다. 기록이 없다는 뜻이 아니다. "
            "다른 도구로 답하거나 지원하지 않는다고 말한다.",
        },
    )


def tool_specs() -> list[dict[str, Any]]:
    """LLM 에 묶을 도구 스키마 목록. 이름은 입력 모델 클래스 이름이 아니라 도구 이름을 쓴다."""
    specs = []
    for name, schema in TOOL_INPUTS.items():
        spec = convert_to_openai_tool(schema)
        spec["function"]["name"] = name
        specs.append(spec)
    return specs
