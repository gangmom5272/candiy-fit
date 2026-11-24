import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.coaching.schemas import EvidenceFormatError, _evidence_item, partition_evidence
from app.health import errors, repository, schemas, service
from app.health.lookup import lookup_boundary
from app.health.rest_schemas import metric_response
from tests.health.test_calculations import NOW, calculate
from tests.health.test_exercise import exercise
from tests.health.test_schemas import USER
from tests.health.test_service import reads as reads
from tests.health.test_service import state


def exercise_window():
    return schemas.ExerciseSampleWindow(
        record_id="exercise",
        start_at=NOW - timedelta(hours=1),
        end_at=NOW,
        computed_at=NOW - timedelta(days=1),
    )


@pytest.mark.parametrize("metric", ["heart_rates", "speeds"])
@pytest.mark.parametrize(
    "timestamp,valid",
    [
        ("0001-01-01T00:00:00+09:00", False),
        ("9999-12-31T23:59:59-09:00", False),
        (NOW.replace(tzinfo=None).isoformat(), False),
        (NOW.astimezone(schemas.KST).isoformat(), True),
    ],
)
async def test_raw_sample_utc_conversion_classifies_overflow(metric, timestamp, valid):
    window = exercise_window()
    key = "beatsPerMinute" if metric == "heart_rates" else "speed_mps"
    header = dict(
        end_user_id=USER,
        record_id="raw",
        start_at=window.start_at,
        end_at=window.end_at,
        source_app="synthetic.app",
        last_modified_at=NOW,
        ingested_at=NOW,
        sample_count=1,
        sample_bytes=100,
    )
    first, second = Mock(), Mock()
    first.mappings.return_value = [header]
    second.mappings.return_value = [
        dict(
            end_user_id=USER,
            data_id="raw",
            sample_array=json.dumps([{"time": timestamp, key: 120}]),
        )
    ]
    db = Mock(execute=AsyncMock(side_effect=[first, second]))
    query = schemas.ExerciseSamplesQuery(
        user_id=USER, record_id="exercise", metric=metric
    )
    if valid:
        async with lookup_boundary(()):
            records = await repository.list_exercise_samples(db, query, window)
        assert len(records) == 1
        assert records[0].measured_at.isoformat() == NOW.isoformat()
        assert records[0].value == 120
    else:
        with pytest.raises(errors.HealthDataLookupError) as caught:
            async with lookup_boundary(()):
                await repository.list_exercise_samples(db, query, window)
        assert isinstance(caught.value.__cause__, errors.InvalidHealthData)


@pytest.fixture
def sample_reads(reads, monkeypatch):
    factory, states, _, lifecycle = reads
    states.return_value = [
        state(metric="exercise_sessions"),
        state(metric="heart_rates"),
    ]
    window = AsyncMock(side_effect=lambda *args: exercise_window())
    samples = AsyncMock(return_value=[])
    monkeypatch.setattr(
        service.repository, "find_exercise_sample_window", window, raising=False
    )
    monkeypatch.setattr(
        service.repository, "list_exercise_samples", samples, raising=False
    )
    return factory, states, window, samples, lifecycle


async def call(factory, **changes):
    return await service.get_exercise_samples(
        **(dict(user_id=USER, record_id="exercise", metric="heart_rates") | changes),
        session_factory=factory,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": "bad"},
        {"record_id": ""},
        {"record_id": "x" * 65},
        {"record_id": None},
        {"metric": "steps"},
    ],
)
async def test_invalid_input_never_opens_database(sample_reads, changes):
    factory, states, _, _, lifecycle = sample_reads
    with pytest.raises(ValidationError):
        await call(factory, **changes)
    states.assert_not_awaited()
    assert lifecycle == []


async def test_unready_other_metric_does_not_block_empty_result(sample_reads):
    factory, states, _, _, lifecycle = sample_reads
    states.return_value += [state(metric="speeds", initial_sync_done=False)]
    result = await call(factory)
    assert result.samples == [] and result.unit == "bpm"
    assert result.exercise.computed_at < result.source_status.last_success_at
    assert result.checked_at == NOW
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_speed_does_not_require_heart_state(sample_reads):
    factory, states, _, _, _ = sample_reads
    states.return_value = [state(metric="exercise_sessions"), state(metric="speeds")]
    result = await call(factory, metric="speeds")
    assert result.unit == "m/s" and result.samples == []


@pytest.mark.parametrize(
    "metric,change,expected",
    [
        ("exercise_sessions", {"initial_sync_done": False}, "HealthDataNotReadyError"),
        ("heart_rates", {"initial_sync_done": False}, "HealthDataNotReadyError"),
        ("heart_rates", {"last_success_at": None}, "HealthDataLookupError"),
        ("heart_rates", {"end_user_id": uuid4()}, "HealthDataForbiddenError"),
    ],
)
async def test_required_states(sample_reads, metric, change, expected):
    factory, states, _, samples, lifecycle = sample_reads
    states.return_value = [
        state(metric=m, **(change if m == metric else {}))
        for m in ("exercise_sessions", "heart_rates")
    ]
    with pytest.raises(getattr(errors, expected)):
        await call(factory)
    samples.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("states", [[], [state(metric="exercise_sessions")]])
