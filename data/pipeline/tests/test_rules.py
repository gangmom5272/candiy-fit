from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from candiy_pipeline import rules
from candiy_pipeline.records import KST
from candiy_pipeline.rules import Sample, Stage


def kst(*args):
    return datetime(*args, tzinfo=KST)


def test_parse_duration_minutes():
    assert rules.parse_duration_minutes("PT6H30M") == 390
    assert rules.parse_duration_minutes("PT5H37M28S") == 337
    assert rules.parse_duration_minutes("null") is None
    assert rules.parse_duration_minutes(None) is None
    assert rules.parse_duration_minutes("PT") is None


DAY = datetime(2026, 10, 6).date()


def test_hourly_steps_by_start_hour():
    records = [
        rules.StepRecord(kst(2026, 10, 6, 9), kst(2026, 10, 6, 10), 100),
        rules.StepRecord(kst(2026, 10, 6, 12, 30), kst(2026, 10, 6, 13, 30), 50),
    ]
    hours = rules.hourly_steps(records, DAY)
    assert hours[9] == 100 and hours[12] == 50 and sum(hours) == 150


def test_hourly_steps_null_when_day_record_has_no_versions():
    day_record = rules.StepRecord(kst(2026, 10, 6, 0), kst(2026, 10, 6, 23, 59), 8000)
    assert rules.hourly_steps([day_record], DAY) is None
    assert rules.hourly_steps([], DAY) is None


def test_hourly_steps_from_cumulative_versions():
    # 하루 누적값이 갱신된 버전: 09:00 1200, 09:15 1900, 09:30 2300, 09:45 2300(변화 없음), 10:05 3000
    versions = (
        (kst(2026, 10, 6, 9, 0), 1200),
        (kst(2026, 10, 6, 9, 15), 1900),
        (kst(2026, 10, 6, 9, 30), 2300),
        (kst(2026, 10, 6, 9, 45), 2300),
        (kst(2026, 10, 6, 10, 5), 3000),
    )
    record = rules.StepRecord(kst(2026, 10, 6, 0), kst(2026, 10, 6, 23, 59), 3000, versions)
    hours = rules.hourly_steps([record], DAY)
    assert hours[9] == 2300  # 첫 버전은 0에서 늘어난 것 (자정~첫 동기화 걸음 포함)
    assert hours[10] == 700
    assert sum(hours) == 3000  # 24칸 합 = 하루 걸음


def test_hourly_from_versions_edge_cases():
    versions = [
        (kst(2026, 10, 6, 22, 0), 5000),
        (kst(2026, 10, 6, 21, 0), 4000),  # 순서가 섞여 와도 시각 순으로 계산
        (kst(2026, 10, 7, 0, 5), 5300),  # 다음 날 자정 뒤 마지막 동기화 → 23시
    ]
    hours = rules.hourly_from_versions(DAY, versions)
    assert (hours[21], hours[22], hours[23]) == (4000, 1000, 300)
    # 누적값이 줄어든 버전은 차이를 버린다
    assert sum(rules.hourly_from_versions(DAY, [(kst(2026, 10, 6, 9), 500), (kst(2026, 10, 6, 10), 400)])) == 500


def test_hourly_steps_null_when_versions_do_not_add_up():
    # 마지막 버전이 빠져 버전 합(2000)이 하루 걸음(3000)과 다르면 믿을 수 없다
    versions = ((kst(2026, 10, 6, 9), 2000),)
    record = rules.StepRecord(kst(2026, 10, 6, 0), kst(2026, 10, 6, 23, 59), 3000, versions)
    assert rules.hourly_steps([record], DAY) is None


