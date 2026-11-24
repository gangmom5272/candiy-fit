"""날짜 지정 질문 반복 검증 실행기(eval/repeated_question.py) 테스트. 실제 LLM 대신 respx 모의 응답을 쓴다."""

import hashlib
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import AddedCalls, ToolCall, ToolRequest, tool_specs
from coach_ai.agent.graph import CoachResult, run_coach
from coach_ai.agent.prompts import DEFAULT_PROMPT_VERSION, load_system_prompt, render_system_prompt
from coach_ai.history.types import HistoryUsage
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import CALL_LIMIT_MESSAGE, DENIED_MESSAGE, ToolGateway
from coach_ai.tools.handlers import get_metric_summary
from coach_ai.tools.periods import resolve_period
from coach_ai.tools.schemas import GetMetricSummary
from eval import repeated_question as rq
from tests.fakes import old_app_run_coach
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response

DEFAULT = rq.TARGETS["default"]
CONTROL = rq.TARGETS["control"]
COMMIT = "abc1234"
# 이전 측정 기록(docs/evals/*-repeated-question-*.json)의 조건별 이력 sha256
MEASURED_HISTORY_SHA256 = {"new": "4f53cda18c2b", "sleep_history": "ff3796174313", "wrong_history": "245dd9c461af"}


# 커밋 조회는 git 을 실행해, git 이 없거나 저장소 소유자가 달라 git 이 거부하면 None 을 반환한다.
# 테스트 결과가 실행 환경의 git 에 따라 달라지지 않게 고정값을 쓴다
@pytest.fixture(autouse=True)
def fixed_commit(monkeypatch):
    monkeypatch.setattr(rq, "git_commit", lambda: COMMIT)


async def test_synthetic_week_matches_integration_averages():
    """지난 7일 공개 평균이 연동 쪽 합성 값(backend/scripts/health_query_example.py)과 같다."""
    port = rq.synthetic_health_data()
    week = resolve_period("last_7_days", rq.TODAY)
    averages = {}
    for metric in ("steps", "sleep", "heart_rate", "oxygen_saturation"):
        averages[metric] = (await port.get_metric_result(rq.USER_ID, metric, week.start, week.end)).summary.avg
    assert rq.NOW.isoformat() == "2026-10-23T16:32:00+09:00"
    assert (week.start, week.end) == (date(2026, 10, 17), date(2026, 10, 23))
    assert averages == {"steps": 7465.0, "sleep": 395.8, "heart_rate": 73.83, "oxygen_saturation": 95.73}


@pytest.mark.parametrize("period", ["last_7_days", "last_14_days", "last_30_days", "this_week", "this_month"])
async def test_oct21_is_lowest_oxygen_day(period):
    port = rq.synthetic_health_data()
    span = resolve_period(period, rq.TODAY)
    oxygen = await port.get_metric_result(rq.USER_ID, "oxygen_saturation", span.start, span.end)
    sleep = await port.get_metric_result(rq.USER_ID, "sleep", span.start, span.end)
    oct21 = date(2026, 10, 21)
    assert {d.date: d.value for d in oxygen.days}[oct21] == 95.1
    assert {d.date: d.value for d in sleep.days}[oct21] == 340
    assert (oxygen.summary.daily_min.date, oxygen.summary.daily_min.value) == (oct21, 95.1)
    assert (DEFAULT.day, DEFAULT.sleep_minutes, DEFAULT.spo2) == (oct21, 340, 95.1)


@pytest.mark.parametrize("period", ["last_7_days", "last_14_days", "last_30_days", "last_week", "this_month"])
async def test_control_day_is_neither_lowest_nor_highest(period):
    port = rq.synthetic_health_data()
    span = resolve_period(period, rq.TODAY)
    oxygen = await port.get_metric_result(rq.USER_ID, "oxygen_saturation", span.start, span.end)
    sleep = await port.get_metric_result(rq.USER_ID, "sleep", span.start, span.end)
    oct18 = date(2026, 10, 18)
    assert {d.date: d.value for d in oxygen.days}[oct18] == 95.5
    assert {d.date: d.value for d in sleep.days}[oct18] == 441
    assert oct18 not in (oxygen.summary.daily_min.date, oxygen.summary.daily_max.date)
    assert (CONTROL.day, CONTROL.sleep_minutes, CONTROL.spo2) == (oct18, 441, 95.5)


