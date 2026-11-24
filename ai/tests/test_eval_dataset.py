"""평가 데이터셋 형식 검사 (eval/datasets/README.md).

도구 정의가 바뀌어 지금 도구 정답이 더 이상 유효하지 않으면 여기서 실패한다.
"""

import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

from coach_ai.agent.executor import tool_specs
from coach_ai.agent.prompts import load_system_prompt
from coach_ai.tools.periods import MAX_LIST_DAYS, WEEKDAYS, PeriodError, period_range, resolve_period
from coach_ai.tools.schemas import PERIODS, TOOL_INPUTS
from eval.tool_selection import expected_variants

DATASETS_DIR = Path(__file__).resolve().parents[1] / "eval" / "datasets"
DATASET = DATASETS_DIR / "tool_selection_v1.jsonl"
DATASET_V1_1 = DATASETS_DIR / "tool_selection_v1.1.jsonl"
DATASET_V1_2 = DATASETS_DIR / "tool_selection_v1.2.jsonl"
DATASET_V1_3 = DATASETS_DIR / "tool_selection_v1.3.jsonl"
SUPPLEMENT = DATASETS_DIR / "tool_selection_ai_v1.jsonl"
SUPPLEMENT_V1_1 = DATASETS_DIR / "tool_selection_ai_v1.1.jsonl"
EXERCISE = DATASETS_DIR / "tool_selection_ai_exercise_v1.jsonl"
EXERCISE_V1_1 = DATASETS_DIR / "tool_selection_ai_exercise_v1.1.jsonl"
EXERCISE_V1_2 = DATASETS_DIR / "tool_selection_ai_exercise_v1.2.jsonl"
PERIOD = DATASETS_DIR / "tool_selection_ai_period_v1.jsonl"
PERIOD_V2 = DATASETS_DIR / "tool_selection_ai_period_v2.jsonl"
PERIOD_V2_1 = DATASETS_DIR / "tool_selection_ai_period_v2.1.jsonl"
PERIOD_V2_2 = DATASETS_DIR / "tool_selection_ai_period_v2.2.jsonl"
PERIOD_V2_3 = DATASETS_DIR / "tool_selection_ai_period_v2.3.jsonl"
PERIOD_V2_4 = DATASETS_DIR / "tool_selection_ai_period_v2.4.jsonl"
GUARD_SET = DATASETS_DIR / "tool_selection_ai_guard_v1.jsonl"
GUARD_SET_V1_1 = DATASETS_DIR / "tool_selection_ai_guard_v1.1.jsonl"
SCORE_SET = DATASETS_DIR / "tool_selection_ai_score_v1.jsonl"
DAILY_MESSAGE_SET = DATASETS_DIR / "tool_selection_ai_daily_message_v1.jsonl"
SLEEP_EXERCISE_DETAIL_SET = DATASETS_DIR / "tool_selection_ai_sleep_exercise_detail_v1.jsonl"
# 데이터셋 README 채점 기준 4 로 조회해도 맞고 조회하지 않아도 맞는 문항.
# 형식에 그 판정이 없어 v1.1 에서 채점에서 제외한다
EITHER_ITEMS = ("synth-01-403", "synth-04-403", "synth-04-404", "synth-04-405", "synth-09-403")
GUARD_CATEGORIES = {"emergency", "mental_crisis", "diagnosis", "medication", "out_of_scope", "adversarial"}
EXPECTED_TYPES = {"tool_calls", "no_tool", "clarify", "guard"}
# "하루에", "날마다", "평일", "주말"은 조회 기간을 정하지 않아 넣지 않는다
EXPLICIT_PERIOD = re.compile(r"오늘|어제|어젯밤|그저께|이번\s?(주|달)|지난\s?(주|달)|일주일|한\s?달|\d+\s?(일|주|개월)")
# 기간 문항 파일의 정답 기간 조합: 지지난주 요약, 지난주와 지지난주 비교,
# 대조 문항(이번 주와 그 전 주, 최근 2주, 요즘, 기준일의 어제인 10월 21일)
PERIOD_SET_PERIODS = (
    {"week_before_last"},
    {"last_week", "week_before_last"},
    {"this_week", "last_week"},
    {"last_14_days"},
    {"last_7_days"},
    {"yesterday"},
)
UNSUPPORTED_PERIOD = re.compile(r"3주 전|두 달 전|지지난달")
# 기간 문항 v2 의 기준일. v1 과 같은 2026-10-22(목)이다
PERIOD_DAY = date(2026, 10, 22)
PERIOD_KINDS = ("days_ago", "recent_days", "weeks_ago", "months_ago", "recent_months", "date", "dates")
# 날짜마다 값을 전달해 31일까지만 받는 도구
LIST_TOOLS = {"get_daily_trend", "get_sleep_records", "get_exercise_sessions"}


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.fixture(
    scope="module",
    params=[
        DATASET,
        DATASET_V1_1,
        DATASET_V1_3,
        SUPPLEMENT,
        SUPPLEMENT_V1_1,
        EXERCISE,
        EXERCISE_V1_1,
        EXERCISE_V1_2,
        PERIOD,
        PERIOD_V2,
        PERIOD_V2_1,
        PERIOD_V2_2,
        PERIOD_V2_3,
        PERIOD_V2_4,
        GUARD_SET,
        GUARD_SET_V1_1,
        SCORE_SET,
        DAILY_MESSAGE_SET,
        SLEEP_EXERCISE_DETAIL_SET,
    ],
    ids=lambda path: path.stem,
)
def rows(request):
    """평가 데이터셋 파일 하나의 문항 목록을 반환한다."""
    return load(request.param)


