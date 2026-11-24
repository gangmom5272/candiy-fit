import copy
import json
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from coach_ai.adapters.memory import ExerciseSession, StepDay, SyncState
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall, ToolRequest, tool_specs
from coach_ai.agent.nodes.tools import TOOL_STATUS
from coach_ai.ports import HealthDataUnavailableError, MetricResult, ResultContractError
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import ToolGateway
from coach_ai.tools import handlers
from coach_ai.tools.handlers import SESSION_LIST_LIMIT, get_exercise_sessions, get_health_overview, get_metric_summary
from coach_ai.tools.results import ExerciseSessions
from coach_ai.tools.schemas import TOOL_INPUTS, GetExerciseSessions, GetHealthOverview, GetMetricSummary
from tests.fakes import COMMON_RESULTS, NOW, TODAY, D, FakeHealthData, SlowPort, at, request

CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
RUN = (56, "달리기", "cardio")
WALK = (79, "걷기", "cardio")
STRENGTH = (70, "근력 운동", "strength")
WEIGHT = (81, "웨이트 트레이닝", "strength")
OTHER = (0, "기타 운동", "other")
# 걸음 수의 기준 시각. 회원의 동기화 기준 시각이 된다
SYNCED = at(15, 12, 5)
# 동기화 기준 시각이 오늘보다 이를 때 붙는 사실 문장
NOT_TODAY = "이 시각은 오늘이 아니고, 오늘 기록은 아직 반영되지 않았다"


def session(d: int, start: tuple[int, int], minutes: int, kind=RUN, counted=True, **extra) -> ExerciseSession:
    """10월 d일에 시작한 운동 한 번. kind 는 (코드, 기준표 이름, 분류)다.

    기준표에 없는 코드는 이름·분류가 None 이다.
    """
    begin = at(d, *start)
    return ExerciseSession(D(2026, 10, d), begin, begin + timedelta(minutes=minutes), minutes, *kind, counted, **extra)


def port(*sessions: ExerciseSession, days=None, synced=SYNCED) -> FakeHealthData:
    """세션이 있는 날과 오늘(15일)에 활동 행이 있다. 동기화 기준 시각은 걸음 수의 기준 시각(synced)이다."""
    activity = sorted({s.date for s in sessions} | {TODAY}) if days is None else [D(2026, 10, d) for d in days]
    sync = {"exercise": SyncState(max((s.end_at for s in sessions), default=None))}
    if synced is not None:
        sync["steps"] = SyncState(synced)
    return FakeHealthData(
        steps={"u1": [StepDay(d, 5000) for d in activity]},
        exercise={"u1": list(sessions)},
        sync={"u1": sync},
        linked_from={"u1": D(2026, 9, 20)},
        now=NOW,
    )


async def run(p, period="last_7_days"):
    return await get_exercise_sessions(GetExerciseSessions(period=period), request(p))


# 10일·13일은 활동 기록이 없고, 14일 달리기와 12일 근력 운동은 거리 기록이 없다
WEEK = (
    session(9, (7, 0), 30, distance_m=5000.0, calories_kcal=300.0, avg_heart_rate=150.0, max_heart_rate=171),
    session(11, (7, 0), 40, distance_m=6234.5, calories_kcal=410.4, avg_heart_rate=155.5, max_heart_rate=178),
    session(12, (19, 0), 50, STRENGTH, calories_kcal=200.0, avg_heart_rate=120.0),
    session(14, (8, 0), 25, calories_kcal=250.0),
)
DISTANCE_NOTE = "운동으로 인정된 세션 중 거리 기록이 있는 세션(distance_sessions)의 거리만 더했다."
CALORIE_DISTANCE_NOTE = (
    "칼로리와 거리는 운동 세션에 기록된 값과 그 합이고, 하루 전체 소모 칼로리나 하루 동안 이동한 거리가 아니다."
)
TYPE_NOTE = (
    "종류(types)는 서로 다른 운동이다. "
    "질문한 이름과 같은 이름의 종류가 있으면 그 종류의 값만 답한다. "
    "여러 종류에 걸치는 이름(예: 헬스)은 결과에 해당하는 종류가 둘 이상이면 종류별로 나눠 말하고 어느 것인지 묻는다. "
    "종류 값을 더한 숫자를 새로 만들지 않고, 전체 합계가 필요하면 total 값을 쓴다."
)


