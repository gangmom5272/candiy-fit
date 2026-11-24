"""수면 기록 결과의 어젯밤·그저께 밤 날짜 (calculation-rules.md 11.1 잠든 시각 구분과 같은 표기)."""

import json
from datetime import date, datetime, time, timedelta

import pytest

from coach_ai.adapters.memory import SleepSession
from coach_ai.agent.executor import ToolRequest
from coach_ai.tools.handlers import get_metric_summary, get_sleep_records
from coach_ai.tools.schemas import GetMetricSummary, GetSleepRecords
from tests.fakes import KST, D, FakeHealthData, request, sample_port

# 오늘은 2026-10-15 (tests/fakes.py 의 TODAY)
LINE = "오늘(10월 15일) 기준 어젯밤 잠은 10월 15일 수면, 그저께 밤 잠은 10월 14일 수면이다 (날짜는 깬 날)"


@pytest.mark.parametrize(
    ("period", "shown"),
    [
        ("last_7_days", True),
        ("days_ago:0", True),
        ("days_ago:1", True),
        ("days_ago:2", True),
        ("days_ago:3", False),
        ("dates:10-01..10-12", False),
        ("dates:10-01..10-13", True),
        ("last_week", False),
    ],
)
async def test_line_comes_only_when_period_reaches_two_days_ago(period, shown):
    payload = (await get_sleep_records(GetSleepRecords(period=period), request(sample_port()))).payload
    assert payload.get("relative_nights") == (LINE if shown else None)


async def test_line_comes_before_nights_and_without_records():
    payload = (await get_sleep_records(GetSleepRecords(period="days_ago:1"), request(sample_port()))).payload
    assert list(payload).index("relative_nights") < list(payload).index("nights")
    empty = FakeHealthData(now=datetime(2026, 10, 15, 12, 0, tzinfo=KST))
    out = await get_sleep_records(GetSleepRecords(period="yesterday"), ToolRequest("u9", D(2026, 10, 15), empty))
    assert out.payload["nights"] == [] and out.payload["relative_nights"] == LINE


@pytest.mark.parametrize(
    ("today", "line"),
    [
        (
            date(2026, 11, 1),
            "오늘(11월 1일) 기준 어젯밤 잠은 11월 1일 수면, 그저께 밤 잠은 10월 31일 수면이다 (날짜는 깬 날)",
        ),
        (
            date(2026, 11, 2),
            "오늘(11월 2일) 기준 어젯밤 잠은 11월 2일 수면, 그저께 밤 잠은 11월 1일 수면이다 (날짜는 깬 날)",
        ),
        (
            date(2027, 1, 1),
            "오늘(1월 1일) 기준 어젯밤 잠은 1월 1일 수면, 그저께 밤 잠은 12월 31일 수면이다 (날짜는 깬 날)",
        ),
    ],
)
async def test_line_uses_dates_across_month_and_year(today, line):
    now = datetime(today.year, today.month, today.day, 9, 0, tzinfo=KST)
    port = FakeHealthData(linked_from={"u1": D(2026, 9, 20)}, now=now)
    req = ToolRequest(user_id="u1", today=today, port=port)
    payload = (await get_sleep_records(GetSleepRecords(period="last_7_days"), req)).payload
    assert payload["relative_nights"] == line


async def test_line_follows_server_today_of_the_request():
    # 날짜는 질문한 날의 KST 오늘(ToolRequest.today)로 계산한다
    req = ToolRequest(user_id="u1", today=D(2026, 10, 14), port=sample_port())
    payload = (await get_sleep_records(GetSleepRecords(period="days_ago:0"), req)).payload
    assert (
        payload["relative_nights"]
        == "오늘(10월 14일) 기준 어젯밤 잠은 10월 14일 수면, 그저께 밤 잠은 10월 13일 수면이다 (날짜는 깬 날)"
    )


async def test_line_is_only_in_sleep_records():
    out = await get_metric_summary(GetMetricSummary(metric="sleep", period="last_7_days"), request(sample_port()))
    assert "어젯밤" not in json.dumps(out.payload, ensure_ascii=False)


def _kst(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST)


