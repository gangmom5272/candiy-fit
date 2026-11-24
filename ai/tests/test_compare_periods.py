import json
from datetime import date, timedelta

import pytest

from coach_ai.adapters.memory import StepDay
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall, ToolRequest
from coach_ai.ports import HealthDataNotReadyError, HealthDataUnavailableError, MetricResult
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import ToolGateway
from coach_ai.tools.handlers import compare_periods, get_metric_summary
from coach_ai.tools.schemas import ComparePeriods, GetMetricSummary
from tests.fakes import TODAY, FailFastPort, FakeHealthData, SlowPort, at, exercise_port, request, sample_port

CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
# TODAY(10-15 목) 기준 이번 주, 지난주, 지지난주의 시작일
THIS_WEEK, LAST_WEEK, WEEK_BEFORE_LAST = date(2026, 10, 12), date(2026, 10, 5), date(2026, 9, 28)


class SummaryPort(FakeHealthData):
    """원형 summary 의 공개 평균·일수만 기간 시작일별로 바꿔 반환한다."""

    def __init__(self, base: FakeHealthData, changes: dict[date, dict]) -> None:
        super().__init__(**vars(base))
        self.changes = changes

    async def get_metric_result(self, user_id, metric, start, end):
        result = await super().get_metric_result(user_id, metric, start, end)
        snapshot = dict(result.snapshot)
        summary = {**snapshot["summary"], **self.changes.get(start, {})}
        summary["days_used_for_summary"] = summary["days_with_data"]
        snapshot["summary"] = summary
        return MetricResult(result.user_id, snapshot)


def averages(current: float | None, baseline: float | None, days=(5, 7), completed=(6, 7)) -> dict[date, dict]:
    return {
        THIS_WEEK: {"avg": current, "days_with_data": days[0], "completed_days": completed[0]},
        LAST_WEEK: {"avg": baseline, "days_with_data": days[1], "completed_days": completed[1]},
    }


async def compare(port, metric="steps", current="this_week", baseline="last_week"):
    inp = ComparePeriods(metric=metric, current=current, baseline=baseline)
    return await compare_periods(inp, request(port))


@pytest.mark.parametrize(
    ("metric", "current", "baseline", "difference", "rate"),
    [
        # 표시 평균은 7,412걸음과 7,001걸음이지만 차이는 공개 평균에서 바로 계산해 한 번만 반올림한다
        ("steps", 7412.4, 7000.6, (412.0, "+412걸음"), (5.9, "+5.9%")),
        # 2.15 는 부동소수 뺄셈이면 2.1499… 라 내림이 된다
        ("heart_rate", 72.25, 70.10, (2.2, "+2.2bpm"), (3.1, "+3.1%")),
        ("sleep", 352.0, 386.0, (-34.0, "-34분"), (-8.8, "-8.8%")),
        ("sleep", 452.5, 386.0, (67.0, "+1시간 7분"), (17.2, "+17.2%")),
        # 변화율 0.05% 도 ROUND_HALF_UP 으로 0.1% 가 된다
        ("steps", 8004.0, 8000.0, (4.0, "+4걸음"), (0.1, "+0.1%")),
        ("steps", 8000.0, 8000.0, (0.0, "0걸음"), (0.0, "0.0%")),
        # 반올림해 0 이 된 음수는 부호 없이 쓴다
        ("steps", 8000.4, 8000.6, (0.0, "0걸음"), (0.0, "0.0%")),
    ],
)
async def test_difference_and_rate_come_from_public_averages_rounded_once(metric, current, baseline, difference, rate):
    out = await compare(SummaryPort(sample_port(), averages(current, baseline)), metric=metric)
    assert out.status == "ok"
    assert (out.payload["difference"]["value"], out.payload["difference"]["display"]) == difference
    assert (out.payload["change_rate_pct"]["value"], out.payload["change_rate_pct"]["display"]) == rate
    assert "-0.0" not in json.dumps(out.payload)