def answer_note(basis: str) -> str:
    """수치를 말할 때 같은 문장에 붙일 기록 기준 안내. basis 는 total.display 의 기록 일수와 오늘 기준 시각이다."""
    return (
        f"횟수·시간·거리·칼로리를 말할 때는 total.display 의 기록 기준({basis})을 같은 문장에 붙인다. "
        "종류별 값으로 답할 때도 같다."
    )


async def test_types_sum_count_time_distance_and_calories_per_kind():
    out = await run(port(*WEEK))
    assert out.status == "ok"
    assert list(out.payload) == [
        "metric", "period", "total", "answer_note", "types", "type_note", "distance_note", "calorie_distance_note",
        "sessions", "not_counted_sessions",
    ]  # fmt: skip
    assert out.payload["period"] == {
        "period": "last_7_days", "start": "2026-10-09", "end": "2026-10-15", "basis": "날짜(KST)"
    }  # fmt: skip
    # 거리·칼로리는 기록이 있는 세션만 더하고 한 번만 반올림한다 (11,234.5m, 1,160.4kcal)
    assert out.payload["total"] == {
        "display": "지난 7일 운동 4회 2시간 25분 (6일 중 활동 기록이 있는 날 4일), 오늘 12:05까지 기록 기준",
        "count": 4, "duration": "2시간 25분", "distance": "11.23km (운동 기록 기준)", "distance_sessions": 2,
        "calories": "1,160kcal",
    }  # fmt: skip
    assert out.payload["answer_note"] == answer_note("6일 중 활동 기록이 있는 날 4일, 오늘 12:05까지 기록 기준")
    assert out.payload["types"] == [
        {"type": "달리기", "count": 3, "duration": "1시간 35분", "distance": "11.23km (운동 기록 기준)",
         "distance_sessions": 2, "calories": "960kcal"},
        {"type": "근력 운동", "count": 1, "duration": "50분", "distance_sessions": 0, "calories": "200kcal"},
    ]  # fmt: skip
    assert out.payload["type_note"] == TYPE_NOTE
    assert out.payload["distance_note"] == DISTANCE_NOTE
    assert out.payload["not_counted_sessions"] == 0


async def test_type_note_comes_only_with_two_or_more_kinds():
    # 근력 운동과 웨이트 트레이닝은 기준표에서 서로 다른 종류다
    mixed = await run(port(session(12, (19, 0), 40, STRENGTH), session(13, (19, 0), 50, WEIGHT),
                           session(14, (19, 0), 45, WEIGHT)))  # fmt: skip
    assert [(t["type"], t["count"]) for t in mixed.payload["types"]] == [("웨이트 트레이닝", 2), ("근력 운동", 1)]
    assert mixed.payload["type_note"] == TYPE_NOTE
    # 종류가 하나뿐이거나 인정 세션이 없으면 합치거나 고를 종류가 없어 넣지 않는다
    single = await run(port(session(13, (19, 0), 50, WEIGHT), session(14, (19, 0), 45, WEIGHT)))
    not_counted = await run(port(session(14, (8, 0), 20, WALK, False)))
    empty = await run(port(days=range(9, 16)))
    for out in (single, not_counted, empty):
        assert "type_note" not in out.payload


async def test_same_kind_sessions_on_one_day_count_each_by_stored_decision():
    # 미인정 걷기는 평균 속도가 빨라도 저장된 인정 여부를 따른다
    out = await run(port(
        session(14, (7, 0), 20), session(14, (18, 0), 30),
        session(14, (12, 0), 15, WALK, False, avg_speed_mps=1.5),
    ))  # fmt: skip
    assert out.payload["types"] == [
        {"type": "달리기", "count": 2, "duration": "50분", "distance_sessions": 0, "missing": "칼로리 기록 없음"}
    ]
    assert (out.payload["total"]["count"], out.payload["total"]["duration"]) == (2, "50분")
    assert out.payload["not_counted_sessions"] == 1
    assert [(s["start"], s.get("counts_as_exercise")) for s in out.payload["sessions"]] == [
        ("18:00", None), ("12:00", False), ("07:00", None),
    ]  # fmt: skip


