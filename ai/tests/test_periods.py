from datetime import UTC, date, datetime, timedelta

import pytest

from coach_ai.tools.periods import (
    COUNT_RANGES,
    MAX_LIST_DAYS,
    MAX_LOOKUP_DAYS,
    NAMED_EXPRESSIONS,
    DateRange,
    PeriodError,
    check_period,
    earliest_start,
    local_today,
    period_label,
    period_range,
    previous_period,
    range_expression,
    resolve_period,
    split_range,
)
from coach_ai.tools.schemas import PERIODS

D = date
# 평가 데이터셋 기준일과 같은 목요일
THU = D(2026, 10, 22)


@pytest.mark.parametrize(
    ("today", "period", "start", "end"),
    [
        # 2026-10-15 목요일
        (D(2026, 10, 15), "today", D(2026, 10, 15), D(2026, 10, 15)),
        (D(2026, 10, 15), "yesterday", D(2026, 10, 14), D(2026, 10, 14)),
        (D(2026, 10, 15), "last_7_days", D(2026, 10, 9), D(2026, 10, 15)),
        (D(2026, 10, 15), "last_14_days", D(2026, 10, 2), D(2026, 10, 15)),
        (D(2026, 10, 15), "last_30_days", D(2026, 9, 16), D(2026, 10, 15)),
        (D(2026, 10, 15), "this_week", D(2026, 10, 12), D(2026, 10, 15)),
        (D(2026, 10, 15), "last_week", D(2026, 10, 5), D(2026, 10, 11)),
        (D(2026, 10, 15), "this_month", D(2026, 10, 1), D(2026, 10, 15)),
        (D(2026, 10, 15), "last_month", D(2026, 9, 1), D(2026, 9, 30)),
        # 월초: 어제와 최근 7일이 지난달로 넘어간다
        (D(2026, 10, 1), "yesterday", D(2026, 9, 30), D(2026, 9, 30)),
        (D(2026, 10, 1), "last_7_days", D(2026, 9, 25), D(2026, 10, 1)),
        (D(2026, 10, 1), "this_month", D(2026, 10, 1), D(2026, 10, 1)),
        # 2026-10-01은 목요일이라 이번 주가 지난달에서 시작한다
        (D(2026, 10, 1), "this_week", D(2026, 9, 28), D(2026, 10, 1)),
        # 월말
        (D(2026, 10, 31), "this_month", D(2026, 10, 1), D(2026, 10, 31)),
        (D(2026, 10, 31), "last_month", D(2026, 9, 1), D(2026, 9, 30)),
        (D(2026, 3, 31), "last_month", D(2026, 2, 1), D(2026, 2, 28)),
        (D(2028, 3, 1), "last_month", D(2028, 2, 1), D(2028, 2, 29)),
        # 주 경계: 월요일은 이번 주가 오늘 하루, 일요일은 월요일부터 7일
        (D(2026, 10, 12), "this_week", D(2026, 10, 12), D(2026, 10, 12)),
        (D(2026, 10, 12), "last_week", D(2026, 10, 5), D(2026, 10, 11)),
        (D(2026, 10, 18), "this_week", D(2026, 10, 12), D(2026, 10, 18)),
        (D(2026, 10, 18), "last_week", D(2026, 10, 5), D(2026, 10, 11)),
        # 연말·연초
        (D(2027, 1, 1), "yesterday", D(2026, 12, 31), D(2026, 12, 31)),
        (D(2027, 1, 1), "last_month", D(2026, 12, 1), D(2026, 12, 31)),
        (D(2027, 1, 1), "last_30_days", D(2026, 12, 3), D(2027, 1, 1)),
        # 2027-01-01은 금요일
        (D(2027, 1, 1), "this_week", D(2026, 12, 28), D(2027, 1, 1)),
        (D(2027, 1, 4), "last_week", D(2026, 12, 28), D(2027, 1, 3)),
        (D(2026, 12, 31), "this_month", D(2026, 12, 1), D(2026, 12, 31)),
        # 지지난주: 지난주 바로 앞 주의 월~일
        (D(2026, 10, 15), "week_before_last", D(2026, 9, 28), D(2026, 10, 4)),
        # 월 경계: 오늘은 11월이고 지지난주는 모두 10월이다
        (D(2026, 11, 2), "week_before_last", D(2026, 10, 19), D(2026, 10, 25)),
        # 연말·연초: 연도를 걸친 주와, 오늘은 새해인데 지지난주는 모두 지난해인 경우
        (D(2027, 1, 13), "week_before_last", D(2026, 12, 28), D(2027, 1, 3)),
        (D(2027, 1, 4), "week_before_last", D(2026, 12, 21), D(2026, 12, 27)),
        (D(2027, 1, 1), "week_before_last", D(2026, 12, 14), D(2026, 12, 20)),
        # 윤일이 든 주
        (D(2028, 3, 15), "week_before_last", D(2028, 2, 28), D(2028, 3, 5)),
    ],
)
def test_resolve_period(today, period, start, end):
    assert resolve_period(period, today) == DateRange(start, end)


# 2026-10-12(월)~18(일) 주의 지지난주
SEP_28_WEEK = DateRange(D(2026, 9, 28), D(2026, 10, 4))


@pytest.mark.parametrize(
    ("day", "before", "after"),
    [
        # (KST 날짜, 그 전날 23:59 의 지지난주, 그날 00:00 의 지지난주). 월요일 0시에만 한 주 뒤로 넘어간다
        (D(2026, 10, 12), DateRange(D(2026, 9, 21), D(2026, 9, 27)), SEP_28_WEEK),  # 일 → 월
        (D(2026, 10, 13), SEP_28_WEEK, SEP_28_WEEK),  # 월 → 화
        (D(2026, 10, 14), SEP_28_WEEK, SEP_28_WEEK),  # 화 → 수
        (D(2026, 10, 15), SEP_28_WEEK, SEP_28_WEEK),  # 수 → 목
        (D(2026, 10, 16), SEP_28_WEEK, SEP_28_WEEK),  # 목 → 금
        (D(2026, 10, 17), SEP_28_WEEK, SEP_28_WEEK),  # 금 → 토
        (D(2026, 10, 18), SEP_28_WEEK, SEP_28_WEEK),  # 토 → 일
        (D(2026, 10, 19), SEP_28_WEEK, DateRange(D(2026, 10, 5), D(2026, 10, 11))),  # 일 → 월
    ],
)
def test_week_before_last_turns_over_at_kst_monday_midnight(day, before, after):
    # KST 0시는 UTC 로 전날 15시다. 서버 시각(UTC)의 날짜가 아니라 한국 시간 날짜로 주를 나눈다
    midnight = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC) - timedelta(days=1)
    assert resolve_period("week_before_last", local_today(midnight - timedelta(minutes=1))) == before
    assert resolve_period("week_before_last", local_today(midnight)) == after


