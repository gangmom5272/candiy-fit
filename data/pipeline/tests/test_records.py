import json
from datetime import UTC, date, datetime, timedelta

import pytest

from candiy_pipeline.records import KST, SkipRecord, in_range, parse_record

# 합성 레코드 (candiy-api.md 2장 형식). 실제 측정값이 아니다.
BASE = {
    "_id": "server-1",
    "user_id": "internal-1",
    "app": "com.sec.android.app.shealth",
    "data_id": "hc-1",
    "start": "2026-10-17T00:00:00+09:00",
    "end": "2026-10-17T23:59:59.999+09:00",
    "data": json.dumps({"count": 1234}),
    "last_modified_time": "2026-10-17T09:30:00+09:00",
    "created_at": "2026-10-17T09:31:00+09:00",
    "updated_at": "2026-10-17T09:31:00+09:00",
}


def make(**overrides):
    record = dict(BASE)
    for key, value in overrides.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    return record


def test_parses_record_and_second_json():
    r = parse_record(make())
    assert r.data_id == "hc-1"
    assert r.payload == {"count": 1234}
    assert r.start_at == datetime(2026, 10, 17, tzinfo=KST)
    assert r.end_at is not None


def test_point_record_has_no_end():
    assert parse_record(make(end=None)).end_at is None


def test_utc_z_suffix():
    r = parse_record(make(start="2026-10-16T15:00:00Z"))
    assert r.start_at == datetime(2026, 10, 16, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"app": "io.candiy.healthconnect.quickstart"}, "excluded_app"),
        ({"app": None}, "missing_app"),
        ({"data_id": None}, "missing_data_id"),
        ({"data": "{not json"}, "invalid_data"),
        ({"data": "[1, 2]"}, "invalid_data"),
        ({"start": None}, "missing_start"),
        ({"start": "어제"}, "invalid_start"),
        ({"last_modified_time": None}, "missing_last_modified_time"),
    ],
)
def test_skips_with_reason(overrides, reason):
    with pytest.raises(SkipRecord) as e:
        parse_record(make(**overrides))
    assert e.value.reason == reason


def test_sample_app_can_be_included_for_development():
    r = parse_record(make(app="io.candiy.healthconnect.quickstart"), include_sample_app=True)
    assert r.app == "io.candiy.healthconnect.quickstart"


def test_skip_reason_does_not_contain_values():
    with pytest.raises(SkipRecord) as e:
        parse_record(make(data='{"percentage": 97'))
    assert "97" not in str(e.value)


def test_in_range_uses_kst_date():
    # UTC 10/16 15:30 = KST 10/17 00:30
    r = parse_record(make(start="2026-10-16T15:30:00Z", end=None))
    assert in_range(r, date(2026, 10, 17), date(2026, 10, 17))
    assert not in_range(r, date(2026, 10, 16), date(2026, 10, 16))


def test_naive_time_is_treated_as_kst():
    r = parse_record(make(start="2026-10-17T08:00:00"))
    assert r.start_at.utcoffset() == timedelta(hours=9)
