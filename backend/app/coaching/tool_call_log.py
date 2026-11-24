"""코칭 요청 하나의 도구 호출 기록을 모았다가 요청이 끝날 때 저장할 행으로 바꾼다."""

from uuid import UUID

from app.coaching.repository import ToolCallInfo
from coach_ai.security.audit import ToolCallRecord


class ToolCallLog:
    """AI 게이트웨이의 호출 기록을 요청이 끝날 때까지 메모리에 모으는 AuditSink 구현.

    write는 DB에 접근하지 않아 답변 생성과 스트리밍을 기다리게 하지 않는다.
    요청마다 새로 만들고, 저장은 실행기가 요청을 종결한 뒤 한 번에 한다.
    """

    def __init__(self) -> None:
        """빈 기록 목록을 준비한다."""
        self._records: list[ToolCallRecord] = []

    async def write(self, record: ToolCallRecord) -> None:
        """게이트웨이가 호출 순서대로 쓰는 기록을 그대로 모은다."""
        self._records.append(record)

    @property
    def records(self) -> tuple[ToolCallRecord, ...]:
        """지금까지 모은 기록을 받은 순서대로 반환한다."""
        return tuple(self._records)

    def rows(self) -> list[ToolCallInfo]:
        """모은 기록을 저장할 행으로 바꾼다.

        회원 ID는 저장할 때 요청 행에서 가져오므로 AI 기록의 회원 ID는 쓰지 않는다.
        """
        return [tool_call_row(record) for record in self._records]


def tool_call_row(record: ToolCallRecord) -> ToolCallInfo:
    """AI 기록 하나를 coaching_tool_call 행으로 바꾼다.

    거부 사유는 거부한 호출에만, 결과 상태는 허용한 호출에만 넣는다.
    """
    return ToolCallInfo(
        request_id=UUID(record.request_id),
        sequence_no=record.sequence_no,
        round_no=record.round_no,
        tool_name=record.tool_name,
        args=record.args,
        period_dates={
            name: {"start": r.start.isoformat(), "end": r.end.isoformat()}
            for name, r in record.period_dates.items()
        },
        decision=record.decision,
        deny_reason=record.reason if record.decision == "deny" else None,
        result_status=record.result_status if record.decision == "allow" else None,
        latency_ms=record.latency_ms,
        result_count=record.result_count,
        evidence_ids=tuple(UUID(evidence_id) for evidence_id in record.evidence_ids),
        created_at=record.created_at,
    )