@pytest.mark.parametrize("path", [DATASET, DATASET_V1_1, DATASET_V1_3], ids=lambda path: path.stem)
def test_size_and_personas(path):
    rows = load(path)
    assert len(rows) == 300
    assert Counter(r["persona"] for r in rows) == {f"synth-{i:02d}": 30 for i in range(1, 11)}


def test_ids_and_questions_are_unique(rows):
    assert len({r["id"] for r in rows}) == len(rows)
    assert len({r["question"] for r in rows}) == len(rows)


def test_fields(rows):
    for r in rows:
        exp = r["expected"]
        assert exp["type"] in EXPECTED_TYPES, r["id"]
        assert (r["category"] == "proposed") == bool(r["proposed"]), r["id"]
        if exp["type"] == "tool_calls":
            assert exp["tool_calls"], r["id"]
        if exp["type"] == "guard":
            assert exp["guard_category"] in GUARD_CATEGORIES, r["id"]
            assert r["category"] == "guard", r["id"]
        if exp["type"] in ("no_tool", "clarify"):
            assert r["category"] == exp["type"], r["id"]


def test_current_tool_answers_match_schemas(rows):
    """제안이 없는 질문의 정답·허용 답안은 지금 도구 스키마를 통과해야 한다.

    분석 인자의 기대값(값 목록, null)은 실제 호출 모양으로 펼쳐 하나씩 검사한다.
    """
    for r in rows:
        if r["proposed"]:
            continue
        for calls in [r["expected"].get("tool_calls", []), *r["acceptable"]]:
            for call in calls:
                assert call["name"] in TOOL_INPUTS, r["id"]
                variants = expected_variants(call)
                assert variants, r["id"]
                for variant in variants:
                    TOOL_INPUTS[call["name"]].model_validate(variant["args"])
                assert "user_id" not in call["args"], r["id"]


def test_supplement_asks_without_period_and_expects_last_7_days():
    v1_questions = {r["question"] for r in load(DATASET)}
    for r in load(SUPPLEMENT):
        question = r["question"]
        assert r["category"] == "tool" and question not in v1_questions, r["id"]
        assert re.search("요즘|요새|최근", question) and not EXPLICIT_PERIOD.search(question), r["id"]
        for calls in [r["expected"]["tool_calls"], *r["acceptable"]]:
            assert {call["args"]["period"] for call in calls} == {"last_7_days"}, r["id"]


@pytest.mark.parametrize("path", [EXERCISE, EXERCISE_V1_1, EXERCISE_V1_2], ids=lambda path: path.stem)
def test_exercise_set_asks_only_exercise_time_or_sessions(path):
    """운동 문항 파일 → id 2xx, 질문 중복 없음, 모두 tool, 정답·허용 답안이 운동 조회뿐이다."""
    others = {r["question"] for r in [*load(DATASET), *load(SUPPLEMENT)]}
    for r in load(path):
        assert re.fullmatch(rf"{r['persona']}-2\d\d", r["id"]), r["id"]
        assert r["question"] not in others, r["id"]
        assert (r["category"], r["proposed"]) == ("tool", []), r["id"]
        calls = [call for calls in [r["expected"]["tool_calls"], *r["acceptable"]] for call in calls]
        # 운동 시간은 metric=exercise 로, 운동 종류·세션은 get_exercise_sessions 로 조회한다
        for call in calls:
            assert call["name"] == "get_exercise_sessions" or call["args"]["metric"] == "exercise", r["id"]


def test_period_set_asks_week_before_last_or_unsupported_periods():
    rows = load(PERIOD)
    assert Counter(r["category"] for r in rows) == {"tool": 12, "no_tool": 4}
    others = {r["question"] for r in [*load(DATASET), *load(SUPPLEMENT), *load(EXERCISE)]}
    for r in rows:
        assert re.fullmatch(rf"{r['persona']}-3\d\d", r["id"]), r["id"]
        assert r["question"] not in others and r["proposed"] == [], r["id"]
        if r["category"] == "no_tool":
            # 목록으로 덮을 수 없는 기간은 다른 기간으로 바꿔 조회하지 않고 안내하는 것이 정답이다
            assert UNSUPPORTED_PERIOD.search(r["question"]), r["id"]
            continue
        for calls in [r["expected"]["tool_calls"], *r["acceptable"]]:
            periods = {v for call in calls for k, v in call["args"].items() if k in ("period", "current", "baseline")}
            assert periods in PERIOD_SET_PERIODS, r["id"]


def _periods(calls: list[dict]) -> list[str]:
    return [v for call in calls for k, v in call["args"].items() if k in ("period", "current", "baseline")]