@pytest.mark.parametrize("question", ["default", "control"])
@pytest.mark.parametrize("tool", ["get_metric_summary", "get_daily_trend"])
async def test_date_expression_results_are_judged_correct(question, tool):
    """date: 로 그날만 조회한 결과의 표시 문자열로 답하면 정답으로 판정한다 (시스템 프롬프트 v1.5 날짜 규칙)."""
    target = rq.TARGETS[question]
    gateway = ToolGateway(rq.synthetic_health_data(), rq.TODAY, audit=MemoryAuditSink())
    ctx = CoachContext(user_id=rq.USER_ID, conversation_id="rq", request_id="rq")
    day = f"date:{target.day:%m-%d}"
    calls = [ToolCall(f"c-{m}", tool, {"metric": m, "period": day}) for m in ("sleep", "oxygen_saturation")]
    replies = await gateway.execute(calls, ctx)
    sleep, oxygen = (json.loads(reply.content)["result"] for reply in replies)
    assert [sleep["period"][k] for k in ("start", "end", "display")] == [
        target.day.isoformat(),
        target.day.isoformat(),
        f"{target.day.month}월 {target.day.day}일",
    ]
    if tool == "get_metric_summary":
        values = sleep["avg"]["display"], oxygen["avg"]["display"]
    else:
        values = sleep["points"][0]["display"], oxygen["points"][0]["display"]
    assert values == (rq.format_minutes(target.sleep_minutes), f"{target.spo2}%")
    label = sleep["period"]["display"]
    answer = f"{label} 수면 시간은 {values[0]}이고, 수면 중 산소포화도는 {values[1]}예요."
    assert rq.judge(answer, target).verdict == "correct"
    evidence = [reply.evidence[0] for reply in replies]
    assert all(rq.evidence_value(e, target.day)["day"]["value"] is not None for e in evidence)
    if tool == "get_metric_summary":
        # 기간 요약의 근거 summary 처럼 날짜 뒤에 평균을 붙여 말해도 그날 값으로 판정한다
        assert rq.judge(". ".join(e.summary for e in evidence), target).verdict == "correct"


async def test_sleep_history_matches_synthetic_week():
    """고정 이력의 평균 수면·기록 일수 문장이 합성 데이터의 도구 결과와 같다."""
    port = rq.synthetic_health_data()
    request = ToolRequest(user_id=rq.USER_ID, today=rq.TODAY, port=port)
    out = await get_metric_summary(GetMetricSummary(metric="sleep", period="last_7_days"), request)
    payload = out.payload
    assert payload["avg"]["display"] == "6시간 36분"
    assert payload["coverage"] == "7일 중 5일 기록, 동기화 전 1일"
    assert (payload["missing_days"], payload["unsynced_days"]) == (["2026-10-20"], ["2026-10-23"])
    first_answer = rq.HISTORIES["sleep_history"][1].content
    assert "6시간 36분" in first_answer and payload["coverage"] in first_answer


def test_data_hash_changes_with_data():
    port = rq.synthetic_health_data()
    assert rq.data_sha256(port) == rq.data_sha256(rq.synthetic_health_data())
    port.sleep[rq.USER_ID].pop()
    assert rq.data_sha256(port) != rq.data_sha256(rq.synthetic_health_data())


@pytest.mark.parametrize(
    "answer, verdict",
    [
        ("10월 21일 수면 시간은 5시간 40분입니다. 해당 날짜의 수면 중 산소포화도는 95.1%입니다.", "correct"),
        ("10월 21일 수면 시간은 5시간 40분이에요. 산소포화도는 기록으로 확인할 수 없어요.", "unavailable"),
        (
            "10월 21일 수면 시간은 5시간 40분이에요. 산소포화도 기록은 10월 21일의 일일 최저치인 95.1%만 확인되며, "
            "해당 날짜의 정확한 평균 수치는 기록으로 확인할 수 없어요.",
            "contradictory",
        ),
        (
            "산소포화도는 해당 날짜의 개별 기록을 확인할 수 없어 정확한 수치를 안내해 드리기 어렵습니다. "
            "다만 최근 30일 요약 기록을 보면 10월 21일에 95.1%로 가장 낮은 수치를 기록한 적이 있습니다.",
            "contradictory",
        ),
        ("10월 21일 수면 시간은 6시간 36분이고, 수면 중 산소포화도는 95.7%예요.", "wrong_value"),
        ("10월 21일 수면 시간은 5시간 40분이고, 수면 중 산소포화도는 96%예요.", "wrong_value"),
        ("10월 21일에는 6시간 8분 주무셨고 산소포화도는 95.1%였어요.", "wrong_value"),
        # 기간 평균 값은 그날 값으로 세지 않는다
        (
            "10월 21일 수면 시간은 5시간 40분이에요. 그날 산소포화도는 확인되지 않고, 최근 7일 평균은 95.7%예요.",
            "unavailable",
        ),
        ("10월 21일 수면 시간은 5시간 40분이에요.", "other"),
        # 기록을 부정하는 표현은 띄어쓰기가 달라도 같게 판정한다
        ("10월 21일 수면 시간은 5시간 40분입니다. 해당 날짜의 산소포화도 기록은 확인 할 수 없습니다.", "unavailable"),
        ("10월 21일 수면 시간은 5시간 40분이에요. 산소포화도는 기록 되지 않았어요.", "unavailable"),
        ("10월 21일 수면 시간은 5시간 40분이에요. 산소포화도 수치는 확인 되지 않아요.", "unavailable"),
        ("10월 21일 수면 시간은 5시간 40분입니다. 산소포화도는 안내 해 드리기 어렵습니다.", "unavailable"),
        (
            "10월 21일 수면 시간은 5시간 40분이에요. 해당 날짜의 산소포화도는 기록으로 확인 할 수 없어요. "
            "다만 최근 30일 중 최저값인 95.1%를 기록한 날이 10월 21일이에요.",
            "contradictory",
        ),
    ],
)
def test_judge(answer, verdict):
    assert rq.judge(answer, DEFAULT).verdict == verdict


