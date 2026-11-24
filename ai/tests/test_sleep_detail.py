"""수면 기록마다의 수면 중 심박·산소포화도와 기간 요약, 운동 세션의 칼로리·거리 기준 안내."""

import copy
import dataclasses
import json
import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from coach_ai.adapters.memory import SleepSession
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.executor import ToolRequest
from coach_ai.ports import MetricResult, ResultContractError
from coach_ai.tools.handlers import (
    SLEEP_RECORD_MEASURE_LIMIT,
    compare_periods,
    get_daily_trend,
    get_exercise_sessions,
    get_health_overview,
    get_metric_summary,
    get_sleep_records,
)
from coach_ai.tools.results import DailyTrend, ExerciseSessions, MetricSummary, SleepRecords
from coach_ai.tools.schemas import (
    ComparePeriods,
    GetDailyTrend,
    GetExerciseSessions,
    GetHealthOverview,
    GetMetricSummary,
    GetSleepRecords,
)
from tests.fakes import KST, D, FakeHealthData, exercise_port, night, request, sample_port, workout

# 수면 기록 결과의 nights 항목에 원래 있던 칸. 기간 요약을 넣은 결과(수면 기록 8개 이상)의 수면 기록은 이 칸만 나간다
NIGHT_KEYS = {"date", "bedtime", "wake", "total_min", "total", "deep_min", "rem_min", "light_min", "awake_min"}
# 수면 기록마다 값을 넣는 결과(7개 이하)는 잠든 시각 구분(slept)이 더해진다. 측정이 없는 수면 기록은 이 칸만 나간다
RECORD_KEYS = NIGHT_KEYS | {"slept"}
# 잠든 시각 구분은 상대 표현 없이 계산한 날짜와 밤·아침·낮이다 (calculation-rules.md 11.1)
SLEPT_WINDOW = re.compile(r"(1[0-2]|[1-9])월 ([1-9]|[12]\d|3[01])일 (새벽|오전|오후|저녁|밤) .+에 잠들어 .+에 깸")
RELATIVE_DAY_WORDS = ("그날", "전날", "다음 날", "다음날", "당일")
MEASURE_KEYS = (
    "avg_heart_rate",
    "min_heart_rate",
    "max_heart_rate",
    "spo2_avg",
    "spo2_min",
    "spo2_max",
    "spo2_low_count",
)
# 산소포화도 값이 보이는 결과에 넣는 사실 문장 (calculation-rules.md 7.5)
DEVICE_NOTE = "워치 측정은 의료기기 측정이 아니다."
CALORIE_DISTANCE_NOTE = (
    "칼로리와 거리는 운동 세션에 기록된 값과 그 합이고, 하루 전체 소모 칼로리나 하루 동안 이동한 거리가 아니다."
)
SUMMARY_NOTE = (
    "수면 중 평균 심박(avg_heart_rate)과 산소포화도 평균(spo2_avg)은 수면마다 기록된 값의 평균을 다시 평균한 값이다. "
    "최저·최고(min_heart_rate, max_heart_rate, spo2_min)는 수면 기록 중 가장 낮은·높은 값과 그 수면 기록의 날짜다. "
    "records 는 그 값이 있는 수면 기록 수다."
)
# 지표 요약·날짜별 조회 결과의 심박·산소포화도에 넣는 사실 문장. 산소포화도는 주 수면 중에만 재서 하루 전체 값이
# 아니다. 수면 기록의 날짜가 깬 날이라는 문장은 밤에 잔 수면을 그날 날짜의 기록으로 읽은 답을 방지한다
HEART_SLEEP_VALUES_NOTE = (
    "이 결과의 심박 값은 하루 전체 측정으로 계산한 값이고, "
    "자는 동안 잰 최저·최고 심박은 수면 기록(get_sleep_records) 결과에 있다. "
    "수면 기록의 날짜는 잠에서 깬 날이다."
)
OXYGEN_SLEEP_VALUES_NOTE = (
    "이 결과의 산소포화도 값은 날마다 주 수면 중에 잰 값의 평균이고, "
    "자는 동안 잰 최저값과 90% 미만 측정은 수면 기록(get_sleep_records) 결과에 있다. "
    "수면 기록의 날짜는 잠에서 깬 날이다."
)
# 10월 7일~14일에 깬 수면 → ((평균, 최저, 최고 심박), (산소포화도 평균, 최저, 최고), 90% 미만 측정 수).
# 10일은 90% 미만 측정 수가 비어 있고, 14일은 측정이 없다
MEASURES = {
    7: ((60.0, 52, 80), (95.0, 90.0, 98.0), 0),
    8: ((58.0, 49, 75), (96.0, 92.0, 99.0), 0),
    9: ((62.5, 54, 91), (94.0, 88.5, 97.0), 4),
    10: (None, (95.2, 91.0, 98.5), None),
    11: ((57.5, 49, 77), None, None),
    12: ((59.0, 51, 91), None, None),
    13: ((61.0, 53, 84), None, None),
    14: (None, None, None),
}
# 점수·한마디 결과의 문장 끝 검사(tests/test_lifestyle_score.py, tests/test_daily_message.py)와 같은 기준이다.
# 결과에 넣는 문장은 사실만 적는다
INSTRUCTION_ENDING = re.compile(r"(주세요|한다|않는다)$")
SAMPLE_NOW = datetime(2026, 10, 22, 14, 0, tzinfo=KST)