def test_period_v2_keeps_v1_tool_items_and_turns_unsupported_items_into_tool_items():
    v1 = {r["id"]: r for r in load(PERIOD)}
    v2 = {r["id"]: r for r in load(PERIOD_V2)}
    assert set(v1) <= set(v2)
    definition = ("category", "question", "expected", "acceptable", "proposed")
    for item_id, old in v1.items():
        new = v2[item_id]
        if old["category"] == "tool":
            # 정의를 그대로 둬 v1 으로 잰 기록과 같은 문항으로 비교할 수 있다
            assert {k: new[k] for k in definition} == {k: old[k] for k in definition}, item_id
            continue
        # 목록으로 덮을 수 없던 기간은 새 표현으로 조회하는 tool 문항이 된다
        assert new["category"] == "tool" and UNSUPPORTED_PERIOD.search(new["question"]), item_id
        periods = _periods(new["expected"]["tool_calls"])
        assert periods and not set(periods) & set(PERIODS), item_id


def test_period_v2_covers_each_kind_and_limit_cases():
    rows = load(PERIOD_V2)
    assert Counter(r["category"] for r in rows) == {"tool": 30}
    others = {r["question"] for path in (DATASET, DATASET_V1_1, SUPPLEMENT, EXERCISE) for r in load(path)}
    expected = []
    for r in rows:
        assert re.fullmatch(rf"{r['persona']}-3\d\d", r["id"]), r["id"]
        assert r["question"] not in others and r["proposed"] == [], r["id"]
        expected += r["expected"]["tool_calls"]
    periods = _periods(expected)
    for kind in PERIOD_KINDS:
        assert any(p.split(":")[0] == kind for p in periods), kind
    # 연도 없는 날짜, 이름 기간으로 묻는 문항
    assert any(re.fullmatch(r"date:\d\d-\d\d|dates:\d\d-\d\d\.\.\d\d-\d\d", p) for p in periods)
    assert any(p in PERIODS for p in periods)
    # 기준일에 모두 날짜 범위로 해석되고, 12개월 한도를 넘는 기간과 31일을 넘는 날짜별 요청이 하나씩 있다
    rejected = []
    for call in expected:
        for p in _periods([call]):
            period_range(p, PERIOD_DAY)
            try:
                resolve_period(p, PERIOD_DAY, max_days=MAX_LIST_DAYS if call["name"] in LIST_TOOLS else None)
            except PeriodError as e:
                rejected.append((call["name"], p, e.reason))
    assert sorted(rejected) == [
        ("get_daily_trend", "recent_months:6", "too_long"),
        ("get_metric_summary", "months_ago:13", "too_old"),
    ]


def _call(name: str, **args) -> dict:
    return {"name": name, "args": args}


def test_exercise_v1_1_adds_answers_by_new_date_rules_only():
    v1 = {r["id"]: r for r in load(EXERCISE)}
    v1_1 = {r["id"]: r for r in load(EXERCISE_V1_1)}
    assert list(v1_1) == list(v1)
    # 결과를 본 뒤 더한 허용 답안이다. 정답과 질문은 그대로 둔다
    added = {
        # 날짜를 말하면 그날만 조회한다 (시스템 프롬프트 v1.5)
        "synth-09-201": [
            [_call("get_daily_trend", metric="exercise", period="date:10-19")],
            [_call("get_metric_summary", metric="exercise", period="date:10-19")],
        ],
        # 지난주 토요일(10-17)은 요일 하루로 조회해도 같은 날이다
        "synth-06-202": [
            [_call("get_daily_trend", metric="exercise", period="weekday:sat")],
            [_call("get_metric_summary", metric="exercise", period="weekday:sat")],
        ],
    }
    for item_id, old in v1.items():
        new = v1_1[item_id]
        for key in ("persona", "category", "question", "expected", "proposed"):
            assert new[key] == old[key], (item_id, key)
        assert new["acceptable"] == old["acceptable"] + added.get(item_id, []), item_id
    assert period_range("weekday:sat", PERIOD_DAY) == period_range("date:10-17", PERIOD_DAY)


def test_period_v2_1_adds_weekday_items_to_v2():
    v2 = {r["id"]: r for r in load(PERIOD_V2)}
    v2_1 = {r["id"]: r for r in load(PERIOD_V2_1)}
    assert {i: v2_1[i] for i in v2} == v2
    new = [v2_1[i] for i in v2_1 if i not in v2]
    others = {r["question"] for path in (DATASET, DATASET_V1_1, SUPPLEMENT, EXERCISE, PERIOD_V2) for r in load(path)}
    assert [r["category"] for r in new] == ["tool", "tool"]
    periods = []
    for r in new:
        assert re.fullmatch(rf"{r['persona']}-3\d\d", r["id"]) and r["question"] not in others, r["id"]
        # 요일만 말한 질문이다
        assert re.search(r"지난 [일토]요일", r["question"]) and "지난주" not in r["question"], r["id"]
        for calls in [r["expected"]["tool_calls"], *r["acceptable"]]:
            periods += _periods(calls)
    assert set(periods) == {"weekday:sun", "weekday:sat"}


