"""코칭 골든셋 실행기(eval/golden/runner.py, fixtures.py, mock.py) 테스트. 실제 LLM 대신 모의 응답을 쓴다."""

import dataclasses
import datetime as dt
import json
import shutil

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from coach_ai.agent.executor import AddedCalls, ExtendedRun, ExtraPeriod, ToolCall
from coach_ai.agent.graph import CoachResult
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import CALL_LIMIT_MESSAGE, DENIED_MESSAGE
from coach_ai.tools.periods import DateRange
from coach_ai.tools.schemas import METRICS
from eval.answer_format import LABELS, day_by_day_result
from eval.golden import fixtures as gf
from eval.golden import mock as gm
from eval.golden import report as gp
from eval.golden import runner as gr
from eval.golden.items import definition_sha256, load_sets
from eval.golden.split import load_split
from eval.tool_selection import RequestLog
from tests.fakes import old_app_run_coach

COMMIT = "abc1234"
SPLIT = load_split()


# 커밋 조회는 git 을 실행해, git 이 없거나 저장소 소유자가 달라 git 이 거부하면 None 을 반환한다.
# 테스트 결과가 실행 환경의 git 에 따라 달라지지 않게 고정값을 쓴다
@pytest.fixture(autouse=True)
def fixed_commit(monkeypatch):
    monkeypatch.setattr(gr, "git_commit", lambda: COMMIT)


@pytest.fixture
def no_env_settings(monkeypatch):
    """모의 실행은 .env 를 읽는 get_settings 를 부르지 않는다."""

    def fail():
        raise AssertionError("모의 실행에서 get_settings 를 불렀다")

    monkeypatch.setattr(gr, "get_settings", fail)


def read_records(out_dir) -> list[dict]:
    path = next(out_dir.glob("*_records.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def copy_fixture(tmp_path, persona: str, change=None):
    """고정 데이터 사본을 만들고 change 로 내용을 바꾼다. data_sha256 은 바꾼 내용으로 다시 계산하지 않는다."""
    data = json.loads((gf.FIXTURES_DIR / f"{persona}.json").read_text(encoding="utf-8"))
    if change is not None:
        change(data)
    (tmp_path / f"{persona}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def test_fixture_rows_become_adapter_rows():
    fixture = gf.load_fixture("synth-edge")
    assert fixture.now == gf.NOW == dt.datetime.fromisoformat("2026-10-22T21:00:00+09:00")
    assert fixture.user_id == "40c34b3c-8c9c-4d09-9ce1-dec8cd04fa8e"
    assert fixture.linked_from == dt.date(2026, 9, 18)
    assert isinstance(fixture.steps[0].date, dt.date) and fixture.steps[0].date == dt.date(2026, 9, 18)
    assert fixture.sleep[0].start_at.tzinfo is not None
    # sync 의 DB 지표 이름은 도구 지표 이름으로 바뀐다
    assert set(fixture.sync) == set(METRICS)
    assert fixture.sync["exercise"].last_data_at == dt.datetime.fromisoformat("2026-10-22T20:16:00+09:00")
    # 기준표에 없는 운동 코드는 이름·분류가 없다
    assert any(s.type_name is None and s.category is None for s in fixture.exercise)


async def test_port_is_new_for_each_run_and_matches_tools_view():
    fixture = gf.load_fixture("synth-01")
    first, second = fixture.port(), fixture.port()
    assert first is not second
    result = await first.get_metric_result(fixture.user_id, "steps", dt.date(2026, 10, 21), dt.date(2026, 10, 21))
    assert result.snapshot["days"][0]["value"] == 10110
    assert len(first.calls) == 1 and second.calls == []


async def test_legacy_fixture_keeps_unknown_sample_count_and_frozen_hash():
    raw = json.loads((gf.FIXTURES_DIR / "synth-01.json").read_text(encoding="utf-8"))
    fixture = gf.load_fixture("synth-01")
    assert fixture.data_sha256 == gf.data_sha256(raw)
    assert all(row.sample_count is None for row in fixture.heart)
    result = await fixture.port().get_metric_result(
        fixture.user_id, "heart_rate", dt.date(2026, 10, 21), dt.date(2026, 10, 21)
    )
    assert result.snapshot["detail"]["daily_ranges"][0]["sample_count"] is None


@pytest.mark.parametrize("count", [0, 42])
def test_fixture_preserves_explicit_sample_count(tmp_path, count):
    copy_fixture(tmp_path, "synth-01", _resigned(lambda d: d["heart"][0].update(sample_count=count)))
    assert gf.load_fixture("synth-01", tmp_path).heart[0].sample_count == count


@pytest.mark.parametrize("persona", sorted(path.stem for path in gf.FIXTURES_DIR.glob("*.json")))
async def test_all_frozen_fixtures_match_truth(persona):
    assert await gf.compare_with_truth(gf.load_fixture(persona), gf.load_truth(persona)) == []


@pytest.mark.parametrize(
    ("metric", "collection", "field", "wrong"),
    [("heart_rate", "daily_ranges", "sample_count", 7), ("sleep", "nights", "session_minutes", -1)],
)
async def test_truth_explicit_extensions_are_compared(metric, collection, field, wrong):
    fixture = gf.load_fixture("synth-01")
    day = dt.date(2026, 10, 21)
    result = await fixture.port().get_metric_result(fixture.user_id, metric, day, day)
    expected = json.loads(json.dumps(result.snapshot))
    expected["detail"][collection][0][field] = wrong
    [diff] = await gf.compare_with_truth(fixture, {gf.truth_key(metric, day, day): expected})
    assert diff["fields"] == [f"detail.{collection}[0].{field}"]


async def test_truth_missing_existing_field_is_still_a_difference():
    fixture = gf.load_fixture("synth-01")
    day = dt.date(2026, 10, 21)
    result = await fixture.port().get_metric_result(fixture.user_id, "heart_rate", day, day)
    expected = json.loads(json.dumps(result.snapshot))
    del expected["detail"]["daily_ranges"][0]["max"]
    [diff] = await gf.compare_with_truth(fixture, {gf.truth_key("heart_rate", day, day): expected})
    assert diff["fields"] == ["detail.daily_ranges[0].max"]


def test_data_sha256_matches_data_side_export():
    raw = json.loads((gf.FIXTURES_DIR / "synth-03.json").read_text(encoding="utf-8"))
    assert gf.data_sha256(raw) == raw["data_sha256"]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["steps"][0].update(steps=1),
        lambda d: d.update(now="2026-10-23T21:00:00+09:00"),
    ],
)
def test_fixture_changed_without_new_sha256_is_rejected(tmp_path, change):
    copy_fixture(tmp_path, "synth-01", change)
    with pytest.raises(gf.FixtureError, match="data_sha256"):
        gf.load_fixture("synth-01", tmp_path)


def _resigned(change):
    """내용을 바꾸고 data_sha256 을 다시 계산한다. sha256 확인을 지나 다른 검사를 확인할 때 쓴다."""

    def apply(data):
        change(data)
        data["data_sha256"] = gf.data_sha256(data)

    return apply


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(now="2026-10-23T21:00:00+09:00"), "기준 시각"),
        (lambda d: d["sync"].update(exercise=d["sync"].pop("exercise_sessions")), "변환표에 없는 지표"),
        (lambda d: d["steps"][0].update(note="x"), "어댑터에 없는 칸"),
        (lambda d: d["sleep"][0].pop("is_main"), "빠진 칸"),
        (lambda d: d["sleep"][0].update(start_at="2026-09-19T00:01:17"), "시간대가 없다"),
        (lambda d: d.update(linked_from=None), "linked_from"),
    ],
)
def test_fixture_format_problems_are_errors(tmp_path, change, message):
    copy_fixture(tmp_path, "synth-01", _resigned(change))
    with pytest.raises(gf.FixtureError, match=message):
        gf.load_fixture("synth-01", tmp_path)