def test_week_before_last_is_the_monday_to_sunday_right_before_last_week():
    # 월말·윤일·연도 경계가 모두 들어가게 윤년을 포함한 2년을 순회한다
    for offset in range(731):
        today = D(2027, 1, 1) + timedelta(days=offset)
        r = resolve_period("week_before_last", today)
        assert (r.start.weekday(), r.end.weekday(), len(r.days())) == (0, 6, 7), today
        assert r.end + timedelta(days=1) == resolve_period("last_week", today).start, today


@pytest.mark.parametrize("period", PERIODS)
def test_every_period_ends_by_today(period):
    # 포트 계약상 end 는 KST 오늘을 넘지 않는다. 월말·윤일·주 경계가 모두 들어가게 윤년을 포함한 2년을 순회한다
    for offset in range(731):
        today = D(2027, 1, 1) + timedelta(days=offset)
        r = resolve_period(period, today)
        assert r.start <= r.end <= today, (period, today)


@pytest.mark.parametrize("period", PERIODS)
@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 12, 31, 14, 59, tzinfo=UTC),  # KST 12/31 23:59
        datetime(2026, 12, 31, 15, 0, tzinfo=UTC),  # KST 1/1 00:00, UTC 로는 아직 12/31
        datetime(2026, 10, 18, 15, 0, tzinfo=UTC),  # KST 월요일 00:00
        datetime(2028, 2, 28, 15, 30, tzinfo=UTC),  # KST 윤일 00:30
    ],
)
def test_period_end_never_passes_kst_today(period, now):
    today = local_today(now)
    assert resolve_period(period, today).end <= today


def test_fixed_length_periods():
    today = D(2026, 1, 3)
    assert len(resolve_period("last_7_days", today).days()) == 7
    assert len(resolve_period("last_14_days", today).days()) == 14
    assert len(resolve_period("last_30_days", today).days()) == 30
    assert len(resolve_period("last_week", today).days()) == 7
    assert len(resolve_period("week_before_last", today).days()) == 7


def test_local_today_uses_seoul_date():
    # UTC 15:30은 한국 시간으로 다음 날 00:30이다
    assert local_today(datetime(2026, 12, 31, 15, 30, tzinfo=UTC)) == D(2027, 1, 1)
    assert local_today(datetime(2026, 12, 31, 14, 59, tzinfo=UTC)) == D(2026, 12, 31)


def test_local_today_requires_aware_datetime():
    with pytest.raises(ValueError):
        local_today(datetime(2026, 10, 15, 12, 0))


def test_named_expressions_follow_team_table():
    # 팀에서 정한 이름 기간과 새 표현의 관계. 구현의 정의를 그대로 옮기지 않고 표에서 따로 적는다
    assert NAMED_EXPRESSIONS == {
        "today": "days_ago:0",
        "yesterday": "days_ago:1",
        "last_7_days": "recent_days:7",
        "last_14_days": "recent_days:14",
        "last_30_days": "recent_days:30",
        "this_week": "weeks_ago:0",
        "last_week": "weeks_ago:1",
        "week_before_last": "weeks_ago:2",
        "this_month": "months_ago:0",
        "last_month": "months_ago:1",
    }
    assert COUNT_RANGES == {
        "days_ago": (0, 365),
        "recent_days": (1, 366),
        "weeks_ago": (0, 52),
        "months_ago": (0, 12),
        "recent_months": (1, 12),
    }
    # 날짜별 조회·수면 기록·운동 기록은 31일까지, 백엔드 건강 조회는 양 끝 포함 366일까지 받는다
    assert (MAX_LIST_DAYS, MAX_LOOKUP_DAYS) == (31, 366)


@pytest.mark.parametrize("period", PERIODS)
def test_named_period_resolves_to_same_range_as_its_expression(period):
    # 월말·윤일·주 경계가 모두 들어가게 윤년을 포함한 2년을 순회한다
    for offset in range(731):
        today = D(2027, 1, 1) + timedelta(days=offset)
        assert resolve_period(period, today) == resolve_period(NAMED_EXPRESSIONS[period], today), (period, today)


