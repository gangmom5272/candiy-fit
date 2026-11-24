import json
from datetime import UTC, datetime

import pytest

from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall, ToolRequest
from coach_ai.ports import HealthDataNotReadyError
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import ToolGateway
from coach_ai.tools.handlers import get_health_overview, get_metric_summary
from coach_ai.tools.periods import local_today
from coach_ai.tools.schemas import GetHealthOverview, GetMetricSummary
from tests.fakes import FailFastPort, FakeHealthData, SlowPort

NOW = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)  # 2026-10-22 14:00 KST (목)
TODAY = local_today(NOW)
CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
ORDER = ["steps", "exercise", "sleep", "heart_rate"]


def port() -> FakeHealthData:
    return sample_health_data(CTX.user_id, NOW)


def request(p) -> ToolRequest:
    return ToolRequest(user_id=CTX.user_id, today=TODAY, port=p)


def nulls(value, path: str = "result") -> list[str]:
    if isinstance(value, dict):
        return [n for k, v in value.items() for n in nulls(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [n for i, v in enumerate(value) for n in nulls(v, f"{path}[{i}]")]
    return [path] if value is None else []


@pytest.mark.parametrize("period", ["last_7_days", "this_week", "today", "last_30_days"])
async def test_overview_holds_four_summaries_in_home_card_order(period):
    p = port()
    out = await get_health_overview(GetHealthOverview(period=period), request(p))
    assert out.status == "ok"
    assert list(out.payload) == ORDER
    for metric in ORDER:
        summary = await get_metric_summary(GetMetricSummary(metric=metric, period=period), request(p))
        # 개별 지표 요약의 추가 안내·운동 확장은 전체 요약에 넣지 않는다.
        expected = {
            k: v
            for k, v in summary.payload.items()
            if v is not None and k not in {"sleep_values_note", "exercise_activity_note", "exercise_activity"}
        }
        assert out.payload[metric] == expected, metric


async def test_overview_keeps_period_basis_per_metric_and_drops_oxygen():
    out = await get_health_overview(GetHealthOverview(period="last_7_days"), request(port()))
    bases = {m: out.payload[m]["period"]["basis"] for m in ORDER}
    assert bases == {
        "steps": "날짜(KST)",
        "exercise": "날짜(KST)",
        "sleep": "수면 종료일(KST)",
        "heart_rate": "날짜(KST)",
    }
    assert "oxygen_saturation" not in out.payload and "period" not in out.payload


async def test_overview_leaves_null_items_out():
    # 지난 30일은 연동 전 날짜가 있고, 수면·운동은 측정 최저·최고가, 수면은 오늘 진행 값이 비어 있다
    out = await get_health_overview(GetHealthOverview(period="last_30_days"), request(port()))
    assert nulls(out.payload) == []
    assert "measured_max" in out.payload["heart_rate"] and "measured_max" not in out.payload["sleep"]


async def test_overview_has_one_evidence_per_metric_like_summary():
    p = port()
    out = await get_health_overview(GetHealthOverview(period="last_7_days"), request(p))
    assert [e.tool for e in out.evidence] == ["get_health_overview"] * 4
    assert [e.metric for e in out.evidence] == ["steps", "exercises", "sleeps", "heart_rates"]
    for evidence, metric in zip(out.evidence, ORDER, strict=True):
        summary = await get_metric_summary(GetMetricSummary(metric=metric, period="last_7_days"), request(p))
        [expected] = summary.evidence
        assert (evidence.period, evidence.summary, evidence.snapshot) == (
            expected.period,
            expected.summary,
            expected.snapshot,
        )


async def test_gateway_numbers_four_evidence_and_counts_four_lookups():
    audit = MemoryAuditSink()
    gateway = ToolGateway(port(), TODAY, audit=audit)
    [reply] = await gateway.execute([ToolCall("c1", "get_health_overview", {"period": "last_7_days"})], CTX)
    assert json.loads(reply.content)["evidence_ids"] == ["E1", "E2", "E3", "E4"]
    [record] = audit.records()
    assert (record.result_count, record.evidence_ids) == (28, tuple(e.id for e in reply.evidence))


class SleepNotReady(FakeHealthData):
    async def get_metric_result(self, user_id, metric, start, end):
        if metric == "sleep":
            raise HealthDataNotReadyError(metrics=("sleep",))
        return await super().get_metric_result(user_id, metric, start, end)


async def test_one_failing_metric_fails_the_whole_overview():
    audit = MemoryAuditSink()
    gateway = ToolGateway(SleepNotReady(**vars(port())), TODAY, audit=audit)
    with pytest.raises(HealthDataNotReadyError) as e:
        await gateway.execute([ToolCall("c1", "get_health_overview", {"period": "last_7_days"})], CTX)
    assert e.value.metrics == ("sleep",)
    [record] = audit.records()
    assert (record.reason, record.evidence_ids, record.result_count) == ("data_not_ready", (), 0)


async def test_four_metrics_are_looked_up_concurrently():
    slow = SlowPort(port(), 0.2)
    gateway = ToolGateway(slow, TODAY, timeout=0.5)
    [reply] = await gateway.execute([ToolCall("c1", "get_health_overview", {"period": "this_week"})], CTX)
    assert slow.peak == 4 and len(reply.evidence) == 4


async def test_one_failing_metric_cancels_the_other_three_lookups():
    p = FailFastPort(port(), fail_metric="sleep")
    with pytest.raises(HealthDataNotReadyError) as e:
        await get_health_overview(GetHealthOverview(period="last_7_days"), request(p))
    assert e.value.metrics == ("sleep",)
    assert p.cancelled == 3
