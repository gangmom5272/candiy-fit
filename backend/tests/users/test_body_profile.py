"""프로필 저장값으로 BMI를 계산하며 사용자·조회 오류를 그대로 보존한다."""

from decimal import Decimal, localcontext
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TemporaryStorageError
from app.users import service
from app.users.models import User


@pytest.mark.parametrize(
    "height,weight,bmi",
    [
        ("170", "65", "22.5"),
        ("200", "80.2", "20.1"),
        ("200", "80.19", "20.0"),
        ("165.25", "56.75", "20.8"),
        ("0.01", "500", "50000000000.0"),
        ("300", "0.01", "0.0"),
    ],
)
async def test_body_profile_uses_saved_values_and_half_up(
    monkeypatch, height, weight, bmi
):
    owner = uuid4()
    session = AsyncMock(spec=AsyncSession)
    lookup = AsyncMock(
        return_value=User(
            id=owner, height_cm=Decimal(height), weight_kg=Decimal(weight)
        )
    )
    monkeypatch.setattr(service, "get_current_profile", lookup)
    with localcontext() as context:
        context.prec = 2
        result = await service.get_body_profile(session, owner)
    lookup.assert_awaited_once_with(session, owner)
    assert result.height_cm == Decimal(height)
    assert result.weight_kg == Decimal(weight)
    assert result.bmi == Decimal(bmi)
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        service.UserNotFoundError(),
        TemporaryStorageError("storage_connection_unavailable"),
    ],
)
async def test_body_profile_does_not_turn_lookup_failure_into_missing(
    monkeypatch, error
):
    monkeypatch.setattr(service, "get_current_profile", AsyncMock(side_effect=error))
    with pytest.raises(type(error)):
        await service.get_body_profile(AsyncMock(), uuid4())


async def test_body_profile_reads_each_owner_again_after_update(monkeypatch):
    first, second = uuid4(), uuid4()
    users = {
        first: User(id=first, height_cm=Decimal("170"), weight_kg=Decimal("65")),
        second: User(id=second, height_cm=Decimal("200"), weight_kg=Decimal("80")),
    }
    lookup = AsyncMock(side_effect=lambda session, owner: users[owner])
    monkeypatch.setattr(service.repository, "find_user", lookup)
    session = AsyncMock(spec=AsyncSession)
    session.in_transaction.return_value = True
    before = await service.get_body_profile(session, first)
    other = await service.get_body_profile(session, second)
    users[first].weight_kg = Decimal("68")
    after = await service.get_body_profile(session, first)
    assert (before.weight_kg, other.weight_kg, after.weight_kg) == (
        Decimal("65"),
        Decimal("80"),
        Decimal("68"),
    )
    assert after.bmi == Decimal("23.5")
    assert lookup.await_count == 3