def test_judge_records_denial_as_written():
    answer = "10월 21일 수면 시간은 5시간 40분입니다. 해당 날짜의 산소포화도 기록은 확인 할 수 없습니다."
    assert rq.judge(answer, DEFAULT).detail["denial"] == "확인 할 수 없"


@pytest.mark.parametrize(
    "answer",
    [
        # 다른 날짜만 말하는 문장의 부정 표현은 그날 기록을 부정한 것이 아니다
        "10월 21일 수면 시간은 340분이고 산소포화도는 95.1%예요. 10월 20일은 기록이 없어요.",
        # 기간 평균을 함께 말해도 그날 값이 맞으면 정답이다
        "10월 21일에는 5시간 40분 주무셨고 산소포화도는 95.1%였어요. 최근 7일 평균 수면은 6시간 36분이에요.",
        # 취침·기상 시각은 수면 시간이 아니다
        "10월 21일에는 새벽 1시 10분에 잠들어 7시 15분에 일어났고 실제 수면 시간은 5시간 40분이에요.\n"
        "수면 중 산소포화도는 95.1 %예요.",
        # 기간을 밝힌 최저 표현은 그날 값을 다르게 설명한 것이 아니다
        "10월 21일 수면 시간은 5시간 40분, 산소포화도는 95.1%로 최근 7일 중 최저치예요.",
    ],
)
def test_judge_correct_with_other_days_and_periods(answer):
    assert rq.judge(answer, DEFAULT).verdict == "correct"


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [
        # 굵게 기호 안의 숫자도 읽는다
        ("10월 21일 수면 시간은 **5**시간 **40**분, 산소포화도는 **95.1**%예요.", "correct"),
        ("10월 21일 기록이에요.\n- 수면 시간: **5시간 40분**\n- 산소포화도: 95.1%", "correct"),
        (
            "| 날짜 | 수면 시간 | 산소포화도 |\n|---|---|---|\n"
            "| 10월 20일 (화) | 6시간 2분 | 96.0% |\n| 10월 21일 (수) | 5시간 40분 | 95.1% |",
            "correct",
        ),
        # 표는 한 행을 한 부분으로 읽는다. 다른 날 행의 쉼표 뒤 값은 그날 값이 아니다
        (
            "| 날짜 | 수면 시간 | 산소포화도 |\n|---|---|---|\n"
            "| 10월 20일 (화) | 6시간 2분, 깊은 잠 1시간 | 96.0%, 평소와 비슷해요 |\n"
            "| 10월 21일 (수) | 5시간 40분 | 기록이 없어요 |",
            "unavailable",
        ),
    ],
)
def test_judge_reads_markdown_answers(answer, verdict):
    assert rq.judge(answer, DEFAULT).verdict == verdict


def test_judge_detail_and_error():
    judged = rq.judge(
        "10월 21일 수면 시간은 5시간 40분이에요. 산소포화도 기록은 10월 21일의 일일 최저치인 95.1%만 확인돼요.",
        DEFAULT,
    )
    assert judged.verdict == "contradictory"
    assert (judged.detail["spo2"], judged.detail["sleep"]) == ("correct", "correct")
    assert judged.detail["reinterpret"] == "일일 최저" and judged.detail["denial"] is None

    failed = rq.judge("10월 21일 수면 시간은 5시간 40분입니다.", DEFAULT, error_code="llm_timeout")
    assert (failed.verdict, failed.detail) == ("error", {"error_code": "llm_timeout"})


def test_judge_control_target():
    assert rq.judge("10월 18일 수면 시간은 7시간 21분이고, 산소포화도는 95.5%예요.", CONTROL).verdict == "correct"
    # 다른 날의 최저값만 말하면 통제 날짜 값을 말한 것이 아니다
    other_day = "10월 18일 수면 시간은 7시간 21분이에요. 산소포화도는 10월 21일 95.1%가 가장 낮았어요."
    assert rq.judge(other_day, CONTROL).verdict == "other"
    assert rq.judge("10월 18일 수면은 7시간 21분, 산소포화도는 95.1%예요.", CONTROL).verdict == "wrong_value"


def test_run_order_shuffles_each_batch_with_seed():
    order = rq.run_order(10, rq.SEED)
    assert rq.SEED == 20261023
    assert order == rq.run_order(10, rq.SEED)
    assert order != rq.run_order(10, rq.SEED + 1)
    assert [run for run, _ in order] == [run for run in range(1, 11) for _ in range(3)]
    batches = [tuple(c for r, c in order if r == run) for run in range(1, 11)]
    assert all(sorted(batch) == sorted(rq.CONDITIONS) for batch in batches)
    assert len(set(batches)) > 1


SLEEP_WEEK = ("get_sleep_records", {"period": "last_7_days"})
OXYGEN_WEEK = ("get_metric_summary", {"metric": "oxygen_saturation", "period": "last_7_days"})
ANSWER = "수면 시간은 5시간 40분이에요. 산소포화도는 기록으로 확인할 수 없어요."