def test_period_v2_2_adds_rule_check_items_not_in_prompt_or_tools():
    v2_1 = {r["id"]: r for r in load(PERIOD_V2_1)}
    v2_2 = {r["id"]: r for r in load(PERIOD_V2_2)}
    assert {i: v2_2[i] for i in v2_1} == v2_1
    new = {i: v2_2[i] for i in v2_2 if i not in v2_1}
    # 문항마다 확인할 표현, 정답, 허용 답안 (기준일 2026-10-22 목)
    expected = {
        # 요일 이름만으로 말한 날은 어느 요일이든 그날만 조회한다. 지난 수요일은 기준일보다 앞선 요일이라
        # 어제(10-21)와 지난주 수요일(10-14) 두 가지로 읽힌다. 두 해석 모두 그날 하루만 조회한 답만 맞다
        "synth-05-304": (
            "지난 수요일",
            [_call("get_metric_summary", metric="heart_rate", period="weekday:wed")],
            [
                [_call("get_daily_trend", metric="heart_rate", period="weekday:wed")],
                [_call("get_metric_summary", metric="heart_rate", period="date:10-14")],
                [_call("get_daily_trend", metric="heart_rate", period="date:10-14")],
            ],
        ),
        "synth-10-304": (
            "지난 금요일",
            [_call("get_metric_summary", metric="exercise", period="weekday:fri")],
            [[_call("get_daily_trend", metric="exercise", period="weekday:fri")]],
        ),
        # 주말은 그 주를 날짜별로 조회해 토·일을 찾는다. 바르게 센 토·일도 허용한다
        "synth-01-305": (
            "지지난 주말",
            [_call("get_exercise_sessions", period="weeks_ago:2")],
            [[_call("get_exercise_sessions", period="dates:10-10..10-11")]],
        ),
        # 주와 요일을 말하면 그 주를 날짜별로 조회한다. 바르게 센 그날도 허용한다
        "synth-02-304": (
            "지난주 월요일",
            [_call("get_daily_trend", metric="steps", period="weeks_ago:1")],
            [[_call("get_metric_summary", metric="steps", period="date:10-12")]],
        ),
    }
    assert set(new) == set(expected)
    others = {r["question"] for path in (DATASET, DATASET_V1_1, SUPPLEMENT, EXERCISE, PERIOD_V2_1) for r in load(path)}
    # 규칙만으로 맞는지 확인하는 문항이라 문장과 표현을 시스템 프롬프트와 도구 설명에 넣지 않는다
    shown = load_system_prompt("v1.5") + load_system_prompt() + json.dumps(tool_specs(), ensure_ascii=False)
    for item_id, (words, calls, acceptable) in expected.items():
        r = new[item_id]
        assert re.fullmatch(rf"{r['persona']}-3\d\d", item_id) and r["category"] == "tool", item_id
        assert words in r["question"] and r["question"] not in others, item_id
        assert words not in shown and r["question"] not in shown, item_id
        assert (r["expected"]["tool_calls"], r["acceptable"]) == (calls, acceptable), item_id
    day = PERIOD_DAY
    assert period_range("weekday:wed", day) == period_range("date:10-21", day) == period_range("days_ago:1", day)
    assert period_range("date:10-14", day) == period_range("days_ago:8", day)
    # 요일 하루가 이번 주 날짜가 되는 것은 오늘(목)보다 앞선 요일뿐이다. 같거나 뒤의 요일은 지난주의 그날이다
    this_week = period_range("weeks_ago:0", day).start
    assert [w for w in WEEKDAYS if period_range(f"weekday:{w}", day).start >= this_week] == ["mon", "tue", "wed"]
    assert period_range("weekday:fri", day) == period_range("date:10-16", day)
    assert period_range("weeks_ago:2", day) == period_range("dates:10-05..10-11", day)
    # 지난주 월요일을 요일 하루로 조회하면 이번 주 월요일이라 오답이다
    assert period_range("weekday:mon", day) == period_range("date:10-19", day)


def _all_periods(row: dict) -> list[str]:
    """문항의 정답과 허용 답안에 든 기간 인자를 모두 모은다."""
    return [p for calls in [row["expected"].get("tool_calls", []), *row["acceptable"]] for p in _periods(calls)]


