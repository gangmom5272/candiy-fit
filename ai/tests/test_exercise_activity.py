import copy
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from coach_ai.adapters.exercise_activity import ExerciseActivityDay
from coach_ai.adapters.memory import SyncState
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall
from coach_ai.ports import MetricResult, ResultContractError
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import ToolGateway
from coach_ai.tools.exercise_activity import UNAVAILABLE, _value, extend
from coach_ai.tools.handlers import (
    compare_periods,
    get_daily_trend,
    get_exercise_sessions,
    get_health_overview,
    get_metric_summary,
)
from coach_ai.tools.schemas import (
    ComparePeriods,
    GetDailyTrend,
    GetExerciseSessions,
    GetHealthOverview,
    GetMetricSummary,
)
from eval.golden.fixtures import FIXTURES_DIR, compare_with_truth, load_fixture, load_truth
from tests.fakes import NOW, TODAY, request
from tests.test_exercise_sessions import WALK, port, session


def activity_port():
    p = port(
        session(11, (8, 0), 30, calories_kcal=200, distance_m=3000),
        session(11, (10, 0), 20, WALK, counted=False, calories_kcal=40, distance_m=1200),
        session(12, (10, 0), 20, WALK, counted=False, calories_kcal=40, distance_m=1200),
        days=[11, 12, 13, 15],
    )
    p.exercise_activity["u1"] = [
        ExerciseActivityDay(TODAY.replace(day=d), minutes, count, calories, distance, NOW)
        for d, minutes, count, calories, distance in (
            (11, 30, 1, Decimal(240), Decimal(4200)),
            (12, 0, 0, Decimal(40), Decimal(1200)),
            (13, 0, 0, Decimal(0), None),
            (15, 0, 0, None, Decimal(50)),
        )
    ]
    p.exercise_activity_statuses["u1"] = {
        name: {
            "ready": False if name == "calories_burned" else None,
            "last_sync_status": "fail" if name == "calories_burned" else None,
            "last_data_at": None,
            "last_success_at": None,
        }
        for name in ("exercise_sessions", "calories_burned", "distances")
    }
    return p


async def test_summary_uses_field_counts_and_keeps_today_separate():
    p = activity_port()
    result = await get_metric_summary(GetMetricSummary(metric="exercise", period="last_7_days"), request(p))
    extra = result.payload["exercise_activity"]
    assert extra["summary"]["exercise_count"]["total"]["value"] == 1
    assert extra["summary"]["exercise_count"]["total"]["display"] == "1회"
    assert extra["summary"]["exercise_calories_kcal"]["total"]["value"] == 280
    assert extra["summary"]["exercise_calories_kcal"]["days_with_data"] == 3
    assert extra["summary"]["exercise_distance_m"]["days_with_data"] == 2
    assert extra["summary"]["exercise_distance_m"]["total"] == {"value": 5400, "display": "5.4km (미인정 포함)"}
    assert extra["summary"]["exercise_distance_m"]["avg"] == {
        "value": 2700,
        "display": "2.7km (미인정 포함) (기록 2일)",
    }
    assert extra["summary"]["exercise_calories_kcal"]["avg"]["display"] == "93kcal (미인정 포함) (기록 3일)"
    assert extra["summary"]["exercise_count"]["avg"] == {"value": 0, "display": "0회 (기록 3일)"}
    assert extra["summary"]["exercise_minutes"]["avg"]["display"] == "10분 (기록 3일)"
    assert extra["in_progress"][0]["exercise_distance_m"]["display"] == "0.1km (미인정 포함)"
    today = extra["in_progress"][0]
    assert today["exercise_count"] == {"value": 0, "display": "0회"}
    assert today["exercise_calories_kcal"] is None
    assert set(today["missing"]) == {"exercise_calories_kcal"}
    assert today["missing"]["exercise_calories_kcal"].startswith("아직 동기화")
    assert "이전에 저장된 기록 기준" in extra["basis"]
    note = result.payload["record_note"]
    assert note.startswith("칼로리 수집 실패로 이전에 저장된 기록 기준이다. 기록 범위: " + result.payload["coverage"])
    assert "시간 3일, 횟수 3일, 칼로리 3일, 거리 2일" in note
    assert "칼로리 수집 실패로 이전에 저장된 기록 기준" in note
    assert extra["progress_note"] in note and extra["as_of"] in note
    assert len(p.calls) == 1
    assert result.status == "ok"
    snapshot = result.evidence[0].snapshot
    assert snapshot["detail"]["exercise_activity"]["summary"]["exercise_distance_m"]["total"] == 5400