async def test_session_belongs_to_kst_start_date_across_midnight():
    # 13일 23:40에 시작해 14일 00:20에 끝난 달리기는 13일, 14일 00:10(UTC 13일 15:10) 근력 운동은 14일이다
    p = port(session(13, (23, 40), 40), session(14, (0, 10), 30, STRENGTH))
    yesterday = await run(p, "yesterday")
    assert [(t["type"], t["count"], t["duration"]) for t in yesterday.payload["types"]] == [("근력 운동", 1, "30분")]
    assert [(s["date"], s["start"]) for s in yesterday.payload["sessions"]] == [("2026-10-14", "00:10")]
    week = await run(p)
    assert [(t["type"], t["count"]) for t in week.payload["types"]] == [("달리기", 1), ("근력 운동", 1)]
    assert [(s["date"], s["start"], s["type"]) for s in week.payload["sessions"]] == [
        ("2026-10-14", "00:10", "근력 운동"), ("2026-10-13", "23:40", "달리기"),
    ]  # fmt: skip


async def test_sessions_are_listed_latest_first_with_display_values():
    out = await run(port(*WEEK))
    assert out.payload["sessions"] == [
        {"date": "2026-10-14", "start": "08:00", "type": "달리기", "duration": "25분", "calories": "250kcal"},
        {"date": "2026-10-12", "start": "19:00", "type": "근력 운동", "duration": "50분", "calories": "200kcal",
         "avg_heart_rate": "120bpm"},
        {"date": "2026-10-11", "start": "07:00", "type": "달리기", "duration": "40분",
         "distance": "6.23km (운동 기록 기준)", "calories": "410kcal", "avg_heart_rate": "155.5bpm",
         "max_heart_rate": "178bpm"},
        {"date": "2026-10-09", "start": "07:00", "type": "달리기", "duration": "30분",
         "distance": "5km (운동 기록 기준)", "calories": "300kcal", "avg_heart_rate": "150bpm",
         "max_heart_rate": "171bpm"},
    ]  # fmt: skip


async def test_codes_outside_code_table_are_grouped_with_other_exercise():
    out = await run(port(session(12, (10, 0), 30, OTHER), session(13, (10, 0), 20, (9999, None, None)),
                         session(14, (10, 0), 15, (84, None, None))))  # fmt: skip
    assert out.payload["types"] == [
        {
            "type": "기타 운동",
            "count": 3,
            "duration": "1시간 5분",
            "distance_sessions": 0,
            "missing": "칼로리 기록 없음",
        }
    ]
    assert [s["type"] for s in out.payload["sessions"]] == ["기타 운동"] * 3
    # 근거 원형에는 저장된 코드가 그대로 있다
    [evidence] = out.evidence
    assert [s["exercise_type"] for s in evidence.snapshot["detail"]["sessions"]] == [0, 9999, 84]


async def test_sessions_outside_exercise_time_are_listed_but_left_out_of_totals():
    out = await run(port(
        session(13, (8, 0), 20, WALK, False, distance_m=0.0, calories_kcal=60.0, avg_heart_rate=95.0),
        session(14, (18, 0), 30, WALK, distance_m=2600.0, calories_kcal=120.0),
        session(14, (19, 0), 15, WALK, False),
    ))  # fmt: skip
    walk = {"count": 1, "duration": "30분", "distance": "2.6km (운동 기록 기준)", "distance_sessions": 1}
    assert out.payload["types"] == [{"type": "걷기", **walk, "calories": "120kcal"}]
    assert out.payload["total"] == {
        "display": "지난 7일 운동 1회 30분 (6일 중 활동 기록이 있는 날 2일), 오늘 12:05까지 기록 기준",
        **walk,
        "calories": "120kcal",
    }
    # 운동으로 인정되지 않은 세션의 거리도 운동 기록의 값이라 같은 기준을 붙인다
    assert out.payload["sessions"] == [
        {"date": "2026-10-14", "start": "19:00", "type": "걷기", "duration": "15분", "counts_as_exercise": False},
        {"date": "2026-10-14", "start": "18:00", "type": "걷기", "duration": "30분",
         "distance": "2.6km (운동 기록 기준)", "calories": "120kcal"},
        {"date": "2026-10-13", "start": "08:00", "type": "걷기", "duration": "20분",
         "distance": "0km (운동 기록 기준)", "calories": "60kcal", "avg_heart_rate": "95bpm",
         "counts_as_exercise": False},
    ]  # fmt: skip
    assert out.payload["not_counted_sessions"] == 2
    assert out.payload["not_counted_note"] == (
        "운동으로 인정되지 않은 세션 2회(걷기 2회)는 합계에서 뺐다. 목록에는 counts_as_exercise 가 false 로 표시된다."
    )