async def test_period_results_hold_display_average_counts_and_coverage_like_summary():
    port = SummaryPort(sample_port(), averages(7412.4, 7000.6))
    out = await compare(port)
    current, baseline = out.payload["current"], out.payload["baseline"]
    assert current["period"] == {
        "period": "this_week",
        "start": "2026-10-12",
        "end": "2026-10-15",
        "basis": "날짜(KST)",
    }
    assert current["avg"] == {"value": 7412.0, "display": "7,412걸음"}
    assert baseline["avg"] == {"value": 7001.0, "display": "7,001걸음"}
    assert (current["days_with_data"], current["completed_days"]) == (5, 6)
    assert (baseline["days_with_data"], baseline["completed_days"]) == (7, 7)
    for name, period in (("current", "this_week"), ("baseline", "last_week")):
        summary = await get_metric_summary(GetMetricSummary(metric="steps", period=period), request(port))
        assert out.payload[name]["coverage"] == summary.payload["coverage"]
        assert out.payload[name]["in_progress"] == summary.payload["in_progress"]
    # 오늘이 든 기간만 오늘 진행 값이 있다
    assert current["in_progress"]["display"] == "오늘 진행 중 4,210걸음 (12:05까지 기록 기준)"
    assert baseline["in_progress"] is None


@pytest.mark.parametrize(
    ("days", "completed", "reliable"),
    [
        ((4, 7), (5, 7), True),  # 지난주 7/7일 vs 이번 주(토요일) 4/5일
        ((2, 7), (3, 7), False),  # 이번 주(목요일) 2/3일: 3일 미만
        ((4, 25), (19, 31), False),  # 이번 달(20일) 4/19일: 절반 미만
        ((3, 3), (6, 6), True),  # 3일과 절반 경계
        ((3, 3), (7, 6), False),  # 3/7 은 절반 미만
        ((3, 2), (6, 4), False),  # 기준 기간이 3일 미만
    ],
)
async def test_reliable_needs_three_days_and_half_of_completed_days_on_both_sides(days, completed, reliable):
    port = SummaryPort(sample_port(), averages(7000.0, 6000.0, days=days, completed=completed))
    out = await compare(port)
    assert out.payload["reliable"] is reliable
    # 기록이 적어도 차이·변화율은 그대로 전달한다
    assert out.payload["difference"]["value"] == 1000.0


@pytest.mark.parametrize("side", ["current", "baseline"])
async def test_missing_average_on_one_side_leaves_difference_and_rate_empty(side):
    current, baseline = (None, 6000.0) if side == "current" else (7000.0, None)
    days = (0, 7) if side == "current" else (5, 0)
    out = await compare(SummaryPort(sample_port(), averages(current, baseline, days=days)))
    assert out.payload[side]["avg"] is None
    assert (out.payload["difference"], out.payload["change_rate_pct"], out.payload["reliable"]) == (None, None, False)


async def test_zero_baseline_average_keeps_difference_without_rate():
    port = SummaryPort(exercise_port(), averages(15.0, 0.0, days=(3, 7)))
    out = await compare(port, metric="exercise")
    assert out.payload["difference"] == {"value": 15.0, "display": "+15분"}
    assert out.payload["change_rate_pct"] is None


@pytest.mark.parametrize(
    ("current", "baseline"),
    [
        ("this_week", "last_7_days"),  # 겹치는 기간
        ("last_7_days", "last_14_days"),
        ("last_week", "this_week"),  # 현재 기간이 기준 기간보다 이르다
        ("last_month", "this_month"),
        ("yesterday", "today"),
    ],
)
async def test_overlapping_or_reversed_periods_get_guidance_without_lookup(current, baseline):
    port = sample_port()
    out = await compare(port, current=current, baseline=baseline)
    assert out.status == "invalid_periods" and out.evidence == ()
    assert port.calls == []
    message = out.to_message([])
    assert message["error"] == "invalid_periods"
    assert "current" in message["message"] and "baseline" in message["message"]
    assert "겹치지 않" in message["message"] and "다시 호출" in message["message"]
    # 기록이 없다는 답으로 옮기지 않게 한다
    assert "기록이 없다는 뜻이 아니다" in message["message"]


async def test_two_evidence_one_per_period_with_summary_text_and_snapshot():
    port = sample_port()
    out = await compare(port, metric="sleep")
    assert [e.tool for e in out.evidence] == ["compare_periods", "compare_periods"]
    assert [e.metric for e in out.evidence] == ["sleeps", "sleeps"]
    for evidence, period in zip(out.evidence, ("this_week", "last_week"), strict=True):
        summary = await get_metric_summary(GetMetricSummary(metric="sleep", period=period), request(port))
        [expected] = summary.evidence
        assert (evidence.period, evidence.summary, evidence.snapshot) == (
            expected.period,
            expected.summary,
            expected.snapshot,
        )


