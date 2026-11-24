"""코칭 골든셋 문항을 읽고 형식을 검사한다.

형식은 eval/datasets/README.md 의 "golden/" 절을 따른다. 문항 형식은 v1(v1, v1.1 파일)과 v2(v2, v2.1 파일) 두 가지다.
v1 형식의 expected_facts 와 v2 형식의 grounding 은 같은 Fact 로, v1 형식의 must_not 과 v2 형식의 rubric.must_not 은
같은 금지 항목 목록으로 읽는다.
정의되지 않은 이름(guard 범주, 기준 코드, 상태 코드, 지표·기간 등)이나 빠진 칸이 있으면 파일 전체를 확인한 뒤
문항 ID 와 함께 모아 GoldenFormatError 를 발생시킨다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from coach_ai.agent.rules.guard import GuardCategory
from coach_ai.history.types import HistoryTurn
from coach_ai.tools.schemas import METRICS, PERIODS, Metric, Period

EVAL_DIR = Path(__file__).resolve().parents[1]
GOLDEN_DIR = EVAL_DIR / "datasets" / "golden"

Form = Literal["v1", "v2"]
Version = Literal["v1", "v1.1", "v2", "v2.1"]
VERSIONS: tuple[Version, ...] = get_args(Version)


@dataclass(frozen=True, slots=True)
class GoldenSet:
    """문항 파일 하나. form 은 문항 형식(v1 사실·데이터 상태, v2 코칭 품질·안전)이다.

    in_progress_today_only 면 오늘 진행 중 상태(in_progress)는 오늘을 묻는 문항(정답 기간에 today 가 있음)에만 붙고,
    오늘이 들어간 다른 기간은 금지 항목 in_progress_as_total 로만 확인한다 (README 상태 규칙).
    """

    form: Form
    file: str
    in_progress_today_only: bool


SETS: dict[Version, GoldenSet] = {
    "v1": GoldenSet("v1", "coaching_golden_v1.jsonl", in_progress_today_only=False),
    "v1.1": GoldenSet("v1", "coaching_golden_v1.1.jsonl", in_progress_today_only=True),
    "v2": GoldenSet("v2", "coaching_golden_v2.jsonl", in_progress_today_only=False),
    "v2.1": GoldenSet("v2", "coaching_golden_v2.1.jsonl", in_progress_today_only=True),
}
# 기본으로 실행하는 최신 버전. 이전 버전 파일은 그 버전으로 잰 기록과 비교할 때 쓴다
LATEST: tuple[Version, ...] = ("v1.1", "v2.1")

FactField = Literal[
    "value",
    "avg",
    "total",
    "days_with_data",
    "daily_min",
    "daily_max",
    "in_progress",
    "measured_min",
    "measured_max",
    "diff",
    "deep_minutes",
    "start_at",
    "end_at",
]
FACT_FIELDS: tuple[FactField, ...] = get_args(FactField)
Unit = Literal["steps", "bpm", "percent", "min", "days", "time_kst"]
UNITS: tuple[Unit, ...] = get_args(Unit)
StatusCode = Literal["missing_days", "unsynced", "in_progress", "no_data", "before_link", "slow_walk_excluded"]
STATUS_CODES: tuple[StatusCode, ...] = get_args(StatusCode)
MustNotCode = Literal[
    "invented_number",
    "diagnosis",
    "missing_as_zero",
    "in_progress_as_total",
    "prescription",
    "causal_claim",
    "unsafe_reassurance",
    "blame",
    "generic_only",
]
MUST_NOT_CODES: tuple[MustNotCode, ...] = get_args(MustNotCode)
# v1 의 must_not 은 README v1 표의 네 코드만 쓴다. 나머지는 v2 기준표(rubric.must_not)의 코드다
V1_MUST_NOT: tuple[MustNotCode, ...] = ("invented_number", "diagnosis", "missing_as_zero", "in_progress_as_total")
MustCode = Literal[
    "personalized",
    "small_action",
    "no_blame",
    "hedged_relation",
    "few_days_hedged",
    "medical_referral",
    "answers_normally",
]
MUST_CODES: tuple[MustCode, ...] = get_args(MustCode)
GuardMode = Literal["rule", "llm", "none"]
GUARD_MODES: tuple[GuardMode, ...] = get_args(GuardMode)
GUARD_CATEGORIES: tuple[GuardCategory, ...] = get_args(GuardCategory)
CATEGORIES: dict[Form, tuple[str, ...]] = {"v1": ("fact", "data_status"), "v2": ("coaching", "safety")}
Subtype = Literal[
    "goal_missed", "next_action", "relation", "trend", "encouragement", "guard_hit", "guard_miss", "benign"
]
# v2 종류별 subtype. 안전 문항의 subtype 은 expected_guard.mode 가 정해져 있다 (README v2 subtype 표)
SUBTYPES: dict[str, tuple[Subtype, ...]] = {
    "coaching": ("goal_missed", "next_action", "relation", "trend", "encouragement"),
    "safety": ("guard_hit", "guard_miss", "benign"),
}
SUBTYPE_GUARD_MODES: dict[Subtype, GuardMode] = {"guard_hit": "rule", "guard_miss": "llm", "benign": "none"}

METRIC_UNITS: dict[Metric, Unit] = {
    "steps": "steps",
    "heart_rate": "bpm",
    "oxygen_saturation": "percent",
    "sleep": "min",
    "exercise": "min",
}
# 칸마다 정해진 단위. 없는 칸은 지표의 단위(METRIC_UNITS)다
FIELD_UNITS: dict[FactField, Unit] = {
    "days_with_data": "days",
    "deep_minutes": "min",
    "start_at": "time_kst",
    "end_at": "time_kst",
}
# 날짜가 있어야 값의 날을 알 수 있는 칸. 하루 최저·최고와 측정 최저·최고는 값이 있을 때만 날짜가 있다
_DATED_FIELDS: tuple[FactField, ...] = ("value", "deep_minutes", "start_at", "end_at")
_DATED_IF_VALUE: tuple[FactField, ...] = ("daily_min", "daily_max", "measured_min", "measured_max")
_SLEEP_FIELDS: tuple[FactField, ...] = ("deep_minutes", "start_at", "end_at")
# 상태마다 있어야 하는 칸
_STATUS_NEEDS: dict[StatusCode, str | None] = {
    "missing_days": "dates",
    "unsynced": "dates",
    "slow_walk_excluded": "dates",
    "in_progress": "as_of",
    "before_link": "linked_from",
    "no_data": None,
}

_FACT_KEYS = {"metric", "period", "field", "value", "unit", "tolerance", "date", "baseline", "as_of", "requires"}
_STATUS_KEYS = {"metric", "status", "dates", "as_of", "linked_from", "requires"}
_ITEM_KEYS: dict[Form, set[str]] = {
    "v1": {
        "id",
        "persona",
        "source_id",
        "category",
        "question",
        "expected_tools",
        "expected_facts",
        "expected_status",
        "must_not",
        "note",
    },
    "v2": {
        "id",
        "persona",
        "category",
        "subtype",
        "question",
        "history",
        "expected_tools",
        "grounding",
        "expected_status",
        "rubric",
        "expected_guard",
        "requires",
        "note",
    },
}
_FACTS_KEY: dict[Form, str] = {"v1": "expected_facts", "v2": "grounding"}
_PERSONA = re.compile(r"synth-(?:\d{2}|edge)")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
# tool_selection_v1 의 proposed 와 같은 형식이다 (tool:get_exercise_sessions, value:oxygen_saturation.measured_min)
_REQUIRES = re.compile(r"(?:tool|value):[a-z_]+(?:\.[a-z_]+)?")


class GoldenFormatError(ValueError):
    """문항 파일 형식 오류. problems 는 "문항 ID: 내용" 목록이다."""

    def __init__(self, path: Path, problems: Sequence[str]) -> None:
        self.path = path
        self.problems = list(problems)
        lines = "\n".join(f"- {p}" for p in self.problems)
        super().__init__(f"{path.name} 형식 오류 {len(self.problems)}건\n{lines}")


@dataclass(frozen=True, slots=True)
class Fact:
    """답변에 나와야 할 값 하나 (v1 expected_facts, v2 grounding).

    value 는 원형 그대로라 반올림 전 값이고, 시각(time_kst)이면 "HH:MM" 이다. 기록이 없으면 None 이다.
    """

    metric: Metric
    period: Period
    field: FactField
    value: float | str | None
    unit: Unit
    tolerance: float
    date: dt.date | None = None
    baseline: Period | None = None
    as_of: dt.datetime | None = None
    requires: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Status:
    """답변이 밝혀야 할 데이터 상태 하나."""

    metric: Metric
    status: StatusCode
    dates: tuple[dt.date, ...] = ()
    as_of: dt.datetime | None = None
    linked_from: dt.date | None = None
    requires: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HistoryPair:
    question: str
    answer: str


@dataclass(frozen=True, slots=True)
class GuardExpectation:
    """v2 안전 문항의 기대 경계. mode 가 none 이면 category 가 None 이다 (막히면 안 되는 질문)."""

    category: GuardCategory | None
    mode: GuardMode


@dataclass(frozen=True, slots=True)
class GoldenItem:
    """골든셋 문항 하나. version 은 문항 파일의 버전이고, v1 형식은 must·subtype·history·expected_guard·requires 가
    비어 있다."""

    version: Version
    id: str
    persona: str
    category: str
    question: str
    facts: tuple[Fact, ...]
    statuses: tuple[Status, ...]
    must_not: tuple[MustNotCode, ...]
    must: tuple[MustCode, ...] = ()
    subtype: Subtype | None = None
    expected_tools: tuple[dict[str, Any], ...] = ()
    expected_guard: GuardExpectation | None = None
    history: tuple[HistoryPair, ...] = ()
    # 문항 전체에 필요한, 아직 없는 도구 (v2). 사실·상태 하나에만 필요하면 그 사실·상태의 requires 다
    requires: tuple[str, ...] = ()
    source_id: str | None = None
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.version}/{self.id}"

    @property
    def form(self) -> Form:
        return SETS[self.version].form


def definition_sha256(item: GoldenItem) -> str:
    """채점에 쓰는 문항 정의의 sha256 앞 12자리. 파일 버전·ID·메모와 참고용 칸(expected_tools, source_id)은 넣지 않아
    버전이 달라도 바뀌지 않은 문항은 값이 같다."""
    body = asdict(item)
    for name in ("version", "id", "note", "expected_tools", "source_id"):
        del body[name]
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def history_turns(item: GoldenItem, *, answered_on: dt.date | None = None) -> tuple[HistoryTurn, ...]:
    """이전 대화 쌍을 CoachContext.turns 에 넣을 HistoryTurn 으로 바꾼다.

    요청·메시지 ID 와 순서 번호는 저장된 대화가 없어 문항 ID 와 쌍 순서로 만든다. 순서 번호는 1부터 질문·답변이
    번갈아 오는 번호다 (대화 API 의 coaching_message.sequence_no 와 같은 방식).
    """
    turns = []
    for index, pair in enumerate(item.history, 1):
        base = f"{item.id}-history-{index}"
        turns.append(
            HistoryTurn(
                request_id=base,
                question_message_id=f"{base}-question",
                answer_message_id=f"{base}-answer",
                question_seq=2 * index - 1,
                answer_seq=2 * index,
                question=pair.question,
                answer=pair.answer,
                answered_on=answered_on,
            )
        )
    return tuple(turns)


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(EVAL_DIR.parent).as_posix()
    except ValueError:
        return path.name


class _Reader:
    """한 파일의 문항을 읽으며 형식 문제를 모은다."""

    def __init__(self, version: Version) -> None:
        self.version = version
        self.form = SETS[version].form
        self.problems: list[str] = []

    def problem(self, where: str, message: str) -> None:
        self.problems.append(f"{where}: {message}")

    def name(self, where: str, label: str, value: Any, allowed: Sequence[str]) -> Any:
        if value not in allowed:
            self.problem(where, f"{label} {value!r} 는 정의되지 않은 이름이다 (허용: {', '.join(allowed)})")
            return None
        return value

    def text(self, where: str, label: str, value: Any, *, empty: bool = False) -> str | None:
        if not isinstance(value, str) or (not empty and not value.strip()):
            self.problem(where, f"{label} 는 비어 있지 않은 문자열이어야 한다")
            return None
        return value

    def day(self, where: str, label: str, value: Any) -> dt.date | None:
        if not isinstance(value, str) or not _DATE.fullmatch(value):
            self.problem(where, f"{label} {value!r} 는 YYYY-MM-DD 날짜여야 한다")
            return None
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            self.problem(where, f"{label} {value!r} 는 없는 날짜다")
            return None

    def moment(self, where: str, label: str, value: Any) -> dt.datetime | None:
        try:
            parsed = dt.datetime.fromisoformat(value) if isinstance(value, str) else None
        except ValueError:
            parsed = None
        if parsed is None or parsed.tzinfo is None:
            self.problem(where, f"{label} {value!r} 는 시간대가 있는 ISO 8601 시각이어야 한다")
            return None
        return parsed

    def requires(self, where: str, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(v, str) and _REQUIRES.fullmatch(v) for v in value):
            self.problem(where, f"requires {value!r} 는 tool:… 또는 value:… 문자열 목록이어야 한다")
            return ()
        return tuple(value)

    def keys(self, where: str, label: str, row: Any, allowed: set[str], required: Sequence[str]) -> bool:
        if not isinstance(row, dict):
            self.problem(where, f"{label} 는 객체여야 한다")
            return False
        if extra := sorted(set(row) - allowed):
            self.problem(where, f"{label} 에 정의되지 않은 칸 {extra}")
        if missing := [k for k in required if k not in row]:
            self.problem(where, f"{label} 에 빠진 칸 {missing}")
            return False
        return True

    def fact(self, where: str, row: Any) -> Fact | None:
        label = _FACTS_KEY[self.form]
        if not self.keys(where, label, row, _FACT_KEYS, ("metric", "period", "field", "value", "unit", "tolerance")):
            return None
        metric = self.name(where, f"{label}.metric", row["metric"], METRICS)
        period = self.name(where, f"{label}.period", row["period"], PERIODS)
        field = self.name(where, f"{label}.field", row["field"], FACT_FIELDS)
        unit = self.name(where, f"{label}.unit", row["unit"], UNITS)
        tolerance = row["tolerance"]
        if isinstance(tolerance, bool) or not isinstance(tolerance, int | float) or tolerance < 0:
            self.problem(where, f"{label}.tolerance {tolerance!r} 는 0 이상의 수여야 한다")
            tolerance = None
        if metric and field and unit and unit != FIELD_UNITS.get(field, METRIC_UNITS[metric]):
            expected = FIELD_UNITS.get(field, METRIC_UNITS[metric])
            self.problem(where, f"{label} {metric}.{field} 의 단위는 {expected} 이어야 한다 ({unit})")
        if field in _SLEEP_FIELDS and metric and metric != "sleep":
            self.problem(where, f"{label}.field {field} 는 sleep 에만 쓴다 ({metric})")
        value = row["value"]
        if unit == "time_kst":
            if value is not None and not (isinstance(value, str) and _CLOCK.fullmatch(value)):
                self.problem(where, f"{label}.value {value!r} 는 HH:MM 시각이어야 한다")
        elif value is not None and (isinstance(value, bool) or not isinstance(value, int | float)):
            self.problem(where, f"{label}.value {value!r} 는 수여야 한다")
        date = None
        if "date" in row:
            date = self.day(where, f"{label}.date", row["date"])
        elif field in _DATED_FIELDS or (field in _DATED_IF_VALUE and value is not None):
            self.problem(where, f"{label} {field} 에 date 가 없다")
        baseline = None
        if field == "diff":
            baseline = self.name(where, f"{label}.baseline", row.get("baseline"), PERIODS)
        elif "baseline" in row:
            self.problem(where, f"{label}.baseline 은 diff 에만 쓴다")
        as_of = None
        if field == "in_progress":
            as_of = self.moment(where, f"{label}.as_of", row.get("as_of"))
        elif "as_of" in row:
            self.problem(where, f"{label}.as_of 는 in_progress 에만 쓴다")
        requires = self.requires(where, row.get("requires"))
        if None in (metric, period, field, unit, tolerance) or (field == "diff" and baseline is None):
            return None
        return Fact(metric, period, field, value, unit, float(tolerance), date, baseline, as_of, requires)

    def status(self, where: str, row: Any) -> Status | None:
        if not self.keys(where, "expected_status", row, _STATUS_KEYS, ("metric", "status")):
            return None
        metric = self.name(where, "expected_status.metric", row["metric"], METRICS)
        code = self.name(where, "expected_status.status", row["status"], STATUS_CODES)
        if code is None:
            return None
        need = _STATUS_NEEDS[code]
        for key in ("dates", "as_of", "linked_from"):
            if key != need and key in row:
                self.problem(where, f"expected_status.{key} 는 {code} 에 쓰지 않는다")
        if need is not None and need not in row:
            self.problem(where, f"expected_status {code} 에 {need} 가 없다")
            return None
        dates: tuple[dt.date, ...] = ()
        as_of = linked_from = None
        if need == "dates":
            raw = row["dates"]
            if not isinstance(raw, list) or not raw:
                self.problem(where, f"expected_status {code} 의 dates 는 날짜 목록이어야 한다")
                return None
            parsed = [self.day(where, "expected_status.dates", d) for d in raw]
            if None in parsed:
                return None
            dates = tuple(d for d in parsed if d is not None)
        elif need == "as_of":
            as_of = self.moment(where, "expected_status.as_of", row["as_of"])
        elif need == "linked_from":
            linked_from = self.day(where, "expected_status.linked_from", row["linked_from"])
        requires = self.requires(where, row.get("requires"))
        if metric is None or (need == "as_of" and as_of is None) or (need == "linked_from" and linked_from is None):
            return None
        return Status(metric, code, dates, as_of, linked_from, requires)

    def codes(self, where: str, label: str, value: Any, allowed: Sequence[str]) -> tuple[Any, ...]:
        if not isinstance(value, list) or not all(isinstance(code, str) for code in value):
            self.problem(where, f"{label} 는 문자열 목록이어야 한다")
            return ()
        known = tuple(self.name(where, label, code, allowed) for code in value)
        if dup := sorted(code for code, n in Counter(value).items() if n > 1):
            self.problem(where, f"{label} 에 같은 코드가 두 번 있다 {dup}")
        return tuple(code for code in known if code is not None)

    def tools(self, where: str, value: Any) -> tuple[dict[str, Any], ...]:
        ok = isinstance(value, list) and all(
            isinstance(t, dict)
            and set(t) == {"name", "args"}
            and isinstance(t["name"], str)
            and isinstance(t["args"], dict)
            for t in value
        )
        if not ok:
            self.problem(where, "expected_tools 는 {name, args} 목록이어야 한다")
            return ()
        return tuple(value)

    def history(self, where: str, value: Any) -> tuple[HistoryPair, ...]:
        if not isinstance(value, list):
            self.problem(where, "history 는 목록이어야 한다")
            return ()
        pairs = []
        for row in value:
            if not isinstance(row, dict) or set(row) != {"question", "answer"}:
                self.problem(where, "history 의 쌍은 {question, answer} 객체여야 한다")
                continue
            question = self.text(where, "history.question", row["question"])
            answer = self.text(where, "history.answer", row["answer"], empty=True)
            if question is not None and answer is not None:
                pairs.append(HistoryPair(question, answer))
        return tuple(pairs)

    def guard(self, where: str, value: Any, category: str | None, subtype: str | None) -> GuardExpectation | None:
        if value is None:
            if category == "safety":
                self.problem(where, "안전 문항에 expected_guard 가 없다")
            return None
        if not isinstance(value, dict) or set(value) != {"category", "mode"}:
            self.problem(where, "expected_guard 는 {category, mode} 객체여야 한다")
            return None
        mode = self.name(where, "expected_guard.mode", value["mode"], GUARD_MODES)
        guard_category = value["category"]
        if guard_category is not None:
            guard_category = self.name(where, "expected_guard.category", guard_category, GUARD_CATEGORIES)
        if mode is None or (value["category"] is not None and guard_category is None):
            return None
        if (mode == "none") != (guard_category is None):
            self.problem(where, f"expected_guard mode {mode} 와 category {guard_category!r} 가 맞지 않는다")
        if category == "coaching":
            self.problem(where, "코칭 문항의 expected_guard 는 null 이어야 한다")
        if subtype in SUBTYPE_GUARD_MODES and SUBTYPE_GUARD_MODES[subtype] != mode:
            self.problem(
                where, f"subtype {subtype} 의 expected_guard.mode 는 {SUBTYPE_GUARD_MODES[subtype]} 이어야 한다"
            )
        return GuardExpectation(guard_category, mode)

    def item(self, where: str, row: Any) -> GoldenItem | None:
        form = self.form
        if not self.keys(where, "문항", row, _ITEM_KEYS[form], sorted(_ITEM_KEYS[form])):
            return None
        item_id = self.text(where, "id", row["id"])
        persona = row["persona"]
        if not isinstance(persona, str) or not _PERSONA.fullmatch(persona):
            self.problem(where, f"persona {persona!r} 는 synth-01~synth-10, synth-edge 형식이어야 한다")
        category = self.name(where, "category", row["category"], CATEGORIES[form])
        question = self.text(where, "question", row["question"])
        tools = self.tools(where, row["expected_tools"])
        facts_raw = row[_FACTS_KEY[form]]
        if not isinstance(facts_raw, list):
            self.problem(where, f"{_FACTS_KEY[form]} 는 목록이어야 한다")
            facts_raw = []
        facts = [self.fact(where, f) for f in facts_raw]
        status_raw = row["expected_status"]
        if not isinstance(status_raw, list):
            self.problem(where, "expected_status 는 목록이어야 한다")
            status_raw = []
        statuses = [self.status(where, s) for s in status_raw]
        note = self.text(where, "note", row["note"], empty=True) or ""
        subtype = None
        must: tuple[Any, ...] = ()
        history: tuple[HistoryPair, ...] = ()
        guard = None
        requires: tuple[str, ...] = ()
        source_id = None
        if form == "v1":
            must_not = self.codes(where, "must_not", row["must_not"], V1_MUST_NOT)
            source_id = row["source_id"]
            if source_id is not None and not isinstance(source_id, str):
                self.problem(where, f"source_id {source_id!r} 는 문자열이나 null 이어야 한다")
        else:
            allowed = SUBTYPES.get(category, ()) if category else get_args(Subtype)
            subtype = self.name(where, "subtype", row["subtype"], allowed)
            rubric = row["rubric"]
            if self.keys(where, "rubric", rubric, {"must", "must_not"}, ("must", "must_not")):
                must = self.codes(where, "rubric.must", rubric["must"], MUST_CODES)
                must_not = self.codes(where, "rubric.must_not", rubric["must_not"], MUST_NOT_CODES)
            else:
                must_not = ()
            history = self.history(where, row["history"])
            guard = self.guard(where, row["expected_guard"], category, subtype)
            requires = self.requires(where, row["requires"])
        if None in (item_id, category, question) or None in facts or None in statuses:
            return None
        if not isinstance(persona, str) or not _PERSONA.fullmatch(persona):
            return None
        return GoldenItem(
            version=self.version,
            id=item_id,
            persona=persona,
            category=category,
            question=question,
            facts=tuple(f for f in facts if f is not None),
            statuses=tuple(s for s in statuses if s is not None),
            must_not=must_not,
            must=must,
            subtype=subtype,
            expected_tools=tools,
            expected_guard=guard,
            history=history,
            requires=requires,
            source_id=source_id,
            note=note,
        )


def load_set(version: Version, path: Path | None = None) -> tuple[list[GoldenItem], dict[str, Any]]:
    """문항 파일 하나를 읽어 (문항, 파일 정보)를 반환한다. 파일 정보는 이름·버전·경로·sha256·문항 수다.

    형식 문제가 하나라도 있으면 모든 문제를 모아 GoldenFormatError 를 발생시킨다.
    """
    path = path or GOLDEN_DIR / SETS[version].file
    raw = path.read_bytes()
    reader = _Reader(version)
    items: list[GoldenItem] = []
    lines = raw.decode("utf-8-sig").splitlines()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            reader.problem(f"{number}번째 줄", f"JSON 으로 읽을 수 없다 ({e.msg})")
            continue
        where = row.get("id") if isinstance(row, dict) and isinstance(row.get("id"), str) else f"{number}번째 줄"
        item = reader.item(where, row)
        if item is not None:
            items.append(item)
    if dup := sorted(i for i, n in Counter(item.id for item in items).items() if n > 1):
        reader.problem(path.name, f"문항 ID 가 겹친다 {dup}")
    if reader.problems:
        raise GoldenFormatError(path, reader.problems)
    info = {
        "name": path.stem,
        "version": version,
        "path": _display_path(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "rows": len(items),
    }
    return items, info


def load_sets(
    versions: Sequence[Version], paths: dict[Version, Path] | None = None
) -> tuple[list[GoldenItem], list[dict[str, Any]]]:
    """여러 버전의 문항을 파일 순서대로 읽는다. 버전이 달라도 문항 ID 가 겹치면 ValueError 를 발생시킨다."""
    items: list[GoldenItem] = []
    infos: list[dict[str, Any]] = []
    for version in versions:
        loaded, info = load_set(version, (paths or {}).get(version))
        items += loaded
        infos.append(info)
    if dup := sorted(i for i, n in Counter(item.id for item in items).items() if n > 1):
        raise ValueError(f"버전이 다른 문항 ID 가 겹친다 {dup}")
    return items, infos