class ScriptedLLM:
    """질문에는 도구 호출 두 개를, 도구 결과가 들어오면 답변을 반환하고 받은 요청 본문을 모은다.

    답변마다 번호를 붙여 앞 실행의 답변이 뒤 실행 요청에 들어가는지 확인한다.
    """

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.answers = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if body["messages"][-1]["role"] == "tool":
            self.answers += 1
            return text_response(f"(답변 {self.answers}) ", ANSWER)
        return tool_call_response(SLEEP_WEEK, OXYGEN_WEEK)

    def first_requests(self) -> list[dict]:
        return [b for b in self.bodies if b["messages"][-1]["role"] == "user"]


def read_records(out_dir) -> list[dict]:
    path = next(out_dir.glob("*_records.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def llm(settings, monkeypatch) -> ScriptedLLM:
    server = ScriptedLLM()
    monkeypatch.setattr(rq, "get_settings", lambda: settings)
    return server


@respx.mock
def test_conditions_follow_seed_order_and_fixed_history(llm, tmp_path):
    respx.post(CHAT_URL).mock(side_effect=llm)
    rq.main(["--runs", "3", "--out", str(tmp_path)])

    order = rq.run_order(3, rq.SEED)
    records = read_records(tmp_path)
    assert [(r["run"], r["condition"]) for r in records] == order
    assert [r["order"] for r in records] == list(range(1, 10))

    firsts = llm.first_requests()
    assert len(firsts) == len(order)
    for body, (_, condition) in zip(firsts, order, strict=True):
        sent = [(m["role"], m["content"]) for m in body["messages"][1:]]
        history = [(turn.role, turn.content) for turn in rq.HISTORIES[condition]]
        assert sent == [*history, ("user", "10월 21일 수면 시간과 산소포화도를 알려줘")]
    assert len(rq.HISTORIES["new"]) == 0
    assert [t.role for t in rq.HISTORIES["sleep_history"]] == ["user", "assistant"] * 2
    assert rq.HISTORIES["wrong_history"][:2] == rq.WRONG_ANSWER
    assert rq.HISTORIES["wrong_history"][2:] == rq.HISTORIES["sleep_history"]
    # 한 실행의 답변은 다음 실행 이력에 넣지 않는다
    sent_texts = [m.get("content") or "" for body in llm.bodies for m in body["messages"]]
    assert not any("(답변 " in text for text in sent_texts)


@respx.mock
def test_record_has_answer_calls_rounds_and_evidence_day_values(llm, tmp_path):
    respx.post(CHAT_URL).mock(side_effect=llm)
    rq.main(["--runs", "1", "--out", str(tmp_path)])

    record = read_records(tmp_path)[0]
    assert record["verdict"] == "unavailable" and record["answer"].endswith(ANSWER)
    assert (record["tool_rounds"], record["error_code"], record["finish_reason"]) == (1, None, "stop")
    assert (record["answer_rechecks"], record["unsupported_rechecks"], record["hedged_sentences"]) == (0, 0, 0)
    assert record["exercise_rechecks"] == 0
    assert record["tool_calls"] == [
        {"round": 1, "name": "get_sleep_records", "args": {"period": "last_7_days"}, "reply": "ok"},
        {
            "round": 1,
            "name": "get_metric_summary",
            "args": {"metric": "oxygen_saturation", "period": "last_7_days"},
            "reply": "ok",
        },
    ]
    sleep, oxygen = record["evidence"]
    assert (sleep["metric"], sleep["day"]["value"], sleep["extreme"]) == ("sleeps", 340, "daily_min")
    assert (oxygen["metric"], oxygen["day"]["value"], oxygen["extreme"]) == ("oxygen_saturations", 95.1, "daily_min")
    assert record["elapsed_s"] >= 0 and record["llm_calls"] == 2
    # 호출별 입력 토큰. 도구 결과가 다음 호출 입력을 얼마나 늘리는지 실행마다 확인할 수 있다
    assert (record["llm_input_tokens"], record["input_tokens"]) == ([300, 500], 800)


@respx.mock
def test_control_question_records_control_day(llm, tmp_path):
    respx.post(CHAT_URL).mock(side_effect=llm)
    rq.main(["--runs", "1", "--question", "control", "--out", str(tmp_path)])

    assert llm.first_requests()[0]["messages"][-1]["content"] == "10월 18일 수면 시간과 산소포화도를 알려줘"
    record = read_records(tmp_path)[0]
    sleep, oxygen = record["evidence"]
    assert (sleep["day"]["value"], sleep["extreme"]) == (441, "daily_max")
    assert (oxygen["day"]["value"], oxygen["extreme"]) == (95.5, None)


@respx.mock
def test_saved_report_and_doc_include_conditions(llm, tmp_path):
    respx.post(CHAT_URL).mock(side_effect=llm)
    results, docs = tmp_path / "results", tmp_path / "docs"
    argv = ["--runs", "2", "--out", str(results), "--save-doc", "--label", "before", "--doc-dir", str(docs)]
    rq.main(argv)

    report = json.loads(next(results.glob("*_repeated_question_default.json")).read_text(encoding="utf-8"))
    assert report["command"] == "python -m eval.repeated_question " + " ".join(argv)
    assert report["commit"] == COMMIT
    assert (report["now"], report["seed"], report["runs"]) == ("2026-10-23T16:32:00+09:00", 20261023, 2)
    assert report["order"] == [list(pair) for pair in rq.run_order(2, rq.SEED)]
    assert report["question"]["text"] == "10월 21일 수면 시간과 산소포화도를 알려줘"
    assert (report["question"]["sleep_minutes"], report["question"]["spo2"]) == (340, 95.1)
    assert set(report["conditions"]) == {"new", "sleep_history", "wrong_history"}
    wrong = report["conditions"]["wrong_history"]
    assert [turn["content"] for turn in wrong["history"][:2]] == [t.content for t in rq.WRONG_ANSWER]
    # 이력이 이전 측정 기록과 같아야 새 측정을 이전 측정과 비교할 수 있다
    assert {c: info["history_sha256"] for c, info in report["conditions"].items()} == MEASURED_HISTORY_SHA256
    assert (report["temperature"], report["temperature_source"]) == (None, "server_default")
    assert len(report["prompt_sha256"]) == 12 and len(report["tools_sha256"]) == 12
    assert report["data"]["sha256"] == rq.data_sha256(rq.synthetic_health_data())[:12]
    assert report["data"]["week"]["matches_integration"] is True
    assert report["data"]["targets"]["control"] == {"date": "2026-10-18", "sleep_minutes": 441, "spo2": 95.5}
    s = report["summary"]
    assert {c: g["runs"] for c, g in s["by_condition"].items()} == {"new": 2, "sleep_history": 2, "wrong_history": 2}
    assert s["overall"]["verdicts"]["unavailable"] == 6 and s["overall"]["tool_rounds"] == {"1": 6}

    doc_json = next(docs.glob("*-repeated-question-before.json"))
    assert json.loads(doc_json.read_text(encoding="utf-8"))["conditions"] == report["conditions"]
    md = next(docs.glob("*-repeated-question-before.md")).read_text(encoding="utf-8")
    for heading in (
        "## 조건별 결과",
        "## 도구 왕복 분포",
        "## 도구 호출",
        "## 합성 데이터",
        "## 이력 조건",
        "## 판정 기준",
    ):
        assert heading in md
    assert "| new | 이력 없음 | 2 | 0 |" in md and f"커밋 `{COMMIT}`" in md

    # 같은 이름의 기록이 있으면 LLM 을 호출하기 전에 멈춘다
    sent = len(llm.bodies)
    with pytest.raises(SystemExit, match="이미 있는"):
        rq.main(argv)
    assert len(llm.bodies) == sent


@pytest.mark.parametrize(
    ("version", "day", "text"),
    [
        ("v1.2", "2026-10-23", "2026년 10월 23일 금요일"),
        # 날짜 자리가 없는 프롬프트는 그대로 보내고, 넣은 날짜는 없다
        ("v1.1", None, None),
    ],
)
@respx.mock
def test_prompt_gets_reference_date_and_report_records_it(llm, tmp_path, version, day, text):
    respx.post(CHAT_URL).mock(side_effect=llm)
    rq.main(["--runs", "1", "--prompt-version", version, "--out", str(tmp_path)])

    v1_1 = load_system_prompt("v1.1")
    sent = v1_1 if text is None else f"{v1_1}\n\n오늘은 {text}이다."
    assert {body["messages"][0]["content"] for body in llm.bodies} == {sent}
    report = json.loads(next(tmp_path.glob("*_repeated_question_default.json")).read_text(encoding="utf-8"))
    template = load_system_prompt(version)
    assert report["prompt_sha256"] == hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]
    assert report["prompt_date"] == day
    md = next(tmp_path.glob("*_repeated_question_default.md")).read_text(encoding="utf-8")
    assert f"(템플릿 sha256 {report['prompt_sha256']}, 넣은 날짜 {text or '없음'})" in md


@respx.mock
def test_llm_failure_is_recorded_as_error(settings, monkeypatch, tmp_path):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(400, json={"error": {"message": "bad request"}}))
    monkeypatch.setattr(rq, "get_settings", lambda: settings)
    rq.main(["--runs", "1", "--out", str(tmp_path)])

    records = read_records(tmp_path)
    assert {r["verdict"] for r in records} == {"error"}
    assert {r["error_code"] for r in records} == {"invalid_request"}