def test_period_v2_3_changes_two_items_and_adds_nine_range_items():
    """period v2.3 → 두 문항만 정답이 바뀌고 나머지 34줄은 v2.2 와 같으며, 새 아홉 문항은 기준일에 해석된다."""
    v2_2 = {r["id"]: r for r in load(PERIOD_V2_2)}
    v2_3 = {r["id"]: r for r in load(PERIOD_V2_3)}
    changed = {"synth-01-304": "weeks_ago:1.sat", "synth-02-304": "weeks_ago:1.mon"}
    assert {i: v2_3[i] for i in v2_2 if i not in changed} == {i: v2_2[i] for i in v2_2 if i not in changed}
    for item_id, period in changed.items():
        new, old = v2_3[item_id], v2_2[item_id]
        assert new["expected"]["tool_calls"] == [_call("get_metric_summary", metric="steps", period=period)]
        # v2.2 의 정답이 허용 답안 맨 앞에 들어가고, 질문·기존 허용 답안은 그대로다
        assert new["acceptable"] == [old["expected"]["tool_calls"], *old["acceptable"]], item_id
        assert new["question"] == old["question"] and new["note"].startswith(old["note"]), item_id
        assert "weeks_ago:N.요일" in new["note"], item_id
    assert period_range("weeks_ago:1.sat", PERIOD_DAY) == period_range("date:10-17", PERIOD_DAY)
    assert period_range("weeks_ago:1.mon", PERIOD_DAY) == period_range("date:10-12", PERIOD_DAY)
    new_rows = [v2_3[i] for i in v2_3 if i not in v2_2]
    assert len(new_rows) == 9 and list(v2_3)[: len(v2_2)] == list(v2_2)
    others = {r["question"] for path in (DATASET, DATASET_V1_1, SUPPLEMENT, EXERCISE, PERIOD_V2_2) for r in load(path)}
    for r in new_rows:
        assert re.fullmatch(rf"{r['persona']}-3\d\d", r["id"]) and r["category"] == "tool", r["id"]
        assert r["question"] not in others and r["proposed"] == [], r["id"]
    ranges = {
        "synth-06-304": ("2026-10-08", "2026-10-21"),
        "synth-05-305": ("2026-10-14", "2026-10-22"),
        "synth-03-304": ("2026-10-12", "2026-10-21"),
        "synth-08-303": ("2026-10-19", "2026-10-21"),
        "synth-09-304": ("2026-10-05", "2026-10-14"),
        "synth-07-305": ("2026-10-15", "2026-10-21"),
        "synth-04-305": ("2026-10-06", "2026-10-06"),
        "synth-03-305": ("2026-10-18", "2026-10-18"),
    }
    by_id = {r["id"]: r for r in new_rows}
    for item_id, (start, end) in ranges.items():
        for period in _all_periods(by_id[item_id]):
            span = period_range(period, PERIOD_DAY)
            if period.startswith(("weeks_ago:2", "weeks_ago:1")) and ".." not in period and "." not in period:
                continue  # 주 전체를 날짜별로 조회한 허용 답안은 그 주를 포함하는 넓은 범위다
            assert (span.start.isoformat(), span.end.isoformat()) == (start, end), (item_id, period)
    compare = by_id["synth-10-305"]["expected"]["tool_calls"][0]
    assert compare["name"] == "compare_periods" and compare["args"]["metric"] == "steps"
    assert period_range(compare["args"]["current"], PERIOD_DAY) == period_range("dates:10-12..10-14", PERIOD_DAY)
    assert period_range(compare["args"]["baseline"], PERIOD_DAY) == period_range("dates:10-05..10-07", PERIOD_DAY)


def test_supplement_v1_1_adds_signals_answers_to_oxygen_item_only():
    """ai v1.1 → synth-04-102 허용 답안에 이상 신호 분석(signals) 호출 두 가지만 더한다."""
    v1 = {r["id"]: r for r in load(SUPPLEMENT)}
    v1_1 = {r["id"]: r for r in load(SUPPLEMENT_V1_1)}
    assert list(v1_1) == list(v1)
    assert {i: v1_1[i] for i in v1 if i != "synth-04-102"} == {i: v1[i] for i in v1 if i != "synth-04-102"}
    new, old = v1_1["synth-04-102"], v1["synth-04-102"]
    assert {k: new[k] for k in new if k not in ("acceptable", "note")} == {
        k: old[k] for k in old if k not in ("acceptable", "note")
    }
    assert new["acceptable"] == [
        *old["acceptable"],
        [_call("get_sleep_records", analysis="signals")],
        [_call("get_sleep_records", period="last_7_days", analysis="signals")],
    ]


def test_exercise_v1_2_changes_only_saturday_item_to_week_weekday():
    """exercise v1.2 → synth-06-202 정답만 바뀌고 v1.1 의 정답·허용 답안은 허용 답안에 들어 있다."""
    v1_1 = {r["id"]: r for r in load(EXERCISE_V1_1)}
    v1_2 = {r["id"]: r for r in load(EXERCISE_V1_2)}
    assert list(v1_2) == list(v1_1)
    assert {i: v1_2[i] for i in v1_1 if i != "synth-06-202"} == {i: v1_1[i] for i in v1_1 if i != "synth-06-202"}
    new, old = v1_2["synth-06-202"], v1_1["synth-06-202"]
    assert new["expected"]["tool_calls"] == [_call("get_metric_summary", metric="exercise", period="weeks_ago:1.sat")]
    assert new["acceptable"] == [old["expected"]["tool_calls"], *old["acceptable"]]
    assert new["question"] == old["question"] and "weeks_ago:N.요일" in new["note"]
    assert period_range("weeks_ago:1.sat", PERIOD_DAY) == period_range("date:10-17", PERIOD_DAY)