@pytest.mark.parametrize(
    ("today", "period", "start", "end"),
    [
        # N일 전의 하루. 0 은 오늘, 1 은 어제
        (THU, "days_ago:0", THU, THU),
        (THU, "days_ago:1", D(2026, 10, 21), D(2026, 10, 21)),
        (THU, "days_ago:5", D(2026, 10, 17), D(2026, 10, 17)),
        (THU, "days_ago:365", D(2025, 10, 22), D(2025, 10, 22)),
        # 최근 N일은 오늘을 포함한다
        (THU, "recent_days:1", THU, THU),
        (THU, "recent_days:7", D(2026, 10, 16), THU),
        (THU, "recent_days:366", D(2025, 10, 22), THU),
        # N주 전의 한 주는 월요일-일요일이다. 0 은 이번 주 월요일-오늘
        (THU, "weeks_ago:0", D(2026, 10, 19), THU),
        (THU, "weeks_ago:1", D(2026, 10, 12), D(2026, 10, 18)),
        (THU, "weeks_ago:3", D(2026, 9, 28), D(2026, 10, 4)),
        (D(2026, 10, 31), "weeks_ago:3", D(2026, 10, 5), D(2026, 10, 11)),
        # 월요일은 이번 주가 오늘 하루이고, 일요일은 월요일부터 7일이다
        (D(2026, 10, 19), "weeks_ago:0", D(2026, 10, 19), D(2026, 10, 19)),
        (D(2026, 10, 25), "weeks_ago:0", D(2026, 10, 19), D(2026, 10, 25)),
        (D(2026, 10, 25), "weeks_ago:1", D(2026, 10, 12), D(2026, 10, 18)),
        # 월요일에는 52주 전 월요일이 12개월 전 같은 날짜(2025-10-19)보다 뒤다
        (D(2026, 10, 19), "weeks_ago:52", D(2025, 10, 20), D(2025, 10, 26)),
        # 연도를 걸친 주
        (D(2027, 1, 6), "weeks_ago:1", D(2026, 12, 28), D(2027, 1, 3)),
        # N달 전의 한 달은 1일-말일이다. 0 은 이번 달 1일-오늘
        (THU, "months_ago:0", D(2026, 10, 1), THU),
        (THU, "months_ago:1", D(2026, 9, 1), D(2026, 9, 30)),
        (THU, "months_ago:2", D(2026, 8, 1), D(2026, 8, 31)),
        (D(2027, 1, 15), "months_ago:2", D(2026, 11, 1), D(2026, 11, 30)),
        (D(2026, 3, 31), "months_ago:1", D(2026, 2, 1), D(2026, 2, 28)),
        (D(2028, 3, 31), "months_ago:1", D(2028, 2, 1), D(2028, 2, 29)),
        # 1일에는 12달 전 1일이 12개월 전 같은 날짜다
        (D(2026, 10, 1), "months_ago:12", D(2025, 10, 1), D(2025, 10, 31)),
        # 최근 N달은 N달 전 같은 날짜의 다음 날부터 오늘까지다
        (THU, "recent_months:1", D(2026, 9, 23), THU),
        (THU, "recent_months:6", D(2026, 4, 23), THU),
        (THU, "recent_months:12", D(2025, 10, 23), THU),
        # 그달에 같은 날짜가 없으면 말일을 같은 날짜로 본다 (2월 31일 → 2월 28일, 다음 날 3월 1일)
        (D(2026, 3, 31), "recent_months:1", D(2026, 3, 1), D(2026, 3, 31)),
        (D(2026, 5, 31), "recent_months:3", D(2026, 3, 1), D(2026, 5, 31)),
        (D(2028, 2, 29), "recent_months:1", D(2028, 1, 30), D(2028, 2, 29)),
        (D(2028, 2, 29), "recent_months:12", D(2027, 3, 1), D(2028, 2, 29)),
        # 날짜 지정
        (THU, "date:10-21", D(2026, 10, 21), D(2026, 10, 21)),
        (THU, "date:10-22", THU, THU),
        (THU, "date:2026-10-21", D(2026, 10, 21), D(2026, 10, 21)),
        (THU, "date:2025-10-22", D(2025, 10, 22), D(2025, 10, 22)),
        (THU, "date:9-5", D(2026, 9, 5), D(2026, 9, 5)),
        (THU, "dates:10-03..10-10", D(2026, 10, 3), D(2026, 10, 10)),
        (THU, "dates:2026-10-03..2026-10-10", D(2026, 10, 3), D(2026, 10, 10)),
        (THU, "dates:10-21..10-21", D(2026, 10, 21), D(2026, 10, 21)),
    ],
)
def test_resolve_period_expressions(today, period, start, end):
    assert resolve_period(period, today) == DateRange(start, end)


@pytest.mark.parametrize(
    ("today", "period", "start", "end"),
    [
        # 연도가 없는 날짜는 오늘 기준으로 가장 최근에 지난 그 날짜다
        (THU, "date:10-23", D(2025, 10, 23), D(2025, 10, 23)),
        (D(2027, 1, 5), "date:12-25", D(2026, 12, 25), D(2026, 12, 25)),
        (D(2026, 1, 1), "date:01-01", D(2026, 1, 1), D(2026, 1, 1)),
        (D(2028, 10, 22), "date:02-29", D(2028, 2, 29), D(2028, 2, 29)),
        # 날짜 범위는 시작 날짜를 가장 최근에 지난 날짜로 정하고, 끝 날짜는 그 뒤에 처음 오는 날짜다
        (D(2027, 1, 10), "dates:12-20..01-05", D(2026, 12, 20), D(2027, 1, 5)),
        (THU, "dates:11-01..11-10", D(2025, 11, 1), D(2025, 11, 10)),
        # 한쪽에만 연도가 있으면 다른 쪽을 그 날짜에 맞춘다
        (D(2027, 1, 10), "dates:2026-12-20..01-05", D(2026, 12, 20), D(2027, 1, 5)),
        (D(2027, 1, 10), "dates:12-20..2027-01-05", D(2026, 12, 20), D(2027, 1, 5)),
    ],
)
def test_dates_without_year_take_latest_past_date(today, period, start, end):
    assert resolve_period(period, today) == DateRange(start, end)


@pytest.mark.parametrize(
    ("today", "period", "reason"),
    [
        # 수의 범위. 최근 N일·N달은 1 이상이다
        (THU, "recent_days:0", "count"),
        (THU, "recent_months:0", "count"),
        # 범위를 넘는 수는 모두 12개월 전보다 앞이라 12개월 한도로 안내한다
        (THU, "days_ago:366", "too_old"),
        (THU, "recent_days:367", "too_old"),
        (THU, "weeks_ago:53", "too_old"),
        (THU, "months_ago:13", "too_old"),
        (THU, "recent_months:13", "too_old"),
        (THU, "days_ago:999", "too_old"),
        # 12개월 한도: 시작일이 12개월 전 같은 날짜(2025-10-22)보다 앞이다
        (THU, "months_ago:12", "too_old"),
        (THU, "weeks_ago:52", "too_old"),
        (THU, "date:2025-10-21", "too_old"),
        (THU, "dates:2025-10-21..2025-10-31", "too_old"),
        # 가장 최근에 지난 2월 29일은 2024년이다
        (THU, "date:02-29", "too_old"),
        # 미래 날짜
        (THU, "date:2026-10-23", "future"),
        (THU, "dates:2026-10-20..2026-10-23", "future"),
        (THU, "dates:10-20..10-25", "future"),
        # 없는 날짜
        (THU, "date:02-30", "no_date"),
        (THU, "date:13-01", "no_date"),
        (THU, "date:2027-02-29", "no_date"),
        (THU, "dates:09-31..10-02", "no_date"),
        # 시작 날짜가 끝 날짜보다 늦다
        (THU, "dates:2026-10-10..2026-10-03", "reversed"),
    ],
)
def test_rejected_periods_raise_period_error(today, period, reason):
    with pytest.raises(PeriodError) as e:
        resolve_period(period, today)
    assert e.value.reason == reason
    assert e.value.message == str(e.value)


