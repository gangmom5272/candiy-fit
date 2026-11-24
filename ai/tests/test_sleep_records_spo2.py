"""수면 기록 결과의 산소포화도 90% 미만 기록 문장 (calculation-rules.md 7.5, 11.7)."""

import copy
import dataclasses
import re
from datetime import UTC, datetime

import pytest

from coach_ai.agent.executor import ToolRequest
from coach_ai.ports import MetricResult, ResultContractError
from coach_ai.tools.handlers import get_sleep_records
from coach_ai.tools.schemas import GetSleepRecords
from eval.golden import fixtures as gf
from tests.fakes import KST, D, night, request, sample_port

DEVICE_FACT = "워치 측정은 의료기기 측정이 아니다."
# 점수·한마디·수면 상세 결과의 문장 끝 검사와 같은 기준이다. 결과에 넣는 문장은 사실만 적는다
INSTRUCTION_ENDING = re.compile(r"(주세요|한다|않는다)$")


def kst(day: int, hh: int, mm: int) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=KST)


def oxygen(session, count, times=None, *, avg=95.0):
    """산소포화도 기록이 있는 수면. count 는 90% 미만 기록 수, times 는 그 시각이다."""
    lowest = 88.0 if count else 91.0
    return dataclasses.replace(
        session, spo2_avg=avg, spo2_min=lowest, spo2_max=98.0, spo2_count=max(count or 0, 2), spo2_low_count=count,
        spo2_low_times=times,
    )  # fmt: skip


def port_with(*sessions):
    port = sample_port()
    port.sleep["u1"] = list(sessions)
    return port


async def records(port, period="last_7_days"):
    return (await get_sleep_records(GetSleepRecords(period=period), request(port))).payload


# 10월 12·13·14일에 깬 수면. 결과 문장에서는 깬 날로 10월 12일 수면, 13일 수면, 14일 수면이다
N12 = night(12, (11, 23, 50), (7, 35), 450)
N13 = night(13, (13, 0, 35), (7, 5), 368)
N14 = night(14, (14, 1, 10), (7, 15), 340)


async def test_result_night_keeps_low_times_with_their_offsets():
    times = (datetime(2026, 10, 13, 17, 2, tzinfo=UTC), kst(14, 3, 40))
    port = port_with(oxygen(N14, 2, times))
    result = await port.get_metric_result("u1", "sleep", D(2026, 10, 14), D(2026, 10, 14))
    # 합성 어댑터 원형도 연동 쪽처럼 UTC 초 단위 문자열로 담는다
    assert result.snapshot["detail"]["nights"][0]["spo2_low_times"] == ["2026-10-13T17:02:00Z", "2026-10-13T18:40:00Z"]
    [n] = result.detail.nights
    assert n.spo2_low_times == times
    # 시각은 연동 쪽 문자열의 오프셋(UTC) 그대로다. 한국 시간으로 바꾸는 일은 처리 함수가 한다
    assert [t.utcoffset().total_seconds() for t in n.spo2_low_times] == [0, 0]


@pytest.mark.parametrize(("count", "times", "expected"), [(None, None, None), (0, (), ()), (3, None, None)])
async def test_result_night_keeps_null_empty_list_and_zero(count, times, expected):
    port = port_with(oxygen(N14, count, times) if count is not None else N14)
    [n] = (await port.get_metric_result("u1", "sleep", D(2026, 10, 14), D(2026, 10, 14))).detail.nights
    assert (n.spo2_low_count, n.spo2_low_times) == (count, expected)


async def test_result_night_without_low_times_key_reads_none():
    # 칸을 더하기 전 원형(백엔드 합성 입력, 이전 근거)에는 키가 없다
    result = await port_with(oxygen(N14, 1, (kst(14, 2, 2),))).get_metric_result(
        "u1", "sleep", D(2026, 10, 14), D(2026, 10, 14)
    )
    snapshot = copy.deepcopy(dict(result.snapshot))
    del snapshot["detail"]["nights"][0]["spo2_low_times"]
    [n] = MetricResult("u1", snapshot).detail.nights
    assert (n.spo2_low_count, n.spo2_low_times) == (1, None)


@pytest.mark.parametrize(("count", "times"), [(2, ["2026-10-13T17:02:00Z"]), (None, ["2026-10-13T17:02:00Z"])])
async def test_result_night_low_times_must_match_count(count, times):
    port = port_with(oxygen(N14, 1, (kst(14, 2, 2),)))
    result = await port.get_metric_result("u1", "sleep", D(2026, 10, 14), D(2026, 10, 14))
    snapshot = copy.deepcopy(dict(result.snapshot))
    snapshot["detail"]["nights"][0].update(spo2_low_count=count, spo2_low_times=times)
    with pytest.raises(ResultContractError):
        MetricResult("u1", snapshot)


