"""로컬 PostgreSQL에서 원본 → 집계를 확인한다. 합성 레코드만 쓴다.

실행: docker compose로 DB를 띄운 뒤 `pytest -m db`
"""

import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest
from psycopg.types.json import Jsonb

from candiy_pipeline.aggregator import AGGREGATE_TABLES, aggregate_member, dates_to_aggregate
from candiy_pipeline.config import load_settings

pytestmark = pytest.mark.db

D = date(2026, 10, 6)


@pytest.fixture
def conn():
    with psycopg.connect(load_settings().db_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def member(conn):
    m = uuid.uuid4()
    yield m
    for table in ("raw_health_record", "step_snapshot", "lifestyle_score", *AGGREGATE_TABLES):
        conn.execute(f"DELETE FROM {table} WHERE end_user_id = %s", (m,))


def put(conn, member, metric, data_id, start, end=None, app="test", **payload):
    conn.execute(
        "INSERT INTO raw_health_record (metric, end_user_id, data_id, app, start_at, end_at, payload, "
        "last_modified_at, source_created_at, source_updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now(), now())",
        (metric, member, data_id, app, start, end, Jsonb(payload)),
    )


def hr(*pairs):
    return [{"time": t, "beatsPerMinute": v} for t, v in pairs]


def one(conn, sql, *params):
    return conn.execute(sql, params).fetchone()


def run(conn, member, *, all_dates=False):
    return aggregate_member(conn, member, dates_to_aggregate(conn, member, all_dates=all_dates))


def test_activity_and_exercise(conn, member):
    # 운동 10:00~11:00 (KST). 칼로리 하나는 안에, 하나는 걸쳐 있다
    put(
        conn,
        member,
        "exercise_sessions",
        "ex1",
        "2026-10-06T10:00+09",
        "2026-10-06T11:00+09",
        exercise_type=56,
        title="",
    )
    put(conn, member, "calories_burned", "c1", "2026-10-06T10:00+09", "2026-10-06T10:30+09", energy_kcal=100.0)
    put(conn, member, "calories_burned", "c2", "2026-10-06T10:50+09", "2026-10-06T11:10+09", energy_kcal=40.0)
    put(conn, member, "distances", "d1", "2026-10-06T10:00+09", "2026-10-06T11:00+09", distance_m=3000.0)
    put(
        conn,
        member,
        "heart_rates",
        "h1",
        "2026-10-06T10:00+09",
        "2026-10-06T10:59+09",
        samples=hr(("2026-10-06T01:10:00Z", 120), ("2026-10-06T01:20:00Z", 140)),
    )
    put(conn, member, "steps", "s1", "2026-10-06T00:00+09", "2026-10-06T23:59:59+09", count=8000)

    counts = run(conn, member)
    assert counts["exercise_session"] == 1 and counts["activity_daily"] == 1

    ex = one(
        conn,
        "SELECT date, code, title, duration_minutes, calories_kcal, distance_m, hr_avg, hr_max, avg_speed_mps "
        "FROM exercise_session WHERE end_user_id = %s",
        member,
    )
    # 속도 기록이 없어도 거리 ÷ 운동 시간으로 평균 속도를 낸다: 3000m / 3600초 (S15P31A403-260)
    assert ex == (D, 56, None, 60, 100.0, 3000.0, 130.0, 140, Decimal("0.83"))

    act = one(
        conn,
        "SELECT steps, hourly_steps, exercise_calories_kcal, exercise_distance_m, exercise_count, exercise_minutes "
        "FROM activity_daily WHERE end_user_id = %s AND date = %s",
        member,
        D,
    )
    # 하루 단위 걸음이라 시간대별은 NULL. 하루 운동 칼로리는 운동 세션 칼로리의 합이라 걸친 c2는 빠진다 (S15P31A403-231)
    assert act == (8000, None, 100.0, 3000.0, 1, 60)


def speed(member_conn, member, data_id, start, end, at, mps):
    put(member_conn, member, "speeds", data_id, start, end, samples=[{"time": at, "speed_mps": mps}])


def test_exercise_counts_as_exercise(conn, member):
    # 걷기는 평균 시속 4.0km(1.11m/s) 이상만 운동 시간에 넣는다 (S15P31A403-116, score-rules.md 2.3)
    put(conn, member, "exercise_sessions", "slow", "2026-10-06T07:00+09", "2026-10-06T07:30+09", exercise_type=79)
    speed(conn, member, "sp1", "2026-10-06T07:00+09", "2026-10-06T07:30+09", "2026-10-05T22:10:00Z", 0.89)  # 3.2km/h
    put(conn, member, "exercise_sessions", "fast", "2026-10-06T08:00+09", "2026-10-06T08:20+09", exercise_type=79)
    speed(conn, member, "sp2", "2026-10-06T08:00+09", "2026-10-06T08:20+09", "2026-10-05T23:10:00Z", 1.39)  # 5.0km/h
    put(conn, member, "exercise_sessions", "nospeed", "2026-10-06T12:00+09", "2026-10-06T12:15+09", exercise_type=79)
    put(conn, member, "exercise_sessions", "run", "2026-10-06T18:00+09", "2026-10-06T18:30+09", exercise_type=56)
    put(conn, member, "steps", "s1", "2026-10-06T00:00+09", "2026-10-06T23:59:59+09", count=9000)
    run(conn, member)

    rows = conn.execute(
        "SELECT data_id, counts_as_exercise FROM exercise_session WHERE end_user_id = %s ORDER BY start_at", (member,)
    ).fetchall()
    # 속도가 없는 걷기도 넣지 않는다. 걷기가 아닌 운동은 속도와 상관없이 넣는다
    assert rows == [("slow", False), ("fast", True), ("nospeed", False), ("run", True)]

    # 점수의 운동 시간은 저장된 인정 여부와 같은 세션만 더한다
    counted = one(
        conn,
        "SELECT sum(duration_minutes) FROM exercise_session WHERE end_user_id = %s AND counts_as_exercise",
        member,
    )[0]
    assert (
        one(conn, "SELECT exercise_minutes FROM lifestyle_score WHERE end_user_id = %s AND date = %s", member, D)[0]
        == counted
        == 50
    )

    # activity_daily도 인정된 세션만 센다: fast 20분 + run 30분 (S15P31A403-117)
    daily = "SELECT exercise_count, exercise_minutes FROM activity_daily WHERE end_user_id = %s AND date = %s"
    assert one(conn, daily, member, D) == (2, 50)


def test_walk_speed_is_distance_over_duration(conn, member):
    # 평균 속도는 거리 ÷ 운동 시간이다. 샘플은 움직이는 동안만 찍혀 평균이 실제보다 빠르다 (S15P31A403-260)
    put(conn, member, "exercise_sessions", "w", "2026-10-06T07:00+09", "2026-10-06T07:30+09", exercise_type=79)
    put(conn, member, "distances", "d", "2026-10-06T07:00+09", "2026-10-06T07:30+09", distance_m=1800.0)  # 3.6km/h
    speed(conn, member, "sp", "2026-10-06T07:00+09", "2026-10-06T07:30+09", "2026-10-05T22:10:00Z", 1.39)
    run(conn, member)

    sql = "SELECT avg_speed_mps, counts_as_exercise FROM exercise_session WHERE end_user_id = %s"
    assert one(conn, sql, member) == (Decimal("1.00"), False)


def test_walk_speed_without_distance_uses_one_source(conn, member):
    # 거리가 없거나 0이면 샘플 평균을 쓰되 출처 하나만 쓴다 (삼성 헬스 우선, 7.1)
    put(conn, member, "exercise_sessions", "w1", "2026-10-06T07:00+09", "2026-10-06T07:30+09", exercise_type=79)
    for data_id, app, mps in (("s1", SHEALTH, 1.0), ("p1", PHONE, 2.0), ("p2", PHONE, 2.0)):
        put(
            conn,
            member,
            "speeds",
            data_id,
            "2026-10-06T07:00+09",
            "2026-10-06T07:30+09",
            app=app,
            samples=[{"time": "2026-10-05T22:10:00Z", "speed_mps": mps}],
        )
    put(conn, member, "exercise_sessions", "w2", "2026-10-06T08:00+09", "2026-10-06T08:20+09", exercise_type=79)
    put(conn, member, "distances", "d0", "2026-10-06T08:00+09", "2026-10-06T08:20+09", distance_m=0.0)
    speed(conn, member, "sp", "2026-10-06T08:00+09", "2026-10-06T08:20+09", "2026-10-05T23:10:00Z", 1.39)
    run(conn, member)

    rows = conn.execute(
        "SELECT data_id, avg_speed_mps, counts_as_exercise FROM exercise_session WHERE end_user_id = %s "
        "ORDER BY start_at",
        (member,),
    ).fetchall()
    assert rows == [("w1", Decimal("1.00"), False), ("w2", Decimal("1.39"), True)]


def test_slow_walk_only_day_keeps_activity_row(conn, member):
    # 느린 걷기만 있는 날도 그날 행은 있고 운동은 0이다 (백엔드가 "기록 없음"이 아니라 0분으로 본다)
    put(conn, member, "exercise_sessions", "slow", "2026-10-06T07:00+09", "2026-10-06T07:30+09", exercise_type=79)
    speed(conn, member, "sp1", "2026-10-06T07:00+09", "2026-10-06T07:30+09", "2026-10-05T22:10:00Z", 0.89)
    run(conn, member)

    daily = "SELECT steps, exercise_count, exercise_minutes FROM activity_daily WHERE end_user_id = %s AND date = %s"
    assert one(conn, daily, member, D) == (None, 0, 0)
    assert one(conn, "SELECT count(*) FROM exercise_session WHERE end_user_id = %s", member)[0] == 1


def test_sleep_main_spo2_heart(conn, member):
    stages = [
        {"stage": 4, "startTime": "2026-10-05T14:00:00Z", "endTime": "2026-10-05T18:00:00Z"},
        {"stage": 5, "startTime": "2026-10-05T18:00:00Z", "endTime": "2026-10-05T20:00:00Z"},
    ]
    put(
        conn,
        member,
        "sleeps",
        "night",
        "2026-10-05T23:00+09",
        "2026-10-06T05:00+09",
        sleep_duration_total="null",
        stages=stages,
    )
    put(
        conn,
        member,
        "sleeps",
        "nap",
        "2026-10-06T14:00+09",
        "2026-10-06T14:40+09",
        sleep_duration_total="PT40M",
        stages=[],
    )
    put(conn, member, "oxygen_saturations", "o1", "2026-10-06T02:00+09", percentage=96.0)
    put(conn, member, "oxygen_saturations", "o2", "2026-10-06T03:00+09", percentage=94.0)
    put(conn, member, "oxygen_saturations", "o3", "2026-10-06T12:00+09", percentage=99.0)  # 수면 밖
    put(
        conn,
        member,
        "heart_rates",
        "h1",
        "2026-10-05T23:30+09",
        "2026-10-06T00:30+09",
        samples=hr(("2026-10-05T14:40:00Z", 58), ("2026-10-05T15:10:00Z", 52)),
    )

    run(conn, member)
    rows = conn.execute(
        "SELECT data_id, date, is_main, session_minutes, sleep_minutes, light_minutes, deep_minutes, "
        "spo2_count, spo2_avg, spo2_min, hr_avg, hr_min FROM sleep_session WHERE end_user_id = %s ORDER BY data_id",
        (member,),
    ).fetchall()
    nap, night = rows
    assert night == ("night", D, True, 360, 360, 240, 120, 2, 95.0, 94.0, 55.0, 52)
    assert nap[:5] == ("nap", D, False, 40, 40)
    assert nap[5:] == (None, None, None, None, None, None, None)  # 단계·산소포화도·심박 없음 → NULL

    # 90% 미만 측정 시각: 측정은 있고 90% 미만이 없으면 [] (0회), 측정이 없으면 NULL (S15P31A403-302)
    lows = conn.execute(
        "SELECT data_id, spo2_low_count, spo2_low_times FROM sleep_session WHERE end_user_id = %s ORDER BY data_id",
        (member,),
    ).fetchall()
    assert lows == [("nap", None, None), ("night", 0, [])]

    # 자정을 넘는 심박 기록의 샘플은 샘플 시각의 날짜로 나뉜다
    days = conn.execute(
        "SELECT date, sample_count FROM heart_daily WHERE end_user_id = %s ORDER BY date", (member,)
    ).fetchall()
    assert days == [(date(2026, 10, 5), 1), (D, 1)]


def test_sleep_spo2_low_times(conn, member):
    # 개수와 시각 목록은 같은 샘플로 만든다 (S15P31A403-302)
    # 수면 시작~끝 양 끝 포함, 70% 미만 이상치 제외, 90.0은 세지 않음
    put(conn, member, "sleeps", "night", "2026-10-05T23:00+09", "2026-10-06T05:00+09", stages=[])
    put(conn, member, "oxygen_saturations", "o1", "2026-10-06T03:00+09", percentage=88.0)
    put(conn, member, "oxygen_saturations", "o2", "2026-10-05T23:00+09", percentage=89.5)  # 시작 시각 (포함)
    put(conn, member, "oxygen_saturations", "o3", "2026-10-06T01:00+09", percentage=90.0)  # 90.0은 세지 않음
    put(conn, member, "oxygen_saturations", "o4", "2026-10-06T02:00+09", percentage=60.0)  # 이상치
    put(conn, member, "oxygen_saturations", "o5", "2026-10-06T03:00+09", percentage=87.0, app="other")  # 같은 시각
    put(conn, member, "oxygen_saturations", "o6", "2026-10-06T05:00+09", percentage=85.0)  # 끝 시각 (포함)
    put(conn, member, "oxygen_saturations", "o7", "2026-10-06T05:01+09", percentage=80.0)  # 수면 밖

    run(conn, member)
    count, low, times = one(
        conn, "SELECT spo2_count, spo2_low_count, spo2_low_times FROM sleep_session WHERE end_user_id = %s", member
    )
    kst = timezone(timedelta(hours=9))
    assert (count, low) == (5, 4)
    assert times == [
        datetime(2026, 10, 5, 23, 0, tzinfo=kst),
        datetime(2026, 10, 6, 3, 0, tzinfo=kst),
        datetime(2026, 10, 6, 3, 0, tzinfo=kst),
        datetime(2026, 10, 6, 5, 0, tzinfo=kst),
    ]


def test_sleep_spo2_low_times_check_constraint(conn, member):
    # 시각 목록 길이가 개수와 다르면 DB가 막는다
    put(conn, member, "sleeps", "night", "2026-10-05T23:00+09", "2026-10-06T05:00+09", stages=[])
    put(conn, member, "oxygen_saturations", "o1", "2026-10-06T03:00+09", percentage=88.0)
    run(conn, member)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE sleep_session SET spo2_low_times = '{}' WHERE end_user_id = %s", (member,))


def test_sleep_heart_only_in_asleep_stages(conn, member):
    # 23:00~23:20 잠들기 전 깸, 23:20~05:00 얕은 잠, 03:00~03:10 중간에 깸 (S15P31A403-210)
    stages = [
        {"stage": 1, "startTime": "2026-10-05T14:00:00Z", "endTime": "2026-10-05T14:20:00Z"},
        {"stage": 4, "startTime": "2026-10-05T14:20:00Z", "endTime": "2026-10-05T18:00:00Z"},
        {"stage": 1, "startTime": "2026-10-05T18:00:00Z", "endTime": "2026-10-05T18:10:00Z"},
        {"stage": 4, "startTime": "2026-10-05T18:10:00Z", "endTime": "2026-10-05T20:00:00Z"},
    ]
    put(conn, member, "sleeps", "night", "2026-10-05T23:00+09", "2026-10-06T05:00+09", stages=stages)
    # 단계 기록이 없는 낮잠은 세션 전체 심박을 쓴다
    put(conn, member, "sleeps", "nap", "2026-10-06T14:00+09", "2026-10-06T14:40+09", stages=[])
    samples = hr(
        ("2026-10-05T14:10:00Z", 80),  # 잠들기 전
        ("2026-10-05T15:00:00Z", 56),
        ("2026-10-05T18:05:00Z", 90),  # 중간에 깸
        ("2026-10-05T19:00:00Z", 54),
        ("2026-10-06T05:10:00Z", 70),  # 낮잠
        ("2026-10-06T05:30:00Z", 66),
    )
    put(conn, member, "heart_rates", "h1", "2026-10-05T23:00+09", "2026-10-06T14:40+09", samples=samples)

    run(conn, member)
    rows = conn.execute(
        "SELECT data_id, hr_avg, hr_min, hr_max FROM sleep_session WHERE end_user_id = %s ORDER BY data_id",
        (member,),
    ).fetchall()
    assert rows == [("nap", Decimal("68.0"), 66, 70), ("night", Decimal("55.0"), 54, 56)]


def test_split_night_is_merged_across_midnight(conn, member):
    # 23:00~23:50 자다 깨고(깬 날짜 10-05) 00:20~06:00 다시 잠(깬 날짜 10-06) + 낮잠. 앞의 둘은 한 밤이다
    put(
        conn,
        member,
        "sleeps",
        "early",
        "2026-10-05T23:00+09",
        "2026-10-05T23:50+09",
        sleep_duration_total="PT45M",
        stages=[{"stage": 4, "startTime": "2026-10-05T14:00:00Z", "endTime": "2026-10-05T14:50:00Z"}],
    )
    put(
        conn,
        member,
        "sleeps",
        "late",
        "2026-10-06T00:20+09",
        "2026-10-06T06:00+09",
        sleep_duration_total="PT5H20M",
        stages=[{"stage": 5, "startTime": "2026-10-05T15:20:00Z", "endTime": "2026-10-05T21:00:00Z"}],
    )
    put(conn, member, "sleeps", "nap", "2026-10-06T14:00+09", "2026-10-06T14:40+09", sleep_duration_total="PT40M")
    put(conn, member, "oxygen_saturations", "o1", "2026-10-05T23:30+09", percentage=95.0)  # 앞 조각 안
    put(conn, member, "oxygen_saturations", "o2", "2026-10-06T03:00+09", percentage=97.0)  # 뒤 조각 안

    run(conn, member)
    rows = conn.execute(
        "SELECT data_id, date, is_main, start_at, session_minutes, sleep_minutes, awake_minutes, light_minutes, "
        "deep_minutes, spo2_count, jsonb_array_length(sleep_timeline) FROM sleep_session "
        "WHERE end_user_id = %s ORDER BY start_at",
        (member,),
    ).fetchall()
    assert len(rows) == 2  # 묶은 밤 + 낮잠. 10-05 날짜의 조각 행은 없다
    night, nap = rows
    data_id, d, is_main, _, session, slept, awake, light, deep, spo2_count, timeline_len = night
    assert (data_id, d, is_main) == ("early", D, True)  # data_id는 첫 조각, 날짜는 마지막 깬 날짜
    assert (session, slept) == (420, 365)  # 23:00~06:00, 잔 시간은 45 + 320
    assert (awake, light, deep) == (30, 50, 340)  # 조각 사이 30분은 깸
    assert spo2_count == 2 and timeline_len == 3  # 얕은 잠, 깸(간격), 깊은 잠
    assert nap[:3] == ("nap", D, False)


def test_merged_sleep_replaces_old_fragment_rows(conn, member):
    # 처음엔 앞 조각만 들어와 따로 저장됐다가, 뒤 조각이 늦게 들어오면 한 행으로 바뀐다
    put(conn, member, "sleeps", "early", "2026-10-06T01:00+09", "2026-10-06T03:00+09", sleep_duration_total="PT2H")
    run(conn, member)
    assert one(conn, "SELECT count(*) FROM sleep_session WHERE end_user_id = %s", member)[0] == 1

    put(conn, member, "sleeps", "late", "2026-10-06T03:20+09", "2026-10-06T07:00+09", sleep_duration_total="PT3H40M")
    run(conn, member)
    rows = conn.execute(
        "SELECT data_id, session_minutes, sleep_minutes FROM sleep_session WHERE end_user_id = %s", (member,)
    ).fetchall()
    assert rows == [("early", 360, 340)]


def test_daily_heart_avg_hour_weighted(conn, member):
    # 8시 평소 측정 1개(60), 19시 연속 측정 60개(140)
    burst = [(f"2026-10-06T10:00:{i:02d}Z", 140) for i in range(60)]
    put(
        conn,
        member,
        "heart_rates",
        "h1",
        "2026-10-06T08:00+09",
        "2026-10-06T08:01+09",
        samples=hr(("2026-10-05T23:00:00Z", 60)),
    )
    put(conn, member, "heart_rates", "h2", "2026-10-06T19:00+09", "2026-10-06T19:01+09", samples=hr(*burst))
    run(conn, member)
    avg, count = one(conn, "SELECT hr_avg, sample_count FROM heart_daily WHERE end_user_id = %s", member)
    assert (avg, count) == (Decimal("100.0"), 61)  # (60 + 140) / 2. 모든 샘플 평균이면 약 138.7


def test_outliers_left_out_of_aggregates(conn, member):
    # 심박: 250 샘플은 빼고 최저·최고·평균을 낸다
    put(
        conn,
        member,
        "heart_rates",
        "h1",
        "2026-10-06T08:00+09",
        "2026-10-06T08:30+09",
        samples=hr(("2026-10-05T23:00:00Z", 60), ("2026-10-05T23:10:00Z", 250), ("2026-10-05T23:20:00Z", 80)),
    )
    # 걸음: 하루 15만 보는 이상치 → 그날 걸음 결측
    put(conn, member, "steps", "s1", "2026-10-06T00:00+09", "2026-10-06T23:59:59+09", count=150000)
    # 운동: 축구 7시간(기본 6시간 초과)은 빼고 그 안의 칼로리도 하루 합계에서 뺀다. 걷기 7시간(8시간까지)은 둔다
    put(conn, member, "exercise_sessions", "soccer", "2026-10-06T09:00+09", "2026-10-06T16:00+09", exercise_type=64)
    put(conn, member, "calories_burned", "c1", "2026-10-06T09:00+09", "2026-10-06T16:00+09", energy_kcal=900.0)
    put(conn, member, "exercise_sessions", "walk", "2026-10-06T16:30+09", "2026-10-06T23:30+09", exercise_type=79)
    # 걷기는 시속 5km로 넣어 운동으로 센다 (느린 걷기는 운동 횟수·시간에서 빠진다, S15P31A403-117)
    speed(conn, member, "sp1", "2026-10-06T16:30+09", "2026-10-06T23:30+09", "2026-10-06T08:00:00Z", 1.39)
    put(conn, member, "calories_burned", "c2", "2026-10-06T16:30+09", "2026-10-06T23:30+09", energy_kcal=500.0)
    put(conn, member, "calories_burned", "c3", "2026-10-06T16:40+09", "2026-10-06T16:50+09", energy_kcal=-30.0)
    # 신체: 키 175(m 단위 실수)는 빼고, 체중 70은 둔다
    put(conn, member, "heights", "b1", "2026-10-06T07:00+09", height_m=175.0)
    put(conn, member, "weights", "b2", "2026-10-06T07:00+09", weight_kg=70.0)

    run(conn, member)
    assert one(conn, "SELECT hr_avg, hr_min, hr_max, sample_count FROM heart_daily WHERE end_user_id = %s", member) == (
        Decimal("70.0"),
        60,
        80,
        2,
    )
    act = one(
        conn,
        "SELECT steps, exercise_calories_kcal, exercise_count, exercise_minutes FROM activity_daily "
        "WHERE end_user_id = %s",
        member,
    )
    assert act == (None, Decimal("500.0"), 1, 420)  # 걷기만 남고, 음수 칼로리도 뺀다
    assert conn.execute("SELECT data_id FROM exercise_session WHERE end_user_id = %s", (member,)).fetchall() == [
        ("walk",)
    ]
    assert conn.execute("SELECT metric FROM body_daily WHERE end_user_id = %s", (member,)).fetchall() == [("weights",)]


def test_sleep_outliers(conn, member):
    # 15시간 수면은 이상치로 빼고, 잔 시간이 세션보다 긴 수면은 잔 시간만 NULL. 70% 미만 산소포화도는 뺀다
    put(
        conn,
        member,
        "sleeps",
        "long",
        "2026-10-04T20:00+09",
        "2026-10-05T11:00+09",
        sleep_duration_total="PT14H",
    )
    put(
        conn,
        member,
        "sleeps",
        "odd",
        "2026-10-05T23:00+09",
        "2026-10-06T03:00+09",
        sleep_duration_total="PT7H",
    )
    put(conn, member, "oxygen_saturations", "o1", "2026-10-06T01:00+09", percentage=95.0)
    put(conn, member, "oxygen_saturations", "o2", "2026-10-06T02:00+09", percentage=50.0)

    run(conn, member)
    rows = conn.execute(
        "SELECT data_id, session_minutes, sleep_minutes, spo2_count FROM sleep_session WHERE end_user_id = %s",
        (member,),
    ).fetchall()
    assert rows == [("odd", 240, None, 1)]


def snapshot(conn, member, data_id, modified, count):
    conn.execute(
        "INSERT INTO step_snapshot (end_user_id, data_id, modified_at, step_count) VALUES (%s, %s, %s, %s)",
        (member, data_id, modified, count),
    )


def test_hourly_steps_from_snapshots(conn, member):
    put(conn, member, "steps", "day1", "2026-10-06T00:00+09", "2026-10-06T23:59:59+09", count=3000)
    snapshot(conn, member, "day1", "2026-10-06T09:10+09", 1000)
    snapshot(conn, member, "day1", "2026-10-06T09:40+09", 1500)
    snapshot(conn, member, "day1", "2026-10-06T13:05+09", 3000)

    run(conn, member)
    steps, hourly = one(conn, "SELECT steps, hourly_steps FROM activity_daily WHERE end_user_id = %s", member)
    assert steps == 3000
    assert (hourly[9], hourly[13], sum(hourly)) == (1500, 1500, 3000)


SHEALTH = "com.sec.android.app.shealth"
PHONE = "com.android.healthconnect.phone"


def test_steps_and_distance_use_one_source(conn, member):
    # 삼성 헬스 하루 기록 + 시간이 겹치는 폰 Health Connect 짧은 기록 (S15P31A403-209)
    put(conn, member, "steps", "sh", "2026-10-06T00:00+09", "2026-10-06T23:59:59+09", app=SHEALTH, count=8000)
    put(conn, member, "steps", "p1", "2026-10-06T08:00+09", "2026-10-06T08:09+09", app=PHONE, count=900)
    put(conn, member, "steps", "p2", "2026-10-06T18:00+09", "2026-10-06T18:05+09", app=PHONE, count=500)
    # 다음 날은 폰 기록만 있다
    put(conn, member, "steps", "p3", "2026-10-07T09:00+09", "2026-10-07T09:08+09", app=PHONE, count=700)
    # 운동 10:00~11:00 안에 삼성 헬스 거리와 폰 걷기 거리가 겹치고, 운동 밖에도 폰 거리가 있다
    put(
        conn,
        member,
        "exercise_sessions",
        "ex1",
        "2026-10-06T10:00+09",
        "2026-10-06T11:00+09",
        app=SHEALTH,
        exercise_type=56,
    )
    put(conn, member, "distances", "d1", "2026-10-06T10:00+09", "2026-10-06T11:00+09", app=SHEALTH, distance_m=3000.0)
    put(conn, member, "distances", "d2", "2026-10-06T10:10+09", "2026-10-06T10:19+09", app=PHONE, distance_m=600.0)
    put(conn, member, "distances", "d3", "2026-10-06T18:00+09", "2026-10-06T18:05+09", app=PHONE, distance_m=400.0)

    run(conn, member)
    rows = conn.execute(
        "SELECT date, steps, hourly_steps, exercise_distance_m FROM activity_daily "
        "WHERE end_user_id = %s ORDER BY date",
        (member,),
    ).fetchall()
    assert [(d, steps, dist) for d, steps, _, dist in rows] == [
        (D, 8000, Decimal("3000.0")),
        (D + timedelta(days=1), 700, None),
    ]
    assert sum(rows[1][2]) == 700 and rows[1][2][9] == 700  # 폰 기록은 시작 시간대에 넣는다
    assert one(conn, "SELECT distance_m FROM exercise_session WHERE end_user_id = %s", member) == (3000.0,)


LINK = "io.candiy.healthconnect.quickstart"


def test_calories_use_one_source(conn, member):
    # 삼성 헬스 운동 칼로리와 같은 시간에 워치 수집 앱(LiNK) 15분 칼로리가 온다 (S15P31A403-231)
    put(
        conn,
        member,
        "exercise_sessions",
        "ex1",
        "2026-10-06T10:00+09",
        "2026-10-06T11:00+09",
        app=SHEALTH,
        exercise_type=56,
    )
    put(
        conn,
        member,
        "calories_burned",
        "c1",
        "2026-10-06T10:00+09",
        "2026-10-06T11:00+09",
        app=SHEALTH,
        energy_kcal=200.0,
    )
    put(conn, member, "calories_burned", "l1", "2026-10-06T10:00+09", "2026-10-06T10:15+09", app=LINK, energy_kcal=50.0)
    put(conn, member, "calories_burned", "l2", "2026-10-06T10:15+09", "2026-10-06T10:30+09", app=LINK, energy_kcal=50.0)
    # 다음 날은 운동 없이 LiNK 15분 칼로리만 있다 → 운동 칼로리가 아니라 활동 행도 생기지 않는다
    put(conn, member, "calories_burned", "l3", "2026-10-07T09:00+09", "2026-10-07T09:15+09", app=LINK, energy_kcal=30.0)

    run(conn, member)
    rows = conn.execute(
        "SELECT date, exercise_calories_kcal FROM activity_daily WHERE end_user_id = %s ORDER BY date", (member,)
    ).fetchall()
    assert rows == [(D, 200)]
    assert one(conn, "SELECT calories_kcal FROM exercise_session WHERE end_user_id = %s", member) == (200,)


def test_late_snapshot_triggers_reaggregation(conn, member):
    # 처음엔 마지막 버전만 있어 모두 13시에 들어갔다가, 이전 버전이 늦게 들어오면 다시 나눈다
    put(conn, member, "steps", "day1", "2026-10-06T00:00+09", "2026-10-06T23:59:59+09", count=3000)
    snapshot(conn, member, "day1", "2026-10-06T13:05+09", 3000)
    run(conn, member)
    assert one(conn, "SELECT hourly_steps FROM activity_daily WHERE end_user_id = %s", member)[0][13] == 3000

    snapshot(conn, member, "day1", "2026-10-06T09:10+09", 1000)
    run(conn, member)
    hourly = one(conn, "SELECT hourly_steps FROM activity_daily WHERE end_user_id = %s", member)[0]
    assert (hourly[9], hourly[13]) == (1000, 2000)


def test_body_last_measurement(conn, member):
    put(conn, member, "weights", "w1", "2026-10-06T08:00+09", weight_kg=70.2)
    put(conn, member, "weights", "w2", "2026-10-06T21:00+09", weight_kg=70.8)
    run(conn, member)
    assert one(conn, "SELECT value, unit, data_id FROM body_daily WHERE end_user_id = %s", member) == (
        Decimal("70.80"),
        "kg",
        "w2",
    )


def test_only_changed_dates_and_idempotent(conn, member):
    put(conn, member, "steps", "s1", "2026-10-06T09:00+09", "2026-10-06T10:00+09", count=100)
    assert dates_to_aggregate(conn, member) == {date(2026, 10, 5), D, date(2026, 10, 7)}
    run(conn, member)
    first = one(conn, "SELECT steps, computed_at FROM activity_daily WHERE end_user_id = %s", member)

    assert dates_to_aggregate(conn, member) == set()  # 바뀐 원본이 없으면 다시 계산하지 않는다

    run(conn, member, all_dates=True)  # 다시 계산해도 결과가 같다
    again = one(conn, "SELECT steps, computed_at FROM activity_daily WHERE end_user_id = %s", member)
    assert again[0] == first[0] and again[1] > first[1]
    assert one(conn, "SELECT count(*) FROM activity_daily WHERE end_user_id = %s", member)[0] == 1

    # 늦게 들어온 같은 날 기록 → 그날만 다시 계산된다
    put(conn, member, "steps", "s2", "2026-10-06T18:00+09", "2026-10-06T19:00+09", count=50)
    run(conn, member)
    assert one(conn, "SELECT steps FROM activity_daily WHERE end_user_id = %s", member)[0] == 150


def test_late_heart_fills_sleep(conn, member):
    put(
        conn,
        member,
        "sleeps",
        "night",
        "2026-10-05T23:00+09",
        "2026-10-06T05:00+09",
        sleep_duration_total="PT6H",
        stages=[],
    )
    run(conn, member)
    assert one(conn, "SELECT hr_avg FROM sleep_session WHERE end_user_id = %s", member)[0] is None

    # 수면보다 늦게 동기화된 심박 (전날 밤 기록) → 다음 날짜의 수면 행도 다시 계산된다
    put(
        conn,
        member,
        "heart_rates",
        "h1",
        "2026-10-05T23:30+09",
        "2026-10-05T23:59+09",
        samples=hr(("2026-10-05T14:40:00Z", 55)),
    )
    run(conn, member)
    assert one(conn, "SELECT hr_avg FROM sleep_session WHERE end_user_id = %s", member)[0] == 55.0


def night(conn, member, data_id, bed, wake, bpm):
    """bed~wake(KST, 같은 해 10월) 수면 1회와 그 안의 심박 샘플 2개."""
    put(conn, member, "sleeps", data_id, bed, wake, sleep_duration_total="PT7H0M", stages=[])
    start = datetime.fromisoformat(bed)
    samples = hr(
        ((start + timedelta(hours=1)).astimezone(UTC).isoformat().replace("+00:00", "Z"), bpm),
        ((start + timedelta(hours=2)).astimezone(UTC).isoformat().replace("+00:00", "Z"), bpm),
    )
    put(conn, member, "heart_rates", f"hr-{data_id}", bed, wake, samples=samples)


def score_row(conn, member, d):
    return one(
        conn,
        "SELECT total_score, activity_score, activity_grade, sleep_score, heart_score, heart_grade, "
        "hr_baseline, hr_baseline_nights FROM lifestyle_score WHERE end_user_id = %s AND date = %s",
        member,
        d,
    )


def test_lifestyle_score_and_late_baseline(conn, member):
    # 10-01, 10-02에 깬 수면 (수면 중 심박 58, 66), 두 날 모두 걸음 8,000
    night(conn, member, "n1", "2026-09-30T23:00+09:00", "2026-10-01T06:00+09:00", 58)
    night(conn, member, "n2", "2026-10-01T23:00+09:00", "2026-10-02T06:00+09:00", 66)
    put(conn, member, "steps", "s1", "2026-10-01T00:00+09", "2026-10-01T23:59:59+09", count=8000)
    put(conn, member, "steps", "s2", "2026-10-02T00:00+09", "2026-10-02T23:59:59+09", count=8000)
    run(conn, member)

    # 10-01: 기준선 0밤 → 심박 데이터 없음. 활동 60(운동 없음), 수면 7시간 80 → (60 + 80) / 2 = 70
    assert score_row(conn, member, date(2026, 10, 1)) == (70, 60, "attention", 80, None, None, None, 0)
    # 10-02: 기준선 58.0(1밤), 66 - 58 = +8 → 55 관심. (60 + 80 + 55) / 3 = 65
    assert score_row(conn, member, date(2026, 10, 2)) == (65, 60, "attention", 80, 55, "attention", Decimal("58.0"), 1)

    # 09-30에 깬 수면(심박 50)이 늦게 들어옴. 다시 집계되는 날짜에 10-02는 없지만,
    # 10-02의 기준선이 (50 + 58) / 2 = 54.0으로 바뀌므로 점수도 다시 계산돼야 한다 (+12 → 40)
    night(conn, member, "n0", "2026-09-29T23:00+09:00", "2026-09-30T06:00+09:00", 50)
    run(conn, member)
    assert score_row(conn, member, date(2026, 10, 2))[4:] == (40, "care", Decimal("54.0"), 2)


def test_heart_uses_one_source_per_day(conn, member):
    # 같은 워치 심박이 삼성 헬스와 LiNK 둘로 온다. 그날 삼성 헬스가 있으면 삼성 헬스만 (S15P31A403-231)
    put(
        conn,
        member,
        "heart_rates",
        "sh",
        "2026-10-06T09:00+09",
        "2026-10-06T09:59+09",
        app=SHEALTH,
        samples=hr(("2026-10-06T00:10:00Z", 60), ("2026-10-06T00:20:00Z", 62)),
    )
    put(
        conn,
        member,
        "heart_rates",
        "lk",
        "2026-10-06T14:00+09",
        "2026-10-06T14:15+09",
        app=LINK,
        samples=hr(("2026-10-06T05:05:00Z", 100)),
    )
    # 다음 날은 LiNK 심박만 있다
    put(
        conn,
        member,
        "heart_rates",
        "lk2",
        "2026-10-07T09:00+09",
        "2026-10-07T09:15+09",
        app=LINK,
        samples=hr(("2026-10-07T00:05:00Z", 70)),
    )

    run(conn, member)
    rows = conn.execute(
        "SELECT date, hr_min, hr_max, sample_count FROM heart_daily WHERE end_user_id = %s ORDER BY date", (member,)
    ).fetchall()
    assert rows == [(D, 60, 62, 2), (D + timedelta(days=1), 70, 70, 1)]


def test_exercise_overlapping_samsung_session_is_dropped(conn, member):
    # 워치에서 시작한 걷기를 삼성 헬스와 LiNK가 둘 다 운동으로 보낸다. 겹치면 삼성 헬스 것만 센다
    put(
        conn,
        member,
        "exercise_sessions",
        "sh",
        "2026-10-06T08:00+09",
        "2026-10-06T08:40+09",
        app=SHEALTH,
        exercise_type=56,
    )
    put(
        conn,
        member,
        "exercise_sessions",
        "lk",
        "2026-10-06T08:02+09",
        "2026-10-06T08:38+09",
        app=LINK,
        exercise_type=79,
    )
    # 겹치지 않는 LiNK 걷기는 남는다
    put(
        conn,
        member,
        "exercise_sessions",
        "lk2",
        "2026-10-06T19:00+09",
        "2026-10-06T19:30+09",
        app=LINK,
        exercise_type=79,
    )

    run(conn, member)
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT data_id FROM exercise_session WHERE end_user_id = %s ORDER BY start_at", (member,)
        ).fetchall()
    ]
    assert ids == ["sh", "lk2"]