def test_rejection_messages_tell_limit_and_dates():
    with pytest.raises(PeriodError) as too_old:
        resolve_period("months_ago:13", THU)
    assert "2025년 10월 22일" in too_old.value.message and "12개월" in too_old.value.message
    assert "확인할 수 없다" in too_old.value.message
    with pytest.raises(PeriodError) as future:
        resolve_period("date:2026-10-23", THU)
    assert "아직 오지 않은" in future.value.message and "10월 22일" in future.value.message
    with pytest.raises(PeriodError) as no_date:
        resolve_period("date:02-30", THU)
    assert "2월 30일" in no_date.value.message
    with pytest.raises(PeriodError) as count:
        resolve_period("recent_days:0", THU)
    assert "recent_days" in count.value.message and "1" in count.value.message


@pytest.mark.parametrize(
    ("period", "allowed"),
    [
        ("recent_days:31", True),
        ("recent_days:32", False),
        # 9월은 30일, 7월은 31일이다
        ("months_ago:1", True),
        ("months_ago:3", True),
        ("recent_months:1", True),
        ("recent_months:2", False),
        ("weeks_ago:4", True),
        ("dates:09-21..10-21", True),
        ("dates:09-20..10-21", False),
    ],
)
def test_list_tools_take_at_most_31_days(period, allowed):
    if allowed:
        assert len(resolve_period(period, THU, max_days=MAX_LIST_DAYS).days()) <= MAX_LIST_DAYS
        return
    with pytest.raises(PeriodError) as e:
        resolve_period(period, THU, max_days=MAX_LIST_DAYS, too_long="날짜별 조회는 31일까지예요")
    assert (e.value.reason, e.value.message) == ("too_long", "날짜별 조회는 31일까지예요")
    # 날짜마다 값을 전달하지 않는 도구는 12개월까지 받는다
    assert len(resolve_period(period, THU).days()) > MAX_LIST_DAYS


def test_twelve_month_limit_starts_a_day_later_when_feb_29_makes_367_days():
    # 백엔드 건강 조회는 양 끝 포함 366일까지 받는다. 윤일이 든 12개월은 367일이라 하루 늦게 시작한다
    today = D(2028, 10, 22)
    assert earliest_start(today) == D(2027, 10, 23)
    assert resolve_period("dates:2027-10-23..2028-10-22", today) == DateRange(D(2027, 10, 23), today)
    with pytest.raises(PeriodError) as e:
        resolve_period("date:2027-10-22", today)
    assert e.value.reason == "too_old"
    assert earliest_start(THU) == D(2025, 10, 22)


def _same_date_year_before(today: date) -> date:
    # 2월 29일의 12개월 전은 2월 28일이다
    try:
        return today.replace(year=today.year - 1)
    except ValueError:
        return today.replace(year=today.year - 1, day=28)


def test_earliest_start_is_twelve_months_back_within_lookup_days():
    # 월말·윤일이 모두 들어가게 윤년을 포함한 2년을 순회한다
    for offset in range(731):
        today = D(2027, 1, 1) + timedelta(days=offset)
        earliest = earliest_start(today)
        same_date = _same_date_year_before(today)
        # 12개월 전 같은 날짜부터 366일이면 그날, 윤일이 들어 367일이면 그다음 날이다
        expected = same_date if (today - same_date).days + 1 <= 366 else same_date + timedelta(days=1)
        assert earliest == expected, today
        assert resolve_period(f"dates:{earliest.isoformat()}..{today.isoformat()}", today).start == earliest
        with pytest.raises(PeriodError):
            resolve_period(f"date:{(earliest - timedelta(days=1)).isoformat()}", today)


@pytest.mark.parametrize("kind", list(COUNT_RANGES))
def test_every_count_in_range_ends_by_today(kind):
    # 수의 범위 안이면 12개월 한도에 걸리더라도 끝 날짜는 오늘을 넘지 않는다
    low, high = COUNT_RANGES[kind]
    for n in range(low, high + 1):
        r = period_range(f"{kind}:{n}", THU)
        assert r.start <= r.end <= THU, (kind, n)


@pytest.mark.parametrize(
    "text",
    [
        "today",
        "week_before_last",
        "days_ago:0",
        "recent_days:366",
        "weeks_ago:3",
        "months_ago:12",
        "recent_months:6",
        "weeks_ago:999",
        "date:10-21",
        "date:9-5",
        "date:2025-10-21",
        "dates:10-03..10-10",
        "dates:2025-12-20..2026-01-05",
    ],
)
def test_check_period_accepts_named_periods_and_expressions(text):
    assert check_period(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "last_90_days",
        "Today",
        "weeks_ago",
        "weeks_ago:",
        "weeks_ago:-1",
        "weeks_ago:1000",
        "weeks_ago:1.5",
        "weeks_ago: 1",
        " weeks_ago:1",
        "WEEKS_AGO:1",
        "weeks_ago:٣",
        "years_ago:1",
        "date:2026/10/21",
        "date:10월21일",
        "date:10-21..10-22",
        "dates:10-03~10-10",
        "dates:10-03..",
        "2026-10-01~2026-10-07",
    ],
)
def test_check_period_rejects_other_text(text):
    with pytest.raises(ValueError):
        check_period(text)


def test_period_range_skips_limits_to_compare_expressions():
    # 도구 선택 채점은 한도를 넘는 기간도 날짜 범위로 비교한다
    assert period_range("months_ago:13", THU) == DateRange(D(2025, 9, 1), D(2025, 9, 30))
    assert period_range("dates:2025-09-01..2025-09-30", THU) == period_range("months_ago:13", THU)
    assert period_range("date:2026-10-25", THU) == DateRange(D(2026, 10, 25), D(2026, 10, 25))
    assert period_range("last_week", THU) == period_range("weeks_ago:1", THU)
    with pytest.raises(PeriodError):
        period_range("date:02-30", THU)
    with pytest.raises(ValueError):
        period_range("last_90_days", THU)


