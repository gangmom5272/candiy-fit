"""30일 일별 추이 입력량 측정(bench/trend_tokens.py) 테스트. LLM 을 호출하지 않는다."""

import json

import pytest

from bench import trend_tokens as tt
from eval import repeated_question as rq

COMMIT = "abc1234"


# 커밋 조회는 git 을 실행해, git 이 없거나 저장소 소유자가 달라 git 이 거부하면 None 을 반환한다.
# _commit 은 커밋 전 변경 여부도 붙여 작업 트리 상태에 따라 값이 달라지므로 함수 전체를 고정값으로 바꾼다
@pytest.fixture(autouse=True)
def fixed_commit(monkeypatch):
    monkeypatch.setattr(tt, "_commit", lambda: COMMIT)


def test_estimate_counts_each_digit_as_one_token():
    assert tt.composition('{"date": "2026-10-21"}') == {"chars": 22, "digits": 8, "others": 14}
    assert tt.estimate_tokens('{"date": "2026-10-21"}', rest_ratio=0.5) == 8 + 7
    assert tt.estimate_tokens("기록 없음", rest_ratio=1.0) == 5


async def test_measure_uses_gateway_messages_of_synthetic_data():
    report = await tt.measure("last_30_days", rest_ratio=0.5)
    oxygen = report["metrics"]["oxygen_saturation"]
    [content] = await tt.tool_contents([("get_daily_trend", {"metric": "oxygen_saturation", "period": "last_30_days"})])
    body = json.loads(content)
    assert body["evidence_ids"] == ["E1"] and len(body["result"]["points"]) == 30
    assert oxygen["chars"] == len(content) and oxygen["tokens"] == tt.estimate_tokens(content, rest_ratio=0.5)

    # 증가량은 고치기 전 측정 때 LLM 에 전달된 not_supported 메시지와 비교한다
    assert tt.composition(tt.BEFORE_REPLY) == {"chars": 110, "digits": 0, "others": 110}
    assert json.loads(tt.BEFORE_REPLY)["error"] == "not_supported"
    assert report["not_supported"] == {**tt.composition(tt.BEFORE_REPLY), "tokens": 55}
    assert oxygen["increase"] == oxygen["tokens"] - report["not_supported"]["tokens"]
    assert set(report["metrics"]) == {"steps", "heart_rate", "oxygen_saturation", "sleep", "exercise"}
    # 날짜 지정 질문은 산소포화도와 수면을 한 왕복에 함께 조회한다
    date_question = report["date_question"]
    assert date_question["increase"] == oxygen["increase"] + report["metrics"]["sleep"]["increase"]
    assert report["data_sha256"] == rq.data_sha256(rq.synthetic_health_data())[:12]
    assert report["now"] == rq.NOW.isoformat()


async def _second_round(period: str) -> str:
    summary, sleep = await tt.tool_contents(
        [
            ("get_metric_summary", {"metric": "oxygen_saturation", "period": period}),
            ("get_sleep_records", {"period": period}),
        ]
    )
    return summary + sleep + tt.PERIOD_REPEATS * period


def _record(question, condition, period, tokens, *, metric="oxygen_saturation"):
    calls = [
        ("get_daily_trend", 1, {"metric": "oxygen_saturation", "period": period}),
        ("get_daily_trend", 1, {"metric": "sleep", "period": period}),
        ("get_metric_summary", 2, {"metric": metric, "period": period}),
        ("get_sleep_records", 2, {"period": period}),
    ]
    return {
        "question": question,
        "condition": condition,
        "llm_calls": 3,
        "input_tokens": tokens,
        "tool_calls": [{"round": r, "name": n, "args": a, "reply": None} for n, r, a in calls],
    }


async def test_calibrate_recovers_ratio_from_runs_that_differ_only_in_period(tmp_path):
    ratio, base = 0.6, 5000
    records = []
    for period in ("last_7_days", "last_14_days", "last_30_days"):
        text = await _second_round(period)
        composition = tt.composition(text)
        tokens = base + composition["digits"] + round(ratio * composition["others"])
        records.append(_record("default", "sleep_history", period, tokens))
    # 둘째 왕복 도구가 다른 실행은 쓰지 않는다
    records.append(_record("default", "sleep_history", "last_7_days", 1, metric="sleep"))
    path = tmp_path / "x_records.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    calibration = await tt.calibrate([path])
    assert calibration["rest_ratio"] == pytest.approx(ratio, abs=0.01)
    pairs = {(p["longer"], p["shorter"]) for p in calibration["pairs"]}
    # 오늘로 끝나는 7일·14일 수면 기록에는 두 줄(줄어드는 중, 어젯밤 대 그 전 7일)이 붙어 7일과 30일은 숫자 밖 글자
    # 수 차이가 _MIN_OTHERS_DIFF 보다 작다. 그 쌍은 쓰지 않고 남은 두 쌍으로 맞춘다
    assert pairs == {("last_14_days", "last_7_days"), ("last_14_days", "last_30_days")}


def test_main_saves_doc_with_date_commit_command_and_conditions(tmp_path):
    argv = ["--save-doc", "--doc-dir", str(tmp_path)]
    tt.main(argv)
    md_path = next(tmp_path.glob("*-daily-trend-tokens.md"))
    [report] = json.loads(next(tmp_path.glob("*-daily-trend-tokens.json")).read_text(encoding="utf-8"))
    assert report["command"] == "python -m bench.trend_tokens " + " ".join(argv)
    assert report["commit"] == COMMIT and report["measured_at"] and report["period"] == "last_30_days"
    assert report["rest_ratio"] == tt.REST_RATIO and report["calibration"] is None
    md = md_path.read_text(encoding="utf-8")
    for text in (f"커밋 `{COMMIT}`", "실행 명령", "합성 데이터 sha256", "도구 스키마 sha256", "추정", "not_supported"):
        assert text in md