async def test_trend_preserves_zero_null_no_row_and_uncounted_values():
    p = activity_port()
    result = await get_daily_trend(GetDailyTrend(metric="exercise", period="last_7_days"), request(p))
    rows = {d["date"]: d for d in result.payload["exercise_activity"]["days"]}
    slow = rows["2026-10-12"]
    assert slow["exercise_minutes"]["value"] == slow["exercise_count"]["value"] == 0
    assert slow["exercise_calories_kcal"]["value"] == 40
    assert slow["exercise_calories_kcal"]["display"] == "40kcal (미인정 포함)"
    assert slow["exercise_distance_m"]["display"] == "1.2km (미인정 포함)"
    assert rows["2026-10-13"]["exercise_distance_m"] is None
    assert rows["2026-10-13"]["exercise_calories_kcal"]["value"] == 0
    assert rows["2026-10-14"]["exercise_count"] is None
    assert rows["2026-10-14"]["missing"]["exercise_count"] == "기록이 없다"
    assert rows["2026-10-15"]["missing"]["exercise_calories_kcal"].startswith("아직 동기화")
    note = result.payload["record_note"]
    assert note.startswith("칼로리 수집 실패로 이전에 저장된 기록 기준이다.")
    assert "값을 확인할 수 없는 날은 0이 아니다" in note
    assert "항목별 평균" not in note
    assert "칼로리·거리는 미인정 운동 포함" in note


async def test_today_only_has_no_completed_statistics():
    result = await get_metric_summary(GetMetricSummary(metric="exercise", period="today"), request(activity_port()))
    extra = result.payload["exercise_activity"]
    assert all(s["total"] is None and s["avg"] is None and s["days_with_data"] == 0 for s in extra["summary"].values())
    assert extra["in_progress"][0]["exercise_count"]["value"] == 0
    assert extra["as_of"].endswith("+09:00")
    assert extra["missing"] == {}
    assert "항목별 평균" not in result.payload["record_note"]
    assert "오늘 운동 시간은 0분으로 기록되어 있다" in result.payload["record_note"]
    assert extra["progress_note"] in result.payload["record_note"]


@pytest.mark.parametrize("unsynced", [False, True])
async def test_summary_preserves_field_missing_reason_without_changing_zero(unsynced):
    p = activity_port()
    if unsynced:
        p.sync["u1"]["steps"] = SyncState(NOW - timedelta(days=2))
    result = await get_metric_summary(GetMetricSummary(metric="exercise", period="date:2026-10-13"), request(p))
    extra = result.payload["exercise_activity"]
    reason = "UNSYNCED" if unsynced else "NULL_VALUE"
    assert extra["missing"] == {
        "exercise_distance_m": {
            reason: {
                "days": 1,
                "fact": "아직 동기화되지 않아 기록이 있는지 알 수 없다" if unsynced else "기록이 없다",
            }
        }
    }
    assert extra["summary"]["exercise_distance_m"]["total"] is None
    assert extra["summary"]["exercise_distance_m"]["avg"] is None
    assert extra["summary"]["exercise_calories_kcal"]["avg"] == {
        "value": 0,
        "display": "0kcal (미인정 포함) (기록 1일)",
    }
    assert extra["summary"]["exercise_calories_kcal"]["total"]["display"] == "0kcal (미인정 포함)"
    assert result.evidence[0].snapshot["detail"]["exercise_activity"]["days"][0]["distance_reason"] == reason
    note = result.payload["record_note"]
    assert "칼로리 1일" in note  # 실제 0도 평균에 포함된다.
    assert "거리 0일" not in note and "운동 기준 시각" not in note
    assert "오늘 값은 진행 중" not in note


@pytest.mark.parametrize(
    "failed_sources,overall_failure,expected",
    [
        ([], False, None),
        (["distances", "exercise_sessions"], False, "운동 시간·횟수·거리 수집 실패"),
        ([], True, "실패 원천은 특정되지 않았다"),
    ],
)
@pytest.mark.parametrize("daily", [False, True])
async def test_notes_preserve_input_and_do_not_invent_failure_source_or_timestamp(
    failed_sources, overall_failure, expected, daily
):
    row = await activity_port().get_metric_result("u1", "exercise", TODAY - timedelta(days=6), TODAY)
    snapshot = copy.deepcopy(row.snapshot)
    snapshot["data_status"]["synced_until"] = None
    snapshot["data_status"]["last_sync_status"] = "fail" if overall_failure else None
    for name, status in snapshot["detail"]["exercise_activity"]["source_statuses"].items():
        status["last_sync_status"] = "fail" if name in failed_sources else None
    before = copy.deepcopy(snapshot)
    payload = {"record_note": "기존 설명."}
    result = extend(payload, MetricResult("u1", snapshot), daily=daily)
    assert result["record_note"].count("기존 설명.") == 1
    assert payload == {"record_note": "기존 설명."} and snapshot == before
    assert result["exercise_activity"]["as_of"] is None
    assert "운동 기준 시각" not in result["record_note"]  # computed_at으로 대체하지 않는다.
    assert "칼로리 수집 실패" not in result["record_note"]
    if expected is None:
        assert "수집 실패" not in result["record_note"]
        assert result["record_note"].startswith("기존 설명.")
    else:
        assert expected in result["record_note"]
        assert result["record_note"].index(expected) < result["record_note"].index("기존 설명.")