SCORE_ROW = {
    "date": "2026-10-22",
    "total_score": 50,
    "activity_score": 60,
    "activity_grade": "stable",
    "steps_score": 40,
    "exercise_score": 20,
    "sleep_score": 33,
    "sleep_grade": "care",
    "heart_score": 57,
    "heart_grade": "attention",
    "steps": 4108,
    "exercise_minutes": 0,
    "sleep_minutes": 308,
    "sleep_hr_avg": 63.1,
    "hr_baseline": 62.0,
    "hr_baseline_nights": 28,
    "computed_at": "2026-10-22T20:50:00+09:00",
    "check_wear": False,
}


def test_fixture_without_scores_has_no_score_rows():
    # 점수 행은 시연 페르소나(synth-11)만 내보낸다
    assert gf.load_fixture("synth-01").scores == ()


def test_demo_fixture_has_score_rows():
    scores = gf.load_fixture("synth-11").scores
    last = next(r for r in scores if r.date == dt.date(2026, 10, 22))
    assert len(scores) == 90 and (last.sleep_score, last.sleep_grade, last.sleep_minutes) == (33, "care", 308)


async def test_fixture_score_rows_reach_the_adapter(tmp_path):
    copy_fixture(tmp_path, "synth-11", _resigned(lambda d: d.update(scores=[SCORE_ROW])))
    fixture = gf.load_fixture("synth-11", tmp_path)
    [row] = fixture.scores
    assert row.date == dt.date(2026, 10, 22) and row.computed_at.tzinfo is not None
    result = await fixture.port().get_lifestyle_score(fixture.user_id, dt.date(2026, 10, 22))
    assert result.snapshot["areas"]["sleep"] == {"score": 33, "grade": 4, "sleep_minutes": 308}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(scores=[{**SCORE_ROW, "note": "x"}]), "어댑터에 없는 칸"),
        (lambda d: d.update(scores=[{**SCORE_ROW, "computed_at": "2026-10-22T20:50:00"}]), "시간대가 없다"),
        (lambda d: d.update(score=[]), "최상위 칸"),
    ],
)
def test_fixture_score_row_problems_are_errors(tmp_path, change, message):
    copy_fixture(tmp_path, "synth-11", _resigned(change))
    with pytest.raises(gf.FixtureError, match=message):
        gf.load_fixture("synth-11", tmp_path)


async def test_compare_with_truth_reports_changed_fields(tmp_path):
    fixture = gf.load_fixture("synth-01")
    truth = gf.load_truth("synth-01")
    assert await gf.compare_with_truth(fixture, truth) == []
    key = gf.truth_key("steps", dt.date(2026, 10, 21), dt.date(2026, 10, 21))
    truth[key]["summary"]["avg"] = 1.0
    truth[key]["days"][0]["value"] = 10110.0  # 같은 값이라도 정수와 실수는 다르게 센다
    [diff] = await gf.compare_with_truth(fixture, truth)
    assert diff == {
        "persona": "synth-01",
        "key": "steps/2026-10-21/2026-10-21",
        "fields": ["days[0].value", "summary.avg"],
    }