def test_save_doc_appends_to_same_day_record_and_keeps_previous(tmp_path):
    tt.main(["--save-doc", "--doc-dir", str(tmp_path)])
    md_path = next(tmp_path.glob("*-daily-trend-tokens.md"))
    json_path = md_path.with_suffix(".json")
    first_md = md_path.read_text(encoding="utf-8")
    [first] = json.loads(json_path.read_text(encoding="utf-8"))

    tt.main(["--save-doc", "--doc-dir", str(tmp_path), "--note", "빈 항목을 뺀 뒤"])
    measurements = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(measurements) == 2 and measurements[0] == first
    assert (first.get("note"), measurements[1]["note"]) == (None, "빈 항목을 뺀 뒤")
    # 기록의 실행 명령은 그대로 붙여 넣어 실행할 수 있게 공백이 든 인자를 따옴표로 감싼다
    assert measurements[1]["command"].endswith('--note "빈 항목을 뺀 뒤"')
    md = md_path.read_text(encoding="utf-8")
    # 이전 기록은 글자 하나 바꾸지 않고 두고 뒤에 붙인다
    assert md.startswith(first_md)
    added = md[len(first_md) :]
    for text in ("## 다시 잰 값", "빈 항목을 뺀 뒤", "### 도구 메시지 하나", "### 이전 측정과 비교"):
        assert text in added
    tokens = measurements[1]["metrics"]["sleep"]["tokens"]
    assert f"| get_daily_trend(sleep) | {tokens:,} | {tokens:,} | +0 |" in added


def test_save_doc_states_other_period_instead_of_comparison(tmp_path):
    tt.main(["--save-doc", "--doc-dir", str(tmp_path)])
    md_path = next(tmp_path.glob("*-daily-trend-tokens.md"))
    first_md = md_path.read_text(encoding="utf-8")

    tt.main(["--save-doc", "--doc-dir", str(tmp_path), "--period", "last_7_days"])
    added = md_path.read_text(encoding="utf-8")[len(first_md) :]
    # 기간이 다르면 추정 토큰을 비교할 수 없어 비교 표 대신 다른 조건을 표 위에 적는다
    assert "### 이전 측정과 비교" not in added
    line = next(line for line in added.splitlines() if "다른 조건" in line)
    assert "기간 `last_30_days` (2026-09-24~2026-10-23) → `last_7_days` (2026-10-17~2026-10-23)" in line
    assert "rest_ratio" not in line and "비율 맞추기" not in line
    assert added.index(line) < added.index("### 도구 메시지 하나")


async def test_appended_record_compares_only_with_same_ratio_and_calibration():
    async def report(measured_at, rest_ratio, calibration=None):
        return {
            "measured_at": measured_at,
            "commit": "0000000",
            "command": "python -m bench.trend_tokens --save-doc",
            "note": None,
            "tools_sha256": "000000000000",
            "calibration": calibration,
            **await tt.measure("last_30_days", rest_ratio=rest_ratio),
        }

    previous = await report("2026-10-26T10:00:00", 0.544)
    same = tt.to_markdown(await report("2026-10-26T11:00:00", 0.544), level=2, previous=previous)
    assert "### 이전 측정과 비교" in same and "다른 조건" not in same

    pair = {
        "question": "default",
        "condition": "new",
        "longer": "last_30_days",
        "shorter": "last_7_days",
        "token_diff": 100,
        "digit_diff": 10,
        "other_diff": 150,
        "ratio": 0.6,
    }
    calibration = {"records": ["eval/results/x_records.jsonl"], "pairs": [pair], "rest_ratio": 0.6}
    md = tt.to_markdown(await report("2026-10-26T12:00:00", 0.6, calibration), level=2, previous=previous)
    # 비율이나 비율 맞추기 여부가 달라도 추정 토큰을 비교할 수 없다
    assert "### 이전 측정과 비교" not in md
    [line] = [line for line in md.splitlines() if "다른 조건" in line]
    assert "rest_ratio 0.544 → 0.6" in line and "비율 맞추기 안 함 → 함" in line and "기간" not in line


def test_save_doc_keeps_single_report_record_as_first_measurement(tmp_path):
    tt.main(["--save-doc", "--doc-dir", str(tmp_path)])
    json_path = next(tmp_path.glob("*-daily-trend-tokens.json"))
    [report] = json.loads(json_path.read_text(encoding="utf-8"))
    # 측정 목록으로 저장하기 전 기록은 측정 하나를 dict 로 담는다
    json_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    tt.main(["--save-doc", "--doc-dir", str(tmp_path)])
    measurements = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(measurements) == 2 and measurements[0] == report


def test_save_doc_stops_when_only_one_record_file_exists(tmp_path):
    tt.main(["--save-doc", "--doc-dir", str(tmp_path)])
    next(tmp_path.glob("*-daily-trend-tokens.md")).unlink()
    json_path = next(tmp_path.glob("*-daily-trend-tokens.json"))
    saved = json_path.read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="하나만"):
        tt.main(["--save-doc", "--doc-dir", str(tmp_path)])
    assert json_path.read_text(encoding="utf-8") == saved
