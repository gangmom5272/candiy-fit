"""오늘의 한마디 도구 (API 명세 5.5). 홈 화면 카드 문구와 그 문구를 고른 기록을 사실 문장으로 전달한다."""

from __future__ import annotations

import datetime as dt

from coach_ai.agent.executor import ToolOutcome, ToolRequest
from coach_ai.ports import (
    DailyMessageMetric,
    DailyMessageNoticeCode,
    DailyMessageResult,
    DailyMessageStatus,
    ResultContractError,
)
from coach_ai.tools.periods import DateRange, period_label
from coach_ai.tools.results import ToolResult, format_basis_time
from coach_ai.tools.schemas import GetDailyMessage

# 홈 화면 카드의 "기록이 반영된 시각"과 같은 이름과 순서다
METRIC_NAMES: dict[DailyMessageMetric, str] = {"steps": "걸음", "exercises": "운동", "sleeps": "수면"}
# 지시하는 문장은 답에 그대로 나올 수 있어 결과에는 사실 문장만 넣는다. 화면용 안내 문장(notices[].message)은
# 전달하지 않고 안내 코드로 문장을 만든다
NOTICE_FACTS: dict[DailyMessageNoticeCode, str] = {
    "LOOKUP_FAILED": "{name} 기록은 불러오지 못해 문구를 고를 때 쓰지 않았다",
    "DATA_NOT_READY": "{name} 기록은 아직 준비 중이라 문구를 고를 때 쓰지 않았다",
    "SYNC_FAILED": "{name} 기록은 최근 동기화에 실패했다",
}
# 최근 동기화에 실패한 지표의 저장 기록을 문구에 쓴 경우
SYNC_FAILED_USED = "{name} 기록은 최근 동기화에 실패해 이전에 저장된 기록으로 골랐다"
# 문구에 쓴 기록이 없는 안내 문구는 상태로 이유를 적는다
GUIDE_BASIS: dict[DailyMessageStatus, str] = {
    "EMPTY": "오늘 한마디로 소개할 활동·수면 기록이 없어 나온 안내 문구다",
    "WAITING": "오늘 기록이 아직 동기화되지 않아 나온 안내 문구다",
}
NOTE = (
    "홈 화면 오늘의 한마디 카드와 같은 문구다. "
    "오늘 기록에 정해진 규칙을 적용해 고르며, 기록이 동기화되면 바뀔 수 있다. "
    "지난 날짜의 오늘의 한마디만 저장되지 않아 조회할 수 없고, "
    "걸음·수면 같은 지난 기록은 한마디와 별개로 조회할 수 있다"
)
# 문구 코드별로 문구를 고르는 조건
# TODO: 기준값(걸음 수, 운동 시간, 수면 점수)이 정해지면 더한다
CODE_CONDITIONS: dict[str, str] = {}


class DailyMessage(ToolResult):
    """홈 화면 오늘의 한마디 (API 명세 5.5). title·description 은 카드 문구 그대로다."""

    date: dt.date
    display: str
    title: str
    description: str
    # 문구를 고르는 조건. CODE_CONDITIONS 에 있는 코드만 채운다
    condition: str | None = None
    # 문구에 쓴 기록. 기록 없음·동기화 대기 안내 문구는 그 문구가 나온 이유다
    basis: str | None = None
    # 홈 화면 카드의 기록이 반영된 시각과 수면 점수 계산 시각
    recorded_at: str | None = None
    notices: list[str] | None = None
    note: str = NOTE


def _basis(row: DailyMessageResult) -> str | None:
    if not row.used_metrics:
        return GUIDE_BASIS.get(row.status)
    records = "·".join(METRIC_NAMES[metric] for metric in row.used_metrics)
    # 수면 점수 계산 시각은 수면 점수를 실제로 쓴 문구에만 있다
    used = f"{records} 기록과 수면 점수로" if row.score_computed_at is not None else f"{records} 기록으로"
    return f"오늘 {used} 고른 문구다"


def _recorded_at(row: DailyMessageResult, today: dt.date) -> str | None:
    times = [
        f"{name} {format_basis_time(value, today)}"
        for metric, name in METRIC_NAMES.items()
        if (value := getattr(row.data_as_of, metric)) is not None
    ]
    if row.score_computed_at is not None:
        times.append(f"수면 점수 계산 {format_basis_time(row.score_computed_at, today)}")
    return ", ".join(times) or None


def _notices(row: DailyMessageResult) -> list[str] | None:
    facts = []
    for notice in row.notices:
        used = notice.code == "SYNC_FAILED" and notice.metric in row.used_metrics
        template = SYNC_FAILED_USED if used else NOTICE_FACTS[notice.code]
        facts.append(template.format(name=METRIC_NAMES[notice.metric]))
    return facts or None


def daily_message_result(row: DailyMessageResult, today: dt.date) -> DailyMessage:
    """포트의 한마디를 LLM 결과로 바꾼다. 카드 문구는 그대로 쓰고 다시 고르지 않는다."""
    return DailyMessage(
        date=row.date,
        display=period_label("days_ago:0", DateRange(today, today), today),
        title=row.title,
        description=row.description,
        condition=CODE_CONDITIONS.get(row.code),
        basis=_basis(row),
        recorded_at=_recorded_at(row, today),
        notices=_notices(row),
    )


async def get_daily_message(inp: GetDailyMessage, req: ToolRequest) -> ToolOutcome:
    row = await req.port.get_daily_message(req.user_id, req.today, request_id=req.request_id)
    if row.date != req.today:
        raise ResultContractError("한마디 날짜가 요청과 다르다")
    result = daily_message_result(row, req.today)
    # 근거 검증·저장은 지표 원형(API 명세 2.2)만 받아 한마디 결과는 근거로 내보내지 않는다
    return ToolOutcome("ok", result.model_dump(mode="json", exclude_none=True))
