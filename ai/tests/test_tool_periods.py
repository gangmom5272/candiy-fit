"""도구 기간 칸의 새 기간 표현: 입력 검사, 결과의 표시 문구, 조회 한도 안내, 게이트웨이 기록."""

import json
import re
from datetime import date

import pytest
from pydantic import ValidationError

from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import DENIED_MESSAGE, ToolGateway
from coach_ai.tools.handlers import (
    compare_periods,
    get_daily_trend,
    get_exercise_sessions,
    get_health_overview,
    get_metric_summary,
    get_sleep_records,
)
from coach_ai.tools.schemas import (
    TOOL_INPUTS,
    ComparePeriods,
    GetDailyTrend,
    GetExerciseSessions,
    GetHealthOverview,
    GetMetricSummary,
    GetSleepRecords,
)
from tests.fakes import TODAY, D, exercise_port, request, sample_port

CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
# TODAY(2026-10-15 목) 기준으로 모두 조회할 수 있는 표현
EXPRESSIONS = (
    "days_ago:3",
    "recent_days:10",
    "weeks_ago:2",
    "months_ago:1",
    "recent_months:1",
    "date:10-13",
    "date:2026-10-13",
    "dates:10-05..10-11",
    "dates:2026-09-28..2026-10-04",
    "weekday:tue",
    "weeks_ago:1.wed",
    "weeks_ago:2.thu..days_ago:1",
    "date:10-05..weeks_ago:1",
)
PERIOD_ARGS = {
    "get_metric_summary": ("period", {"metric": "steps"}),
    "get_daily_trend": ("period", {"metric": "heart_rate"}),
    "get_sleep_records": ("period", {}),
    "get_health_overview": ("period", {}),
    "get_exercise_sessions": ("period", {}),
    "compare_periods": ("current", {"metric": "sleep", "baseline": "months_ago:2"}),
}