@pytest.mark.parametrize(
    ("today", "period", "label"),
    [
        (D(2026, 10, 31), "weeks_ago:3", "3주 전 (10월 5일-11일)"),
        (THU, "weeks_ago:0", "이번 주 (10월 19일-22일)"),
        (THU, "weeks_ago:1", "지난주 (10월 12일-18일)"),
        (THU, "weeks_ago:3", "3주 전 (9월 28일-10월 4일)"),
        (THU, "days_ago:0", "오늘 (10월 22일)"),
        (THU, "days_ago:1", "어제 (10월 21일)"),
        (THU, "days_ago:5", "5일 전 (10월 17일)"),
        (THU, "recent_days:7", "최근 7일 (10월 16일-22일)"),
        (THU, "months_ago:0", "이번 달 (10월 1일-22일)"),
        (THU, "months_ago:1", "지난달 (9월 1일-30일)"),
        (THU, "months_ago:2", "2달 전 (8월 1일-31일)"),
        (THU, "recent_months:6", "최근 6달 (4월 23일-10월 22일)"),
        (THU, "recent_months:12", "최근 12달 (2025년 10월 23일-2026년 10월 22일)"),
        (THU, "date:10-21", "10월 21일"),
        (THU, "date:10-23", "2025년 10월 23일"),
        (THU, "dates:10-03..10-10", "10월 3일-10일"),
        (THU, "dates:11-01..11-10", "2025년 11월 1일-10일"),
        (D(2027, 1, 10), "dates:12-20..01-05", "2026년 12월 20일-2027년 1월 5일"),
    ],
)
def test_period_label_shows_kind_and_dates(today, period, label):
    assert period_label(period, resolve_period(period, today), today) == label


@pytest.mark.parametrize("period", PERIODS)
def test_named_periods_keep_their_labels(period):
    # 이름 기간은 지금 문구를 그대로 쓴다
    assert period_label(period, resolve_period(period, THU), THU) is None


@pytest.mark.parametrize(
    ("today", "period", "day"),
    [
        # 2026-10-22 목요일. 오늘보다 앞선 가장 최근의 그 요일이다
        (THU, "weekday:wed", D(2026, 10, 21)),
        (THU, "weekday:tue", D(2026, 10, 20)),
        (THU, "weekday:mon", D(2026, 10, 19)),
        (THU, "weekday:sun", D(2026, 10, 18)),
        (THU, "weekday:sat", D(2026, 10, 17)),
        (THU, "weekday:fri", D(2026, 10, 16)),
        # 오늘과 같은 요일이면 오늘이 아니라 7일 전이다
        (THU, "weekday:thu", D(2026, 10, 15)),
        # 월요일의 일요일은 어제, 월요일은 7일 전이다
        (D(2026, 10, 19), "weekday:sun", D(2026, 10, 18)),
        (D(2026, 10, 19), "weekday:mon", D(2026, 10, 12)),
        # 연도 경계 (2027-01-01 금요일)
        (D(2027, 1, 1), "weekday:thu", D(2026, 12, 31)),
        (D(2027, 1, 1), "weekday:sat", D(2026, 12, 26)),
        (D(2027, 1, 1), "weekday:fri", D(2026, 12, 25)),
        # 윤일 다음 날 (2028-03-01 수요일)
        (D(2028, 3, 1), "weekday:tue", D(2028, 2, 29)),
    ],
)
def test_weekday_is_latest_such_day_before_today(today, period, day):
    assert resolve_period(period, today) == DateRange(day, day)


def test_weekday_is_one_of_the_seven_days_before_today():
    # 월말·윤일·연도 경계가 모두 들어가게 윤년을 포함한 2년을 순회한다
    names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    for offset in range(731):
        today = D(2027, 1, 1) + timedelta(days=offset)
        days = [resolve_period(f"weekday:{name}", today).start for name in names]
        assert [d.weekday() for d in days] == list(range(7)), today
        assert sorted(days) == [today - timedelta(days=n) for n in range(7, 0, -1)], today
        # 날짜마다 값을 전달하는 도구도 받는다
        assert all(len(resolve_period(f"weekday:{n}", today, max_days=MAX_LIST_DAYS).days()) == 1 for n in names)


@pytest.mark.parametrize(
    ("today", "period", "label"),
    [
        (THU, "weekday:sun", "지난 일요일 (10월 18일)"),
        (THU, "weekday:thu", "지난 목요일 (10월 15일)"),
        (D(2027, 1, 1), "weekday:sat", "지난 토요일 (2026년 12월 26일)"),
    ],
)
def test_weekday_label_shows_day_name_and_date(today, period, label):
    assert period_label(period, resolve_period(period, today), today) == label


@pytest.mark.parametrize("text", ["weekday:mon", "weekday:sun"])
def test_check_period_accepts_weekday(text):
    assert check_period(text) == text


@pytest.mark.parametrize(
    "text", ["weekday:", "weekday:sunday", "weekday:Sun", "weekday:7", "weekday:일", "weekdays:sun", "weekday: sun"]
)
def test_check_period_rejects_other_weekday_text(text):
    with pytest.raises(ValueError):
        check_period(text)


def test_weekday_range_compares_with_same_day_in_other_words():
    # 도구 선택 채점은 날짜 범위로 비교한다. 지난 일요일(10-18)은 date:10-18, days_ago:4 와 같다
    assert period_range("weekday:sun", THU) == period_range("date:10-18", THU) == period_range("days_ago:4", THU)


def test_previous_period_of_weekday_is_same_weekday_a_week_before():
    assert previous_period("weekday:sun", THU) == "date:10-11"
    assert previous_period("weekday:thu", THU) == "date:10-08"


# 2026-11-03 화요일. 아래 표현 예 표는 모두 이 기준일이다
TUE = D(2026, 11, 3)


