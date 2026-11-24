from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coach_ai.adapters.memory import InMemoryHealthData
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall, ToolReply
from coach_ai.ports import HealthDataError, MetricResult
from coach_ai.security.audit import JsonlAuditSink, MemoryAuditSink, ToolCallRecord
from coach_ai.security.gateway import CALL_LIMIT_MESSAGE, DENIED_MESSAGE, LOOKUP_TIMEOUT_S, ToolGateway
from coach_ai.tools.periods import local_today
from coach_ai.tools.schemas import Metric

USER = "demo-user"
OTHER_USER = "other-user"
UNLINKED_USER = "unlinked-user"


def _summary(period: str, metric: Metric = "steps") -> tuple[str, dict[str, Any]]:
    return ("get_metric_summary", {"metric": metric, "period": period})


@dataclass(frozen=True)
class Case:
    """시연 사례 하나. 사례마다 새 요청으로 게이트웨이를 지난다."""

    title: str
    calls: list[tuple[str, dict[str, Any]]]
    port: str = "normal"
    user: str = USER


CASES = [
    Case("정상: 걸음 수 요약", [_summary("last_7_days")]),
    Case("정상: 수면 기록", [("get_sleep_records", {"period": "last_7_days"})]),
    Case("등록 안 된 도구", [("export_all_users", {"format": "csv"})]),
    Case("다른 회원 ID 주입", [("get_metric_summary", {"metric": "steps", "period": "today", "user_id": OTHER_USER})]),
    Case("열거형 밖 기간", [("get_sleep_records", {"period": "all"})]),
    Case("SQL 조각", [("get_metric_summary", {"metric": "steps' OR 1=1--", "period": "today"})]),
    Case("같은 호출 반복", [_summary("last_7_days"), _summary("last_7_days")]),
    Case(
        "호출 횟수 초과",
        [_summary(p) for p in ("today", "yesterday", "last_7_days", "last_14_days", "last_30_days", "this_week")]
        + [_summary("last_week")],
    ),
    Case("다른 회원 결과 반환", [_summary("last_7_days", "heart_rate")], port="leaking"),
    Case("조회 1초 초과", [_summary("last_7_days", "heart_rate")], port="slow"),
    Case("미연동 회원", [_summary("last_7_days")], user=UNLINKED_USER),
]


class _LeakingPort(InMemoryHealthData):
    async def get_metric_result(self, user_id: str, metric: Metric, start: dt.date, end: dt.date) -> MetricResult:
        return await super().get_metric_result(OTHER_USER, metric, start, end)


class _SlowPort(InMemoryHealthData):
    delay = LOOKUP_TIMEOUT_S + 0.2

    async def get_metric_result(self, user_id: str, metric: Metric, start: dt.date, end: dt.date) -> MetricResult:
        await asyncio.sleep(self.delay)
        return await super().get_metric_result(user_id, metric, start, end)


class _TeeSink:
    def __init__(self, *sinks: MemoryAuditSink | JsonlAuditSink) -> None:
        self._sinks = sinks

    async def write(self, record: ToolCallRecord) -> None:
        for sink in self._sinks:
            await sink.write(record)


def _cell(text: str, limit: int = 90) -> str:
    text = text if len(text) <= limit else text[: limit - 1] + "…"
    return text.replace("|", "\\|")


def _returned(reply: ToolReply) -> str:
    if reply.content in (DENIED_MESSAGE, CALL_LIMIT_MESSAGE):
        return reply.content
    if reply.evidence:
        # 도구 메시지에 근거 번호로 넣은 근거만 쓴다
        refs = ", ".join(f"{e.ref}: {e.summary}" for e in reply.evidence if e.ref is not None)
        return f"결과 JSON (근거 {refs})"
    return f"결과 JSON ({json.loads(reply.content).get('error')})"


async def run_demo(log_path: Path, *, timeout: float = LOOKUP_TIMEOUT_S, now: dt.datetime | None = None) -> str:
    """정해진 공격·정상 호출을 게이트웨이에 차례로 넣고 판정 표와 그 실행의 호출 로그를 만든다."""
    now = now or dt.datetime.now(dt.UTC)
    base = sample_health_data(USER, now)
    base.not_linked = {UNLINKED_USER}
    slow = _SlowPort(**vars(base))
    slow.delay = timeout + 0.2
    ports: dict[str, InMemoryHealthData] = {"normal": base, "leaking": _LeakingPort(**vars(base)), "slow": slow}
    jsonl = JsonlAuditSink(log_path)
    lines = ["| 사례 | 입력 | 판정 | 사유 | LLM 에 돌려준 문장 |", "| --- | --- | --- | --- | --- |"]
    for no, case in enumerate(CASES, 1):
        memory = MemoryAuditSink()
        gateway = ToolGateway(ports[case.port], local_today(now), audit=_TeeSink(memory, jsonl), timeout=timeout)
        ctx = CoachContext(user_id=case.user, conversation_id="demo", request_id=f"demo-{no:02d}")
        calls = [ToolCall(f"call_{i}", name, args) for i, (name, args) in enumerate(case.calls, 1)]
        try:
            returned = [_returned(reply) for reply in await gateway.execute(calls, ctx)]
        except HealthDataError as e:
            returned = [f"(요청 종료: {e.code})"] * len(calls)
        for i, (call, record, text) in enumerate(zip(calls, memory.records(), returned, strict=True)):
            cells = [
                case.title if i == 0 else "",
                f"{call.name} {json.dumps(call.args, ensure_ascii=False)}",
                record.decision,
                record.reason or "-",
                text,
            ]
            lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    log = log_path.read_text(encoding="utf-8").rstrip("\n")
    return "\n".join([*lines, "", f"호출 로그: {log_path}", "", log])


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="인가 게이트웨이 시연 (합성 데이터, 실제 LLM 호출 없음)")
    p.add_argument("--log", type=Path, help="호출 로그 JSONL 경로. 생략하면 임시 폴더에 새로 만든다")
    args = p.parse_args(argv)
    path = args.log or Path(tempfile.mkdtemp(prefix="coach_gateway_demo_")) / "tool_calls.jsonl"
    if path.exists():
        raise SystemExit(f"{path} 파일이 이미 있습니다. 이번 실행의 기록만 보여 주도록 새 경로를 지정하세요.")
    print(asyncio.run(run_demo(path)))


if __name__ == "__main__":
    main()