async def test_only_sessions_outside_exercise_time_have_no_totals():
    out = await run(port(session(14, (8, 0), 20, WALK, False), session(13, (9, 0), 10, (8, "자전거", "cardio"), False)))
    assert out.payload["types"] == [] and "total" not in out.payload and "no_record" not in out.payload
    assert len(out.payload["sessions"]) == 2 and "distance_note" not in out.payload
    assert out.payload["not_counted_sessions"] == 2
    assert out.payload["not_counted_note"].startswith("운동으로 인정되지 않은 세션 2회(걷기 1회, 자전거 1회)는")
    assert out.evidence[0].summary.startswith("지난 7일 운동으로 인정된 기록 없음 (")
    # 합계 문구가 없어 기록 일수와 오늘 진행 안내는 따로 두고, 답변 안내도 넣지 않는다
    assert out.payload["coverage"] == "6일 중 활동 기록이 있는 날 2일"
    assert out.payload["in_progress_note"] == "오늘은 진행 중이라 12:05까지 기록 기준이다"
    assert "answer_note" not in out.payload


async def test_missing_distance_or_calories_is_written_and_stored_zero_is_a_record():
    out = await run(port(session(12, (7, 0), 30, distance_m=0.0), session(13, (7, 0), 30, calories_kcal=100.0),
                         session(14, (19, 0), 40, STRENGTH)))  # fmt: skip
    assert out.payload["types"] == [
        {"type": "달리기", "count": 2, "duration": "1시간", "distance": "0km (운동 기록 기준)",
         "distance_sessions": 1, "calories": "100kcal", "missing": "칼로리 기록 없는 세션 1회"},
        {"type": "근력 운동", "count": 1, "duration": "40분", "distance_sessions": 0, "missing": "칼로리 기록 없음"},
    ]  # fmt: skip
    total = out.payload["total"]
    assert (total["distance_sessions"], total["missing"]) == (1, "칼로리 기록 없는 세션 2회")


@pytest.mark.parametrize(
    ("distances", "expected"),
    [
        ([None, 0.0, 1200.5], {"count": 3, "distance": "1.2km (운동 기록 기준)", "distance_sessions": 2}),
        ([None, None], {"count": 2, "distance_sessions": 0}),
        ([0.0], {"count": 1, "distance": "0km (운동 기록 기준)", "distance_sessions": 1}),
    ],
    ids=["null_zero_value", "all_null", "stored_zero"],
)
async def test_distance_adds_recorded_values_of_counted_sessions_only(distances, expected):
    runs = [session(9 + i, (7, 0), 30, distance_m=m) for i, m in enumerate(distances)]
    # 미인정 걷기의 거리 500m 는 합계에 넣지 않는다
    out = await run(port(*runs, session(14, (18, 0), 20, WALK, False, distance_m=500.0)))
    keys = ("count", "distance", "distance_sessions")
    [kind] = out.payload["types"]
    assert {k: v for k, v in kind.items() if k in keys} == expected
    assert {k: v for k, v in out.payload["total"].items() if k in keys} == expected
    assert ("distance_note" in out.payload) == ("distance" in expected)
    assert out.payload["not_counted_sessions"] == 1
    assert out.payload["sessions"][0]["distance"] == "0.5km (운동 기록 기준)"


async def test_list_limit_changes_neither_totals_nor_not_counted_count(monkeypatch):
    # 하루 한 번 운동하는 사람의 한 주 기록을 모두 담는 상한이다
    assert SESSION_LIST_LIMIT == 8
    # 2~4일 미인정 걷기 셋은 목록 상한 밖이다
    walks = [session(d, (18, 0), 20, WALK, False) for d in (2, 3, 4)]
    sessions = [*(session(d, (7, 0), 30) for d in range(1, 15)), *walks]
    limited = await run(port(*sessions), "last_30_days")
    listed = limited.payload["sessions"]
    assert [s["date"] for s in listed] == [f"2026-10-{d:02d}" for d in range(14, 14 - SESSION_LIST_LIMIT, -1)]
    assert limited.payload["omitted_sessions"] == 17 - SESSION_LIST_LIMIT
    assert not any("counts_as_exercise" in s for s in listed)
    assert (limited.payload["total"]["count"], limited.payload["total"]["duration"]) == (14, "7시간")
    assert limited.payload["not_counted_sessions"] == 3
    monkeypatch.setattr(handlers, "SESSION_LIST_LIMIT", 100)
    full = await run(port(*sessions), "last_30_days")
    assert len(full.payload["sessions"]) == 17 and "omitted_sessions" not in full.payload
    for key in ("total", "types", "not_counted_sessions", "not_counted_note"):
        assert limited.payload[key] == full.payload[key], key