@pytest.mark.parametrize(
    "argv",
    [
        ["--runs", "0"],
        ["--question", "unknown"],
        ["--save-doc"],
        ["--save-doc", "--label", "Before 1"],
        ["--temperature", "3"],
        # 재판정은 측정 때의 문항·조건·label 을 그대로 쓴다
        ["--rejudge", "x_records.jsonl", "--runs", "3"],
        ["--rejudge", "x_records.jsonl", "--question", "control"],
        ["--rejudge", "x_records.jsonl", "--prompt-version", "v1"],
        ["--rejudge", "x_records.jsonl", "--temperature", "0"],
        ["--rejudge", "x_records.jsonl", "--save-doc", "--label", "after"],
    ],
)
def test_parse_args_rejects_bad_values(argv):
    with pytest.raises(SystemExit):
        rq.parse_args(argv)


async def send_message_list(model, question: str, history) -> None:
    """이력을 질문·답변 메시지로 그대로 넣던 첫 호출(시스템 프롬프트 → 이력 → 질문, 도구 정의 포함)을 보낸다."""
    system = render_system_prompt(load_system_prompt(DEFAULT_PROMPT_VERSION), rq.TODAY)
    turns = [HumanMessage(content=t.content) if t.role == "user" else AIMessage(content=t.content) for t in history]
    messages = [SystemMessage(content=system), *turns, HumanMessage(content=question)]
    async for _ in model.bind_tools(tool_specs()).astream(messages):
        pass


