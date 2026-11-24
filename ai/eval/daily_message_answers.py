"""오늘의 한마디 답변을 사람이 읽어 보는 실행기.

질문 몇 개를 운영과 같은 경로로 끝까지 실행해 답과 도구 호출을 출력한다.

ai/ 디렉터리에서 실행한다. 실제 사내 LLM 을 호출하므로 .env 에 LLM_API_KEY 가 있어야 한다.
    uv run python -m eval.daily_message_answers                        # 질문 10개 × 3회
    uv run python -m eval.daily_message_answers --runs 1 --only today  # 스모크
    uv run python -m eval.daily_message_answers --save                 # eval/results/ 에 md·jsonl 저장 (git 제외)

- 건강데이터는 메모리 어댑터의 합성 값(adapters/sample.py)이고 기준 시각은 2026-10-22 14:00(KST)로 고정한다.
  오늘 한마디는 오늘 걸음 기록으로 고른 STEPS_PROGRESS(READY)다. 질문에 따라 오늘 한마디 행만 바꿔, 수면 조회 실패와
  걸음 동기화 실패가 있는 PARTIAL, 오늘 기록이 동기화 전인 WAITING, 소개할 기록이 없을 때의 EMPTY 를 만든다.
- 채점하지 않는다. 질문에 쓴 합성 한마디마다 LLM 에 전달하는 한마디 도구 결과를 한 번 출력하고, 실행마다 답변 전체,
  도구 호출(왕복 순서, 인자, 결과 종류), 종료 사유를 출력한다.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from bench.probe_llm import git_commit
from coach_ai.adapters.memory import InMemoryHealthData
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.executor import ToolRequest, tool_specs
from coach_ai.agent.graph import run_coach
from coach_ai.agent.prompts import DEFAULT_PROMPT_VERSION, load_system_prompt
from coach_ai.config import Settings, get_settings
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.ports import DailyMessageStatus
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.tools.daily_message import get_daily_message
from coach_ai.tools.periods import DEFAULT_TZ
from coach_ai.tools.schemas import GetDailyMessage
from eval.repeated_question import calls_signature, tool_calls_of
from eval.tool_selection import RequestLog, fmt_call, make_model

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"

NOW = datetime(2026, 10, 22, 14, 0, tzinfo=ZoneInfo(DEFAULT_TZ))
DEFAULT_RUNS = 3
USER_ID = "synthetic-daily-message-answers"
CONVERSATION_ID = "eval-daily-message-answers"
# 이름 → (질문, 오늘 한마디의 상태). 한마디 문구와 이유, 안내가 있는 한마디, 지난 날짜·다시 고르기 요청,
# 짧은 말이라는 뜻의 한마디, 오늘 할 일을 묻는 조언 요청, 점수와 함께 묻는 질문이다
QUESTIONS: dict[str, tuple[str, DailyMessageStatus]] = {
    "today": ("오늘의 한마디 뭐야?", "READY"),
    "reason": ("오늘 한마디가 왜 이렇게 나왔어?", "READY"),
    "partial": ("한마디에 수면 기록 못 불러왔다던데?", "PARTIAL"),
    "waiting": ("홈 화면 한마디가 동기화 확인하라는데 무슨 뜻이야?", "WAITING"),
    "empty": ("홈 한마디에 기록이 없다고 나오는데 왜 그래?", "EMPTY"),
    "yesterday": ("어제 한마디 뭐였어?", "READY"),
    "redraw": ("한마디 다른 걸로 다시 뽑아 줘", "READY"),
    "cheer": ("응원 한마디 해 줘", "READY"),
    "advice": ("오늘 뭐 하면 좋을까?", "READY"),
    "with_score": ("오늘 점수랑 한마디 같이 알려 줘", "READY"),
}
# 연동 쪽 문구 표(backend/app/health/daily_message.py)의 동기화 대기 문구와 지표 안내 문장
WAITING_TITLE = "오늘의 기록을 아직 확인하지 못했어요"
WAITING_DESCRIPTION = "괜찮다면 앱의 동기화 상태를 확인해 주세요."
PARTIAL_NOTICES = (
    ("steps", "SYNC_FAILED", "최근 동기화에 실패해 이전에 저장된 기록을 보여 드려요."),
    ("sleeps", "LOOKUP_FAILED", "수면 기록은 불러오지 못했어요."),
)


def health_data(status: DailyMessageStatus) -> InMemoryHealthData:
    """합성 건강데이터. status 가 READY 가 아니면 오늘 한마디 행만 바꾼다."""
    port = sample_health_data(USER_ID, NOW)
    [row] = port.daily_messages[USER_ID]
    if status == "EMPTY":
        # 그날 행이 없으면 메모리 어댑터가 소개할 기록이 없을 때의 응답(NO_RECORDS)을 반환한다
        port.daily_messages[USER_ID] = []
        return port
    if status == "PARTIAL":
        # 걸음 문구는 그대로 두고, 조회하지 못한 수면은 기준 시각을 비운다
        row = dataclasses.replace(
            row, status="PARTIAL", data_as_of={**row.data_as_of, "sleeps": None}, notices=PARTIAL_NOTICES
        )
    elif status == "WAITING":
        row = dataclasses.replace(
            row,
            status="WAITING",
            code="PENDING_RECORDS",
            area=None,
            title=WAITING_TITLE,
            description=WAITING_DESCRIPTION,
            used_metrics=(),
        )
    port.daily_messages[USER_ID] = [row]
    return port


async def message_results(statuses: Sequence[DailyMessageStatus]) -> dict[DailyMessageStatus, dict[str, Any]]:
    """합성 한마디마다 LLM 에 전달하는 한마디 도구 결과."""
    results: dict[DailyMessageStatus, dict[str, Any]] = {}
    for status in statuses:
        request = ToolRequest(USER_ID, NOW.date(), health_data(status), request_id="daily-message-preview")
        results[status] = (await get_daily_message(GetDailyMessage(), request)).payload
    return results


async def run_once(model: BaseChatModel, name: str, run: int, prompt_version: str) -> dict[str, Any]:
    """질문 하나를 한 번 실행하고 기록 한 줄을 반환한다. 합성 데이터와 호출 기록기는 실행마다 새로 만든다."""
    question, status = QUESTIONS[name]
    request_id = f"daily-message-{name}-{run}"
    ctx = CoachContext(user_id=USER_ID, conversation_id=CONVERSATION_ID, request_id=request_id)
    audit = MemoryAuditSink()
    result = await run_coach(
        ctx,
        question,
        port=health_data(status),
        model=model,
        sink=ListSink(),
        now=NOW,
        message_id=f"{request_id}-answer",
        audit=audit,
        prompt_version=prompt_version,
    )
    calls = tool_calls_of(result, audit)
    return {
        "name": name,
        "question": question,
        "status": status,
        "run": run,
        "answer": result.answer,
        "finish_reason": result.finish_reason,
        "error_code": result.error_code,
        "blocked_by": result.blocked_by,
        "tool_calls": calls,
        "calls_signature": calls_signature(calls),
    }


async def run_all(settings: Settings, args: argparse.Namespace) -> list[dict[str, Any]]:
    names = args.only or list(QUESTIONS)
    total = len(names) * args.runs
    model = make_model(settings, args.temperature, MetricsRecorder(max_records=max(1000, total * 5)), RequestLog())
    records: list[dict[str, Any]] = []
    try:
        for name in names:
            for run in range(1, args.runs + 1):
                record = await run_once(model, name, run, args.prompt_version)
                records.append(record)
                progress = f"[{len(records)}/{total}] {name} {run}회 · {record['calls_signature']}"
                print(f"{progress} · {record['finish_reason']}", file=sys.stderr)
    finally:
        await model.http_async_client.aclose()
    return records


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def to_markdown(
    records: list[dict[str, Any]],
    results: dict[DailyMessageStatus, dict[str, Any]],
    args: argparse.Namespace,
) -> str:
    template = load_system_prompt(args.prompt_version)
    tools = json.dumps(tool_specs(), ensure_ascii=False, sort_keys=True)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_name.setdefault(record["name"], []).append(record)
    lines = [
        "# 한마디 답변 확인",
        "",
        f"- 커밋 `{git_commit()}` · 프롬프트 `{args.prompt_version}` (템플릿 sha256 {_sha256(template)}) · "
        f"도구 스키마 sha256 {_sha256(tools)}",
        f"- 기준 시각 {NOW.isoformat()} · 합성 데이터 `adapters/sample.py` (질문에 따라 오늘 한마디 행만 바꿈) · "
        f"질문 {len(by_name)}개 × {args.runs}회 · 채점하지 않음",
        "",
        "## 도구 호출 묶음",
        "",
        "| 질문 | 합성 한마디 | 도구 호출 (횟수) |",
        "| --- | --- | --- |",
    ]
    for name, rows in by_name.items():
        question, status = QUESTIONS[name]
        counts = Counter(r["calls_signature"] for r in rows)
        lines.append(f"| {question} | {status} | {', '.join(f'{sig} ({n})' for sig, n in counts.most_common())} |")
    lines += ["", "## 합성 한마디의 도구 결과"]
    for status, payload in results.items():
        lines += ["", f"### {status}", "", "```json", json.dumps(payload, ensure_ascii=False, indent=2), "```"]
    for name, rows in by_name.items():
        question, status = QUESTIONS[name]
        lines += ["", f"## {question} ({name})", "", f"- 합성 한마디 {status}"]
        for r in rows:
            ending = r["finish_reason"] if r["error_code"] is None else f"{r['finish_reason']} {r['error_code']}"
            lines += ["", f"### {r['run']}회 · {ending}", ""]
            calls = []
            for c in r["tool_calls"]:
                # 오류로 끝난 실행은 왕복과 결과 종류 없이 호출 기록의 이름·인자만 있다
                prefix = "" if c["round"] is None else f"{c['round']}왕복 "
                calls.append(f"- {prefix}{fmt_call(c)} → {c['reply']}")
            lines += calls or ["- 도구 호출 없음"]
            lines += ["", "```text", r["answer"].strip() or "(답변 없음)", "```"]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="합성 한마디로 질문을 끝까지 실행해 답과 도구 호출을 출력한다 (실제 LLM 호출)"
    )
    p.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=f"질문마다 반복 횟수 (기본 {DEFAULT_RUNS})")
    p.add_argument("--only", nargs="+", choices=list(QUESTIONS), help="실행할 질문 이름. 생략하면 전부")
    p.add_argument("--temperature", type=float, help="생략하면 요청에 넣지 않아 서버 기본값을 쓴다")
    p.add_argument(
        "--prompt-version",
        default=DEFAULT_PROMPT_VERSION,
        help=f"시스템 프롬프트 버전 (기본 {DEFAULT_PROMPT_VERSION})",
    )
    p.add_argument("--save", action="store_true", help="결과를 --out 폴더에 md·문항별 jsonl 로 저장 (git 제외)")
    p.add_argument("--out", type=Path, default=RESULTS_DIR, help="결과 저장 폴더 (기본 eval/results/)")
    args = p.parse_args(argv)
    if args.runs < 1:
        p.error("--runs 는 1 이상이어야 한다")
    return args


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    try:
        settings = get_settings()
    except ValidationError:
        raise SystemExit("LLM_API_KEY가 없습니다. LLM_API_KEY를 넣은 뒤 다시 실행하세요.") from None
    records = asyncio.run(run_all(settings, args))
    statuses = list(dict.fromkeys(QUESTIONS[name][1] for name in args.only or QUESTIONS))
    text = to_markdown(records, asyncio.run(message_results(statuses)), args)
    print(text, end="")
    if args.save:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        md_path = args.out / f"{stamp}_daily_message_answers.md"
        records_path = args.out / f"{stamp}_daily_message_answers_records.jsonl"
        md_path.write_text(text, encoding="utf-8")
        records_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        print(f"저장: {md_path}\n실행별: {records_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
