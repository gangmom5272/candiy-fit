"""생활 습관 점수와 수면 중 심박·산소포화도, 운동 칼로리·거리 답변을 사람이 읽어 보는 실행기.

질문 몇 개를 운영과 같은 경로로 끝까지 실행해 답과 도구 호출, LLM 에 전달한 도구 결과를 출력한다.

ai/ 디렉터리에서 실행한다. 실제 사내 LLM 을 호출하므로 .env 에 LLM_API_KEY 가 있어야 한다.
    uv run python -m eval.score_answers                        # 질문 18개 × 3회
    uv run python -m eval.score_answers --runs 1 --only today  # 스모크
    uv run python -m eval.score_answers --save                 # eval/results/ 에 md·jsonl 저장 (git 제외)

- 건강데이터는 메모리 어댑터의 합성 값(adapters/sample.py)이고 기준 시각은 2026-10-22 14:00(KST)로 고정한다.
  오늘 점수는 활동만 있는 30점(관리)이고, 오늘 아침 수면은 동기화 전이라 오늘 수면·심박 점수와 수면 중 평균 심박이 없다.
  어제 점수는 92점이고 어제 수면 중 평균 심박은 59bpm 이다. 그 값은 그저께 밤 잠(10월 21일에 깬 수면)의 잠든 동안
  평균 심박과 같다. 그 수면의 최저·최고 심박은 51·76bpm, 산소포화도는 평균 96%·최저 91.8%·최고 98.1%이고 90% 미만
  측정은 0회다. 이번 주에는 10월 20일에 깬 수면에 90% 미만 측정이 3회 있다. 어제 운동은 달리기 한 번(35분, 330kcal,
  6.2km)이다.
- 최근 2주는 수면 기록이 12개라 수면 기록마다 값을 넣는 대신 기간 요약(measure_summary)이 나간다. 수면 중 평균 심박
  59.5bpm(수면 기록 11개), 최저 49bpm(10월 10일), 최고 88bpm(10월 20일), 산소포화도 평균 95.7%(10개), 최저
  88.6%(10월 20일), 90% 미만 측정이 있던 수면 기록 1개(10개 중)다.
- 채점하지 않는다. 질문마다 LLM 에 전달한 도구 결과를 호출별로 한 번 출력하고, 실행마다 답변, 도구 호출(왕복 순서, 인자,
  결과 종류), 종료 사유를 출력한다. 답변의 값이 결과와 같은지, 90% 미만 측정 횟수를 시간으로, 세션 칼로리를 하루 전체
  소모 칼로리로 말하지 않는지는 사람이 읽어 확인한다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from bench.probe_llm import git_commit
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.executor import tool_specs
from coach_ai.agent.graph import CoachResult, run_coach
from coach_ai.agent.prompts import DEFAULT_PROMPT_VERSION, load_system_prompt
from coach_ai.config import Settings, get_settings
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.tools.periods import DEFAULT_TZ
from eval.repeated_question import calls_signature, tool_calls_of
from eval.tool_selection import RequestLog, fmt_call, make_model

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"

NOW = datetime(2026, 10, 22, 14, 0, tzinfo=ZoneInfo(DEFAULT_TZ))
DEFAULT_RUNS = 3
USER_ID = "synthetic-score-answers"
CONVERSATION_ID = "eval-score-answers"
# 이름 → 질문. 점수 질문, 점수를 묻지 않은 건강 상태 질문, 안정 시 심박수라는 말 없이 묻는 수면 중 심박과 산소포화도
# 질문, 운동 세션 칼로리·거리와 하루 전체 소모 칼로리 질문, 하루 평균 심박 질문, 기간 요약을 읽는 최근 2주 질문이다
QUESTIONS: dict[str, str] = {
    "today": "오늘 점수 몇 점이야?",
    "reason": "왜 관리야?",
    "raise": "점수 올리려면 뭐 해야 돼?",
    "resting_today": "안정 시 심박수 몇이야?",
    "resting_yesterday": "어제 안정 시 심박수 얼마였어?",
    "several_days": "이번 주 점수 어땠어?",
    "health": "요즘 건강 어때?",
    "sleep_heart_range": "그저께 밤 자는 동안 심박 최저·최고 알려 줘",
    "sleep_heart_avg": "그저께 밤 잘 때 평균 심박 몇이었어?",
    "sleep_heart_week": "이번 주 밤마다 자는 동안 심박 어땠어?",
    "spo2_range": "그저께 밤 산소포화도 최저·최고 몇이었어?",
    "spo2_low": "이번 주 잘 때 산소포화도 90% 아래로 떨어진 시간 얼마나 돼?",
    "session_calories": "어제 운동하면서 칼로리 얼마나 썼어?",
    "day_calories": "어제 하루 전체로 소모한 칼로리 총 얼마야?",
    "session_distance": "이번 주 운동한 거리 다 합치면 얼마야?",
    "daily_heart": "어제 하루 평균 심박 얼마였어?",
    "sleep_heart_two_weeks": "최근 2주 자는 동안 심박 평균이랑 제일 낮았던 날 알려 줘",
    "spo2_low_two_weeks": "최근 2주 잘 때 산소포화도가 90% 밑으로 내려간 적 있었어?",
}


async def run_once(model: BaseChatModel, name: str, run: int, prompt_version: str) -> dict[str, Any]:
    """질문 하나를 한 번 실행하고 기록 한 줄을 반환한다. 합성 데이터와 호출 기록기는 실행마다 새로 만든다."""
    request_id = f"score-{name}-{run}"
    ctx = CoachContext(user_id=USER_ID, conversation_id=CONVERSATION_ID, request_id=request_id)
    audit = MemoryAuditSink()
    result = await run_coach(
        ctx,
        QUESTIONS[name],
        port=sample_health_data(USER_ID, NOW),
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
        "question": QUESTIONS[name],
        "run": run,
        "answer": result.answer,
        "finish_reason": result.finish_reason,
        "error_code": result.error_code,
        "blocked_by": result.blocked_by,
        "tool_calls": calls,
        "calls_signature": calls_signature(calls),
        "tool_results": tool_results_of(result),
    }


def tool_results_of(result: CoachResult) -> list[dict[str, str]]:
    """LLM 에 전달한 도구 메시지를 호출 순서대로 호출 표시와 함께 정리한다.

    오류로 끝난 실행은 메시지가 없어 비어 있다.
    """
    calls: dict[str, str] = {}
    results: list[dict[str, str]] = []
    for message in result.messages:
        if isinstance(message, AIMessage):
            # 도구 메시지의 tool_call_id 는 tools_node 가 정한 ID 다 (없으면 도구 이름)
            calls.update({call["id"] or call["name"]: fmt_call(call) for call in message.tool_calls})
        elif isinstance(message, ToolMessage) and message.tool_call_id in calls:
            results.append({"call": calls[message.tool_call_id], "content": str(message.content)})
    return results


def _result_block(content: str) -> list[str]:
    """도구 메시지의 결과 부분. JSON 이 아닌 메시지(거부 안내 등)는 그대로 둔다."""
    try:
        message = json.loads(content)
    except json.JSONDecodeError:
        return ["```text", content, "```"]
    body = message.get("result", message) if isinstance(message, dict) else message
    return ["```json", json.dumps(body, ensure_ascii=False, indent=2), "```"]


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


def to_markdown(records: list[dict[str, Any]], args: argparse.Namespace) -> str:
    template = load_system_prompt(args.prompt_version)
    tools = json.dumps(tool_specs(), ensure_ascii=False, sort_keys=True)
    lines = [
        "# 점수·수면·운동 답변 확인",
        "",
        f"- 커밋 `{git_commit()}` · 프롬프트 `{args.prompt_version}` (템플릿 sha256 {_sha256(template)}) · "
        f"도구 스키마 sha256 {_sha256(tools)}",
        f"- 기준 시각 {NOW.isoformat()} · 합성 데이터 `adapters/sample.py` · 질문 {len(records) // args.runs}개 × "
        f"{args.runs}회 · 채점하지 않음",
        "",
        "## 도구 호출 묶음",
        "",
        "| 질문 | 도구 호출 (횟수) |",
        "| --- | --- |",
    ]
    by_name: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_name.setdefault(record["name"], []).append(record)
    for name, rows in by_name.items():
        counts = Counter(r["calls_signature"] for r in rows)
        lines.append(f"| {QUESTIONS[name]} | {', '.join(f'{sig} ({n})' for sig, n in counts.most_common())} |")
    for name, rows in by_name.items():
        lines += ["", f"## {QUESTIONS[name]} ({name})", "", "### 도구 결과"]
        # 기준 시각과 합성 데이터가 고정이라 같은 호출의 결과는 실행마다 같아 호출별로 한 번만 출력한다
        results: dict[str, str] = {}
        for r in rows:
            for item in r["tool_results"]:
                results.setdefault(item["call"], item["content"])
        for call, content in results.items():
            lines += ["", f"- {call}", "", *_result_block(content)]
        if not results:
            lines += ["", "- 도구 결과 없음"]
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
        description="합성 점수로 질문을 끝까지 실행해 답과 도구 호출을 출력한다 (실제 LLM 호출)"
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
    text = to_markdown(records, args)
    print(text, end="")
    if args.save:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        md_path = args.out / f"{stamp}_score_answers.md"
        records_path = args.out / f"{stamp}_score_answers_records.jsonl"
        md_path.write_text(text, encoding="utf-8")
        records_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        print(f"저장: {md_path}\n실행별: {records_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