@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        ("weeks_ago:2.thu", D(2026, 10, 22), D(2026, 10, 22)),
        ("weeks_ago:1.wed", D(2026, 10, 28), D(2026, 10, 28)),
        ("weeks_ago:0.mon", D(2026, 11, 2), D(2026, 11, 2)),
        ("weeks_ago:0.tue", D(2026, 11, 3), D(2026, 11, 3)),
        ("weeks_ago:2.thu..days_ago:1", D(2026, 10, 22), D(2026, 11, 2)),
        ("weeks_ago:1.wed..days_ago:1", D(2026, 10, 28), D(2026, 11, 2)),
        ("weeks_ago:1..days_ago:1", D(2026, 10, 26), D(2026, 11, 2)),
        ("months_ago:1..weeks_ago:1", D(2026, 10, 1), D(2026, 11, 1)),
        ("date:10-15..days_ago:1", D(2026, 10, 15), D(2026, 11, 2)),
        ("days_ago:3..days_ago:1", D(2026, 10, 31), D(2026, 11, 2)),
        ("weekday:thu..days_ago:0", D(2026, 10, 29), D(2026, 11, 3)),
        ("weeks_ago:2.mon..weeks_ago:1.wed", D(2026, 10, 19), D(2026, 10, 28)),
        ("date:10-01..date:10-31", D(2026, 10, 1), D(2026, 10, 31)),
        # 끝점이 이번 주·이번 달이면 마지막 날은 오늘이다
        ("weeks_ago:1..weeks_ago:0", D(2026, 10, 26), TUE),
        ("months_ago:1..months_ago:0", D(2026, 10, 1), TUE),
        # 끝만 연도가 없는 날짜면 시작 이후 처음 오는 그 날짜다
        ("weeks_ago:3..date:10-20", D(2026, 10, 12), D(2026, 10, 20)),
        # 시작에 연도가 있는 날짜
        ("date:2026-10-15..date:10-20", D(2026, 10, 15), D(2026, 10, 20)),
    ],
)
def test_relative_expressions_on_tuesday_11_03(period, start, end):
    """오늘 11-03(화)에 표현 예 → 표의 날짜 범위."""
    assert resolve_period(period, TUE) == DateRange(start, end)


def test_date_endpoints_equal_dates_expression():
    """date:A..date:B 는 dates:A..B 와 같은 날짜이고, 연도 없는 끝점은 dates: 규칙을 따른다."""
    for today in (TUE, D(2027, 1, 10), THU):
        assert period_range("date:10-01..date:10-31", today) == period_range("dates:10-01..10-31", today)
    assert period_range("date:12-20..date:01-05", D(2027, 1, 10)) == period_range("dates:12-20..01-05", D(2027, 1, 10))
    assert period_range("date:2026-12-20..date:01-05", D(2027, 1, 10)) == DateRange(D(2026, 12, 20), D(2027, 1, 5))


def test_range_start_date_without_year_is_latest_before_the_end():
    """시작이 연도 없는 날짜면 끝 이전(같은 날 포함)에 마지막으로 온 그 날짜다."""
    assert period_range("date:12-25..days_ago:1", D(2027, 1, 5)) == DateRange(D(2026, 12, 25), D(2027, 1, 4))
    # 끝(2026-10-11)과 같은 날 포함: 10-11 은 끝 당일이다
    assert period_range("date:10-11..weeks_ago:3", D(2026, 10, 26)) == DateRange(D(2026, 10, 11), D(2026, 10, 11))


@pytest.mark.parametrize(
    ("today", "period", "start", "end"),
    [
        # 2026-11-08 일요일
        (D(2026, 11, 8), "weeks_ago:1.wed..days_ago:1", D(2026, 10, 28), D(2026, 11, 7)),
        (D(2026, 11, 8), "weeks_ago:2.thu", D(2026, 10, 22), D(2026, 10, 22)),
        (D(2026, 11, 8), "weeks_ago:0.sun", D(2026, 11, 8), D(2026, 11, 8)),
        # 2026-11-02 월요일
        (D(2026, 11, 2), "weeks_ago:1.wed..days_ago:1", D(2026, 10, 28), D(2026, 11, 1)),
        (D(2026, 11, 2), "weeks_ago:2.thu", D(2026, 10, 22), D(2026, 10, 22)),
        (D(2026, 11, 2), "weeks_ago:0.mon", D(2026, 11, 2), D(2026, 11, 2)),
        # 2026-11-04 수요일
        (D(2026, 11, 4), "weeks_ago:1.wed..days_ago:1", D(2026, 10, 28), D(2026, 11, 3)),
        (D(2026, 11, 4), "weeks_ago:2.thu", D(2026, 10, 22), D(2026, 10, 22)),
        (D(2026, 11, 4), "weeks_ago:0.wed", D(2026, 11, 4), D(2026, 11, 4)),
    ],
)
def test_same_expression_follows_each_base_date(today, period, start, end):
    """같은 표현을 일·월·수요일 기준일에 계산 → 표현은 같고 날짜만 그날 기준."""
    assert resolve_period(period, today) == DateRange(start, end)


@pytest.mark.parametrize(
    ("now", "day"),
    [
        (datetime(2026, 11, 1, 14, 59, tzinfo=UTC), D(2026, 10, 21)),  # KST 11-01(일) 23:59, 지난주는 10-19 주
        (datetime(2026, 11, 1, 15, 0, tzinfo=UTC), D(2026, 10, 28)),  # KST 11-02(월) 00:00, 지난주는 10-26 주
    ],
)
def test_last_week_wednesday_turns_over_at_kst_monday_midnight(now, day):
    """KST 월요일 0시 직전(일 23:59)의 weeks_ago:1.wed 는 10-21, 직후(월 00:00)는 10-28."""
    assert resolve_period("weeks_ago:1.wed", local_today(now)) == DateRange(day, day)


def test_week_day_is_always_that_weekday_of_that_week():
    """윤년 포함 2년을 순회해 weeks_ago:N.요일 이 N주 전 월-일 주의 그 요일 하루인지 확인한다."""
    for offset in range(731):
        today = D(2027, 1, 1) + timedelta(days=offset)
        for n in (0, 1, 2, 52):
            week = period_range(f"weeks_ago:{n}", today)
            for index, code in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun")):
                day = period_range(f"weeks_ago:{n}.{code}", today)
                assert day.start == day.end == week.start + timedelta(days=index), (today, n, code)