async def test_missing_state_is_failure(sample_reads, states):
    factory, source, _, _, _ = sample_reads
    source.return_value = states
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory)


async def test_all_404_precedes_lookup(sample_reads):
    factory, states, window, _, _ = sample_reads
    states.return_value = [state(metric="heart_rates", last_error="http_404")]
    with pytest.raises(errors.HealthDataNotLinkedError):
        await call(factory)
    window.assert_not_awaited()


@pytest.mark.parametrize("kind", ["missing", "limit", "invalid", "io", "bug", "cancel"])
async def test_errors_and_cleanup(sample_reads, kind):
    factory, _, window, samples, lifecycle = sample_reads
    if kind == "missing":
        window.side_effect = None
        window.return_value = None
        expected = errors.ExerciseRecordNotFoundError
    else:
        failure = {
            "limit": errors.ExerciseSampleLimitError(),
            "invalid": errors.InvalidHealthData("synthetic"),
            "io": OSError("synthetic"),
            "bug": RuntimeError("synthetic"),
            "cancel": asyncio.CancelledError(),
        }[kind]
        samples.side_effect = failure
        expected = (
            type(failure)
            if kind in ("limit", "bug", "cancel")
            else errors.HealthDataLookupError
        )
    with pytest.raises(expected):
        await call(factory)
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_timeout_cancels_and_cleans_up(sample_reads, monkeypatch):
    factory, _, _, samples, lifecycle = sample_reads
    cancelled = []

    async def slow(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    samples.side_effect = slow
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory)
    assert cancelled == [True]
    assert lifecycle == ["transaction_closed", "session_closed"]


def snapshot():
    from app.health.calculations import exercise_records

    session = exercise(record_id="exercise")
    return calculate(exercise_records([session]), metric="exercises").to_public()


def evidence(payload):
    return dict(
        id=str(uuid4()),
        position=1,
        metric="exercises",
        period=payload["period"],
        snapshot=payload,
    )


def test_new_list_and_rest_require_record_id():
    """새 운동 조회·REST 응답은 record_id를 제공하고 키 누락을 거부한다."""
    result = snapshot()
    assert (
        metric_response(result).model_dump(mode="json")["detail"]["sessions"][0][
            "recordId"
        ]
        == "exercise"
    )
    payload = result.detail.sessions[0].model_dump()
    del payload["record_id"]
    with pytest.raises(ValidationError):
        schemas.ExerciseSessionDetail.model_validate(payload)


def test_evidence_accepts_legacy_missing_id_without_inventing_it():
    """식별자 키가 없는 도구 결과를 허용하고 원형에 키를 만들어 넣지 않는다."""
    payload = snapshot().model_dump(mode="json")
    del payload["detail"]["sessions"][0]["record_id"]
    result = partition_evidence([evidence(payload)])
    assert result.skipped_metrics == ()
    assert result.valid[0].snapshot == payload


@pytest.mark.parametrize("bad_id", [None, "", 4, "x" * 65])
def test_evidence_does_not_relax_present_invalid_id(bad_id):
    """도구 결과에 식별자가 명시되면 null·빈 값 등 잘못된 값을 거부한다."""
    payload = snapshot().model_dump(mode="json")
    payload["detail"]["sessions"][0]["record_id"] = bad_id
    with pytest.raises(EvidenceFormatError):
        _evidence_item(evidence(payload))


def test_evidence_keeps_new_id_and_validates_other_legacy_fields():
    """유효한 식별자를 보존하며 식별자 키 부재가 다른 필드 검증을 완화하지 않는다."""
    payload = snapshot().model_dump(mode="json")
    assert _evidence_item(evidence(payload)).snapshot == payload
    del payload["detail"]["sessions"][0]["record_id"]
    del payload["detail"]["sessions"][0]["counts_as_exercise"]
    with pytest.raises(EvidenceFormatError):
        _evidence_item(evidence(payload))


def test_partition_keeps_new_and_legacy_sessions_while_skipping_invalid_id():
    """혼합된 AI 결과에서 잘못된 식별자 항목만 제외하고 나머지 원형을 보존한다."""
    current = snapshot().model_dump(mode="json")
    legacy = snapshot().model_dump(mode="json")
    del legacy["detail"]["sessions"][0]["record_id"]
    invalid = snapshot().model_dump(mode="json")
    invalid["detail"]["sessions"][0]["record_id"] = None

    result = partition_evidence(
        [evidence(current), evidence(invalid), evidence(legacy)]
    )

    assert result.skipped_metrics == ("exercises",)
    assert [item.snapshot for item in result.valid] == [current, legacy]