def test_v1_3_changes_only_answers_that_differ_from_period_definition():
    """tool_selection v1.3 → 기간 정의(11-04 결정)와 다른 네 문항만 바뀌고 나머지 296줄은 v1.2 와 같다."""
    v1_2 = {r["id"]: r for r in load(DATASET_V1_2)}
    v1_3 = {r["id"]: r for r in load(DATASET_V1_3)}
    changed = {"synth-06-020", "synth-02-025", "synth-07-022", "synth-07-019"}
    assert list(v1_3) == list(v1_2)
    assert {i: v1_3[i] for i in v1_2 if i not in changed} == {i: v1_2[i] for i in v1_2 if i not in changed}
    same = ("question", "category", "proposed")
    for item_id in changed:
        new, old = v1_3[item_id], v1_2[item_id]
        assert [new[k] for k in same] == [old[k] for k in same] and "v1.3:" in new["note"], item_id
    # 지난 주말은 날짜별 조회라 지난주 전체(정답 그대로)와 지난주 토·일 이틀(허용 답안) 모두 맞다
    weekend = v1_3["synth-06-020"]
    assert weekend["expected"] == v1_2["synth-06-020"]["expected"]
    assert weekend["acceptable"] == [[_call("get_exercise_sessions", period="weeks_ago:1.sat..weeks_ago:1.sun")]]
    assert period_range("last_week", PERIOD_DAY) == period_range("weeks_ago:1", PERIOD_DAY)
    two_days = period_range("weeks_ago:1.sat..weeks_ago:1.sun", PERIOD_DAY)
    assert two_days == period_range("dates:10-17..10-18", PERIOD_DAY)
    # proposed 세 문항의 날짜는 지난 X요일(질문한 날 이전 가장 최근의 그 요일)로 세어 v1.2 보다 하루씩 앞이다.
    # 지난 금요일(10-16) 밤 잠은 토요일에 깬 수면이고, 지난 토요일·일요일은 그날이다
    dates = {"synth-02-025": "2026-10-17", "synth-07-022": "2026-10-17", "synth-07-019": "2026-10-18"}
    for item_id, day in dates.items():
        [new], [old] = v1_3[item_id]["expected"]["tool_calls"], v1_2[item_id]["expected"]["tool_calls"]
        assert new == {**old, "args": {**old["args"], "date": day}}, item_id
        assert date.fromisoformat(old["args"]["date"]) - date.fromisoformat(day) == timedelta(days=1), item_id
    assert [period_range(f"weekday:{w}", PERIOD_DAY).start.day for w in ("fri", "sat", "sun")] == [16, 17, 18]


def test_period_v2_4_drops_last_week_wednesday_answers_only():
    """period v2.4 → synth-05-304 에서 지난주 수요일(10-14) 허용 답안 두 개만 빠지고 나머지 44줄은 v2.3 과 같다."""
    v2_3 = {r["id"]: r for r in load(PERIOD_V2_3)}
    v2_4 = {r["id"]: r for r in load(PERIOD_V2_4)}
    assert list(v2_4) == list(v2_3)
    assert {i: v2_4[i] for i in v2_3 if i != "synth-05-304"} == {i: v2_3[i] for i in v2_3 if i != "synth-05-304"}
    new, old = v2_4["synth-05-304"], v2_3["synth-05-304"]
    assert new["expected"] == old["expected"] and new["question"] == old["question"]
    assert new["note"].startswith(old["note"]) and "v2.4:" in new["note"]
    assert new["acceptable"] == [[_call("get_daily_trend", metric="heart_rate", period="weekday:wed")]]
    assert [a for a in old["acceptable"] if a not in new["acceptable"]] == [
        [_call("get_metric_summary", metric="heart_rate", period="date:10-14")],
        [_call("get_daily_trend", metric="heart_rate", period="date:10-14")],
    ]
    # 지난 수요일은 질문한 날 이전에 가장 최근에 지난 수요일(10-21) 하루다
    assert period_range("weekday:wed", PERIOD_DAY) == period_range("date:10-21", PERIOD_DAY)


def test_guard_set_holds_new_blocks_and_record_questions():
    rows = load(GUARD_SET)
    assert Counter(r["category"] for r in rows) == {"guard": 21, "tool": 19, "no_tool": 6}
    paths = (DATASET, DATASET_V1_1, SUPPLEMENT, EXERCISE, EXERCISE_V1_1, PERIOD, PERIOD_V2_2)
    others = {r["question"] for path in paths for r in load(path)}
    for r in rows:
        assert re.fullmatch(rf"{r['persona']}-4\d\d", r["id"]), r["id"]
        assert r["question"] not in others and r["proposed"] == [], r["id"]
    # 넓힌 범주만 새로 차단한다. 진단·약물·범위 밖·다른 사람은 차단 조건이 좁아 이 묶음에 차단 문항이 없다
    assert {r["expected"]["guard_category"] for r in rows if r["category"] == "guard"} == {"emergency", "mental_crisis"}
    # 기간은 시스템 프롬프트의 표현으로 적고, 기준일에 조회 한도 안이다
    for r in rows:
        for calls in [r["expected"].get("tool_calls", []), *r["acceptable"]]:
            for call in calls:
                for p in _periods([call]):
                    assert p not in PERIODS, r["id"]
                    resolve_period(p, PERIOD_DAY, max_days=MAX_LIST_DAYS if call["name"] in LIST_TOOLS else None)


def test_guard_set_v1_1_moves_either_items_to_proposed():
    v1 = {r["id"]: r for r in load(GUARD_SET)}
    v1_1 = {r["id"]: r for r in load(GUARD_SET_V1_1)}
    assert list(v1_1) == list(v1)
    assert Counter(r["category"] for r in v1_1.values()) == {"guard": 21, "tool": 14, "no_tool": 6, "proposed": 5}
    same = ("persona", "question", "expected", "acceptable")
    for item_id, old in v1.items():
        new = v1_1[item_id]
        if item_id not in EITHER_ITEMS:
            assert new == old, item_id
            continue
        # 정답·허용 답안은 조회할 때의 답으로 두고 종류·제안·메모만 바꾼다. proposed 는 채점하지 않고 응답 유형만 센다
        assert {k: new[k] for k in same} == {k: old[k] for k in same}, item_id
        assert (old["category"], new["category"]) == ("tool", "proposed"), item_id
        assert new["proposed"] == ["type:tool_calls_or_no_tool"], item_id
        assert new["note"].startswith(old["note"]) and "채점 기준 4" in new["note"], item_id


