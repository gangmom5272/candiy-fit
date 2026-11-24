import pytest
from sqlalchemy import delete, select

from app.health import service
from app.health.models import ExerciseCode

pytestmark = pytest.mark.db


async def test_catalog_reads_sorted_database_fields_and_new_registration(health_db):
    factory, _ = health_db
    async with factory() as db:
        before = (await db.execute(select(ExerciseCode.__table__))).mappings().all()
    assert before
    assert any(row["code"] == 0 for row in before)
    code = max(row["code"] for row in before) + 1000
    try:
        async with factory.begin() as db:
            db.add(
                ExerciseCode(
                    code=code,
                    name_en="SYNTHETIC_CATALOG_TEST",
                    name_ko="합성 기준표 운동",
                    category="strength",
                )
            )
        actual = await service.list_exercise_types(session_factory=factory)
        expected = [
            {key: row[key] for key in ("code", "name_ko", "category")}
            for row in sorted(before, key=lambda row: row["code"])
        ] + [dict(code=code, name_ko="합성 기준표 운동", category="strength")]
        assert [entry.model_dump(mode="json") for entry in actual] == expected
    finally:
        async with factory.begin() as db:
            await db.execute(delete(ExerciseCode).where(ExerciseCode.code == code))
    async with factory() as db:
        after = (await db.execute(select(ExerciseCode.__table__))).mappings().all()
    assert sorted(after, key=lambda row: row["code"]) == sorted(
        before, key=lambda row: row["code"]
    )
