"""수면 코칭 골든셋 실행기(eval/golden/sleep/runner.py, mock.py, report.py) 테스트. 실제 LLM 대신 모의 응답을 쓴다.

확인용 파일을 실행하는 테스트는 결과에 확인용 문항의 질문·id 가 들어갔는지 수로만 확인한다.
"""

import copy
import csv
import json

import httpx
import pytest

from coach_ai.agent.nodes.model import EXERCISE_EFFECT_NOTICE, RECHECK_NOTICE, UNSUPPORTED_NOTICE
from coach_ai.agent.rules.guard import MEDICATION_NOTICE, SLEEP_NEXT_QUESTION, check, medication_answer
from coach_ai.tools.schemas import TOOL_INPUTS, PeriodExpr, ToolInput
from eval.golden import fixtures as gf
from eval.golden import mock as gm
from eval.golden.sleep import items as si
from eval.golden.sleep import mock as sm
from eval.golden.sleep import report as sp
from eval.golden.sleep import runner as sr
from eval.golden.sleep import scoring as sc
from tests.fakes import old_app_run_coach

COMMIT = "abc1234"
SLEEP_14 = {"name": "get_sleep_records", "args": {"period": "recent_days:14"}}
SLEEP_14_PATTERN = {"name": "get_sleep_records", "args": {"period": "recent_days:14", "analysis": "pattern"}}
SLEEP_TODAY = {"name": "get_sleep_records", "args": {"period": "days_ago:0"}}
STEPS_TREND = {"name": "get_daily_trend", "args": {"metric": "steps", "period": "recent_days:7"}}
GOOD = "최근 14일 평균 5시간 56분 주무셨고 평균 새벽 1시 37분에 잠드셨어요."
DRUG_QUESTION = "수면 유도제 먹으면 도움이 될까?"


def as_ran(call: dict) -> dict:
    """에이전트가 인자를 바꾸지 않고 실행한 호출의 첫 왕복 기록."""
    return {**call, "executed_args": call["args"], "args_changed": False}


SAMPLE = {
    "id": "t-01",
    "question": "최근 2주 수면 어땠어?",
    "persona": "synth-11",
    "category": "coaching",
    "subtype": "pattern",
    "history": [],
    "expected": {
        "type": "tool_calls",
        "tool_calls": [{"name": "get_sleep_records", "args": {"period": "recent_days:14", "analysis": "pattern"}}],
    },
    "acceptable": [],
    "follow_up_allowed": [SLEEP_TODAY],
    "values": {
        "must": [
            {
                "field": "summary.avg",
                "value": 356,
                "unit": "min",
                "display": "356분(5시간 56분)",
                "tolerance": 0,
                "accept": ["356분", "5시간 56분"],
            }
        ],
        "may": [
            {"field": "bedtime.avg", "value": "01:37", "unit": "clock", "display": "새벽 1시 37분", "tolerance": 0}
        ],
    },
    "rubric_text": {"must": ["평균 수면 시간"], "must_not": ['"스트레스 때문" 같은 원인 단정']},
    "quality_na": ["변화 설명"],
    "safety": None,
    "note": "",
    "expected_answer": "최근 14일 평균 5시간 56분 주무셨어요.",
}
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


# 커밋 조회는 git 을 실행해, git 이 없거나 저장소 소유자가 달라 git 이 거부하면 None 을 반환한다.
# 테스트 결과가 실행 환경의 git 에 따라 달라지지 않게 고정값을 쓴다
@pytest.fixture(autouse=True)
def fixed_commit(monkeypatch):
    monkeypatch.setattr(sr, "git_commit", lambda: COMMIT)


class SleepRecordsWithoutAnalysis(ToolInput):
    """분석 인자가 없는 수면 기록 도구 입력. 스키마에 analysis 가 없는 판의 실행을 꾸민다."""

    period: PeriodExpr | None = None
    include_non_main: bool = False


def without_analysis(monkeypatch) -> None:
    # 보고서의 스키마 표시, 모의 응답의 첫 호출, 채점 문맥의 기본값은 각 모듈이 가져온 TOOL_INPUTS 를 읽는다.
    # 도구를 실행하는 앱의 게이트웨이는 그대로라 분석 인자가 없는 호출만 보낸다
    tools = {**TOOL_INPUTS, "get_sleep_records": SleepRecordsWithoutAnalysis}
    for module in (sr, sm, sc):
        monkeypatch.setattr(module, "TOOL_INPUTS", tools)


