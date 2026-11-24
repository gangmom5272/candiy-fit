from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.health import repository, service
from app.health.errors import HealthDataLookupError
from app.health.schemas import ExerciseTypeItem
from tests.health import test_service

reads = test_service.reads


def item(**changes):
    return ExerciseTypeItem(
        **(dict(code=0, name_ko="기타 운동", category="other") | changes)
    )


def test_catalog_item_serializes_only_the_shared_fields():
    assert item().model_dump(mode="json") == {
        "code": 0,
        "name_ko": "기타 운동",
        "category": "other",
    }


@pytest.mark.parametrize("code", [True, "70", 70.0])
def test_catalog_code_does_not_coerce_other_types(code):
    with pytest.raises(ValidationError):
        item(code=code)


def test_catalog_item_rejects_unknown_category_and_missing_name():
    with pytest.raises(ValidationError):
        item(category="unknown")
    with pytest.raises(ValidationError):
        ExerciseTypeItem(code=0, category="other")


@pytest.mark.parametrize(
    "rows",
    [[], [dict(code=9999, name_ko="합성 신규 운동", category="strength")]],
)
async def test_repository_maps_registered_rows_without_inventing_codes(rows):
    result = Mock()
    result.mappings.return_value = rows
    db = Mock(execute=AsyncMock(return_value=result))
    actual = await repository.list_exercise_types(db)
    assert [entry.model_dump() for entry in actual] == rows
    assert db.execute.await_count == 1


@pytest.mark.parametrize("entries", [[], [item()]])
async def test_service_reads_catalog_without_member_data(reads, monkeypatch, entries):
    factory, states, daily, lifecycle = reads
    lookup = AsyncMock(return_value=entries)
    monkeypatch.setattr(repository, "list_exercise_types", lookup)
    assert await service.list_exercise_types(session_factory=factory) == entries
    assert lookup.await_count == 1
    db = lookup.await_args.args[0]
    assert "READ ONLY" in str(db.execute.await_args.args[0])
    states.assert_not_awaited()
    daily.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("failure", ["database", "invalid_row"])
async def test_service_catalog_failure_is_not_an_empty_list(
    reads, monkeypatch, failure
):
    factory, _, _, lifecycle = reads

    async def lookup(db):
        if failure == "database":
            raise OperationalError(None, None, Exception("synthetic failure"))
        return [item(category="invalid")]

    monkeypatch.setattr(repository, "list_exercise_types", lookup)
    with pytest.raises(HealthDataLookupError) as caught:
        await service.list_exercise_types(session_factory=factory)
    assert caught.value.metrics == ("exercises",)
    assert str(caught.value) == "HEALTH_DATA_UNAVAILABLE"
    assert lifecycle == ["transaction_closed", "session_closed"]
