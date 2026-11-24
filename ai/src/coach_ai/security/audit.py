from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from coach_ai.tools.periods import DateRange

Decision = Literal["allow", "deny"]


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """도구 호출 하나의 기록. 건강 수치·질문 원문·예외 메시지는 담지 않는다."""

    request_id: str
    user_id: str
    # 한 요청 안에서 게이트웨이가 받은 순서와 그 호출이 든 LLM 응답의 순서(도구 왕복). 둘 다 1부터 센다
    sequence_no: int
    round_no: int
    tool_name: str
    args: dict[str, Any] | None
    # 인자의 기간 표현이 가리키는 날짜. 인자 이름마다 하나이고, 날짜를 정할 수 없는 표현은 넣지 않는다
    period_dates: dict[str, DateRange]
    decision: Decision
    reason: str | None
    # 허용한 호출의 결과 상태. 기록 유무(ok, no_record, unsynced, before_link)나 reason 과 같은 값이고, 거부면 None 이다
    result_status: str | None
    latency_ms: int
    result_count: int
    # 이 호출이 만든 근거의 UUID. 근거 번호 순서이고, 근거가 없는 호출은 비어 있다
    evidence_ids: tuple[str, ...]
    created_at: datetime

    def to_json(self) -> str:
        data = asdict(self)
        data["period_dates"] = {
            name: {"start": r.start.isoformat(), "end": r.end.isoformat()} for name, r in self.period_dates.items()
        }
        data["evidence_ids"] = list(self.evidence_ids)
        data["created_at"] = self.created_at.isoformat(timespec="milliseconds")
        return json.dumps(data, ensure_ascii=False)


class AuditSink(Protocol):
    """도구 호출 기록을 저장하는 곳. DB 기록기는 backend 가 같은 인터페이스로 구현한다."""

    async def write(self, record: ToolCallRecord) -> None: ...


class MemoryAuditSink:
    """최근 기록을 메모리에 둔다."""

    def __init__(self, max_records: int = 1000) -> None:
        self._records: deque[ToolCallRecord] = deque(maxlen=max_records)

    async def write(self, record: ToolCallRecord) -> None:
        self._records.append(record)

    def records(self) -> list[ToolCallRecord]:
        return list(self._records)


class JsonlAuditSink:
    """기록을 JSONL 파일에 한 줄씩 이어 쓴다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # 여러 스레드의 이벤트 루프가 같은 기록기를 쓰면 줄이 섞일 수 있다
        self._lock = threading.Lock()

    async def write(self, record: ToolCallRecord) -> None:
        line = record.to_json()
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


DEFAULT_AUDIT_SINK = MemoryAuditSink()