async def test_gateway_numbers_both_evidence_and_counts_both_lookups():
    audit = MemoryAuditSink()
    gateway = ToolGateway(sample_port(), TODAY, audit=audit)
    call = ToolCall("c1", "compare_periods", {"metric": "steps", "current": "this_week", "baseline": "last_week"})
    [reply] = await gateway.execute([call], CTX)
    message = json.loads(reply.content)
    assert message["evidence_ids"] == ["E1", "E2"]
    assert [e.period.period for e in reply.evidence] == ["this_week", "last_week"]
    [record] = audit.records()
    # 이번 주 4일과 지난주 7일
    assert (record.result_count, record.evidence_ids) == (11, tuple(e.id for e in reply.evidence))


async def test_two_periods_are_looked_up_concurrently_within_one_deadline():
    port = SlowPort(sample_port(), 0.3)
    gateway = ToolGateway(port, TODAY, timeout=0.5)
    call = ToolCall("c1", "compare_periods", {"metric": "steps", "current": "this_week", "baseline": "last_week"})
    [reply] = await gateway.execute([call], CTX)
    assert port.peak == 2
    assert len(reply.evidence) == 2


async def test_deadline_covers_the_whole_comparison():
    port = SlowPort(sample_port(), 0.3)
    gateway = ToolGateway(port, TODAY, timeout=0.2)
    call = ToolCall("c1", "compare_periods", {"metric": "steps", "current": "this_week", "baseline": "last_week"})
    with pytest.raises(HealthDataUnavailableError) as e:
        await gateway.execute([call], CTX)
    assert e.value.metrics == ("steps",)


async def test_oxygen_comparison_is_marked_as_main_sleep_value():
    out = await compare(sample_port(), metric="oxygen_saturation")
    assert out.payload["measured_during"] == "주 수면 중"
    assert out.payload["current"]["period"]["basis"] == "수면 종료일(KST)"


async def test_today_only_period_gets_note_to_tell_progress_and_baseline_average_apart():
    out = await compare(sample_port(), current="today", baseline="yesterday")
    payload = out.payload
    assert payload["current"]["avg"] is None and payload["current"]["completed_days"] == 0
    assert payload["current"]["in_progress"]["display"] == "오늘 진행 중 4,210걸음 (12:05까지 기록 기준)"
    assert payload["difference"] is None and payload["change_rate_pct"] is None
    note = payload["in_progress_note"]
    assert "진행 중" in note and "하루 평균이 없어" in note and "in_progress" in note and "baseline" in note
    # 기록이 적어서가 아니라 오늘이 진행 중이라 비교하지 못하므로 기록 부족 안내는 넣지 않는다
    assert payload["reliable"] is False and "reliable_note" not in payload


async def test_period_without_completed_days_before_link_has_no_progress_note():
    # 기준 기간이 모두 연동 전이라 완성된 날이 없어도 오늘 진행 값이 없으면 안내하지 않는다
    port = SummaryPort(sample_port(), averages(7000.0, None, days=(5, 0), completed=(6, 0)))
    out = await compare(port)
    assert out.payload["baseline"]["avg"] is None and out.payload["baseline"]["in_progress"] is None
    assert "in_progress_note" not in out.payload


async def test_unreliable_comparison_gets_note_with_criteria_and_reliable_one_does_not():
    low = await compare(SummaryPort(sample_port(), averages(7000.0, 6000.0, days=(2, 7), completed=(3, 7))))
    assert low.payload["reliable"] is False
    note = low.payload["reliable_note"]
    assert "단정하기 어렵다" in note and "3일 이상" in note and "절반 이상" in note
    # 이번 주는 완성된 날이 있어 진행 중 안내는 없다
    assert "in_progress_note" not in low.payload
    enough = await compare(SummaryPort(sample_port(), averages(7000.0, 6000.0, days=(4, 7), completed=(5, 7))))
    assert enough.payload["reliable"] is True and "reliable_note" not in enough.payload


async def test_oxygen_difference_is_in_percentage_points_and_rate_in_percent():
    out = await compare(SummaryPort(sample_port(), averages(96.3, 95.8)), metric="oxygen_saturation")
    assert out.payload["difference"] == {"value": 0.5, "display": "+0.5%p"}
    assert out.payload["change_rate_pct"] == {"value": 0.5, "display": "+0.5%"}


async def test_change_rate_display_has_thousands_separator():
    out = await compare(SummaryPort(exercise_port(), averages(60.0, 1.0, days=(3, 7))), metric="exercise")
    assert out.payload["change_rate_pct"] == {"value": 5900.0, "display": "+5,900.0%"}