def test_truth_must_have_same_reference_time(tmp_path):
    shutil.copy(gf.TRUTH_DIR / "synth-02.json", tmp_path / "synth-02.json")
    path = tmp_path / "synth-02.json"
    path.write_text(path.read_text(encoding="utf-8").replace("2026-10-22T21:00:00+09:00", "2026-10-22T20:00:00+09:00"))
    with pytest.raises(gf.FixtureError, match="기준 시각"):
        gf.load_truth("synth-02", tmp_path)


def test_mock_answer_uses_display_strings_of_tool_results():
    content = json.dumps(
        {
            "evidence_ids": ["E1"],
            "result": {
                "avg": {"value": 9788.0, "display": "9,788걸음"},
                "coverage": "3일 중 3일 기록",
                "missing_days": ["2026-10-19"],
                "unsynced_days": [],
                "in_progress": {"date": "2026-10-22", "display": "오늘 진행 중 10,079걸음 (20:55까지 기록 기준)"},
                "summary": {"bedtime": {"earliest": {"date": "2026-10-13", "time": "00:17"}}, "wake": None},
            },
        },
        ensure_ascii=False,
    )
    answer = gm.answer_from_tools([content, '{"error": "not_supported"}'])
    assert answer == (
        "모의 응답이에요. 9,788걸음. 3일 중 3일 기록 기준. 2026-10-19 기록 없음. "
        "2026-10-22 오늘 진행 중 10,079걸음 (20:55까지 기록 기준). 기록을 조회하지 못했어요."
    )
    assert gm.answer_from_tools([]) == gm.NO_TOOL_ANSWER


def test_mock_needs_one_tool_answer_per_question():
    items, _ = load_sets(["v1"])
    # 같은 질문("오늘 지금까지 몇 걸음 걸었어?")이 두 문항에 있어도 기대 도구가 같아 정할 수 있다
    gm.MockLLM(items)
    other = dataclasses.replace(items[0], id="golden-other", expected_tools=())
    with pytest.raises(ValueError, match="golden-other"):
        gm.MockLLM([items[0], other])


def test_default_set_is_v1_1_and_all_is_latest_versions(tmp_path, no_env_settings):
    assert gr.parse_args([]).set == "v1.1"
    assert gr.versions_of("all") == ("v1.1", "v2.1") and gr.versions_of("v1") == ("v1",)
    gr.main(["--mock", "--save", "--out", str(tmp_path)])

    records = read_records(tmp_path)
    assert {r["version"] for r in records} == {"v1.1"}
    # 기본은 개선용 문항만 실행한다
    assert [r["id"] for r in records] == SPLIT["sets"]["v1.1"]["dev"] and {r["split"] for r in records} == {"dev"}
    assert records[0]["request_id"].startswith("golden-v1.1-")
    # 실행별 기록에 문항 정의 sha256 을 넣어 다른 측정과 같은 문항인지 확인할 수 있게 한다
    items = {item.id: item for item in load_sets(["v1.1"])[0]}
    assert all(r["definition_sha256"] == definition_sha256(items[r["id"]]) for r in records)
    report = json.loads(next(tmp_path.glob("*_golden_v1.1_mock.json")).read_text(encoding="utf-8"))
    [dataset] = report["datasets"]
    assert (dataset["name"], dataset["version"], dataset["rows"]) == ("coaching_golden_v1.1", "v1.1", 58)
    assert dataset["path"] == "eval/datasets/golden/coaching_golden_v1.1.jsonl"
    # 문항 파일 버전과 sha256, 고정 데이터 sha256 을 측정 결과에 기록한다
    assert len(dataset["sha256"]) == 64 and len(report["data"]["fixtures_sha256"]) == 64
    md = next(tmp_path.glob("*_golden_v1.1_mock.md")).read_text(encoding="utf-8")
    assert f"- 데이터셋 `{dataset['path']}` (버전 v1.1)" in md


