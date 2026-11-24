from __future__ import annotations

import calendar
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, get_args
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Seoul"

# 이름 기간. 새 기간 표현 전에 쓰던 값이고, 데이터셋·골든셋·이전 대화와 호환하도록 계속 받는다
Period = Literal[
    "today",
    "yesterday",
    "last_7_days",
    "last_14_days",
    "last_30_days",
    "this_week",
    "last_week",
    "week_before_last",
    "this_month",
    "last_month",
]
PERIODS: tuple[Period, ...] = get_args(Period)

# 수를 받는 기간 종류와 받는 수의 범위
CountKind = Literal["days_ago", "recent_days", "weeks_ago", "months_ago", "recent_months"]
COUNT_RANGES: dict[CountKind, tuple[int, int]] = {
    "days_ago": (0, 365),
    "recent_days": (1, 366),
    "weeks_ago": (0, 52),
    "months_ago": (0, 12),
    "recent_months": (1, 12),
}
# 이름 기간과 날짜 범위가 같은 새 표현. 이름 기간은 이 표현으로 바꿔 계산한다
NAMED_EXPRESSIONS: dict[Period, str] = {
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
# 날짜마다 값을 전달하는 도구(날짜별 조회, 밤별 수면 기록, 운동 기록)가 받는 최대 일수
MAX_LIST_DAYS = 31
# 백엔드 건강 조회가 받는 최대 일수(양 끝 포함). 윤일이 든 12개월은 367일이라 시작일을 하루 늦춘다
MAX_LOOKUP_DAYS = 366

# count: 수가 범위보다 작음, no_date: 없는 날짜, reversed: 시작 날짜가 끝 날짜보다 늦음,
# future: 아직 오지 않은 날짜, too_old: 12개월 전보다 앞, too_long: 도구가 받는 일수를 넘음
Rejection = Literal["count", "no_date", "reversed", "future", "too_old", "too_long"]

# date.weekday() 순서의 요일 이름
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WEEKDAY_LABELS = "월화수목금토일"

_COUNT = re.compile(r"(days_ago|recent_days|weeks_ago|months_ago|recent_months):([0-9]{1,3})")
_DAY = r"(?:([0-9]{4})-)?([0-9]{1,2})-([0-9]{1,2})"
_ONE_DAY = re.compile(rf"date:{_DAY}")
_DAY_RANGE = re.compile(rf"dates:{_DAY}\.\.{_DAY}")
_WEEKDAY_CODE = "|".join(WEEKDAYS)
_WEEKDAY = re.compile(rf"weekday:({_WEEKDAY_CODE})")
# 주와 요일을 함께 말한 하루 (weeks_ago:2.thu)
_WEEK_DAY = re.compile(rf"weeks_ago:([0-9]{{1,3}})\.({_WEEKDAY_CODE})")
# 범위의 끝점으로 받는 수 표현. recent_days·recent_months 는 끝점이 아니다
_END_COUNT = re.compile(r"(days_ago|weeks_ago|months_ago):([0-9]{1,3})")
# 표현 안의 모든 수. 수마다 COUNT_RANGES 의 위쪽 상한을 검사한다
_ANY_COUNT = re.compile(r"(days_ago|recent_days|weeks_ago|months_ago|recent_months):([0-9]{1,3})")
# 2월 29일은 4년(세기 규칙이 걸리면 8년)마다 온다
_LEAP_SEARCH_YEARS = 9


@dataclass(frozen=True, slots=True)
class DateRange:
    """양 끝을 포함하는 날짜 구간."""

    start: date
    end: date

    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]