def test_stage_minutes_mapping():
    st = [
        Stage(kst(2026, 10, 6, 0), kst(2026, 10, 6, 0, 10), 1),
        Stage(kst(2026, 10, 6, 0, 10), kst(2026, 10, 6, 1, 10), 4),
        Stage(kst(2026, 10, 6, 1, 10), kst(2026, 10, 6, 1, 40), 5),
        Stage(kst(2026, 10, 6, 1, 40), kst(2026, 10, 6, 1, 50), 7),
    ]
    m = rules.stage_minutes(st)
    assert m == {"awake": 20, "light": 60, "deep": 30, "rem": 0, "unspecified": 0, "unknown": 0}
    assert rules.stage_minutes([]) == dict.fromkeys(rules.STAGE_COLUMNS)


def test_sleep_minutes_falls_back_to_asleep_stages():
    st = [Stage(kst(2026, 10, 6, 0), kst(2026, 10, 6, 1), 4), Stage(kst(2026, 10, 6, 1), kst(2026, 10, 6, 1, 20), 1)]
    assert rules.sleep_minutes({"sleep_duration_total": "PT7H"}, st) == 420
    assert rules.sleep_minutes({"sleep_duration_total": "null"}, st) == 60
    assert rules.sleep_minutes({"sleep_duration_total": "null"}, []) is None


def test_stages_parse_utc_and_sort():
    payload = {
        "stages": [
            {"stage": 5, "startTime": "2026-10-05T16:00:00Z", "endTime": "2026-10-05T17:00:00Z"},
            {"stage": 4, "startTime": "2026-10-05T15:00:00Z", "endTime": "2026-10-05T16:00:00Z"},
        ]
    }
    st = rules.stages(payload)
    assert [s.stage for s in st] == [4, 5]
    assert st[0].start == datetime(2026, 10, 5, 15, tzinfo=UTC)
    assert rules.sleep_timeline(st)[0]["start"] == "2026-10-06T00:00:00+09:00"


def test_spo2_stats_null_when_no_measurement():
    assert rules.spo2_stats([]) == {"count": None, "avg": None, "min": None, "max": None, "low_count": None}
    s = rules.spo2_stats([97.0, 95.0, 96.0])
    assert (s["count"], s["avg"], s["min"], s["max"]) == (3, 96.0, 95.0, 97.0)
    assert s["low_count"] == 0


def test_spo2_low_count_below_threshold():
    # 90% 미만만 센다. 90.0은 세지 않는다 (S15P31A403-130)
    s = rules.spo2_stats([95.0, 90.0, 89.9, 85.0, 72.0])
    assert s["low_count"] == 3


def test_spo2_low_times_match_low_count():
    # 개수와 같은 샘플·같은 기준. 이른 순, 같은 시각 중복도 둔다 (S15P31A403-302)
    t = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)
    samples = [
        Sample(t + timedelta(minutes=30), 85.0),
        Sample(t, 95.0),
        Sample(t + timedelta(minutes=10), 89.9),
        Sample(t + timedelta(minutes=20), 90.0),  # 90.0은 세지 않는다
        Sample(t + timedelta(minutes=10), 88.0),  # 같은 시각 두 번째 측정
    ]
    times = rules.spo2_low_times(samples)
    assert times == [t + timedelta(minutes=10), t + timedelta(minutes=10), t + timedelta(minutes=30)]
    assert len(times) == rules.spo2_stats([s.value for s in samples])["low_count"]


def test_spo2_low_times_empty_vs_null():
    # 측정은 있는데 90% 미만이 없으면 [] (0회), 측정이 없으면 NULL
    t = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)
    assert rules.spo2_low_times([Sample(t, 96.0)]) == []
    assert rules.spo2_low_times([]) is None


@pytest.mark.parametrize("value, expected", [(70.15, 70.2), (95.15, 95.2), (95.25, 95.3), (95.35, 95.4), (96.15, 96.2)])
@pytest.mark.parametrize("count", [1, 7, 1000])
def test_spo2_equal_samples_round_consistently(value, expected, count):
    assert rules.spo2_stats([value] * count) == {
        "count": count,
        "avg": expected,
        "min": expected,
        "max": expected,
        "low_count": count if value < 90 else 0,
    }