async def test_no_sessions_says_no_record_with_coverage():
    out = await run(port(days=range(9, 16)))
    assert (out.payload["types"], out.payload["sessions"]) == ([], [])
    assert out.payload["no_record"] == "기간 안에 시작한 운동 기록 없음"
    assert out.payload["coverage"] == "6일 중 활동 기록이 있는 날 6일"
    assert out.evidence[0].summary == (
        "지난 7일 운동 기록 없음 (6일 중 활동 기록이 있는 날 6일), 오늘 12:05까지 기록 기준"
    )


async def test_no_sessions_keeps_link_and_sync_state():
    # 11일부터 동기화 전이고 동기화 기준 시각이 오늘이 아니다
    unsynced = await run(port(days=[9, 10], synced=at(11, 9, 0)))
    assert unsynced.payload["coverage"] == "6일 중 활동 기록이 있는 날 2일, 동기화 전 4일"
    # 오늘보다 이른 기준 시각은 "오늘"과 다른 문장에 둔다
    assert unsynced.payload["in_progress_note"] == f"오늘은 진행 중이다. 10월 11일 09:00까지 기록 기준이다. {NOT_TODAY}"
    assert unsynced.evidence[0].summary == (
        "지난 7일 운동 기록 없음 (6일 중 활동 기록이 있는 날 2일, 동기화 전 4일). "
        f"10월 11일 09:00까지 기록 기준이다. {NOT_TODAY}"
    )
    nobody = await run(FakeHealthData(now=NOW))
    assert nobody.payload["coverage"] == "연동 후 받은 기록 없음"
    assert nobody.payload["no_record"] == "기간 안에 시작한 운동 기록 없음"
    assert nobody.payload["in_progress_note"] == "오늘은 진행 중이라 기록이 더 들어올 수 있다"


async def test_total_text_carries_coverage_and_today_basis():
    p = port(*WEEK, session(15, (9, 0), 30))
    # 오늘이 든 기간은 오늘 시작한 세션도 세고, 합계 문구에 기록 일수와 오늘 기준 시각을 붙인다
    week = await run(p, "this_week")
    assert week.payload["sessions"][0] == {"date": "2026-10-15", "start": "09:00", "type": "달리기", "duration": "30분"}
    assert week.payload["total"]["display"] == (
        "이번 주 운동 3회 1시간 45분 (3일 중 활동 기록이 있는 날 2일), 오늘 12:05까지 기록 기준"
    )
    # 완성된 날이 없는 오늘은 기록 일수 없이 기준 시각만 붙인다
    today = await run(p, "today")
    assert today.payload["total"]["display"] == "오늘 운동 1회 30분, 오늘 12:05까지 기록 기준"
    # 오늘이 없는 기간은 기록 일수만 붙인다
    yesterday = await run(p, "yesterday")
    assert yesterday.payload["total"]["display"] == "어제 운동 1회 25분 (1일 중 활동 기록이 있는 날 1일)"
    for out in (week, today, yesterday):
        assert "coverage" not in out.payload and "in_progress_note" not in out.payload
        # 근거 summary 는 같은 형식의 문구를 그대로 쓴다
        assert out.evidence[0].summary == out.payload["total"]["display"]
    # 답변 안내에는 합계 문구의 기록 일수와 오늘 기준 시각을 함께 넣는다.
    # 오늘만 있거나 오늘이 없는 기간은 있는 것만 넣는다
    assert week.payload["answer_note"] == answer_note("3일 중 활동 기록이 있는 날 2일, 오늘 12:05까지 기록 기준")
    assert today.payload["answer_note"] == answer_note("오늘 12:05까지 기록 기준")
    assert yesterday.payload["answer_note"] == answer_note("1일 중 활동 기록이 있는 날 1일")
    # 동기화 기준 시각이 없으면 오늘 기록이 더 들어올 수 있다고 붙이고, 근거 summary 에는 붙이지 않는다.
    # 기준 시각이 없으면 기록이 없는 13일은 동기화 전으로 센다
    p.sync["u1"] = {}
    unknown = await run(p, "this_week")
    head = "이번 주 운동 3회 1시간 45분 (3일 중 활동 기록이 있는 날 2일, 동기화 전 1일)"
    assert unknown.payload["total"]["display"] == f"{head}, 오늘은 진행 중이라 기록이 더 들어올 수 있다"
    assert unknown.evidence[0].summary == head
    assert unknown.payload["answer_note"] == answer_note(
        "3일 중 활동 기록이 있는 날 2일, 동기화 전 1일, 오늘은 진행 중이라 기록이 더 들어올 수 있다"
    )