def measured_port():
    """12일은 심박·산소포화도가 모두 있고 90% 미만 측정이 3회다. 13일은 심박만, 14일은 산소포화도 평균과
    측정 수만 있고 90% 미만 측정은 0회다."""
    port = sample_port()
    port.sleep["u1"] = [
        dataclasses.replace(
            night(12, (11, 23, 50), (7, 35), 450),
            avg_heart_rate=58.2, min_heart_rate=51, max_heart_rate=76,
            spo2_avg=95.1, spo2_min=88.6, spo2_max=97.2, spo2_count=465, spo2_low_count=3,
        ),
        dataclasses.replace(
            night(13, (13, 0, 35), (7, 5), 368), avg_heart_rate=59.0, min_heart_rate=52, max_heart_rate=71
        ),
        dataclasses.replace(night(14, (14, 1, 10), (7, 15), None), spo2_avg=96.0, spo2_count=365, spo2_low_count=0),
    ]  # fmt: skip
    return port


def many_records_port(days=range(7, 15)):
    """MEASURES 의 날짜 중 days 에 깬 수면 기록이 있다. 기본은 10월 7일~14일의 수면 기록 8개다."""
    port = sample_port()
    records = []
    for d in days:
        heart, oxygen, low = MEASURES[d]
        values = {}
        if heart is not None:
            values.update(avg_heart_rate=heart[0], min_heart_rate=heart[1], max_heart_rate=heart[2])
        if oxygen is not None:
            values.update(
                spo2_avg=oxygen[0], spo2_min=oxygen[1], spo2_max=oxygen[2], spo2_count=420, spo2_low_count=low
            )
        records.append(dataclasses.replace(night(d, (d - 1, 23, 30), (7, 0), 420), **values))
    port.sleep["u1"] = records
    return port


async def test_result_night_reads_sleep_heart_rate_and_oxygen():
    result = await measured_port().get_metric_result("u1", "sleep", D(2026, 10, 12), D(2026, 10, 14))
    full, heart_only, oxygen_only = result.detail.nights
    assert (full.avg_heart_rate, full.min_heart_rate, full.max_heart_rate) == (58.2, 51, 76)
    assert (full.spo2_avg, full.spo2_min, full.spo2_max, full.spo2_low_count) == (95.1, 88.6, 97.2, 3)
    assert (heart_only.avg_heart_rate, heart_only.spo2_avg, heart_only.spo2_low_count) == (59.0, None, None)
    assert (oxygen_only.avg_heart_rate, oxygen_only.spo2_avg, oxygen_only.spo2_low_count) == (None, 96.0, 0)


@pytest.mark.parametrize("key", MEASURE_KEYS)
async def test_night_without_measurement_key_breaks_contract(key):
    # API 명세 2.2 수면 nights 의 새 칸은 값이 null 이어도 키는 있어야 한다
    result = await measured_port().get_metric_result("u1", "sleep", D(2026, 10, 12), D(2026, 10, 12))
    snapshot = copy.deepcopy(dict(result.snapshot))
    del snapshot["detail"]["nights"][0][key]
    with pytest.raises(ResultContractError):
        MetricResult("u1", snapshot)


