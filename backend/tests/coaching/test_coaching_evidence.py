"""합성 근거로 AI 입력 검증과 저장 원형의 공개 직렬화를 검사한다."""

import traceback
from copy import deepcopy
from dataclasses import fields, replace
from datetime import date
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.coaching import repository
from app.coaching.repository import ConversationRecord, HistoryMessage, MessagePage
from app.coaching.schemas import (
    EvidenceFormatError,
    EvidencePartition,
    RequestIds,
    _evidence_item,
    completed_event,
    evidence_data,
    evidence_event,
    message_page_data,
    partition_evidence,
    request_snapshot_event,
    request_state_data,
)
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall
from coach_ai.security.gateway import ToolGateway
from coach_ai.tools.periods import local_today
from tests.coaching.fakes import (
    NOW,
    SYNTHETIC_SNAPSHOT,
    synthetic_evidence,
    synthetic_evidence_info,
)
from tests.coaching.test_request_status_api import make_state
from tests.health.test_rest_response import result_for
from tests.health.test_schemas import USER

SAMPLE_EVIDENCE_CALLS = (
    ToolCall("call_sleep", "get_sleep_records", {"period": "last_7_days"}),
    ToolCall(
        "call_1", "get_metric_summary", {"metric": "exercise", "period": "last_7_days"}
    ),
    ToolCall(
        "call_2", "get_daily_trend", {"metric": "exercise", "period": "last_14_days"}
    ),
    ToolCall(
        "call_3", "get_metric_summary", {"metric": "steps", "period": "last_7_days"}
    ),
)


@pytest.mark.parametrize("container", [list, tuple])
def test_partition_evidence_valid_pair_preserves_fields(container) -> None:
    """정상 근거 두 개 → 입력 순서와 원형을 유지하고 공개 필드만 남긴다."""
    items = container(
        [synthetic_evidence(2).event_item(), synthetic_evidence().event_item()]
    )
    partition = partition_evidence(items)
    assert partition.skipped_metrics == ()
    result = partition.valid
    assert len(result) == 2
    assert [item.position for item in result] == [2, 1]
    assert [item.id for item in result] == [
        UUID("77777777-7777-4777-8777-777777777777"),
        UUID("66666666-6666-4666-8666-666666666666"),
    ]
    for item in result:
        assert item.metric == "steps"
        assert item.period_start == date(2026, 10, 12)
        assert item.period_end == date(2026, 10, 18)
        assert item.snapshot == SYNTHETIC_SNAPSHOT
        assert [field.name for field in fields(item)] == [
            "id",
            "position",
            "metric",
            "period_start",
            "period_end",
            "snapshot",
        ]


@pytest.mark.parametrize("items", [[], ()])
def test_partition_evidence_empty_valid_is_empty_tuple(items) -> None:
    """빈 근거 목록 → 맞는 근거도 버린 지표도 빈 튜플이다."""
    partition = partition_evidence(items)
    assert partition.valid == ()
    assert partition.skipped_metrics == ()


@pytest.mark.parametrize("distance", [None, 0, 1234.5])
def test_exercise_evidence_keeps_distance_in_snake_case(distance):
    """운동 근거의 distance_m(None·0·소수) → 저장 snapshot에 snake_case 그대로 남는다."""
    snapshot = result_for("exercises").to_public().model_dump(mode="json")
    snapshot["detail"]["sessions"][0]["distance_m"] = distance
    item = synthetic_evidence().event_item()
    item.update(metric="exercises", period=snapshot["period"], snapshot=snapshot)
    stored = partition_evidence([item]).valid
    assert stored[0].snapshot == snapshot
    assert evidence_data(stored)[0].model_dump(mode="json")["snapshot"] == snapshot


def test_stored_exercise_evidence_without_distance_is_returned_without_backfill():
    snapshot = result_for("exercises").to_public().model_dump(mode="json")
    del snapshot["detail"]["sessions"][0]["distance_m"]
    item = replace(synthetic_evidence_info(), metric="exercises", snapshot=snapshot)
    state = replace(make_state(), evidence=(item,))
    payload = request_state_data(state).model_dump(mode="json")
    assert payload["evidence"][0]["snapshot"] == snapshot
    assert "distance_m" not in snapshot["detail"]["sessions"][0]


