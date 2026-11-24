"""골든셋 정답 검사. LLM 없이 고정 데이터만으로 정답을 다시 계산해 문항의 정답과 대조한다. 모델 답변은 쓰지 않는다.

- 질문 읽기: 질문 문장의 지표·기간 표현(phrases.QUESTION_*)으로 문항이 묻는 지표·기간을 정해 사실의 지표·기간과
  비교한다. 질문만으로 정할 수 없는 문항과 두 가지로 읽히는 문항을 따로 모은다.
- 사실: 사실의 지표·기간으로 메모리 어댑터의 공통 원형을 조회해 칸 값을 뽑고, 처리 함수가 LLM 에 전달하는 값도 구한다.
  정답, 원형 값, 도구 결과 값을 표시 자리수로 ROUND_HALF_UP 반올림해 비교한다.
- 상태: 사실의 원형 요약으로 데이터 상태를 다시 계산해 expected_status 와 비교한다 (README 상태 규칙).
  v1.1·v2.1 은 오늘 진행 중 상태를 오늘을 묻는 문항(정답 기간에 today 가 있음)에만 붙인다.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from collections.abc import Awaitable, Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from coach_ai.agent.executor import ToolRequest
from coach_ai.tools.handlers import compare_periods, get_daily_trend, get_metric_summary, get_sleep_records
from coach_ai.tools.periods import DEFAULT_TZ, resolve_period
from coach_ai.tools.schemas import ComparePeriods, GetDailyTrend, GetMetricSummary, GetSleepRecords
from eval.golden import phrases
from eval.golden.fixtures import Fixture
from eval.golden.items import SETS, Fact, GoldenItem
from eval.golden.scoring import DISPLAY_PLACES, compact, display_value

_KST = ZoneInfo(DEFAULT_TZ)
_WEEKDAYS = "월화수목금토일"
_SLEEP_METRICS = ("sleep", "oxygen_saturation")
_SLEEP_FIELDS = ("deep_minutes", "start_at", "end_at")
# 원형 요약으로 알 수 없는 상태 (데이터 쪽이 문항마다 따로 붙인다)
_UNDERIVED_STATUSES = ("slow_walk_excluded",)

REASON_LABELS = {
    "value_mismatch": "고정 데이터로 다시 계산한 값과 다름",
    "tool_missing": "도구 결과에 없는 값",
    "tool_mismatch": "도구 결과 값과 다름",
    "status_mismatch": "다시 계산한 데이터 상태와 다름",
    "period_mismatch": "질문 기간과 정답 기간이 다름",
    "metric_mismatch": "질문 지표와 정답 지표가 다름",
    "date_mismatch": "질문 날짜와 정답 날짜가 다름",
}
UNDETERMINED_LABELS = {"period": "기간", "metric": "지표"}
READING_RULES = (
    '지표는 질문의 지표 표현(phrases.QUESTION_METRICS)으로 정한다. "잘 때 산소포화도"의 "잘"은 수면으로 읽지 않는다.',
    "기간은 기간 표현(phrases.QUESTION_PERIODS)으로 정한다. 수면·산소포화도의 어젯밤은 오늘, 그저께 밤은 어제다. "
    '"지난 일요일"처럼 요일을 말하면 기준일로 날짜를 정하고, 수면의 "금요일 밤"은 깬 날인 토요일이다.',
    '"지난주보다 늘었어?"처럼 비교 기준만 말하면 지금 기간(이번 주·이번 달)과 비교하는 것으로 읽는다.',
    '"요즘·요새·최근"은 기간을 정하지 않은 표현이라 도구 선택 평가 관례대로 지난 7일로 읽는다. 정답 기간이 다르면 '
    "두 가지로 읽히는 문항이다.",
    '수면 질문의 "어제"(어제 깬 수면과 어젯밤 수면), "주말"(이번 주말과 지난 주말), 이번 주에 있는 요일의 '
    '"지난 X요일"은 두 가지로 읽힌다.',
    "지표나 기간 표현이 없으면 질문만으로 정할 수 없는 문항이다. 사실이 없는 문항은 검사하지 않는다.",
    "정답 고칠 후보는 정답 검사에서 걸린 문항과 질문이 두 가지로 읽히는 문항이다. "
    "모델 답변의 실패는 후보에 넣지 않는다.",
)


@dataclasses.dataclass(frozen=True, slots=True)
class Reading:
    """질문 문장이 가리키는 지표·기간.

    periods 는 질문에 쓴 기간, implicit 은 비교 기준만 말해 정한 지금 기간, conventions 는 "요즘"처럼 기간을 정하지 않은
    표현을 관례로 읽은 기간이다. dates 는 요일·주말 표현이 가리키는 날짜다.
    """

    metrics: tuple[str, ...]
    periods: tuple[str, ...]
    dates: tuple[dt.date, ...] = ()
    implicit: tuple[str, ...] = ()
    conventions: tuple[str, ...] = ()
    ambiguous: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "metrics": list(self.metrics),
            "periods": list(self.periods),
            "dates": [d.isoformat() for d in self.dates],
            "implicit": list(self.implicit),
            "conventions": list(self.conventions),
            "ambiguous": list(self.ambiguous),
            "notes": list(self.notes),
        }


def _unique(values: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(dict.fromkeys(values))


def _weekday_date(today: dt.date, weekday: int, *, last: bool) -> tuple[dt.date, bool]:
    """요일의 날짜와 두 가지로 읽히는지. "지난 X요일"은 오늘 전 가장 가까운 그 요일이고, 그날이 이번 주면 지난주의
    그 요일로도 읽힌다. "X요일"은 이번 주의 그 요일이 오늘 이후면 지난주의 그 요일이다.
    """
    this_week = today - dt.timedelta(days=today.weekday() - weekday)
    if last:
        day = this_week if this_week < today else this_week - dt.timedelta(days=7)
        return day, day >= today - dt.timedelta(days=today.weekday())
    return (this_week if this_week <= today else this_week - dt.timedelta(days=7)), False


def read_question(question: str, today: dt.date) -> Reading:
    """질문 문장에서 지표와 기간을 읽는다. 규칙은 READING_RULES 다."""
    text = compact(question)
    metrics = tuple(m for m, pattern in phrases.QUESTION_METRICS.items() if re.search(pattern, text))
    sleepy = bool(set(metrics) & set(_SLEEP_METRICS))
    rest = text
    # (질문에서의 위치, 기간). 질문에 쓴 순서대로 기간을 둔다
    found_periods: list[tuple[int, str]] = []
    dates: list[dt.date] = []
    ambiguous: list[str] = []
    notes: list[str] = []

    def take(pattern: str) -> list[re.Match[str]]:
        nonlocal rest
        found = list(re.finditer(pattern, rest))
        for m in found:
            rest = rest[: m.start()] + " " * (m.end() - m.start()) + rest[m.end() :]
        return found

    for m in take(phrases.QUESTION_NIGHT_BEFORE):
        if sleepy:
            found_periods.append((m.start(), "yesterday"))
            notes.append("그저께 밤 수면은 어제 깬 수면이다")
        else:
            notes.append("그저께는 기간 목록에 없다")
    if take(phrases.QUESTION_DAY_BEFORE):
        notes.append("그저께는 기간 목록에 없다")
    for m in take(phrases.QUESTION_LAST_NIGHT):
        found_periods.append((m.start(), "today" if sleepy else "yesterday"))
        if sleepy:
            notes.append("어젯밤 수면은 오늘 깬 수면이다")
    for m in take(phrases.QUESTION_YESTERDAY):
        if sleepy and re.search(phrases.QUESTION_SLEEP_VERB, text):
            found_periods += [(m.start(), "yesterday"), (m.start(), "today")]
            ambiguous.append("어제: 어제 깬 수면(yesterday)과 어젯밤 수면(today) 두 가지로 읽힌다")
        else:
            found_periods.append((m.start(), "yesterday"))
    if take(phrases.QUESTION_LAST_WEEKEND):
        monday = today - dt.timedelta(days=today.weekday())
        dates += [monday - dt.timedelta(days=2), monday - dt.timedelta(days=1)]
    for m in take(phrases.QUESTION_WEEKDAY):
        day, both = _weekday_date(today, _WEEKDAYS.index(m.group(2)), last=bool(m.group(1)))
        if m.group(3) and sleepy:
            day += dt.timedelta(days=1)
            notes.append(f"{m.group(2)}요일 밤 수면은 다음 날 깬 수면이다")
        dates.append(day)
        if both:
            ambiguous.append(f"지난 {m.group(2)}요일: 이번 주와 지난주의 {m.group(2)}요일 두 가지로 읽힌다")
    for pattern, period in phrases.QUESTION_PERIODS:
        found_periods += [(m.start(), period) for m in take(pattern)]
    if take(phrases.QUESTION_WEEKEND):
        ambiguous.append("주말: 이번 주말과 지난 주말 두 가지로 읽힌다")
    conventions = ["last_7_days"] if take(phrases.QUESTION_RECENT) else []
    periods = [period for _, period in sorted(found_periods, key=lambda found: found[0])]
    implicit = []
    if re.search(phrases.QUESTION_COMPARE, text):
        for base, current in (("last_week", "this_week"), ("last_month", "this_month")):
            if base in periods and current not in periods:
                implicit.append(current)
    return Reading(
        metrics=metrics,
        periods=_unique(periods),
        dates=_unique(dates),
        implicit=tuple(implicit),
        conventions=tuple(conventions),
        ambiguous=tuple(ambiguous),
        notes=_unique(notes),
    )


def _shown(unit: str, value: Any) -> Decimal | None:
    """값을 표시 자리수로 반올림한다. 시각은 자정부터의 분이다."""
    if value is None:
        return None
    if unit == "time_kst":
        hour, minute = str(value).split(":")
        return Decimal(int(hour) * 60 + int(minute))
    return Decimal(str(value)).quantize(DISPLAY_PLACES[unit], ROUND_HALF_UP)


def _text(unit: str, value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{int(value) // 60:02d}:{int(value) % 60:02d}" if unit == "time_kst" else f"{value:f}"


def _kst_clock(iso: str) -> str:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_KST).strftime("%H:%M")


def prototype_value(proto: dict[str, Any], fact: Fact, base: dict[str, Any] | None) -> tuple[Any, str | None]:
    """원형에서 사실 칸의 값과 그 날짜를 뽑는다. 데이터 쪽 정답 만들기(golden_build.extract)와 같은 칸을 쓴다."""
    s = proto["summary"]
    day = (fact.date or dt.date.fromisoformat(proto["period"]["start"])).isoformat()
    field = fact.field
    if field in ("avg", "total", "days_with_data"):
        return s[field], None
    if field in ("daily_min", "daily_max"):
        point = s[field]
        return (None, None) if point is None else (point["value"], point["date"])
    if field == "value":
        row = next((d for d in proto["days"] if d["date"] == day), None)
        return (None if row is None else row["value"]), day
    if field == "in_progress":
        progress = proto["in_progress"]
        return (None if progress is None else progress["value"]), None
    if field in ("measured_min", "measured_max"):
        point = proto["detail"][field]
        return (None, None) if point is None else (point["value"], point["date"])
    if field == "diff":
        current, baseline = s["avg"], (base or {}).get("summary", {}).get("avg")
        return (None if current is None or baseline is None else round(current - baseline, 2)), None
    night = next((n for n in proto["detail"]["nights"] if n["date"] == day), None)
    if night is None:
        return None, day
    return (night["deep_minutes"] if field == "deep_minutes" else _kst_clock(night[field])), day


async def tool_value(fact: Fact, request: ToolRequest) -> tuple[bool, Any, str | None]:
    """처리 함수가 LLM 에 전달하는 결과에서 사실 칸의 값을 찾는다. (결과에 그 칸이 있는지, 값, 날짜)를 반환한다."""
    metric, period = fact.metric, fact.period
    day = None if fact.date is None else fact.date.isoformat()
    if fact.field == "diff":
        out = await compare_periods(ComparePeriods(metric=metric, current=period, baseline=fact.baseline), request)
        diff = out.payload.get("difference")
        return True, None if diff is None else diff["value"], None
    if fact.field in _SLEEP_FIELDS:
        out = await get_sleep_records(GetSleepRecords(period=period), request)
        night = next((n for n in out.payload.get("nights", []) if n["date"] == day), None)
        if night is None:
            return True, None, day
        keys = {"deep_minutes": "deep_min", "start_at": "bedtime", "end_at": "wake"}
        return True, night[keys[fact.field]], day
    span = resolve_period(period, request.today)
    if fact.field == "value" and span.start != span.end:
        out = await get_daily_trend(GetDailyTrend(metric=metric, period=period), request)
        point = next((p for p in out.payload.get("points", []) if p["date"] == day), None)
        return True, None if point is None else point.get("value"), day
    out = await get_metric_summary(GetMetricSummary(metric=metric, period=period), request)
    payload = out.payload
    if fact.field in ("measured_min", "measured_max"):
        # 기간 요약은 측정 최저·최고를 심박에만 전달한다
        point = payload.get(fact.field)
        return (
            metric == "heart_rate",
            None if point is None else point["value"],
            None if point is None else point["date"],
        )
    key = {"avg": "avg", "total": "total", "value": "avg", "daily_min": "lowest_day", "daily_max": "highest_day"}
    if fact.field == "days_with_data":
        return True, payload["days_with_data"], None
    if fact.field == "in_progress":
        progress = payload.get("in_progress")
        return True, None if progress is None else progress["value"], None
    point = payload.get(key[fact.field])
    if point is None:
        return True, None, day
    return True, point["value"], point.get("date", day)


def prototype_statuses(proto: dict[str, Any], metric: str, *, in_progress: bool = True) -> list[list[Any]]:
    """원형 요약의 데이터 상태. README 상태 규칙(데이터 쪽 status_of)과 같다. [지표, 상태, 날짜·시각] 목록이다.

    in_progress 가 False 면 오늘 진행 중 상태를 넣지 않는다. 기간 내 기록 없음은 진행 중 값이 있는지로 그대로 정한다.
    """
    s = proto["summary"]
    out: list[list[Any]] = []
    if s["missing_days"]:
        out.append([metric, "missing_days", list(s["missing_days"])])
    if s["unsynced_days"]:
        out.append([metric, "unsynced", list(s["unsynced_days"])])
    progress = proto.get("in_progress")
    has_progress = progress is not None and progress["value"] is not None
    if has_progress and in_progress:
        out.append([metric, "in_progress", progress["as_of"]])
    if s["linked_from"] and s["linked_from"] > proto["period"]["start"]:
        out.append([metric, "before_link", s["linked_from"]])
    if s["days_with_data"] == 0 and not s["unsynced_days"] and not has_progress:
        out.append([metric, "no_data", None])
    return out


def _status_row(status: Any) -> list[Any]:
    if status.status in ("missing_days", "unsynced", "slow_walk_excluded"):
        detail: Any = [d.isoformat() for d in status.dates]
    elif status.status == "in_progress":
        detail = status.as_of.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    elif status.status == "before_link":
        detail = status.linked_from.isoformat()
    else:
        detail = None
    return [status.metric, status.status, detail]


def _contains(period: str, day: dt.date, today: dt.date) -> bool:
    span = resolve_period(period, today)
    return span.start <= day <= span.end


def _period_check(fact: Fact, reading: Reading, today: dt.date) -> tuple[list[str], list[str]]:
    """사실의 기간을 질문의 기간과 비교해 (문제 이유, 두 가지로 읽히는 이유)를 반환한다."""
    stated = set(reading.periods) | set(reading.implicit)
    problems: list[str] = []
    readings: list[str] = []
    for period in filter(None, (fact.period, fact.baseline)):
        if period in stated or period in reading.conventions:
            continue
        if fact.date is not None and fact.date in reading.dates and _contains(period, fact.date, today):
            continue
        if stated or reading.dates:
            problems.append("period_mismatch")
        elif reading.conventions:
            readings.append(f"요즘: 관례(지난 7일)와 정답 기간({period})이 다르다")
    if fact.date is not None and reading.dates and fact.date not in reading.dates:
        problems.append("date_mismatch")
    return list(_unique(problems)), readings


Prototype = Callable[[str, str], Awaitable[dict[str, Any]]]


async def _check_fact(
    fact: Fact, reading: Reading, prototype: Prototype, request: ToolRequest
) -> tuple[dict[str, Any], list[str]]:
    today = request.today
    proto = await prototype(fact.metric, fact.period)
    base = await prototype(fact.metric, fact.baseline) if fact.baseline else None
    proto_raw, proto_date = prototype_value(proto, fact, base)
    has_tool, tool_raw, tool_date = await tool_value(fact, request)
    expected = display_value(fact)
    day = None if fact.date is None else fact.date.isoformat()
    reasons: list[str] = []
    if _shown(fact.unit, proto_raw) != expected or (day and proto_date and proto_date != day):
        reasons.append("value_mismatch")
    if not has_tool:
        reasons.append("tool_missing")
    # 정답이 고정 데이터와 다르면 도구 결과와도 다르다. 정답이 맞는데 도구가 다르게 보이는 경우만 따로 센다
    elif not reasons and (_shown(fact.unit, tool_raw) != expected or (day and tool_date and tool_date != day)):
        reasons.append("tool_mismatch")
    if reading.metrics and fact.metric not in reading.metrics:
        reasons.append("metric_mismatch")
    period_problems, readings = _period_check(fact, reading, today)
    reasons += period_problems
    question_period = question_value = None
    stated = [*reading.periods, *reading.implicit]
    if "period_mismatch" in reasons and len(stated) == 1 and fact.baseline is None:
        question_period = stated[0]
        changed = dataclasses.replace(fact, period=question_period, date=None)
        raw, _ = prototype_value(await prototype(fact.metric, question_period), changed, None)
        question_value = _text(fact.unit, _shown(fact.unit, raw))
    separate = reasons == ["tool_missing"] and bool(fact.requires)
    row = {
        "metric": fact.metric,
        "period": fact.period,
        "field": fact.field,
        "baseline": fact.baseline,
        "date": day,
        "unit": fact.unit,
        "expected": fact.value,
        "expected_display": _text(fact.unit, expected),
        "prototype": proto_raw,
        "prototype_display": _text(fact.unit, _shown(fact.unit, proto_raw)),
        "tool": tool_raw if has_tool else None,
        "tool_display": _text(fact.unit, _shown(fact.unit, tool_raw)) if has_tool else None,
        "requires": list(fact.requires),
        "question_periods": stated,
        "question_period": question_period,
        "question_value": question_value,
        "reasons": reasons,
        "verdict": "requires" if separate else ("problem" if reasons else "ok"),
    }
    return row, readings


async def check_item(item: GoldenItem, fixture: Fixture, today: dt.date) -> dict[str, Any]:
    reading = read_question(item.question, today)
    row: dict[str, Any] = {
        "id": item.id,
        "version": item.version,
        "persona": item.persona,
        "category": item.category,
        "question": item.question,
        "reading": reading.as_dict(),
        "checked": bool(item.facts),
        "facts": [],
        "statuses": None,
        "problems": [],
        "undetermined": [],
        "two_readings": [],
    }
    if not item.facts:
        return row
    request = ToolRequest(user_id=fixture.user_id, today=today, port=fixture.port())
    protos: dict[tuple[str, str], dict[str, Any]] = {}

    async def prototype(metric: str, period: str) -> dict[str, Any]:
        if (metric, period) not in protos:
            span = resolve_period(period, today)
            result = await request.port.get_metric_result(fixture.user_id, metric, span.start, span.end)
            protos[(metric, period)] = result.snapshot
        return protos[(metric, period)]

    readings = list(reading.ambiguous)
    problems: list[str] = []
    computed: list[list[Any]] = []
    in_progress = not SETS[item.version].in_progress_today_only or any(f.period == "today" for f in item.facts)
    for fact in item.facts:
        fact_row, fact_readings = await _check_fact(fact, reading, prototype, request)
        row["facts"].append(fact_row)
        readings += fact_readings
        if fact_row["verdict"] == "problem":
            problems += fact_row["reasons"]
        for period in filter(None, (fact.period, fact.baseline)):
            proto = await prototype(fact.metric, period)
            statuses = prototype_statuses(proto, fact.metric, in_progress=in_progress)
            computed += [s for s in statuses if s not in computed]
    expected = [_status_row(s) for s in item.statuses if s.status not in _UNDERIVED_STATUSES]
    missing = [s for s in expected if s not in computed]
    extra = [s for s in computed if s not in expected]
    row["statuses"] = {"expected": expected, "computed": computed, "missing": missing, "extra": extra}
    if missing or extra:
        problems.append("status_mismatch")
    if not reading.metrics:
        row["undetermined"].append("metric")
    if not (reading.periods or reading.implicit or reading.conventions or reading.dates):
        row["undetermined"].append("period")
    row["problems"] = list(_unique(problems))
    row["two_readings"] = list(_unique(readings))
    return row


async def check_answers(items: Sequence[GoldenItem], fixtures: dict[str, Fixture], today: dt.date) -> dict[str, Any]:
    """문항마다 정답을 다시 계산해 대조한다. (문항별 결과, 요약)을 담은 dict 를 반환한다."""
    rows = [await check_item(item, fixtures[item.persona], today) for item in items]
    return {"items": rows, "summary": summarize(rows)}


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    checked = [r for r in rows if r["checked"]]
    facts = [f for r in checked for f in r["facts"]]
    return {
        "items": len(rows),
        "checked": len(checked),
        "no_facts": len(rows) - len(checked),
        "facts": len(facts),
        "facts_ok": sum(f["verdict"] == "ok" for f in facts),
        "facts_requires": sum(f["verdict"] == "requires" for f in facts),
        "facts_problem": sum(f["verdict"] == "problem" for f in facts),
        "problems": [r["id"] for r in checked if r["problems"]],
        "undetermined": [r["id"] for r in checked if r["undetermined"]],
        "two_readings": [r["id"] for r in checked if r["two_readings"]],
        "candidates": [r["id"] for r in checked if r["problems"] or r["two_readings"]],
    }


def _md(text: Any) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _fact_name(fact: dict[str, Any]) -> str:
    name = f"{fact['metric']} {fact['period']} {fact['field']}"
    name += f" (기준 {fact['baseline']})" if fact["baseline"] else ""
    return name + (f" {fact['date']}" if fact["date"] else "")


def _problem_detail(row: dict[str, Any]) -> list[str]:
    details = []
    for fact in row["facts"]:
        if fact["verdict"] != "problem":
            continue
        labels = ", ".join(REASON_LABELS[r] for r in fact["reasons"])
        values = f"정답 {fact['expected_display']}, 원형 {fact['prototype_display']}, 도구 {fact['tool_display']}"
        if fact["question_value"] is not None:
            values += f", 질문 기간({fact['question_period']})으로 계산 {fact['question_value']}"
        elif fact["question_periods"] and "period_mismatch" in fact["reasons"]:
            values += f", 질문 기간 {'·'.join(fact['question_periods'])}"
        details.append(f"{_fact_name(fact)}: {labels} ({values})")
    statuses = row["statuses"] or {}
    if statuses.get("missing"):
        details.append(f"{REASON_LABELS['status_mismatch']}: 정답에만 있음 {statuses['missing']}")
    if statuses.get("extra"):
        details.append(f"{REASON_LABELS['status_mismatch']}: 다시 계산한 상태에만 있음 {statuses['extra']}")
    return details


def section_lines(check: dict[str, Any]) -> list[list[str]]:
    """정답 검사 결과 절들. 실행 보고서와 정답 검사 보고서가 같이 쓴다."""
    rows = {r["id"]: r for r in check["items"]}
    s = check["summary"]
    problems = ["## 정답 검사에서 걸린 문항", ""]
    if s["problems"]:
        problems += ["| 문항 | 질문 | 이유 |", "| --- | --- | --- |"]
        problems += [
            f"| {i} | {_md(rows[i]['question'])} | {_md('; '.join(_problem_detail(rows[i])))} |" for i in s["problems"]
        ]
    else:
        problems.append("없음")
    undetermined = ["## 질문만으로 기간·지표를 정할 수 없는 문항", ""]
    if s["undetermined"]:
        undetermined += ["| 문항 | 질문 | 정할 수 없는 것 | 정답에 쓴 지표·기간 |", "| --- | --- | --- | --- |"]
        for i in s["undetermined"]:
            row = rows[i]
            missing = ", ".join(UNDETERMINED_LABELS[u] for u in row["undetermined"])
            used = ", ".join(dict.fromkeys(f"{f['metric']} {f['period']}" for f in row["facts"]))
            undetermined.append(f"| {i} | {_md(row['question'])} | {missing} | {used} |")
    else:
        undetermined.append("없음")
    two = ["## 질문이 두 가지로 읽히는 문항", ""]
    if s["two_readings"]:
        two += ["| 문항 | 질문 | 읽는 방법 | 정답에 쓴 기간 |", "| --- | --- | --- | --- |"]
        for i in s["two_readings"]:
            row = rows[i]
            used = ", ".join(dict.fromkeys(p for f in row["facts"] for p in (f["period"], f["baseline"]) if p))
            two.append(f"| {i} | {_md(row['question'])} | {_md('; '.join(row['two_readings']))} | {used} |")
    else:
        two.append("없음")
    return [problems, undetermined, two]


def candidate_lines(check: dict[str, Any]) -> list[str]:
    s = check["summary"]
    lines = [
        "## 정답 고칠 후보",
        "",
        "정답 검사에서 걸린 문항과 질문이 두 가지로 읽히는 문항만 올린다. 모델 답변이 틀린 문항은 넣지 않는다.",
        "",
    ]
    if not s["candidates"]:
        return [*lines, "없음"]
    rows = {r["id"]: r for r in check["items"]}
    for i in s["candidates"]:
        why = [REASON_LABELS[r] for r in rows[i]["problems"]]
        why += ["두 가지로 읽힘"] if rows[i]["two_readings"] else []
        lines.append(f"- `{i}` {', '.join(why)}")
    return lines


def summary_line(check: dict[str, Any]) -> str:
    s = check["summary"]
    return (
        f"- 정답 검사(LLM 없음): 문항 {s['items']}개 중 사실이 있는 {s['checked']}개 · 사실 {s['facts']}개 중 같음 "
        f"{s['facts_ok']}, requires(도구 결과에 없는 값) {s['facts_requires']}, 걸림 {s['facts_problem']} · "
        f"걸린 문항 {len(s['problems'])} · 정할 수 없는 문항 {len(s['undetermined'])} · "
        f"두 가지로 읽히는 문항 {len(s['two_readings'])}"
    )


def to_markdown(report: dict[str, Any]) -> str:
    """정답 검사만 실행한 보고서."""
    check = report["answer_check"]
    lines = [
        f"# 코칭 골든셋 정답 검사 ({report['measured_at']})",
        "",
        f"- 커밋 `{report['commit']}` · 실행 명령 `{report['command']}` · LLM 호출 없음, 모델 답변을 쓰지 않음",
        *(
            f"- 데이터셋 `{d['path']}` {d['selected']}/{d['rows']}문항 · sha256 {d['sha256'][:12]}"
            for d in report["datasets"]
        ),
        f"- 고정 데이터 sha256 {report['fixtures_sha256'][:12]} · 기준 시각 {report['now']}",
        summary_line(check),
        "",
    ]
    for section in [*section_lines(check), candidate_lines(check)]:
        lines += [*section, ""]
    lines += ["## 질문 읽기 규칙", "", *(f"- {rule}" for rule in READING_RULES)]
    return "\n".join(lines) + "\n"