async def test_sleep_records_show_measurements_of_each_night():
    out = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(measured_port()))
    full, heart_only, oxygen_only = out.payload["nights"]
    assert {k: full[k] for k in MEASURE_KEYS} == {
        "avg_heart_rate": "58.2bpm", "min_heart_rate": "51bpm", "max_heart_rate": "76bpm",
        "spo2_avg": "95.1%", "spo2_min": "88.6%", "spo2_max": "97.2%", "spo2_low_count": 3,
    }  # fmt: skip
    # 값이 없는 칸은 넣지 않는다. 0회는 측정이 있고 90% 미만이 없었다는 기록이라 넣는다
    assert set(heart_only) == RECORD_KEYS | {"avg_heart_rate", "min_heart_rate", "max_heart_rate"}
    assert (heart_only["avg_heart_rate"], heart_only["max_heart_rate"]) == ("59bpm", "71bpm")
    assert set(oxygen_only) == RECORD_KEYS | {"spo2_avg", "spo2_low_count"}
    assert (oxygen_only["spo2_avg"], oxygen_only["spo2_low_count"]) == ("96%", 0)
    # 메모리 어댑터 원형에는 시각이 없어 횟수만 쓴다
    assert out.payload["spo2_low"] == "산소포화도를 기록한 2일 중 90% 미만으로 기록된 날 1일, 기록 3회"
    assert out.payload["spo2_note"] == DEVICE_NOTE
    # 근거 snapshot 은 공통 원형 그대로라 측정 수와 숫자 값이 들어 있다
    snapshot_night = out.evidence[0].snapshot["detail"]["nights"][0]
    assert (snapshot_night["spo2_count"], snapshot_night["spo2_low_count"]) == (465, 3)


async def test_sleep_records_show_when_each_sleep_started():
    # date 는 깬 날이다. 잠든 때와 깬 때는 "밤" 같은 때 구분 없이 실제 날짜와 시간대 말을 붙인 시각으로 쓴다
    out = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(measured_port()))
    nights = out.payload["nights"]
    assert [(n["date"], n["slept"], n["bedtime"]) for n in nights] == [
        ("2026-10-12", "10월 11일 밤 11시 50분에 잠들어 12일 오전 7시 35분에 깸", "23:50"),
        ("2026-10-13", "10월 13일 새벽 12시 35분에 잠들어 오전 7시 5분에 깸", "00:35"),
        ("2026-10-14", "10월 14일 새벽 1시 10분에 잠들어 오전 7시 15분에 깸", "01:10"),
    ]
    assert [list(n)[:3] for n in nights] == [["date", "slept", "bedtime"]] * 3
    # 기간 요약을 넣은 결과(8개 이상)에는 넣지 않는다. 긴 기간 결과가 도구 결과 예약 안에 들게 다른 칸과 같이 뺀다
    summary = (await get_sleep_records(GetSleepRecords(period="last_14_days"), request(many_records_port()))).payload
    assert len(summary["nights"]) == 8 and all("slept" not in n for n in summary["nights"])


@pytest.mark.parametrize(
    ("bed", "wake", "slept"),
    [
        ((13, 17, 59), (1, 30), "10월 13일 오후 5시 59분에 잠들어 14일 새벽 1시 30분에 깸"),
        ((13, 18, 0), (6, 0), "10월 13일 저녁 6시에 잠들어 14일 오전 6시에 깸"),
        ((13, 20, 59), (7, 0), "10월 13일 저녁 8시 59분에 잠들어 14일 오전 7시에 깸"),
        ((13, 21, 0), (7, 0), "10월 13일 밤 9시에 잠들어 14일 오전 7시에 깸"),
        ((13, 23, 59), (7, 0), "10월 13일 밤 11시 59분에 잠들어 14일 오전 7시에 깸"),
        ((14, 0, 0), (7, 0), "10월 14일 새벽 12시에 잠들어 오전 7시에 깸"),
        ((14, 5, 59), (11, 0), "10월 14일 새벽 5시 59분에 잠들어 오전 11시에 깸"),
        ((14, 6, 0), (11, 0), "10월 14일 오전 6시에 잠들어 오전 11시에 깸"),
        ((14, 11, 59), (13, 0), "10월 14일 오전 11시 59분에 잠들어 오후 1시에 깸"),
        ((14, 12, 0), (17, 0), "10월 14일 오후 12시에 잠들어 오후 5시에 깸"),
    ],
)
async def test_slept_window_uses_time_of_day_words(bed, wake, slept):
    # 10월 14일에 깬 수면. 시간대 말은 0~5시 새벽, 6~11시 오전, 12~17시 오후, 18~20시 저녁, 21~23시 밤이다.
    # 깬 날이 잠든 날과 같으면 깬 쪽 날짜를 뺀다
    port = sample_port()
    port.sleep["u1"] = [night(14, bed, wake, 300)]
    nights = (await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))).payload["nights"]
    assert [(n["date"], n["slept"]) for n in nights] == [("2026-10-14", slept)]


