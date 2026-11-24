import json
import logging
from datetime import date

import pytest

from coach_ai.agent import evidence as evidence_module
from coach_ai.agent.context import CoachContext
from coach_ai.agent.evidence import (
    BASE_EVIDENCE,
    KIND_SNAPSHOT_MAX_BYTES,
    SNAPSHOT_TOO_LARGE,
    KindEvidenceDraft,
    emitted_evidence,
    is_evidence_enabled,
    with_detail,
)
from coach_ai.agent.executor import EvidenceDraft, ToolCall, ToolOutcome
from coach_ai.config import Settings
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import ToolGateway
from coach_ai.tools.outcomes import detail_failed
from coach_ai.tools.results import PeriodSpan
from tests.fakes import FakeHealthData

TODAY = date(2026, 10, 15)
CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
SPAN = PeriodSpan(period="yesterday", start=date(2026, 10, 14), end=date(2026, 10, 14), basis="수면 종료일(KST)")
SLEEP_RECORDS = ToolCall("c1", "get_sleep_records", {"period": "yesterday"})
METRIC_SNAPSHOT = {"metric": "sleeps", "period": {"start": "2026-10-14", "end": "2026-10-14"}}


def sessions_snapshot() -> dict:
    """포트가 넘기는 전체 수면 세션 snapshot (백엔드 결과 모델에서 user_id 를 뺀 모양). 값은 모두 합성이다."""
    return {
        "period": {"start": "2026-10-14", "end": "2026-10-14"},
        "sessions": [
            {
                "session_id": "s-001",
                "is_main": True,
                "date": "2026-10-14",
                "start_at": "2026-10-13T14:10:00Z",
                "end_at": "2026-10-13T21:40:00Z",
                "sleep_minutes": 420,
                "spo2_low_count": 2,
                "spo2_low_times": ["2026-10-13T17:02:00Z", "2026-10-13T17:02:00Z"],
            },
            {"session_id": "s-002", "is_main": False, "sleep_minutes": 35, "spo2_low_times": None},
        ],
        "data_status": {"last_sync_status": "success", "checked_at": "2026-10-14T01:10:00Z"},
    }


def base_draft(metric: str = "sleeps") -> EvidenceDraft:
    return EvidenceDraft("get_sleep_records", metric, SPAN, "어제 수면 7시간", {**METRIC_SNAPSHOT, "metric": metric})


def kind_draft(snapshot: dict | None = None, *, kind: str = "sleep_sessions", metric: str = "sleeps"):
    snapshot = sessions_snapshot() if snapshot is None else snapshot
    return KindEvidenceDraft("get_sleep_records", metric, kind, SPAN, "어제 수면 세션 2개", snapshot)


async def run_drafts(drafts, enabled):
    async def handler(inp, req):
        return ToolOutcome("ok", {"metric": "sleep"}, tuple(drafts))

    audit = MemoryAuditSink()
    gateway = ToolGateway(
        FakeHealthData(), TODAY, handlers={"get_sleep_records": handler}, audit=audit, evidence_enabled=enabled
    )
    reply = (await gateway.execute([SLEEP_RECORDS], CTX))[0]
    return reply, audit.records()[0]


def compact_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def test_enabled_list_is_empty_by_default(settings):
    assert settings.coach_evidence_enabled == ""
    assert settings.evidence_names == frozenset()