@pytest.mark.parametrize("question", list(rq.TARGETS))
@respx.mock
async def test_first_request_is_same_bytes_as_message_list_history(settings, question):
    """조건마다 첫 LLM 요청 본문이 이력을 질문·답변 메시지로 그대로 넣던 요청과 바이트 단위로 같다.

    이력을 CoachContext.turns 로 넘겨도 같은 요청을 보내야 이전 측정 기록과 비교할 수 있다.
    """
    route = respx.post(CHAT_URL).mock(side_effect=lambda request: text_response("네."))
    recorder = MetricsRecorder()
    model = rq.make_model(settings, None, recorder, rq.RequestLog())
    try:
        for condition in rq.CONDITIONS:
            await rq.run_once(model, recorder, question, condition, 1, 1, DEFAULT_PROMPT_VERSION)
            await send_message_list(model, rq.TARGETS[question].question, rq.HISTORIES[condition])
    finally:
        await model.http_async_client.aclose()

    sent = [call.request.content for call in route.calls]
    assert len(sent) == 2 * len(rq.CONDITIONS)
    for i, condition in enumerate(rq.CONDITIONS):
        assert sent[2 * i] == sent[2 * i + 1], condition


@respx.mock
async def test_history_goes_through_context_turns(settings, monkeypatch):
    """이력은 대화 API 처럼 CoachContext.turns 로 넘긴다."""
    respx.post(CHAT_URL).mock(side_effect=lambda request: text_response("네."))
    received = []

    async def recording_run_coach(ctx, question, **kw):
        result = await run_coach(ctx, question, **kw)
        received.append((ctx, result))
        return result

    monkeypatch.setattr(rq, "run_coach", recording_run_coach)
    recorder = MetricsRecorder()
    model = rq.make_model(settings, None, recorder, rq.RequestLog())
    try:
        for condition in rq.CONDITIONS:
            await rq.run_once(model, recorder, "default", condition, 1, 1, DEFAULT_PROMPT_VERSION)
    finally:
        await model.http_async_client.aclose()

    for (ctx, result), condition in zip(received, rq.CONDITIONS, strict=True):
        rows = [(t.role, t.content) for t in rq.HISTORIES[condition]]
        assert [row for t in ctx.turns for row in (("user", t.question), ("assistant", t.answer))] == rows
        assert ctx.summary is None
        # 이력이 짧아 첫 호출에 모든 쌍이 들어간다
        assert result.history_usage == HistoryUsage.of(None, ctx.turns, received_turns=len(ctx.turns))


@respx.mock
async def test_app_result_without_recheck_and_hedge_fields_records_zero(settings, monkeypatch):
    # 두 칸이 없는 옛 앱 코드를 이 실행기로 잴 때도 기록을 만든다. 그 기능이 없어 0 이 실제 값이다
    respx.post(CHAT_URL).mock(side_effect=lambda request: text_response("네."))
    monkeypatch.setattr(rq, "run_coach", old_app_run_coach(rq.run_coach))
    recorder = MetricsRecorder()
    model = rq.make_model(settings, None, recorder, rq.RequestLog())
    try:
        record = await rq.run_once(model, recorder, "default", "new", 1, 1, DEFAULT_PROMPT_VERSION)
    finally:
        await model.http_async_client.aclose()

    assert (record["error_code"], record["answer"]) == (None, "네.")
    assert (record["answer_rechecks"], record["unsupported_rechecks"], record["hedged_sentences"]) == (0, 0, 0)
    assert record["exercise_rechecks"] == 0


def test_context_turns_numbers_pairs_like_conversation_api():
    history = rq.HISTORIES["wrong_history"]
    turns = rq.context_turns(history)
    assert [(t.question_seq, t.answer_seq) for t in turns] == [(1, 2), (3, 4), (5, 6)]
    pairs = zip(history[::2], history[1::2], strict=True)
    assert [(t.question, t.answer) for t in turns] == [(q.content, a.content) for q, a in pairs]
    assert rq.context_turns(rq.HISTORIES["new"]) == ()


