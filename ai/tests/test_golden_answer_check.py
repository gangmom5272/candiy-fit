"""코칭 골든셋 정답 검사(eval/golden/answer_check.py) 테스트. LLM 을 호출하지 않는다."""

import dataclasses
import datetime as dt
import json

import pytest

from eval.golden import answer_check as ac
from eval.golden import fixtures as gf
from eval.golden import runner as gr
from eval.golden.items import load_sets

TODAY = dt.date(2026, 10, 22)
ITEMS = {item.id: item for item in load_sets(["v1", "v2"])[0]}
LATEST = {item.id: item for item in load_sets(["v1.1", "v2.1"])[0]}
COMMIT = "abc1234"


@pytest.fixture(autouse=True)
def fixed_commit(monkeypatch):
    monkeypatch.setattr(gr, "git_commit", lambda: COMMIT)


@pytest.fixture(scope="module")
def fixtures():
    return gf.load_fixtures({item.persona for item in ITEMS.values()})


async def check(item, fixtures):
    return (await ac.check_answers([item], fixtures, TODAY))["items"][0]


@pytest.mark.parametrize(
    ("question", "metrics", "periods"),
    [
        ("어제 몇 걸음 걸었어?", ["steps"], ["yesterday"]),
        ("오늘 지금까지 몇 걸음 걸었어?", ["steps"], ["today"]),
        # 수면·산소포화도의 어젯밤은 오늘 깬 수면, 그저께 밤은 어제 깬 수면이다
        ("어젯밤 잠은 몇 시간 잤어?", ["sleep"], ["today"]),
        ("어젯밤 잘 때 산소포화도 괜찮았어?", ["oxygen_saturation"], ["today"]),
        ("그저께 밤에는 몇 시에 잤어?", ["sleep"], ["yesterday"]),
        ("지난주보다 이번 주에 더 많이 걸었어?", ["steps"], ["last_week", "this_week"]),
        ("최근 30일 중에 제일 많이 걸은 날이 언제야?", ["steps"], ["last_30_days"]),
        ("최근 한 달 동안 수면 기록 있는 날이 며칠이야?", ["sleep"], ["last_30_days"]),
        # "잘 때"는 수면 지표가 아니다
        ("잘 때 산소포화도 최근 2주 평균 알려 줘", ["oxygen_saturation"], ["last_14_days"]),
        ("요즘 심박수 평균 얼마야? 최근 일주일로", ["heart_rate"], ["last_7_days"]),
        ("최근 2주 중에 가장 오래 잔 날은?", ["sleep"], ["last_14_days"]),
        ("이번 달이 지난달보다 잠을 덜 잤어?", ["sleep"], ["this_month", "last_month"]),
        (
            "잠을 적게 자서 그런지 산소포화도도 낮아? 최근 일주일 알려 줘",
            ["sleep", "oxygen_saturation"],
            ["last_7_days"],
        ),
        ("이번 주도 하루 3천 보도 못 걸었어", ["steps"], ["this_week"]),
        ("지지난주 평균 걸음 알려 줘", ["steps"], ["week_before_last"]),
    ],
)
def test_read_question_finds_metrics_and_periods(question, metrics, periods):
    reading = ac.read_question(question, TODAY)
    assert list(reading.metrics) == metrics
    assert list(reading.periods) == periods
    assert reading.ambiguous == ()


def test_read_question_marks_implicit_conventional_and_dated_readings():
    compared = ac.read_question("지난주보다 걸음 수 늘었어?", TODAY)
    assert (compared.periods, compared.implicit) == (("last_week",), ("this_week",))
    recent = ac.read_question("요즘 기록이 왜 이렇게 띄엄띄엄해?", TODAY)
    assert (recent.metrics, recent.periods, recent.conventions) == ((), (), ("last_7_days",))
    sunday = ac.read_question("지난 일요일 아침에 몇 시에 일어났어?", TODAY)
    assert (sunday.metrics, sunday.periods, sunday.dates) == (("sleep",), (), (dt.date(2026, 10, 18),))
    # 수면은 깬 날짜로 묶여 "금요일 밤 잠"은 토요일이다
    night = ac.read_question("지난 금요일 밤 잠은 몇 시간이었어?", TODAY)
    assert night.dates == (dt.date(2026, 10, 17),)


@pytest.mark.parametrize(
    ("question", "expression"),
    [
        ("어제도 자정 넘어서 잤어", "어제"),
        ("주말에 몰아서 자는 거 괜찮은 거야?", "주말"),
    ],
)
def test_read_question_reports_two_readings(question, expression):
    reading = ac.read_question(question, TODAY)
    assert any(expression in note for note in reading.ambiguous)


def test_read_question_without_metric_or_period():
    assert ac.read_question("지난주보다 이번 주 나아진 거 맞아?", TODAY).metrics == ()
    reading = ac.read_question("평일엔 거의 안 움직이네…", TODAY)
    assert (reading.metrics, reading.periods, reading.conventions, reading.dates) == (("steps",), (), (), ())


