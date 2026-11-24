"""평가 데이터셋으로 LLM 의 도구 선택 정확도와 일관성을 잰다.

ai/ 디렉터리에서 실행한다.
    uv run python -m eval.tool_selection                          # v1.1 에서 proposed 를 뺀 4종, 문항마다 5회
    uv run python -m eval.tool_selection --limit 10 --repeat 1    # 스모크
    uv run python -m eval.tool_selection --only tool clarify --temperature 0
    uv run python -m eval.tool_selection --only guard             # 경계 확인만 채점 (LLM 호출 없음)
    uv run python -m eval.tool_selection --save                   # eval/results/ 에 json·md·문항별 jsonl 저장
    uv run python -m eval.tool_selection --scoring exact          # 기간 인자를 쓴 그대로 비교 (옛 채점)
    uv run python -m eval.tool_selection --rescore eval/results/<측정>_records.jsonl  # LLM 없이 다시 채점

- 운영의 첫 라운드와 같은 요청을 보낸다: 경계 확인(check) → build_messages → 도구 스키마를 묶은 스트리밍 호출.
  도구는 실행하지 않으므로 문항당 LLM 호출은 1회다.
- 시스템 프롬프트의 날짜 자리에는 기준일(--today)을 넣는다. 결과에는 템플릿 sha256 과 넣은 날짜를 기록한다.
- guard 문항은 LLM 없이 check 로 채점한다. 다른 문항이 check 에 걸리면 운영처럼 LLM 을 부르지 않고 오답(오탐)이다.
- proposed 는 정답 도구가 아직 없어 채점하지 않고 응답 유형만 센다.
- 정답 기준은 eval/datasets/README.md 를 따른다. 도구 호출은 (도구 이름, 키를 정렬한 인자) 묶음이 정답이나
  허용 답안 하나와 같으면 맞고, 순서는 검사하지 않는다.
- 기본 채점(range)은 기간 인자(period, current, baseline)를 기준일로 해석한 날짜 범위로 비교한다. last_week 와
  weeks_ago:1 처럼 범위가 같으면 같은 답이다. --scoring exact 는 인자를 쓴 그대로 비교한다.
- 수면 기록의 분석 인자(analysis, condition)는 정답 호출이 기대값(값 하나, 값 목록, 없음 null)을 적었을 때만 비교한다.
  선택 인자를 도구 스키마의 기본값으로 적은 호출은 적지 않은 호출과 같은 답이다.
- 다시 센 점수: 도구 선택 v1.1 의 "지난 X요일" 두 문항에서 그 요일 하루로 부른 실행은 원래 채점에서 틀린 답이고,
  다시 센 점수에서는 맞음이다. 원래 채점과 같은 비교로 확인해 기록과 보고서에 따로 적는다.
- 데이터셋 정답은 DEFAULT_TODAY 를 오늘로 본 답이다. --today 가 이 날짜가 아니면 이 날짜로 계산한 정답·허용 답안이
  있는 문항은 실행하지 않는다. "지난 X요일"만 말한 문항은 그 답안을 빼고 질문한 날 이전에 가장 최근에 지난 X요일을
  정답으로 넣어 판정한다. 결과의 excluded·adjusted 에 문항과 이유를 기록한다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, message_chunk_to_message
from pydantic import ValidationError

from bench.probe_llm import fmt, git_commit, pct
from coach_ai.agent.executor import tool_specs
from coach_ai.agent.graph import build_messages
from coach_ai.agent.prompts import (
    DEFAULT_PROMPT_VERSION,
    TODAY_SLOT,
    load_system_prompt,
    render_system_prompt,
    today_text,
)
from coach_ai.agent.rules.guard import check
from coach_ai.config import Settings, get_settings
from coach_ai.llm.client import CoachChatModel, LLMError, create_chat_model, make_http_client
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.tools.periods import WEEKDAYS, period_range
from coach_ai.tools.schemas import TOOL_INPUTS

EVAL_DIR = Path(__file__).resolve().parent
# 최신 버전. 이전 버전(tool_selection_v1.jsonl)은 --dataset 으로 골라 그 버전으로 잰 기록과 비교한다
DEFAULT_DATASET = EVAL_DIR / "datasets" / "tool_selection_v1.1.jsonl"
RESULTS_DIR = EVAL_DIR / "results"
# 데이터셋 README 의 기준일. 데이터셋 정답은 이 날짜를 오늘로 본 답이다. --today 의 기본값이다
DEFAULT_TODAY = date(2026, 10, 22)
CATEGORIES = ("tool", "proposed", "no_tool", "clarify", "guard")
# proposed 는 정답이 아직 없는 도구·값이라 기본에서 뺀다 (README "채점 방법")
DEFAULT_CATEGORIES = ("tool", "no_tool", "clarify", "guard")
NODE = "eval_tool_selection"
# range 는 기간 인자를 기준일로 해석한 날짜 범위로, exact 는 인자를 쓴 그대로 비교한다 (기간 표현을 받기 전 채점)
SCORINGS = ("range", "exact")
SCORING_TEXT = {
    "range": "기간 인자(period·current·baseline)는 기준일로 해석한 날짜 범위로 비교"
    "(last_week 와 weeks_ago:1 은 같은 답)",
    "exact": "기간 인자도 쓴 그대로 비교",
}
# 날짜 범위 채점에서 날짜 범위로 바꾸는 인자
PERIOD_ARGS = ("period", "current", "baseline")
# 정답 호출이 args 에 기대값을 적을 때만 비교하는 인자 (도구별). 값 하나는 그 값, 값 목록은 그중 하나,
# null 은 인자가 없는 호출이 맞다. 기대값을 적지 않은 정답과는 이 인자를 비교하지 않아,
# 인자가 생기기 전에 만든 문항의 판정이 바뀌지 않는다
ANALYSIS_ARGS: dict[str, tuple[str, ...]] = {"get_sleep_records": ("analysis", "condition")}
# 다시 센 점수에서 맞음으로 세는 답안. 도구 선택 v1.1 의 두 문항은 정답이 그 요일이 든 지난주 전체(last_week)라,
# 기간 정의(11-04 결정)대로 그 요일 하루만 조회한 실행이 원래 채점에서 틀린 답이 된다. v1.2 의 두 문항 정답과 같다
RECOUNT_ANSWERS: dict[str, list[dict[str, Any]]] = {
    "tool_selection_v1.1/synth-06-019": [{"name": "get_exercise_sessions", "args": {"period": "weekday:sat"}}],
    "tool_selection_v1.1/synth-07-008": [{"name": "get_sleep_records", "args": {"period": "weekday:sun"}}],
}

ERROR_LABELS = {
    "wrong_tool": "도구 틀림",
    "wrong_args": "인자 틀림",
    "missing_call": "호출 누락",
    "extra_call": "호출 초과",
    "invalid_call": "인자 해석 실패",
    "no_clarify": "되묻지 않음",
    "guard_missed": "미탐",
    "guard_wrong": "차단 사유 불일치",
    "guard_blocked": "오탐",
    "llm_error": "LLM 오류",
}
# 계측 기록(LLMCallRecord)에서 문항 기록으로 옮기는 값
TIMING_FIELDS = (
    "finish_reason",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "queue_wait_s",
    "total_s",
    "attempts",
)


# ---------------------------------------------------------------------------
# 데이터셋
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Item:
    """데이터셋 문항 하나. 형식은 eval/datasets/README.md 를 따른다."""

    dataset: str
    id: str
    persona: str
    category: str
    question: str
    expected: dict[str, Any]
    acceptable: list[list[dict[str, Any]]]

    @property
    def key(self) -> str:
        return f"{self.dataset}/{self.id}"

    @property
    def references(self) -> list[list[dict[str, Any]]]:
        """정답 호출 목록과 허용 답안들. 첫 번째가 정답이다."""
        return [self.expected.get("tool_calls", []), *self.acceptable]


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(EVAL_DIR.parent).as_posix()
    except ValueError:
        return path.name


_VERSION = re.compile(r"_(v\d+(?:\.\d+)*)$")


def dataset_version(name: str) -> str | None:
    """데이터셋 파일 이름 끝의 버전 (tool_selection_v1.1 → v1.1). 없으면 None 이다."""
    m = _VERSION.search(name)
    return None if m is None else m.group(1)


def definition_sha256(item: Item) -> str:
    """채점에 쓰는 문항 정의(종류, 질문, 정답, 허용 답안)의 sha256 앞 12자리. 버전이 달라도 같은 문항이면 같다."""
    body = {
        "category": item.category,
        "question": item.question,
        "expected": item.expected,
        "acceptable": item.acceptable,
    }
    return _sha256(json.dumps(body, ensure_ascii=False, sort_keys=True))[:12]


def load_datasets(paths: Sequence[Path]) -> tuple[list[Item], list[dict[str, Any]]]:
    """jsonl 데이터셋을 읽어 문항과 파일 정보(이름·버전·경로·sha256·문항 수)를 반환한다.

    이름은 확장자를 뺀 파일 이름이다. 데이터셋이 다르면 같은 문항 ID 도 따로 센다.
    """
    items: list[Item] = []
    infos: list[dict[str, Any]] = []
    for path in paths:
        raw = path.read_bytes()
        name = path.stem
        if any(info["name"] == name for info in infos):
            raise ValueError(f"같은 이름의 데이터셋을 두 번 받았다: {name}")
        rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
        if dup := sorted(i for i, n in Counter(row["id"] for row in rows).items() if n > 1):
            raise ValueError(f"{name}: 문항 ID 가 겹친다 {dup}")
        if bad := sorted({row["category"] for row in rows} - set(CATEGORIES)):
            raise ValueError(f"{name}: 알 수 없는 종류 {bad}")
        for row in rows:
            items.append(
                Item(
                    dataset=name,
                    id=row["id"],
                    persona=row["persona"],
                    category=row["category"],
                    question=row["question"],
                    expected=row["expected"],
                    acceptable=row.get("acceptable") or [],
                )
            )
        sha = hashlib.sha256(raw).hexdigest()
        version = dataset_version(name)
        infos.append({"name": name, "version": version, "path": _display_path(path), "sha256": sha, "rows": len(rows)})
    return items, infos


def select(items: Sequence[Item], categories: Sequence[str], limit: int | None = None) -> list[Item]:
    """고른 종류의 문항을 파일 순서대로 고르고, limit 이 있으면 앞에서 그만큼만 남긴다."""
    chosen = [item for item in items if item.category in categories]
    return chosen[:limit] if limit else chosen


def needs_llm(items: Sequence[Item]) -> bool:
    """LLM 을 부를 문항이 있는지. guard 문항과 경계 확인에 걸리는 문항은 LLM 을 부르지 않는다."""
    return any(item.category != "guard" and check(item.question) is None for item in items)


# ---------------------------------------------------------------------------
# 기준일
# ---------------------------------------------------------------------------
# DEFAULT_TODAY 로 계산해 다른 기준일에서 판정할 수 없는 정답·허용 답안의 이유
FIXED_ANSWER_REASONS = {
    "date_not_in_question": "질문에 없는 날짜를 정답에 적음",
    "period_for_question_date": "질문의 날짜를 기간 코드로 적음",
    "weekday_question": "요일을 말한 질문의 정답이 '지난 X요일' 하루(weekday:X)가 아님",
}
# 질문에 적힌 날짜 (10월 21일, 10월, 10/21)
_QUESTION_DATE = re.compile(r"\d{1,2}\s*월|\d{1,2}/\d{1,2}")
# WEEKDAYS 와 같은 순서의 요일 이름
_DAY_NAMES = "월화수목금토일"
_NAMED_WEEKDAY = re.compile(f"([{_DAY_NAMES}])요일")
_LAST_WEEKDAY = re.compile(rf"지난\s*([{_DAY_NAMES}])요일")


def _says_dates(question: str, value: str) -> bool:
    """date:·dates: 값의 날짜가 질문에 'M월 D일'로 적혀 있는지. 범위의 끝은 시작과 같은 달이면 'D일'만 적어도 된다."""
    try:
        r = period_range(value, DEFAULT_TODAY)
    except ValueError:
        return False
    text = "".join(question.split())
    start = f"{r.start.month}월{r.start.day}일"
    at = text.find(start)
    if at < 0:
        return False
    if r.end == r.start:
        return True
    end = f"{r.end.day}일" if r.end.month == r.start.month else f"{r.end.month}월{r.end.day}일"
    return end in text[at + len(start) :]


def _fixed_answer_reason(value: Any, question: str) -> str | None:
    """기간 인자 값이 DEFAULT_TODAY 로 계산한 값이면 이유를 반환한다.

    기간 코드(yesterday, weeks_ago:1, weekday:sat 등)는 기준일을 따라 다시 계산되고 질문에 적힌 날짜는 그대로라,
    둘이 어긋난 값은 DEFAULT_TODAY 에서만 질문이 묻는 날을 가리킨다. 요일은 "지난 X요일"과 weekday:X 만 같이 바뀐다.
    """
    if not isinstance(value, str):
        return None
    if value.startswith(("date:", "dates:")):
        return None if _says_dates(question, value) else "date_not_in_question"
    if _QUESTION_DATE.search(question):
        return "period_for_question_date"
    named = set(_NAMED_WEEKDAY.findall(question))
    if not named:
        return None
    last = set(_LAST_WEEKDAY.findall(question))
    if named == last and value in {f"weekday:{WEEKDAYS[_DAY_NAMES.index(name)]}" for name in last}:
        return None
    return "weekday_question"


def _reference_reasons(reference: Sequence[dict[str, Any]], question: str) -> set[str]:
    """답안 하나(호출 묶음)의 기간 인자가 DEFAULT_TODAY 로 계산한 값인 이유."""
    return {
        reason
        for call in reference
        if isinstance(call["args"], dict)
        for key in PERIOD_ARGS
        if (reason := _fixed_answer_reason(call["args"].get(key), question))
    }


def fixed_answer_reasons(item: Item) -> list[str]:
    """정답·허용 답안의 기간 인자가 DEFAULT_TODAY 로 계산한 값인 이유 목록.

    비어 있으면 다른 기준일에서도 판정할 수 있다.
    """
    return sorted(set().union(*(_reference_reasons(ref, item.question) for ref in item.references)))


def _last_weekday(question: str) -> str | None:
    """질문이 요일 하나를 "지난 X요일"로만 말하고 날짜를 적지 않았으면 그 요일(WEEKDAYS 의 값)."""
    named = set(_NAMED_WEEKDAY.findall(question))
    if len(named) != 1 or named != set(_LAST_WEEKDAY.findall(question)) or _QUESTION_DATE.search(question):
        return None
    return WEEKDAYS[_DAY_NAMES.index(named.pop())]


def _weekday_references(references: Sequence[list[dict[str, Any]]], weekday: str) -> list[list[dict[str, Any]]]:
    """답안의 기간을 weekday:<요일>로 바꾼 답안. 모든 호출의 기간 인자가 period 하나인 답안만 바꾼다."""
    out = []
    for reference in references:
        if reference and all(
            isinstance(c["args"], dict) and [k for k in PERIOD_ARGS if k in c["args"]] == ["period"] for c in reference
        ):
            out.append([{**c, "args": {**c["args"], "period": f"weekday:{weekday}"}} for c in reference])
    return out


def _unique(references: Sequence[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    """같은 호출 묶음(순서 무시)을 한 번만 남긴다. 처음 나온 순서를 지킨다."""
    seen: set[tuple[tuple[str, str], ...]] = set()
    out = []
    for reference in references:
        key = tuple(sorted(map(call_key, reference)))
        if key not in seen:
            seen.add(key)
            out.append(reference)
    return out


def _item_for_other_day(item: Item) -> tuple[Item | None, list[str]]:
    """DEFAULT_TODAY 가 아닌 기준일에 판정할 문항과 DEFAULT_TODAY 로 계산한 답안의 이유. 판정할 수 없으면 None 이다.

    "지난 X요일"만 말한 문항은 그 답안을 빼고 정답·허용 답안의 기간을 weekday:X 로 바꾼 답안을 넣는다. weekday:X 는
    채점에서 질문한 날 이전에 가장 최근에 지난 X요일로 계산된다. 다른 문항은 남은 답안으로만 판정하면 그 답안이
    맞히던 답(지지난 주말의 토·일 이틀 등)이 틀린 답이 되어 None 이다.
    """
    per_reference = [_reference_reasons(ref, item.question) for ref in item.references]
    reasons = sorted(set().union(*per_reference))
    if not reasons:
        return item, []
    weekday = _last_weekday(item.question)
    added = _weekday_references(item.references, weekday) if weekday else []
    if not added:
        return None, reasons
    kept = [ref for ref, fixed in zip(item.references, per_reference, strict=True) if not fixed]
    expected, *acceptable = _unique(added + kept if per_reference[0] else kept + added)
    return replace(item, expected={**item.expected, "tool_calls": expected}, acceptable=acceptable), reasons


@dataclass(frozen=True, slots=True)
class DayItems:
    """기준일에 실행할 문항. 기준일이 DEFAULT_TODAY 면 데이터셋 문항 그대로다."""

    items: list[Item]
    # 실행하지 않는 문항 키 → 정답·허용 답안을 DEFAULT_TODAY 로 계산한 이유
    excluded: dict[str, list[str]] = field(default_factory=dict)
    # "지난 X요일"만 말해 DEFAULT_TODAY 로 계산한 답안을 빼고 실행하는 문항 키 → 뺀 답안의 이유
    adjusted: dict[str, list[str]] = field(default_factory=dict)


def items_for_day(items: Sequence[Item], today: date) -> DayItems:
    """기준일 today 에 실행할 문항. today 가 DEFAULT_TODAY 가 아니면 _item_for_other_day 로 답안을 고친다.

    proposed 는 채점하지 않아 고치지 않는다.
    """
    if today == DEFAULT_TODAY:
        return DayItems(list(items))
    kept: list[Item] = []
    excluded: dict[str, list[str]] = {}
    adjusted: dict[str, list[str]] = {}
    for item in items:
        fitted, reasons = (item, []) if item.category == "proposed" else _item_for_other_day(item)
        if fitted is None:
            excluded[item.key] = reasons
            continue
        kept.append(fitted)
        if reasons:
            adjusted[item.key] = reasons
    return DayItems(kept, excluded, adjusted)


def last_weekday_dates(items: Sequence[Item], today: date) -> dict[str, str]:
    """채점 문항 중 "지난 X요일"만 말한 문항의 요일 → 질문한 날(today) 이전에 가장 최근에 지난 그 요일. 요일 순서다."""
    weekdays = {weekday for item in items if item.category != "proposed" and (weekday := _last_weekday(item.question))}
    return {w: period_range(f"weekday:{w}", today).start.isoformat() for w in WEEKDAYS if w in weekdays}


def _day_fields(day: DayItems, today: date) -> dict[str, Any]:
    """DEFAULT_TODAY 가 아닌 기준일로 잰 결과에 넣는 항목. 데이터셋 기준일이면 넣지 않는다."""
    if today == DEFAULT_TODAY:
        return {}
    return {
        "dataset_today": DEFAULT_TODAY.isoformat(),
        "excluded": day.excluded,
        "adjusted": day.adjusted,
        "last_weekdays": last_weekday_dates(day.items, today),
    }


# ---------------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Outcome:
    """문항 한 번의 결과. guard 문항이거나 경계 확인에 걸렸으면 LLM 을 부르지 않아 llm 이 False 다."""

    blocked_by: str | None = None
    llm: bool = False
    calls: tuple[dict[str, Any], ...] = ()
    # 인자가 JSON 객체로 해석되지 않은 호출. args 는 원문 문자열이다
    invalid_calls: tuple[dict[str, Any], ...] = ()
    text: str = ""
    # LLMError.code. 오류면 채점하지 않는다
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    # None 은 채점하지 않은 것이다 (proposed, LLM 오류)
    correct: bool | None
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _period_value(value: Any, today: date) -> Any:
    """기간 인자를 기준일로 해석한 날짜 범위로 바꾼다. 형식이 아니거나 구간을 만들 수 없는 값은 그대로 둔다.

    날짜 범위는 문자열이 아닌 목록이라, 날짜 범위처럼 쓴 문자열 인자와 같아지지 않는다. 조회 한도는 검사하지 않아
    한도를 넘는 기간도 날짜로 비교한다.
    """
    if not isinstance(value, str):
        return value
    try:
        r = period_range(value, today)
    except ValueError:
        return value
    return ["dates", r.start.isoformat(), r.end.isoformat()]


def with_dates(calls: Sequence[dict[str, Any]], today: date | None) -> list[dict[str, Any]]:
    """today 가 있으면 기간 인자를 날짜 범위로 바꾼 사본을 반환한다. 없으면 그대로다."""
    if today is None:
        return list(calls)
    out = []
    for call in calls:
        args = call["args"]
        if isinstance(args, dict):
            args = {k: _period_value(v, today) if k in PERIOD_ARGS else v for k, v in args.items()}
        out.append({**call, "args": args})
    return out


def without_defaults(calls: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """선택 인자를 기본값과 같은 값·타입으로 적은 호출에서 그 인자를 뺀 사본.

    include_non_main=false 를 적은 호출은 적지 않은 호출과 게이트웨이에서 같은 호출이라 같은 답으로 센다.
    """
    out = []
    for call in calls:
        schema = TOOL_INPUTS.get(call["name"])
        args = call["args"]
        if schema is not None and isinstance(args, dict):
            fields = schema.model_fields
            args = {
                k: v
                for k, v in args.items()
                if not (
                    k in fields
                    and not fields[k].is_required()
                    and type(v) is type(fields[k].default)
                    and v == fields[k].default
                )
            }
        out.append({**call, "args": args})
    return out


def call_key(call: dict[str, Any]) -> tuple[str, str]:
    """(도구 이름, 키를 정렬한 인자 JSON). 두 호출이 같은지 이 값으로 확인한다."""
    return call["name"], json.dumps(call["args"], ensure_ascii=False, sort_keys=True)


def _expected_keys(call: dict[str, Any]) -> list[str]:
    """정답 호출이 기대값을 적은 분석 인자."""
    args = call["args"]
    return [k for k in ANALYSIS_ARGS.get(call["name"], ()) if isinstance(args, dict) and k in args]


def _meets(args: dict[str, Any], key: str, expected: Any) -> bool:
    """실제 호출의 인자가 기대값을 만족하는지. 값 목록은 그중 하나와 같으면 맞고, null 은 인자가 없어야 맞다."""
    choices = expected if isinstance(expected, list) else [expected]
    if key not in args:
        return None in choices
    value = json.dumps(args[key], ensure_ascii=False, sort_keys=True)
    return any(c is not None and value == json.dumps(c, ensure_ascii=False, sort_keys=True) for c in choices)


def _without_analysis(call: dict[str, Any]) -> dict[str, Any]:
    """분석 인자를 뺀 사본. 분석 인자가 없는 도구의 호출은 그대로다."""
    keys = ANALYSIS_ARGS.get(call["name"], ())
    if not keys or not isinstance(call["args"], dict):
        return call
    return {**call, "args": {k: v for k, v in call["args"].items() if k not in keys}}


def _same_call(call: dict[str, Any], reference: dict[str, Any]) -> bool:
    """실제 호출 하나가 정답 호출 하나와 같은지. 분석 인자는 정답이 적은 것만 기대값으로 비교한다."""
    if call["name"] != reference["name"] or not isinstance(call["args"], dict):
        return False
    if call_key(_without_analysis(call)) != call_key(_without_analysis(reference)):
        return False
    return all(_meets(call["args"], k, reference["args"][k]) for k in _expected_keys(reference))


def same_calls(actual: Sequence[dict[str, Any]], reference: Sequence[dict[str, Any]]) -> bool:
    """호출 묶음이 같은지. 순서는 검사하지 않고 같은 호출의 개수는 검사한다.

    분석 인자(ANALYSIS_ARGS)는 정답 호출이 기대값을 적었을 때만 비교한다. 기대값을 적은 정답 호출이 없으면 실제 호출의
    분석 인자를 빼고 호출 묶음을 그대로 비교하고, 있으면 정답 호출마다 같은 실제 호출을 하나씩 짝짓는다.
    """
    if not any(map(_expected_keys, reference)):
        return Counter(call_key(_without_analysis(c)) for c in actual) == Counter(map(call_key, reference))
    if len(actual) != len(reference):
        return False

    def pair(i: int, free: tuple[int, ...]) -> bool:
        if i == len(reference):
            return True
        return any(
            _same_call(actual[j], reference[i]) and pair(i + 1, free[:n] + free[n + 1 :]) for n, j in enumerate(free)
        )

    return pair(0, tuple(range(len(actual))))


def expected_variants(call: dict[str, Any]) -> list[dict[str, Any]]:
    """정답 호출 하나를 기대값을 만족하는 실제 호출들로 펼친다. 값 목록은 값마다 하나씩, null 은 그 인자를 뺀 호출이다.

    데이터셋 검사가 정답·허용 답안을 도구 스키마로 확인할 때 쓴다.
    """
    variants = [call["args"]]
    for key in _expected_keys(call):
        value = call["args"][key]
        choices = value if isinstance(value, list) else [value]
        variants = [
            {k: v for k, v in args.items() if k != key} if choice is None else {**args, key: choice}
            for args in variants
            for choice in choices
        ]
    return [{**call, "args": args} for args in variants]


def gateway_denial(call: dict[str, Any]) -> str | None:
    """도구 스키마로 거부될 호출이면 사유(없는 도구, 스키마에 없는 인자, 허용되지 않는 값)를 반환한다."""
    schema = TOOL_INPUTS.get(call["name"])
    if schema is None:
        return "unknown_tool"
    try:
        schema.model_validate(call["args"])
    except ValidationError as e:
        return "extra_field" if any(err["type"] == "extra_forbidden" for err in e.errors()) else "invalid_args"
    return None


def _names(calls: Sequence[dict[str, Any]]) -> Counter[str]:
    return Counter(c["name"] for c in calls)


def _differing_keys(a: dict[str, Any], b: dict[str, Any], checked: Sequence[str] = ()) -> set[str]:
    """값이 다른 인자 이름. checked(분석 인자)는 b(정답)가 적었을 때만 기대값으로 비교한다."""
    keys = set()
    for k in a.keys() | b.keys():
        if k in checked:
            if k in b and not _meets(a, k, b[k]):
                keys.add(k)
        elif k not in a or k not in b or a[k] != b[k]:
            keys.add(k)
    return keys


def arg_diff(actual: Sequence[dict[str, Any]], reference: Sequence[dict[str, Any]]) -> list[str]:
    """도구 이름이 같은 호출끼리 짝지어 값이 다른 인자 이름을 모은다. 가장 덜 다른 짝을 고른다."""
    diff: set[str] = set()
    for name in _names(reference):
        mine = [c["args"] for c in actual if c["name"] == name]
        theirs = [c["args"] for c in reference if c["name"] == name]
        checked = ANALYSIS_ARGS.get(name, ())
        # 한 응답의 같은 도구 호출은 몇 개뿐이라 모든 짝을 확인한다. 너무 많으면 정렬 순서로 짝짓는다
        orders = itertools.permutations(mine) if len(mine) <= 6 else [sorted(mine, key=str)]
        diff |= min(
            (
                set().union(*(_differing_keys(a, b, checked) for a, b in zip(order, theirs, strict=False)))
                for order in orders
            ),
            key=len,
            default=set(),
        )
    return sorted(diff)


def _distance(actual: Sequence[dict[str, Any]], reference: Sequence[dict[str, Any]]) -> tuple[int, int, int]:
    mine, theirs = _names(actual), _names(reference)
    names = sum(((mine - theirs) + (theirs - mine)).values())
    args = len(arg_diff(actual, reference)) if mine == theirs else 0
    return abs(len(actual) - len(reference)), names, args


def classify(actual: Sequence[dict[str, Any]], reference: Sequence[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """정답과 다른 호출 묶음을 오답 분류 하나로 나눈다. 호출 수 → 도구 이름 → 인자 순서로 검사한다."""
    if len(actual) < len(reference):
        return "missing_call", {}
    if len(actual) > len(reference):
        return "extra_call", {}
    if _names(actual) != _names(reference):
        return "wrong_tool", {}
    return "wrong_args", {"args": arg_diff(actual, reference)}


# 물음표로 끝나는 문장에서 시각·시간대를 물으면 되물은 것으로 본다. 보고서에 답변을 모아 두어 사람이 다시 확인한다
_QUESTION = re.compile(r"[^.?!？\n]*[?？]")
_TIME_WORDS = re.compile(r"몇\s?시|시간|시각|언제|부터|까지")


def asks_back(text: str) -> bool:
    return any(_TIME_WORDS.search(q) for q in _QUESTION.findall(text))


def score(item: Item, out: Outcome, *, today: date | None = None) -> Verdict:
    """문항 한 번의 결과를 채점한다. 기준은 eval/datasets/README.md "채점 방법"이다.

    today 가 있으면 기간 인자를 그날 기준 날짜 범위로 비교하고(range), 없으면 쓴 그대로 비교한다(exact).
    다시 세는 문항(RECOUNT_ANSWERS)에서 틀린 실행이 다시 세는 답안과 같으면 판정은 그대로 두고
    detail 에 recount 를 넣는다.
    """
    if out.error_code is not None:
        return Verdict(None, "llm_error", {"code": out.error_code})
    exp = item.expected
    if exp["type"] == "guard":
        if out.blocked_by == exp["guard_category"]:
            return Verdict(True)
        error = "guard_missed" if out.blocked_by is None else "guard_wrong"
        return Verdict(False, error, {"blocked_by": out.blocked_by})
    if out.blocked_by is not None:
        # 운영에서는 LLM 호출 전에 정해진 안내로 끝나 도구 호출이 생기지 않는다
        return Verdict(None if item.category == "proposed" else False, "guard_blocked", {"blocked_by": out.blocked_by})

    denied = [{"name": c["name"], "reason": reason} for c in out.calls if (reason := gateway_denial(c))]
    detail: dict[str, Any] = {"denied": denied} if denied else {}
    if item.category == "proposed":
        return Verdict(None, None, detail)
    if out.invalid_calls:
        return Verdict(False, "invalid_call", detail)
    if exp["type"] in ("no_tool", "clarify"):
        if out.calls:
            return Verdict(False, "extra_call", detail)
        if exp["type"] == "no_tool":
            return Verdict(True, None, detail)
        asked = asks_back(out.text)
        return Verdict(asked, None if asked else "no_clarify", {**detail, "asked": asked})

    actual = with_dates(without_defaults(out.calls), today)
    references = [with_dates(reference, today) for reference in item.references]
    for i, reference in enumerate(references):
        if same_calls(actual, reference):
            # 0 은 정답, 1 부터는 허용 답안 번호다
            return Verdict(True, None, {**detail, "matched": i})
    closest = min(range(len(references)), key=lambda i: _distance(actual, references[i]))
    error, extra = classify(actual, references[closest])
    detail = {**detail, "closest": closest, **extra}
    recount = RECOUNT_ANSWERS.get(item.key)
    if recount is not None and same_calls(actual, with_dates(recount, today)):
        detail["recount"] = True
    return Verdict(False, error, detail)


def fmt_call(call: dict[str, Any]) -> str:
    args = call.get("args")
    inner = ", ".join(f"{k}={v}" for k, v in sorted(args.items())) if isinstance(args, dict) else str(args)
    return f"{call.get('name')}({inner})"


def signature(out: Outcome, verdict: Verdict) -> str | None:
    """반복한 결과가 같은 출력인지 비교하는 문자열. 답변 문장은 비교하지 않는다. LLM 오류는 None 이다."""
    if out.error_code is not None:
        return None
    if out.blocked_by is not None:
        return f"(경계: {out.blocked_by})"
    if not out.llm:
        return "(경계 통과)"
    parts = sorted(fmt_call(c) for c in out.calls)
    parts += [f"(해석 실패: {c.get('name') or '?'})" for c in out.invalid_calls]
    if parts:
        return " + ".join(parts)
    return "(도구 없음, 되물음)" if verdict.detail.get("asked") else "(도구 없음)"


def _fmt_expected_call(call: dict[str, Any]) -> str:
    """정답 호출 표시. 분석 인자의 값 목록은 | 로 잇고 null 은 (없음)으로 적는다. 나머지는 fmt_call 과 같다."""
    args = call.get("args")
    if not isinstance(args, dict):
        return fmt_call(call)
    checked = ANALYSIS_ARGS.get(call.get("name"), ())

    def arg(key: str, value: Any) -> str:
        if key in checked and (value is None or isinstance(value, list)):
            choices = value if isinstance(value, list) else [value]
            return f"{key}=" + "|".join("(없음)" if c is None else str(c) for c in choices)
        return f"{key}={value}"

    return f"{call.get('name')}({', '.join(arg(k, v) for k, v in sorted(args.items()))})"


def expected_text(item: Item) -> str:
    exp = item.expected
    if exp["type"] == "tool_calls":
        text = " + ".join(_fmt_expected_call(c) for c in exp["tool_calls"])
        return text + (f" (허용 답안 {len(item.acceptable)})" if item.acceptable else "")
    if exp["type"] == "guard":
        return f"(경계: {exp['guard_category']})"
    return "(도구 없음, 되물음)" if exp["type"] == "clarify" else "(도구 없음)"


# ---------------------------------------------------------------------------
# 호출
# ---------------------------------------------------------------------------
class RequestLog:
    """LLM 요청 본문에서 메시지·도구를 뺀 생성 인자를 모은다. temperature 를 실제로 보냈는지 결과에 기록한다.

    요청마다 같아야 하므로 서로 다른 것만 둔다.
    """

    def __init__(self) -> None:
        self.params: list[dict[str, Any]] = []

    async def __call__(self, request: httpx.Request) -> None:
        try:
            body = json.loads(request.content)
        except (httpx.RequestNotRead, ValueError):
            return
        if not isinstance(body, dict) or "messages" not in body:
            return
        params = {k: v for k, v in body.items() if k not in ("messages", "tools")}
        if params not in self.params:
            self.params.append(params)


def make_model(
    settings: Settings, temperature: float | None, recorder: MetricsRecorder, log: RequestLog
) -> CoachChatModel:
    """운영과 같은 create_chat_model 로 만든다. temperature 는 주었을 때만 요청에 넣는다 (생략하면 서버 기본값)."""
    http_client = make_http_client(settings)
    http_client.event_hooks["request"].append(log)
    overrides: dict[str, Any] = {} if temperature is None else {"temperature": temperature}
    return create_chat_model(settings, recorder=recorder, http_client=http_client, **overrides)


async def first_round(
    model: BaseChatModel, question: str, system_prompt: str, *, request_id: str | None = None
) -> AIMessage:
    """운영 첫 라운드(agent_node, 요약·이전 대화 없음)와 같은 요청을 보내고 조각을 합친 응답을 반환한다.

    agent_node 의 호출 방식을 바꾸면 여기도 맞춘다. 두 요청 본문이 같은지는 테스트가 확인한다.
    """
    messages = build_messages(question, None, (), system_prompt)
    config = {"metadata": {"request_id": request_id, "node": NODE}}
    merged: AIMessageChunk | None = None
    async for chunk in model.bind_tools(tool_specs()).astream(messages, config=config):
        merged = chunk if merged is None else merged + chunk
    return message_chunk_to_message(merged) if merged is not None else AIMessage(content="")


def outcome_of(message: AIMessage) -> Outcome:
    return Outcome(
        llm=True,
        calls=tuple({"name": c["name"], "args": c["args"]} for c in message.tool_calls),
        invalid_calls=tuple(
            {"name": c.get("name"), "args": c.get("args"), "error": c.get("error")} for c in message.invalid_tool_calls
        ),
        text=message.text,
    )


def make_record(item: Item, run: int, out: Outcome, verdict: Verdict, request_id: str | None) -> dict[str, Any]:
    """문항 한 번의 기록. 질문은 합성 문장이고 도구를 실행하지 않아 답변에 건강 기록 값이 섞이지 않는다."""
    return {
        "dataset": item.dataset,
        "id": item.id,
        "persona": item.persona,
        "category": item.category,
        "run": run,
        "question": item.question,
        "correct": verdict.correct,
        "error": verdict.error,
        "detail": verdict.detail,
        "signature": signature(out, verdict),
        "blocked_by": out.blocked_by,
        "llm": out.llm,
        "calls": list(out.calls),
        "invalid_calls": list(out.invalid_calls),
        "text": out.text,
        "request_id": request_id,
        **dict.fromkeys(TIMING_FIELDS),
    }


def _attach_timings(records: list[dict[str, Any]], recorder: MetricsRecorder) -> None:
    """계측 기록의 시간·토큰을 요청 ID 로 찾아 문항 기록에 붙인다."""
    calls = {r.request_id: r for r in recorder.records("llm")}
    for rec in records:
        call = calls.get(rec["request_id"])
        if call is not None:
            rec.update({name: getattr(call, name) for name in TIMING_FIELDS})


def _progress_line(run: int, repeat: int, batch: Sequence[dict[str, Any]], elapsed: float) -> str:
    scored = [r for r in batch if r["correct"] is not None]
    correct = sum(r["correct"] for r in scored)
    llm = sum(r["llm"] for r in batch)
    errors = sum(r["error"] == "llm_error" for r in batch)
    return (
        f"[{run}/{repeat}] {len(batch)}문항 · 정답 {correct}/{len(scored)} ({_pct(_ratio(correct, len(scored)))}) · "
        f"LLM {llm}회 · 오류 {errors} · {elapsed:.1f}초"
    )


async def evaluate(
    items: Sequence[Item],
    *,
    repeat: int,
    system_prompt: str,
    model: BaseChatModel | None,
    recorder: MetricsRecorder | None = None,
    concurrency: int = 1,
    progress: Callable[[str], None] = print,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """문항마다 repeat 회 채점한 기록을 회차·문항 순서로 반환한다. 회차 하나를 끝내고 다음 회차로 간다.

    today 가 있으면 기간 인자를 그날 기준 날짜 범위로 비교한다 (score).
    """
    slots = asyncio.Semaphore(concurrency)

    async def one(item: Item, run: int) -> dict[str, Any]:
        blocked = check(item.question)
        out = Outcome(blocked_by=blocked.category if blocked else None)
        request_id = None
        if item.category != "guard" and blocked is None:
            if model is None:
                raise RuntimeError("LLM 을 불러야 하는 문항인데 모델이 없다")
            request_id = f"eval-{item.key}-{run}"
            async with slots:
                try:
                    out = outcome_of(await first_round(model, item.question, system_prompt, request_id=request_id))
                except LLMError as e:
                    out = Outcome(llm=True, error_code=e.code)
        return make_record(item, run, out, score(item, out, today=today), request_id)

    records: list[dict[str, Any]] = []
    for run in range(1, repeat + 1):
        started = time.perf_counter()
        batch = await asyncio.gather(*(one(item, run) for item in items))
        records += batch
        progress(_progress_line(run, repeat, batch, time.perf_counter() - started))
    if recorder is not None:
        _attach_timings(records, recorder)
    return records


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------
def _ratio(n: float, d: int) -> float | None:
    return n / d if d else None


def _record_key(r: dict[str, Any]) -> str:
    return f"{r['dataset']}/{r['id']}"


def accuracy(records: Sequence[dict[str, Any]], repeat: int) -> dict[str, Any]:
    """채점한 기록의 정답 비율과 회차별 정답 비율의 최소·최대."""
    scored = [r for r in records if r["correct"] is not None]
    correct = sum(r["correct"] for r in scored)
    per_run = []
    for run in range(1, repeat + 1):
        rs = [r for r in scored if r["run"] == run]
        if rs:
            per_run.append(sum(r["correct"] for r in rs) / len(rs))
    return {
        "items": len({_record_key(r) for r in records}),
        "scored": len(scored),
        "correct": correct,
        "accuracy": _ratio(correct, len(scored)),
        "run_min": min(per_run, default=None),
        "run_max": max(per_run, default=None),
    }


def item_stats(item: Item, records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """문항 하나의 반복 결과. outputs 는 출력(signature)별 횟수다."""
    scored = [r for r in records if r["correct"] is not None]
    outputs = Counter(r["signature"] for r in records if r["signature"] is not None)
    wrong = Counter(r["signature"] for r in scored if not r["correct"] and r["signature"] is not None)
    top = outputs.most_common(1)
    return {
        "key": item.key,
        "id": item.id,
        "persona": item.persona,
        "category": item.category,
        "question": item.question,
        "expected": expected_text(item),
        "definition_sha256": definition_sha256(item),
        "runs": len(records),
        "scored": len(scored),
        "correct": sum(r["correct"] for r in scored),
        "outputs": dict(outputs),
        "modal_share": top[0][1] / outputs.total() if top else None,
        "top_wrong": list(wrong.most_common(1)[0]) if wrong else None,
    }


def consistency(stats: Sequence[dict[str, Any]], repeat: int) -> dict[str, Any] | None:
    """문항별 반복 결과를 묶는다. 반복이 1회면 계산하지 않는다.

    same_output 은 반복한 출력이 모두 같은 문항 비율, modal_share 는 문항마다 가장 많이 나온 출력의 비율 평균이다.
    flaky 는 맞을 때와 틀릴 때가 섞인 문항 수다.
    """
    if repeat < 2:
        return None
    observed = [s for s in stats if s["outputs"]]
    scored = [s for s in stats if s["scored"]]
    return {
        "items": len(stats),
        "same_output": _ratio(sum(len(s["outputs"]) == 1 for s in observed), len(observed)),
        "modal_share": statistics.fmean(s["modal_share"] for s in observed) if observed else None,
        "all_correct": _ratio(sum(s["correct"] == s["scored"] for s in scored), len(scored)),
        "majority_correct": _ratio(sum(s["correct"] * 2 > s["scored"] for s in scored), len(scored)),
        "flaky": sum(0 < s["correct"] < s["scored"] for s in scored),
    }


def _proposed_behavior(r: dict[str, Any]) -> str:
    if r["blocked_by"]:
        return "경계에 걸림"
    if r["error"] == "llm_error":
        return "LLM 오류"
    if r["invalid_calls"]:
        return "인자 해석 실패"
    if not r["calls"]:
        return "도구 없이 답함"
    reasons = {d["reason"] for d in r["detail"].get("denied", [])}
    if "unknown_tool" in reasons:
        return "없는 도구 호출"
    if reasons:
        return "스키마 밖 인자로 호출"
    return "지금 도구로 호출"


def _clarify_answer(r: dict[str, Any]) -> dict[str, Any]:
    if r["error"] == "llm_error":
        verdict = "LLM 오류"
    elif r["blocked_by"]:
        verdict = f"경계: {r['blocked_by']}"
    elif r["calls"] or r["invalid_calls"]:
        verdict = f"도구 호출: {r['signature']}"
    else:
        verdict = "되물음" if r["correct"] else "되묻지 않음"
    return {"id": r["id"], "dataset": r["dataset"], "run": r["run"], "verdict": verdict, "text": r["text"]}


def summarize(records: Sequence[dict[str, Any]], items: Sequence[Item], repeat: int) -> dict[str, Any]:
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_key[_record_key(r)].append(r)
    stats = {item.key: item_stats(item, by_key[item.key]) for item in items}
    scored_items = [item for item in items if item.category != "proposed"]

    def group(members: Sequence[Item]) -> dict[str, Any]:
        rs = [r for item in members for r in by_key[item.key]]
        return {**accuracy(rs, repeat), "consistency": consistency([stats[i.key] for i in members], repeat)}

    def grouped(key: Callable[[Item], str], members: Sequence[Item]) -> dict[str, list[Item]]:
        out: dict[str, list[Item]] = defaultdict(list)
        for item in members:
            out[key(item)].append(item)
        return out

    categories = grouped(lambda i: i.category, items)
    datasets = grouped(lambda i: i.dataset, scored_items)
    personas = grouped(lambda i: i.persona, scored_items)
    tools = grouped(
        lambda i: " + ".join(sorted(c["name"] for c in i.expected["tool_calls"])),
        [i for i in items if i.category == "tool"],
    )
    wrong = [r for r in records if r["correct"] is False]
    llm = [r for r in records if r["llm"]]
    totals = [r["total_s"] for r in llm if r["total_s"] is not None]
    waits = [r["queue_wait_s"] for r in llm if r["queue_wait_s"] is not None]
    proposed = [r for r in records if r["category"] == "proposed"]
    overall = group(scored_items)
    recounted = Counter(_record_key(r) for r in wrong if r["detail"].get("recount"))
    recount_keys = [key for key in RECOUNT_ANSWERS if key in stats]
    return {
        "overall": overall,
        # 다시 세는 문항을 잰 측정에만 있다
        "recount": (
            {"correct": overall["correct"] + recounted.total(), "added": {key: recounted[key] for key in recount_keys}}
            if recount_keys
            else None
        ),
        "by_category": {c: group(categories[c]) for c in CATEGORIES if c in categories},
        "by_dataset": {d: group(members) for d, members in datasets.items()},
        "by_persona": {p: group(members) for p, members in sorted(personas.items())},
        "by_tool": {t: group(members) for t, members in sorted(tools.items())},
        "errors": dict(Counter(r["error"] for r in wrong).most_common()),
        "wrong_arg_keys": dict(
            Counter(k for r in wrong if r["error"] == "wrong_args" for k in r["detail"]["args"]).most_common()
        ),
        "answered_without_tools": sum(r["error"] == "missing_call" and not r["calls"] for r in wrong),
        "denied_calls": dict(
            Counter(d["reason"] for r in records for d in r["detail"].get("denied", [])).most_common()
        ),
        "llm": {
            "calls": len(llm),
            "errors": sum(r["error"] == "llm_error" for r in llm),
            "input_tokens": sum(r["input_tokens"] or 0 for r in llm),
            "output_tokens": sum(r["output_tokens"] or 0 for r in llm),
            "total_p50": pct(totals, 50),
            "total_p95": pct(totals, 95),
            "wait_p95": pct(waits, 95),
        },
        "proposed": dict(Counter(map(_proposed_behavior, proposed)).most_common()) if proposed else None,
        "clarify_answers": [_clarify_answer(r) for r in records if r["category"] == "clarify"],
        "items": list(stats.values()),
    }


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def run_eval(
    settings: Settings | None,
    args: argparse.Namespace,
    items: Sequence[Item],
    datasets: Sequence[dict[str, Any]],
    template: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """문항을 채점하고 (보고서, 문항별 기록)을 반환한다. settings 가 None 이면 LLM 을 부르는 문항이 없어야 한다.

    template 의 날짜 자리에는 기준일(args.today)을 넣는다. 기준일이 DEFAULT_TODAY 가 아니면 items_for_day 로 고친
    문항을 실행하고, 실행하지 않은 문항과 답안을 고친 문항을 보고서의 excluded·adjusted 에 기록한다.
    """
    day = items_for_day(items, args.today)
    items = day.items
    measured_at = datetime.now().isoformat(timespec="seconds")
    recorder = MetricsRecorder(max_records=max(1000, len(items) * args.repeat))
    log = RequestLog()
    model = make_model(settings, args.temperature, recorder, log) if settings is not None else None
    started = time.perf_counter()
    try:
        records = await evaluate(
            items,
            repeat=args.repeat,
            system_prompt=render_system_prompt(template, args.today),
            model=model,
            recorder=recorder,
            concurrency=args.concurrency,
            today=args.today if args.scoring == "range" else None,
        )
    finally:
        if model is not None:
            await model.http_async_client.aclose()
    params = log.params[0] if log.params else None
    selected = Counter(item.dataset for item in items)
    report = {
        "measured_at": measured_at,
        "commit": git_commit(),
        "model": settings.llm_model if settings else None,
        "base_url_host": httpx.URL(settings.llm_base_url).host if settings else None,
        "max_concurrency": settings.llm_max_concurrency if settings else None,
        "concurrency": args.concurrency,
        "repeat": args.repeat,
        "categories": list(args.only or DEFAULT_CATEGORIES),
        "limit": args.limit,
        "today": args.today.isoformat(),
        # 데이터셋 기준일로 잰 결과에는 이 항목들이 없다
        **_day_fields(day, args.today),
        "period_scoring": args.scoring,
        "prompt_version": args.prompt_version,
        "prompt_sha256": _sha256(template)[:12],
        # 날짜 자리가 없는 템플릿(v1, v1.1)이면 None 이다
        "prompt_date": args.today.isoformat() if TODAY_SLOT in template else None,
        "tools_sha256": _sha256(json.dumps(tool_specs(), ensure_ascii=False, sort_keys=True))[:12],
        # 실제 요청에 들어간 값. 요청에 없으면 서버 기본값을 쓴 것이다 (LLM 을 부르지 않았으면 둘 다 None)
        "temperature": None if params is None else params.get("temperature"),
        "temperature_source": None if params is None else ("request" if "temperature" in params else "server_default"),
        # 메시지·도구를 뺀 요청 생성 인자. 요청마다 같아야 해서 보통 하나다
        "request_params": log.params,
        "datasets": [{**info, "selected": selected[info["name"]]} for info in datasets],
        "elapsed_s": round(time.perf_counter() - started, 1),
        "summary": summarize(records, items, args.repeat),
    }
    return report, records


# ---------------------------------------------------------------------------
# 보고
# ---------------------------------------------------------------------------
def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _md(text: Any, limit: int | None = None) -> str:
    """표 칸에 넣을 수 있게 줄바꿈을 공백으로 바꾸고 | 를 이스케이프한다."""
    s = " ".join(str(text).split()).replace("|", "\\|")
    return s if limit is None or len(s) <= limit else s[: limit - 1] + "…"


def _temperature_text(report: dict[str, Any]) -> str:
    if report["temperature_source"] is None:
        return "- (LLM 호출 없음)"
    if report["temperature_source"] == "server_default":
        return "서버 기본값 (요청에 넣지 않음)"
    return f"{report['temperature']} (요청에 넣음)"


def prompt_text(report: dict[str, Any]) -> str:
    """프롬프트 버전, 템플릿 sha256, 넣은 날짜. 날짜를 넣기 전에 저장한 기록에는 prompt_date 가 없다."""
    day = report.get("prompt_date")
    inserted = "없음" if day is None else today_text(date.fromisoformat(day))
    return f"`{report['prompt_version']}` (템플릿 sha256 {report['prompt_sha256']}, 넣은 날짜 {inserted})"


def dataset_line(d: dict[str, Any]) -> str:
    """보고서 머리말의 데이터셋 줄. 버전을 기록하기 전에 저장한 결과에는 version 이 없다."""
    version = f" (버전 {d['version']})" if d.get("version") else ""
    return f"- 데이터셋 `{d['path']}`{version} {d['selected']}/{d['rows']}문항 · sha256 {d['sha256'][:12]}"


def _today_line(report: dict[str, Any]) -> str:
    if report.get("prompt_date") is None:
        return f"- 기준일 {report['today']}. 이 프롬프트에는 날짜 자리가 없어 결과에만 기록한다"
    return f"- 기준일 {report['today']}. 시스템 프롬프트의 날짜 자리에 넣었다"


def _excluded_lines(report: dict[str, Any]) -> list[str]:
    """데이터셋 기준일이 아닌 날로 잰 결과에서 뺀 문항, 답안을 고친 문항, "지난 X요일" 정답 날짜."""
    excluded = report.get("excluded")
    if excluded is None:
        return []
    adjusted = report.get("adjusted") or {}
    head = f"- 데이터셋 정답의 기준일: {report['dataset_today']}."
    many = len(report["datasets"]) > 1

    def listed(entries: dict[str, list[str]]) -> list[str]:
        return [
            f"  - `{key if many else key.split('/', 1)[1]}` {', '.join(FIXED_ANSWER_REASONS[r] for r in reasons)}"
            for key, reasons in entries.items()
        ]

    if not excluded and not adjusted:
        lines = [f"{head} 그날로 계산한 정답·허용 답안이 있는 문항은 없다"]
    elif excluded:
        reason = "그날로 계산한 정답·허용 답안이 있어 이 기준일에 판정할 수 없는"
        lines = [f"{head} {reason} {len(excluded)}문항은 실행하지 않았다", *listed(excluded)]
    else:
        lines = [f"{head} 그날로 계산한 정답·허용 답안이 있어 실행하지 않은 문항은 없다"]
    if adjusted:
        lines += [f"- 그날로 계산한 답안을 빼고 판정한 {len(adjusted)}문항", *listed(adjusted)]
    if days := report.get("last_weekdays"):
        answers = ", ".join(
            f"{_DAY_NAMES[WEEKDAYS.index(w)]}요일 {d.month}월 {d.day}일"
            for w, d in ((w, date.fromisoformat(iso)) for w, iso in days.items())
        )
        lines.append(f'- "지난 X요일" 정답(질문한 날 이전에 가장 최근에 지난 그 요일): {answers}')
    return lines


def _accuracy_row(name: str, g: dict[str, Any]) -> str:
    spread = "-" if g["run_min"] is None else f"{_pct(g['run_min'])}~{_pct(g['run_max'])}"
    return f"| {name} | {g['items']} | {g['scored']} | {g['correct']} | {_pct(g['accuracy'])} | {spread} |"


def _consistency_row(name: str, c: dict[str, Any]) -> str:
    return (
        f"| {name} | {c['items']} | {_pct(c['same_output'])} | {_pct(c['modal_share'])} | {_pct(c['all_correct'])} | "
        f"{_pct(c['majority_correct'])} | {c['flaky']} |"
    )


def _error_note(code: str, s: dict[str, Any]) -> str:
    if code == "wrong_args":
        return ", ".join(f"{k} {n}" for k, n in s["wrong_arg_keys"].items())
    if code == "missing_call" and s["answered_without_tools"]:
        return f"도구 없이 답함 {s['answered_without_tools']}"
    return ""


def _recount_lines(s: dict[str, Any]) -> list[str]:
    """정확도 표 아래의 다시 센 점수. 다시 세는 문항을 재지 않았거나 이 항목이 생기기 전에 저장한 결과에는 없다."""
    recount = s.get("recount")
    if recount is None:
        return []
    original = s["overall"]["correct"]
    added = ", ".join(f"`{key}` {n}" for key, n in recount["added"].items())
    return [
        "",
        f"- 다시 센 점수 {recount['correct']}: 원래 채점의 정답 {original} + 다시 센 실행 "
        f'{recount["correct"] - original} ({added}). 도구 선택 v1.1 의 "지난 X요일" 두 문항에서 그 요일 하루'
        "(v1.2 정답)로 부른 실행을 원래 채점과 같은 비교로 확인해 맞음으로 센다",
    ]


def _rescored_line(report: dict[str, Any]) -> list[str]:
    r = report.get("rescored")
    if r is None:
        return []
    return [
        f"- 다시 채점: {r['at']} 커밋 `{r['commit']}`, `{r['from']}`의 출력을 LLM 호출 없이 채점했다. "
        f"판정이 바뀐 실행 {r['changed']}회"
    ]


def to_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    llm = s["llm"]
    many = len(report["datasets"]) > 1
    scope = f"종류 {', '.join(report['categories'])}"
    if report["limit"]:
        scope += f" (앞 {report['limit']}문항)"
    # 기간 채점을 기록하기 전 결과는 인자를 쓴 그대로 비교했다
    scoring = SCORING_TEXT[report.get("period_scoring", "exact")]
    lines = [
        f"# 도구 선택 평가 ({report['measured_at']})",
        "",
        f"- 커밋 `{report['commit']}` · 모델 `{report['model'] or '-'}` · 호스트 `{report['base_url_host'] or '-'}`",
        f"- 프롬프트 {prompt_text(report)} · 도구 스키마 sha256 {report['tools_sha256']}",
        *(dataset_line(d) for d in report["datasets"]),
        f"- {scope} · 반복 {report['repeat']}회 · 동시성 {report['concurrency']} "
        f"(래퍼 한도 {report['max_concurrency'] or '-'}) · temperature {_temperature_text(report)}",
        _today_line(report),
        *_excluded_lines(report),
        f"- LLM 호출 {llm['calls']}회 · 실패 {llm['errors']} · 입력 토큰 {llm['input_tokens']:,} · "
        f"출력 토큰 {llm['output_tokens']:,} · 호출 시간 p50 {fmt(llm['total_p50'])} p95 {fmt(llm['total_p95'])} · "
        f"슬롯 대기 p95 {fmt(llm['wait_p95'])} · 전체 {report['elapsed_s']}초",
        "- guard 문항과 경계 확인에 걸린 문항은 LLM 을 부르지 않았다 (운영도 LLM 전에 끝난다)",
        "- 채점: 도구 호출은 (도구 이름, 정렬한 인자) 묶음이 정답·허용 답안 하나와 같으면 맞음(순서 무시). "
        f"{scoring}. "
        "선택 인자를 기본값으로 적은 호출은 적지 않은 호출과 같고, 수면 기록의 analysis·condition 은 정답이 기대값을 "
        "적었을 때만 비교. "
        "no_tool 은 도구 없이 답하면, clarify 는 도구 없이 시각·시간대를 되물으면(물음표 문장 기준) 맞음. "
        "guard 는 차단 사유가 같으면 맞음",
        *_rescored_line(report),
        "",
        "## 정확도",
        "",
        "| 종류 | 문항 | 채점 | 정답 | 정확도 | 회차별 최소~최대 |",
        "| --- | --- | --- | --- | --- | --- |",
        *(_accuracy_row(name, g) for name, g in s["by_category"].items()),
        _accuracy_row("전체 (proposed 제외)", s["overall"]),
        *_recount_lines(s),
    ]
    if many:
        lines += [
            "",
            "## 데이터셋별 (proposed 제외)",
            "",
            "| 데이터셋 | 문항 | 채점 | 정답 | 정확도 | 회차별 최소~최대 |",
            "| --- | --- | --- | --- | --- | --- |",
            *(_accuracy_row(name, g) for name, g in s["by_dataset"].items()),
        ]
    if s["overall"]["consistency"] is not None:
        lines += [
            "",
            "## 일관성",
            "",
            f"문항마다 {report['repeat']}회 반복한 출력(도구 이름·인자, 도구 없음·되물음, 경계 범주)을 비교한다. "
            "답변 문장은 비교하지 않는다.",
            "",
            "| 종류 | 문항 | 출력이 모두 같음 | 최빈 출력 비율 평균 | 매번 정답 | 과반 정답 | 맞고 틀림이 섞인 문항 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            *(_consistency_row(name, g["consistency"]) for name, g in s["by_category"].items() if name != "proposed"),
            _consistency_row("전체 (proposed 제외)", s["overall"]["consistency"]),
        ]
    lines += ["", "## 오답 분류", ""]
    if s["errors"]:
        lines += ["| 분류 | 건수 | 내역 |", "| --- | --- | --- |"]
        lines += [f"| {ERROR_LABELS[code]} | {n} | {_error_note(code, s)} |" for code, n in s["errors"].items()]
    else:
        lines.append("오답 없음")
    if s["denied_calls"]:
        denied = ", ".join(f"{reason} {n}" for reason, n in s["denied_calls"].items())
        lines += ["", f"- 도구 스키마가 거부할 호출(운영에서는 게이트웨이가 차단함): {denied}"]
    if llm["errors"]:
        lines += ["", f"- LLM 오류 {llm['errors']}건은 채점에서 뺐다"]
    lines += [
        "",
        "## 페르소나별 (proposed 제외)",
        "",
        "| 페르소나 | 문항 | 채점 | 정답 | 정확도 | 회차별 최소~최대 |",
        "| --- | --- | --- | --- | --- | --- |",
        *(_accuracy_row(name, g) for name, g in s["by_persona"].items()),
    ]
    if s["by_tool"]:
        lines += [
            "",
            "## 정답 도구별 (tool)",
            "",
            "| 정답 도구 | 문항 | 채점 | 정답 | 정확도 | 회차별 최소~최대 |",
            "| --- | --- | --- | --- | --- | --- |",
            *(_accuracy_row(name, g) for name, g in s["by_tool"].items()),
        ]
    failing = sorted(
        (st for st in s["items"] if st["scored"] and st["correct"] < st["scored"]),
        key=lambda st: (st["correct"] / st["scored"], st["key"]),
    )
    lines += ["", "## 틀린 문항", ""]
    if failing:
        lines += ["| 문항 | 종류 | 정답 | 질문 | 기대 | 가장 많은 오답 |", "| --- | --- | --- | --- | --- | --- |"]
        for st in failing:
            top = "-" if st["top_wrong"] is None else f"{_md(st['top_wrong'][0])} ({st['top_wrong'][1]}회)"
            name = st["key"] if many else st["id"]
            lines.append(
                f"| {name} | {st['category']} | {st['correct']}/{st['scored']} | {_md(st['question'])} | "
                f"{_md(st['expected'])} | {top} |"
            )
    else:
        lines.append("없음")
    if s["clarify_answers"]:
        lines += ["", "## 되묻기 답변 (사람 확인용)", ""]
        for a in s["clarify_answers"]:
            name = f"{a['dataset']}/{a['id']}" if many else a["id"]
            lines.append(f"- `{name}` {a['run']}회 · {a['verdict']} · {_md(a['text'], 200) or '(본문 없음)'}")
    if s["proposed"]:
        lines += [
            "",
            "## proposed 관찰 (채점 안 함)",
            "",
            "| 응답 유형 | 건수 |",
            "| --- | --- |",
            *(f"| {behavior} | {n} |" for behavior, n in s["proposed"].items()),
        ]
    return "\n".join(lines) + "\n"


def save(
    report: dict[str, Any], records: Sequence[dict[str, Any]], out_dir: Path, stamp: str, name: str = "tool_selection"
) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stamp}_{name}.json"
    md_path = out_dir / f"{stamp}_{name}.md"
    records_path = out_dir / f"{stamp}_{name}_records.jsonl"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    records_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return json_path, md_path, records_path


# ---------------------------------------------------------------------------
# 다시 채점
# ---------------------------------------------------------------------------
def _outcome_from_record(record: dict[str, Any]) -> Outcome:
    code = record["detail"].get("code") if record["error"] == "llm_error" else None
    return Outcome(
        blocked_by=record["blocked_by"],
        llm=record["llm"],
        calls=tuple(record["calls"]),
        invalid_calls=tuple(record.get("invalid_calls") or ()),
        text=record.get("text") or "",
        error_code=code,
    )


def _report_path(records_path: Path) -> Path:
    """문항별 기록과 같은 측정의 집계 json. <시각>_tool_selection_records.jsonl 이면 <시각>_tool_selection.json 이다."""
    stem = records_path.stem.removesuffix("_records")
    return records_path.with_name(f"{stem}.json")


def rescore(records_path: Path, scoring: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """저장한 문항별 기록의 출력을 지금 데이터셋과 채점 방식으로 다시 채점한다. LLM 을 호출하지 않는다.

    측정 조건은 같은 측정의 집계 json 에서 읽는다. 데이터셋 sha256 이 측정 때와 다르면 멈춘다.
    """
    report_path = _report_path(records_path)
    if not report_path.exists():
        raise SystemExit(f"같은 측정의 집계 json 이 없습니다: {report_path.name}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    paths = [EVAL_DIR.parent / d["path"] for d in report["datasets"]]
    if missing := [p.name for p in paths if not p.exists()]:
        raise SystemExit(f"데이터셋 파일이 없습니다: {missing}")
    all_items, infos = load_datasets(paths)
    if changed := [i["name"] for i, d in zip(infos, report["datasets"], strict=True) if i["sha256"] != d["sha256"]]:
        raise SystemExit(f"다른 시험지라 다시 채점하지 않습니다. 측정 때와 sha256 이 다른 데이터셋: {changed}")
    # 측정 때 뺀 문항은 기록이 없어, 지금 규칙으로 뺀 문항이 측정 결과와 다르면 다시 채점할 수 없다
    base = date.fromisoformat(report.get("today", DEFAULT_TODAY.isoformat()))
    day = items_for_day(select(all_items, report["categories"], report["limit"]), base)
    if sorted(day.excluded) != sorted(report.get("excluded") or {}):
        raise SystemExit(
            f"기준일 {base} 에 뺀 문항이 측정 때와 달라 다시 채점하지 않습니다. "
            f"측정 {sorted(report.get('excluded') or {})} · 지금 {sorted(day.excluded)}"
        )
    items = day.items
    by_key = {item.key: item for item in items}
    today = date.fromisoformat(report["today"]) if scoring == "range" else None
    lines = records_path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    changed_runs = 0
    for old in (json.loads(line) for line in lines if line.strip()):
        item = by_key[f"{old['dataset']}/{old['id']}"]
        out = _outcome_from_record(old)
        record = make_record(item, old["run"], out, score(item, out, today=today), old.get("request_id"))
        record.update({name: old.get(name) for name in TIMING_FIELDS})
        changed_runs += record["correct"] != old["correct"]
        records.append(record)
    rescored = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "commit": git_commit(),
        "from": _display_path(records_path),
        "changed": changed_runs,
    }
    summary = summarize(records, items, report["repeat"])
    fields = _day_fields(day, base)
    return {**report, **fields, "period_scoring": scoring, "summary": summary, "rescored": rescored}, records


# ---------------------------------------------------------------------------
# 명령줄
# ---------------------------------------------------------------------------
def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("1 이상이어야 한다")
    return value


def _temperature(text: str) -> float:
    value = float(text)
    if not 0 <= value <= 2:
        raise argparse.ArgumentTypeError("0~2 사이여야 한다")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="평가 데이터셋으로 도구 선택 정확도·일관성을 잰다 (실제 LLM 호출)")
    p.add_argument(
        "--dataset",
        nargs="+",
        type=Path,
        default=[DEFAULT_DATASET],
        help=f"평가 데이터셋 jsonl (여러 개 가능, 기본 {DEFAULT_DATASET.name})",
    )
    p.add_argument("--repeat", type=_positive, default=5, help="문항마다 반복 횟수 (기본 5)")
    p.add_argument(
        "--only",
        nargs="+",
        choices=CATEGORIES,
        help="채점할 종류. 생략하면 proposed 를 뺀 4종. proposed 는 채점하지 않고 행동만 센다",
    )
    p.add_argument("--limit", type=_positive, help="종류를 고른 뒤 앞에서부터 N문항만 (스모크용)")
    p.add_argument("--temperature", type=_temperature, help="생략하면 요청에 넣지 않아 서버 기본값을 쓴다")
    p.add_argument(
        "--today",
        type=date.fromisoformat,
        default=DEFAULT_TODAY,
        help="기준일 YYYY-MM-DD (기본 2026-10-22). 시스템 프롬프트의 날짜 자리와 기간 채점에 쓰고 결과에 기록. "
        "다른 날이면 2026-10-22 로 계산한 정답·허용 답안이 있는 문항은 실행하지 않는다. '지난 X요일'만 말한 문항은 "
        "그 답안을 빼고 그 기준일 이전에 가장 최근에 지난 X요일을 정답으로 넣는다",
    )
    p.add_argument("--concurrency", type=_positive, default=1, help="동시 호출 수 (기본 1). LLM_MAX_CONCURRENCY 이하")
    p.add_argument(
        "--prompt-version",
        default=DEFAULT_PROMPT_VERSION,
        help=f"시스템 프롬프트 버전 (agent/prompts/system_<버전>.md, 기본 {DEFAULT_PROMPT_VERSION})",
    )
    p.add_argument(
        "--scoring",
        choices=SCORINGS,
        default="range",
        help="기간 인자 비교 방식. range 는 기준일로 해석한 날짜 범위(기본), exact 는 쓴 그대로 (옛 채점)",
    )
    p.add_argument(
        "--rescore",
        type=Path,
        help="저장한 문항별 기록(*_tool_selection_records.jsonl)을 LLM 없이 --scoring 으로 다시 채점해 --out 에 저장",
    )
    p.add_argument("--save", action="store_true", help="결과를 --out 폴더에 json·md·문항별 jsonl 로 저장 (git 제외)")
    p.add_argument("--out", type=Path, default=RESULTS_DIR, help="결과 저장 폴더 (기본 eval/results/)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    if args.rescore is not None:
        report, records = rescore(args.rescore, args.scoring)
        print(to_markdown(report))
        stamp = args.rescore.stem.split("_tool_selection")[0]
        json_path, md_path, records_path = save(
            report, records, args.out, stamp, f"tool_selection_rescored_{args.scoring}"
        )
        print(f"저장: {json_path}\n요약: {md_path}\n문항별: {records_path}")
        return
    try:
        template = load_system_prompt(args.prompt_version)
    except FileNotFoundError:
        raise SystemExit(f"프롬프트 {args.prompt_version!r} 가 없습니다 (agent/prompts/system_<버전>.md)") from None
    all_items, datasets = load_datasets(args.dataset)
    items = select(all_items, args.only or DEFAULT_CATEGORIES, args.limit)
    if not items:
        raise SystemExit("고른 종류에 해당하는 문항이 없습니다")
    # run_eval 이 같은 방식으로 다시 고쳐 뺀 문항을 결과에 기록하므로 run_eval 에는 고치기 전 문항을 넘긴다
    kept = items_for_day(items, args.today).items
    if not kept:
        raise SystemExit(
            f"기준일 {args.today} 에 판정할 수 있는 문항이 없습니다. 고른 {len(items)}문항 모두 정답·허용 답안에 "
            f"데이터셋 기준일 {DEFAULT_TODAY} 로 계산한 값이 있습니다"
        )
    settings = None
    if needs_llm(kept):
        try:
            settings = get_settings()
        except ValidationError:
            raise SystemExit("LLM_API_KEY가 없습니다. LLM_API_KEY를 넣은 뒤 다시 실행하세요.") from None
        if args.concurrency > settings.llm_max_concurrency:
            raise SystemExit(
                f"--concurrency {args.concurrency} 는 래퍼 한도 LLM_MAX_CONCURRENCY={settings.llm_max_concurrency} 를 "
                "넘습니다. 값을 줄이거나 LLM_MAX_CONCURRENCY 를 올리세요 (서버 한도 3, 공유 서버라 사전 공지 후)."
            )
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    report, records = asyncio.run(run_eval(settings, args, items, datasets, template))
    print("\n" + to_markdown(report))
    if args.save:
        json_path, md_path, records_path = save(report, records, args.out, stamp)
        print(f"저장: {json_path}\n요약: {md_path}\n문항별: {records_path}")
    else:
        print("(저장하지 않음. 저장하려면 --save)")


if __name__ == "__main__":
    main()