async def test_slept_window_reads_times_in_kst():
    # 시각은 UTC 로 저장된다. UTC 로 쓰면 10월 12일 15:30Z(KST 13일 00:30)는 12일 오후, 10월 13일 21:30Z
    # (KST 14일 06:30)는 13일 밤이 된다
    port = sample_port()
    port.sleep["u1"] = [
        SleepSession(
            date=D(2026, 10, 13), start_at=datetime(2026, 10, 12, 15, 30, tzinfo=UTC),
            end_at=datetime(2026, 10, 12, 22, 0, tzinfo=UTC), is_main=True, sleep_minutes=370,
        ),
        SleepSession(
            date=D(2026, 10, 14), start_at=datetime(2026, 10, 13, 21, 30, tzinfo=UTC),
            end_at=datetime(2026, 10, 14, 2, 0, tzinfo=UTC), is_main=True, sleep_minutes=250,
        ),
    ]  # fmt: skip
    nights = (await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))).payload["nights"]
    assert [(n["date"], n["bedtime"], n["slept"]) for n in nights] == [
        ("2026-10-13", "00:30", "10월 13일 새벽 12시 30분에 잠들어 오전 7시에 깸"),
        ("2026-10-14", "06:30", "10월 14일 오전 6시 30분에 잠들어 오전 11시에 깸"),
    ]


async def test_slept_window_keeps_the_month_of_each_side():
    now = datetime(2026, 11, 2, 9, 0, tzinfo=KST)
    asleep = SleepSession(
        date=D(2026, 11, 1), start_at=datetime(2026, 11, 1, 1, 0, tzinfo=KST),
        end_at=datetime(2026, 11, 1, 7, 30, tzinfo=KST), is_main=True, sleep_minutes=370,
    )  # fmt: skip
    port = FakeHealthData(sleep={"u1": [asleep]}, linked_from={"u1": D(2026, 9, 20)}, now=now)
    req = ToolRequest(user_id="u1", today=now.date(), port=port)
    nights = (await get_sleep_records(GetSleepRecords(period="last_7_days"), req)).payload["nights"]
    assert [(n["date"], n["slept"]) for n in nights] == [
        ("2026-11-01", "11월 1일 새벽 1시에 잠들어 오전 7시 30분에 깸")
    ]


async def test_slept_window_follows_first_part_and_daytime_start():
    # 나뉜 수면의 start_at 은 첫 조각의 시작이다 (calculation-rules.md 7.5). 05:40 에 잠들었다 깨서 06:40 에 다시 잔
    # 수면은 새벽 5시 40분으로 쓰고, 낮에 잠들어 자정을 넘긴 교대 근무 수면은 잠든 날 오후와 깬 날 새벽을 함께 쓴다
    port = sample_port()
    port.sleep["u1"] = [
        dataclasses.replace(night(13, (13, 5, 40), (11, 0), 260), awake_minutes=60),
        night(15, (14, 16, 0), (2, 0), 500),
    ]
    nights = (await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))).payload["nights"]
    assert [(n["date"], n["slept"]) for n in nights] == [
        ("2026-10-13", "10월 13일 새벽 5시 40분에 잠들어 오전 11시에 깸"),
        ("2026-10-15", "10월 14일 오후 4시에 잠들어 15일 새벽 2시에 깸"),
    ]


