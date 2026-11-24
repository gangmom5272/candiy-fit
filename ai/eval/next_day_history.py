"""어제 나눈 대화 뒤에 오늘 날짜·오늘 걸음을 묻는 질문을 실행해, 답에서 오늘 날짜를 틀리게 말했는지 판정한다.

ai/ 디렉터리에서 실행한다. 실제 사내 LLM 을 호출하므로 .env 에 LLM_API_KEY 가 있어야 한다.
    uv run python -m eval.next_day_history                  # 문항 4개 × 조건 2개 × 5회
    uv run python -m eval.next_day_history --runs 1         # 스모크
    uv run python -m eval.next_day_history --save           # eval/results/ 에 md·jsonl 저장 (git 제외)

- 운영과 같은 run_coach 경로를 거친다. 건강데이터와 기준 시각(2026-10-23 16:32 KST)은 날짜 질문 실행기
  (eval/repeated_question.py)와 같다. 오늘은 10월 23일 금요일이고, 문항의 이전 대화는 10월 22일에 나눈 대화다.
- 문항 파일(datasets/next_day_history_v1.jsonl)의 이전 대화 쌍은 대화 API 처럼 CoachContext.turns 로 넘긴다.
  dated 조건은 쌍마다 파일의 질문한 날짜(answered_on)를 넣어 다른 날 대화 안내가 붙는다. undated 조건은 날짜를 넣지
  않아 안내를 넣기 전과 요청이 같다. 회차·문항마다 두 조건을 한 번씩 실행하고 순서는 SEED 로 섞는다.
- 판정 기준은 JUDGE_RULES 다. 오늘이라고 말한 날짜와 걸음 수를 실행별 기록과 md 에 적어 사람이 다시 확인할 수 있다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, get_args

from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from bench.probe_llm import git_commit
from coach_ai.adapters.memory import InMemoryHealthData
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.executor import tool_specs
from coach_ai.agent.graph import run_coach
from coach_ai.agent.prompts import DEFAULT_PROMPT_VERSION, load_system_prompt, other_day_notice
from coach_ai.config import Settings, get_settings
from coach_ai.history.types import HistoryTurn
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.security.audit import MemoryAuditSink
from eval.answer_markdown import plain_answer
from eval.repeated_question import NOW, TODAY, calls_signature, data_sha256, synthetic_health_data, tool_calls_of
from eval.tool_selection import RequestLog, fmt_call, make_model

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
DATASET = EVAL_DIR / "datasets" / "next_day_history_v1.jsonl"
DEFAULT_RUNS = 5
SEED = 20261104
USER_ID = "synthetic-next-day-history"
CONVERSATION_ID = "eval-next-day-history"
_WEEKDAYS = "월화수목금토일"

Kind = Literal["today_date", "today_steps"]
KINDS: tuple[Kind, ...] = get_args(Kind)
Condition = Literal["dated", "undated"]
CONDITIONS: tuple[Condition, ...] = get_args(Condition)
CONDITION_LABELS: dict[Condition, str] = {
    "dated": "쌍마다 질문한 날짜를 넣음. 다른 날 대화 안내가 붙는다",
    "undated": "질문한 날짜를 넣지 않음. 안내를 넣기 전과 요청이 같다",
}
Verdict = Literal["correct", "wrong_date", "stale_steps", "other", "error"]
VERDICTS: tuple[Verdict, ...] = get_args(Verdict)


@dataclass(frozen=True, slots=True)
class Pair:
    """이전 대화의 질문–답변 한 쌍과 그 쌍을 질문한 날짜."""

    answered_on: date
    question: str
    answer: str


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    kind: Kind
    question: str
    history: tuple[Pair, ...]
    # 이전 대화 답에 나온 걸음 수. 이 값을 오늘 걸음으로 말하면 그 대화의 "오늘"을 옮겨 쓴 것이다
    stale_steps: tuple[int, ...] = ()
    note: str = ""


_KEYS = {"id", "kind", "question", "history", "stale_steps", "note"}
_PAIR_KEYS = {"answered_on", "question", "answer"}


def _pairs(where: str, raw: Any) -> tuple[Pair, ...]:
    if not isinstance(raw, list) or not raw or any(not isinstance(p, dict) or set(p) != _PAIR_KEYS for p in raw):
        raise ValueError(f"{where}: history 는 {sorted(_PAIR_KEYS)} 칸으로 된 쌍 목록이어야 한다")
    try:
        return tuple(Pair(date.fromisoformat(p["answered_on"]), p["question"], p["answer"]) for p in raw)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: answered_on 은 YYYY-MM-DD 날짜여야 한다") from None


def load_items(path: Path = DATASET) -> list[Item]:
    """문항 파일을 읽는다. 형식이 다르면 파일 이름·줄 번호와 함께 ValueError 를 발생시킨다."""
    items: list[Item] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        where = f"{path.name}:{number}"
        row = json.loads(line)
        if not isinstance(row, dict) or set(row) != _KEYS:
            raise ValueError(f"{where}: 칸은 {sorted(_KEYS)} 이어야 한다")
        if row["kind"] not in KINDS:
            raise ValueError(f"{where}: kind {row['kind']!r} 를 알 수 없다")
        stale = row["stale_steps"]
        if not isinstance(stale, list) or any(not isinstance(v, int) or isinstance(v, bool) for v in stale):
            raise ValueError(f"{where}: stale_steps 는 정수 목록이어야 한다")
        history = _pairs(where, row["history"])
        items.append(Item(row["id"], row["kind"], row["question"], history, tuple(stale), row["note"]))
    ids = [item.id for item in items]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path.name}: 문항 ID 가 겹친다")
    return items


def context_turns(item: Item, condition: Condition) -> tuple[HistoryTurn, ...]:
    """이전 대화 쌍을 CoachContext.turns 로 바꾼다. dated 조건만 쌍마다 질문한 날짜를 넣는다.

    ID 와 순서 번호는 대화 API 처럼 질문 n·답변 n+1 로 붙인 합성 값이고 LLM 요청에는 들어가지 않는다.
    """
    return tuple(
        HistoryTurn(
            request_id=f"{CONVERSATION_ID}-{item.id}-{n}",
            question_message_id=f"{CONVERSATION_ID}-{item.id}-{n}-question",
            answer_message_id=f"{CONVERSATION_ID}-{item.id}-{n}-answer",
            question_seq=2 * n - 1,
            answer_seq=2 * n,
            question=pair.question,
            answer=pair.answer,
            answered_on=pair.answered_on if condition == "dated" else None,
        )
        for n, pair in enumerate(item.history, 1)
    )


def run_order(
    items: Sequence[Item], runs: int, conditions: Sequence[Condition], seed: int = SEED
) -> list[tuple[int, Item, Condition]]:
    """(회차, 문항, 조건) 실행 순서. 회차·문항마다 조건을 한 번씩 넣고 그 순서를 seed 로 섞는다."""
    rng = random.Random(seed)
    order: list[tuple[int, Item, Condition]] = []
    for run in range(1, runs + 1):
        for item in items:
            batch = list(conditions)
            rng.shuffle(batch)
            order += [(run, item, condition) for condition in batch]
    return order


JUDGE_RULES = (
    "답변의 마크다운은 기호를 지운 글로 읽는다 (eval/answer_markdown.py).",
    '오늘이라고 말한 날짜는 "오늘" 바로 뒤의 날짜("오늘은 10월 23일", "오늘(10월 23일)", "오늘 날짜는 10월 23일")와 '
    '바로 뒤에 "(오늘)"·"인 오늘"이 붙은 날짜다. 날짜 뒤의 요일과 "오늘은 금요일" 같은 요일도 함께 읽는다.',
    "날짜 문항은 오늘이라고 말한 날짜가 없으면, 바로 앞에 어제·그저께·전날·내일·지난이 없는 첫 날짜를 오늘이라고 "
    "말한 날짜로 본다.",
    "아래 순서로 먼저 맞는 판정을 쓴다.",
    "error: 실행이 오류로 끝난다.",
    "wrong_date: 오늘이라고 말한 날짜나 요일 중 기준 시각의 오늘과 다른 것이 있다.",
    "stale_steps: 걸음 문항에서 이전 대화 답의 걸음 수(stale_steps)를 말한다.",
    "correct: 날짜 문항은 오늘이라고 말한 날짜가 있고 모두 오늘이다. 걸음 문항은 오늘 진행 중 걸음 수를 말한다.",
    "other: 어느 것에도 해당하지 않는다. 답변을 사람이 확인한다.",
)

_DATE_BODY = r"(?:(?P<year>\d{4})\s*년\s*)?(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일"
# 날짜 뒤의 요일은 "목요일"과 괄호 안 한 글자("(목)")만 읽는다. "목표"의 "목"은 요일이 아니다
_WEEKDAY_TAIL = r"(?:\s*(?P<weekday>[월화수목금토일])요일|\s*\(\s*(?P<short>[월화수목금토일])(?:요일)?\s*\))?"
_TODAY_DATE = re.compile(rf"오늘(?:은|는|이|의)?\s*(?:날짜(?:는|가)?\s*)?[,(]?\s*{_DATE_BODY}{_WEEKDAY_TAIL}")
_DATE_TODAY = re.compile(rf"{_DATE_BODY}{_WEEKDAY_TAIL}\s*(?:\(\s*오늘\s*\)|인\s*오늘)")
_TODAY_WEEKDAY = re.compile(r"오늘(?:은|는|이)?\s*(?P<weekday>[월화수목금토일])요일")
_ANY_DATE = re.compile(rf"{_DATE_BODY}{_WEEKDAY_TAIL}")
# 날짜 바로 앞의 다른 날 표현 ("어제는 10월 22일", "어제(10월 22일)")
_OTHER_DAY_BEFORE = re.compile(r"(?:어제|그저께|그제|전날|내일|지난)\S{0,3}\s*\(?\s*$")
# 천 단위 쉼표가 있는 걸음 수. "보다"의 "보"는 걸음 단위가 아니다
_STEPS = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)\s*(?:걸음|보(?!다))")


@dataclass(frozen=True, slots=True)
class TodayMention:
    """답변에서 오늘이라고 말한 날짜·요일. text 는 답변에 쓰인 그대로다."""

    text: str
    day: date | None
    weekday: str | None


@dataclass(frozen=True, slots=True)
class Judgement:
    verdict: Verdict
    detail: dict[str, Any] = field(default_factory=dict)


def _mention(m: re.Match[str], today: date) -> TodayMention:
    groups = m.groupdict()
    day = None
    if groups.get("month"):
        try:
            day = date(int(groups["year"] or today.year), int(groups["month"]), int(groups["day"]))
        except ValueError:
            day = None
    return TodayMention(m.group(0), day, groups.get("weekday") or groups.get("short"))


def today_mentions(text: str, today: date, *, first_date: bool = False) -> list[TodayMention]:
    """답변에서 오늘이라고 말한 날짜·요일을 나온 순서로 반환한다 (JUDGE_RULES 둘째·셋째 기준).

    first_date 면 그런 날짜·요일이 없을 때 바로 앞에 다른 날 표현이 없는 첫 날짜를 넣는다.
    """
    found: list[tuple[int, int, TodayMention]] = []
    for pattern in (_TODAY_DATE, _DATE_TODAY, _TODAY_WEEKDAY):
        for m in pattern.finditer(text):
            if not any(start < m.end() and m.start() < end for start, end, _ in found):
                found.append((m.start(), m.end(), _mention(m, today)))
    if not found and first_date:
        first = next((m for m in _ANY_DATE.finditer(text) if not _OTHER_DAY_BEFORE.search(text[: m.start()])), None)
        if first is not None:
            found.append((first.start(), first.end(), _mention(first, today)))
    return [mention for _, _, mention in sorted(found, key=lambda f: f[0])]


def _is_wrong(mention: TodayMention, today: date) -> bool:
    if mention.day is not None and mention.day != today:
        return True
    return mention.weekday is not None and mention.weekday != _WEEKDAYS[today.weekday()]


def judge(
    answer: str, item: Item, *, steps_today: int, error_code: str | None = None, today: date = TODAY
) -> Judgement:
    """답변 하나를 JUDGE_RULES 대로 판정한다. detail 에 오늘이라고 말한 날짜와 답의 걸음 수를 담는다."""
    if error_code is not None:
        return Judgement("error", {"error_code": error_code})
    text = plain_answer(answer).text
    mentions = today_mentions(text, today, first_date=item.kind == "today_date")
    wrong = [m for m in mentions if _is_wrong(m, today)]
    steps = [int(value.replace(",", "")) for value in _STEPS.findall(text)]
    stale = [value for value in steps if value in item.stale_steps]
    verdict: Verdict
    if wrong:
        verdict = "wrong_date"
    elif item.kind == "today_steps" and stale:
        verdict = "stale_steps"
    elif item.kind == "today_date":
        verdict = "correct" if mentions else "other"
    else:
        verdict = "correct" if steps_today in steps else "other"
    detail = {
        "today_mentions": [
            {"text": m.text, "date": None if m.day is None else m.day.isoformat(), "weekday": m.weekday}
            for m in mentions
        ],
        "wrong_today": [m.text for m in wrong],
        "steps": steps,
        "stale_steps": stale,
    }
    return Judgement(verdict, detail)


async def today_steps(port: InMemoryHealthData) -> int:
    """합성 데이터의 오늘 진행 중 걸음 수. 걸음 문항의 정답이다."""
    result = await port.get_metric_result(USER_ID, "steps", TODAY, TODAY)
    if result.in_progress is None or result.in_progress.value is None:
        raise ValueError("합성 데이터에 오늘 진행 중 걸음 수가 없다")
    return round(result.in_progress.value)


async def run_once(
    model: BaseChatModel,
    recorder: MetricsRecorder,
    item: Item,
    condition: Condition,
    run: int,
    order: int,
    *,
    prompt_version: str,
    steps_today: int,
) -> dict[str, Any]:
    """문항 하나를 조건 하나로 한 번 실행하고 기록 한 줄을 반환한다. 합성 데이터와 호출 기록기는 실행마다 만든다."""
    request_id = f"nd-{item.id}-{run}-{condition}"
    turns = context_turns(item, condition)
    ctx = CoachContext(user_id=USER_ID, conversation_id=CONVERSATION_ID, request_id=request_id, turns=turns)
    audit = MemoryAuditSink()
    started = time.perf_counter()
    result = await run_coach(
        ctx,
        item.question,
        port=synthetic_health_data(USER_ID),
        model=model,
        sink=ListSink(),
        now=NOW,
        message_id=f"{request_id}-answer",
        audit=audit,
        prompt_version=prompt_version,
    )
    elapsed = time.perf_counter() - started
    judgement = judge(result.answer, item, steps_today=steps_today, error_code=result.error_code)
    calls = tool_calls_of(result, audit)
    llm = [r for r in recorder.records("llm") if r.request_id == request_id]
    return {
        "id": item.id,
        "kind": item.kind,
        "condition": condition,
        "run": run,
        "order": order,
        "request_id": request_id,
        # 요청 시스템 메시지에 다른 날 대화 안내가 붙었는지. run_coach 와 같은 규칙으로 정한다
        "day_notice": other_day_notice((turn.answered_on for turn in turns), TODAY) is not None,
        "verdict": judgement.verdict,
        "judge": judgement.detail,
        "answer": result.answer,
        "finish_reason": result.finish_reason,
        "error_code": result.error_code,
        "exception": None if result.exception is None else type(result.exception).__name__,
        "tool_calls": calls,
        "calls_signature": calls_signature(calls),
        "llm_calls": len(llm),
        "input_tokens": sum(r.input_tokens or 0 for r in llm),
        "output_tokens": sum(r.output_tokens or 0 for r in llm),
        "elapsed_s": round(elapsed, 3),
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


async def run_all(
    settings: Settings, args: argparse.Namespace, items: Sequence[Item]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """문항·조건마다 args.runs 회 실행하고 (실행별 기록, 보고서 머리말 값)을 반환한다."""
    order = run_order(items, args.runs, args.conditions)
    reference = synthetic_health_data(USER_ID)
    steps = await today_steps(reference)
    recorder = MetricsRecorder(max_records=max(1000, len(order) * 5))
    log = RequestLog()
    model = make_model(settings, args.temperature, recorder, log)
    records: list[dict[str, Any]] = []
    try:
        for index, (run, item, condition) in enumerate(order, 1):
            record = await run_once(
                model, recorder, item, condition, run, index, prompt_version=args.prompt_version, steps_today=steps
            )
            records.append(record)
            line = f"[{index}/{len(order)}] {item.id} {run}회 {condition} · {record['verdict']}"
            print(f"{line} · {record['calls_signature']}", file=sys.stderr)
    finally:
        await model.http_async_client.aclose()
    params = log.params[0] if log.params else None
    info = {
        "steps_today": steps,
        "data_sha256": data_sha256(reference)[:12],
        "dataset_sha256": _sha256(args.dataset.read_bytes()),
        "temperature": None if params is None else params.get("temperature"),
    }
    return records, info


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(EVAL_DIR.parent).as_posix()
    except ValueError:
        return path.name


def _counts(rows: Sequence[dict[str, Any]]) -> list[int]:
    verdicts = Counter(r["verdict"] for r in rows)
    wrong = sum(1 for r in rows if r["judge"].get("wrong_today"))
    stale = sum(1 for r in rows if r["judge"].get("stale_steps"))
    return [len(rows), wrong, stale, verdicts["correct"], verdicts["other"], verdicts["error"]]


def _mentions_text(record: dict[str, Any]) -> str:
    mentions = record["judge"].get("today_mentions") or []
    return ", ".join(f"`{m['text']}`" for m in mentions) or "없음"


def to_markdown(
    records: Sequence[dict[str, Any]], info: dict[str, Any], args: argparse.Namespace, items: Sequence[Item]
) -> str:
    template = load_system_prompt(args.prompt_version)
    tools = json.dumps(tool_specs(), ensure_ascii=False, sort_keys=True)
    weekday = _WEEKDAYS[TODAY.weekday()]
    lines = [
        "# 다음 날 이어진 대화의 오늘 날짜 확인",
        "",
        f"- 커밋 `{git_commit()}` · 프롬프트 `{args.prompt_version}` (템플릿 sha256 {_sha256(template.encode())}) · "
        f"도구 스키마 sha256 {_sha256(tools.encode())}",
        f"- 기준 시각 {NOW.isoformat()} (오늘 {TODAY.month}월 {TODAY.day}일 {weekday}요일) · 합성 데이터 "
        f"`eval/repeated_question.py` (sha256 {info['data_sha256']}) · 오늘 진행 중 걸음 {info['steps_today']:,}걸음",
        f"- 문항 `{_display_path(args.dataset)}` (sha256 {info['dataset_sha256']}) · 문항 {len(items)}개 × "
        f"조건 {len(args.conditions)}개 × {args.runs}회 · temperature {info['temperature']}",
        *[f"- {condition}: {CONDITION_LABELS[condition]}" for condition in args.conditions],
        "",
        "## 판정 기준",
        "",
        *[f"- {rule}" for rule in JUDGE_RULES],
        "",
        "## 조건별",
        "",
        "| 조건 | 실행 | 오늘 날짜를 틀리게 말함 | 이전 대화 걸음 수를 말함 | 맞음 | 그 밖 | 오류 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for condition in args.conditions:
        rows = [r for r in records if r["condition"] == condition]
        lines.append(f"| {condition} | " + " | ".join(map(str, _counts(rows))) + " |")
    lines += [
        "",
        "## 문항별",
        "",
        "| 문항 | 질문 | 조건 | 실행 | 오늘 날짜를 틀리게 말함 | 이전 대화 걸음 수를 말함 | 맞음 | 그 밖 | 오류 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in items:
        for condition in args.conditions:
            rows = [r for r in records if r["id"] == item.id and r["condition"] == condition]
            counts = " | ".join(map(str, _counts(rows)))
            lines.append(f"| {item.id} | {item.question} | {condition} | {counts} |")
    lines += ["", "## 실행별"]
    for item in items:
        lines += ["", f"### {item.id} {item.question} ({item.kind})", ""]
        lines += [f"- 이전 대화 {p.answered_on.isoformat()}: {p.question} → {p.answer}" for p in item.history]
        for r in (r for r in records if r["id"] == item.id):
            ending = r["finish_reason"] if r["error_code"] is None else f"{r['finish_reason']} {r['error_code']}"
            calls = ", ".join(fmt_call(c) for c in r["tool_calls"]) or "도구 호출 없음"
            lines += [
                "",
                f"#### {r['run']}회 {r['condition']} · {r['verdict']} · {ending}",
                "",
                f"- 도구: {calls}",
                f"- 오늘이라고 말한 날짜: {_mentions_text(r)} · 걸음 수: {r['judge'].get('steps') or '없음'}",
                "",
                "```text",
                r["answer"].strip() or "(답변 없음)",
                "```",
            ]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="어제 나눈 대화 뒤에 오늘 날짜·오늘 걸음을 물어 답의 오늘 날짜를 판정한다 (실제 LLM 호출)"
    )
    p.add_argument("--dataset", type=Path, default=DATASET, help=f"문항 파일 (기본 {_display_path(DATASET)})")
    p.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=f"문항·조건마다 반복 횟수 (기본 {DEFAULT_RUNS})")
    p.add_argument("--only", nargs="+", help="실행할 문항 ID. 생략하면 전부")
    p.add_argument("--conditions", nargs="+", choices=list(CONDITIONS), default=list(CONDITIONS), help="실행할 조건")
    p.add_argument("--temperature", type=float, help="생략하면 요청에 넣지 않아 서버 기본값을 쓴다")
    p.add_argument(
        "--prompt-version",
        default=DEFAULT_PROMPT_VERSION,
        help=f"시스템 프롬프트 버전 (기본 {DEFAULT_PROMPT_VERSION})",
    )
    p.add_argument("--save", action="store_true", help="결과를 --out 폴더에 md·실행별 jsonl 로 저장 (git 제외)")
    p.add_argument("--out", type=Path, default=RESULTS_DIR, help="결과 저장 폴더 (기본 eval/results/)")
    args = p.parse_args(argv)
    if args.runs < 1:
        p.error("--runs 는 1 이상이어야 한다")
    return args


def select_items(args: argparse.Namespace) -> list[Item]:
    items = load_items(args.dataset)
    if not args.only:
        return items
    unknown = sorted(set(args.only) - {item.id for item in items})
    if unknown:
        raise SystemExit(f"문항 파일에 없는 ID: {', '.join(unknown)}")
    return [item for item in items if item.id in args.only]


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    items = select_items(args)
    try:
        settings = get_settings()
    except ValidationError:
        raise SystemExit("LLM_API_KEY가 없습니다. LLM_API_KEY를 넣은 뒤 다시 실행하세요.") from None
    records, info = asyncio.run(run_all(settings, args, items))
    text = to_markdown(records, info, args, items)
    print(text, end="")
    if args.save:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        md_path = args.out / f"{stamp}_next_day_history.md"
        records_path = args.out / f"{stamp}_next_day_history_records.jsonl"
        md_path.write_text(text, encoding="utf-8")
        records_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        print(f"저장: {md_path}\n실행별: {records_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