async def test_partition_evidence_sample_exercise_and_steps_evidence_is_accepted():
    """local·test 합성 포트의 운동·걸음 근거 → 최신 원형 검증을 통과하고 원형을 유지한다.

    공통 원형과 메모리 어댑터 원형이 다르면 local·test 코칭의 근거 저장이 실패한다.
    """
    gateway = ToolGateway(sample_health_data(str(USER), NOW), local_today(NOW))
    ctx = CoachContext(user_id=str(USER), conversation_id="c-1", request_id="r-1")
    replies = await gateway.execute(SAMPLE_EVIDENCE_CALLS, ctx)
    items = [evidence.event_item() for reply in replies for evidence in reply.evidence]
    partition = partition_evidence(items)
    assert partition.skipped_metrics == ()
    stored = partition.valid
    assert [item.metric for item in stored] == [
        "sleeps",
        "exercises",
        "exercises",
        "steps",
    ]
    assert [item.snapshot for item in stored] == [item["snapshot"] for item in items]
    distances = [
        session["distance_m"]
        for item in items
        for session in item["snapshot"]["detail"]["sessions"]
    ]
    # 거리 기록 없음·0·소수 값이 모두 검증 대상에 들어 있다
    assert None in distances and 0 in distances
    assert any(value not in (None, 0) for value in distances)


async def test_partition_evidence_sample_exercise_sessions_evidence_is_accepted():
    """운동 세션 조회 근거 → 운동 원형 하나가 최신 원형 검증을 통과하고 그대로 저장된다."""
    gateway = ToolGateway(sample_health_data(str(USER), NOW), local_today(NOW))
    ctx = CoachContext(user_id=str(USER), conversation_id="c-1", request_id="r-1")
    call = ToolCall("call_1", "get_exercise_sessions", {"period": "last_14_days"})
    [reply] = await gateway.execute([call], ctx)
    items = [evidence.event_item() for evidence in reply.evidence]
    partition = partition_evidence(items)
    assert partition.skipped_metrics == ()
    stored = partition.valid
    assert [
        (item["tool"], stored_item.metric)
        for item, stored_item in zip(items, stored, strict=True)
    ] == [("get_exercise_sessions", "exercises")]
    assert stored[0].snapshot == items[0]["snapshot"]
    assert len(items[0]["snapshot"]["detail"]["sessions"]) > 1


@pytest.mark.parametrize(
    "case",
    [
        "none",
        "item",
        "missing_id",
        "id",
        "id_type",
        "missing_position",
        "position_zero",
        "position_bool",
        "position_string",
        "missing_metric",
        "metric",
        "metric_type",
        "period",
        "missing_start",
        "missing_end",
        "start_format",
        "end_format",
        "date_type",
        "invalid_date",
        "reversed_period",
        "snapshot_missing",
        "snapshot_extra",
        "snapshot_user",
        "snapshot_type",
        "snapshot_nested_extra",
        "snapshot_value_type",
    ],
)
def test_evidence_item_invalid_format_raises_safe_error(case: str) -> None:
    """근거 항목 형식 위반 → 입력값이나 원인 traceback 없이 고정 오류를 낸다."""
    items = _invalid_items(case)
    target = items[0] if isinstance(items, list) else items
    with pytest.raises(EvidenceFormatError) as caught:
        _evidence_item(target)
    error = caught.value
    assert str(error) == "Evidence format mismatch"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(error))
    assert "synthetic-private-input" not in rendered
    assert "synthetic-private-summary" not in rendered
    assert "88888888-8888-4888-8888-888888888888" not in rendered


