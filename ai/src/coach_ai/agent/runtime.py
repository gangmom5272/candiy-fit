from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from langchain_core.language_models import BaseChatModel

from coach_ai.agent.budget import PreviousConversation
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import EventEmitter
from coach_ai.agent.executor import ToolExecutor
from coach_ai.history.types import HistoryUsage

MAX_TOOL_ROUNDS = 3


@dataclass(frozen=True, slots=True)
class CoachRuntime:
    """그래프 실행 컨텍스트. 상태와 달리 LLM 출력으로 바뀌지 않는다."""

    today: date
    ctx: CoachContext
    model: BaseChatModel
    executor: ToolExecutor
    events: EventEmitter
    # CoachContext 로 받은 이전 대화. LLM 호출마다 예산에 맞춰 넣을 쌍과 요약을 고른다
    history: PreviousConversation
    max_tool_rounds: int = MAX_TOOL_ROUNDS
    # LLM 호출마다 그 호출에 넣은 이전 대화의 양을 추가한다. 실패로 끝난 요청도 마지막 값을 결과에 담도록
    # 상태 밖에 둔다
    history_sent: list[HistoryUsage] = field(default_factory=list)
    # 답을 다 받은 뒤 보내는 요청(약 일반 지식 질문)에서 아직 보내지 않은 답 조각. 도구 왕복을 거쳐도 이어진다
    held: list[str] = field(default_factory=list)
