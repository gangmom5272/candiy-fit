"""snapshot.kind 근거(hourly_steps, sleep_sessions)의 검증·직렬화를 검사한다."""

import traceback
from typing import Any

import pytest

from app.coaching.schemas import (
    EvidenceFormatError,
    _evidence_item,
    evidence_data,
    partition_evidence,
)
from tests.coaching.fakes import SYNTHETIC_SNAPSHOT, synthetic_evidence
from tests.coaching.kind_fixtures import (
    SYNTHETIC_HOURLY_STEPS,
    SYNTHETIC_SLEEP_SESSIONS,
    kind_item,
)

PRIVATE_VALUES = ("s-001", "2026-11-04T14:10", "88888888-8888-4888-8888-888888888888")


def test_partition_evidence_hourly_steps_kind_is_accepted() -> None:
    """hourly_steps 항목 → valid 1개, metric steps, kind와 키 집합을 보존한다."""
    result = partition_evidence([kind_item("hourly_steps")])
    assert result.skipped == 0
    assert len(result.valid) == 1
    info = result.valid[0]
    assert info.metric == "steps"
    assert info.snapshot["kind"] == "hourly_steps"
    assert set(info.snapshot) == set(SYNTHETIC_HOURLY_STEPS)


def test_partition_evidence_sleep_sessions_kind_is_accepted() -> None:
    """sleep_sessions 항목(주 수면+낮잠) → valid 1개, 낮잠 is_main은 False다."""
    result = partition_evidence([kind_item("sleep_sessions")])
    assert result.skipped == 0
    info = result.valid[0]
    assert info.metric == "sleeps"
    assert info.snapshot["kind"] == "sleep_sessions"
    assert len(info.snapshot["sessions"]) == len(SYNTHETIC_SLEEP_SESSIONS["sessions"])
    assert info.snapshot["sessions"][1]["is_main"] is False
    assert len(info.snapshot["sessions"][0]["spo2_low_times"]) == 2
    assert info.snapshot["sessions"][1]["spo2_low_times"] is None


@pytest.mark.parametrize("kind", ["exercise_samples", "vo2_max_records", None, 1])
def test_partition_evidence_unknown_kind_is_skipped(kind: Any) -> None:
    """kind가 둘 밖(exercise_samples, null, 숫자 등) → 항목을 버리고 외곽 metric을 기록한다."""
    item = kind_item("hourly_steps")
    item["snapshot"]["kind"] = kind
    result = partition_evidence([item])
    assert result.valid == ()
    assert result.skipped_metrics == ("steps",)


def test_partition_evidence_kind_metric_mismatch_is_skipped() -> None:
    """hourly_steps snapshot에 외곽 metric sleeps → 그 항목만 버리고 skipped_metrics는 sleeps다."""
    result = partition_evidence([kind_item("hourly_steps", metric="sleeps")])
    assert result.valid == ()
    assert result.skipped_metrics == ("sleeps",)


def _broken(case: str) -> dict[str, Any]:
    """case에 맞게 sleep_sessions 근거 항목을 깨뜨려 돌려준다."""
    item = kind_item("sleep_sessions")
    snapshot = item["snapshot"]
    if case == "user_id":
        snapshot["user_id"] = "88888888-8888-4888-8888-888888888888"
    elif case == "missing_data_status":
        del snapshot["data_status"]
    elif case == "common_key":
        snapshot["summary"] = {}
    elif case == "unknown_kind":
        snapshot["kind"] = "exercise_samples"
    elif case == "mismatch":
        item["metric"] = "steps"
    return item


BROKEN_CASES = [
    "user_id",
    "missing_data_status",
    "common_key",
    "unknown_kind",
    "mismatch",
]


@pytest.mark.parametrize("case", ["user_id", "missing_data_status", "common_key"])
def test_partition_evidence_malformed_kind_snapshot_is_skipped(case: str) -> None:
    """kind snapshot에 user_id 추가·필수 키 누락·공통 원형 키 혼입 → 그 항목을 버린다."""
    result = partition_evidence([_broken(case)])
    assert result.valid == ()
    assert result.skipped_metrics == ("sleeps",)


