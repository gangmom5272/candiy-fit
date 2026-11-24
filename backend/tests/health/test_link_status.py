from uuid import uuid4

import pytest

from app.health import service
from app.health.errors import HealthDataForbiddenError, HealthDataNotLinkedError
from tests.health.test_schemas import USER
from tests.health.test_service import state


@pytest.mark.parametrize(
    "errors,unlinked",
    [
        ([], False),
        ([None], False),
        (["http_503"], False),
        (["http_404"], True),
        (["http_404", "http_404"], True),
        (["http_404", None], False),
        (["http_404", "http_503"], False),
        (["http_404", "aggregate:RuntimeError"], False),
        (["http_404", "HTTP_404"], False),
        (["http_404", "http_404 "], False),
    ],
)
@pytest.mark.parametrize("ready", [True, False])
def test_link_signal_uses_all_stored_errors_and_not_readiness(errors, unlinked, ready):
    states = [
        state(
            metric="steps" if index == 0 else "weights",
            last_error=error,
            last_status="fail",
            initial_sync_done=ready,
        )
        for index, error in enumerate(errors)
    ]
    if unlinked:
        with pytest.raises(HealthDataNotLinkedError) as caught:
            service.check_candiy_link(states, user_id=USER)
        assert caught.value.metrics == ()  # 111번처럼 단일 지표가 없는 호출도 지원한다.
        assert "http_404" not in str(caught.value)
    else:
        service.check_candiy_link(states, user_id=USER)


def test_owner_check_precedes_link_signal():
    states = [state(end_user_id=uuid4(), last_error="http_404")]
    with pytest.raises(HealthDataForbiddenError) as caught:
        service.check_candiy_link(states, user_id=USER, metrics=("exercises",))
    assert caught.value.metrics == ("exercises",)


def test_metric_context_is_preserved():
    with pytest.raises(HealthDataNotLinkedError) as caught:
        service.check_candiy_link(
            [state(last_error="http_404")], user_id=USER, metrics=("exercises",)
        )
    assert caught.value.metrics == ("exercises",)