async def test_summary_counts_before_link_and_no_record_separately():
    p = activity_port()
    p.linked_from["u1"] = TODAY.replace(day=12)
    result = await get_metric_summary(GetMetricSummary(metric="exercise", period="last_7_days"), request(p))
    missing = result.payload["exercise_activity"]["missing"]
    assert missing["exercise_count"]["BEFORE_LINK"]["days"] == 3
    assert missing["exercise_count"]["NO_RECORD"]["days"] == 1
    assert missing["exercise_distance_m"]["NULL_VALUE"]["days"] == 1
    assert "UNSYNCED" not in missing["exercise_calories_kcal"]  # 오늘 결측은 in_progress에서만 전달


@pytest.mark.parametrize(
    "field,value,display",
    [
        ("exercise_calories_kcal", Decimal("0.5"), "1kcal (미인정 포함)"),
        ("exercise_count", Decimal("0.5"), "1회"),
        ("exercise_distance_m", Decimal("50"), "0.1km (미인정 포함)"),
        ("exercise_distance_m", Decimal("49.9"), "0.0km (미인정 포함)"),
    ],
)
def test_rounding(field, value, display):
    assert _value(field, value).display == display


async def test_legacy_has_explicit_unavailable_fact_and_no_invented_values():
    result = await get_metric_summary(GetMetricSummary(metric="exercise", period="today"), request(port()))
    assert result.payload["exercise_activity_note"] == UNAVAILABLE
    assert "exercise_activity" not in result.payload
    row = await port().get_metric_result("u1", "exercise", TODAY, TODAY)
    assert extend({"record_note": "기존 설명."}, row, daily=False) == {
        "record_note": "기존 설명.",
        "exercise_activity_note": UNAVAILABLE,
    }


@pytest.mark.parametrize("break_kind", ["negative", "date", "source", "shape"])
async def test_bad_extension_is_lazy_contract_error(break_kind):
    p = activity_port()
    row = await p.get_metric_result("u1", "exercise", TODAY, TODAY)
    snapshot = copy.deepcopy(row.snapshot)
    extra = snapshot["detail"]["exercise_activity"]
    if break_kind == "negative":
        extra["days"][0]["exercise_distance_m"] = -1
    elif break_kind == "date":
        extra["days"][0]["date"] = "2026-10-14"
    elif break_kind == "source":
        extra["source_statuses"].pop("distances")
    else:
        extra.pop("summary")
    parsed = MetricResult("u1", snapshot)
    assert parsed.exercise_sessions() == row.exercise_sessions()
    with pytest.raises(ResultContractError):
        parsed.exercise_activity()


async def test_extension_does_not_change_other_tool_payloads():
    p = activity_port()
    legacy = copy.deepcopy(p)
    legacy.exercise_activity.clear()
    for handler, inp in (
        (get_exercise_sessions, GetExerciseSessions(period="last_7_days")),
        (compare_periods, ComparePeriods(metric="exercise", current="this_week", baseline="last_week")),
        (get_health_overview, GetHealthOverview(period="last_7_days")),
        (get_metric_summary, GetMetricSummary(metric="steps", period="last_7_days")),
        (get_daily_trend, GetDailyTrend(metric="steps", period="last_7_days")),
    ):
        assert (await handler(inp, request(p))).payload == (await handler(inp, request(legacy))).payload


async def test_sample_is_opt_in_and_keeps_old_snapshot():
    old = sample_health_data("u1", NOW)
    new = sample_health_data("u1", NOW, include_exercise_activity=True)
    before = await old.get_metric_result("u1", "exercise", TODAY, TODAY)
    after = await new.get_metric_result("u1", "exercise", TODAY, TODAY)
    extra = after.exercise_activity()
    assert extra is not None
    stripped = copy.deepcopy(after.snapshot)
    stripped["detail"].pop("exercise_activity")
    assert stripped == before.snapshot


@pytest.mark.parametrize("persona", sorted(path.stem for path in FIXTURES_DIR.glob("*.json")))
async def test_golden_truth_is_unchanged(persona):
    assert await compare_with_truth(load_fixture(persona), load_truth(persona)) == []


async def test_gateway_keeps_call_count_status_and_snapshot():
    p = activity_port()
    audit = MemoryAuditSink()
    gateway = ToolGateway(p, TODAY, audit=audit)
    replies = await gateway.execute(
        [ToolCall("a", "get_metric_summary", {"metric": "exercise", "period": "today"})],
        CoachContext(user_id="u1", conversation_id="c1", request_id="r1"),
    )
    assert "exercise_activity" in json.loads(replies[0].content)["result"]
    assert len(p.calls) == 1
    assert replies[0].evidence
    assert audit.records()[0].result_status == "ok"
    assert audit.records()[0].result_count == 1