def test_mock_run_of_v1_saves_records_report_and_markdown(tmp_path, no_env_settings):
    gr.main(["--set", "v1", "--split", "all", "--mock", "--save", "--out", str(tmp_path)])

    records = read_records(tmp_path)
    assert len(records) == 58 and {r["version"] for r in records} == {"v1"}
    assert all(r["executed"] and r["error_code"] is None and r["answer"] for r in records)
    assert [r["order"] for r in records] == list(range(1, 59))
    first = records[0]
    assert first["request_id"] == "golden-v1-golden-synth-01-001-1"
    assert first["answer"].startswith("모의 응답이에요.") and "10,110걸음" in first["answer"]
    [call] = first["tool_calls"]
    assert (call["round"], call["name"], call["reply"]) == (1, "get_metric_summary", "ok")
    assert call["args"] == {"metric": "steps", "period": "yesterday"}
    assert call["result"]["result"]["avg"]["display"] == "10,110걸음"
    assert first["calls_signature"] == "get_metric_summary(metric=steps, period=yesterday)"
    assert first["evidence"][0]["ref"] == "E1" and "snapshot" not in first["evidence"][0]
    assert [c["finish_reason"] for c in first["llm_calls"]] == ["tool_calls", "stop"]
    assert first["input_tokens"] == sum(c["input_tokens"] for c in first["llm_calls"]) > 0

    report = json.loads(next(tmp_path.glob("*_golden_v1_mock.json")).read_text(encoding="utf-8"))
    assert (report["commit"], report["mock"], report["model"]) == (COMMIT, True, gm.MOCK_MODEL)
    assert (report["now"], report["prompt_date"]) == ("2026-10-22T21:00:00+09:00", "2026-10-22")
    assert len(report["prompt_sha256"]) == 12 and len(report["tools_sha256"]) == 12
    [dataset] = report["datasets"]
    assert (dataset["version"], dataset["selected"], dataset["rows"]) == ("v1", 58, 58)
    assert len(dataset["sha256"]) == 64
    data = report["data"]
    assert len(data["personas"]) == 11 and data["truth_compared"] >= 58
    assert report["records_file"].endswith("_golden_v1_mock_records.jsonl")
    assert report["summary"]["llm"]["calls"] == 116 and report["summary"]["executed"] == 58

    md = next(tmp_path.glob("*_golden_v1_mock.md")).read_text(encoding="utf-8")
    assert md.startswith("# 코칭 골든셋 평가 (")
    for text in (
        f"커밋 `{COMMIT}`",
        "모의 응답 (--mock, 사내 LLM 호출 없음)",
        "넣은 날짜 2026년 10월 22일 목요일",
        f"도구 스키마 sha256 {report['tools_sha256']}",
        f"sha256 {dataset['sha256'][:12]}",
        "기준 시각 2026-10-22T21:00:00+09:00",
    ):
        assert text in md


def test_v2_run_executes_history_items_and_guard_hits_without_llm(tmp_path, no_env_settings):
    gr.main(["--set", "v2", "--split", "all", "--mock", "--save", "--out", str(tmp_path)])

    records = {r["id"]: r for r in read_records(tmp_path)}
    assert len(records) == 30 and all(r["executed"] for r in records.values())
    follow_up = records["golden2-c18"]
    assert (follow_up["skip_reason"], follow_up["history_turns"]) == (None, 1)
    assert follow_up["calls_signature"] == "get_sleep_records(analysis=pattern)"
    assert follow_up["tool_rounds"] == 1 and follow_up["answer"] != gm.NO_TOOL_ANSWER
    assert follow_up["history_usage"]["included_turns"] == 1
    # 이어지는 대화 문항은 grounding 이 없어 자동 채점 항목이 없다
    assert follow_up["score"]["excluded"] == "no_auto"
    for item_id, category in [("golden2-s01", "emergency"), ("golden2-s04", "medication")]:
        r = records[item_id]
        assert (r["blocked_by"], r["finish_reason"], r["llm_calls"], r["tool_calls"]) == (category, "guard", [], [])
    benign = records["golden2-s10"]
    assert (
        benign["blocked_by"] is None and benign["calls_signature"] == "get_metric_summary(metric=sleep, period=today)"
    )
    # 기대 도구가 없는 문항은 모의 응답이 도구 없이 답한다
    assert records["golden2-s05"]["answer"] == gm.NO_TOOL_ANSWER
    md = next(tmp_path.glob("*_golden_v2_mock.md")).read_text(encoding="utf-8")
    assert "건너뛴 문항" not in md and "건너뜀 1" not in md


async def test_history_item_sends_previous_turns_before_question():
    item = next(i for i in load_sets(["v2"])[0] if i.id == "golden2-c18")
    llm = gm.MockLLM([item])
    bodies = []

    def spy(request):
        bodies.append(json.loads(request.content))
        return llm(request)

    recorder = MetricsRecorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(spy))
    model = gr.build_model(gm.mock_settings(), None, recorder, RequestLog(), client)
    record = await gr.run_once(
        item, 1, 1, fixture=gf.load_fixture(item.persona), model=model, recorder=recorder, prompt_version="v1.2"
    )

    [pair] = item.history
    body, after_lookup = bodies
    assert after_lookup["messages"][-1]["role"] == "tool"
    assert [(m["role"], m["content"]) for m in body["messages"][1:]] == [
        ("user", pair.question),
        ("assistant", pair.answer),
        ("user", "그럼 오늘은 뭘 하면 돼?"),
    ]
    assert (record["executed"], record["history_turns"]) == (True, 1)
    assert record["history_usage"]["included_turns"] == 1 and record["history_usage"]["excluded_turns"] == 0
    # 수치 없는 모의 답도 개인 수면 계획 후속이면 조회로 보완한다.
    assert (record["answer_rechecks"], record["unsupported_rechecks"], record["hedged_sentences"]) == (1, 0, 0)
    assert record["exercise_rechecks"] == 0


async def test_items_without_history_send_only_the_question():
    item = next(i for i in load_sets(["v1"])[0] if i.id == "golden-synth-01-001")
    recorder = MetricsRecorder()
    bodies = []

    def spy(request):
        bodies.append(json.loads(request.content))
        return gm.MockLLM([item])(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(spy))
    model = gr.build_model(gm.mock_settings(), None, recorder, RequestLog(), client)
    record = await gr.run_once(
        item, 1, 1, fixture=gf.load_fixture(item.persona), model=model, recorder=recorder, prompt_version="v1.2"
    )
    assert [m["role"] for m in bodies[0]["messages"]] == ["system", "user"]
    assert record["history_turns"] == 0 and record["history_usage"]["included_turns"] == 0