async def test_total_text_says_basis_time_is_not_today_when_sync_is_late():
    # 14일 23:30 뒤로 받은 기록이 없다. 그 시각은 합계 문장과 다른 문장에 두고, 오늘이 아니라는 문장을 뒤에 붙인다
    p = port(*WEEK, days=[9, 11, 12, 14], synced=at(14, 23, 30))
    week = await run(p)
    line = "지난 7일 운동 4회 2시간 25분 (6일 중 활동 기록이 있는 날 4일)"
    assert week.payload["total"]["display"] == f"{line}. 10월 14일 23:30까지 기록 기준이다. {NOT_TODAY}"
    assert week.evidence[0].summary == week.payload["total"]["display"]
    # 같은 문장에 붙일 기록 기준에는 기록 일수와 기준 시각만 넣는다
    assert week.payload["answer_note"] == answer_note("6일 중 활동 기록이 있는 날 4일, 10월 14일 23:30까지 기록 기준")
    # 기간 이름에 "오늘"이 든 기간도 합계 문장과 기준 시각을 나눈다
    span = await run(p, "days_ago:1..days_ago:0")
    head = "어제부터 오늘까지 (10월 14일-15일) 운동 1회 25분 (1일 중 활동 기록이 있는 날 1일)"
    assert span.payload["total"]["display"] == f"{head}. 10월 14일 23:30까지 기록 기준이다. {NOT_TODAY}"
    # 오늘이 없는 기간은 기준 시각을 붙이지 않는다
    yesterday = await run(p, "yesterday")
    assert yesterday.payload["total"]["display"] == "어제 운동 1회 25분 (1일 중 활동 기록이 있는 날 1일)"


async def test_result_model_rejects_misplaced_total_text_and_type_note():
    two = (await run(port(*WEEK))).payload
    one = (await run(port(session(14, (8, 0), 25)))).payload
    walk = (await run(port(session(14, (8, 0), 20, WALK, False)))).payload
    changes = [
        (two, lambda r: r["total"].pop("display")),
        (two, lambda r: r["types"][0].update(display="지난 7일 달리기 3회")),
        (two, lambda r: r.update(coverage="6일 중 활동 기록이 있는 날 4일")),
        (two, lambda r: r.update(in_progress_note="오늘은 진행 중이라 12:05까지 기록 기준이다")),
        (two, lambda r: r.pop("type_note")),
        (one, lambda r: r.update(type_note=TYPE_NOTE)),
        (one, lambda r: r.pop("answer_note")),
        (walk, lambda r: r.update(answer_note=answer_note("6일 중 활동 기록이 있는 날 1일"))),
    ]
    for base, change in changes:
        ExerciseSessions.model_validate(base)
        payload = copy.deepcopy(base)
        change(payload)
        with pytest.raises(ValidationError):
            ExerciseSessions.model_validate(payload)