@pytest.mark.parametrize(
    ("current", "baseline", "options"),
    [
        # TODAY 10-15 목: 이번 달은 10-01 에 시작하고, 지지난주(9-28~10-04)는 10-04 에 끝난다.
        # 같은 종류로 바로 앞 기간이 이름 기간과 범위가 같으면 이름 기간 목록 그대로다
        ("this_month", "last_30_days", ["last_month"]),
        ("this_week", "last_7_days", ["last_week", "week_before_last", "last_month"]),
        ("last_week", "this_week", ["week_before_last", "last_month"]),
        # 이름 기간 목록에 없으면 같은 종류로 바로 앞 기간을 맨 앞에 넣는다
        ("last_7_days", "last_14_days", ["dates:10-02..10-08", "week_before_last", "last_month"]),
        ("yesterday", "today", ["days_ago:2", "last_week", "week_before_last", "last_month"]),
        ("last_month", "last_week", ["months_ago:2"]),
        # 새 표현도 같은 규칙이다
        ("weeks_ago:3", "weeks_ago:1", ["weeks_ago:4"]),
        ("recent_months:1", "months_ago:1", ["dates:08-16..09-15"]),
        ("date:10-13", "weeks_ago:0", ["date:10-12", "last_week", "week_before_last", "last_month"]),
        # 바로 앞 7일(9-28~10-04)은 지지난주와 범위가 같아 더하지 않는다
        ("dates:10-05..10-11", "recent_days:7", ["week_before_last", "last_month"]),
        # 요일 하루(10-13 화)의 바로 앞은 한 주 전 같은 요일이다
        ("weekday:tue", "weeks_ago:0", ["date:10-06", "last_week", "week_before_last", "last_month"]),
        # 주와 요일을 함께 말한 하루(지난주 수요일 10-07)의 바로 앞은 한 주 전 같은 요일(9-30)이다
        ("weeks_ago:1.wed", "weeks_ago:0", ["weeks_ago:2.wed", "week_before_last", "last_month"]),
        # 범위(10-01..10-14)의 바로 앞은 같은 길이의 날짜 구간(9-17..9-30)이다
        ("weeks_ago:2.thu..days_ago:1", "weeks_ago:0", ["dates:09-17..09-30", "last_month"]),
    ],
)
async def test_period_error_lists_baselines_that_end_before_current_starts(current, baseline, options):
    """current 가 baseline 보다 늦지 않은 기간 쌍 → current 보다 앞에서 끝나는 baseline_options 목록."""
    out = await compare(sample_port(), current=current, baseline=baseline)
    message = out.to_message([])
    assert message["baseline_options"] == options
    assert "baseline_options" in message["message"] and "this_week, last_week" not in message["message"]


async def test_period_error_without_any_earlier_baseline_says_so():
    # 11달 전(2025-11)보다 앞의 달은 12개월 전 같은 날짜(2025-10-15)보다 일찍 시작해 조회할 수 없다
    out = await compare(sample_port(), current="months_ago:11", baseline="months_ago:0")
    message = out.to_message([])
    assert message["baseline_options"] == []
    assert "함께 쓸 수 있는 baseline 이 없다" in message["message"]


@pytest.mark.parametrize(("current", "baseline"), [("last_week", "weeks_ago:1"), ("weeks_ago:0", "this_week")])
async def test_same_range_in_different_words_gets_guidance_without_lookup(current, baseline):
    port = sample_port()
    out = await compare(port, current=current, baseline=baseline)
    assert out.status == "invalid_periods" and port.calls == []


async def test_weeks_ago_compared_with_three_weeks_before():
    port = sample_port()
    out = await compare(port, current="weeks_ago:0", baseline="weeks_ago:3")
    assert out.status == "ok"
    assert sorted(call[3:] for call in port.calls) == [
        (date(2026, 9, 21), date(2026, 9, 27)),
        (date(2026, 10, 12), date(2026, 10, 15)),
    ]
    assert out.payload["baseline"]["period"]["display"] == "3주 전 (9월 21일-27일)"
    assert out.evidence[1].summary.startswith("3주 전 (9월 21일-27일) 걸음 수 ")


async def test_last_week_is_compared_with_week_before_last():
    changes = {
        LAST_WEEK: {"avg": 95.8, "days_with_data": 6, "completed_days": 7},
        WEEK_BEFORE_LAST: {"avg": 96.3, "days_with_data": 7, "completed_days": 7},
    }
    port = SummaryPort(sample_port(), changes)
    out = await compare(port, metric="oxygen_saturation", current="last_week", baseline="week_before_last")
    assert out.status == "ok"
    # 포트에는 기간 이름이 아니라 날짜 범위가 전달된다
    assert sorted(call[3:] for call in port.calls) == [
        (date(2026, 9, 28), date(2026, 10, 4)),
        (date(2026, 10, 5), date(2026, 10, 11)),
    ]
    assert out.payload["baseline"]["period"] == {
        "period": "week_before_last",
        "start": "2026-09-28",
        "end": "2026-10-04",
        "basis": "수면 종료일(KST)",
    }
    assert out.payload["difference"] == {"value": -0.5, "display": "-0.5%p"}
    assert out.payload["reliable"] is True
    assert [e.period.period for e in out.evidence] == ["last_week", "week_before_last"]
    assert out.evidence[1].summary == "지지난주 수면 중 산소포화도 평균 96.3% (7일 중 7일 기록)"