@pytest.mark.parametrize(
    "case",
    [
        "none",
        "item",
        "missing_id",
        "id",
        "id_type",
        "missing_position",
        "position_zero",
        "position_bool",
        "position_string",
        "missing_metric",
        "metric",
        "metric_type",
        "period",
        "missing_start",
        "missing_end",
        "start_format",
        "end_format",
        "date_type",
        "invalid_date",
        "reversed_period",
        "snapshot_missing",
        "snapshot_extra",
        "snapshot_user",
        "snapshot_type",
        "snapshot_nested_extra",
        "snapshot_value_type",
    ],
)
def test_partition_evidence_invalid_item_is_skipped_and_good_neighbors_kept(
    case: str,
) -> None:
    """틀린 항목 하나 → 그 항목만 빠지고 앞뒤 정상 항목은 순서대로 남는다."""
    if case in {"none", "item"}:
        bad: Any = None if case == "none" else "synthetic-private-input"
    else:
        bad = _invalid_items(case)[0]
    first = synthetic_evidence().event_item()
    last = synthetic_evidence(2).event_item()
    result = partition_evidence([first, bad, last])
    assert result.skipped == 1
    assert len(result.skipped_metrics) == 1
    assert [item.position for item in result.valid] == [1, 2]
    assert partition_evidence([bad]).valid == ()
    assert partition_evidence([bad]).skipped == 1


@pytest.mark.parametrize("items", [None, {}, "synthetic", 1])
def test_partition_evidence_non_sequence_counts_one_skipped(items) -> None:
    """list·tuple이 아닌 입력 → 맞는 근거 없이 unknown 1건을 버린 것으로 센다."""
    partition = partition_evidence(items)
    assert partition == EvidencePartition((), ("unknown",))
    assert partition.skipped_metrics == ("unknown",)
    assert partition.skipped == 1


def test_partition_evidence_container_mapping_is_unknown_skipped() -> None:
    """근거 전체가 목록이 아닌 dict → 맞는 근거 없이 unknown 1건을 버린다."""
    partition = partition_evidence(_invalid_items("container"))
    assert partition == EvidencePartition((), ("unknown",))


def test_partition_evidence_skipped_metrics_names_public_metric_or_unknown() -> None:
    """정상 sleeps, 틀린 steps, 목록에 없는 metric, dict 아닌 항목 → steps, unknown, unknown 순서로 남는다."""
    good = synthetic_evidence().event_item()
    good["metric"] = "sleeps"
    bad_steps = synthetic_evidence(2).event_item()
    bad_steps["position"] = 0
    bad_metric = synthetic_evidence(
        3, id="55555555-5555-4555-8555-555555555555"
    ).event_item()
    bad_metric["metric"] = "blood_pressure"
    partition = partition_evidence([good, bad_steps, bad_metric, "not-a-dict"])
    assert partition.skipped_metrics == ("steps", "unknown", "unknown")
    assert partition.skipped == 3
    assert [item.metric for item in partition.valid] == ["sleeps"]


def test_partition_evidence_arbitrary_metric_string_is_not_exposed() -> None:
    """임의 문자열 metric이 든 틀린 항목 → skipped_metrics에 그 문자열 없이 unknown만 남는다."""
    bad = synthetic_evidence().event_item()
    bad["metric"] = "합성 임의 문자열"
    partition = partition_evidence([bad])
    assert partition.skipped_metrics == ("unknown",)
    assert "합성 임의 문자열" not in repr(partition)


@pytest.mark.parametrize("items", [[], ()])
def test_partition_evidence_empty_returns_empty_without_skipped(items) -> None:
    """빈 목록 → (빈 튜플, 빈 튜플)이고 버린 수는 0이다."""
    partition = partition_evidence(items)
    assert partition == EvidencePartition((), ())
    assert partition.skipped == 0


def test_partition_evidence_all_valid_has_no_skipped_metrics() -> None:
    """모두 정상인 입력 → 입력 순서의 근거 둘이 남고 버린 지표가 없다."""
    items = [synthetic_evidence().event_item(), synthetic_evidence(2).event_item()]
    result = partition_evidence(items)
    assert [item.position for item in result.valid] == [1, 2]
    assert result.skipped_metrics == ()
    assert result.skipped == 0