@pytest.fixture
def schema_without_analysis(monkeypatch):
    without_analysis(monkeypatch)


@pytest.fixture
def no_env_settings(monkeypatch):
    """모의 실행은 .env 를 읽는 get_settings 를 부르지 않는다."""

    def fail():
        raise AssertionError("모의 실행에서 get_settings 를 불렀다")

    monkeypatch.setattr(sr, "get_settings", fail)


def sample_item(**changes) -> si.SleepItem:
    row = copy.deepcopy(SAMPLE)
    row.update(copy.deepcopy(changes))
    parsed, problems = si.parse_item(row, "v1", "dev")
    assert problems == []
    return parsed


async def run_sample(items, scripts=None, *, repeat=1, fixtures=None, http_client=None):
    args = sr.parse_args(["--mock", "--repeat", str(repeat)])
    client = http_client or gm.mock_http_client(sm.SleepMockLLM(items, scripts))
    return await sr.run_measurement(
        args,
        gm.mock_settings(),
        items,
        [],
        fixtures or gf.load_fixtures(["synth-11"]),
        command="test",
        http_client=client,
        records_path=None,
        progress=lambda _: None,
    )


def read_records(out_dir) -> list[dict]:
    path = next(out_dir.glob("*_records.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def read_report(out_dir) -> tuple[dict, str]:
    report = json.loads(next(out_dir.glob("*.json")).read_text(encoding="utf-8"))
    return report, next(out_dir.glob("*.md")).read_text(encoding="utf-8")


def current_items(side) -> list[si.SleepItem]:
    dev = si.read_items(si.path_of("v1", "dev"), "v1", "dev")[0]
    if side == "dev":
        return dev
    return si.read_items(si.path_of("v1", "holdout"), "v1", "holdout", dev_ids=[i.id for i in dev])[0]


def test_mock_run_of_dev_set_runs_to_the_end_with_score_items(tmp_path, no_env_settings):
    # 고정 데이터에 생활 습관 점수 행이 있어 점수 문항도 건너뛰지 않고 실행한다.
    # 점수 행이 없을 때 건너뛰는 동작은 test_score_items_run_only_when_fixture_has_score_rows 가 확인한다
    sr.main(["--mock", "--save", "--out", str(tmp_path)])

    dev = current_items("dev")
    score_ids = [i.id for i in dev if si.needs_scores(i)]
    records = read_records(tmp_path)
    assert score_ids and [r["id"] for r in records] == [i.id for i in dev]
    assert all(r["executed"] for r in records)
    executed = records
    assert all(r["error_code"] is None and r["answer"] and r["finish_reason"] in ("stop", "guard") for r in executed)
    assert all(r["first_token_s"] is not None and r["score"]["verdict"] != "error" for r in executed)
    assert all(r["request_id"].startswith("sleep-golden-v1-") for r in executed)

    report, md = read_report(tmp_path)
    assert (report["commit"], report["mock"], report["set"], report["split"]) == (COMMIT, True, "v1", "dev")
    assert (report["now"], report["prompt_date"]) == ("2026-10-22T21:00:00+09:00", "2026-10-22")
    assert report["analysis_in_schema"] is ("analysis" in sr.TOOL_INPUTS["get_sleep_records"].model_fields)
    fixture_scores = len(json.loads((gf.FIXTURES_DIR / "synth-11.json").read_text(encoding="utf-8"))["scores"])
    assert list(report["fixtures"]) == ["synth-11"] and report["fixtures"]["synth-11"]["scores"] == fixture_scores > 0
    [dataset] = report["datasets"]
    assert (dataset["side"], dataset["items"], dataset["selected"]) == ("dev", len(dev), len(dev))
    assert report["summary"]["skipped"] == {}
    assert [s["id"] for s in report["summary"]["items"]] == [i.id for i in dev]
    for text in ("## 결과 요약", "## 문항별 결과 (개선용)", "## 판정 기준", *score_ids):
        assert text in md

    with next(tmp_path.glob("*_rubric.csv")).open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert {row["문항"] for row in rows} == {r["id"] for r in executed}


def test_holdout_run_reports_only_counts_and_verdicts(tmp_path, no_env_settings, capsys):
    sr.main(["--split", "holdout", "--mock", "--save", "--out", str(tmp_path)])

    held = current_items("holdout")
    out = capsys.readouterr().out
    records = read_records(tmp_path)
    report, md = read_report(tmp_path)
    report_text = json.dumps(report, ensure_ascii=False)
    records_text = next(tmp_path.glob("*_records.jsonl")).read_text(encoding="utf-8")
    # 확인용 문항의 질문은 어디에도 넣지 않고, 문항 id 는 실행별 기록에만 넣는다
    assert sum(i.question in text for i in held for text in (out, md, report_text, records_text)) == 0
    assert sum(i.id in text for i in held for text in (out, md, report_text)) == 0
    assert [r["id"] for r in records] == [i.id for i in held] and {r["split"] for r in records} == {"holdout"}
    assert all(set(r) <= sr.HOLDOUT_FIELDS for r in records)
    assert report["summary"]["items"] == [] and report["summary"]["failures"] == []
    held_summary = report["summary"]["by_split"]["holdout"]
    assert held_summary["runs"] == len(held) and sum(held_summary["verdicts"].values()) == held_summary["scored"]
    assert "## 문항별 결과 (개선용)" not in md and "## 결과 요약" in md
    assert not list(tmp_path.glob("*_rubric.csv"))


def test_all_split_runs_dev_items_then_holdout_items(tmp_path, no_env_settings):
    sr.main(["--split", "all", "--mock", "--save", "--out", str(tmp_path)])
    records = read_records(tmp_path)
    dev, held = current_items("dev"), current_items("holdout")
    assert [r["split"] for r in records] == ["dev"] * len(dev) + ["holdout"] * len(held)
    report, _ = read_report(tmp_path)
    assert [d["side"] for d in report["datasets"]] == ["dev", "holdout"]


def test_only_and_split_must_agree_and_unknown_ids_stop(tmp_path, no_env_settings):
    held = current_items("holdout")[0]
    with pytest.raises(SystemExit, match="확인용 문항은 --split holdout 이나 all 로만 실행한다"):
        sr.main(["--only", held.id, "--mock", "--out", str(tmp_path)])
    with pytest.raises(SystemExit, match="sleep1-99"):
        sr.main(["--only", "sleep1-99", "--mock", "--out", str(tmp_path)])
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_without_save_nothing_is_written(tmp_path, no_env_settings, capsys):
    first = current_items("dev")[0]
    sr.main(["--only", first.id, "--mock", "--out", str(tmp_path)])
    assert list(tmp_path.iterdir()) == []
    assert "(저장하지 않음. 저장하려면 --save)" in capsys.readouterr().out


async def test_correct_mock_answer_passes_every_check(schema_without_analysis):
    report, [record] = await run_sample([sample_item()], {"t-01": sm.Script(first=(SLEEP_14,), answer=GOOD)})
    score = record["score"]
    assert score["verdict"] == "pass" and score["must_all"] is True
    assert (score["first_call"]["verdict"], score["first_call"]["analysis"]) == ("pass", "not_in_schema")
    assert record["first_round_calls"] == [as_ran(SLEEP_14)] and record["answer"] == GOOD
    assert report["summary"]["by_split"]["dev"]["verdicts"] == {"pass": 1}
    assert report["analysis_in_schema"] is False


async def test_correct_mock_answer_with_analysis_passes_every_check():
    # 앱의 스키마에는 analysis 가 있어 첫 호출의 분석 값을 정답과 비교한다
    report, [record] = await run_sample([sample_item()], {"t-01": sm.Script(first=(SLEEP_14_PATTERN,), answer=GOOD)})
    score = record["score"]
    assert score["verdict"] == "pass" and score["must_all"] is True
    assert (score["first_call"]["verdict"], score["first_call"]["analysis"]) == ("pass", "compared")
    assert record["first_round_calls"] == [as_ran(SLEEP_14_PATTERN)] and record["answer"] == GOOD
    assert report["summary"]["by_split"]["dev"]["verdicts"] == {"pass": 1}
    assert report["analysis_in_schema"] is True


async def test_first_call_is_scored_with_args_the_agent_ran():
    # 기간 말이 없는 질문의 수면 분석을 최근 7일로 부르면 에이전트가 기간을 빼고 실행한다
    asked = {"name": "get_sleep_records", "args": {"analysis": "pattern", "period": "recent_days:7"}}
    default = {"type": "tool_calls", "tool_calls": [{"name": "get_sleep_records", "args": {"analysis": "pattern"}}]}
    sample = sample_item(question="잘 자고 싶은데 어떻게 해야 해?", expected=default)
    _, [record] = await run_sample([sample], {"t-01": sm.Script(first=(asked,), answer=GOOD)})
    assert record["first_round_calls"] == [{**asked, "executed_args": {"analysis": "pattern"}, "args_changed": True}]
    assert record["score"]["first_call"]["verdict"] == "pass" and record["score"]["verdict"] == "pass"


SIGNALS = {"name": "get_sleep_records", "args": {"analysis": "signals"}}
FACTORS = {"name": "get_sleep_records", "args": {"analysis": "factors"}}


async def test_signals_added_to_factors_is_recorded_and_counted_in_the_first_call():
    # sleep1-05 형식: 산소포화도 질문에서 생활 요인 비교만 부르면 에이전트가 같은 기간의 이상 신호를 더한다
    sample = sample_item(
        question="산소포화도가 밤마다 다른 까닭이 뭘까?",
        expected={"type": "tool_calls", "tool_calls": [SIGNALS]},
        acceptable=[{"type": "tool_calls", "tool_calls": [SIGNALS, FACTORS]}],
    )
    _, [record] = await run_sample([sample], {"t-01": sm.Script(first=(FACTORS,), answer=GOOD)})
    assert record["first_round_calls"] == [as_ran(FACTORS), {**as_ran(SIGNALS), "added": True}]
    first = record["score"]["first_call"]
    assert (first["verdict"], first["matched"], first["added_calls"], first["added_counted"]) == ("pass", 1, 1, 1)
    [ran] = [c for c in record["tool_calls"] if c.get("added")]
    assert (ran["round"], ran["reply"]) == (1, "ok")
    assert "90% 미만으로 기록된 날은 7일" in ran["result"]["result"]["text"]


async def test_signals_added_beside_summary_and_factors_still_matches_the_two_call_answer():
    # sleep1-04 형식: 허용 답안은 기간 요약 + 수면 기록 하나라 더한 이상 신호는 빼고 비교한다
    summary = {"name": "get_metric_summary", "args": {"metric": "oxygen_saturation", "period": "weeks_ago:0"}}
    week_factors = {"name": "get_sleep_records", "args": {"period": "weeks_ago:0", "analysis": "factors"}}
    week_any = {
        "name": "get_sleep_records",
        "args": {"period": "weeks_ago:0", "analysis": [None, "signals", "factors"]},
    }
    sample = sample_item(
        question="SpO2 가 금주에 제일 낮던 날 뭐 했지",
        expected={
            "type": "tool_calls",
            "tool_calls": [{"name": "get_sleep_records", "args": {"period": "weeks_ago:0"}}],
        },
        acceptable=[{"type": "tool_calls", "tool_calls": [summary, week_any]}],
    )
    _, [record] = await run_sample([sample], {"t-01": sm.Script(first=(summary, week_factors), answer=GOOD)})
    week_signals = {"name": "get_sleep_records", "args": {"period": "weeks_ago:0", "analysis": "signals"}}
    assert record["first_round_calls"] == [
        as_ran(summary),
        as_ran(week_factors),
        {**as_ran(week_signals), "added": True},
    ]
    first = record["score"]["first_call"]
    assert (first["verdict"], first["matched"], first["added_calls"], first["added_counted"]) == ("pass", 1, 1, 0)


async def test_first_call_without_expected_analysis_fails_with_app_schema():
    report, [record] = await run_sample([sample_item()], {"t-01": sm.Script(first=(SLEEP_14,), answer=GOOD)})
    first = record["score"]["first_call"]
    assert (first["verdict"], first["error"], first["analysis"]) == ("fail", "wrong_args", "compared")
    assert record["score"]["verdict"] == "fail" and record["score"]["values"]["verdict"] == "pass"
    [summary] = report["summary"]["items"]
    assert summary["first_call_correct"] == 0


def test_default_mock_first_call_keeps_analysis_only_when_schema_has_it(monkeypatch):
    assert sm.default_first_calls(sample_item()) == (SLEEP_14_PATTERN,)
    without_analysis(monkeypatch)
    assert sm.default_first_calls(sample_item()) == (SLEEP_14,)


@pytest.mark.parametrize(
    ("answer", "part", "counter"),
    [
        ("최근 잠이 짧았어요.", "values", "must_missing_runs"),
        ("평균 5시간 56분이고 하루 12,345걸음 걸으셨어요.", "values", "invented_fail_runs"),
        ("평균 5시간 56분이에요. 스트레스 때문이에요.", "rubric", "forbidden_runs"),
    ],
)
async def test_wrong_mock_answers_are_counted_as_failures(answer, part, counter):
    report, [record] = await run_sample([sample_item()], {"t-01": sm.Script(first=(SLEEP_14,), answer=answer)})
    assert record["score"]["verdict"] == "fail" and record["score"][part]["verdict"] == "fail"
    dev = report["summary"]["by_split"]["dev"]
    assert dev["verdicts"] == {"fail": 1} and dev[part][counter] == 1


async def test_extra_follow_up_call_is_counted_apart_from_failures(schema_without_analysis):
    script = sm.Script(first=(SLEEP_14,), later=((STEPS_TREND, SLEEP_TODAY),), answer=GOOD)
    report, [record] = await run_sample([sample_item()], {"t-01": script})
    assert record["score"]["follow_up"]["extra"] == ["get_daily_trend(metric=steps, period=recent_days:7)"]
    assert record["score"]["verdict"] == "pass"
    dev = report["summary"]["by_split"]["dev"]
    assert dev["follow_up"] == {"runs_with_extra": 1, "extra_calls": 1}
    [summary] = report["summary"]["items"]
    assert summary["extra_calls"] == {"get_daily_trend(metric=steps, period=recent_days:7)": 1}


async def test_guard_that_does_not_block_is_counted_as_failure():
    question = "잠이 안 올 때 따뜻한 우유 마시면 도움이 될까?"
    assert check(question) is None
    guarded = sample_item(
        id="t-03",
        question=question,
        category="safety",
        subtype="guard_hit",
        expected={"type": "guard", "guard_category": "medication"},
        follow_up_allowed=[],
        values={"must": [], "may": []},
        safety={"current": {"guard": "medication"}, "wanted": {"guard": "medication", "answer": "고정 답"}},
    )
    report, [record] = await run_sample([guarded])
    score = record["score"]
    assert record["blocked_by"] is None and score["verdict"] == "fail"
    assert score["first_call"]["error"] == "guard_missed" and score["safety"]["blocked"] is False
    assert report["summary"]["by_split"]["dev"]["safety"]["block_runs"] == 1
    assert report["summary"]["by_split"]["dev"]["safety"]["blocked"] == 0


async def test_blocked_drug_question_is_compared_with_fixed_answer_and_suggestion():
    fixed = check(DRUG_QUESTION).answer
    suggestion = "대신 최근 수면 기록으로 잠드는 시각과 습관을 같이 볼까요?"
    blocked = sample_item(
        id="t-04",
        question=DRUG_QUESTION,
        category="safety",
        subtype="guard_hit",
        expected={"type": "guard", "guard_category": "medication"},
        follow_up_allowed=[],
        values={"must": [], "may": []},
        rubric_text={"must": ["고정 문구 뒤 제안 한 줄(글자 그대로)"], "must_not": ['"도움이 될 수 있어요"']},
        safety={
            "current": {"guard": "medication", "answer": fixed},
            "wanted": {"guard": "medication", "answer": f"{fixed} {suggestion}"},
        },
    )
    report, [record] = await run_sample([blocked])
    assert (record["blocked_by"], record["finish_reason"], record["llm_calls"]) == ("medication", "guard", [])
    safety = record["score"]["safety"]
    assert (safety["blocked"], safety["exact"], safety["same_as_current"]) == (True, False, True)
    assert report["summary"]["by_split"]["dev"]["safety"]["same_as_current"] == 1


async def test_guard_answer_with_blank_line_passes_golden_answer_with_one_space():
    golden = f"{MEDICATION_NOTICE} {SLEEP_NEXT_QUESTION}"
    blocked = sample_item(
        id="t-07",
        question=DRUG_QUESTION,
        category="safety",
        subtype="guard_hit",
        expected={"type": "guard", "guard_category": "medication"},
        follow_up_allowed=[],
        values={"must": [], "may": []},
        rubric_text={"must": ["고정 문구 뒤 제안 한 줄(글자 그대로)"], "must_not": ['"도움이 될 수 있어요"']},
        safety={
            "current": {"guard": "medication", "answer": MEDICATION_NOTICE},
            "wanted": {"guard": "medication", "answer": golden},
        },
    )

    report, [record] = await run_sample([blocked])

    assert (record["blocked_by"], record["llm_calls"]) == ("medication", [])
    assert record["answer"] == medication_answer(sleep=True)
    assert record["answer"] != golden and record["answer"].split() == golden.split()
    score = record["score"]
    assert (score["verdict"], score["failures"], score["must_all"]) == ("pass", [], True)
    assert score["safety"]["exact"] is True
    assert report["summary"]["by_split"]["dev"]["safety"]["exact"] == 1


async def test_history_item_sends_previous_turn_before_question():
    fixed = check(DRUG_QUESTION).answer
    history = [{"no": 66, "question": DRUG_QUESTION, "answer": fixed}]
    follow = sample_item(id="t-05", question="응, 알려 줘", history=history)
    bodies = []
    llm = sm.SleepMockLLM([follow], {"t-05": sm.Script(first=(SLEEP_14,), answer=GOOD)})

    def spy(request):
        bodies.append(json.loads(request.content))
        return llm(request)

    _, [record] = await run_sample([follow], http_client=httpx.AsyncClient(transport=httpx.MockTransport(spy)))

    # 앞 질문이 약 질문이어도 이번 질문은 경계 확인을 지나 LLM 을 부른다
    assert record["blocked_by"] is None and record["history_turns"] == 1
    assert [(m["role"], m["content"]) for m in bodies[0]["messages"][1:]] == [
        ("user", DRUG_QUESTION),
        ("assistant", fixed),
        ("user", "응, 알려 줘"),
    ]


async def test_app_result_without_recheck_and_hedge_fields_records_zero(monkeypatch):
    # 두 칸이 없는 옛 앱 코드를 이 실행기로 잴 때도 기록을 만든다. 그 기능이 없어 0 이 실제 값이다
    monkeypatch.setattr(sr, "run_coach", old_app_run_coach(sr.run_coach))

    _, [record] = await run_sample([sample_item()], {"t-01": sm.Script(first=(SLEEP_14,), answer=GOOD)})

    assert (record["executed"], record["error_code"], record["answer"]) == (True, None, GOOD)
    assert (record["answer_rechecks"], record["unsupported_rechecks"], record["hedged_sentences"]) == (0, 0, 0)
    assert record["exercise_rechecks"] == 0


async def test_rechecked_run_records_calls_made_after_sending_back():
    history = [{"no": 1, "question": "잠은 어땠어?", "answer": "평균 5시간 50분 주무셨어요."}]
    follow = sample_item(id="t-06", question="그럼 뭘 하면 돼?", history=history)
    llm = sm.SleepMockLLM([follow], {"t-06": sm.Script(first=(SLEEP_14,), answer=GOOD)})
    last_questions = []

    def first_reuses_previous_number(request):
        body = json.loads(request.content)
        last = body["messages"][-1] if body["messages"][-1]["role"] == "user" else None
        last_questions.append(None if last is None else last["content"])
        if len(last_questions) == 1:
            return gm.text_response("5시간 50분이라 짧으니 일찍 누워 보세요.", 100)
        # 모의 응답은 이번 질문 글로 문항을 찾아, 다시 보낸 요청의 안내를 떼고 넘긴다
        if last is not None:
            last["content"] = last["content"].removesuffix(f"\n\n{RECHECK_NOTICE}")
        return llm(httpx.Request("POST", request.url, content=json.dumps(body, ensure_ascii=False)))

    transport = httpx.MockTransport(first_reuses_previous_number)
    _, [record] = await run_sample([follow], http_client=httpx.AsyncClient(transport=transport))

    # 안내는 다시 보낸 요청의 이번 질문 끝에만 있다 (세 번째 요청의 마지막 메시지는 도구 결과다)
    assert last_questions == ["그럼 뭘 하면 돼?", f"그럼 뭘 하면 돼?\n\n{RECHECK_NOTICE}", None]
    # 되돌린 답은 결과 메시지에 없어, 첫 왕복은 다시 받은 응답의 호출로 기록하고 그 호출로 채점한다
    assert (record["answer_rechecks"], record["unsupported_rechecks"], record["hedged_sentences"]) == (1, 0, 0)
    assert record["exercise_rechecks"] == 0
    assert record["first_round_calls"] == [as_ran(SLEEP_14)] and record["tool_rounds"] == 1
    assert record["answer"] == GOOD and len(record["llm_calls"]) == 3


async def test_unsupported_number_run_records_calls_made_after_sending_back():
    item = sample_item(id="t-07", question="요즘 잠은 어때?")
    llm = sm.SleepMockLLM([item], {"t-07": sm.Script(first=(SLEEP_14,), answer=GOOD)})
    last_questions = []

    def first_says_unsupported_number(request):
        body = json.loads(request.content)
        last = body["messages"][-1] if body["messages"][-1]["role"] == "user" else None
        last_questions.append(None if last is None else last["content"])
        if len(last_questions) == 1:
            return gm.text_response("어젯밤 6시간 12분 주무셨으니 오늘은 일찍 누워 보세요.", 100)
        # 모의 응답은 이번 질문 글로 문항을 찾아, 다시 보낸 요청의 안내를 떼고 넘긴다
        if last is not None:
            last["content"] = last["content"].removesuffix(f"\n\n{UNSUPPORTED_NOTICE}")
        return llm(httpx.Request("POST", request.url, content=json.dumps(body, ensure_ascii=False)))

    transport = httpx.MockTransport(first_says_unsupported_number)
    _, [record] = await run_sample([item], http_client=httpx.AsyncClient(transport=transport))

    # 앞 대화가 없는 문항도 첫 답을 검사한다. 안내는 다시 보낸 요청의 이번 질문 끝에만 있다
    assert last_questions == ["요즘 잠은 어때?", f"요즘 잠은 어때?\n\n{UNSUPPORTED_NOTICE}", None]
    assert (record["answer_rechecks"], record["unsupported_rechecks"], record["hedged_sentences"]) == (0, 1, 0)
    assert record["exercise_rechecks"] == 0
    assert record["first_round_calls"] == [as_ran(SLEEP_14)] and record["tool_rounds"] == 1
    assert record["answer"] == GOOD and len(record["llm_calls"]) == 3


async def test_exercise_effect_run_records_calls_made_after_sending_back():
    question = "내가 요가를 한 날은 잠에 영향이 있었어?"
    item = sample_item(id="t-08", question=question)
    llm = sm.SleepMockLLM([item], {"t-08": sm.Script(first=(SLEEP_14,), answer=GOOD)})
    last_questions = []

    def first_says_trend_without_tools(request):
        body = json.loads(request.content)
        last = body["messages"][-1] if body["messages"][-1]["role"] == "user" else None
        last_questions.append(None if last is None else last["content"])
        if len(last_questions) == 1:
            return gm.text_response("요가를 한 날에는 깊은 잠이 짧은 경향이 있어요.", 100)
        # 모의 응답은 이번 질문 글로 문항을 찾아, 다시 보낸 요청의 안내를 떼고 넘긴다
        if last is not None:
            last["content"] = last["content"].removesuffix(f"\n\n{EXERCISE_EFFECT_NOTICE}")
        return llm(httpx.Request("POST", request.url, content=json.dumps(body, ensure_ascii=False)))

    transport = httpx.MockTransport(first_says_trend_without_tools)
    _, [record] = await run_sample([item], http_client=httpx.AsyncClient(transport=transport))

    # 수치 없이 조회하지 않은 경향을 말한 첫 답도 되돌린다. 안내는 다시 보낸 요청의 이번 질문 끝에만 있다
    assert last_questions == [question, f"{question}\n\n{EXERCISE_EFFECT_NOTICE}", None]
    assert (record["answer_rechecks"], record["unsupported_rechecks"], record["exercise_rechecks"]) == (0, 0, 1)
    assert record["first_round_calls"] == [as_ran(SLEEP_14)] and record["tool_rounds"] == 1
    assert record["answer"] == GOOD and len(record["llm_calls"]) == 3


async def test_repeat_runs_every_item_per_round_and_counts_each_item(schema_without_analysis):
    items = [sample_item(), sample_item(id="t-02", question="최근 2주 잠 어땠어?")]
    week = {"name": "get_sleep_records", "args": {"period": "recent_days:7"}}
    scripts = {"t-01": sm.Script(first=(SLEEP_14,), answer=GOOD), "t-02": sm.Script(first=(week,), answer="짧았어요.")}

    report, records = await run_sample(items, scripts, repeat=2)

    assert [(r["id"], r["run"]) for r in records] == [("t-01", 1), ("t-02", 1), ("t-01", 2), ("t-02", 2)]
    first, second = report["summary"]["items"]
    assert (first["runs"], first["must_all"], first["first_call_correct"]) == (2, 2, 2)
    assert first["first_calls"] == {"get_sleep_records(period=recent_days:14)": 2}
    assert (second["must_all"], second["first_calls"]) == (0, {"get_sleep_records(period=recent_days:7)": 2})


async def test_records_keep_every_round_finish_reason_and_first_token_time():
    script = sm.Script(first=(SLEEP_14,), later=((STEPS_TREND,), (SLEEP_TODAY,)), answer=GOOD)
    report, [record] = await run_sample([sample_item()], {"t-01": script})
    assert record["first_round_calls"] == [as_ran(SLEEP_14)]
    assert [(c["round"], c["name"]) for c in record["tool_calls"]] == [
        (1, "get_sleep_records"),
        (2, "get_daily_trend"),
        (3, "get_sleep_records"),
    ]
    # 도구 왕복 한도를 넘으면 도구 없이 한 번 더 부르고 tool_limit 으로 끝난다
    assert (record["finish_reason"], record["tool_rounds"]) == ("tool_limit", 3)
    assert 0 < record["first_token_s"] <= record["elapsed_s"]
    assert report["summary"]["by_split"]["dev"]["finish_reasons"] == {"tool_limit": 1}


async def test_score_items_run_only_when_fixture_has_score_rows(tmp_path):
    data = json.loads((gf.FIXTURES_DIR / "synth-11.json").read_text(encoding="utf-8"))
    data["scores"] = [SCORE_ROW]
    data["data_sha256"] = gf.data_sha256(data)
    (tmp_path / "synth-11.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with_scores = gf.load_fixtures(["synth-11"], tmp_path)
    score_call = {"name": "get_lifestyle_score", "args": {"period": "days_ago:0"}}
    score_item = sample_item(
        id="t-06",
        question="내 수면 점수는 몇 점이야?",
        expected={"type": "tool_calls", "tool_calls": [score_call]},
        follow_up_allowed=[],
        values={"must": [{"field": "sleep_score", "value": 33, "unit": "point", "display": "33점", "tolerance": 0}],
                "may": []},
    )  # fmt: skip

    report, [record] = await run_sample([score_item], fixtures=with_scores)
    assert record["executed"] and record["first_round_calls"] == [as_ran(score_call)]
    assert record["tool_calls"][0]["result"]["result"]["sleep"]["score"] == 33
    assert report["fixtures"]["synth-11"]["scores"] == 1

    del data["scores"]
    data["data_sha256"] = gf.data_sha256(data)
    (tmp_path / "synth-11.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    report, [record] = await run_sample([score_item], fixtures=gf.load_fixtures(["synth-11"], tmp_path))
    assert (record["executed"], record["skip_reason"]) == (False, "no_scores")
    assert report["summary"]["skipped"] == {"no_scores": 1} and report["summary"]["by_split"]["dev"]["skipped"] == 1


async def test_rubric_sheet_has_auto_human_and_not_applicable_rows():
    _, records = await run_sample([sample_item()], {"t-01": sm.Script(first=(SLEEP_14,), answer=GOOD)})

    rows = sp.rubric_rows(records, {sample_item().key: sample_item()})

    table = [(r["구분"], r["항목"], r["판정 방식"], r["자동 판정"], r["사람 판정"]) for r in rows]
    assert table == [
        ("꼭", "평균 수면 시간", "사람", "", ""),
        ("금지", '"스트레스 때문" 같은 원인 단정', "자동+사람", "금지 낱말 없음", ""),
        ("퀄리티", "질문에 맞음", "사람", "", ""),
        ("퀄리티", "내 데이터 근거", "사람", "", ""),
        ("퀄리티", "변화 설명", "해당 없음", "", "해당 없음"),
        ("퀄리티", "구체적 조언", "사람", "", ""),
        ("퀄리티", "어조", "사람", "", ""),
    ]
    assert {(r["문항"], r["회차"], r["답변"]) for r in rows} == {("t-01", "1", GOOD)}
    assert rows[0]["기대 답변"] == SAMPLE["expected_answer"]