def test_enabled_list_reads_comma_separated_names(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("COACH_EVIDENCE_ENABLED", " sleep_sessions, weights ,,hourly_steps ")

    assert Settings(_env_file=None).evidence_names == {"sleep_sessions", "weights", "hourly_steps"}


def test_base_five_metrics_are_always_enabled_without_reading_settings(monkeypatch):
    def unreadable():
        raise AssertionError("기본 지표는 설정을 읽지 않는다")

    monkeypatch.setattr(evidence_module, "get_settings", unreadable)

    assert BASE_EVIDENCE == {"steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises"}
    assert all(is_evidence_enabled(name) for name in BASE_EVIDENCE)
    assert all(is_evidence_enabled(name, frozenset()) for name in BASE_EVIDENCE)


def test_new_metric_or_kind_is_enabled_only_when_listed():
    assert not is_evidence_enabled("sleep_sessions", frozenset())
    assert is_evidence_enabled("sleep_sessions", {"sleep_sessions"})
    assert not is_evidence_enabled("weights", {"sleep_sessions"})


def test_enabled_list_comes_from_settings_when_not_given(monkeypatch):
    configured = Settings(_env_file=None, LLM_API_KEY="k", COACH_EVIDENCE_ENABLED="weights")
    monkeypatch.setattr(evidence_module, "get_settings", lambda: configured)

    assert is_evidence_enabled("weights")
    assert not is_evidence_enabled("sleep_sessions")


def test_kind_draft_keeps_port_snapshot_as_it_is():
    snapshot = sessions_snapshot()
    draft = kind_draft(snapshot)

    assert draft.snapshot is snapshot
    assert snapshot == sessions_snapshot()
    assert draft.event_snapshot() == {"kind": "sleep_sessions", **sessions_snapshot()}
    assert next(iter(draft.event_snapshot())) == "kind"
    assert snapshot == sessions_snapshot()


def test_kind_draft_rejects_owner_and_other_kind_in_snapshot():
    with pytest.raises(ValueError):
        kind_draft({**sessions_snapshot(), "user_id": "u1"})
    with pytest.raises(ValueError):
        kind_draft({**sessions_snapshot(), "kind": "hourly_steps"})
    assert kind_draft({**sessions_snapshot(), "kind": "sleep_sessions"}).event_snapshot()["kind"] == "sleep_sessions"


def test_kind_draft_size_is_bytes_of_the_snapshot_to_emit():
    draft = kind_draft()
    assert draft.size == compact_bytes({"kind": "sleep_sessions", **sessions_snapshot()})


def test_with_detail_puts_base_first_and_shares_tool_metric_and_period():
    base = base_draft()
    snapshot = sessions_snapshot()

    first, detail = with_detail(base, "sleep_sessions", snapshot, summary="어제 수면 세션 2개")

    assert first is base
    assert isinstance(detail, KindEvidenceDraft)
    assert (detail.tool, detail.metric, detail.kind, detail.period, detail.summary) == (
        "get_sleep_records",
        "sleeps",
        "sleep_sessions",
        SPAN,
        "어제 수면 세션 2개",
    )
    assert detail.snapshot is snapshot


def test_with_detail_can_take_another_outer_metric():
    _, detail = with_detail(base_draft(), "hourly_steps", {"period": {}}, summary="시간대 걸음", metric="steps")
    assert detail.metric == "steps"


async def test_base_and_detail_make_two_evidence_ids_in_the_call_record():
    drafts = with_detail(base_draft(), "sleep_sessions", sessions_snapshot(), summary="어제 수면 세션 2개")

    reply, record = await run_drafts(drafts, {"sleep_sessions"})

    assert [e.position for e in reply.evidence] == [1, 2]
    assert len(set(record.evidence_ids)) == 2
    assert record.evidence_ids == tuple(e.id for e in reply.evidence)
    assert json.loads(reply.content)["evidence_ids"] == ["E1", "E2"]
    base, detail = (e.event_item() for e in reply.evidence)
    assert base["snapshot"] == METRIC_SNAPSHOT
    assert {k: detail[k] for k in ("tool", "metric", "period", "summary")} == {
        "tool": "get_sleep_records",
        "metric": "sleeps",
        "period": SPAN.model_dump(mode="json"),
        "summary": "어제 수면 세션 2개",
    }
    assert detail["snapshot"] == {"kind": "sleep_sessions", **sessions_snapshot()}


async def test_detail_failed_call_keeps_only_the_base_evidence():
    base = ToolOutcome("ok", {"metric": "sleep"}, (base_draft(),))

    async def handler(inp, req):
        return detail_failed(base)

    audit = MemoryAuditSink()
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_sleep_records": handler}, audit=audit)
    reply = (await gateway.execute([SLEEP_RECORDS], CTX))[0]
    record = audit.records()[0]

    assert [e.metric for e in reply.evidence] == ["sleeps"]
    assert record.evidence_ids == (reply.evidence[0].id,)
    assert record.result_status == "detail_failed"
    assert json.loads(reply.content)["evidence_ids"] == ["E1"]


async def test_metric_evidence_is_emitted_as_before_with_an_empty_list():
    draft = base_draft()

    reply, record = await run_drafts([draft], frozenset())

    assert len(reply.evidence) == 1
    assert reply.evidence[0].snapshot is draft.snapshot
    assert len(record.evidence_ids) == 1


async def test_kind_evidence_not_in_the_list_is_not_emitted():
    drafts = with_detail(base_draft(), "sleep_sessions", sessions_snapshot(), summary="어제 수면 세션 2개")

    reply, record = await run_drafts(drafts, frozenset())

    assert [e.metric for e in reply.evidence] == ["sleeps"]
    assert "kind" not in reply.evidence[0].snapshot
    assert len(record.evidence_ids) == 1
    assert json.loads(reply.content)["evidence_ids"] == ["E1"]


async def test_new_metric_evidence_needs_the_list():
    weights = base_draft("weights")

    reply, _ = await run_drafts([base_draft("steps"), weights], frozenset())
    assert [e.metric for e in reply.evidence] == ["steps"]

    reply, _ = await run_drafts([base_draft("steps"), weights], {"weights"})
    assert [e.metric for e in reply.evidence] == ["steps", "weights"]


async def test_kind_evidence_with_a_new_outer_metric_needs_both_names():
    draft = kind_draft({"period": {}}, kind="vo2_max_records", metric="vo2_maxes")

    reply, _ = await run_drafts([draft], {"vo2_max_records"})
    assert reply.evidence == ()

    reply, _ = await run_drafts([draft], {"vo2_max_records", "vo2_maxes"})
    assert [e.snapshot["kind"] for e in reply.evidence] == ["vo2_max_records"]


def padded(size: int) -> dict:
    """근거로 내보낼 snapshot 의 바이트 수가 size 가 되는 합성 snapshot."""
    empty = compact_bytes({"kind": "sleep_sessions", "pad": ""})
    return {"pad": "x" * (size - empty)}


async def test_kind_snapshot_at_the_limit_is_emitted():
    assert KIND_SNAPSHOT_MAX_BYTES == 256 * 1024
    draft = kind_draft(padded(KIND_SNAPSHOT_MAX_BYTES))
    assert draft.size == KIND_SNAPSHOT_MAX_BYTES

    reply, record = await run_drafts([draft], {"sleep_sessions"})

    assert len(reply.evidence) == len(record.evidence_ids) == 1


async def test_kind_snapshot_over_the_limit_is_dropped_and_only_the_code_is_logged(caplog):
    draft = kind_draft(padded(KIND_SNAPSHOT_MAX_BYTES + 1))

    with caplog.at_level(logging.WARNING, logger="coach_ai.agent.evidence"):
        reply, record = await run_drafts([base_draft(), draft], {"sleep_sessions"})

    assert [e.metric for e in reply.evidence] == ["sleeps"]
    assert len(record.evidence_ids) == 1
    assert json.loads(reply.content)["evidence_ids"] == ["E1"]
    assert [r.getMessage() for r in caplog.records] == [
        f"coaching evidence dropped code={SNAPSHOT_TOO_LARGE} request_id=r-1 tool=get_sleep_records kind=sleep_sessions"
    ]
    assert SNAPSHOT_TOO_LARGE == "snapshot_too_large"


def test_emitted_evidence_keeps_order_and_skips_only_what_is_not_emitted():
    first, detail = with_detail(base_draft(), "sleep_sessions", sessions_snapshot(), summary="s")
    weights = base_draft("weights")

    assert emitted_evidence([first, weights, detail], request_id="r-1", enabled={"sleep_sessions"}) == [first, detail]
