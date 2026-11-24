"""한마디 답변 확인 실행기(eval/daily_message_answers.py) 테스트. 실제 LLM 대신 respx 모의 응답을 쓴다."""

import json

import pytest
import respx

from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.executor import tool_specs
from coach_ai.agent.prompts import load_system_prompt
from coach_ai.agent.rules.guard import check
from coach_ai.config import Settings
from coach_ai.tools.daily_message import daily_message_result
from eval import daily_message_answers as da
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response

# 줄이 여러 개인 답변. 실행기는 답변을 자르지 않고 끝까지 출력한다
ANSWER = '오늘의 한마디는 "한 걸음씩, 내 페이스대로 가볼까요?"예요.\n\n오늘 걸음 기록으로 고른 문구예요.'


class ScriptedLLM:
    """질문이 마지막 메시지인 호출에는 한마디 도구 호출을, 도구 결과가 붙은 호출에는 답변을 반환한다."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        if body["messages"][-1]["role"] == "tool":
            return text_response(ANSWER)
        return tool_call_response(("get_daily_message", {}))


def test_questions_cover_message_states_and_requests_and_pass_guard():
    assert list(da.QUESTIONS) == [
        "today", "reason", "partial", "waiting", "empty", "yesterday", "redraw", "cheer", "advice", "with_score",
    ]  # fmt: skip
    assert {status for _, status in da.QUESTIONS.values()} == {"READY", "PARTIAL", "WAITING", "EMPTY"}
    questions = [question for question, _ in da.QUESTIONS.values()]
    # 안전 규칙에 걸리면 LLM 을 부르지 않아 답변을 읽을 수 없다
    assert [q for q in questions if check(q) is not None] == []
    # 규칙과 도구 설명으로 답하는지 읽는 질문이라 시스템 프롬프트와 도구 설명에 넣지 않는다
    shown = load_system_prompt() + json.dumps(tool_specs(), ensure_ascii=False)
    assert [q for q in questions if q in shown] == []
    assert da.DEFAULT_RUNS == 3


async def test_synthetic_messages_change_only_today_message():
    sample = sample_health_data(da.USER_ID, da.NOW)
    rows = {}
    for status in ("READY", "PARTIAL", "WAITING", "EMPTY"):
        port = da.health_data(status)
        # 한마디 행만 바꾸고 나머지 합성 데이터는 adapters/sample.py 그대로다
        for name in ("steps", "heart", "sleep", "exercise", "scores", "sync"):
            assert getattr(port, name) == getattr(sample, name), name
        rows[status] = await port.get_daily_message(da.USER_ID, da.NOW.date(), request_id="r-1")

    ready, partial, waiting, empty = rows["READY"], rows["PARTIAL"], rows["WAITING"], rows["EMPTY"]
    assert (ready.status, ready.code, ready.used_metrics) == ("READY", "STEPS_PROGRESS", ("steps",))
    # 수면 조회 실패와 걸음 동기화 실패. 문구는 READY 와 같은 걸음 문구다
    assert (partial.status, partial.code, partial.title) == ("PARTIAL", "STEPS_PROGRESS", ready.title)
    assert [(n.metric, n.code) for n in partial.notices] == [("steps", "SYNC_FAILED"), ("sleeps", "LOOKUP_FAILED")]
    assert partial.data_as_of.sleeps is None and partial.data_as_of.steps == ready.data_as_of.steps
    assert daily_message_result(partial, da.NOW.date()).notices == [
        "걸음 기록은 최근 동기화에 실패해 이전에 저장된 기록으로 골랐다",
        "수면 기록은 불러오지 못해 문구를 고를 때 쓰지 않았다",
    ]
    assert (waiting.status, waiting.code, waiting.area, waiting.used_metrics) == (
        "WAITING",
        "PENDING_RECORDS",
        None,
        (),
    )
    assert waiting.title == "오늘의 기록을 아직 확인하지 못했어요" and waiting.data_as_of == ready.data_as_of
    # 오늘 한마디 행이 없으면 메모리 어댑터가 소개할 기록이 없을 때의 안내 문구를 반환한다
    assert (empty.status, empty.code, empty.area, empty.used_metrics) == ("EMPTY", "NO_RECORDS", None, ())
    assert empty.title == "오늘은 기록 없이도 내 하루를 돌아볼까요?" and empty.data_as_of == ready.data_as_of
    empty_basis = daily_message_result(empty, da.NOW.date()).basis
    assert empty_basis == "오늘 한마디로 소개할 활동·수면 기록이 없어 나온 안내 문구다"


@respx.mock
def test_runs_questions_and_prints_full_answers_with_tool_calls(settings, monkeypatch, tmp_path, capsys):
    llm = ScriptedLLM()
    respx.post(CHAT_URL).mock(side_effect=llm)
    monkeypatch.setattr(da, "get_settings", lambda: settings)
    monkeypatch.setattr(da, "git_commit", lambda: "abc1234")
    da.main(["--runs", "2", "--only", "today", "partial", "--save", "--out", str(tmp_path)])

    out = capsys.readouterr().out
    assert "## 오늘의 한마디 뭐야? (today)" in out and "## 한마디에 수면 기록 못 불러왔다던데? (partial)" in out
    assert out.count("- 1왕복 get_daily_message() → ok") == 4
    assert out.count(ANSWER) == 4
    assert "| 오늘의 한마디 뭐야? | READY | get_daily_message() (2) |" in out
    # 질문에 쓴 합성 한마디마다 LLM 에 전달한 도구 결과를 한 번씩 출력한다
    assert out.count("수면 기록은 불러오지 못해 문구를 고를 때 쓰지 않았다") == 1
    assert "PENDING_RECORDS" not in out and "오늘의 기록을 아직 확인하지 못했어요" not in out
    # 실행마다 질문 호출과 도구 결과가 붙은 호출로 LLM 을 두 번 부른다
    assert len(llm.bodies) == 8
    replies = [json.loads(b["messages"][-1]["content"]) for b in llm.bodies if b["messages"][-1]["role"] == "tool"]
    assert [len(reply["result"].get("notices", [])) for reply in replies] == [0, 0, 2, 2]
    records_path = next(tmp_path.glob("*_daily_message_answers_records.jsonl"))
    records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]
    assert [(r["name"], r["status"], r["run"]) for r in records] == [
        ("today", "READY", 1),
        ("today", "READY", 2),
        ("partial", "PARTIAL", 1),
        ("partial", "PARTIAL", 2),
    ]
    assert all(r["finish_reason"] == "stop" and r["answer"] == ANSWER for r in records)
    assert next(tmp_path.glob("*_daily_message_answers.md")).read_text(encoding="utf-8") == out


@respx.mock
def test_without_key_stops_before_calling_llm(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(da, "get_settings", lambda: Settings(_env_file=None))
    # 등록한 경로가 없어 LLM 을 부르면 실패한다
    with pytest.raises(SystemExit, match="LLM_API_KEY"):
        da.main(["--runs", "1", "--only", "today"])
