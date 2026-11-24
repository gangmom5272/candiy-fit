"""운동 근거의 detail.exercise_activity(315 확장)가 검증·저장 원형을 통과하고 그대로 남는지 검사한다."""

from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest

from app.coaching.schemas import evidence_data, partition_evidence, request_state_data
from tests.coaching.fakes import synthetic_evidence, synthetic_evidence_info
from tests.coaching.test_request_status_api import make_state
from tests.health.test_exercise import activity_result
from tests.health.test_rest_response import result_for

THIRD_ID = "55555555-5555-4555-8555-555555555555"
LEGACY_DETAIL_KEYS = {
    "measured_min",
    "measured_max",
    "nights",
    "daily_ranges",
    "sessions",
}


def activity_snapshot() -> dict:
    """activity_result()의 공개 원형을 근거 snapshot(snake_case JSON dict)으로 만든다."""
    return activity_result().to_public().model_dump(mode="json")


def activity_item(
    position: int = 1,
    *,
    snapshot: dict | None = None,
    metric: str = "exercises",
    id: str | None = None,
) -> dict:
    """운동 확장 snapshot을 담은 AI 근거 항목을 만든다. metric·snapshot은 바꿔 끼울 수 있다."""
    snapshot = activity_snapshot() if snapshot is None else snapshot
    item = synthetic_evidence(position, id=id).event_item()
    item.update(metric=metric, period=snapshot["period"], snapshot=snapshot)
    return item


def test_exercise_activity_evidence_passes_and_keeps_snapshot():
    """운동 확장 근거 한 건 → partition_evidence를 통과하고 출력 snapshot이 입력과 같다."""
    item = activity_item()
    snapshot = item["snapshot"]
    assert snapshot["metric"] == "exercises", "재료 전제: 운동 지표"
    assert snapshot["period"] == {"start": "2026-10-19", "end": "2026-10-22"}
    partition = partition_evidence([item])
    assert partition.skipped_metrics == ()
    assert len(partition.valid) == 1
    valid = partition.valid[0]
    assert valid.metric == "exercises"
    assert valid.period_start == date(2026, 10, 19)
    assert valid.period_end == date(2026, 10, 22)
    assert valid.snapshot == snapshot
    output = evidence_data(partition.valid)[0].model_dump(mode="json")["snapshot"]
    assert output == snapshot
    activity = output["detail"]["exercise_activity"]
    assert activity["days"][1]["exercise_calories_kcal"] == 0
    assert activity["days"][1]["exercise_distance_m"] is None
    assert activity["days"][1]["distance_reason"] == "NULL_VALUE"
    assert activity["days"][2]["exercise_count"] is None
    assert activity["summary"]["exercise_count"]["avg"] == 0.5
    assert item["snapshot"] == activity_snapshot()


def corrupt_extra_detail(activity):
    """detail.exercise_activity에 모르는 키를 넣는다."""
    activity["extra"] = "synthetic-private-input"


def corrupt_extra_day(activity):
    """days[0]에 모르는 키를 넣는다."""
    activity["days"][0]["extra"] = "synthetic-private-input"


def corrupt_extra_statistic(activity):
    """summary.exercise_count에 모르는 키를 넣는다."""
    activity["summary"]["exercise_count"]["extra"] = 1


def corrupt_extra_source(activity):
    """source_statuses.exercise_sessions에 모르는 키를 넣는다."""
    activity["source_statuses"]["exercise_sessions"]["extra"] = True


def corrupt_unknown_source(activity):
    """source_statuses에 모르는 출처 키를 더한다."""
    statuses = activity["source_statuses"]
    statuses["synthetic_source"] = deepcopy(statuses["distances"])


def corrupt_missing_source(activity):
    """source_statuses에서 distances를 뺀다."""
    del activity["source_statuses"]["distances"]


def corrupt_negative_distance(activity):
    """days[0] 거리를 음수로 만든다."""
    activity["days"][0]["exercise_distance_m"] = -1


def corrupt_boolean_calories(activity):
    """days[0] 칼로리를 bool로 만든다."""
    activity["days"][0]["exercise_calories_kcal"] = True


def corrupt_unknown_reason(activity):
    """days[1] 거리 사유를 모르는 값으로 만든다."""
    activity["days"][1]["distance_reason"] = "SYNTHETIC"