async def test_other_tools_keep_coverage_and_today_progress_in_own_fields():
    p = port(*WEEK, session(15, (9, 0), 30))
    summary = await get_metric_summary(GetMetricSummary(metric="exercise", period="this_week"), request(p))
    expected = {
        "metric": "exercise",
        "period": {"period": "this_week", "start": "2026-10-12", "end": "2026-10-15", "basis": "날짜(KST)"},
        "unit": "min", "measured_during": None,
        # 완성된 3일 중 기록이 있는 12일 50분, 14일 25분. 평균 37.5분은 반올림해 38분이다
        "avg": {"value": 38.0, "display": "38분"},
        "lowest_day": {"value": 25.0, "display": "25분", "date": "2026-10-14"},
        "highest_day": {"value": 50.0, "display": "50분", "date": "2026-10-12"},
        "measured_min": None, "measured_max": None,
        "total": {"value": 75.0, "display": "1시간 15분"},
        "days_with_data": 2, "completed_days": 3, "missing_days": ["2026-10-13"], "unsynced_days": [],
        "linked_from": "2026-09-20",
        "coverage": "3일 중 활동 기록이 있는 날 2일",
        "in_progress": {"date": "2026-10-15", "value": 30.0, "display": "오늘 진행 중 30분 (12:05까지 기록 기준)",
                        "as_of": "2026-10-15T12:05:00+09:00"},
    }  # fmt: skip
    expected["record_note"] = (
        "기록 범위: 3일 중 활동 기록이 있는 날 2일. 기록 없는 날은 평균에 0으로 포함되지 않는다. "
        "실제 활동량 감소나 기록 누락 원인은 이 결과만으로 확인할 수 없다."
    )
    assert summary.payload == {
        **expected,
        "exercise_activity_note": "추가 운동 수치(횟수·칼로리·거리)를 제공받지 못했다. "
        "0이나 운동 기록 없음이라는 뜻이 아니다.",
    }
    assert summary.evidence[0].summary == (
        "이번 주 운동 시간 평균 38분 (3일 중 활동 기록이 있는 날 2일), 오늘 진행 중 30분 (12:05까지 기록 기준)"
    )
    overview = await get_health_overview(GetHealthOverview(period="this_week"), request(p))
    assert overview.payload["exercise"] == {k: v for k, v in expected.items() if v is not None}


async def test_evidence_is_exercise_prototype_as_is():
    p = port(*WEEK)
    out = await run(p)
    [evidence] = out.evidence
    assert (evidence.tool, evidence.metric) == ("get_exercise_sessions", "exercises")
    assert evidence.period.model_dump() == {
        "period": "last_7_days", "start": D(2026, 10, 9), "end": TODAY, "basis": "날짜(KST)"
    }  # fmt: skip
    summary = await get_metric_summary(GetMetricSummary(metric="exercise", period="last_7_days"), request(p))
    assert evidence.snapshot == summary.evidence[0].snapshot
    assert evidence.summary == "지난 7일 운동 4회 2시간 25분 (6일 중 활동 기록이 있는 날 4일), 오늘 12:05까지 기록 기준"


class FixedPort:
    """연동 모듈의 계산 함수가 만든 원형을 그대로 반환한다."""

    def __init__(self, case: str) -> None:
        self.result = MetricResult(COMMON_RESULTS[case]["user_id"], COMMON_RESULTS[case]["snapshot"])

    async def get_metric_result(self, user_id, metric, start, end):
        return self.result


async def test_reads_sessions_of_prototype_made_by_common_query():
    fixed = FixedPort("week_exercise")
    req = ToolRequest(user_id=fixed.result.user_id, today=date(2026, 10, 22), port=fixed)
    out = await get_exercise_sessions(GetExerciseSessions(period="last_7_days"), req)
    # 기준표에 없는 코드 84, 느린 걷기 두 번(거리 0, 소수), 거리 없는 근력 운동이 들어 있다
    assert out.payload == {
        "metric": "exercise",
        "period": {"period": "last_7_days", "start": "2026-10-16", "end": "2026-10-22", "basis": "날짜(KST)"},
        "total": {"display": "지난 7일 운동 3회 2시간 2분 (6일 중 활동 기록이 있는 날 5일), 오늘 13:53까지 기록 기준",
                  "count": 3, "duration": "2시간 2분", "distance": "5.57km (운동 기록 기준)", "distance_sessions": 1,
                  "calories": "491kcal", "missing": "칼로리 기록 없는 세션 1회"},
        "answer_note": answer_note("6일 중 활동 기록이 있는 날 5일, 오늘 13:53까지 기록 기준"),
        "types": [
            {"type": "기타 운동", "count": 1, "duration": "50분", "distance_sessions": 0,
             "missing": "칼로리 기록 없음"},
            {"type": "근력 운동", "count": 1, "duration": "40분", "distance_sessions": 0, "calories": "180kcal"},
            {"type": "달리기", "count": 1, "duration": "32분", "distance": "5.57km (운동 기록 기준)",
             "distance_sessions": 1, "calories": "311kcal"},
        ],
        "type_note": TYPE_NOTE,
        "distance_note": DISTANCE_NOTE,
        "calorie_distance_note": CALORIE_DISTANCE_NOTE,
        "sessions": [
            {"date": "2026-10-21", "start": "19:00", "type": "근력 운동", "duration": "40분", "calories": "180kcal",
             "avg_heart_rate": "118.5bpm", "max_heart_rate": "142bpm"},
            {"date": "2026-10-19", "start": "08:05", "type": "걷기", "duration": "20분",
             "distance": "1.1km (운동 기록 기준)", "calories": "58kcal", "avg_heart_rate": "94bpm",
             "max_heart_rate": "104bpm", "counts_as_exercise": False},
            {"date": "2026-10-17", "start": "18:20", "type": "걷기", "duration": "22분",
             "distance": "0km (운동 기록 기준)", "calories": "65kcal", "avg_heart_rate": "96bpm",
             "max_heart_rate": "108bpm", "counts_as_exercise": False},
            {"date": "2026-10-17", "start": "10:00", "type": "기타 운동", "duration": "50분"},
            {"date": "2026-10-16", "start": "07:10", "type": "달리기", "duration": "32분",
             "distance": "5.57km (운동 기록 기준)", "calories": "311kcal", "avg_heart_rate": "152bpm",
             "max_heart_rate": "171bpm"},
        ],
        "not_counted_sessions": 2,
        "not_counted_note": "운동으로 인정되지 않은 세션 2회(걷기 2회)는 합계에서 뺐다. "
        "목록에는 counts_as_exercise 가 false 로 표시된다.",
    }  # fmt: skip