async def test_slept_windows_hold_dates_without_relative_words():
    port = measured_port()
    port.sleep["u1"] += [night(15, (15, 6, 30), (11, 0), 200)]
    payload = (await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))).payload
    labels = [n["slept"] for n in payload["nights"]]
    assert labels == [
        "10월 11일 밤 11시 50분에 잠들어 12일 오전 7시 35분에 깸",
        "10월 13일 새벽 12시 35분에 잠들어 오전 7시 5분에 깸",
        "10월 14일 새벽 1시 10분에 잠들어 오전 7시 15분에 깸",
        "10월 15일 오전 6시 30분에 잠들어 오전 11시에 깸",
    ]
    assert all(SLEPT_WINDOW.fullmatch(label) for label in labels)
    text = json.dumps(payload, ensure_ascii=False)
    assert [word for word in RELATIVE_DAY_WORDS if word in text] == []


async def test_sleep_records_without_measurements_keep_previous_fields():
    out = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(sample_port()))
    assert [set(n) for n in out.payload["nights"]] == [RECORD_KEYS] * 3
    assert "spo2_low" not in out.payload and "spo2_note" not in out.payload


async def test_spo2_low_sentence_comes_only_with_low_count():
    port = measured_port()
    # 90% 미만 기록 수가 있는 수면 기록이 하나라도 있으면 넣는다. 산소포화도 평균만 있으면 의료기기 문장만 넣는다
    port.sleep["u1"] = [dataclasses.replace(n, spo2_low_count=None) for n in port.sleep["u1"]]
    out = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))
    assert "spo2_low" not in out.payload and out.payload["nights"][0]["spo2_min"] == "88.6%"
    assert out.payload["spo2_note"] == DEVICE_NOTE

    payload = (await get_sleep_records(GetSleepRecords(period="last_7_days"), request(measured_port()))).payload
    with pytest.raises(ValidationError, match="spo2_low"):
        SleepRecords.model_validate({k: v for k, v in payload.items() if k != "spo2_low"})
    with pytest.raises(ValidationError, match="spo2_note"):
        SleepRecords.model_validate({k: v for k, v in payload.items() if k != "spo2_note"})


async def test_seven_records_keep_measurements_in_each_record():
    assert SLEEP_RECORD_MEASURE_LIMIT == 7
    out = await get_sleep_records(GetSleepRecords(period="last_14_days"), request(many_records_port(range(8, 15))))
    nights = out.payload["nights"]
    assert len(nights) == 7 and "measure_summary" not in out.payload
    assert (nights[0]["avg_heart_rate"], nights[0]["spo2_min"], nights[0]["spo2_low_count"]) == ("58bpm", "92%", 0)
    assert out.payload["spo2_note"] == DEVICE_NOTE


async def test_eight_records_carry_period_summary_instead_of_each_record():
    out = await get_sleep_records(GetSleepRecords(period="last_14_days"), request(many_records_port()))
    assert [set(n) for n in out.payload["nights"]] == [NIGHT_KEYS] * 8
    assert list(out.payload)[-4:] == ["summary", "measure_summary", "sleep_vs_baseline", "last_night_vs_week"]
    # 값이 있는 수면 기록만 쓰고 그 수를 records 에 담는다
    assert out.payload["measure_summary"] == {
        # (60 + 58 + 62.5 + 57.5 + 59 + 61) / 6 = 59.67
        "avg_heart_rate": {"value": 59.7, "display": "59.7bpm", "records": 6},
        # 같은 값이 둘이면 이른 날짜다 (49bpm 은 8일·11일, 91bpm 은 9일·12일)
        "min_heart_rate": {"value": 49.0, "display": "49bpm", "date": "2026-10-08", "records": 6},
        "max_heart_rate": {"value": 91.0, "display": "91bpm", "date": "2026-10-09", "records": 6},
        # (95 + 96 + 94 + 95.2) / 4 = 95.05 를 ROUND_HALF_UP 으로 95.1 로 쓴다
        "spo2_avg": {"value": 95.1, "display": "95.1%", "records": 4},
        "spo2_min": {"value": 88.5, "display": "88.5%", "date": "2026-10-09", "records": 4},
        "note": SUMMARY_NOTE,
    }
    # 90% 미만 기록 수가 있는 수면 기록 3개(10일은 비어 있다) 중 그 수가 1 이상인 기록은 9일 하나다.
    # 기간 요약 앞에 같은 문장으로 쓰고, 지속 시간이 아니라는 문장은 기간 요약에도 그대로 둔다
    assert out.payload["spo2_low"] == "산소포화도를 기록한 3일 중 90% 미만으로 기록된 날 1일, 기록 4회"
    assert out.payload["spo2_note"] == DEVICE_NOTE