@pytest.mark.parametrize("history", [rq.SLEEP_TALK[:1], rq.SLEEP_TALK[1:3]], ids=["odd", "answer_first"])
def test_context_turns_needs_question_answer_pairs(history):
    with pytest.raises(ValueError):
        rq.context_turns(history)


SPACED_ANSWER = "10월 21일 수면 시간은 5시간 40분입니다. 해당 날짜의 산소포화도 기록은 확인 할 수 없습니다."


@pytest.fixture
def measured(llm, tmp_path):
    """모의 LLM 으로 측정해 docs 에 before 로 저장하고, 첫 실행의 답변은 띄어쓰기가 다른 부정 표현으로, 판정은
    지금 판정 기준과 다른 other 로 바꿔 둔다.

    (실행별 기록, 결과 폴더, docs 폴더)를 반환한다.
    """
    results, docs = tmp_path / "results", tmp_path / "docs"
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=llm)
        rq.main(["--runs", "1", "--out", str(results), "--save-doc", "--label", "before", "--doc-dir", str(docs)])
    path = next(results.glob("*_records.jsonl"))
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0] |= {"answer": SPACED_ANSWER, "verdict": "other"}
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return path, results, docs


def test_rejudge_rebuilds_report_and_doc_without_llm(measured, monkeypatch):
    path, results, docs = measured
    source = path.read_bytes()
    measured_report = json.loads(next(results.glob("*_repeated_question_default.json")).read_text(encoding="utf-8"))

    def no_settings():
        raise AssertionError("재판정은 LLM 설정을 읽지 않는다")

    monkeypatch.setattr(rq, "get_settings", no_settings)
    argv = ["--rejudge", str(path), "--out", str(results), "--save-doc", "--doc-dir", str(docs)]
    with respx.mock:  # 등록한 경로가 없어 LLM 을 호출하면 실패한다
        rq.main(argv)

    assert path.read_bytes() == source  # 측정 기록은 바꾸지 않는다
    new_path = next(results.glob("*_rejudged_records.jsonl"))
    first = json.loads(new_path.read_text(encoding="utf-8").splitlines()[0])
    assert (first["verdict"], first["judge"]["denial"]) == ("unavailable", "확인 할 수 없")

    report = json.loads(next(results.glob("*_rejudged.json")).read_text(encoding="utf-8"))
    for key in ("measured_at", "commit", "command", "label", "prompt_sha256", "tools_sha256", "question", "data"):
        assert report[key] == measured_report[key]
    assert report["records_file"].endswith(new_path.name)
    [entry] = report["rejudges"]
    assert entry["source_records"].endswith(path.name)
    assert entry["command"] == "python -m eval.repeated_question " + " ".join(argv)
    assert entry["commit"] == COMMIT
    assert entry["changed"] == 1
    assert entry["changes"] == [
        {"order": 1, "run": 1, "condition": first["condition"], "before": "other", "after": "unavailable"}
    ]
    assert report["summary"]["overall"]["verdicts"]["unavailable"] == 3

    # docs 의 같은 측정 기록을 재판정 결과로 바꾼다
    assert json.loads(next(docs.glob("*-repeated-question-before.json")).read_text(encoding="utf-8")) == report
    md = next(docs.glob("*-repeated-question-before.md")).read_text(encoding="utf-8")
    assert "바뀐 판정 1건" in md and "other → unavailable" in md
    assert "띄어쓰기 차이를 무시" in md


def test_rejudge_does_not_overwrite_doc_of_other_measurement(measured):
    path, results, docs = measured
    doc_json = next(docs.glob("*-repeated-question-before.json"))
    other = json.loads(doc_json.read_text(encoding="utf-8")) | {"measured_at": "2026-10-26T09:00:00"}
    doc_json.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
    saved = sorted(p.name for p in results.iterdir())

    with pytest.raises(SystemExit, match="다른 측정"):
        rq.main(["--rejudge", str(path), "--out", str(results), "--save-doc", "--doc-dir", str(docs)])
    assert sorted(p.name for p in results.iterdir()) == saved


def test_rejudge_stops_when_records_differ_from_doc(measured):
    path, results, docs = measured
    rq.main(["--rejudge", str(path), "--out", str(results), "--save-doc", "--doc-dir", str(docs)])
    doc_json = next(docs.glob("*-repeated-question-before.json"))
    doc_md = doc_json.with_suffix(".md")
    saved = (doc_json.read_bytes(), doc_md.read_bytes())

    # docs 기록은 재판정한 실행별 기록을 가리킨다. 측정 때 기록으로 다시 재판정하면 docs 의 재판정 이력이 사라진다
    again = results.parent / "again"
    with pytest.raises(SystemExit, match="records_file"):
        rq.main(["--rejudge", str(path), "--out", str(again), "--save-doc", "--doc-dir", str(docs)])
    assert (doc_json.read_bytes(), doc_md.read_bytes()) == saved
    assert not again.exists()


