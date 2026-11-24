"""'지난 X요일' 문항을 기준일 월~일 7개로 run_coach 끝까지 실행해, 첫 호출과 최종 조회의 기간을 정답 날짜와 비교한다.

ai/ 디렉터리에서 실행한다. 실제 사내 LLM 을 호출하므로 .env 에 LLM_API_KEY 가 있어야 한다.
    uv run python -m eval.weekday_final_lookup                  # 6문항 × 기준일 7개 × 10회
    uv run python -m eval.weekday_final_lookup --runs 1         # 스모크
    uv run python -m eval.weekday_final_lookup --save           # eval/results/weekday-final/ 에 저장 (git 제외)

- 문항은 기간 v2.2 와 도구 선택 v1.1 에서 "지난 X요일"만 말한 채점 문항이다. 고르는 규칙과 기준일
  (2026-10-12(월)~18(일))은 첫 호출만 세는 eval.weekday_base_dates 와 같다.
- 운영과 같은 run_coach 경로를 거친다. 이전 대화는 없고, 건강데이터는 기준일 14:00(KST) 기준 메모리 어댑터 합성
  데이터(coach_ai.adapters.sample)다.
- 판정 기준은 RULES 다. 회차마다 기준일과 문항을 한 번씩 실행한다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time as clock
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

import coach_ai
from bench.probe_llm import git_commit
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.executor import tool_specs
from coach_ai.agent.graph import run_coach
from coach_ai.agent.prompts import DEFAULT_PROMPT_VERSION, load_system_prompt
from coach_ai.config import Settings, get_settings
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.tools.periods import DEFAULT_TZ, WEEKDAYS, DateRange, period_range
from eval.repeated_question import calls_signature, tool_calls_of
from eval.tool_selection import PERIOD_ARGS, Item, RequestLog, load_datasets, make_model
from eval.weekday_base_dates import BASE_DATES, weekday_of

EVAL_DIR = Path(__file__).resolve().parent
DATASETS_DIR = EVAL_DIR / "datasets"
SOURCES = (DATASETS_DIR / "tool_selection_ai_period_v2.2.jsonl", DATASETS_DIR / "tool_selection_v1.1.jsonl")
OUT_DIR = EVAL_DIR / "results" / "weekday-final"
DEFAULT_RUNS = 10
CLOCK = time(14, 0)
USER_ID = "synthetic-weekday-final"
CONVERSATION_ID = "eval-weekday-final"
_KST = ZoneInfo(DEFAULT_TZ)
_DAY_NAMES = "월화수목금토일"

Kind = Literal["exact", "future", "contains", "other", "invalid", "none"]
KINDS: tuple[Kind, ...] = get_args(Kind)
KIND_LABELS: dict[Kind, str] = {
    "exact": "정답 날짜 하루",
    "future": "아직 오지 않은 날 포함",
    "contains": "정답 날짜를 포함한 여러 날",
    "other": "다른 날",
    "invalid": "날짜를 계산할 수 없음",
    "none": "조회 없음",
}
RULES = (
    "정답 날짜는 기간 정의(eval/datasets/README.md)대로 기준일 이전에 가장 최근에 지난 그 요일 하루다.",
    "호출의 기간은 기간 인자(period, current, baseline)를 기준일로 계산한 날짜다. 서버가 조회하지 않은 기간"
    "(아직 오지 않은 날짜 등)도 날짜를 계산할 수 있으면 계산한다.",
    "호출 묶음은 정답 날짜 하루(exact), 아직 오지 않은 날 포함(future), 정답 날짜를 포함한 여러 날(contains), "
    "다른 날(other), 날짜를 계산할 수 없음(invalid) 순서로 먼저 맞는 것으로 나눈다.",
    "첫 호출은 첫 왕복의 기간 조회 묶음이다. 도구를 부르지 않았으면 조회 없음(none), 기간 인자가 없는 도구만 "
    "불렀으면 다른 날(other)이다.",
    "최종 성공 조회는 결과(ok)를 받은 기간 조회가 있는 마지막 왕복의 그 호출들이다. 없으면 조회 없음이다.",
    "최종 조회는 기간 조회를 부른 마지막 왕복의 호출들이다. 결과 종류(ok, 게이트웨이 거부, 조회할 수 없는 기간)와 "
    "관계없이 센다. 그 안에 기준일보다 뒤 날짜가 있으면 아직 오지 않은 날을 조회한 실행이다.",
    "최종 조회 정답은 최종 성공 조회가 정답 날짜 하루(exact)인 실행이다.",
)


def select_items(sources: Sequence[Path] = SOURCES) -> list[Item]:
    """원본 데이터셋에서 "지난 X요일" 문항을 파일 순서대로 고른다."""
    items, _ = load_datasets(list(sources))
    return [item for item in items if weekday_of(item)]


def answer_day(asked: str, base: date) -> date:
    """기준일 이전에 가장 최근에 지난 그 요일."""
    return period_range(f"weekday:{asked}", base).start


def _ranges(call: dict[str, Any], base: date) -> list[DateRange] | None:
    """호출의 기간 인자를 기준일로 계산한 날짜. 기간 인자가 없으면 None, 계산할 수 없는 값은 뺀다."""
    args = call.get("args")
    if not isinstance(args, dict):
        return None
    values = [value for key in PERIOD_ARGS if isinstance(value := args.get(key), str)]
    if not values:
        return None
    ranges = []
    for value in values:
        try:
            ranges.append(period_range(value, base))
        except ValueError:
            continue
    return ranges


def kind_of(calls: Sequence[dict[str, Any]], day: date, base: date) -> Kind:
    """기간 조회 호출 묶음 하나를 RULES 의 순서로 나눈다."""
    ranges = [r for call in calls if (found := _ranges(call, base)) is not None for r in found]
    if not calls:
        return "none"
    if any(r.start == r.end == day for r in ranges):
        return "exact"
    if any(r.end > base for r in ranges):
        return "future"
    if any(r.start <= day <= r.end for r in ranges):
        return "contains"
    return "other" if ranges else "invalid"


def _period_calls(calls: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """기간 인자가 있는 호출. 날짜를 계산할 수 없는 값만 있는 호출도 넣는다."""
    return [
        call
        for call in calls
        if isinstance(call.get("args"), dict) and any(isinstance(call["args"].get(k), str) for k in PERIOD_ARGS)
    ]


def _last_round(calls: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not calls:
        return []
    last = max(call["round"] or 0 for call in calls)
    return [call for call in calls if (call["round"] or 0) == last]


def judge(calls: Sequence[dict[str, Any]], asked: str, base: date) -> dict[str, Any]:
    """실행 하나의 도구 호출(eval.repeated_question.tool_calls_of)을 RULES 대로 판정한다."""
    day = answer_day(asked, base)
    lookups = _period_calls(calls)
    first_round = [call for call in lookups if call["round"] == 1]
    succeeded = _last_round([call for call in lookups if call["reply"] == "ok"])
    attempted = _last_round(lookups)
    final_ranges = [r for call in attempted for r in (_ranges(call, base) or [])]
    first: Kind = "none" if not calls else kind_of(first_round, day, base) if first_round else "other"
    return {
        "answer_day": day.isoformat(),
        "first": first,
        "final_success": kind_of(succeeded, day, base),
        "final_attempt": kind_of(attempted, day, base),
        "final_attempt_future": any(r.end > base for r in final_ranges),
        "final_success_periods": _periods(succeeded),
        "final_attempt_periods": _periods(attempted),
    }


def _periods(calls: Sequence[dict[str, Any]]) -> list[str]:
    return [call["args"][key] for call in calls for key in PERIOD_ARGS if isinstance(call["args"].get(key), str)]


def run_order(items: Sequence[Item], bases: Sequence[date], runs: int) -> list[tuple[int, date, Item]]:
    """(회차, 기준일, 문항) 실행 순서. 회차마다 기준일·문항을 한 번씩 넣는다."""
    return [(run, base, item) for run in range(1, runs + 1) for base in bases for item in items]


async def run_once(
    model: BaseChatModel,
    recorder: MetricsRecorder,
    item: Item,
    base: date,
    run: int,
    order: int,
    *,
    prompt_version: str,
) -> dict[str, Any]:
    """문항 하나를 기준일 하나로 한 번 실행하고 기록 한 줄을 반환한다. 합성 데이터와 호출 기록기는 실행마다 만든다."""
    asked = weekday_of(item) or ""
    request_id = f"wf-{base.isoformat()}-{item.dataset}-{item.id}-{run}"
    now = datetime.combine(base, CLOCK, tzinfo=_KST)
    ctx = CoachContext(user_id=USER_ID, conversation_id=CONVERSATION_ID, request_id=request_id)
    audit = MemoryAuditSink()
    started = clock.perf_counter()
    result = await run_coach(
        ctx,
        item.question,
        port=sample_health_data(USER_ID, now),
        model=model,
        sink=ListSink(),
        now=now,
        message_id=f"{request_id}-answer",
        audit=audit,
        prompt_version=prompt_version,
    )
    elapsed = clock.perf_counter() - started
    calls = tool_calls_of(result, audit)
    llm = [r for r in recorder.records("llm") if r.request_id == request_id]
    return {
        "key": item.key,
        "dataset": item.dataset,
        "id": item.id,
        "asked": asked,
        "base": base.isoformat(),
        "base_weekday": WEEKDAYS[base.weekday()],
        "run": run,
        "order": order,
        "request_id": request_id,
        **judge(calls, asked, base),
        "tool_calls": calls,
        "calls_signature": calls_signature(calls),
        "tool_rounds": result.tool_rounds,
        "finish_reason": result.finish_reason,
        "error_code": result.error_code,
        "answer": result.answer,
        "llm_calls": len(llm),
        "input_tokens": sum(r.input_tokens or 0 for r in llm),
        "output_tokens": sum(r.output_tokens or 0 for r in llm),
        "elapsed_s": round(elapsed, 3),
    }


def app_sha256() -> str:
    """실행한 앱 코드(coach_ai 패키지의 .py 파일)의 sha256 앞 12자리. 비교용 판을 import 경로로 붙였는지 구분한다."""
    root = Path(coach_ai.__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:12]


async def run_all(
    settings: Settings, args: argparse.Namespace, items: Sequence[Item], bases: Sequence[date]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    order = run_order(items, bases, args.runs)
    recorder = MetricsRecorder(max_records=max(1000, len(order) * 5))
    log = RequestLog()
    model = make_model(settings, args.temperature, recorder, log)
    records: list[dict[str, Any]] = []
    try:
        for index, (run, base, item) in enumerate(order, 1):
            record = await run_once(model, recorder, item, base, run, index, prompt_version=args.prompt_version)
            records.append(record)
            line = f"[{index}/{len(order)}] {base} {item.key} {run}회 · 최종 {record['final_success']}"
            print(f"{line} · {record['calls_signature']}", file=sys.stderr)
    finally:
        await model.http_async_client.aclose()
    params = log.params[0] if log.params else None
    return records, {"temperature": None if params is None else params.get("temperature")}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _day(d: date) -> str:
    return f"{d.isoformat()}({_DAY_NAMES[d.weekday()]})"


def _counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    return {
        "runs": len(rows),
        "first_exact": sum(r["first"] == "exact" for r in rows),
        "final_exact": sum(r["final_success"] == "exact" for r in rows),
        "final_future": sum(r["final_attempt_future"] for r in rows),
        "no_success": sum(r["final_success"] == "none" for r in rows),
        "errors": sum(r["error_code"] is not None for r in rows),
    }


def build_report(
    records: Sequence[dict[str, Any]], info: dict[str, Any], args: argparse.Namespace, items: Sequence[Item]
) -> dict[str, Any]:
    template = load_system_prompt(args.prompt_version)
    tools = json.dumps(tool_specs(), ensure_ascii=False, sort_keys=True)
    bases = sorted({date.fromisoformat(r["base"]) for r in records})
    return {
        "measured_at": datetime.now(_KST).isoformat(timespec="seconds"),
        "commit": git_commit(),
        "app_sha256": app_sha256(),
        "prompt_version": args.prompt_version,
        "prompt_sha256": _sha256(template.encode()),
        "tools_sha256": _sha256(tools.encode()),
        "temperature": info.get("temperature"),
        "runs": args.runs,
        "clock": CLOCK.strftime("%H:%M"),
        "bases": [b.isoformat() for b in bases],
        "items": [{"key": item.key, "asked": weekday_of(item), "question": item.question} for item in items],
        "rules": list(RULES),
        "total": _counts(records),
        "by_base": [
            {"base": b.isoformat(), **_counts([r for r in records if r["base"] == b.isoformat()])} for b in bases
        ],
        "by_item": [
            {"key": item.key, "base": b.isoformat(), **_counts(rows)}
            for item in items
            for b in bases
            if (rows := [r for r in records if r["key"] == item.key and r["base"] == b.isoformat()])
        ],
        "first_kinds": {k: sum(r["first"] == k for r in records) for k in KINDS},
        "final_kinds": {k: sum(r["final_success"] == k for r in records) for k in KINDS},
        "llm_calls": sum(r["llm_calls"] for r in records),
    }


def to_markdown(report: dict[str, Any]) -> str:
    total = report["total"]
    head = "| 실행 | 첫 호출 정답 | 최종 조회 정답 | 최종 조회가 아직 오지 않은 날 | 성공한 조회 없음 | 오류 |"
    align = "| ---: | ---: | ---: | ---: | ---: | ---: |"

    def cells(c: dict[str, Any]) -> str:
        keys = ("runs", "first_exact", "final_exact", "final_future", "no_success", "errors")
        return " | ".join(str(c[k]) for k in keys)

    bases = ", ".join(_day(date.fromisoformat(b)) for b in report["bases"])
    lines = [
        "# 지난 X요일 최종 조회 확인",
        "",
        f"- 커밋 `{report['commit']}` · 앱 코드 sha256 {report['app_sha256']} · 프롬프트 `{report['prompt_version']}` "
        f"(템플릿 sha256 {report['prompt_sha256']}) · 도구 스키마 sha256 {report['tools_sha256']}",
        f"- 기준일 {bases} · 기준 시각 {report['clock']}(KST) · 문항 {len(report['items'])}개 × {report['runs']}회 · "
        f"temperature {report['temperature']} · LLM 호출 {report['llm_calls']}",
        f"- 측정 시각 {report['measured_at']}",
        "",
        "## 판정 기준",
        "",
        *[f"- {rule}" for rule in report["rules"]],
        "",
        "## 합계",
        "",
        head.replace("| 실행", "| 범위 | 실행"),
        align.replace("| ---:", "| --- | ---:", 1),
        f"| 전체 | {cells(total)} |",
        *[f"| {_day(date.fromisoformat(row['base']))} | {cells(row)} |" for row in report["by_base"]],
        "",
        "## 문항 × 기준일",
        "",
        head.replace("| 실행", "| 문항 | 기준일 | 실행"),
        align.replace("| ---:", "| --- | --- | ---:", 1),
        *[f"| {row['key']} | {_day(date.fromisoformat(row['base']))} | {cells(row)} |" for row in report["by_item"]],
        "",
        "## 호출 묶음 종류",
        "",
        "| 종류 | 첫 호출 | 최종 성공 조회 |",
        "| --- | ---: | ---: |",
        *[f"| {KIND_LABELS[k]} ({k}) | {report['first_kinds'][k]} | {report['final_kinds'][k]} |" for k in KINDS],
    ]
    return "\n".join(lines) + "\n"


def signatures_markdown(records: Sequence[dict[str, Any]], items: Sequence[Item]) -> str:
    """문항마다 기준일별 호출 순서(왕복을 → 로 이음)와 횟수. 실행별 기록과 함께만 저장한다."""
    lines = ["", "## 호출 순서 (문항 × 기준일)", ""]
    for item in items:
        lines += [f"### {item.key} {item.question}", ""]
        for base in sorted({r["base"] for r in records}):
            rows = [r for r in records if r["key"] == item.key and r["base"] == base]
            counted = Counter(r["calls_signature"] for r in rows)
            text = "; ".join(f"`{sig}` {n}" for sig, n in counted.most_common())
            lines.append(f"- {_day(date.fromisoformat(base))}: {text}")
        lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="'지난 X요일' 문항을 기준일 월~일로 끝까지 실행해 최종 조회 기간을 센다 (실제 LLM 호출)"
    )
    p.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=f"기준일·문항마다 반복 횟수 (기본 {DEFAULT_RUNS})")
    p.add_argument("--only", nargs="+", help="실행할 문항 키(데이터셋/ID). 생략하면 전부")
    p.add_argument("--bases", nargs="+", type=date.fromisoformat, help="기준일 (YYYY-MM-DD). 생략하면 10-12~10-18")
    p.add_argument("--temperature", type=float, help="생략하면 요청에 넣지 않아 서버 기본값을 쓴다")
    p.add_argument(
        "--prompt-version", default=DEFAULT_PROMPT_VERSION, help=f"시스템 프롬프트 버전 (기본 {DEFAULT_PROMPT_VERSION})"
    )
    p.add_argument("--save", action="store_true", help="--out 폴더에 md·json·실행별 jsonl 저장 (git 제외)")
    p.add_argument("--out", type=Path, default=OUT_DIR, help="결과 저장 폴더 (기본 eval/results/weekday-final/)")
    args = p.parse_args(argv)
    if args.runs < 1:
        p.error("--runs 는 1 이상이어야 한다")
    return args


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    items = select_items()
    if args.only:
        unknown = sorted(set(args.only) - {item.key for item in items})
        if unknown:
            raise SystemExit(f"요일 문항에 없는 키: {', '.join(unknown)}")
        items = [item for item in items if item.key in args.only]
    bases = args.bases or list(BASE_DATES)
    try:
        settings = get_settings()
    except ValidationError:
        raise SystemExit("LLM_API_KEY가 없습니다. LLM_API_KEY를 넣은 뒤 다시 실행하세요.") from None
    records, info = asyncio.run(run_all(settings, args, items, bases))
    report = build_report(records, info, args, items)
    text = to_markdown(report)
    print(text, end="")
    if args.save:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (args.out / f"{stamp}_weekday_final.md").write_text(text, encoding="utf-8")
        body = json.dumps(report, ensure_ascii=False, indent=2)
        (args.out / f"{stamp}_weekday_final.json").write_text(body, encoding="utf-8")
        records_path = args.out / f"{stamp}_weekday_final_records.jsonl"
        records_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        (args.out / f"{stamp}_weekday_final_calls.md").write_text(signatures_markdown(records, items), encoding="utf-8")
        print(f"저장: {args.out / f'{stamp}_weekday_final.md'}", file=sys.stderr)


if __name__ == "__main__":
    main()
