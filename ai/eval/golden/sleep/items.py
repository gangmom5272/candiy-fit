"""수면 코칭 골든셋 문항을 읽고 형식을 검사한다.

형식은 eval/datasets/README.md 의 "수면 코칭 골든셋 v1" 절을 따른다. 개선용과 확인용은 파일로 나뉘고, 확인용 문항에는
원 문항 id 를 가리키는 pair 가 더 있다. category 는 coaching·safety, acceptable 은 {"type", "tool_calls"} 객체의
목록이라 도구 선택 데이터셋을 읽는 함수(eval.tool_selection.load_datasets)와 따로 읽는다.
형식이 잘못된 문항은 문항 id 와 이유를 모으고 그 문항만 건너뛴다. 이유에는 칸 이름과 기대하는 형식만 쓰고 문항의 값은
쓰지 않아, 확인용 문항의 내용이 보고에 들어가지 않는다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from coach_ai.agent.rules.guard import GuardCategory
from coach_ai.history.types import HistoryTurn
from coach_ai.tools.schemas import TOOL_INPUTS
from eval.golden.items import EVAL_DIR, GOLDEN_DIR, HistoryPair

Side = Literal["dev", "holdout"]
# 버전 → 개선용·확인용 파일 이름
SETS: dict[str, dict[Side, str]] = {
    "v1": {"dev": "sleep_coaching_golden_v1.jsonl", "holdout": "sleep_coaching_golden_v1_holdout.jsonl"},
}
LATEST = "v1"
SIDES: tuple[Side, ...] = ("dev", "holdout")
SPLITS = ("dev", "holdout", "all")
SIDE_LABELS: dict[str, str] = {"dev": "개선용", "holdout": "확인용"}
CATEGORIES = ("coaching", "safety")
# 퀄리티 다섯 항목. quality_na 는 이 가운데 해당 없는 항목이다
QUALITY_ITEMS = ("질문에 맞음", "내 데이터 근거", "변화 설명", "구체적 조언", "어조")
ANSWER_TYPES = ("tool_calls", "no_tool", "guard")
GUARD_CATEGORIES: tuple[str, ...] = get_args(GuardCategory)
# 숫자 값의 단위. min 은 분, pct 는 %, nights 는 밤 수, point 는 점수다
NUMBER_UNITS = ("min", "pct", "steps", "nights", "point")
UNITS = (*NUMBER_UNITS, "clock", "date", "grade", "rule")
SCORE_TOOL = "get_lifestyle_score"

_REQUIRED = (
    "id",
    "question",
    "persona",
    "category",
    "subtype",
    "history",
    "expected",
    "acceptable",
    "follow_up_allowed",
    "values",
    "rubric_text",
    "quality_na",
    "safety",
    "note",
)
_OPTIONAL = ("source_no", "expected_answer")
_VALUE_REQUIRED = ("field", "value", "unit", "display", "tolerance")
_VALUE_OPTIONAL = ("accept", "source")
_PERSONA = re.compile(r"synth-(?:\d{2}|edge)")
_CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True, slots=True)
class Value:
    """values.must·may 의 숫자 하나. value 는 숫자, "HH:MM" 시각, "YYYY-MM-DD" 날짜, 글(grade, rule)이거나 그 목록이다.

    accept 는 같은 값으로 인정하는 표기이고, source 는 그 값이 나오는 분석(그 분석을 불렀을 때만 기대)이다.
    """

    field: str
    value: Any
    unit: str
    display: str
    tolerance: float
    accept: tuple[str, ...] = ()
    source: str | None = None


@dataclass(frozen=True, slots=True)
class SleepItem:
    """수면 코칭 골든셋 문항 하나. expected·acceptable·follow_up_allowed·safety 는 파일의 값 그대로다."""

    version: str
    side: Side
    id: str
    persona: str
    category: str
    subtype: str
    question: str
    history: tuple[HistoryPair, ...]
    expected: dict[str, Any]
    acceptable: tuple[dict[str, Any], ...]
    follow_up_allowed: tuple[dict[str, Any], ...]
    must: tuple[Value, ...]
    may: tuple[Value, ...]
    rubric_must: tuple[str, ...]
    rubric_must_not: tuple[str, ...]
    quality_na: tuple[str, ...]
    safety: dict[str, Any] | None
    note: str
    # 확인용 문항이 짝으로 둔 개선용 문항 id
    pair: str | None = None
    source_no: int | None = None
    expected_answer: str | None = None

    @property
    def key(self) -> str:
        return f"{self.version}/{self.id}"

    @property
    def expected_calls(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.expected.get("tool_calls", ()))


@dataclass(frozen=True, slots=True)
class Skipped:
    """형식이 잘못돼 건너뛴 문항. id 를 읽을 수 없으면 "N번째 줄"이다."""

    id: str
    side: Side
    reasons: tuple[str, ...]


def path_of(version: str, side: Side) -> Path:
    return GOLDEN_DIR / SETS[version][side]


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(EVAL_DIR.parent).as_posix()
    except ValueError:
        return path.name


def needs_scores(item: SleepItem) -> bool:
    """생활 습관 점수 행이 있어야 잴 수 있는 문항. 정답 첫 호출이 점수 도구이거나 must 에 규칙 값이 아닌 점수가 있다."""
    if any(call.get("name") == SCORE_TOOL for call in item.expected_calls):
        return True
    return any(v.unit == "point" and not v.field.startswith("rule.") for v in item.must)


def history_turns(item: SleepItem) -> tuple[HistoryTurn, ...]:
    """history 쌍을 CoachContext.turns 에 넣을 HistoryTurn 으로 바꾼다. no 칸은 넣지 않는다.

    요청·메시지 ID 와 순서 번호는 저장된 대화가 없어 문항 ID 와 쌍 순서로 만든다 (eval.golden.items.history_turns 와
    같은 방식).
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
            )
        )
    return tuple(turns)


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_date(text: str) -> bool:
    if not _DATE.fullmatch(text):
        return False
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return False
    return True