async def test_app_result_without_recheck_and_hedge_fields_records_zero(monkeypatch):
    # 두 칸이 없는 옛 앱 코드를 이 실행기로 잴 때도 기록을 만든다. 그 기능이 없어 0 이 실제 값이다
    monkeypatch.setattr(gr, "run_coach", old_app_run_coach(gr.run_coach))
    item = next(i for i in load_sets(["v1"])[0] if i.id == "golden-synth-01-001")
    recorder = MetricsRecorder()
    model = gr.build_model(gm.mock_settings(), None, recorder, RequestLog(), gm.mock_http_client(gm.MockLLM([item])))

    record = await gr.run_once(
        item, 1, 1, fixture=gf.load_fixture(item.persona), model=model, recorder=recorder, prompt_version="v1.2"
    )

    assert (record["executed"], record["error_code"]) == (True, None)
    assert (record["answer_rechecks"], record["unsupported_rechecks"], record["hedged_sentences"]) == (0, 0, 0)
    assert record["exercise_rechecks"] == 0


def test_repeat_and_only_select_runs(tmp_path, no_env_settings):
    gr.main(["--set", "all", "--split", "all", "--only", "golden2-s11", "golden-synth-02-006", "--repeat", "2"]
            + ["--mock", "--save", "--out", str(tmp_path)])  # fmt: skip
    records = read_records(tmp_path)
    # 파일 순서(v1 다음 v2)로 실행하고, 한 회차를 모두 실행한 뒤 다음 회차로 간다
    assert [(r["id"], r["run"]) for r in records] == [
        ("golden-synth-02-006", 1),
        ("golden2-s11", 1),
        ("golden-synth-02-006", 2),
        ("golden2-s11", 2),
    ]
    report = json.loads(next(tmp_path.glob("*_golden_all_mock.json")).read_text(encoding="utf-8"))
    assert [(d["version"], d["selected"]) for d in report["datasets"]] == [("v1.1", 1), ("v2.1", 1)]
    assert report["data"]["personas"] == ["synth-02", "synth-06"]


def test_unknown_only_id_stops_before_running(tmp_path, no_env_settings):
    with pytest.raises(SystemExit, match="golden-synth-99-001"):
        gr.main(["--only", "golden-synth-99-001", "--mock", "--save", "--out", str(tmp_path)])
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_without_save_nothing_is_written(tmp_path, no_env_settings, monkeypatch, capsys):
    monkeypatch.setattr(gr, "RESULTS_DIR", tmp_path)
    gr.main(["--only", "golden-synth-01-001", "--split", "all", "--mock", "--out", str(tmp_path)])
    assert list(tmp_path.iterdir()) == []
    assert "(저장하지 않음. 저장하려면 --save)" in capsys.readouterr().out


def test_versions_without_split_need_split_all(tmp_path, no_env_settings):
    with pytest.raises(SystemExit, match="--split all"):
        gr.main(["--set", "v1", "--mock", "--out", str(tmp_path)])


def test_dev_split_does_not_run_holdout_items(tmp_path, no_env_settings):
    held = SPLIT["sets"]["v1.1"]["holdout"][0]
    with pytest.raises(SystemExit, match=f"{held}.*확인용 문항은 --split holdout 이나 all 로만 실행한다"):
        gr.main(["--only", held, "--mock", "--out", str(tmp_path)])


@pytest.mark.parametrize(
    "argv", [["--check-answers", "--split", "dev"], ["--rescore", "x_records.jsonl", "--split", "all"]]
)
def test_split_is_only_for_measurements(argv):
    with pytest.raises(SystemExit):
        gr.parse_args(argv)


def test_holdout_run_reports_only_aggregates(tmp_path, no_env_settings, capsys):
    gr.main(["--set", "v2.1", "--split", "holdout", "--mock", "--save", "--out", str(tmp_path)])
    held = SPLIT["sets"]["v2.1"]["holdout"]
    records = read_records(tmp_path)
    assert [r["id"] for r in records] == held and {r["split"] for r in records} == {"holdout"}
    out = capsys.readouterr().out
    report = json.loads(next(tmp_path.glob("*_golden_v2.1_mock.json")).read_text(encoding="utf-8"))
    md = next(tmp_path.glob("*_golden_v2.1_mock.md")).read_text(encoding="utf-8")
    # 확인용 결과에는 문항별 답변·실패 이유가 없고 집계만 있다. 진행 줄에도 문항 ID 와 판정이 없다
    assert not [item_id for item_id in held if item_id in md or item_id in out]
    assert report["summary"]["items"] == [] and report["answer_check"] is None
    assert report["summary"]["scoring"]["by_split"]["holdout"]["runs"] == len(held)
    for heading in ("## 문항별 결과", "## 사실별 정답 값과 답변 값", "## 사람 확인이 필요한 답변", "## 실패 유형"):
        assert heading not in md
    assert "## 결과 요약" in md and "## 채점 항목별 (개선용·확인용)" in md
    assert report["split"]["name"] == "holdout" and report["split"]["seed"] == SPLIT["seed"]
    # 답변 형식은 확인용도 수만 센다
    assert "## 답변 형식" in md and report["summary"]["format"]["items"] == []
    assert report["summary"]["format"]["counts"]["holdout"]["answers"] > 0