async def test_period_summary_has_only_values_that_exist():
    port = many_records_port()
    port.sleep["u1"] = [
        dataclasses.replace(n, spo2_avg=None, spo2_min=None, spo2_max=None, spo2_count=None, spo2_low_count=None)
        for n in port.sleep["u1"]
    ]
    payload = (await get_sleep_records(GetSleepRecords(period="last_14_days"), request(port))).payload
    assert list(payload["measure_summary"]) == ["avg_heart_rate", "min_heart_rate", "max_heart_rate", "note"]
    assert payload["measure_summary"]["note"] == SUMMARY_NOTE
    assert "spo2_low" not in payload and "spo2_note" not in payload
    # 수면 기록이 8개 이상이어도 측정이 하나도 없으면 기간 요약 없이 고치기 전과 같은 칸만 나간다
    port.sleep["u1"] = [night(d, (d - 1, 23, 30), (7, 0), 420) for d in range(7, 15)]
    payload = (await get_sleep_records(GetSleepRecords(period="last_14_days"), request(port))).payload
    assert "measure_summary" not in payload and "spo2_note" not in payload
    assert [set(n) for n in payload["nights"]] == [NIGHT_KEYS] * 8


async def test_records_carry_either_each_measurement_or_period_summary():
    summary = (await get_sleep_records(GetSleepRecords(period="last_14_days"), request(many_records_port()))).payload
    each = (await get_sleep_records(GetSleepRecords(period="last_7_days"), request(measured_port()))).payload
    with pytest.raises(ValidationError, match="measure_summary"):
        SleepRecords.model_validate({**each, "measure_summary": summary["measure_summary"]})
    with pytest.raises(ValidationError, match="spo2_note"):
        SleepRecords.model_validate({k: v for k, v in summary.items() if k != "spo2_note"})
    # 잠든 때·깬 때 글도 수면 기록마다 값을 넣는 결과의 칸이라 기간 요약과 함께 두지 않는다
    labeled = [{**n, "slept": "10월 6일 밤 11시에 잠들어 7일 오전 7시에 깸"} for n in summary["nights"]]
    with pytest.raises(ValidationError, match="measure_summary"):
        SleepRecords.model_validate({**summary, "nights": labeled})


async def test_sessions_with_calories_or_distance_carry_basis_note():
    out = await get_exercise_sessions(GetExerciseSessions(period="last_7_days"), request(sample_exercise_port()))
    assert out.payload["calorie_distance_note"] == CALORIE_DISTANCE_NOTE
    # 칼로리·거리 기록이 없는 세션만 있으면 넣지 않는다
    out = await get_exercise_sessions(GetExerciseSessions(period="last_7_days"), request(exercise_port()))
    assert "calorie_distance_note" not in out.payload
    assert all("calories" not in s and "distance" not in s for s in out.payload["sessions"])

    payload = (
        await get_exercise_sessions(GetExerciseSessions(period="last_7_days"), request(sample_exercise_port()))
    ).payload
    with pytest.raises(ValidationError, match="calorie_distance_note"):
        ExerciseSessions.model_validate({k: v for k, v in payload.items() if k != "calorie_distance_note"})


def sample_exercise_port():
    """오늘 운동한 달리기 한 번에 칼로리만 있고, 12일 달리기에는 거리만 있다."""
    port = exercise_port(dataclasses.replace(workout(15, (7, 0), 30), calories_kcal=280.0))
    port.exercise["u1"] = [
        dataclasses.replace(s, distance_m=5200.0) if s.date == D(2026, 10, 12) else s for s in port.exercise["u1"]
    ]
    return port


def _sentences(text: str) -> list[str]:
    """문장으로 나눈다. 소수점과 % 뒤에는 빈칸이 없어 나누지 않는다."""
    return [part.strip(" .") for part in re.split(r"\.\s", text) if part.strip(" .")]