def _without_period_names(value):
    """결과에서 기간 표현과 표시 문구를 뺀다. 이름 기간과 새 표현의 결과가 나머지 칸에서 같은지 비교한다."""
    if isinstance(value, dict):
        if {"period", "start", "end", "basis"} <= set(value):
            return {k: v for k, v in value.items() if k not in ("period", "display")}
        return {k: _without_period_names(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_without_period_names(v) for v in value]
    return value


@pytest.mark.parametrize("name", list(PERIOD_ARGS))
@pytest.mark.parametrize("period", [*EXPRESSIONS, "last_week"])
def test_every_period_field_accepts_expressions_and_named_periods(name, period):
    field, others = PERIOD_ARGS[name]
    args = {**others, field: period}
    # LLM 도구 호출 인자는 JSON 문자열로 온다
    assert getattr(TOOL_INPUTS[name].model_validate_json(json.dumps(args)), field) == period
    assert getattr(TOOL_INPUTS[name].model_validate(args), field) == period


def test_compare_periods_accepts_expressions_in_both_fields():
    inp = ComparePeriods.model_validate({"metric": "steps", "current": "weeks_ago:0", "baseline": "weeks_ago:3"})
    assert (inp.current, inp.baseline) == ("weeks_ago:0", "weeks_ago:3")


@pytest.mark.parametrize("name", list(PERIOD_ARGS))
@pytest.mark.parametrize(
    "period", ["3_weeks_ago", "weeks_ago:-1", "weeks ago:1", "date:2026/10/13", "dates:10-05~10-11", "last_90_days"]
)
def test_period_fields_reject_text_outside_expressions(name, period):
    field, others = PERIOD_ARGS[name]
    with pytest.raises(ValidationError):
        TOOL_INPUTS[name].model_validate({**others, field: period})


async def test_summary_of_expression_shows_kind_and_dates_in_result_and_evidence():
    port = sample_port()
    named = await get_metric_summary(GetMetricSummary(metric="steps", period="last_week"), request(port))
    out = await get_metric_summary(GetMetricSummary(metric="steps", period="weeks_ago:1"), request(port))
    assert out.status == "ok"
    assert out.payload["period"] == {
        "period": "weeks_ago:1",
        "start": "2026-10-05",
        "end": "2026-10-11",
        "basis": "날짜(KST)",
        "display": "지난주 (10월 5일-11일)",
    }
    # 수치와 기록 일수는 같은 범위의 이름 기간과 같고, 기간 문구만 종류와 날짜를 함께 쓴다
    assert out.evidence[0].summary == named.evidence[0].summary.replace("지난주", "지난주 (10월 5일-11일)", 1)
    assert out.evidence[0].summary.startswith("지난주 (10월 5일-11일) 걸음 수 평균 ")
    assert out.evidence[0].period.model_dump(mode="json")["display"] == "지난주 (10월 5일-11일)"


@pytest.mark.parametrize(
    ("named", "expression"),
    [
        ("yesterday", "days_ago:1"),
        ("last_7_days", "recent_days:7"),
        ("this_week", "weeks_ago:0"),
        ("last_week", "weeks_ago:1"),
        ("this_month", "months_ago:0"),
    ],
)
@pytest.mark.parametrize(
    ("handler", "model", "args"),
    [
        (get_metric_summary, GetMetricSummary, {"metric": "steps"}),
        (get_metric_summary, GetMetricSummary, {"metric": "heart_rate"}),
        (get_daily_trend, GetDailyTrend, {"metric": "sleep"}),
        (get_sleep_records, GetSleepRecords, {}),
        (get_health_overview, GetHealthOverview, {}),
    ],
)
async def test_expression_result_matches_named_period_except_period_text(named, expression, handler, model, args):
    port = sample_port()
    before = await handler(model.model_validate({**args, "period": named}), request(port))
    after = await handler(model.model_validate({**args, "period": expression}), request(port))
    assert _without_period_names(after.payload) == _without_period_names(before.payload)
    assert [e.snapshot for e in after.evidence] == [e.snapshot for e in before.evidence]
    assert [(e.period.start, e.period.end) for e in after.evidence] == [
        (e.period.start, e.period.end) for e in before.evidence
    ]


@pytest.mark.parametrize(
    ("handler", "model", "args"),
    [
        (get_metric_summary, GetMetricSummary, {"metric": "steps", "period": "last_7_days"}),
        (get_daily_trend, GetDailyTrend, {"metric": "steps", "period": "this_week"}),
        (get_sleep_records, GetSleepRecords, {"period": "yesterday"}),
        (get_health_overview, GetHealthOverview, {"period": "last_week"}),
    ],
)
async def test_named_period_results_have_no_display(handler, model, args):
    out = await handler(model.model_validate(args), request(sample_port()))
    assert "display" not in json.dumps(_period_spans(out.payload), ensure_ascii=False)
    assert all("display" not in e.period.model_dump(mode="json") for e in out.evidence)


def _period_spans(value):
    if isinstance(value, dict):
        if {"period", "start", "end", "basis"} <= set(value):
            return [value]
        return [s for v in value.values() for s in _period_spans(v)]
    if isinstance(value, list):
        return [s for v in value for s in _period_spans(v)]
    return []


@pytest.mark.parametrize(
    ("handler", "model", "args", "note"),
    [
        (
            get_daily_trend,
            GetDailyTrend,
            {"metric": "steps", "period": "recent_days:32"},
            "날짜별 값은 한 번에 31일까지 조회된다.",
        ),
        # 상대 날짜 범위도 31일을 넘으면 같은 안내다 (8-01..10-14 는 75일)
        (
            get_daily_trend,
            GetDailyTrend,
            {"metric": "steps", "period": "months_ago:2..days_ago:1"},
            "날짜별 값은 한 번에 31일까지 조회된다.",
        ),
        (
            get_sleep_records,
            GetSleepRecords,
            {"period": "months_ago:2..days_ago:1"},
            "날짜별 수면 기록은 한 번에 31일까지 조회된다.",
        ),
        (
            get_sleep_records,
            GetSleepRecords,
            {"period": "recent_months:2"},
            "날짜별 수면 기록은 한 번에 31일까지 조회된다.",
        ),
        (
            get_exercise_sessions,
            GetExerciseSessions,
            {"period": "recent_months:6"},
            "운동 기록은 한 번에 31일까지 조회된다.",
        ),
    ],
)
async def test_list_tools_guide_periods_over_31_days_without_lookup(handler, model, args, note):
    """31일을 넘는 기간(옛 표현·상대 범위) → unavailable_period 안내, 증거·조회 없음."""
    port = exercise_port() if handler is get_exercise_sessions else sample_port()
    out = await handler(model.model_validate(args), request(port))
    assert out.status == "unavailable_period" and out.evidence == ()
    assert port.calls == []
    message = out.to_message([])
    assert message["error"] == "unavailable_period"
    assert (message["tool"], message["period"]) == (handler.__name__, args["period"])
    assert message["message"].startswith(note)
    # 같은 기간은 기간 요약으로 조회할 수 있다고 알린다
    assert "get_metric_summary" in message["message"]
    # 결과 문장은 사실만 쓰고 지시하는 문장을 쓰지 않는다
    assert not any(word in message["message"] for word in ("주세요", "제안", "하세요"))


# 6주 질문(최근 42일)의 31일 한도 안내 (TODAY 10-15)
SLEEP_42_DAYS_NOTE = (
    "날짜별 수면 기록은 한 번에 31일까지 조회된다. 최근 42일(9월 4일-10월 15일) 기간은 42일이라 조회하지 않았다. "
    "31일 이하로 나누면 dates:09-04..09-14, dates:09-15..10-15 로 조회된다. "
    "기간 요약(get_metric_summary)은 이 기간 전체의 수면 시간을 조회하지만 날짜별 값은 없다."
)
_EXPRESSION = re.compile(r"dates?:[0-9-]+(?:\.\.[0-9-]+)?")


async def test_sleep_records_over_31_days_states_limit_dates_and_split_periods():
    port = sample_port()
    out = await get_sleep_records(GetSleepRecords(period="recent_days:42"), request(port))
    assert out.status == "unavailable_period" and out.evidence == () and port.calls == []
    assert out.payload["message"] == SLEEP_42_DAYS_NOTE


async def test_split_periods_in_the_guidance_are_each_queried():
    port = sample_port()
    out = await get_sleep_records(GetSleepRecords(period="recent_days:42"), request(port))
    parts = _EXPRESSION.findall(out.payload["message"])
    assert parts == ["dates:09-04..09-14", "dates:09-15..10-15"]
    spans = []
    for part in parts:
        result = await get_sleep_records(GetSleepRecords(period=part), request(port))
        assert result.status == "ok"
        spans.append((result.payload["period"]["start"], result.payload["period"]["end"]))
    # 두 기간을 이으면 질문한 42일이다
    assert spans == [("2026-09-04", "2026-09-14"), ("2026-09-15", "2026-10-15")]


@pytest.mark.parametrize(
    ("handler", "model", "args", "message"),
    [
        (
            get_daily_trend,
            GetDailyTrend,
            {"metric": "steps", "period": "recent_months:6"},
            "날짜별 값은 한 번에 31일까지 조회된다. 최근 6달(4월 16일-10월 15일) 기간은 183일이라 조회하지 않았다. "
            "기간 요약(get_metric_summary)은 이 기간 전체를 조회하지만 날짜별 값은 없다.",
        ),
        (
            get_sleep_records,
            GetSleepRecords,
            {"period": "recent_days:50"},
            "날짜별 수면 기록은 한 번에 31일까지 조회된다. "
            "최근 50일(8월 27일-10월 15일) 기간은 50일이라 조회하지 않았다. "
            "기간 요약(get_metric_summary)은 이 기간 전체의 수면 시간을 조회하지만 날짜별 값은 없다.",
        ),
        (
            get_exercise_sessions,
            GetExerciseSessions,
            {"period": "dates:07-01..09-30"},
            "운동 기록은 한 번에 31일까지 조회된다. 7월 1일-9월 30일 기간은 92일이라 조회하지 않았다. "
            "기간 요약(get_metric_summary, metric=exercise)은 이 기간 전체의 운동 시간을 조회하지만 "
            "종류별·세션별 기록은 없다.",
        ),
    ],
)
async def test_periods_over_seven_weeks_get_no_split(handler, model, args, message):
    """7주(49일)를 넘는 기간에는 나눈 기간을 넣지 않는다."""
    port = exercise_port() if handler is get_exercise_sessions else sample_port()
    out = await handler(model.model_validate(args), request(port))
    assert out.status == "unavailable_period" and port.calls == []
    assert out.payload["message"] == message


async def test_seven_weeks_is_the_longest_period_with_split():
    out = await get_sleep_records(GetSleepRecords(period="recent_days:49"), request(sample_port()))
    assert "31일 이하로 나누면 dates:08-28..09-14, dates:09-15..10-15 로 조회된다." in out.payload["message"]


@pytest.mark.parametrize(
    ("handler", "model", "args"),
    [
        (get_daily_trend, GetDailyTrend, {"metric": "steps", "period": "recent_days:31"}),
        (get_sleep_records, GetSleepRecords, {"period": "months_ago:0"}),
        (get_exercise_sessions, GetExerciseSessions, {"period": "recent_months:1"}),
    ],
)
async def test_list_tools_take_31_days(handler, model, args):
    port = exercise_port() if handler is get_exercise_sessions else sample_port()
    out = await handler(model.model_validate(args), request(port))
    assert out.status == "ok"


async def test_summary_tools_take_up_to_twelve_months():
    port = sample_port()
    out = await get_metric_summary(GetMetricSummary(metric="steps", period="recent_months:12"), request(port))
    assert out.status == "ok"
    assert port.calls[-1][3:] == (D(2025, 10, 16), TODAY)
    assert out.payload["period"]["display"] == "최근 12달 (2025년 10월 16일-2026년 10월 15일)"
    overview = await get_health_overview(GetHealthOverview(period="recent_months:6"), request(port))
    assert overview.status == "ok" and len(overview.evidence) == 4
    inp = ComparePeriods(metric="steps", current="months_ago:0", baseline="months_ago:11")
    compared = await compare_periods(inp, request(port))
    assert compared.status == "ok"
    assert compared.payload["baseline"]["period"]["display"] == "11달 전 (2025년 11월 1일-30일)"


@pytest.mark.parametrize(
    ("handler", "model", "args", "field", "fragment"),
    [
        (get_metric_summary, GetMetricSummary, {"metric": "steps", "period": "months_ago:13"}, "period", "12개월"),
        (get_metric_summary, GetMetricSummary, {"metric": "steps", "period": "date:2025-10-14"}, "period", "12개월"),
        (get_health_overview, GetHealthOverview, {"period": "date:2026-10-16"}, "period", "아직 오지 않은"),
        (get_daily_trend, GetDailyTrend, {"metric": "sleep", "period": "date:02-30"}, "period", "없는 날짜"),
        (
            compare_periods,
            ComparePeriods,
            {"metric": "steps", "current": "weeks_ago:0", "baseline": "months_ago:13"},
            "baseline",
            "12개월",
        ),
        (
            compare_periods,
            ComparePeriods,
            {"metric": "steps", "current": "date:2026-10-20", "baseline": "weeks_ago:1"},
            "current",
            "아직 오지 않은",
        ),
    ],
)
async def test_periods_out_of_limits_get_guidance_without_lookup(handler, model, args, field, fragment):
    port = sample_port()
    out = await handler(model.model_validate(args), request(port))
    assert out.status == "unavailable_period" and out.evidence == ()
    assert port.calls == []
    message = out.to_message([])
    assert message[field] == args[field] and message["tool"] == handler.__name__
    assert fragment in message["message"]


async def test_gateway_logs_period_guidance_as_allowed_call_without_evidence():
    audit = MemoryAuditSink()
    gateway = ToolGateway(sample_port(), TODAY, audit=audit)
    args = {"metric": "steps", "period": "recent_months:6"}
    [reply] = await gateway.execute([ToolCall("c1", "get_daily_trend", args)], CTX)
    assert reply.evidence == ()
    body = json.loads(reply.content)
    assert (body["error"], body["tool"], body["period"]) == ("unavailable_period", "get_daily_trend", "recent_months:6")
    [record] = audit.records()
    assert (record.decision, record.reason, record.result_count, record.args) == (
        "allow",
        "unavailable_period",
        0,
        args,
    )


async def test_gateway_denies_text_outside_expressions_without_reason():
    audit = MemoryAuditSink()
    gateway = ToolGateway(sample_port(), TODAY, audit=audit)
    call = ToolCall("c1", "get_metric_summary", {"metric": "steps", "period": "3주 전"})
    [reply] = await gateway.execute([call], CTX)
    assert reply.content == DENIED_MESSAGE
    assert [(r.decision, r.reason) for r in audit.records()] == [("deny", "invalid_args")]


async def test_gateway_runs_expression_and_numbers_its_evidence():
    audit = MemoryAuditSink()
    gateway = ToolGateway(sample_port(), TODAY, audit=audit)
    calls = [
        ToolCall("c1", "get_metric_summary", {"metric": "steps", "period": "date:10-13"}),
        ToolCall("c2", "compare_periods", {"metric": "sleep", "current": "weeks_ago:0", "baseline": "weeks_ago:1"}),
    ]
    replies = await gateway.execute(calls, CTX)
    assert [e.ref for r in replies for e in r.evidence] == ["E1", "E2", "E3"]
    first = replies[0].evidence[0]
    assert (first.period.start, first.period.end) == (D(2026, 10, 13), D(2026, 10, 13))
    assert first.period.display == "10월 13일"
    assert first.summary.startswith("10월 13일 걸음 수 평균 ")
    assert [r.args for r in audit.records()] == [c.args for c in calls]
    assert all(r.decision == "allow" and r.reason is None for r in audit.records())


async def test_weekday_summary_is_that_day_with_day_name_and_date():
    port = sample_port()
    out = await get_metric_summary(GetMetricSummary(metric="steps", period="weekday:tue"), request(port))
    assert out.status == "ok"
    # TODAY(10-15 목)보다 앞선 가장 최근 화요일은 10-13 이다
    assert port.calls[-1][3:] == (D(2026, 10, 13), D(2026, 10, 13))
    assert out.payload["period"]["display"] == "지난 화요일 (10월 13일)"
    assert out.evidence[0].summary.startswith("지난 화요일 (10월 13일) 걸음 수 평균 ")


def test_period_span_keeps_display_out_of_named_period_json():
    from coach_ai.tools.results import PeriodSpan

    named = PeriodSpan(period="last_week", start=date(2026, 10, 5), end=date(2026, 10, 11), basis="날짜(KST)")
    assert named.model_dump(mode="json") == {
        "period": "last_week",
        "start": "2026-10-05",
        "end": "2026-10-11",
        "basis": "날짜(KST)",
    }
    labeled = named.model_copy(update={"period": "weeks_ago:1", "display": "지난주 (10월 5일-11일)"})
    assert labeled.model_dump(mode="json")["display"] == "지난주 (10월 5일-11일)"
    with pytest.raises(ValidationError):
        PeriodSpan(period="3주 전", start=date(2026, 10, 5), end=date(2026, 10, 11), basis="날짜(KST)")


async def test_week_day_summary_is_that_day_with_week_and_day_name():
    """TODAY 10-15(목)에 weeks_ago:1.tue → 10-06 하루와 "지난주 화요일 (10월 6일)" 문구."""
    port = sample_port()
    out = await get_metric_summary(GetMetricSummary(metric="steps", period="weeks_ago:1.tue"), request(port))
    assert out.status == "ok"
    assert port.calls[-1][3:] == (D(2026, 10, 6), D(2026, 10, 6))
    assert out.payload["period"]["display"] == "지난주 화요일 (10월 6일)"
    assert out.evidence[0].summary.startswith("지난주 화요일 (10월 6일) ")


async def test_range_summary_shows_start_and_end_names():
    """TODAY 10-15(목)에 weeks_ago:2.thu..days_ago:1 → 10-01..10-14 조회와 시작·끝 이름 문구."""
    port = sample_port()
    out = await get_metric_summary(
        GetMetricSummary(metric="steps", period="weeks_ago:2.thu..days_ago:1"), request(port)
    )
    assert out.status == "ok"
    assert port.calls[-1][3:] == (D(2026, 10, 1), D(2026, 10, 14))
    assert out.payload["period"]["display"] == "2주 전 목요일부터 어제까지 (10월 1일-14일)"


@pytest.mark.parametrize(
    ("period", "fragment"),
    [
        ("weeks_ago:0.sun", "아직 오지 않은"),
        ("weeks_ago:1.fri..weeks_ago:1.mon", "시작..끝 순서"),
        ("weeks_ago:60.mon", "12개월"),
        ("date:02-30..days_ago:1", "없는 날짜"),
    ],
)
async def test_rejected_week_day_and_range_get_guidance_without_lookup(period, fragment):
    """거절되는 새 하루·범위 → unavailable_period 안내와 조회 0회."""
    port = sample_port()
    out = await get_metric_summary(GetMetricSummary(metric="steps", period=period), request(port))
    assert out.status == "unavailable_period" and out.evidence == ()
    assert port.calls == []
    assert fragment in out.to_message([])["message"]