def test_partition_evidence_bad_kind_keeps_neighbors_in_order() -> None:
    """[공통 원형, 잘못된 kind, 정상 kind] → 둘만 순서대로 남고 skipped는 1이다."""
    bad = kind_item("hourly_steps", 2)
    bad["id"] = "99999999-9999-4999-8999-999999999999"
    bad["snapshot"]["kind"] = "vo2_max_records"
    result = partition_evidence(
        [synthetic_evidence(1).event_item(), bad, kind_item("sleep_sessions", 2)]
    )
    assert [item.position for item in result.valid] == [1, 2]
    assert result.skipped == 1


def test_partition_evidence_common_snapshot_is_unchanged_by_kind_branch() -> None:
    """kind 없는 공통 원형 항목 → 기존대로 snapshot이 SYNTHETIC_SNAPSHOT과 같다."""
    result = partition_evidence([synthetic_evidence().event_item()])
    assert result.valid[0].snapshot == SYNTHETIC_SNAPSHOT


@pytest.mark.parametrize("case", BROKEN_CASES)
def test_evidence_item_kind_error_has_no_input_values(case: str) -> None:
    """kind 근거 형식 위반 → 고정 메시지이고 원인·traceback에 입력값이 없다."""
    with pytest.raises(EvidenceFormatError) as caught:
        _evidence_item(_broken(case))
    error = caught.value
    assert str(error) == "Evidence format mismatch"
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    for secret in PRIVATE_VALUES:
        assert secret not in rendered


def test_evidence_data_kind_snapshot_keeps_kind_and_outer_metric() -> None:
    """kind 근거 저장 원형 → 공개 직렬화에 snapshot.kind가 남고 metric이 대응 지표다."""
    info = partition_evidence([kind_item("sleep_sessions")]).valid[0]
    dumped = evidence_data([info])[0].model_dump(mode="json", by_alias=True)
    assert dumped["snapshot"]["kind"] == "sleep_sessions"
    assert dumped["metric"] == "sleeps"


def _main_session_item(**changes: Any) -> dict[str, Any]:
    """주 수면 세션에 changes를 덮어쓴 sleep_sessions 근거 항목을 돌려준다(값이 None이면 키를 뺀다)."""
    item = kind_item("sleep_sessions")
    session = item["snapshot"]["sessions"][0]
    for key, value in changes.items():
        if value is None:
            del session[key]
        else:
            session[key] = value
    return item


def test_partition_evidence_spo2_low_times_count_mismatch_is_skipped() -> None:
    """spo2_low_times 3개인데 spo2_low_count 2 → 항목을 버리고 skipped_metrics는 sleeps다."""
    item = _main_session_item(
        spo2_low_times=[
            "2026-11-04T17:02:00Z",
            "2026-11-04T18:00:00Z",
            "2026-11-04T19:31:00Z",
        ]
    )
    result = partition_evidence([item])
    assert result.valid == ()
    assert result.skipped_metrics == ("sleeps",)


def test_partition_evidence_spo2_low_times_key_missing_is_skipped() -> None:
    """주 수면에서 spo2_low_times 키 누락 → 필수 키라 항목을 버린다."""
    result = partition_evidence([_main_session_item(spo2_low_times=None)])
    assert result.valid == ()
    assert result.skipped_metrics == ("sleeps",)


def test_partition_evidence_spo2_low_times_descending_is_skipped() -> None:
    """spo2_low_times가 내림차순 → 항목을 버린다."""
    item = _main_session_item(
        spo2_low_times=["2026-11-04T19:31:00Z", "2026-11-04T17:02:00Z"]
    )
    result = partition_evidence([item])
    assert result.valid == ()
    assert result.skipped_metrics == ("sleeps",)