def _invalid_items(case: str) -> Any:
    """사례 이름에 맞게 형식이 틀린 근거 입력(목록 또는 목록 아닌 값)을 만든다."""
    item = synthetic_evidence().event_item()
    item["summary"] = "synthetic-private-summary"
    items: Any = [item]
    if case == "container":
        items = {"synthetic-private-input": item}
    elif case == "none":
        items = None
    elif case == "item":
        items = ["synthetic-private-input"]
    elif case.startswith("missing_"):
        key = case.removeprefix("missing_")
        del (item["period"] if key in {"start", "end"} else item)[key]
    elif case == "id":
        item["id"] = "synthetic-private-input"
    elif case == "id_type":
        item["id"] = UUID(item["id"])
    elif case.startswith("position_"):
        item["position"] = {
            "position_zero": 0,
            "position_bool": True,
            "position_string": "1",
        }[case]
    elif case in {"metric", "metric_type"}:
        item["metric"] = "synthetic-private-input" if case == "metric" else []
    elif case == "period":
        item["period"] = None
    elif case in {"start_format", "end_format"}:
        item["period"][case.removesuffix("_format")] = "20261012"
    elif case == "date_type":
        item["period"]["start"] = date(2026, 10, 12)
    elif case == "invalid_date":
        item["period"]["start"] = "2026-02-30"
    elif case == "reversed_period":
        item["period"]["start"] = "2026-10-19"
    elif case == "snapshot_missing":
        del item["snapshot"]["detail"]
    elif case in {"snapshot_extra", "snapshot_user"}:
        item["snapshot"]["user_id" if case == "snapshot_user" else "extra"] = (
            "88888888-8888-4888-8888-888888888888"
            if case == "snapshot_user"
            else "synthetic-private-input"
        )
    elif case == "snapshot_type":
        item["snapshot"] = []
    elif case == "snapshot_nested_extra":
        item["snapshot"]["summary"]["extra"] = "synthetic-private-input"
    elif case == "snapshot_value_type":
        item["snapshot"]["summary"]["avg"] = {"synthetic-private-input": 1}
    return items


def test_partition_evidence_gateway_consistency_is_not_rechecked() -> None:
    """중복 ID·순번 및 snapshot과 다른 지표·기간 → 형식이 맞으면 그대로 변환한다."""
    item = synthetic_evidence().event_item()
    item.update(position=7, metric="exercises")
    item["period"]["start"] = "2026-10-11"
    partition = partition_evidence([item, item])
    assert partition.skipped_metrics == ()
    result = partition.valid
    assert [entry.position for entry in result] == [7, 7]
    assert result[0].id == result[1].id
    assert result[0].metric == "exercises"
    assert result[0].period_start == date(2026, 10, 11)
    assert result[0].snapshot == SYNTHETIC_SNAPSHOT