async def test_fact_is_recomputed_from_fixture_prototype_and_tool_result(fixtures):
    row = await check(ITEMS["golden-synth-01-005"], fixtures)
    assert row["problems"] == [] and row["undetermined"] == [] and row["two_readings"] == []
    avg, base, diff = row["facts"]
    assert (avg["expected_display"], avg["prototype_display"], avg["tool_display"]) == ("9788", "9788", "9788")
    assert (diff["expected"], diff["prototype"], diff["tool"]) == (712.67, 712.67, 713.0)
    assert all(f["verdict"] == "ok" for f in row["facts"])


async def test_value_differing_from_fixture_is_a_problem(fixtures):
    item = ITEMS["golden-synth-01-001"]
    changed = dataclasses.replace(item, facts=(dataclasses.replace(item.facts[0], value=10111),))
    row = await check(changed, fixtures)
    [fact] = row["facts"]
    assert (fact["verdict"], fact["reasons"], fact["prototype_display"]) == ("problem", ["value_mismatch"], "10110")
    assert row["problems"] == ["value_mismatch"]


async def test_values_are_compared_after_display_rounding(fixtures):
    # 9787.67 과 9787.6 은 둘 다 9,788걸음으로 보인다
    item = ITEMS["golden-synth-01-005"]
    first = dataclasses.replace(item.facts[0], value=9787.6)
    row = await check(dataclasses.replace(item, facts=(first, *item.facts[1:])), fixtures)
    assert row["problems"] == []


async def test_value_missing_from_tool_result_is_separated_when_required(fixtures):
    row = await check(ITEMS["golden-synth-edge-001"], fixtures)
    value, measured = row["facts"]
    assert value["verdict"] == "ok"
    assert (measured["verdict"], measured["reasons"], measured["tool"]) == ("requires", ["tool_missing"], None)
    assert row["problems"] == []
    measured_fact = ITEMS["golden-synth-edge-001"].facts[1]
    assert measured_fact.requires
    plain = dataclasses.replace(measured_fact, requires=())
    row = await check(dataclasses.replace(ITEMS["golden-synth-edge-001"], facts=(plain,)), fixtures)
    assert row["problems"] == ["tool_missing"]


async def test_dated_and_sleep_time_facts(fixtures):
    for item_id in ("golden-synth-03-008", "golden-synth-07-008", "golden-synth-edge-004", "golden-synth-edge-007"):
        row = await check(ITEMS[item_id], fixtures)
        assert row["problems"] == [], item_id
    row = await check(ITEMS["golden-synth-03-008"], fixtures)
    assert (row["facts"][0]["prototype"], row["facts"][0]["tool"]) == ("02:21", "02:21")


async def test_question_period_differing_from_answer_period(fixtures):
    item = ITEMS["golden-synth-01-001"]
    changed = dataclasses.replace(item, question="오늘 몇 걸음 걸었어?")
    row = await check(changed, fixtures)
    assert row["problems"] == ["period_mismatch"]
    assert row["facts"][0]["question_periods"] == ["today"]


async def test_question_metric_differing_from_answer_metric(fixtures):
    item = ITEMS["golden-synth-01-001"]
    row = await check(dataclasses.replace(item, question="어제 몇 시간 잤어?"), fixtures)
    assert row["problems"] == ["metric_mismatch"]


async def test_question_period_value_is_shown_for_mismatch(fixtures):
    item = ITEMS["golden2-c01"]
    row = await check(item, fixtures)
    [fact] = row["facts"]
    assert "period_mismatch" in fact["reasons"]
    # 질문의 "이번 주"로 다시 계산한 값을 함께 보여 준다
    assert fact["question_period"] == "this_week" and fact["question_value"] is not None


async def test_status_recomputed_and_compared(fixtures):
    item = ITEMS["golden-synth-09-005"]
    assert (await check(item, fixtures))["problems"] == []
    row = await check(dataclasses.replace(item, statuses=item.statuses[:1]), fixtures)
    assert row["problems"] == ["status_mismatch"]
    assert row["statuses"]["extra"] == [["sleep", "unsynced", ["2026-10-21", "2026-10-22"]]]


def _progress(row) -> list[list]:
    return [s for s in row["statuses"]["computed"] if s[1] == "in_progress"]


async def test_in_progress_status_follows_rule_of_the_set(fixtures):
    # v1 은 오늘이 들어간 기간이면 진행 중 상태를 붙이고, v1.1·v2.1 은 오늘을 묻는 문항에만 붙인다
    old = await check(ITEMS["golden-synth-01-005"], fixtures)
    new = await check(LATEST["golden-synth-01-005"], fixtures)
    assert _progress(old) == [["steps", "in_progress", "2026-10-22T11:55:48Z"]] and old["problems"] == []
    assert _progress(new) == [] and new["problems"] == []
    today = await check(LATEST["golden-synth-01-002"], fixtures)
    assert _progress(today) == [["steps", "in_progress", "2026-10-22T11:55:48Z"]] and today["problems"] == []
    v2 = await check(LATEST["golden2-c03"], fixtures)
    assert _progress(v2) == [] and "status_mismatch" not in v2["problems"]