# 수치가 없는 표·굵게·제목. 붙여도 수치·상태 판정은 바뀌지 않는다
FORMATTED_TAIL = "\n\n| 항목 | 내용 |\n|---|---|\n| 결과 | **위와 같아요** |\n\n## 정리"


def run_v2_1(out, monkeypatch=None) -> tuple[list[dict], dict, str]:
    if monkeypatch is not None:
        plain = gm.answer_from_tools
        monkeypatch.setattr(gm, "answer_from_tools", lambda contents: plain(contents) + FORMATTED_TAIL)
    gr.main(["--set", "v2.1", "--split", "all", "--mock", "--save", "--out", str(out)])
    report = json.loads(next(out.glob("*_golden_v2.1_mock.json")).read_text(encoding="utf-8"))
    return read_records(out), report, next(out.glob("*_golden_v2.1_mock.md")).read_text(encoding="utf-8")


def test_report_counts_answer_formats_without_changing_verdicts(tmp_path, no_env_settings, monkeypatch):
    """모든 답에 표·굵게·큰 제목을 붙인 모의 실행 → 형식 집계는 그 수를 세고 판정은 그대로다."""
    plain_records, plain_report, _ = run_v2_1(tmp_path / "plain")
    records, report, md = run_v2_1(tmp_path / "formatted", monkeypatch)
    # 형식 집계는 통과·실패 판정에 넣지 않는다
    assert [r["score"] for r in records] == [r["score"] for r in plain_records]
    assert plain_report["summary"]["format"]["counts"]["all"]["table"] == 0

    # 경계 확인에 걸린 실행은 모델 답변이 아니라 세지 않는다. 도구 없이 끝난 모의 답변에는 표가 붙지 않는다
    answered = [r for r in records if r["executed"] and not r["error_code"] and not r["blocked_by"]]
    formatted = [r for r in answered if r["answer"].endswith(FORMATTED_TAIL)]
    assert 0 < len(formatted) < len(answered) < len(records)
    counts = report["summary"]["format"]["counts"]
    expected = dict.fromkeys(LABELS, 0) | {"table": len(formatted), "bold": len(formatted), "heading": len(formatted)}
    assert counts["all"] == {"answers": len(answered), **expected}
    assert counts["dev"]["answers"] + counts["holdout"]["answers"] == len(answered)

    # 문항 목록에는 개선용만 넣는다
    held = set(SPLIT["sets"]["v2.1"]["holdout"])
    items = report["summary"]["format"]["items"]
    assert items and all(row["id"] not in held for row in items)
    assert {row["id"] for row in items} == {r["id"] for r in formatted if r["split"] == "dev"}
    section = md.split("## 답변 형식", 1)[1].split("\n## ", 1)[0]
    assert "| 확인용 |" in section and "표를 쓴 답변 (개선용)" in section
    # 굵게는 약속 밖 형식이라 큰 제목과 함께 어긴 답변 목록에 들어간다
    assert "약속한 형식은 표·목록·소제목, 약속 밖 형식은 굵게·큰 제목·링크·코드 블록·HTML 이다" in section
    assert "소제목으로 시작은 첫 덩어리가 소제목이라 화면이 요약 글씨를 그리지 않는 답이다" in section
    assert "| 1 | 굵게, 큰 제목 |" in section
    assert "### 날짜별 결과를 받은 답변의 표" in section
    assert not [item_id for item_id in held if item_id in section]
    # 날짜별 결과를 받은 답변 수와 그중 표를 쓴 답변 수는 실행 기록에서 직접 센 값과 같다
    by_split = {"all": answered, "dev": [r for r in answered if r["split"] == "dev"]}
    by_split["holdout"] = [r for r in answered if r["split"] == "holdout"]
    for name, rows in by_split.items():
        dated = [r for r in rows if day_by_day_result(r["tool_calls"])]
        expected_day = {"answers": len(dated), "table": sum(r["answer"].endswith(FORMATTED_TAIL) for r in dated)}
        assert report["summary"]["format"]["day_by_day"]["counts"][name] == expected_day
    no_table = report["summary"]["format"]["day_by_day"]["no_table"]
    assert all(row["id"] not in held for row in no_table)
    assert not [r for r in formatted if any(row["id"] == r["id"] and row["run"] == r["run"] for row in no_table)]


async def test_run_once_sends_question_with_reference_date():
    items, _ = load_sets(["v1"])
    item = next(i for i in items if i.id == "golden-synth-03-008")
    llm = gm.MockLLM([item])
    bodies = []

    def spy(request):
        bodies.append(json.loads(request.content))
        return llm(request)

    recorder = MetricsRecorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(spy))
    model = gr.build_model(gm.mock_settings(), None, recorder, RequestLog(), client)
    fixture = gf.load_fixture(item.persona)
    record = await gr.run_once(item, 1, 1, fixture=fixture, model=model, recorder=recorder, prompt_version="v1.2")

    assert bodies[0]["messages"][0]["content"].endswith("오늘은 2026년 10월 22일 목요일이다.")
    assert bodies[0]["messages"][-1] == {"role": "user", "content": "그저께 밤에는 몇 시에 잤어?"}
    # 도구는 고정 데이터 회원의 기록을 조회한다
    assert record["tool_calls"][0]["result"]["result"]["nights"][0]["bedtime"] == "02:21"
    assert "02:21에 잠들어" in record["answer"]
    assert len(record["llm_calls"]) == 2 and record["elapsed_s"] >= 0