def test_evidence_serialization_all_surfaces_match_literal_json() -> None:
    """근거가 있는 완료 상태 → WS·요청·메시지 전체 JSON이 명세와 일치한다."""
    items = (synthetic_evidence_info(), synthetic_evidence_info(2))
    state = replace(make_state(), evidence=items)
    expected_items = [
        {
            "id": "66666666-6666-4666-8666-666666666666",
            "position": 1,
            "metric": "steps",
            "periodStart": "2026-10-12",
            "periodEnd": "2026-10-18",
            "snapshot": deepcopy(SYNTHETIC_SNAPSHOT),
        },
        {
            "id": "77777777-7777-4777-8777-777777777777",
            "position": 2,
            "metric": "steps",
            "periodStart": "2026-10-12",
            "periodEnd": "2026-10-18",
            "snapshot": deepcopy(SYNTHETIC_SNAPSHOT),
        },
    ]
    envelope = {
        "clientRequestId": "11111111-1111-4111-8111-111111111111",
        "requestId": "33333333-3333-4333-8333-333333333333",
        "conversationId": "22222222-2222-4222-8222-222222222222",
    }
    assistant = {
        "id": "55555555-5555-4555-8555-555555555555",
        "role": "assistant",
        "sequenceNo": 2,
        "content": "합성 완료 답변",
        "createdAt": "2026-10-19T01:00:00Z",
    }
    expected_state = {
        **envelope,
        "status": "completed",
        "userMessageId": "44444444-4444-4444-8444-444444444444",
        "assistantMessage": assistant,
        "evidence": expected_items,
        "errorCode": None,
        "createdAt": "2026-10-19T01:00:00Z",
        "finishedAt": "2026-10-19T01:00:02Z",
        "errorContext": None,
    }
    ids = RequestIds.from_state(state)
    assert evidence_event(ids, 3, items) == {
        **envelope,
        "type": "evidence",
        "eventSeq": 3,
        "data": {"items": expected_items},
    }
    assert completed_event(
        ids,
        4,
        assistant_message=state.assistant_message,
        answer="합성 완료 답변",
        evidence=items,
        finished_at=state.finished_at,
    ) == {
        **envelope,
        "type": "completed",
        "eventSeq": 4,
        "data": {
            "status": "completed",
            "assistantMessage": assistant,
            "evidence": expected_items,
            "finishedAt": "2026-10-19T01:00:02Z",
        },
    }
    assert request_state_data(state).model_dump(mode="json") == expected_state
    assert request_snapshot_event(state) == {
        **envelope,
        "type": "request_snapshot",
        "eventSeq": 0,
        "data": expected_state,
    }
    page = MessagePage(
        ConversationRecord(
            state.conversation_id,
            "합성 대화",
            None,
            state.created_at,
            state.finished_at,
        ),
        (
            HistoryMessage(state.user_message, state.id, "completed", None, None),
            HistoryMessage(
                state.assistant_message, state.id, "completed", None, None, items
            ),
        ),
        False,
    )
    assert message_page_data(page).model_dump(mode="json") == {
        "conversation": {
            "id": "22222222-2222-4222-8222-222222222222",
            "title": "합성 대화",
            "summary": None,
            "createdAt": "2026-10-19T01:00:00Z",
            "updatedAt": "2026-10-19T01:00:02Z",
        },
        "items": [
            {
                "id": "44444444-4444-4444-8444-444444444444",
                "requestId": "33333333-3333-4333-8333-333333333333",
                "role": "user",
                "sequenceNo": 1,
                "content": "합성 코칭 질문",
                "requestStatus": "completed",
                "errorCode": None,
                "createdAt": "2026-10-19T01:00:00Z",
                "evidence": [],
                "errorContext": None,
            },
            {
                **assistant,
                "requestId": "33333333-3333-4333-8333-333333333333",
                "requestStatus": "completed",
                "errorCode": None,
                "evidence": expected_items,
                "errorContext": None,
            },
        ],
        "nextBeforeSequenceNo": None,
    }


def test_evidence_data_legacy_snapshot_preserves_unknown_and_missing_fields() -> None:
    """현재 원형과 다른 저장 snapshot → 재검증 없이 그대로 반환하고 외곽 사본을 만든다."""
    snapshot = deepcopy(SYNTHETIC_SNAPSHOT)
    del snapshot["detail"]
    snapshot["legacy_field"] = {"snake_case": "합성 옛 값"}
    item = replace(synthetic_evidence_info(), snapshot=snapshot)
    data = evidence_data((item,))[0]
    assert data.snapshot == snapshot
    assert data.snapshot is not snapshot
    state = replace(make_state(), evidence=(item,))
    assert (
        request_state_data(state).model_dump(mode="json")["evidence"][0]["snapshot"]
        == snapshot
    )


async def test_list_evidence_empty_message_ids_skips_query() -> None:
    """근거 조회할 메시지 ID가 없음 → 세션 호출 없이 빈 매핑을 반환한다."""
    session = AsyncMock(spec=AsyncSession)
    assert await repository._list_evidence(session, []) == {}
    assert session.mock_calls == []


@pytest.mark.parametrize(
    "metric", ["steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises"]
)
def test_partition_evidence_public_metric_accepts_all_contract_values(metric) -> None:
    """공개 지표 다섯 종류 → snapshot과의 일치 여부를 다시 판단하지 않고 허용한다."""
    item = synthetic_evidence().event_item()
    item["metric"] = metric
    assert partition_evidence([item]).valid[0].metric == metric


@pytest.mark.parametrize("hours,worn", [(None, None), (9, False), (10, True)])
def test_new_wear_fields_survive_evidence_validation(hours, worn):
    item = synthetic_evidence().event_item()
    item["snapshot"]["days"][0].update(wear_hours=hours, is_well_worn=worn)
    result = partition_evidence([item])
    assert result.skipped_metrics == ()
    day = result.valid[0].snapshot["days"][0]
    assert day["wear_hours"] == hours
    assert day["is_well_worn"] is worn