@pytest.mark.parametrize(
    "values, expected",
    [
        ([95.1, 95.2], (95.2, 95.1, 95.2, 0)),
        ([95.2, 95.3], (95.3, 95.2, 95.3, 0)),
        ([89.95, 90.0], (90.0, 90.0, 90.0, 1)),
        ([70.0, 100.0], (85.0, 70.0, 100.0, 1)),
    ],
)
def test_spo2_decimal_ties_and_raw_low_count(values, expected):
    stats = rules.spo2_stats(values)
    assert (stats["avg"], stats["min"], stats["max"], stats["low_count"]) == expected
    assert stats["count"] == len(values)


def test_heart_stats_and_hourly():
    items = [Sample(kst(2026, 10, 6, 8, 0), 60), Sample(kst(2026, 10, 6, 8, 30), 70), Sample(kst(2026, 10, 6, 9), 80)]
    assert rules.heart_stats(items) == {"avg": 70.0, "min": 60, "max": 80, "count": 3}
    assert rules.heart_stats([])["count"] == 0
    hourly = rules.hourly_heart(items)
    assert hourly["8"]["count"] == 2 and hourly["9"]["avg"] == 80.0
    assert rules.hourly_heart([]) is None


def test_daily_heart_avg_gives_each_hour_equal_weight():
    # 8시 평소 측정 2개, 19시 운동 중 연속 측정 100개
    items = [Sample(kst(2026, 10, 6, 8, 0), 60), Sample(kst(2026, 10, 6, 8, 30), 70)]
    items += [Sample(kst(2026, 10, 6, 19, 0) + timedelta(seconds=i), 140) for i in range(100)]
    assert rules.daily_heart_avg(items) == 102.5  # (65 + 140) / 2. 모든 샘플 평균이면 약 138.5
    assert rules.heart_stats(items)["avg"] > 130
    assert rules.daily_heart_avg([]) is None


@dataclass(frozen=True)
class Piece:
    data_id: str
    start_at: datetime
    end_at: datetime


def test_merge_sleeps_by_gap_not_date():
    a = Piece("a", kst(2026, 10, 5, 23, 0), kst(2026, 10, 5, 23, 50))  # 자정 전에 깸
    b = Piece("b", kst(2026, 10, 6, 0, 20), kst(2026, 10, 6, 3, 10))  # 30분 뒤 다시 잠
    c = Piece("c", kst(2026, 10, 6, 4, 10), kst(2026, 10, 6, 7, 0))  # 정확히 60분 뒤 → 묶음
    nap = Piece("nap", kst(2026, 10, 6, 14, 0), kst(2026, 10, 6, 14, 40))  # 7시간 뒤 → 따로
    groups = rules.merge_sleeps([nap, c, a, b])
    assert [[p.data_id for p in g] for g in groups] == [["a", "b", "c"], ["nap"]]
    assert rules.merge_sleeps([]) == []


def test_merge_sleeps_gap_over_limit_is_separate():
    a = Piece("a", kst(2026, 10, 5, 23, 0), kst(2026, 10, 6, 2, 0))
    b = Piece("b", kst(2026, 10, 6, 3, 1), kst(2026, 10, 6, 7, 0))  # 61분 뒤
    assert len(rules.merge_sleeps([a, b])) == 2


def test_gap_stages_are_awake():
    a = Piece("a", kst(2026, 10, 5, 23, 0), kst(2026, 10, 6, 3, 0))
    b = Piece("b", kst(2026, 10, 6, 3, 30), kst(2026, 10, 6, 7, 0))
    gaps = rules.gap_stages([b, a])
    assert gaps == [Stage(kst(2026, 10, 6, 3, 0), kst(2026, 10, 6, 3, 30), 1)]
    assert rules.stage_minutes(gaps)["awake"] == 30