def test_rejudge_of_doc_records_appends_to_rejudges_of_doc(measured):
    path, results, docs = measured
    rq.main(["--rejudge", str(path), "--out", str(results), "--save-doc", "--doc-dir", str(docs)])
    doc_json = next(docs.glob("*-repeated-question-before.json"))
    first = json.loads(doc_json.read_text(encoding="utf-8"))
    rejudged = next(results.glob("*_rejudged_records.jsonl"))
    assert first["records_file"].endswith(rejudged.name)

    again = results.parent / "again"
    rq.main(["--rejudge", str(rejudged), "--out", str(again), "--save-doc", "--doc-dir", str(docs)])
    doc = json.loads(doc_json.read_text(encoding="utf-8"))
    # docs 의 재판정 이력은 그대로 두고 새 재판정을 뒤에 붙인다
    assert doc["rejudges"][0] == first["rejudges"][0]
    assert [(r["source_records"], r["changed"]) for r in doc["rejudges"]] == [
        (first["rejudges"][0]["source_records"], 1),
        (first["records_file"], 0),
    ]
    assert doc["records_file"].endswith(next(again.glob("*_rejudged_records.jsonl")).name)
    lines = doc_json.with_suffix(".md").read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("- 재판정 ") for line in lines) == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda path: path.rename(path.with_name("records.jsonl")), "실행별 기록"),
        (lambda path: next(path.parent.glob("*_default.json")).unlink(), "집계"),
        (
            lambda path: path.write_text(path.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8"),
            "기록 수",
        ),
    ],
)
def test_rejudge_needs_complete_measurement(measured, change, message):
    path, results, _ = measured
    change(path)
    target = path if path.exists() else path.with_name("records.jsonl")
    with pytest.raises(SystemExit, match=message):
        rq.main(["--rejudge", str(target), "--out", str(results)])


def test_rejudge_reads_measurement_saved_without_prompt_date(measured):
    # 날짜를 넣기 전에 저장한 집계 json 에는 prompt_date 가 없다
    path, results, _ = measured
    report_path = next(results.glob("*_repeated_question_default.json"))
    old = json.loads(report_path.read_text(encoding="utf-8"))
    del old["prompt_date"]
    report_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    rq.main(["--rejudge", str(path), "--out", str(results)])
    assert "넣은 날짜 없음" in next(results.glob("*_rejudged.md")).read_text(encoding="utf-8")


def test_rejudge_stops_when_target_differs_from_measurement(measured, monkeypatch):
    path, results, _ = measured
    monkeypatch.setitem(rq.TARGETS, "default", rq.Target(date(2026, 10, 21), 340, 95.2))
    with pytest.raises(SystemExit, match="정답"):
        rq.main(["--rejudge", str(path), "--out", str(results)])


def test_parse_args_defaults():
    """인자 없이 파싱 → 기본 질문·횟수·프롬프트 버전(v1.20)과 저장 옵션 기본값이다."""
    args = rq.parse_args([])
    assert (args.question, args.runs, args.temperature, args.prompt_version) == ("default", 10, None, "v1.20")
    assert (args.out, args.save_doc, args.label, args.doc_dir) == (rq.RESULTS_DIR, False, None, rq.DOCS_DIR)
    assert args.rejudge is None


def test_parse_args_rejudge_takes_records_path_and_save_doc():
    args = rq.parse_args(["--rejudge", "eval/results/x_records.jsonl", "--save-doc"])
    assert (args.rejudge, args.save_doc, args.label) == (Path("eval/results/x_records.jsonl"), True, None)


def test_gateway_denials_including_call_limit_are_denied_replies():
    assert rq._reply_kind(DENIED_MESSAGE) == "denied"
    assert rq._reply_kind(CALL_LIMIT_MESSAGE) == "denied"
    assert rq._reply_kind('{"error": "unavailable_period"}') == "unavailable_period"


def test_added_calls_are_recorded_after_the_model_calls_of_the_round():
    factors = ToolCall("call_1", "get_sleep_records", {"analysis": "factors"})
    signals = ToolCall("added-0", "get_sleep_records", {"analysis": "signals", "period": "weeks_ago:0"})
    body = {"evidence_ids": ["E1"], "result": {}, "added": [{"error": "unavailable_period", "message": "안내"}]}
    messages = [
        HumanMessage("산소포화도가 밤마다 다른 까닭"),
        AIMessage("", tool_calls=[{"name": "get_sleep_records", "args": {"analysis": "factors"}, "id": "call_1"}]),
        ToolMessage(json.dumps(body), tool_call_id="call_1", artifact=AddedCalls(factors, (signals,))),
        AIMessage("답"),
    ]
    result = CoachResult(answer="답", evidence=[], finish_reason="stop", messages=messages)
    assert rq.tool_calls_of(result, MemoryAuditSink()) == [
        {"round": 1, "name": "get_sleep_records", "args": {"analysis": "factors"}, "reply": "ok"},
        {
            "round": 1,
            "name": "get_sleep_records",
            "args": dict(signals.args),
            "reply": "unavailable_period",
            "added": True,
        },
    ]
    # 더한 호출이 없거나 실행한 호출만 넣은 메시지에서는 더한 호출이 없다
    assert rq.added_lookups(ToolMessage("{}", tool_call_id="call_1")) == []
    assert rq.added_lookups(ToolMessage("{}", tool_call_id="call_1", artifact=factors)) == []