def test_p1_reversed_range_is_rejected_with_message():
    """시작이 끝보다 늦으면 reversed 와 시작..끝 순서 안내."""
    with pytest.raises(PeriodError) as e:
        resolve_period("weeks_ago:1.fri..weeks_ago:1.mon", TUE)
    assert e.value.reason == "reversed"
    assert e.value.message == "시작 날짜가 끝 날짜보다 늦어요. 시작..끝 순서로 써 주세요"
    with pytest.raises(PeriodError) as other:
        resolve_period("days_ago:1..days_ago:3", TUE)
    assert other.value.reason == "reversed"
    # 기존 dates: 의 안내는 그대로다
    with pytest.raises(PeriodError) as old:
        resolve_period("dates:2026-10-10..2026-10-03", THU)
    assert old.value.message == "시작 날짜가 끝 날짜보다 늦어요. dates:시작..끝 순서로 써 주세요"


@pytest.mark.parametrize("period", ["weeks_ago:0.sun", "days_ago:1..weeks_ago:0.sun", "weeks_ago:1..weeks_ago:0.wed"])
def test_p2_future_end_is_rejected(period):
    """오늘 11-03(화)에 끝이 미래인 하루·범위 → future 와 지금 문구."""
    with pytest.raises(PeriodError) as e:
        resolve_period(period, TUE)
    assert e.value.reason == "future"
    assert e.value.message == "아직 오지 않은 날짜가 들어 있어요. 오늘(11월 3일)까지만 조회할 수 있어요"


@pytest.mark.parametrize(
    "period",
    [
        "weeks_ago:60.mon",  # 수가 상한(52) 초과
        "weeks_ago:53.mon..days_ago:1",
        "days_ago:400..days_ago:1",
        "weeks_ago:1..days_ago:999",  # 끝 끝점의 수가 상한 초과
        "months_ago:13..weeks_ago:1",
    ],
)
def test_p3_beyond_twelve_months_is_too_old(period):
    """12개월 밖이거나 표현 안의 수가 상한을 넘는 하루·범위 → too_old."""
    with pytest.raises(PeriodError) as e:
        resolve_period(period, TUE)
    assert e.value.reason == "too_old"
    assert "12개월" in e.value.message and "확인할 수 없다" in e.value.message


def test_week_day_before_twelve_month_limit_is_too_old():
    """수는 상한(52) 안이어도 시작일이 12개월 한도 앞이면 → 수요일 11-04 기준 weeks_ago:52.mon(2025-11-03)은 too_old."""
    today = D(2026, 11, 4)
    assert earliest_start(today) == D(2025, 11, 4)
    with pytest.raises(PeriodError) as e:
        resolve_period("weeks_ago:52.mon", today)
    assert e.value.reason == "too_old"
    assert resolve_period("weeks_ago:52.tue", today) == DateRange(D(2025, 11, 4), D(2025, 11, 4))


@pytest.mark.parametrize(
    "text",
    [
        "weeks_ago:2.thursday",
        "recent_days:3..days_ago:1",
        "days_ago:1..recent_days:3",
        "recent_months:2..days_ago:1",
        "dates:10-01..10-05..days_ago:1",
        "days_ago:1..days_ago:2..days_ago:3",
        "last_week..days_ago:1",
        "days_ago:1..last_week",
        "weeks_ago:1.",
        "..days_ago:1",
        "days_ago:1..",
        "days_ago:3...days_ago:1",
        "weeks_ago:1..dates:10-01..10-05",
        "weeks_ago:2.Thu",
        "weeks_ago:2.thu.fri",
        "weeks_ago:2.thu .. days_ago:1",
        "weeks_ago:1000.thu",
        "weeks_ago:-1.thu",
    ],
)
def test_p5_malformed_expressions_are_rejected_by_check_period(text):
    """형식이 아닌 하루·범위 → check_period 가 ValueError (게이트웨이에서는 invalid_args)."""
    with pytest.raises(ValueError):
        check_period(text)
    with pytest.raises(ValueError):
        period_range(text, TUE)


@pytest.mark.parametrize(
    "text",
    [
        "weeks_ago:2.thu",
        "weeks_ago:0.sun",
        "weeks_ago:999.mon",
        "weeks_ago:2.thu..days_ago:1",
        "weeks_ago:1..days_ago:1",
        "months_ago:1..weeks_ago:1",
        "date:10-15..days_ago:1",
        "date:2026-10-15..date:10-20",
        "weekday:thu..days_ago:0",
        "days_ago:999..days_ago:1",
        "date:02-30..days_ago:1",
    ],
)
def test_check_period_accepts_week_day_and_range_forms(text):
    """새 하루·범위 형식 → check_period 가 그대로 반환(수 상한과 날짜 유효성은 resolve_period 몫)."""
    assert check_period(text) == text


def test_p6_nonexistent_date_endpoint_is_no_date():
    """없는 날짜 끝점(date:02-30..days_ago:1) → no_date 와 날짜 안내."""
    for period in ("date:02-30..days_ago:1", "weeks_ago:2..date:02-30", "date:2026-02-29..days_ago:1"):
        with pytest.raises(PeriodError) as e:
            resolve_period(period, TUE)
        assert e.value.reason == "no_date", period
    with pytest.raises(PeriodError) as e:
        resolve_period("date:02-30..days_ago:1", TUE)
    assert "2월 30일" in e.value.message


def test_range_respects_max_days_with_too_long():
    """max_days 를 지정하면 31일 넘는 범위는 too_long 과 too_long 인자의 문구, 이하는 통과."""
    with pytest.raises(PeriodError) as e:
        resolve_period("months_ago:2..days_ago:1", TUE, max_days=MAX_LIST_DAYS, too_long="날짜별 조회는 31일까지예요")
    assert (e.value.reason, e.value.message) == ("too_long", "날짜별 조회는 31일까지예요")
    assert len(resolve_period("weeks_ago:2.thu..days_ago:1", TUE, max_days=MAX_LIST_DAYS).days()) == 12
    assert len(resolve_period("weeks_ago:1.wed", TUE, max_days=1).days()) == 1