def corrupt_statistic_mismatch(activity):
    """통계의 days_with_data만 0으로 만들어 total과 어긋나게 한다."""
    activity["summary"]["exercise_count"]["days_with_data"] = 0


def corrupt_fewer_days(activity):
    """days의 마지막 하루를 뺀다."""
    activity["days"].pop()


CORRUPTIONS = {
    "extra_detail": corrupt_extra_detail,
    "extra_day": corrupt_extra_day,
    "extra_statistic": corrupt_extra_statistic,
    "extra_source": corrupt_extra_source,
    "unknown_source": corrupt_unknown_source,
    "missing_source": corrupt_missing_source,
    "negative_distance": corrupt_negative_distance,
    "boolean_calories": corrupt_boolean_calories,
    "unknown_reason": corrupt_unknown_reason,
    "statistic_mismatch": corrupt_statistic_mismatch,
    "fewer_days": corrupt_fewer_days,
}


@pytest.mark.parametrize("corruption", list(CORRUPTIONS))
def test_exercise_activity_corrupted_item_is_skipped_and_neighbors_kept(corruption):
    """exercise_activity 안의 모르는 키·잘못된 값 → 그 항목만 버리고 지표 이름을 남긴다."""
    first = synthetic_evidence().event_item()
    bad = activity_item(2)
    CORRUPTIONS[corruption](bad["snapshot"]["detail"]["exercise_activity"])
    last = synthetic_evidence(3, id=THIRD_ID).event_item()
    partition = partition_evidence([first, bad, last])
    assert partition.skipped_metrics == ("exercises",)
    assert [item.position for item in partition.valid] == [1, 3]
    assert [item.metric for item in partition.valid] == ["steps", "steps"]
    assert partition_evidence([bad]).valid == ()


@pytest.mark.parametrize(
    "metric", ["steps", "heart_rates", "oxygen_saturations", "sleeps"]
)
def test_exercise_activity_on_non_exercise_metric_is_skipped(metric):
    """운동 외 지표에 날짜가 맞는 exercise_activity가 붙음 → 지표 규칙으로 그 근거만 버리고 이웃은 남긴다."""
    snapshot = activity_snapshot()
    assert "exercise_activity" in snapshot["detail"]
    activity_days = [d["date"] for d in snapshot["detail"]["exercise_activity"]["days"]]
    assert activity_days == [d["date"] for d in snapshot["days"]]
    snapshot["metric"] = metric
    item = activity_item(snapshot=snapshot, metric=metric)
    good = synthetic_evidence(2).event_item()
    partition = partition_evidence([item, good])
    assert partition.skipped_metrics == (metric,)
    assert [i.position for i in partition.valid] == [2]


@pytest.mark.parametrize(
    "metric", ["steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises"]
)
def test_legacy_snapshot_without_exercise_activity_passes_without_key(metric):
    """exercise_activity 키가 없는 구형 근거 → 통과하고 출력에도 키가 생기지 않는다."""
    snapshot = result_for(metric).to_public().model_dump(mode="json")
    assert "exercise_activity" not in snapshot["detail"]
    assert set(snapshot["detail"]) == LEGACY_DETAIL_KEYS
    item = activity_item(snapshot=snapshot, metric=metric)
    partition = partition_evidence([item])
    assert partition.skipped_metrics == ()
    assert partition.valid[0].snapshot == snapshot
    assert "exercise_activity" not in partition.valid[0].snapshot["detail"]
    output = evidence_data(partition.valid)[0].model_dump(mode="json")["snapshot"]
    assert "exercise_activity" not in output["detail"]


def test_stored_exercise_evidence_without_activity_is_returned_without_backfill():
    """저장된 구형 운동 근거 재조회 → 키를 덧붙이지 않고, 확장 근거는 그대로 나온다."""
    legacy = result_for("exercises").to_public().model_dump(mode="json")
    assert "exercise_activity" not in legacy["detail"]
    new = activity_snapshot()
    for snapshot, expected in ((legacy, False), (new, True)):
        item = replace(synthetic_evidence_info(), metric="exercises", snapshot=snapshot)
        state = replace(make_state(), evidence=(item,))
        output = request_state_data(state).model_dump(mode="json")["evidence"][0][
            "snapshot"
        ]
        assert output == snapshot
        assert ("exercise_activity" in output["detail"]) is expected
