import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from coach_ai.adapters.memory import InMemoryHealthData, SleepSession, SyncState
from eval.golden import fixtures as gf

KST = timezone(timedelta(hours=9))
PERSONAS = [f"synth-{i:02d}" for i in range(1, 12)] + ["synth-edge"]
LOW_TIMES = ["2026-10-21T02:13:00+09:00", "2026-10-21T02:13:00+09:00", "2026-10-21T01:50:00+09:00"]


def night(**changes) -> SleepSession:
    session = SleepSession(
        date(2026, 10, 21),
        datetime(2026, 10, 20, 23, 30, tzinfo=KST),
        datetime(2026, 10, 21, 7, 0, tzinfo=KST),
        True,
        420,
        spo2_avg=95.1,
        spo2_min=88.6,
        spo2_max=98.0,
        spo2_count=450,
        spo2_low_count=3,
    )
    return replace(session, **changes)


def fixture_with_low_times(tmp_path, value) -> None:
    data = json.loads((gf.FIXTURES_DIR / "synth-11.json").read_text(encoding="utf-8"))
    data["sleep"][0]["spo2_low_times"] = value
    data["data_sha256"] = gf.data_sha256(data)
    (tmp_path / "synth-11.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_sleep_session_low_times_default_to_none():
    assert night().spo2_low_times is None


def test_sleep_session_low_times_are_times_like_start_and_end():
    times = tuple(datetime.fromisoformat(t) for t in LOW_TIMES)
    assert night(spo2_low_times=times).spo2_low_times == times


def test_all_current_fixtures_load_as_they_are():
    assert sorted(path.stem for path in gf.FIXTURES_DIR.glob("*.json")) == sorted(PERSONAS)

    fixtures = gf.load_fixtures(PERSONAS)

    assert sorted(fixtures) == sorted(PERSONAS)
    assert sum(len(f.sleep) for f in fixtures.values()) > 0
    # 시각은 시연 페르소나 synth-11 고정 데이터에만 있다 (golden.py 가 시각을 내보낸다)
    assert all(s.spo2_low_times is None for name, f in fixtures.items() if name != "synth-11" for s in f.sleep)
    assert any(s.spo2_low_times for s in fixtures["synth-11"].sleep)


def test_fixture_reads_kst_low_times_in_order_with_repeats(tmp_path):
    fixture_with_low_times(tmp_path, LOW_TIMES)

    session = gf.load_fixture("synth-11", tmp_path).sleep[0]

    assert session.spo2_low_times == tuple(datetime.fromisoformat(t) for t in LOW_TIMES)
    assert [t.utcoffset() for t in session.spo2_low_times] == [timedelta(hours=9)] * 3


def test_fixture_null_low_times_is_none(tmp_path):
    fixture_with_low_times(tmp_path, None)
    assert gf.load_fixture("synth-11", tmp_path).sleep[0].spo2_low_times is None


@pytest.mark.parametrize("value", ["2026-10-21T02:13:00+09:00", ["2026-10-21T02:13:00"], [1], [None]])
def test_fixture_rejects_low_times_that_are_not_a_list_of_aware_times(tmp_path, value):
    fixture_with_low_times(tmp_path, value)
    with pytest.raises(gf.FixtureError):
        gf.load_fixture("synth-11", tmp_path)


async def test_sleep_metric_result_carries_low_times_like_the_backend():
    # 연동 쪽 수면 원형처럼 nights 에 UTC 초 단위 시각을 순서와 반복 그대로 담고, 없으면 null 이다.
    # 산소포화도 원형은 nights 가 없어 시각이 있어도 같다
    uid = "u1"
    now = datetime(2026, 10, 22, 21, 0, tzinfo=KST)
    states = {metric: SyncState(datetime(2026, 10, 22, 8, 0, tzinfo=KST), "success") for metric in ("sleep",)}
    times = tuple(datetime.fromisoformat(t) for t in LOW_TIMES)
    day = date(2026, 10, 21)

    def port(session: SleepSession) -> InMemoryHealthData:
        return InMemoryHealthData(sleep={uid: [session]}, sync={uid: states}, now=now)

    plain = await port(night()).get_metric_result(uid, "sleep", day, day)
    timed = await port(night(spo2_low_times=times)).get_metric_result(uid, "sleep", day, day)
    [plain_night], [timed_night] = plain.snapshot["detail"]["nights"], timed.snapshot["detail"]["nights"]
    assert plain_night.pop("spo2_low_times") is None
    assert timed_night.pop("spo2_low_times") == ["2026-10-20T17:13:00Z", "2026-10-20T17:13:00Z", "2026-10-20T16:50:00Z"]
    assert timed.snapshot == plain.snapshot
    oxygen = [
        await port(s).get_metric_result(uid, "oxygen_saturation", day, day)
        for s in (night(), night(spo2_low_times=times))
    ]
    assert oxygen[0].snapshot == oxygen[1].snapshot