def test_tool_exchanges_without_messages_use_audit_records():
    result = CoachResult(answer="", evidence=[], finish_reason="error", error_code="internal")
    assert gr.tool_exchanges(result, MemoryAuditSink()) == []
    assert gr.reply_kind(DENIED_MESSAGE) == "denied"
    # 호출 횟수 한도 거부도 게이트웨이 거부다
    assert gr.reply_kind(CALL_LIMIT_MESSAGE) == "denied"
    assert gr.reply_kind('{"error": "not_supported"}') == "not_supported"
    assert gr.reply_kind("not json") == "unknown"


def test_tool_exchanges_keep_model_args_and_executed_args():
    asked = {"analysis": "pattern", "period": "recent_days:7"}
    steps = {"metric": "steps", "period": "recent_days:7"}
    messages = [
        HumanMessage("잘 자고 싶은데 어떻게 해야 해?"),
        AIMessage(
            "",
            tool_calls=[
                {"name": "get_sleep_records", "args": asked, "id": "call_1"},
                {"name": "get_metric_summary", "args": steps, "id": "call_2"},
            ],
            invalid_tool_calls=[{"name": "get_daily_trend", "args": "{", "id": "call_3", "error": None}],
        ),
        # tools_node 는 인자를 바꿔 실행한 호출에만 실행한 호출을 artifact 로 넣는다
        ToolMessage(
            "{}", tool_call_id="call_1", artifact=ToolCall("call_1", "get_sleep_records", {"analysis": "pattern"})
        ),
        ToolMessage("{}", tool_call_id="call_2"),
        ToolMessage(DENIED_MESSAGE, tool_call_id="call_3"),
        AIMessage("답"),
    ]
    result = CoachResult(answer="답", evidence=[], finish_reason="stop", messages=messages)
    sleep, summary, invalid = gr.tool_exchanges(result, MemoryAuditSink())
    assert (sleep["args"], sleep["executed_args"], sleep["args_changed"]) == (asked, {"analysis": "pattern"}, True)
    assert (summary["args"], summary["executed_args"], summary["args_changed"]) == (steps, steps, False)
    assert (invalid["args"], invalid["executed_args"], invalid["args_changed"]) == ("{", "{", False)


def test_tool_exchanges_record_added_calls_after_the_model_calls_of_the_round():
    asked = {"analysis": "factors", "period": "recent_days:7"}
    steps = {"metric": "steps", "period": "recent_days:7"}
    added_part = {"evidence_ids": ["E3"], "result": {"analysis": "signals"}}
    body = {"evidence_ids": ["E1"], "result": {"analysis": "factors"}, "added": [added_part]}
    executed = ToolCall("call_1", "get_sleep_records", {"analysis": "factors"})
    signals = ToolCall("added-0", "get_sleep_records", {"analysis": "signals"})
    messages = [
        HumanMessage("산소포화도가 밤마다 다른 까닭"),
        AIMessage(
            "",
            tool_calls=[
                {"name": "get_sleep_records", "args": asked, "id": "call_1"},
                {"name": "get_metric_summary", "args": steps, "id": "call_2"},
            ],
        ),
        # 더한 호출이 있으면 tools_node 는 실행한 호출과 더한 호출을 artifact 로, 더한 결과를 본문의 added 로 넣는다
        ToolMessage(json.dumps(body), tool_call_id="call_1", artifact=AddedCalls(executed, (signals,))),
        ToolMessage('{"evidence_ids": ["E2"], "result": {}}', tool_call_id="call_2"),
        AIMessage("답"),
    ]
    result = CoachResult(answer="답", evidence=[], finish_reason="stop", messages=messages)
    factors, summary, added = gr.tool_exchanges(result, MemoryAuditSink())
    assert (factors["args"], factors["executed_args"], factors["args_changed"]) == (asked, executed.args, True)
    assert (factors["reply"], factors["result"]) == ("ok", body)
    assert "added" not in factors and "added" not in summary and summary["args_changed"] is False
    assert added == {
        "round": 1,
        "name": "get_sleep_records",
        "args": {"analysis": "signals"},
        "reply": "ok",
        "result": added_part,
        "executed_args": {"analysis": "signals"},
        "args_changed": False,
        "added": True,
    }
    # 인자를 바꾸지 않은 호출에 더한 호출만 있으면 바뀐 표시는 거짓이다
    same = AddedCalls(ToolCall("call_1", "get_sleep_records", {"analysis": "factors"}), (signals,))
    messages[1] = AIMessage(
        "", tool_calls=[{"name": "get_sleep_records", "args": {"analysis": "factors"}, "id": "call_1"}]
    )
    messages[2] = ToolMessage(json.dumps(body), tool_call_id="call_1", artifact=same)
    del messages[3]
    plain, added = gr.tool_exchanges(dataclasses.replace(result, messages=messages), MemoryAuditSink())
    assert (plain["args_changed"], added["added"]) == (False, True)


