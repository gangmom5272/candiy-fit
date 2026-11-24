from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.health import repository
from app.health.daily_message_service import classify_sleep_score
from app.health.errors import HealthDataForbiddenError
from tests.health.test_score_contract import OWNER, TODAY, stored_score

NOW = stored_score()["computed_at"]


def score_row(**changes):
    return repository.SleepScoreRow(
        **(
            dict(
                end_user_id=OWNER,
                date=TODAY,
                sleep_score=80,
                sleep_minutes=450,
                computed_at=NOW,
            )
            | changes
        )
    )


def classify(row):
    return classify_sleep_score(row, user_id=OWNER, today=TODAY, sleep_minutes=450)


@pytest.mark.parametrize("score", [0, 79, 80, 100])
def test_score_uses_only_valid_matching_sleep_fields(score):
    result = classify(score_row(sleep_score=score))
    assert result.value == score
    assert result.computed_at == NOW


@pytest.mark.parametrize(
    "changes",
    [
        {"sleep_score": -1},
        {"sleep_score": 101},
        {"sleep_score": True},
        {"sleep_score": 80.0},
        {"sleep_score": "80"},
        {"sleep_minutes": -1},
        {"sleep_minutes": True},
        {"computed_at": None},
        {"computed_at": NOW.replace(tzinfo=None)},
        {"date": "2026-10-26"},
    ],
)
def test_bad_storage_fields_are_not_query_failures(changes):
    assert classify(score_row(**changes)).reason == "INVALID_VALUE"


def test_absence_null_and_mismatch_are_distinct():
    assert classify(None).reason == "NO_ROW"
    assert classify(score_row(sleep_score=None)).reason == "NULL_SCORE"
    assert classify(score_row(sleep_minutes=420)).reason == "MISMATCH"
    assert classify(score_row(date=TODAY - timedelta(days=1))).reason == "MISMATCH"


def test_nullable_stored_score_is_not_invalid_storage():
    assert (
        classify(score_row(sleep_score=None, sleep_minutes=None)).reason == "NULL_SCORE"
    )
    assert classify(score_row(sleep_score=80, sleep_minutes=None)).reason == "MISMATCH"


def test_owner_check_precedes_every_payload_problem():
    with pytest.raises(HealthDataForbiddenError):
        classify(score_row(end_user_id=uuid4(), sleep_score="bad", computed_at=None))


async def test_repository_selects_only_five_fields_and_does_not_own_transaction():
    row = score_row()
    result = Mock()
    result.mappings.return_value.one_or_none.return_value = row.__dict__
    session = Mock(execute=AsyncMock(return_value=result))
    assert await repository.find_sleep_score(session, OWNER, TODAY) == row
    query = session.execute.call_args.args[0]
    assert list(query.selected_columns.keys()) == [
        "end_user_id",
        "date",
        "sleep_score",
        "sleep_minutes",
        "computed_at",
    ]
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()
    result.mappings.return_value.one_or_none.return_value = None
    assert await repository.find_sleep_score(session, OWNER, TODAY) is None
    result.mappings.return_value.one_or_none.return_value = replace(
        row, end_user_id=uuid4()
    ).__dict__
    with pytest.raises(HealthDataForbiddenError):
        await repository.find_sleep_score(session, OWNER, TODAY)


async def test_repository_does_not_turn_query_error_into_absence():
    with pytest.raises(OSError):
        await repository.find_sleep_score(
            Mock(execute=AsyncMock(side_effect=OSError("synthetic"))), OWNER, TODAY
        )