class SessionChangedPort(FakeHealthData):
    """원형의 첫 세션 항목을 고쳐 반환한다."""

    def __init__(self, base: FakeHealthData, change) -> None:
        super().__init__(**vars(base))
        self.change = change

    async def get_metric_result(self, user_id, metric, start, end):
        result = await super().get_metric_result(user_id, metric, start, end)
        snapshot = copy.deepcopy(dict(result.snapshot))
        self.change(snapshot["detail"]["sessions"][0])
        return MetricResult(result.user_id, snapshot)


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.pop("type_name"),
        lambda s: s.update(start_at="2026-10-09T07:00:00"),
        lambda s: s.update(counts_as_exercise=None),
        lambda s: s.update(date="2026-10-01"),
        lambda s: s.update(max_heart_rate="빠름"),
    ],
    ids=["no_type_name", "naive_start", "no_counted", "outside_period", "max_heart_rate_text"],
)
async def test_malformed_session_is_contract_error_only_for_session_tool(change):
    p = SessionChangedPort(port(*WEEK), change)
    with pytest.raises(ResultContractError):
        await run(p)
    # 세션을 읽지 않는 도구의 결과는 그대로다
    out = await get_metric_summary(GetMetricSummary(metric="exercise", period="last_7_days"), request(p))
    assert out.status == "ok"


async def test_gateway_runs_tool_and_rejects_kind_filter_argument():
    audit = MemoryAuditSink()
    gateway = ToolGateway(port(*WEEK), TODAY, audit=audit)
    calls = [
        ToolCall("c1", "get_exercise_sessions", {"period": "last_7_days"}),
        ToolCall("c2", "get_exercise_sessions", {"period": "this_week", "exercise_type": 56}),
    ]
    ok, denied = await gateway.execute(calls, CTX)
    body = json.loads(ok.content)
    assert body["evidence_ids"] == ["E1"] and body["result"]["total"]["count"] == 4
    assert [e.metric for e in ok.evidence] == ["exercises"]
    assert denied.content == "이 요청은 처리할 수 없습니다"
    assert [(r.tool_name, r.decision, r.reason, r.result_count) for r in audit.records()] == [
        ("get_exercise_sessions", "allow", None, 7),
        ("get_exercise_sessions", "deny", "extra_field", 0),
    ]


async def test_gateway_timeout_names_exercise_metric():
    gateway = ToolGateway(SlowPort(port(*WEEK), delay=0.5), TODAY, timeout=0.05)
    with pytest.raises(HealthDataUnavailableError) as e:
        await gateway.execute([ToolCall("c1", "get_exercise_sessions", {"period": "last_7_days"})], CTX)
    assert e.value.metrics == ("exercise",)


def test_tool_takes_only_period_like_other_tools_and_has_status_message():
    [spec] = [s for s in tool_specs() if s["function"]["name"] == "get_exercise_sessions"]
    properties = spec["function"]["parameters"]["properties"]
    assert list(properties) == ["period"]
    assert properties["period"] == tool_specs()[0]["function"]["parameters"]["properties"]["period"]
    assert set(TOOL_STATUS) == set(TOOL_INPUTS)