async def test_latest_files_have_no_status_or_value_mismatch(fixtures):
    result = await ac.check_answers(list(LATEST.values()), fixtures, TODAY)
    for row in result["items"]:
        for fact in row["facts"]:
            assert not {"value_mismatch", "tool_mismatch"} & set(fact["reasons"]), row["id"]
        assert "status_mismatch" not in row["problems"], row["id"]


async def test_undetermined_and_two_reading_items(fixtures):
    row = await check(ITEMS["golden2-c06"], fixtures)
    assert row["undetermined"] == ["metric"]
    row = await check(ITEMS["golden2-c13"], fixtures)
    assert row["undetermined"] == ["period"]
    row = await check(ITEMS["golden2-c12"], fixtures)
    assert row["two_readings"] and row["problems"] == []
    # "요즘"은 관례상 지난 7일이다. 정답 기간이 다르면 두 가지로 읽히는 문항이다
    row = await check(ITEMS["golden2-c14"], fixtures)
    assert any("요즘" in note for note in row["two_readings"]) and "metric" in row["undetermined"]


async def test_items_without_facts_are_not_checked(fixtures):
    row = await check(ITEMS["golden2-s01"], fixtures)
    assert (row["checked"], row["problems"], row["undetermined"]) == (False, [], [])


async def test_real_files_values_match_fixtures(fixtures):
    """모든 사실의 정답 값은 고정 데이터로 다시 계산한 값과 같고, 도구 결과에 없는 값은 requires 로 표시돼 있다."""
    result = await ac.check_answers(list(ITEMS.values()), fixtures, TODAY)
    for row in result["items"]:
        for fact in row["facts"]:
            assert "value_mismatch" not in fact["reasons"], row["id"]
            assert "tool_mismatch" not in fact["reasons"], row["id"]
            assert fact["verdict"] != "problem" or fact["reasons"] != ["tool_missing"], row["id"]
    summary = result["summary"]
    assert summary["checked"] + summary["no_facts"] == len(ITEMS)
    candidates = set(summary["candidates"])
    assert candidates == {r["id"] for r in result["items"] if r["problems"] or r["two_readings"]}


def test_check_answers_command_saves_report_without_llm(tmp_path, monkeypatch):
    def fail():
        raise AssertionError("정답 검사는 LLM 설정을 읽지 않는다")

    monkeypatch.setattr(gr, "get_settings", fail)
    gr.main(["--check-answers", "--set", "all", "--save", "--out", str(tmp_path)])
    report = json.loads(next(tmp_path.glob("*_golden_all_answer_check.json")).read_text(encoding="utf-8"))
    assert report["commit"] == COMMIT and report["now"] == "2026-10-22T21:00:00+09:00"
    # all 은 최신 버전 두 파일이다
    assert [d["version"] for d in report["datasets"]] == ["v1.1", "v2.1"]
    md = next(tmp_path.glob("*_golden_all_answer_check.md")).read_text(encoding="utf-8")
    for heading in (
        "# 코칭 골든셋 정답 검사",
        "## 정답 검사에서 걸린 문항",
        "## 질문만으로 기간·지표를 정할 수 없는 문항",
        "## 질문이 두 가지로 읽히는 문항",
        "## 정답 고칠 후보",
        "## 질문 읽기 규칙",
    ):
        assert heading in md
    assert not list(tmp_path.glob("*_records.jsonl"))


@pytest.mark.parametrize(
    "argv",
    [
        ["--check-answers", "--mock"],
        ["--check-answers", "--repeat", "2"],
        ["--check-answers", "--rescore", "x_records.jsonl"],
    ],
)
def test_check_answers_does_not_take_run_options(argv):
    with pytest.raises(SystemExit):
        gr.parse_args(argv)


def test_measurement_report_separates_answer_check_and_model_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "get_settings", lambda: (_ for _ in ()).throw(AssertionError("get_settings")))
    gr.main(["--set", "v1", "--split", "all", "--mock", "--save", "--out", str(tmp_path)])
    report = json.loads(next(tmp_path.glob("*_golden_v1_mock.json")).read_text(encoding="utf-8"))
    check = report["answer_check"]["summary"]
    model = report["summary"]["scoring"]["model_mismatches"]
    md = next(tmp_path.glob("*_golden_v1_mock.md")).read_text(encoding="utf-8")
    assert "## 정답 검사에서 걸린 문항" in md and "## 모델 답과 정답이 다른 문항" in md
    # 모의 답변이 놓친 느린 걷기 안내는 모델 답 문제라 정답 고칠 후보가 아니다
    assert any(m["id"] == "golden-synth-edge-002" for m in model)
    assert "golden-synth-edge-002" not in check["candidates"]
    candidates = md.split("## 정답 고칠 후보", 1)[1].split("\n## ", 1)[0]
    assert all(item_id in candidates for item_id in check["candidates"])
    assert "golden-synth-edge-002" not in candidates
