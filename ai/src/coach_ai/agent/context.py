from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from coach_ai.history.types import HistoryTurn, StoredSummary, validate_history
from coach_ai.tools.periods import DEFAULT_TZ

_TEXT_FIELDS = ("user_id", "conversation_id", "request_id", "tz")


class CoachProfile(BaseModel):
    """서버가 조회한 등록값과 계산한 BMI. 건강 측정 이력과 별개다."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)

    height_cm: Decimal = Field(gt=0, le=300, decimal_places=2, allow_inf_nan=False, repr=False)
    weight_kg: Decimal = Field(gt=0, le=500, decimal_places=2, allow_inf_nan=False, repr=False)
    # 프로필 허용 범위의 최대 BMI는 500kg / (0.01cm / 100)^2다.
    bmi: Decimal = Field(ge=0, le=50_000_000_000, decimal_places=1, allow_inf_nan=False, repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class CoachContext:
    """인증을 마친 서버가 요청마다 만든다.

    user_id·conversation_id·request_id·tz 는 LLM 출력이나 클라이언트 메시지로 채우지 않는다.
    도구는 이 값의 user_id로만 데이터를 조회한다.
    summary·turns 는 대화 API 가 저장한 이전 대화다. turns 는 summary 뒤의 정상 완료 질문–답변 쌍이고 오래된 순이다.
    profile은 서버가 이번 요청에 조회한 등록값·계산 BMI이며 이전 대화에 저장된 값으로 채우지 않는다.
    """

    user_id: str
    conversation_id: str
    request_id: str
    tz: str = DEFAULT_TZ
    summary: StoredSummary | None = None
    turns: tuple[HistoryTurn, ...] = ()
    profile: CoachProfile | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for name in _TEXT_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name}은 비어 있지 않은 문자열이어야 한다")
        validate_history(self.summary, self.turns)
        if self.profile is not None and not isinstance(self.profile, CoachProfile):
            raise ValueError("profile은 서버가 만든 CoachProfile이어야 한다")