def test_score_set_separates_score_questions_from_other_record_questions():
    rows = load(SCORE_SET)
    assert Counter(r["category"] for r in rows) == {"tool": 25, "no_tool": 1}
    paths = [path for path in DATASETS_DIR.glob("tool_selection_*.jsonl") if path != SCORE_SET]
    others = {r["question"] for path in paths for r in load(path)}
    # 규칙과 도구 설명만으로 고르는지 확인하는 문항이라 질문을 시스템 프롬프트와 도구 설명에 넣지 않는다
    shown = load_system_prompt() + json.dumps(tool_specs(), ensure_ascii=False)
    score_days = []
    for r in rows:
        assert re.fullmatch(rf"{r['persona']}-5\d\d", r["id"]), r["id"]
        assert r["question"] not in others and r["question"] not in shown and r["proposed"] == [], r["id"]
        calls = [call for calls in [r["expected"].get("tool_calls", []), *r["acceptable"]] for call in calls]
        names = {call["name"] for call in calls}
        # 점수 문항은 점수 도구만 부르고, 나머지 문항은 점수 도구를 부르지 않는 것이 정답이다
        assert names == {"get_lifestyle_score"} or "get_lifestyle_score" not in names, r["id"]
        for call in calls:
            for p in _periods([call]):
                assert p not in PERIODS, r["id"]
                if call["name"] == "get_lifestyle_score":
                    score_days.append(len(period_range(p, PERIOD_DAY).days()))
    # 점수 문항 17개(호출 18개) 중 여러 날을 묻는 문항은 두 개다. 도구가 조회하지 않고 하루를 제안하라고 안내한다
    assert len(score_days) == 18 and sorted(n for n in score_days if n > 1) == [4, 7]
    several = [r for r in rows if "unavailable_period" in r["note"]]
    assert [r["expected"]["tool_calls"][0]["args"]["period"] for r in several] == ["weeks_ago:0", "recent_days:7"]
    for r in several:
        with pytest.raises(PeriodError) as e:
            resolve_period(r["expected"]["tool_calls"][0]["args"]["period"], PERIOD_DAY, max_days=1)
        assert e.value.reason == "too_long"


def test_daily_message_set_separates_daily_message_questions_from_other_questions():
    rows = {r["id"]: r for r in load(DAILY_MESSAGE_SET)}
    daily = _call("get_daily_message")
    score = _call("get_lifestyle_score", period="days_ago:0")
    steps = _call("get_metric_summary", metric="steps", period="days_ago:0")
    # 묶음마다 정답과 허용 답안 (기준일 2026-10-22 목). 측정 결과를 보기 전에 정했다
    expected = {
        # 한마디 질문은 한마디 도구만 부른다
        **{f"synth-0{i}-601": ([daily], []) for i in range(1, 8)},
        # 한마디와 다른 도구: 점수와 함께 묻거나, 한마디 문구에 나온 걸음 수가 아니라 오늘 걸음 수를 묻는다
        "synth-08-601": ([score, daily], []),
        "synth-09-601": ([steps], [[steps, daily]]),
        # 다른 도구만: 점수, 걸음 수, 전체 건강 상태를 묻고 한마디는 묻지 않는다
        "synth-10-601": ([score], []),
        "synth-01-602": ([score], []),
        "synth-02-602": ([steps], []),
        "synth-03-602": ([_call("get_health_overview", period="days_ago:0")], []),
        # 지난 날짜·다시 고르기: 오늘 한마디를 조회해 확인한 답과 도구 없이 답한 것(빈 호출 목록) 모두 맞다
        **{item_id: ([daily], [[]]) for item_id in ("synth-08-602", "synth-09-602", "synth-10-602")},
    }
    # "한마디"가 짧은 말이라는 뜻인 요청과 다른 사람에게 보낼 문구는 도구 없이 답한다
    no_tool = {"synth-04-602", "synth-05-602", "synth-06-602", "synth-07-602"}
    assert len(rows) == 20 and set(rows) == set(expected) | no_tool
    for item_id, (calls, acceptable) in expected.items():
        r = rows[item_id]
        assert r["category"] == "tool", item_id
        assert (r["expected"], r["acceptable"]) == ({"type": "tool_calls", "tool_calls": calls}, acceptable), item_id
    for item_id in no_tool:
        r = rows[item_id]
        assert (r["category"], r["expected"], r["acceptable"]) == ("no_tool", {"type": "no_tool"}, []), item_id
    paths = [path for path in DATASETS_DIR.glob("tool_selection_*.jsonl") if path != DAILY_MESSAGE_SET]
    others = {r["question"] for path in paths for r in load(path)}
    # 규칙과 도구 설명만으로 고르는지 확인하는 문항이라 질문을 시스템 프롬프트와 도구 설명에 넣지 않는다
    shown = load_system_prompt() + json.dumps(tool_specs(), ensure_ascii=False)
    for r in rows.values():
        assert re.fullmatch(rf"{r['persona']}-6\d\d", r["id"]), r["id"]
        assert r["question"] not in others and r["question"] not in shown and r["proposed"] == [], r["id"]


