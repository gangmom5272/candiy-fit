from copy import deepcopy
from datetime import timedelta, timezone

import pytest
from pydantic import ValidationError

from app.core.schemas import DataResponse
from app.health.daily_message import (
    MetricFailure,
    UsableSleepScore,
    build_daily_message,
)
from app.health.rest_schemas import DailyMessageResponse, daily_message_response
from tests.health.test_daily_message_rules import (
    AS_OF,
    SCORE_AT,
    message_input,
    success,
)


def payload_for(**kwargs):
    return daily_message_response(
        build_daily_message(message_input(**kwargs))
    ).model_dump(mode="json")


def test_balanced_response_exact_contract_and_utc():
    kst = timezone(timedelta(hours=9))
    facts = message_input(
        steps=success(8200, as_of=AS_OF.astimezone(kst)),
        exercises=success(35, as_of=AS_OF - timedelta(minutes=30)),
        sleeps=success(450, detail=True, as_of=AS_OF - timedelta(hours=8)),
        score=UsableSleepScore(value=90, computed_at=SCORE_AT.astimezone(kst)),
    )
    result = build_daily_message(facts)
    before = result.model_dump_json()
    response = daily_message_response(result)
    expected = {
        "date": "2026-10-29",
        "status": "READY",
        "code": "BALANCED_RECORDS",
        "area": None,
        "title": "오늘은 나에게 칭찬 한마디 건네볼까요?",
        "description": "수면과 활동 기록이 함께 쌓였어요. 오늘 남긴 작은 성취들을 돌아보세요.",
        "dataAsOf": {
            "steps": "2026-10-29T06:00:00Z",
            "exercises": "2026-10-29T05:30:00Z",
            "sleeps": "2026-10-28T22:00:00Z",
        },
        "scoreComputedAt": "2026-10-29T06:10:00Z",
        "notices": [],
    }
    assert DataResponse(data=response).model_dump(mode="json") == {"data": expected}
    assert (
        DailyMessageResponse.model_validate(expected).model_dump(mode="json")
        == expected
    )
    assert result.model_dump_json() == before
    assert set(result.model_dump()) - set(response.model_dump(by_alias=False)) == {
        "user_id",
        "used_metrics",
    }


def test_partial_response_exact_contract():
    assert payload_for(
        steps=success(8200),
        exercises=success(35),
        sleeps=MetricFailure(state="LOOKUP_FAILED", sync_failed=None),
    ) == {
        "date": "2026-10-29",
        "status": "PARTIAL",
        "code": "ACTIVE_DAY",
        "area": "activity",
        "title": "차곡차곡 쌓은 움직임, 뿌듯해해도 좋아요",
        "description": "운동과 걸음 기록에 오늘의 움직임이 담겼어요.",
        "dataAsOf": {
            "steps": "2026-10-29T06:00:00Z",
            "exercises": "2026-10-29T06:00:00Z",
            "sleeps": None,
        },
        "scoreComputedAt": None,
        "notices": [
            {
                "metric": "sleeps",
                "code": "LOOKUP_FAILED",
                "message": "수면 기록은 불러오지 못했어요.",
            }
        ],
    }


@pytest.mark.parametrize(
    "reason,status,code",
    [("NO_RECORD", "EMPTY", "NO_RECORDS"), ("UNSYNCED", "WAITING", "PENDING_RECORDS")],
)
def test_empty_and_waiting_keep_null_keys_and_empty_array(reason, status, code):
    payload = payload_for(
        steps=success(reason=reason, as_of=None),
        exercises=success(as_of=None),
        sleeps=success(as_of=None),
    )
    assert (payload["status"], payload["code"]) == (status, code)
    assert payload["area"] is None
    assert payload["scoreComputedAt"] is None
    assert payload["dataAsOf"] == {"steps": None, "exercises": None, "sleeps": None}
    assert payload["notices"] == []
    assert set(payload) == {
        "date",
        "status",
        "code",
        "area",
        "title",
        "description",
        "dataAsOf",
        "scoreComputedAt",
        "notices",
    }


@pytest.mark.parametrize(
    "path",
    [
        ("date",),
        ("status",),
        ("code",),
        ("area",),
        ("title",),
        ("description",),
        ("dataAsOf",),
        ("scoreComputedAt",),
        ("notices",),
        ("dataAsOf", "steps"),
        ("dataAsOf", "exercises"),
        ("dataAsOf", "sleeps"),
        ("notices", 0, "metric"),
        ("notices", 0, "code"),
        ("notices", 0, "message"),
    ],
)
def test_every_public_key_is_required_including_nullable_keys(path):
    payload = payload_for(steps=success(sync_failed=True))
    container = payload
    for key in path[:-1]:
        container = container[key]
    del container[path[-1]]
    with pytest.raises(ValidationError):
        DailyMessageResponse.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("date", "2026-02-30"),
        ("status", "UNKNOWN"),
        ("area", "heart"),
        ("code", None),
        ("title", None),
        ("description", None),
        ("notices", None),
        ("dataAsOf", None),
        ("scoreComputedAt", "2026-10-29T06:10:00"),
    ],
)
def test_response_rejects_invalid_public_values(field, value):
    payload = payload_for()
    payload[field] = value
    with pytest.raises(ValidationError):
        DailyMessageResponse.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [("metric", "heart_rates"), ("code", "INTERNAL_ERROR"), ("message", None)],
)
def test_notice_contract_rejects_other_metrics_and_error_codes(field, value):
    payload = payload_for(steps=success(sync_failed=True))
    payload["notices"][0][field] = value
    with pytest.raises(ValidationError):
        DailyMessageResponse.model_validate(payload)


@pytest.mark.parametrize("metric", ["steps", "exercises", "sleeps"])
def test_as_of_requires_timezone(metric):
    payload = payload_for()
    payload["dataAsOf"][metric] = "2026-10-29T06:00:00"
    with pytest.raises(ValidationError):
        DailyMessageResponse.model_validate(payload)


def test_public_code_remains_string_for_future_codes():
    payload = payload_for()
    changed = deepcopy(payload)
    changed["code"] = "FUTURE_CODE"
    assert DailyMessageResponse.model_validate(changed).code == "FUTURE_CODE"