class _Reader:
    """문항 하나의 형식 문제를 모은다."""

    def __init__(self, side: Side, dev_ids: Collection[str] | None) -> None:
        self.side = side
        self.dev_ids = dev_ids
        self.problems: list[str] = []

    def problem(self, message: str) -> None:
        self.problems.append(message)

    def keys(self, label: str, row: Any, required: Collection[str], optional: Collection[str] = ()) -> bool:
        if not isinstance(row, dict):
            self.problem(f"{label} 는 객체여야 한다")
            return False
        if extra := sorted(set(row) - set(required) - set(optional)):
            self.problem(f"{label} 에 정의되지 않은 칸 {extra}")
        if missing := [k for k in required if k not in row]:
            self.problem(f"{label} 에 빠진 칸 {missing}")
            return False
        return True

    def call(self, label: str, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or set(value) != {"name", "args"} or not isinstance(value["args"], dict):
            self.problem(f"{label} 는 {{name, args}} 객체여야 한다")
            return None
        if value["name"] not in TOOL_INPUTS:
            self.problem(f"{label}.name 은 지금 도구 이름이어야 한다")
            return None
        return value

    def calls(self, label: str, value: Any, *, empty: bool) -> list[dict[str, Any]] | None:
        if not isinstance(value, list) or (not empty and not value):
            self.problem(f"{label} 는 {'' if empty else '비어 있지 않은 '}호출 목록이어야 한다")
            return None
        parsed = [self.call(f"{label}[{i}]", c) for i, c in enumerate(value)]
        return None if None in parsed else [c for c in parsed if c is not None]

    def answer(self, label: str, value: Any, allowed: Collection[str]) -> dict[str, Any] | None:
        if not isinstance(value, dict) or value.get("type") not in allowed:
            self.problem(f"{label}.type 은 {', '.join(allowed)} 중 하나여야 한다")
            return None
        kind = value["type"]
        if kind == "tool_calls":
            if not self.keys(label, value, ("type", "tool_calls")):
                return None
            if self.calls(f"{label}.tool_calls", value["tool_calls"], empty=False) is None:
                return None
        elif kind == "guard":
            if not self.keys(label, value, ("type", "guard_category")):
                return None
            if value["guard_category"] not in GUARD_CATEGORIES:
                self.problem(f"{label}.guard_category 는 {', '.join(GUARD_CATEGORIES)} 중 하나여야 한다")
                return None
        elif not self.keys(label, value, ("type",)):
            return None
        return value

    def value(self, label: str, row: Any) -> Value | None:
        if not self.keys(label, row, _VALUE_REQUIRED, _VALUE_OPTIONAL):
            return None
        ok = True
        unit = row["unit"]
        if unit not in UNITS:
            self.problem(f"{label}.unit 은 {', '.join(UNITS)} 중 하나여야 한다")
            ok = False
        else:
            ok = self._value_matches_unit(label, unit, row["value"])
        if not _text(row["field"]) or not _text(row["display"]):
            self.problem(f"{label}.field·display 는 비어 있지 않은 문자열이어야 한다")
            ok = False
        tolerance = row["tolerance"]
        if not _number(tolerance) or tolerance < 0:
            self.problem(f"{label}.tolerance 는 0 이상의 수여야 한다")
            ok = False
        accept = row.get("accept", [])
        if not isinstance(accept, list) or not all(_text(a) for a in accept):
            self.problem(f"{label}.accept 는 비어 있지 않은 문자열 목록이어야 한다")
            ok = False
        source = row.get("source")
        if source is not None and not _text(source):
            self.problem(f"{label}.source 는 문자열이나 null 이어야 한다")
            ok = False
        if not ok:
            return None
        return Value(row["field"], row["value"], unit, row["display"], float(tolerance), tuple(accept), source)

    def _value_matches_unit(self, label: str, unit: str, value: Any) -> bool:
        items = value if isinstance(value, list) and value else [value]
        if unit in NUMBER_UNITS:
            ok, expected = all(_number(v) for v in items), "수이거나 수 목록이어야 한다"
        elif unit == "clock":
            ok = all(isinstance(v, str) and _CLOCK.fullmatch(v) for v in items)
            expected = "HH:MM 시각이거나 그 목록이어야 한다"
        elif unit == "date":
            ok, expected = isinstance(value, str) and _valid_date(value), "YYYY-MM-DD 날짜여야 한다"
        else:
            ok, expected = _text(value), "비어 있지 않은 문자열이어야 한다"
        if not ok:
            self.problem(f"{label}.value 는 {expected}")
        return bool(ok)

    def values(self, value: Any) -> tuple[tuple[Value, ...], tuple[Value, ...]] | None:
        if not self.keys("values", value, ("must", "may")):
            return None
        out = []
        for side in ("must", "may"):
            rows = value[side]
            if not isinstance(rows, list):
                self.problem(f"values.{side} 는 목록이어야 한다")
                return None
            parsed = [self.value(f"values.{side}[{i}]", r) for i, r in enumerate(rows)]
            if None in parsed:
                return None
            out.append(tuple(v for v in parsed if v is not None))
        return out[0], out[1]

    def texts(self, label: str, value: Any) -> tuple[str, ...] | None:
        if not isinstance(value, list) or not all(_text(v) for v in value):
            self.problem(f"{label} 는 비어 있지 않은 문자열 목록이어야 한다")
            return None
        return tuple(value)

    def history(self, value: Any) -> tuple[HistoryPair, ...] | None:
        if not isinstance(value, list):
            self.problem("history 는 목록이어야 한다")
            return None
        pairs = []
        for i, row in enumerate(value):
            label = f"history[{i}]"
            if not self.keys(label, row, ("question", "answer"), ("no",)):
                return None
            if not _text(row["question"]) or not isinstance(row["answer"], str):
                self.problem(f"{label} 의 question 은 비어 있지 않은 문자열, answer 는 문자열이어야 한다")
                return None
            no = row.get("no")
            if no is not None and (not isinstance(no, int) or isinstance(no, bool)):
                self.problem(f"{label}.no 는 정수나 null 이어야 한다")
                return None
            pairs.append(HistoryPair(row["question"], row["answer"]))
        return tuple(pairs)

    def safety(self, value: Any, category: Any) -> dict[str, Any] | None:
        """safety 칸. 바뀐 뒤 동작(wanted)이 없으면 비교하지 않으므로 current 만 있어도 된다."""
        if value is None:
            if category == "safety":
                self.problem("안전 문항(category safety)의 safety 는 객체여야 한다")
            return None
        if not isinstance(value, dict) or not isinstance(value.get("current"), dict):
            self.problem("safety 는 current 객체가 있는 객체여야 한다")
            return None
        wanted = value.get("wanted")
        if wanted is None:
            return value
        if not isinstance(wanted, dict):
            self.problem("safety.wanted 는 객체여야 한다")
            return None
        if wanted.get("guard") is not None and wanted["guard"] not in GUARD_CATEGORIES:
            self.problem(f"safety.wanted.guard 는 {', '.join(GUARD_CATEGORIES)} 중 하나나 null 이어야 한다")
        if "answer" in wanted and not _text(wanted["answer"]):
            self.problem("safety.wanted.answer 는 비어 있지 않은 문자열이어야 한다")
        revert = wanted.get("revert")
        if revert is not None and (not isinstance(revert, dict) or not _text(revert.get("answer"))):
            self.problem("safety.wanted.revert 는 answer 가 있는 객체여야 한다")
        return value

    def pair(self, value: Any) -> None:
        if self.side == "dev":
            return
        if not _text(value):
            self.problem("pair 는 개선용 문항 id 여야 한다")
        elif self.dev_ids is not None and value not in self.dev_ids:
            self.problem("pair 가 개선용 파일에 없는 id 를 가리킨다")

    def item(self, row: Any, version: str) -> SleepItem | None:
        required = (*_REQUIRED, "pair") if self.side == "holdout" else _REQUIRED
        if not self.keys("문항", row, required, _OPTIONAL):
            return None
        for name in ("id", "question", "subtype"):
            if not _text(row[name]):
                self.problem(f"{name} 는 비어 있지 않은 문자열이어야 한다")
        if not isinstance(row["persona"], str) or not _PERSONA.fullmatch(row["persona"]):
            self.problem("persona 는 synth-NN, synth-edge 형식이어야 한다")
        if row["category"] not in CATEGORIES:
            self.problem(f"category 는 {', '.join(CATEGORIES)} 중 하나여야 한다")
        if not isinstance(row["note"], str):
            self.problem("note 는 문자열이어야 한다")
        source_no = row.get("source_no")
        if source_no is not None and (not isinstance(source_no, int) or isinstance(source_no, bool)):
            self.problem("source_no 는 정수나 null 이어야 한다")
        expected_answer = row.get("expected_answer")
        if expected_answer is not None and not isinstance(expected_answer, str):
            self.problem("expected_answer 는 문자열이어야 한다")
        self.pair(row.get("pair"))
        expected = self.answer("expected", row["expected"], ANSWER_TYPES)
        acceptable = []
        if isinstance(row["acceptable"], list):
            acceptable = [
                self.answer(f"acceptable[{i}]", a, ("tool_calls", "no_tool")) for i, a in enumerate(row["acceptable"])
            ]
        else:
            self.problem("acceptable 은 {type, tool_calls} 객체의 목록이어야 한다")
        if expected is not None and expected["type"] == "guard" and row["acceptable"]:
            self.problem("expected 가 guard 인 문항은 acceptable 을 쓰지 않는다")
        follow_up = self.calls("follow_up_allowed", row["follow_up_allowed"], empty=True)
        values = self.values(row["values"])
        rubric = None
        if self.keys("rubric_text", row["rubric_text"], ("must", "must_not")):
            must = self.texts("rubric_text.must", row["rubric_text"]["must"])
            must_not = self.texts("rubric_text.must_not", row["rubric_text"]["must_not"])
            rubric = None if must is None or must_not is None else (must, must_not)
        quality_na = self.texts("quality_na", row["quality_na"])
        if quality_na is not None and any(q not in QUALITY_ITEMS for q in quality_na):
            self.problem(f"quality_na 는 {', '.join(QUALITY_ITEMS)} 중에서 고른다")
        if quality_na is not None and len(set(quality_na)) != len(quality_na):
            self.problem("quality_na 에 같은 항목이 두 번 있다")
        history = self.history(row["history"])
        safety = self.safety(row["safety"], row["category"])
        if self.problems or expected is None or values is None or rubric is None:
            return None
        return SleepItem(
            version=version,
            side=self.side,
            id=row["id"],
            persona=row["persona"],
            category=row["category"],
            subtype=row["subtype"],
            question=row["question"],
            history=history or (),
            expected=expected,
            acceptable=tuple(a for a in acceptable if a is not None),
            follow_up_allowed=tuple(follow_up or ()),
            must=values[0],
            may=values[1],
            rubric_must=rubric[0],
            rubric_must_not=rubric[1],
            quality_na=quality_na or (),
            safety=safety,
            note=row["note"],
            pair=row["pair"] if self.side == "holdout" else None,
            source_no=source_no,
            expected_answer=expected_answer,
        )


def parse_item(
    row: Any, version: str, side: Side, *, dev_ids: Collection[str] | None = None
) -> tuple[SleepItem | None, list[str]]:
    """문항 한 줄을 읽는다. (문항, 형식 문제)를 반환하고 문제가 있으면 문항은 None 이다.

    확인용(holdout) 문항은 pair 가 있어야 하고, dev_ids 를 넘기면 pair 가 그 안에 있어야 한다.
    """
    reader = _Reader(side, dev_ids)
    item = reader.item(row, version)
    return item, reader.problems


def read_items(
    path: Path, version: str, side: Side, *, dev_ids: Collection[str] | None = None
) -> tuple[list[SleepItem], list[Skipped], dict[str, Any]]:
    """문항 파일 하나를 읽어 (문항, 건너뛴 문항, 파일 정보)를 반환한다.

    파일 정보는 이름·버전·개선용·확인용·경로·sha256·줄 수(rows)·읽은 문항 수(items)·건너뛴 문항 수(skipped)다.
    id 가 앞 문항과 겹치는 줄은 뒤 줄을 건너뛴다.
    """
    raw = path.read_bytes()
    items: list[SleepItem] = []
    skipped: list[Skipped] = []
    seen: set[str] = set()
    rows = 0
    for number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        rows += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            skipped.append(Skipped(f"{number}번째 줄", side, ("JSON 으로 읽을 수 없다",)))
            continue
        raw_id = row.get("id") if isinstance(row, dict) else None
        where = raw_id if _text(raw_id) else f"{number}번째 줄"
        if where in seen:
            skipped.append(Skipped(where, side, ("id 가 앞 문항과 겹친다",)))
            continue
        seen.add(where)
        item, problems = parse_item(row, version, side, dev_ids=dev_ids)
        if item is None:
            skipped.append(Skipped(where, side, tuple(problems)))
        else:
            items.append(item)
    info = {
        "name": path.stem,
        "version": version,
        "side": side,
        "path": display_path(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "rows": rows,
        "items": len(items),
        "skipped": len(skipped),
    }
    return items, skipped, info


def row_ids(path: Path) -> list[str]:
    """파일 순서대로 읽을 수 있는 문항 id. 등록(split.json)에 쓴다. 형식 검사는 하지 않는다."""
    ids = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and _text(row.get("id")):
            ids.append(row["id"])
    return ids