def test_dates_keeps_its_own_rule_apart_from_range_grammar():
    """점 두 개가 든 dates: 는 범위 문법과 섞이지 않고 지금 규칙으로만 처리한다."""
    assert resolve_period("dates:10-03..10-10", THU) == DateRange(D(2026, 10, 3), D(2026, 10, 10))
    with pytest.raises(ValueError):
        check_period("dates:10-03..")
    with pytest.raises(ValueError):
        check_period("date:10-21..10-22")


@pytest.mark.parametrize(
    ("period", "label"),
    [
        ("weeks_ago:1.wed", "지난주 수요일 (10월 28일)"),
        ("weeks_ago:2.thu", "2주 전 목요일 (10월 22일)"),
        ("weeks_ago:0.mon", "이번 주 월요일 (11월 2일)"),
        ("weeks_ago:2.thu..days_ago:1", "2주 전 목요일부터 어제까지 (10월 22일-11월 2일)"),
        ("weeks_ago:1..days_ago:1", "지난주부터 어제까지 (10월 26일-11월 2일)"),
        ("months_ago:1..weeks_ago:1", "지난달부터 지난주까지 (10월 1일-11월 1일)"),
        ("months_ago:2..months_ago:0", "2달 전부터 이번 달까지 (9월 1일-11월 3일)"),
        ("days_ago:3..days_ago:0", "3일 전부터 오늘까지 (10월 31일-11월 3일)"),
        ("weekday:thu..days_ago:0", "지난 목요일부터 오늘까지 (10월 29일-11월 3일)"),
        ("date:10-15..days_ago:1", "10월 15일부터 어제까지 (10월 15일-11월 2일)"),
        ("weeks_ago:2.mon..weeks_ago:1.wed", "2주 전 월요일부터 지난주 수요일까지 (10월 19일-28일)"),
        ("date:2025-12-20..days_ago:1", "2025년 12월 20일부터 어제까지 (2025년 12월 20일-2026년 11월 2일)"),
    ],
)
def test_relative_expression_labels(period, label):
    """오늘 11-03(화)에 새 하루·범위 → 시작 이름부터 끝 이름까지 (날짜) 문구."""
    assert period_label(period, resolve_period(period, TUE), TUE) == label


@pytest.mark.parametrize(
    ("period", "previous"),
    [
        ("weeks_ago:1.wed", "weeks_ago:2.wed"),
        ("weeks_ago:0.mon", "weeks_ago:1.mon"),
        ("weeks_ago:2.thu..days_ago:1", "dates:10-10..10-21"),
        ("weeks_ago:1..days_ago:1", "dates:10-18..10-25"),
        ("weeks_ago:11.mon..weeks_ago:10.mon", "dates:08-09..08-16"),
    ],
)
def test_previous_period_of_week_day_and_range(period, previous):
    """새 하루는 한 주 앞 같은 요일로, 범위는 같은 길이의 바로 앞 날짜로 → 후보 표현."""
    assert previous_period(period, TUE) == previous
    assert resolve_period(previous, TUE).end < resolve_period(period, TUE).start


@pytest.mark.parametrize("period", ["weeks_ago:52.mon", "weeks_ago:51.mon..weeks_ago:50.sun", "weeks_ago:60.mon"])
def test_previous_period_that_cannot_be_looked_up_is_none(period):
    """바로 앞 기간이 12개월 밖이면(또는 표현 자체가 조회 불가면) None."""
    assert previous_period(period, TUE) is None


def test_period_range_compares_new_expressions_with_old_words():
    """도구 선택 채점: 새 표현은 같은 날짜의 옛 표현과 날짜 범위가 같다."""
    assert period_range("weeks_ago:1.wed", TUE) == period_range("date:10-28", TUE)
    assert period_range("weeks_ago:1..weeks_ago:1", TUE) == period_range("last_week", TUE)
    assert period_range("weeks_ago:2.thu..days_ago:1", TUE) == period_range("dates:10-22..11-02", TUE)
    # 미래 날짜도 period_range 는 계산한다. 한도는 resolve_period 가 검사한다
    assert period_range("weeks_ago:0.wed", TUE) == DateRange(D(2026, 11, 4), D(2026, 11, 4))


@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        # 시작의 연도는 오늘이 아니라 끝(2026-11-01) 이전에 마지막으로 온 11-02 로 정한다
        ("date:11-02..weeks_ago:1", D(2025, 11, 2), D(2026, 11, 1)),
        # 끝의 연도는 오늘이 아니라 시작(2025-11-01) 이후 처음 오는 11-10 으로 정한다
        ("months_ago:12..date:11-10", D(2025, 11, 1), D(2025, 11, 10)),
    ],
)
def test_year_less_date_endpoint_follows_the_other_endpoint_not_today(period, start, end):
    """오늘 11-03(화)에 연도 없는 date: 끝점 → 오늘이 아니라 다른 끝점 기준으로 연도를 정한다."""
    assert period_range(period, TUE) == DateRange(start, end)


@pytest.mark.parametrize(
    ("days", "sizes"),
    [(1, [1]), (31, [31]), (32, [1, 31]), (42, [11, 31]), (62, [31, 31]), (63, [1, 31, 31])],
)
def test_split_range_cuts_from_the_end_into_parts_up_to_max_days(days, sizes):
    r = DateRange(THU - timedelta(days=days - 1), THU)
    parts = split_range(r, MAX_LIST_DAYS)
    assert [len(p.days()) for p in parts] == sizes
    # 조각을 이으면 빈 날 없이 원래 구간이다
    assert [d for p in parts for d in p.days()] == r.days()


def test_split_parts_are_written_as_expressions_resolving_to_the_same_days():
    """연도가 바뀌는 구간도 각 조각의 표현이 같은 날짜로 해석된다."""
    today = D(2026, 1, 20)
    r = DateRange(D(2025, 12, 1), today)
    expressions = [range_expression(p, today) for p in split_range(r, MAX_LIST_DAYS)]
    assert expressions == ["dates:12-01..12-20", "dates:12-21..01-20"]
    assert [resolve_period(e, today) for e in expressions] == split_range(r, MAX_LIST_DAYS)