# (오늘, 오늘 깬 주 수면의 잠든 시각·깬 시각, 결과 안에서 그 잠을 부르는 이름, 안내 문장)
TODAY_SLEEPS = [
    pytest.param(
        D(2026, 10, 15),
        _kst(2026, 10, 15, 6, 0),
        _kst(2026, 10, 15, 13, 0),
        "오늘 수면",
        "오늘(10월 15일) 기준 오늘 깬 잠은 오늘 아침 6시 이후에 잠든 오늘 수면, "
        "그저께 밤 잠은 10월 14일 수면이다 (날짜는 깬 날)",
        id="at-0600",
    ),
    pytest.param(
        D(2026, 10, 15),
        _kst(2026, 10, 14, 16, 0),
        _kst(2026, 10, 15, 2, 0),
        "10월 15일 수면",
        "오늘(10월 15일) 기준 오늘 깬 잠은 어제 저녁 6시 전에 잠든 10월 15일 수면, "
        "그저께 밤 잠은 10월 14일 수면이다 (날짜는 깬 날)",
        id="prev-afternoon-past-midnight",
    ),
    pytest.param(
        D(2026, 10, 15),
        _kst(2026, 10, 15, 0, 0),
        _kst(2026, 10, 15, 7, 0),
        "어젯밤",
        "오늘(10월 15일) 기준 어젯밤 잠은 10월 15일 수면, 그저께 밤 잠은 10월 14일 수면이다 (날짜는 깬 날)",
        id="midnight",
    ),
    pytest.param(
        D(2026, 10, 15),
        _kst(2026, 10, 15, 5, 59),
        _kst(2026, 10, 15, 11, 0),
        "어젯밤",
        "오늘(10월 15일) 기준 어젯밤 잠은 10월 15일 수면, 그저께 밤 잠은 10월 14일 수면이다 (날짜는 깬 날)",
        id="0559",
    ),
    pytest.param(
        D(2026, 11, 1),
        _kst(2026, 10, 31, 16, 0),
        _kst(2026, 11, 1, 2, 0),
        "11월 1일 수면",
        "오늘(11월 1일) 기준 오늘 깬 잠은 어제 저녁 6시 전에 잠든 11월 1일 수면, "
        "그저께 밤 잠은 10월 31일 수면이다 (날짜는 깬 날)",
        id="month-end-afternoon",
    ),
    pytest.param(
        D(2026, 11, 1),
        _kst(2026, 10, 31, 23, 30),
        _kst(2026, 11, 1, 7, 0),
        "어젯밤",
        "오늘(11월 1일) 기준 어젯밤 잠은 11월 1일 수면, 그저께 밤 잠은 10월 31일 수면이다 (날짜는 깬 날)",
        id="month-end-night",
    ),
    pytest.param(
        D(2027, 1, 1),
        _kst(2027, 1, 1, 6, 0),
        _kst(2027, 1, 1, 12, 0),
        "오늘 수면",
        "오늘(1월 1일) 기준 오늘 깬 잠은 오늘 아침 6시 이후에 잠든 오늘 수면, "
        "그저께 밤 잠은 12월 31일 수면이다 (날짜는 깬 날)",
        id="new-year-0600",
    ),
    pytest.param(
        D(2027, 1, 1),
        _kst(2026, 12, 31, 23, 30),
        _kst(2027, 1, 1, 7, 0),
        "어젯밤",
        "오늘(1월 1일) 기준 어젯밤 잠은 1월 1일 수면, 그저께 밤 잠은 12월 31일 수면이다 (날짜는 깬 날)",
        id="new-year-night",
    ),
]


def _session(woke: date, start: datetime, end: datetime) -> SleepSession:
    """주 수면 하나. 산소포화도 90% 미만 기록을 넣어 spo2_low 도 그 잠의 이름을 쓴다."""
    return SleepSession(
        date=woke,
        start_at=start,
        end_at=end,
        is_main=True,
        sleep_minutes=300,
        spo2_low_count=1,
        spo2_low_times=(start + timedelta(minutes=30),),
    )


def _port(today: date, *sessions: SleepSession) -> FakeHealthData:
    now = datetime.combine(today, time(21), tzinfo=KST)
    return FakeHealthData(sleep={"u1": list(sessions)}, linked_from={"u1": D(2026, 9, 20)}, now=now)


@pytest.mark.parametrize("period", ["days_ago:0", "days_ago:1", "last_30_days"])
@pytest.mark.parametrize(("today", "start", "end", "name", "line"), TODAY_SLEEPS)
async def test_today_sleep_has_one_name_in_a_result(period, today, start, end, name, line):
    # 오늘 깬 잠은 어제 저녁 6시부터 오늘 새벽 6시 전에 잠들었을 때만 어젯밤이고, 그 밖에는 잠 이름 규칙대로 부른다.
    # days_ago:1 은 오늘 깬 잠이 기간 밖이라 두 줄을 계산한 보조 조회의 잠든 시각을 쓴다
    req = ToolRequest(user_id="u1", today=today, port=_port(today, _session(today, start, end)))
    payload = (await get_sleep_records(GetSleepRecords(period=period), req)).payload
    assert payload["relative_nights"] == line
    if period != "days_ago:1":
        assert f"깬 날 기준 {name} " in payload["spo2_low"]
    if period != "last_30_days":
        assert payload["last_night_vs_week"].startswith(f"{name}(")
    if name != "어젯밤":
        assert "어젯밤" not in json.dumps(payload, ensure_ascii=False)