async def test_new_notes_state_facts_without_instructions_to_the_model():
    sleep = (await get_sleep_records(GetSleepRecords(period="last_7_days"), request(measured_port()))).payload
    summary = (await get_sleep_records(GetSleepRecords(period="last_14_days"), request(many_records_port()))).payload
    sessions = (
        await get_exercise_sessions(GetExerciseSessions(period="last_7_days"), request(sample_exercise_port()))
    ).payload
    req = request(measured_port())
    notes = []
    for tool, schema in ((get_metric_summary, GetMetricSummary), (get_daily_trend, GetDailyTrend)):
        for metric in ("heart_rate", "oxygen_saturation"):
            notes.append((await tool(schema(metric=metric, period="last_7_days"), req)).payload["sleep_values_note"])
    found = _sentences(sleep["spo2_note"]) + [sleep["spo2_low"], sleep["relative_nights"]]
    found += _sentences(sessions["calorie_distance_note"]) + _sentences(summary["measure_summary"]["note"])
    found += [s for note in notes for s in _sentences(note)]
    assert len(found) == 15
    assert [s for s in found if INSTRUCTION_ENDING.search(s)] == []


async def test_heart_and_oxygen_summaries_state_day_values_and_where_sleep_values_are():
    req = request(measured_port())
    heart = (await get_metric_summary(GetMetricSummary(metric="heart_rate", period="last_7_days"), req)).payload
    oxygen = (await get_metric_summary(GetMetricSummary(metric="oxygen_saturation", period="last_7_days"), req)).payload
    assert heart["sleep_values_note"] == HEART_SLEEP_VALUES_NOTE
    assert oxygen["sleep_values_note"] == OXYGEN_SLEEP_VALUES_NOTE
    # 산소포화도 결과는 주 수면 중에 잰 값이라 하루 전체 값이라고 적지 않는다
    assert oxygen["measured_during"] == "주 수면 중" and "하루 전체" not in oxygen["sleep_values_note"]


async def test_sleep_values_note_is_only_in_heart_and_oxygen_summaries():
    req = request(measured_port())
    for metric in ("steps", "sleep"):
        payload = (await get_metric_summary(GetMetricSummary(metric=metric, period="last_7_days"), req)).payload
        assert "sleep_values_note" not in payload, metric
    # 기간 비교와 전체 요약 결과는 같은 요약 모델을 쓰지만 이 칸을 넣지 않는다
    compare = await compare_periods(ComparePeriods(metric="heart_rate", current="this_week", baseline="last_week"), req)
    overview = await get_health_overview(GetHealthOverview(period="last_7_days"), req)
    assert "sleep_values_note" not in json.dumps(compare.payload) + json.dumps(overview.payload)


async def test_sleep_values_note_is_rejected_for_other_metrics():
    req = request(measured_port())
    steps = (await get_metric_summary(GetMetricSummary(metric="steps", period="last_7_days"), req)).payload
    with pytest.raises(ValidationError, match="sleep_values_note는 heart_rate·oxygen_saturation에서만"):
        MetricSummary.model_validate({**steps, "sleep_values_note": HEART_SLEEP_VALUES_NOTE})
    trend = (await get_daily_trend(GetDailyTrend(metric="steps", period="last_7_days"), req)).payload
    with pytest.raises(ValidationError, match="sleep_values_note는 heart_rate·oxygen_saturation에서만"):
        DailyTrend.model_validate({**trend, "sleep_values_note": HEART_SLEEP_VALUES_NOTE})


async def test_heart_and_oxygen_daily_trends_carry_the_same_notes():
    # 자는 동안의 값을 묻는 질문에 첫 호출로 날짜별 조회를 불러도 같은 안내를 받는다
    req = request(measured_port())
    heart = (await get_daily_trend(GetDailyTrend(metric="heart_rate", period="last_7_days"), req)).payload
    oxygen = (await get_daily_trend(GetDailyTrend(metric="oxygen_saturation", period="last_7_days"), req)).payload
    assert heart["sleep_values_note"] == HEART_SLEEP_VALUES_NOTE
    assert oxygen["sleep_values_note"] == OXYGEN_SLEEP_VALUES_NOTE
    for metric in ("steps", "sleep"):
        payload = (await get_daily_trend(GetDailyTrend(metric=metric, period="last_7_days"), req)).payload
        assert "sleep_values_note" not in payload, metric