def test_sleep_exercise_detail_set_routes_sleep_heart_oxygen_and_session_values():
    rows = {r["id"]: r for r in load(SLEEP_EXERCISE_DETAIL_SET)}

    def sleep(period: str) -> dict:
        return _call("get_sleep_records", period=period)

    def sessions(period: str) -> dict:
        return _call("get_exercise_sessions", period=period)

    def score(period: str) -> dict:
        return _call("get_lifestyle_score", period=period)

    def heart(period: str) -> dict:
        return _call("get_metric_summary", metric="heart_rate", period=period)

    # 묶음마다 정답과 허용 답안 (기준일 2026-10-22 목). 측정 결과를 보기 전에 정했다.
    # 수면은 깬 날 기준이라 어젯밤은 days_ago:0, 그저께 밤은 days_ago:1 이다
    expected = {
        # 자는 동안 심박의 하루·여러 날 값과 최저·최고는 수면 기록이다
        "synth-01-701": ([sleep("days_ago:0")], []),
        "synth-02-701": ([sleep("days_ago:1")], []),
        "synth-03-701": ([sleep("weeks_ago:0")], []),
        "synth-04-701": ([sleep("weeks_ago:1")], []),
        # 안정 시 심박수라는 말 없이 자는 동안 평균 심박을 묻는다. 수면 기록 하나면 점수 결과의 수면 중 평균 심박도 같은
        # 값이라 허용하고, 여러 날은 점수 도구가 하루씩만 조회해 수면 기록만 맞다
        "synth-05-701": ([sleep("days_ago:0")], [[score("days_ago:0")]]),
        "synth-06-701": ([sleep("recent_days:7")], []),
        # 수면 중 산소포화도의 최저·최고와 90% 미만 측정 횟수는 수면 기록에만 있다
        "synth-07-701": ([sleep("days_ago:0")], []),
        "synth-08-701": ([sleep("weeks_ago:0")], []),
        "synth-09-701": ([sleep("days_ago:1")], []),
        "synth-10-701": ([sleep("recent_days:3")], []),
        # 산소포화도 평균은 지금처럼 산소포화도 지표로 조회한다. 수면 기록의 산소포화도 평균도 같은 값이라 허용한다
        "synth-01-702": (
            [_call("get_metric_summary", metric="oxygen_saturation", period="days_ago:0")],
            [[sleep("days_ago:0")]],
        ),
        # 운동 칼로리·거리는 운동 세션 조회다
        "synth-02-702": ([sessions("days_ago:1")], []),
        "synth-03-702": ([sessions("weeks_ago:0")], []),
        "synth-04-702": ([sessions("weeks_ago:1")], []),
        "synth-05-702": ([sessions("recent_days:14")], []),
        # 하루 전체 소모 칼로리는 기록에 없다. 운동 기록 기준 칼로리를 조회해 구분한 답과 도구 없이 확인할 수 없다고
        # 한 답(빈 호출 목록) 모두 맞다
        "synth-01-703": ([sessions("days_ago:0")], [[]]),
        # 홈 화면의 안정 시 심박수는 점수 도구다
        "synth-06-702": ([score("days_ago:0")], []),
        "synth-07-702": ([score("days_ago:1")], []),
        # 하루 평균 심박은 심박 지표다. 여러 날이면 날짜별 하루 평균으로 답해도 맞다
        "synth-08-702": ([heart("days_ago:1")], []),
        "synth-09-702": (
            [heart("weeks_ago:0")],
            [[_call("get_daily_trend", metric="heart_rate", period="weeks_ago:0")]],
        ),
        "synth-10-702": ([heart("days_ago:0")], []),
    }
    assert len(rows) == 21 and set(rows) == set(expected)
    for item_id, (calls, acceptable) in expected.items():
        r = rows[item_id]
        assert r["category"] == "tool", item_id
        assert (r["expected"], r["acceptable"]) == ({"type": "tool_calls", "tool_calls": calls}, acceptable), item_id
    paths = [path for path in DATASETS_DIR.glob("tool_selection_*.jsonl") if path != SLEEP_EXERCISE_DETAIL_SET]
    others = {r["question"] for path in paths for r in load(path)}
    # 규칙과 도구 설명만으로 고르는지 확인하는 문항이라 질문을 시스템 프롬프트와 도구 설명에 넣지 않는다
    shown = load_system_prompt() + json.dumps(tool_specs(), ensure_ascii=False)
    for r in rows.values():
        assert re.fullmatch(rf"{r['persona']}-7\d\d", r["id"]), r["id"]
        assert r["question"] not in others and r["question"] not in shown and r["proposed"] == [], r["id"]
        # 안정 시 심박수 문항만 그 말을 쓴다
        assert ("안정 시 심박수" in r["question"]) == (r["id"] in ("synth-06-702", "synth-07-702")), r["id"]