# (오늘, 어제 깬 주 수면의 잠든 시각·깬 시각, 안내 문장)
YESTERDAY_SLEEPS = [
    pytest.param(
        D(2026, 10, 15),
        _kst(2026, 10, 13, 17, 59),
        _kst(2026, 10, 14, 3, 0),
        "오늘(10월 15일) 기준 어젯밤 잠은 10월 15일 수면, "
        "어제 깬 잠은 그저께 저녁 6시 전에 잠든 어제 수면이다 (날짜는 깬 날)",
        id="1759-two-days-ago",
    ),
    pytest.param(
        D(2026, 10, 15),
        _kst(2026, 10, 13, 18, 0),
        _kst(2026, 10, 14, 4, 0),
        LINE,
        id="1800-two-days-ago",
    ),
    pytest.param(D(2026, 10, 15), _kst(2026, 10, 14, 0, 0), _kst(2026, 10, 14, 7, 0), LINE, id="midnight"),
    pytest.param(D(2026, 10, 15), _kst(2026, 10, 14, 5, 59), _kst(2026, 10, 14, 11, 0), LINE, id="0559"),
    pytest.param(
        D(2026, 10, 15),
        _kst(2026, 10, 14, 6, 0),
        _kst(2026, 10, 14, 13, 0),
        "오늘(10월 15일) 기준 어젯밤 잠은 10월 15일 수면, "
        "어제 깬 잠은 어제 아침 6시 이후에 잠든 어제 수면이다 (날짜는 깬 날)",
        id="at-0600",
    ),
    pytest.param(
        D(2026, 11, 2),
        _kst(2026, 10, 31, 17, 59),
        _kst(2026, 11, 1, 2, 0),
        "오늘(11월 2일) 기준 어젯밤 잠은 11월 2일 수면, "
        "어제 깬 잠은 그저께 저녁 6시 전에 잠든 어제 수면이다 (날짜는 깬 날)",
        id="month-end-1759",
    ),
    pytest.param(
        D(2026, 11, 2),
        _kst(2026, 10, 31, 18, 0),
        _kst(2026, 11, 1, 2, 0),
        "오늘(11월 2일) 기준 어젯밤 잠은 11월 2일 수면, 그저께 밤 잠은 11월 1일 수면이다 (날짜는 깬 날)",
        id="month-end-1800",
    ),
    pytest.param(
        D(2027, 1, 2),
        _kst(2027, 1, 1, 6, 0),
        _kst(2027, 1, 1, 12, 0),
        "오늘(1월 2일) 기준 어젯밤 잠은 1월 2일 수면, "
        "어제 깬 잠은 어제 아침 6시 이후에 잠든 어제 수면이다 (날짜는 깬 날)",
        id="new-year-0600",
    ),
    pytest.param(
        D(2027, 1, 2),
        _kst(2026, 12, 31, 23, 30),
        _kst(2027, 1, 1, 7, 0),
        "오늘(1월 2일) 기준 어젯밤 잠은 1월 2일 수면, 그저께 밤 잠은 1월 1일 수면이다 (날짜는 깬 날)",
        id="new-year-night",
    ),
]


@pytest.mark.parametrize("period", ["days_ago:0", "days_ago:1", "last_30_days"])
@pytest.mark.parametrize(("today", "start", "end", "line"), YESTERDAY_SLEEPS)
async def test_yesterday_sleep_is_night_before_last_only_when_slept_at_night(period, today, start, end, line):
    # 어제 깬 잠은 그저께 저녁 6시부터 어제 새벽 6시 전에 잠들었을 때만 그저께 밤 잠이고, 그 밖에는 어제 수면이다.
    # days_ago:0 은 어제 깬 잠이 기간 밖이라 두 줄을 계산한 보조 조회의 잠든 시각을 쓴다
    yesterday = today - timedelta(days=1)
    req = ToolRequest(user_id="u1", today=today, port=_port(today, _session(yesterday, start, end)))
    payload = (await get_sleep_records(GetSleepRecords(period=period), req)).payload
    assert payload["relative_nights"] == line


async def test_line_names_both_day_sleeps_of_a_night_worker():
    # 야간 근무로 오늘과 어제 모두 아침에 잠들었다 (골든셋 synth-08 과 같은 경우)
    port = _port(
        D(2026, 10, 15),
        _session(D(2026, 10, 14), _kst(2026, 10, 14, 9, 0), _kst(2026, 10, 14, 16, 0)),
        _session(D(2026, 10, 15), _kst(2026, 10, 15, 9, 10), _kst(2026, 10, 15, 16, 30)),
    )
    payload = (await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))).payload
    assert payload["relative_nights"] == (
        "오늘(10월 15일) 기준 오늘 깬 잠은 오늘 아침 6시 이후에 잠든 오늘 수면, "
        "어제 깬 잠은 어제 아침 6시 이후에 잠든 어제 수면이다 (날짜는 깬 날)"
    )


async def test_line_keeps_night_words_when_starts_are_not_in_the_result():
    # 2일 전만 조회하면 오늘·어제 깬 잠도 두 줄도 결과에 없어, 잠든 시각을 모르는 잠처럼 어젯밤·그저께 밤으로 쓴다
    port = _port(
        D(2026, 10, 15),
        _session(D(2026, 10, 14), _kst(2026, 10, 14, 9, 0), _kst(2026, 10, 14, 16, 0)),
        _session(D(2026, 10, 15), _kst(2026, 10, 15, 6, 0), _kst(2026, 10, 15, 13, 0)),
    )
    payload = (await get_sleep_records(GetSleepRecords(period="days_ago:2"), request(port))).payload
    assert payload["relative_nights"] == LINE
    assert "last_night_vs_week" not in payload