async def test_no_oxygen_record_has_no_low_sentence_or_note():
    payload = await records(port_with(N12, N13, N14))
    assert "spo2_low" not in payload and "spo2_note" not in payload
    assert all("spo2_low_count" not in n for n in payload["nights"])


async def test_zero_low_records_states_zero_nights_and_zero_times():
    payload = await records(port_with(N12, oxygen(N13, 0, ())))
    assert payload["spo2_low"] == "산소포화도를 기록한 1일 중 90% 미만으로 기록된 날 0일, 기록 0회"
    assert payload["nights"][1]["spo2_low_count"] == 0


async def test_count_without_times_states_count_only():
    # 시각 칸을 채우기 전에 집계된 수면(재집계 전)은 시각 없이 횟수만 쓴다
    payload = await records(port_with(oxygen(N13, 0, ()), oxygen(N14, 3, None)))
    assert payload["spo2_low"] == "산소포화도를 기록한 2일 중 90% 미만으로 기록된 날 1일, 기록 3회"


async def test_times_are_written_in_kst_with_the_wake_date_of_their_sleep():
    # 13일 17:02Z, 18:40Z 는 한국 시간 14일 02:02, 03:40 이고 14일에 깬 수면(10월 14일 수면)의 기록이다
    times = (datetime(2026, 10, 13, 17, 2, tzinfo=UTC), datetime(2026, 10, 13, 18, 40, tzinfo=UTC))
    payload = await records(port_with(oxygen(N13, 0, ()), oxygen(N14, 2, times)))
    assert payload["spo2_low"] == (
        "산소포화도를 기록한 2일 중 90% 미만으로 기록된 날 1일, 기록 2회 (깬 날 기준 10월 14일 수면 새벽 2시 "
        "2분·3시 40분)"
    )


async def test_more_than_five_times_show_earliest_five_and_the_rest_as_count():
    early = (kst(13, 1, 0), kst(13, 1, 30), kst(13, 2, 0), kst(13, 2, 30))
    late = (kst(14, 1, 20), kst(14, 1, 40), kst(14, 2, 0))
    payload = await records(port_with(oxygen(N13, 4, early), oxygen(N14, 3, late)))
    # 7회 중 이른 순 5개를 보여 주고 나머지 2회는 "외 2회"다
    assert payload["spo2_low"] == (
        "산소포화도를 기록한 2일 중 90% 미만으로 기록된 날 2일, 기록 7회 "
        "(깬 날 기준 10월 13일 수면 새벽 1시·1시 30분·2시·2시 30분, 10월 14일 수면 새벽 1시 20분 외 2회)"
    )


async def test_same_time_twice_is_shown_and_counted_twice():
    times = (kst(14, 2, 13), kst(14, 2, 13), kst(14, 3, 0))
    payload = await records(port_with(oxygen(N14, 3, times)))
    assert payload["spo2_low"] == (
        "산소포화도를 기록한 1일 중 90% 미만으로 기록된 날 1일, 기록 3회 (깬 날 기준 10월 14일 수면 새벽 2시 "
        "13분·2시 13분·3시)"
    )


async def test_low_nights_without_times_leave_out_every_time():
    # 90% 미만 기록이 있는 잠 중 하나라도 시각이 없으면 이른 순 5개를 정할 수 없어 시각을 쓰지 않는다
    payload = await records(port_with(oxygen(N13, 2, (kst(13, 2, 0), kst(13, 3, 0))), oxygen(N14, 1, None)))
    assert payload["spo2_low"] == "산소포화도를 기록한 2일 중 90% 미만으로 기록된 날 2일, 기록 3회"


async def test_low_sentence_and_count_come_before_averages():
    payload = await records(port_with(N12, oxygen(N13, 0, ()), oxygen(N14, 2, (kst(14, 2, 2), kst(14, 3, 40)))))
    keys = list(payload)
    assert keys.index("spo2_low") < keys.index("nights")
    night_keys = list(payload["nights"][2])
    assert night_keys.index("spo2_low_count") < night_keys.index("spo2_avg")
    # 수면 기록마다 시각을 다시 쓰지 않는다
    assert all("spo2_low_times" not in n for n in payload["nights"])