def test_outlier_samples_and_values():
    t = kst(2026, 10, 6, 8)
    hr = [Sample(t, v) for v in (29, 30, 120, 220, 221)]
    assert [s.value for s in rules.valid_heart(hr)] == [30, 120, 220]
    speed = [Sample(t, v) for v in (-1, 0, 5.0, 16.6, 16.7, 50.0)]  # 시속 60km ≈ 16.67m/s
    assert [s.value for s in rules.valid_speed(speed)] == [0, 5.0, 16.6]
    assert rules.valid_spo2(70) and rules.valid_spo2(100)
    assert not rules.valid_spo2(69.9) and not rules.valid_spo2(100.1)
    assert rules.valid_body("weights", 30) and not rules.valid_body("weights", 29.9)
    assert not rules.valid_body("body_fats", 60.1) and not rules.valid_body("heights", 175)
    assert rules.non_negative(-1.0) is None and rules.non_negative(0.0) == 0.0


def test_exercise_duration_limits_by_code():
    start = kst(2026, 10, 6, 6)
    assert not rules.exercise_duration_ok(64, start, start + timedelta(seconds=59))  # 1분 미만
    assert rules.exercise_duration_ok(64, start, start + timedelta(hours=6))  # 축구: 기본 6시간
    assert not rules.exercise_duration_ok(64, start, start + timedelta(hours=6, minutes=1))
    assert rules.exercise_duration_ok(37, start, start + timedelta(hours=11))  # 등산 12시간
    assert rules.exercise_duration_ok(79, start, start + timedelta(hours=7))  # 걷기 8시간
    assert not rules.exercise_duration_ok(79, start, start + timedelta(hours=9))
    assert not rules.exercise_duration_ok(999, start, start + timedelta(hours=7))  # 기준표에 없는 코드는 기본


def test_pick_main_sleep_longest_then_earliest():
    sessions = [
        {"data_id": "nap", "session_minutes": 60, "start_at": kst(2026, 10, 6, 14)},
        {"data_id": "night", "session_minutes": 400, "start_at": kst(2026, 10, 5, 23)},
        {"data_id": "tie", "session_minutes": 400, "start_at": kst(2026, 10, 6, 1)},
    ]
    assert rules.pick_main_sleep(sessions) == "night"
    assert rules.pick_main_sleep([]) is None


@dataclass(frozen=True)
class FromApp:
    app: str
    n: int


def test_one_source_prefers_samsung_health():
    shealth = FromApp(rules.PRIMARY_APP, 0)
    phone = [FromApp("com.android.healthconnect.phone", i) for i in range(1, 4)]
    assert rules.one_source([*phone, shealth]) == [shealth]


def test_one_source_without_samsung_health_keeps_largest_source():
    a = [FromApp("app.a", 1), FromApp("app.a", 2)]
    b = [FromApp("app.b", 3)]
    assert rules.one_source([*b, *a]) == a
    # 기록 수가 같으면 앱 이름 순
    assert rules.one_source([FromApp("app.b", 1), FromApp("app.a", 2)]) == [FromApp("app.a", 2)]


def test_one_source_single_source_unchanged():
    records = [FromApp("app.a", 1), FromApp("app.a", 2)]
    assert rules.one_source(records) == records
    assert rules.one_source([]) == []


def test_asleep_samples_skip_awake_stages():
    t0 = datetime(2026, 10, 5, 14, tzinfo=UTC)
    stages = [
        Stage(t0, t0 + timedelta(minutes=10), 1),
        Stage(t0 + timedelta(minutes=10), t0 + timedelta(minutes=60), 5),
        Stage(t0 + timedelta(minutes=60), t0 + timedelta(minutes=70), 3),
    ]
    samples = [Sample(t0 + timedelta(minutes=m), v) for m, v in ((5, 80), (10, 55), (59, 53), (60, 85), (65, 88))]
    assert [s.value for s in rules.asleep_samples(samples, stages)] == [55, 53]
