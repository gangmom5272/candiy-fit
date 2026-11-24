"""302 후속: 저장 목록을 주 수면 REST와 전체 세션 조회에 전달한다."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.coaching.schemas import evidence_data, partition_evidence
from app.health import repository
from app.health.calculations import sleep_record
from app.health.rest_schemas import metric_response
from tests.coaching.fakes import synthetic_evidence, synthetic_evidence_info
from tests.health.test_calculations import calculate
from tests.health.test_sleep_detail import detailed_night, read_row
from tests.health.test_sleep_sessions import query, stored


@pytest.mark.parametrize(
    "count,size", [(None, None), (0, None), (3, None), (0, 0), (3, 3)]
)
async def test_states_survive_both_queries_and_rest(count, size):
    start = detailed_night().start_at
    times = None if size is None else [start + timedelta(minutes=5)] * size
    fields = dict(spo2_low_count=count, spo2_low_times=times)
    records = await read_row("sleeps", **fields)
    result = calculate(records, metric="sleeps")
    payload = metric_response(result.to_public()).model_dump(mode="json")
    night = payload["detail"]["nights"][0]
    expected = None if size is None else ["2026-10-20T14:05:00Z"] * size
    assert night["spo2LowTimes"] == expected
    assert night["spo2LowCount"] == count

    rows = Mock()
    rows.mappings.return_value = [stored(**fields)]
    db = Mock(execute=AsyncMock(return_value=rows))
    sessions = await repository.list_sleep_sessions(db, query())
    assert "spo2_low_times" in str(db.execute.call_args.args[0])
    assert sessions[0].model_dump(mode="json")["spo2_low_times"] == expected


def test_new_kst_evidence_times_become_utc_without_changing_the_instant():
    snapshot = (
        calculate([sleep_record(detailed_night())], metric="sleeps")
        .to_public()
        .model_dump(mode="json")
    )
    night = snapshot["detail"]["nights"][0]
    night.update(
        spo2_low_count=2,
        spo2_low_times=["2026-10-21T02:02:00+09:00"] * 2,
    )
    item = synthetic_evidence().event_item()
    item.update(metric="sleeps", period=snapshot["period"], snapshot=snapshot)
    result = partition_evidence([item])
    assert result.skipped_metrics == ()
    actual = result.valid[0].snapshot["detail"]["nights"][0]
    assert actual["spo2_low_times"] == ["2026-10-20T17:02:00Z"] * 2
    assert actual["start_at"] == night["start_at"]
    assert actual["end_at"] == night["end_at"]
    assert actual["date"] == night["date"]


@pytest.mark.parametrize(
    "present,times",
    [
        (False, None),
        (True, None),
        (True, []),
        (True, ["2026-10-21T02:02:00+09:00"] * 2),
        (True, ["2026-10-20T17:02:00Z"] * 2),
    ],
)
def test_stored_evidence_output_preserves_original_times(present, times):
    snapshot = (
        calculate([sleep_record(detailed_night())], metric="sleeps")
        .to_public()
        .model_dump(mode="json")
    )
    night = snapshot["detail"]["nights"][0]
    night["spo2_low_count"] = len(times) if times is not None else None
    if present:
        night["spo2_low_times"] = times
    else:
        night.pop("spo2_low_times")
    expected = deepcopy(snapshot)
    stored_evidence = replace(
        synthetic_evidence_info(),
        metric="sleeps",
        period_start=date.fromisoformat(snapshot["period"]["start"]),
        period_end=date.fromisoformat(snapshot["period"]["end"]),
        snapshot=snapshot,
    )

    payload = evidence_data([stored_evidence])[0].model_dump(mode="json", by_alias=True)

    assert payload["snapshot"] == expected
    assert stored_evidence.snapshot == expected
    assert ("spo2_low_times" in payload["snapshot"]["detail"]["nights"][0]) == present


def test_utc_output_keeps_existing_second_precision():
    base = detailed_night()
    measured_at = (base.start_at + timedelta(minutes=5)).replace(microsecond=123456)
    row = detailed_night(spo2_low_count=1, spo2_low_times=[measured_at])
    assert row.spo2_low_times == [measured_at]
    assert row.model_dump(mode="json")["spo2_low_times"] == ["2026-10-20T14:05:00Z"]


@pytest.mark.parametrize("case", ["count", "no_count", "order", "naive"])
def test_invalid_stored_lists_are_not_silently_normalized(case):
    start = detailed_night().start_at
    times = [start, start + timedelta(minutes=1)]
    count = 2
    if case == "count":
        count = 1
    elif case == "no_count":
        count = None
    elif case == "order":
        times.reverse()
    else:
        times[0] = start.replace(tzinfo=None)
    with pytest.raises(ValidationError):
        detailed_night(spo2_low_count=count, spo2_low_times=times)


def test_boundaries_duplicates_and_more_than_five_are_preserved():
    base = detailed_night()
    times = [base.start_at] * 5 + [base.end_at]
    result = calculate(
        [sleep_record(detailed_night(spo2_low_count=6, spo2_low_times=times))],
        metric="sleeps",
    )
    data = metric_response(result.to_public()).model_dump(mode="json")
    assert len(data["detail"]["nights"][0]["spo2LowTimes"]) == 6


@pytest.mark.parametrize("present,value", [(False, None), (True, None), (True, [])])
def test_evidence_preserves_missing_null_and_empty_list(present, value):
    snapshot = (
        calculate(
            [sleep_record(detailed_night(spo2_low_count=0, spo2_low_times=value))],
            metric="sleeps",
        )
        .to_public()
        .model_dump(mode="json")
    )
    if not present:
        snapshot["detail"]["nights"][0].pop("spo2_low_times")
    item = synthetic_evidence().event_item()
    item.update(metric="sleeps", period=snapshot["period"], snapshot=snapshot)
    result = partition_evidence([item])
    assert result.skipped_metrics == ()
    assert result.valid[0].snapshot == snapshot
