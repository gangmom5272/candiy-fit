from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.health import repository
from app.health.errors import HealthDataForbiddenError, InvalidHealthData
from app.health.repository import list_daily_records, list_exercise_sessions
from tests.health.test_calculations import NOW
from tests.health.test_exercise import DAY, exercise
from tests.health.test_schemas import USER, query


def session_with_rows(rows):
    result = Mock()
    result.mappings.return_value = rows
    return Mock(execute=AsyncMock(return_value=result))


def row(**changes):
    return (
        dict(
            end_user_id=USER,
            code_name="축구",
            code_category="cardio",
            **exercise().model_dump(),
        )
        | changes
    )


async def test_exercise_lookup_maps_known_and_unknown_codes_without_dropping_rows():
    session = session_with_rows(
        [
            row(),
            row(
                exercise_type=9999,
                code_name=None,
                code_category=None,
                title="합성 운동",
            ),
        ]
    )
    records = await list_exercise_sessions(session, USER, DAY, DAY)
    assert records[0] == exercise()
    assert records[1].exercise_type == 9999
    assert records[1].type_name == "기타 운동"
    assert records[1].category == "other"
    assert records[1].title == "합성 운동"
    assert session.execute.await_count == 1


async def test_exercise_lookup_rejects_wrong_owner():
    session = session_with_rows([row(end_user_id=uuid4())])
    with pytest.raises(HealthDataForbiddenError):
        await list_exercise_sessions(session, USER, DAY, DAY)


async def test_exercise_lookup_empty_is_distinct_from_database_failure():
    assert await list_exercise_sessions(session_with_rows([]), USER, DAY, DAY) == []
    session = Mock(
        execute=AsyncMock(side_effect=OperationalError(None, None, Exception()))
    )
    with pytest.raises(OperationalError):
        await list_exercise_sessions(session, USER, DAY, DAY)


async def test_exercise_lookup_selects_distance_and_preserves_null_zero_decimal():
    distances = [None, Decimal("0.0"), Decimal("1234.5")]
    session = session_with_rows([row(distance_m=value) for value in distances])
    records = await list_exercise_sessions(session, USER, DAY, DAY)
    assert [record.distance_m for record in records] == distances
    assert "distance_m" in session.execute.call_args.args[0].selected_columns


async def test_exercise_lookup_reads_decision_without_recomputing_from_speed():
    session = session_with_rows(
        [row(exercise_type=79, counts_as_exercise=False, avg_speed_mps=3)]
    )
    records = await list_exercise_sessions(session, USER, DAY, DAY)
    assert records[0].counts_as_exercise is False
    assert records[0].avg_speed_mps == 3
    columns = session.execute.call_args.args[0].selected_columns.keys()
    assert "counts_as_exercise" in columns
    assert "avg_speed_mps" in columns


@pytest.mark.parametrize("counted", [True, False])
@pytest.mark.parametrize("corruption", [None, "minutes", "count", "missing_daily"])
async def test_exercise_daily_checks_counted_aggregate_and_keeps_all_sessions(
    monkeypatch, counted, corruption
):
    sessions = [
        exercise(duration_minutes=20, counts_as_exercise=counted),
        exercise(duration_minutes=30, counts_as_exercise=counted),
        exercise(duration_minutes=25, counts_as_exercise=False),
        exercise(duration_minutes=20, counts_as_exercise=False),
    ]
    monkeypatch.setattr(
        repository, "list_exercise_sessions", AsyncMock(return_value=sessions)
    )
    daily = dict(
        end_user_id=USER,
        date=DAY,
        exercise_minutes=50 if counted else 0,
        exercise_count=2 if counted else 0,
        exercise_calories_kcal=Decimal("12.3"),
        exercise_distance_m=None,
        computed_at=NOW,
    )
    if corruption == "minutes":
        daily["exercise_minutes"] = 95
    elif corruption == "count":
        daily["exercise_count"] = 4
    db = session_with_rows([] if corruption == "missing_daily" else [daily])
    request = query(metric="exercises", start=DAY, end=DAY)
    if corruption:
        with pytest.raises(InvalidHealthData):
            await list_daily_records(db, request)
    else:
        records = await list_daily_records(db, request)
        assert records[0].value == (50 if counted else 0)
        assert records[0].sessions == tuple(sessions)
        assert records[0].activity["exercise_calories_kcal"] == Decimal("12.3")
        assert records[0].activity["exercise_distance_m"] is None
        assert "exercise_calories_kcal" in db.execute.call_args.args[0].selected_columns