async def test_sample_nights_share_sleep_heart_rate_with_score_rows():
    port = sample_health_data("u1", SAMPLE_NOW)
    result = await port.get_metric_result("u1", "sleep", D(2026, 10, 9), D(2026, 10, 22))
    nights = result.detail.nights
    assert [n.date.day for n in nights] == [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20, 21]
    for n in nights:
        heart = (await port.get_lifestyle_score("u1", n.date)).areas.heart
        # 홈 화면의 안정 시 심박수(점수 행의 수면 중 평균 심박)와 같은 값이다
        assert n.avg_heart_rate == heart.sleep_hr_avg, n.date
    # 심박만 있는 수면 기록, 산소포화도만 있는 수면 기록, 90% 미만 측정이 있는 수면 기록이 하나 이상 있다
    by_day = {n.date.day: n for n in nights}
    assert by_day[14].avg_heart_rate is None and by_day[14].spo2_avg == 95.7
    assert by_day[11].spo2_avg is None and by_day[11].avg_heart_rate == 59.4
    assert [(d, n.spo2_min, n.spo2_low_count) for d, n in by_day.items() if n.spo2_low_count] == [(20, 88.6, 3)]
    assert (by_day[21].avg_heart_rate, by_day[21].min_heart_rate, by_day[21].max_heart_rate) == (59.0, 51, 76)
    assert (by_day[21].spo2_avg, by_day[21].spo2_min, by_day[21].spo2_max, by_day[21].spo2_low_count) == (
        96.0,
        91.8,
        98.1,
        0,
    )


async def test_sample_nights_follow_stored_measurement_rules():
    # 연동 함수의 수면 검증(backend app/health/schemas.py 의 Night)과 같은 규칙이다
    port = sample_health_data("u1", SAMPLE_NOW)
    snapshot = (await port.get_metric_result("u1", "sleep", D(2026, 10, 9), D(2026, 10, 22))).snapshot
    for n in snapshot["detail"]["nights"]:
        low, avg, high = n["min_heart_rate"], n["avg_heart_rate"], n["max_heart_rate"]
        assert (low is None) == (avg is None) == (high is None), n["date"]
        if avg is not None:
            assert isinstance(low, int) and isinstance(high, int) and low - 0.5 <= avg <= high + 0.5, n["date"]
        values = (n["spo2_min"], n["spo2_avg"], n["spo2_max"], n["spo2_count"], n["spo2_low_count"])
        assert all(v is None for v in values) or all(v is not None for v in values), n["date"]
        if n["spo2_avg"] is not None:
            assert n["spo2_min"] <= n["spo2_avg"] <= n["spo2_max"] <= 100, n["date"]
            assert 0 <= n["spo2_low_count"] <= n["spo2_count"], n["date"]
            # 90% 미만 측정이 있으면 최저가 90% 아래이고, 없으면 최저가 90% 이상이다
            assert (n["spo2_low_count"] > 0) == (n["spo2_min"] < 90), n["date"]


async def test_sample_two_weeks_summarize_twelve_sleep_records():
    request_ = ToolRequest(user_id="u1", today=SAMPLE_NOW.date(), port=sample_health_data("u1", SAMPLE_NOW))
    payload = (await get_sleep_records(GetSleepRecords(period="recent_days:14"), request_)).payload
    assert len(payload["nights"]) == 12
    summary = payload["measure_summary"]
    shown = {k: (v["display"], v.get("date"), v["records"]) for k, v in summary.items() if "display" in v}
    # 심박은 8일 전 수면에, 산소포화도는 11일·4일 전 수면에 측정이 없다
    assert shown == {
        "avg_heart_rate": ("59.5bpm", None, 11),
        "min_heart_rate": ("49bpm", "2026-10-10", 11),
        "max_heart_rate": ("88bpm", "2026-10-20", 11),
        "spo2_avg": ("95.7%", None, 10),
        "spo2_min": ("88.6%", "2026-10-20", 10),
    }
    # 90% 미만 기록은 10-20 에 깬 수면(10-20 01:10 에 잠들어 10월 19일 밤)의 세 번이고 시각이 있다
    assert payload["spo2_low"] == (
        "산소포화도를 기록한 10일 중 90% 미만으로 기록된 날 1일, 기록 3회 (깬 날 기준 10월 20일 수면 새벽 3시 "
        "12분·3시 40분·4시 5분)"
    )