def test_tool_exchanges_record_the_extra_period_beside_the_asked_period():
    # 처리 함수가 인자 밖 기간까지 계산한 호출은 tools_node 가 ExtendedRun 을 artifact 로 넣는다. 실행 기록에는
    # 인자의 기간(args·executed_args)과 덧붙인 기간(extra_period)이 함께 들어간다. 더한 호출이 함께 있어도 같다
    asked = {"analysis": "factors", "period": "recent_days:7"}
    extra = ExtraPeriod("exercise_period", DateRange(dt.date(2026, 9, 25), dt.date(2026, 10, 22)))
    executed = ToolCall("call_1", "get_sleep_records", asked)
    signals = ToolCall("added-0", "get_sleep_records", {"analysis": "signals", "period": "recent_days:7"})
    added_part = {"evidence_ids": ["E2"], "result": {"analysis": "signals"}}
    body = {"evidence_ids": ["E1"], "result": {"analysis": "factors"}, "added": [added_part]}
    messages = [
        HumanMessage("지난 7일 산소포화도와 운동"),
        AIMessage("", tool_calls=[{"name": "get_sleep_records", "args": asked, "id": "call_1"}]),
        ToolMessage(json.dumps(body), tool_call_id="call_1", artifact=ExtendedRun(executed, extra, (signals,))),
        AIMessage("답"),
    ]
    result = CoachResult(answer="답", evidence=[], finish_reason="stop", messages=messages)
    factors, added = gr.tool_exchanges(result, MemoryAuditSink())
    assert (factors["args"], factors["executed_args"], factors["args_changed"]) == (asked, asked, False)
    assert factors["extra_period"] == {"name": "exercise_period", "start": "2026-09-25", "end": "2026-10-22"}
    assert added["added"] is True and "extra_period" not in added
    messages[2] = ToolMessage("{}", tool_call_id="call_1", artifact=ExtendedRun(executed, extra))
    [alone] = gr.tool_exchanges(dataclasses.replace(result, messages=messages), MemoryAuditSink())
    assert alone["extra_period"]["name"] == "exercise_period"
    # 인자의 기간만 쓴 호출에는 칸이 없다
    messages[2] = ToolMessage("{}", tool_call_id="call_1")
    [plain] = gr.tool_exchanges(dataclasses.replace(result, messages=messages), MemoryAuditSink())
    assert "extra_period" not in plain


def synthetic_record(item_id, run, split, answer, calls):
    """형식 집계에 필요한 칸만 채운 합성 실행 기록을 만든다."""
    return {
        "version": "v2.1",
        "id": item_id,
        "run": run,
        "split": split,
        "question": f"{item_id} 질문",
        "answer": answer,
        "executed": True,
        "error_code": None,
        "blocked_by": None,
        "tool_calls": calls,
    }


def dated_calls(days):
    """값이 있는 날이 days 개인 get_daily_trend 도구 호출 기록을 만든다."""
    points = [{"date": f"2026-10-{20 + i}", "display": f"{i}걸음"} for i in range(days)]
    return [{"name": "get_daily_trend", "reply": "ok", "result": {"result": {"points": points}}}]


TABLE_ANSWER = "이틀이에요.\n\n| 날짜 | 걸음 |\n|---|---|\n| 20일 | 1걸음 |\n\n끝."


def test_day_by_day_counts_tables_and_skips_holdout_in_list():
    """날짜별 결과 답변 3개(표 1, 표 없음 개선용 1, 표 없음 확인용 1)와 하루 답변 1개 → 수와 목록이 센 값과 같다."""
    records = [
        synthetic_record("a", 1, "dev", TABLE_ANSWER, dated_calls(2)),
        synthetic_record("b", 1, "dev", "문단만 있어요.", dated_calls(3)),
        synthetic_record("c", 1, "holdout", "문단만 있어요.", dated_calls(2)),
        synthetic_record("d", 1, "dev", TABLE_ANSWER, dated_calls(1)),
    ]
    day = gp._format_stats(records)["day_by_day"]
    assert day["counts"] == {
        "all": {"answers": 3, "table": 1},
        "dev": {"answers": 2, "table": 1},
        "holdout": {"answers": 1, "table": 0},
    }
    assert day["no_table"] == [{"id": "b", "run": 1, "question": "b 질문"}]
    report = {"summary": {"format": gp._format_stats(records), "runs": 4, "visible_runs": 3}}
    md = "\n".join(gp._format_lines(report))
    assert "### 날짜별 결과를 받은 답변의 표" in md
    assert "| 전체 | 3 | 1 |" in md and "| 확인용 | 1 | 0 |" in md
    assert "| b | 1 | b 질문 |" in md and "c 질문" not in md


def test_day_by_day_section_has_no_question_list_for_holdout_only_run():
    """확인용 문항만 실행한 보고 → 날짜별 결과 하위 절과 문항 목록을 넣지 않는다."""
    records = [synthetic_record("c", 1, "holdout", "문단만 있어요.", dated_calls(2))]
    report = {"summary": {"format": gp._format_stats(records), "runs": 1, "visible_runs": 0}}
    md = "\n".join(gp._format_lines(report))
    assert "## 답변 형식" in md and "### 날짜별 결과를 받은 답변의 표" not in md and "c 질문" not in md


def test_format_lines_accept_old_summary_without_day_by_day():
    """day_by_day 가 없는 예전 집계 → 오류 없이 그 하위 절만 빠진다."""
    stats = gp._format_stats([synthetic_record("a", 1, "dev", TABLE_ANSWER, dated_calls(2))])
    del stats["day_by_day"]
    lines = gp._format_lines({"summary": {"format": stats, "runs": 1, "visible_runs": 1}})
    md = "\n".join(lines)
    assert "## 답변 형식" in md and "### 날짜별 결과를 받은 답변의 표" not in md
