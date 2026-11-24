from __future__ import annotations

import copy
import operator
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from coach_ai.agent.rules.guard import GuardCategory
from coach_ai.tools.results import PeriodSpan
from coach_ai.tools.schemas import PublicMetric

# stop·length 는 LLM 종료 사유다. guard 는 경계 확인 안내, tool_limit 은 도구 왕복 상한 뒤 답변이다
FinishReason = Literal["stop", "length", "guard", "tool_limit", "error"]


@dataclass(frozen=True, slots=True)
class Evidence:
    """답변 근거로 쓴 도구 결과 하나. id 는 UUID, position 은 한 답변 안의 순서(1부터)다.

    대화 API 가 id 를 그대로 coaching_evidence.id 로, snapshot 을 그대로 coaching_evidence.snapshot 으로 저장한다.
    도구 메시지에서는 ref(E1 …)로 가리킨다. ref 는 도구 메시지에 넣는 근거만 차례로 센 번호라 근거 이벤트로만
    보내는 근거가 앞에 있으면 position 과 다르고, 근거 이벤트로만 보내는 근거는 None 이다.
    """

    id: str
    position: int
    tool: str
    # 공개 지표 코드 (heart_rates 등). 지표 근거는 snapshot 의 metric 과 같고, kind 근거는 kind 마다 정한 지표다
    metric: PublicMetric
    period: PeriodSpan
    # 화면용 한 줄 ("지난 7일 평균 수면 6시간 26분")
    summary: str
    # 지표 근거는 조회 결과의 공통 원형(API 명세 2.2) 그대로, kind 근거는 포트가 넘긴 snapshot 맨 앞에 kind 를 넣은 값
    snapshot: Mapping[str, Any]
    # 게이트웨이가 늘 넣는다. 대화 API 테스트처럼 게이트웨이 밖에서 만든 근거는 번호 없이 None 이다
    ref: str | None = None

    def event_item(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "position": self.position,
            "tool": self.tool,
            "metric": self.metric,
            "period": self.period.model_dump(mode="json"),
            "summary": self.summary,
            # 이벤트를 받는 쪽이 고쳐도 저장할 근거가 바뀌지 않게 사본을 넣는다
            "snapshot": copy.deepcopy(dict(self.snapshot)),
        }


class CoachState(TypedDict):
    """그래프 상태. LLM 출력이 바꿀 수 있는 값만 두고, 사용자·요청 식별자는 CoachRuntime 으로 넘긴다."""

    messages: Annotated[list[AnyMessage], add_messages]
    evidence: Annotated[list[Evidence], operator.add]
    tool_rounds: int
    finish_reason: FinishReason | None
    # 경계 확인에 걸린 유형. 통과하면 None. 약 일반 지식 답을 고정 답으로 바꿨으면 medication 이다
    blocked_by: GuardCategory | None
    # 경계 확인을 통과한 약 일반 지식 질문이면 답이 검사에 걸렸을 때 보낼 고정 답. 이때는 답을 다 받은 뒤 보낸다
    answer_fallback: str | None
    # 앞 답의 건강 수치를 도구 없이 다시 쓴 첫 답을 되돌린 횟수 (요청마다 많아야 1)
    answer_rechecks: int
    # 앞 답에도 질문에도 없는 건강 수치를 도구 없이 말한 첫 답을 되돌린 횟수. answer_rechecks 와 합쳐 요청마다 많아야 1
    unsupported_rechecks: int
    # 운동이 잠에 준 효과를 물은 질문에 도구 없이 끝난 첫 답을 되돌린 횟수. 위 두 칸과 합쳐 요청마다 많아야 1
    exercise_rechecks: int
    # 효과 단정 끝맺음을 완곡한 말로 바꿔 보낸 문장 수
    hedged_sentences: int
    # 답 글의 내부 표시(사고 채널 표시 등)를 정리한 응답 수
    marker_cleanups: int