class PeriodError(ValueError):
    """조회할 수 없는 기간. message 는 모델에 그대로 전달하는 짧은 안내다."""

    def __init__(self, reason: Rejection, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class _Day:
    """기간 표현에 적힌 날짜. 연도가 없으면 year 가 None 이다."""

    year: int | None
    month: int
    day: int


def local_today(now: datetime, tz: str = DEFAULT_TZ) -> date:
    """시각을 받아 해당 시간대의 날짜를 반환한다. now는 시간대 정보가 있어야 한다."""
    if now.tzinfo is None:
        raise ValueError("now에 시간대 정보가 없다")
    return now.astimezone(ZoneInfo(tz)).date()


def check_period(text: str) -> str:
    """기간 표현의 형식만 확인한다. 이름 기간이나 새 표현이면 그대로 반환하고, 아니면 ValueError 를 발생시킨다.

    수의 범위와 날짜 한도는 오늘 날짜가 있어야 알 수 있어 resolve_period 가 검사한다.
    """
    if text in PERIODS or any(p.fullmatch(text) for p in (_COUNT, _ONE_DAY, _DAY_RANGE, _WEEKDAY, _WEEK_DAY)):
        return text
    if (parts := _range_parts(text)) is not None and all(_is_endpoint(part) for part in parts):
        return text
    raise ValueError("기간 표현 형식이 아니다")


def _range_parts(text: str) -> list[str] | None:
    """끝점..끝점 형식의 범위 표현이면 점 두 개로 나눈 조각을, 아니면 None 을 반환한다. dates: 는 범위가 아니다."""
    if text.startswith("dates:") or ".." not in text:
        return None
    parts = text.split("..")
    return parts if len(parts) == 2 else None


def _is_endpoint(text: str) -> bool:
    return any(p.fullmatch(text) for p in (_END_COUNT, _WEEK_DAY, _ONE_DAY, _WEEKDAY))


def earliest_start(today: date) -> date:
    """조회할 수 있는 가장 이른 날짜. 12개월 전 같은 날짜이고, 그날부터 오늘까지 366일을 넘으면 그다음 날이다."""
    return max(_shift_months(today, -12), today - timedelta(days=MAX_LOOKUP_DAYS - 1))


def period_range(period: str, today: date) -> DateRange:
    """기간 표현을 today 기준 날짜 구간으로 바꾼다. 수의 범위 위쪽과 조회 한도는 검사하지 않는다.

    도구 선택 채점이 두 표현의 날짜 범위가 같은지 비교할 때도 쓴다. 형식이 아니면 ValueError, 구간을 만들 수 없으면
    (없는 날짜, 뒤바뀐 날짜, 범위보다 작은 수) PeriodError 를 발생시킨다.
    """
    expression = NAMED_EXPRESSIONS.get(period, period)
    if m := _COUNT.fullmatch(expression):
        return _count_range(m.group(1), int(m.group(2)), today)
    if m := _ONE_DAY.fullmatch(expression):
        day = _resolve_alone(_day(m, 0), today)
        return DateRange(day, day)
    if m := _DAY_RANGE.fullmatch(expression):
        return _day_range(_day(m, 0), _day(m, 3), today)
    if m := _WEEKDAY.fullmatch(expression):
        day = _last_weekday(m.group(1), today)
        return DateRange(day, day)
    if m := _WEEK_DAY.fullmatch(expression):
        return _week_day_range(int(m.group(1)), m.group(2), today)
    if (parts := _range_parts(expression)) is not None and all(_is_endpoint(part) for part in parts):
        return _span_range(parts[0], parts[1], today)
    raise ValueError("기간 표현 형식이 아니다")


def _last_weekday(code: str, today: date) -> date:
    """오늘보다 앞선 가장 최근의 그 요일 날짜를 반환한다. 오늘과 같은 요일이면 7일 전이다."""
    return today - timedelta(days=(today.weekday() - WEEKDAYS.index(code)) % 7 or 7)


def _week_day_range(n: int, code: str, today: date) -> DateRange:
    """weeks_ago:N 주(월-일)의 그 요일 하루를 반환한다. 이번 주에서 오늘 뒤의 요일이면 미래 날짜가 나온다."""
    week_start = today - timedelta(days=today.weekday() + 7 * n)
    day = week_start + timedelta(days=WEEKDAYS.index(code))
    return DateRange(day, day)


def _endpoint_range(text: str, today: date) -> DateRange:
    """날짜가 아닌 범위 끝점 하나를 혼자 쓴 기간으로 계산한다. 끝점 형식이 아니면 ValueError 를 발생시킨다."""
    if m := _END_COUNT.fullmatch(text):
        return _count_range(m.group(1), int(m.group(2)), today)
    if m := _WEEK_DAY.fullmatch(text):
        return _week_day_range(int(m.group(1)), m.group(2), today)
    if m := _WEEKDAY.fullmatch(text):
        day = _last_weekday(m.group(1), today)
        return DateRange(day, day)
    raise ValueError("기간 표현 형식이 아니다")


def _span_range(first: str, last: str, today: date) -> DateRange:
    """시작 끝점 구간의 첫날부터 끝 끝점 구간의 마지막 날까지를 계산한다.

    date: 끝점의 연도가 없으면 dates: 와 같은 규칙으로 정한다. 시작이 끝보다 늦으면 PeriodError(reversed)를 발생시킨다.
    """
    first_date, last_date = _ONE_DAY.fullmatch(first), _ONE_DAY.fullmatch(last)
    if first_date and last_date:
        start, end = _day_bounds(_day(first_date, 0), _day(last_date, 0), today)
    elif first_date:
        end = _endpoint_range(last, today).end
        day = _day(first_date, 0)
        start = _exact(day) if day.year is not None else _latest(day, end)
    elif last_date:
        start = _endpoint_range(first, today).start
        day = _day(last_date, 0)
        end = _exact(day) if day.year is not None else _first(day, start)
    else:
        start, end = _endpoint_range(first, today).start, _endpoint_range(last, today).end
    if start > end:
        raise PeriodError("reversed", "시작 날짜가 끝 날짜보다 늦어요. 시작..끝 순서로 써 주세요")
    return DateRange(start, end)


def resolve_period(period: str, today: date, *, max_days: int | None = None, too_long: str | None = None) -> DateRange:
    """기간 표현을 today 기준 날짜 구간으로 바꾸고 조회 한도를 검사한다. today는 이미 사용자 시간대의 날짜다.

    진행 중인 주·달은 오늘에서 끝난다 (미래 날짜를 결측으로 세지 않음).
    조회할 수 없는 기간이면 PeriodError 를 발생시킨다. max_days 를 넘으면 too_long 문구를 안내로 쓴다.
    """
    for m in _ANY_COUNT.finditer(period):
        # 범위보다 큰 수는 모두 12개월 전보다 앞이라 12개월 한도로 안내한다. 표현 안의 수마다 검사한다
        if int(m.group(2)) > COUNT_RANGES[m.group(1)][1]:
            raise _too_old(today)
    r = period_range(period, today)
    if r.end > today:
        raise PeriodError(
            "future", f"아직 오지 않은 날짜가 들어 있어요. 오늘({_day_text(today, today)})까지만 조회할 수 있어요"
        )
    if r.start < earliest_start(today):
        raise _too_old(today)
    if max_days is not None and (r.end - r.start).days + 1 > max_days:
        raise PeriodError("too_long", too_long or f"한 번에 {max_days}일까지 조회할 수 있어요")
    return r


def period_label(period: str, r: DateRange, today: date) -> str | None:
    """새 기간 표현의 표시 문구. "3주 전 (10월 5일-11일)"처럼 종류와 실제 날짜를 함께 쓴다.

    날짜 지정은 날짜만 쓴다. 이름 기간은 None 이다 (지금 문구를 그대로 쓴다).
    """
    if period in PERIODS:
        return None
    dates = range_text(r, today)
    if m := _WEEKDAY.fullmatch(period):
        return f"지난 {_WEEKDAY_LABELS[WEEKDAYS.index(m.group(1))]}요일 ({dates})"
    if _WEEK_DAY.fullmatch(period):
        return f"{_endpoint_name(period, r.start, today)} ({dates})"
    if (parts := _range_parts(period)) is not None and all(_is_endpoint(part) for part in parts):
        first, last = _endpoint_name(parts[0], r.start, today), _endpoint_name(parts[1], r.end, today)
        return f"{first}부터 {last}까지 ({dates})"
    m = _COUNT.fullmatch(period)
    return dates if m is None else f"{_count_label(m.group(1), int(m.group(2)))} ({dates})"


def _endpoint_name(text: str, day: date, today: date) -> str:
    """범위 끝점(또는 새 하루)의 이름을 쓴다. day 는 그 끝점이 가리키는 실제 날짜이고 date: 끝점에 쓴다."""
    if m := _END_COUNT.fullmatch(text):
        return _count_label(m.group(1), int(m.group(2)))
    if m := _WEEK_DAY.fullmatch(text):
        return f"{_count_label('weeks_ago', int(m.group(1)))} {_WEEKDAY_LABELS[WEEKDAYS.index(m.group(2))]}요일"
    if m := _WEEKDAY.fullmatch(text):
        return f"지난 {_WEEKDAY_LABELS[WEEKDAYS.index(m.group(1))]}요일"
    return _day_text(day, today)


def previous_period(period: str, today: date) -> str | None:
    """같은 종류로 바로 앞 기간의 표현. 조회할 수 없는 기간이면 None 이다. 기간 비교의 기준 기간 후보로 쓴다.

    N일·N주·N달 전은 수를 하나 늘리고, 요일 하루는 한 주 전 같은 요일로, 최근 N일·N달과 날짜 지정은 같은 길이의
    바로 앞 날짜로 쓴다.
    """
    expression = NAMED_EXPRESSIONS.get(period, period)
    r = period_range(expression, today)
    m = _COUNT.fullmatch(expression)
    if m is not None and m.group(1) in ("days_ago", "weeks_ago", "months_ago"):
        candidate = f"{m.group(1)}:{int(m.group(2)) + 1}"
    elif w := _WEEK_DAY.fullmatch(expression):
        candidate = f"weeks_ago:{int(w.group(1)) + 1}.{w.group(2)}"
    elif _WEEKDAY.fullmatch(expression):
        week_before = r.start - timedelta(days=7)
        candidate = range_expression(DateRange(week_before, week_before), today)
    else:
        if m is not None and m.group(1) == "recent_months":
            start = _shift_months(today, -2 * int(m.group(2))) + timedelta(days=1)
        else:
            start = r.start - (r.end - r.start) - timedelta(days=1)
        candidate = range_expression(DateRange(start, r.start - timedelta(days=1)), today)
    try:
        resolve_period(candidate, today)
    except PeriodError:
        return None
    return candidate


def split_range(r: DateRange, max_days: int) -> list[DateRange]:
    """구간을 max_days 일 이하 조각으로 나눈다. 끝 날짜부터 max_days 일씩 자르고, 이른 조각부터 반환한다."""
    parts: list[DateRange] = []
    end = r.end
    while end >= r.start:
        start = max(r.start, end - timedelta(days=max_days - 1))
        parts.append(DateRange(start, end))
        end = start - timedelta(days=1)
    return parts[::-1]


def range_expression(r: DateRange, today: date) -> str:
    """날짜 구간을 date:·dates: 표현으로 쓴다. 연도가 없어도 같은 날짜로 해석되면 연도를 붙이지 않는다."""
    for text in (lambda d: f"{d.month:02d}-{d.day:02d}", date.isoformat):
        expression = f"date:{text(r.start)}" if r.start == r.end else f"dates:{text(r.start)}..{text(r.end)}"
        with suppress(PeriodError):
            if period_range(expression, today) == r:
                return expression
    raise ValueError("날짜 구간을 표현으로 쓸 수 없다")


def range_text(r: DateRange, today: date) -> str:
    """날짜 구간을 "10월 5일-11일" 형태로 쓴다. 오늘과 연도가 다르면 연도를 붙인다."""
    if r.start == r.end:
        return _day_text(r.start, today)
    if r.start.year != r.end.year:
        return f"{_full_text(r.start)}-{_full_text(r.end)}"
    tail = f"{r.end.day}일" if r.start.month == r.end.month else f"{r.end.month}월 {r.end.day}일"
    return f"{_day_text(r.start, today)}-{tail}"


def _count_label(kind: str, n: int) -> str:
    match kind:
        case "days_ago":
            return {0: "오늘", 1: "어제"}.get(n, f"{n}일 전")
        case "recent_days":
            return f"최근 {n}일"
        case "weeks_ago":
            return {0: "이번 주", 1: "지난주"}.get(n, f"{n}주 전")
        case "months_ago":
            return {0: "이번 달", 1: "지난달"}.get(n, f"{n}달 전")
    return f"최근 {n}달"


def _full_text(day: date) -> str:
    return f"{day.year}년 {day.month}월 {day.day}일"


def _day_text(day: date, today: date) -> str:
    return f"{day.month}월 {day.day}일" if day.year == today.year else _full_text(day)


def _too_old(today: date) -> PeriodError:
    return PeriodError(
        "too_old",
        f"{_full_text(earliest_start(today))}(12개월 전)부터 오늘까지만 조회할 수 있어요. "
        "그 전 기간은 확인할 수 없다고 말하고 이 안의 기간을 제안해 주세요",
    )


def _shift_months(day: date, months: int) -> date:
    """day 에서 months 달 뒤(음수면 앞)의 같은 날짜. 그달에 같은 날짜가 없으면 말일이다."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _month_end(first: date) -> date:
    return first.replace(day=calendar.monthrange(first.year, first.month)[1])


def _count_range(kind: str, n: int, today: date) -> DateRange:
    low = COUNT_RANGES[kind][0]
    if n < low:
        raise PeriodError("count", f"{kind} 의 수는 {low} 이상이어야 해요")
    match kind:
        case "days_ago":
            day = today - timedelta(days=n)
            return DateRange(day, day)
        case "recent_days":
            return DateRange(today - timedelta(days=n - 1), today)
        case "weeks_ago":
            start = today - timedelta(days=today.weekday() + 7 * n)
            return DateRange(start, today if n == 0 else start + timedelta(days=6))
        case "months_ago":
            start = _shift_months(today.replace(day=1), -n)
            return DateRange(start, today if n == 0 else _month_end(start))
    return DateRange(_shift_months(today, -n) + timedelta(days=1), today)


def _day(m: re.Match[str], offset: int) -> _Day:
    year, month, day = m.group(offset + 1, offset + 2, offset + 3)
    return _Day(None if year is None else int(year), int(month), int(day))


def _no_date(d: _Day) -> PeriodError:
    year = "" if d.year is None else f"{d.year}년 "
    return PeriodError("no_date", f"{year}{d.month}월 {d.day}일은 없는 날짜예요")


def _exact(d: _Day) -> date:
    try:
        return date(d.year, d.month, d.day)
    except ValueError:
        raise _no_date(d) from None


def _latest(d: _Day, limit: date) -> date:
    """limit 이전(같은 날 포함)에 마지막으로 온 그 날짜."""
    for year in range(limit.year, limit.year - _LEAP_SEARCH_YEARS, -1):
        with suppress(ValueError):
            if (found := date(year, d.month, d.day)) <= limit:
                return found
    raise _no_date(d)


def _first(d: _Day, limit: date) -> date:
    """limit 이후(같은 날 포함)에 처음 오는 그 날짜."""
    for year in range(limit.year, limit.year + _LEAP_SEARCH_YEARS):
        with suppress(ValueError):
            if (found := date(year, d.month, d.day)) >= limit:
                return found
    raise _no_date(d)


def _resolve_alone(d: _Day, today: date) -> date:
    """연도가 없는 날짜는 오늘 기준으로 가장 최근에 지난 그 날짜다."""
    return _latest(d, today) if d.year is None else _exact(d)


def _day_bounds(first: _Day, last: _Day, today: date) -> tuple[date, date]:
    """두 날짜의 연도를 정해 (시작, 끝)을 반환한다. 순서는 검사하지 않는다."""
    if first.year is not None and last.year is not None:
        return _exact(first), _exact(last)
    if first.year is not None:
        start = _exact(first)
        return start, _first(last, start)
    if last.year is not None:
        end = _exact(last)
        return _latest(first, end), end
    start = _latest(first, today)
    return start, _first(last, start)


def _day_range(first: _Day, last: _Day, today: date) -> DateRange:
    """연도가 없는 쪽은 다른 쪽에 맞춘다. 둘 다 없으면 시작 날짜를 가장 최근에 지난 날짜로 정한다."""
    start, end = _day_bounds(first, last, today)
    if start > end:
        raise PeriodError("reversed", "시작 날짜가 끝 날짜보다 늦어요. dates:시작..끝 순서로 써 주세요")
    return DateRange(start, end)