@pytest.mark.parametrize("current", ["last_week", "this_week", "last_7_days", "yesterday"])
async def test_periods_after_week_before_last_are_looked_up(current):
    port = sample_port()
    out = await compare(port, current=current, baseline="week_before_last")
    assert out.status == "ok" and len(port.calls) == 2


@pytest.mark.parametrize(
    ("current", "baseline"),
    [
        # TODAY 10-15 목: 지지난주 9-28~10-04, 최근 14일 10-02~, 최근 30일 9-16~, 이번 달 10-01~, 지난달 9-01~9-30
        ("last_14_days", "week_before_last"),
        ("week_before_last", "last_14_days"),
        ("last_30_days", "week_before_last"),
        ("this_month", "week_before_last"),
        ("week_before_last", "last_month"),
        ("week_before_last", "last_week"),  # 현재 기간이 기준 기간보다 이르다
    ],
)
async def test_periods_overlapping_week_before_last_get_guidance_without_lookup(current, baseline):
    port = sample_port()
    out = await compare(port, current=current, baseline=baseline)
    assert out.status == "invalid_periods" and out.evidence == ()
    assert port.calls == []


async def test_last_14_days_starts_right_after_week_before_last_on_sunday():
    # 10-18(일)에는 최근 14일이 10-05 에 시작해 지지난주(9-28~10-04)와 겹치지 않는다. 겹침은 이름이 아니라 날짜로 정한다
    port = FakeHealthData(
        steps={"u1": [StepDay(WEEK_BEFORE_LAST + timedelta(days=i), 6000 + i) for i in range(21)]},
        linked_from={"u1": date(2026, 9, 20)},
        now=at(18, 21, 0),
    )
    inp = ComparePeriods(metric="steps", current="last_14_days", baseline="week_before_last")
    out = await compare_periods(inp, ToolRequest("u1", date(2026, 10, 18), port))
    assert out.status == "ok"
    assert (out.payload["current"]["period"]["start"], out.payload["baseline"]["period"]["end"]) == (
        "2026-10-05",
        "2026-10-04",
    )


@pytest.mark.parametrize(
    ("today", "options"),
    [
        # 10-15(목) 지지난주는 9-28 에 시작하는데 지난달은 9-30 에 끝나 이름 기간 중에는 함께 쓸 수 있는 기간이 없다
        (date(2026, 10, 15), ["weeks_ago:3"]),
        # 10-29(목) 지지난주는 10-12 에 시작한다
        (date(2026, 10, 29), ["weeks_ago:3", "last_month"]),
    ],
)
async def test_week_before_last_as_current_lists_only_periods_that_end_before_it(today, options):
    port = sample_port()
    inp = ComparePeriods(metric="steps", current="week_before_last", baseline="last_week")
    out = await compare_periods(inp, ToolRequest("u1", today, port))
    assert out.to_message([])["baseline_options"] == options
    assert port.calls == []


async def test_one_failing_period_fails_comparison_and_cancels_the_other_lookup():
    port = FailFastPort(sample_port(), fail_start=LAST_WEEK)
    with pytest.raises(HealthDataNotReadyError) as e:
        await compare(port)
    assert e.value.metrics == ("steps",)
    # 실패한 뒤 기다리던 이번 주 조회는 처리 함수가 끝나기 전에 취소된다
    assert port.cancelled == 1


async def test_failing_period_ends_gateway_call_without_waiting_for_the_other_lookup():
    audit = MemoryAuditSink()
    gateway = ToolGateway(FailFastPort(sample_port(), fail_start=LAST_WEEK), TODAY, audit=audit, timeout=1.0)
    call = ToolCall("c1", "compare_periods", {"metric": "steps", "current": "this_week", "baseline": "last_week"})
    with pytest.raises(HealthDataNotReadyError):
        await gateway.execute([call], CTX)
    [record] = audit.records()
    assert (record.reason, record.evidence_ids, record.result_count) == ("data_not_ready", (), 0)
    assert record.latency_ms < 1000