async def test_eight_records_put_low_sentence_before_period_summary():
    sessions = [oxygen(night(d, (d - 1, 23, 30), (7, 0), 420), 2 if d == 9 else 0, None) for d in range(7, 15)]
    payload = await records(port_with(*sessions), "last_14_days")
    keys = list(payload)
    assert keys.index("spo2_low") < keys.index("nights") < keys.index("measure_summary")
    assert payload["spo2_low"] == "산소포화도를 기록한 8일 중 90% 미만으로 기록된 날 1일, 기록 2회"
    # 기간 요약에는 90% 미만 기록 칸을 따로 두지 않는다
    assert "spo2_low_records" not in payload["measure_summary"]
    assert list(payload["measure_summary"]) == ["spo2_avg", "spo2_min", "note"]


async def test_device_fact_comes_with_any_oxygen_value():
    # 90% 미만 기록 수가 없고 평균만 있어도 산소포화도 값이 보이면 넣는다
    only_avg = dataclasses.replace(N14, spo2_avg=95.5, spo2_min=92.0, spo2_max=98.0)
    payload = await records(port_with(N13, only_avg))
    assert payload["spo2_note"] == DEVICE_FACT and "spo2_low" not in payload
    payload = await records(port_with(N13, oxygen(N14, 0, ())))
    assert payload["spo2_note"] == DEVICE_FACT
    assert list(payload).index("spo2_note") < list(payload).index("nights")


def _sentences(text: str) -> list[str]:
    """문장으로 나눈다. 소수점과 % 뒤에는 빈칸이 없어 나누지 않는다."""
    return [part.strip(" .") for part in re.split(r"\.\s", text) if part.strip(" .")]


async def test_oxygen_sentences_say_recorded_and_state_facts_only():
    each = await records(port_with(oxygen(N13, 0, ()), oxygen(N14, 3, (kst(14, 2, 2),) * 3)))
    sessions = [oxygen(night(d, (d - 1, 23, 30), (7, 0), 420), 1 if d == 9 else 0, None) for d in range(7, 15)]
    summary = await records(port_with(*sessions), "last_14_days")
    texts = [each["spo2_low"], each["spo2_note"], summary["spo2_low"], summary["spo2_note"]]
    texts.append(summary["measure_summary"]["note"])
    joined = " ".join(texts)
    # 밤마다 기록이 1~2개라 "측정된 횟수"나 "밤새 평균"으로 쓰지 않는다
    assert "측정된" not in joined and "밤새" not in joined and "잰" not in joined
    assert "기록된" in each["spo2_low"]
    sentences = [s for text in texts for s in _sentences(text)]
    assert [s for s in sentences if INSTRUCTION_ENDING.search(s)] == []


async def test_evidence_snapshot_of_main_sleep_keeps_port_values():
    # 근거 snapshot 은 포트가 넘긴 원형 그대로다. 실제 백엔드처럼 90% 미만 기록 시각이 들어 있다
    times = (datetime(2026, 10, 13, 17, 2, tzinfo=UTC),)
    port = port_with(oxygen(N14, 1, times))
    out = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))
    expected = await port.get_metric_result("u1", "sleep", D(2026, 10, 9), D(2026, 10, 15))
    assert out.evidence[0].snapshot == expected.snapshot
    assert out.evidence[0].snapshot["detail"]["nights"][0]["spo2_low_times"] == ["2026-10-13T17:02:00Z"]


# 골든셋 고정 데이터 synth-11 (90% 미만 기록 시각이 있는 시연 페르소나)
async def synth11(period: str) -> dict:
    fixture = gf.load_fixture("synth-11")
    req = ToolRequest(user_id=fixture.user_id, today=fixture.now.date(), port=fixture.port())
    return (await get_sleep_records(GetSleepRecords(period=period), req)).payload


async def test_synth11_last_night_shows_low_time():
    # 10-22 에 깬 수면은 10-22 00:45 에 잠든 밤잠이라 "어젯밤"이고, 90% 미만 기록은 04:32 한 번이다
    payload = await synth11("days_ago:0")
    assert payload["spo2_low"] == (
        "산소포화도를 기록한 1일 중 90% 미만으로 기록된 날 1일, 기록 1회 (깬 날 기준 어젯밤 새벽 4시 32분)"
    )


async def test_synth11_week_shows_earliest_five_times_and_the_rest():
    # 10-16~22 에 깬 7일 중 6일에 7회. 이른 순 5개를 보여 주고 10-21 08:37 과 10-22 04:32 는 "외 2회"다
    payload = await synth11("recent_days:7")
    assert payload["spo2_low"] == (
        "산소포화도를 기록한 7일 중 90% 미만으로 기록된 날 6일, 기록 7회 (깬 날 기준 10월 16일 수면 새벽 4시 43분, "
        "10월 17일 수면 새벽 3시 12분, 10월 18일 수면 새벽 4시 42분, 10월 19일 수면 새벽 3시 46분, 10월 21일 수면 "
        "오전 8시 15분 외 2회)"
    )
