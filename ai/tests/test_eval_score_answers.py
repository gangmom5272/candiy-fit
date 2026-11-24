"""점수 답변 확인 실행기(eval/score_answers.py) 테스트. 실제 LLM 대신 respx 모의 응답을 쓴다."""

import json

import pytest
import respx

from coach_ai.agent.rules.guard import check
from coach_ai.config import Settings
from eval import score_answers as sa
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response

ANSWER = "오늘 점수는 30점이에요."


class ScriptedLLM:
    """질문이 마지막 메시지인 호출에는 점수 도구 호출을, 도구 결과가 붙은 호출에는 답변을 반환한다."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        if body["messages"][-1]["role"] == "tool":
            return text_response(ANSWER)
        return tool_call_response(("get_lifestyle_score", {"period": "days_ago:0"}))


def test_questions_cover_score_and_health_status_and_pass_guard():
    assert list(sa.QUESTIONS) == [
        "today", "reason", "raise", "resting_today", "resting_yesterday", "several_days", "health",
        "sleep_heart_range", "sleep_heart_avg", "sleep_heart_week", "spo2_range", "spo2_low", "session_calories",
        "day_calories", "session_distance", "daily_heart", "sleep_heart_two_weeks", "spo2_low_two_weeks",
    ]  # fmt: skip
    # 안전 규칙에 걸리면 LLM 을 부르지 않아 답변을 읽을 수 없다
    assert [q for q in sa.QUESTIONS.values() if check(q) is not None] == []
    assert sa.DEFAULT_RUNS == 3
    # 안정 시 심박수 질문과 같은 수면의 수면 중 평균 심박을 그 말 없이 묻는 질문을 함께 둔다
    assert "안정 시 심박수" in sa.QUESTIONS["resting_yesterday"] and "안정 시" not in sa.QUESTIONS["sleep_heart_avg"]
    # 수면 기록이 8개 이상인 기간을 묻는 질문은 기간 요약을 읽는다
    assert all("2주" in sa.QUESTIONS[name] for name in ("sleep_heart_two_weeks", "spo2_low_two_weeks"))


@respx.mock
def test_runs_questions_and_prints_answers_with_tool_calls(settings, monkeypatch, tmp_path, capsys):
    llm = ScriptedLLM()
    respx.post(CHAT_URL).mock(side_effect=llm)
    monkeypatch.setattr(sa, "get_settings", lambda: settings)
    monkeypatch.setattr(sa, "git_commit", lambda: "abc1234")
    sa.main(["--runs", "2", "--only", "today", "reason", "--save", "--out", str(tmp_path)])

    out = capsys.readouterr().out
    assert "## 오늘 점수 몇 점이야? (today)" in out and "## 왜 관리야? (reason)" in out
    assert out.count("- 1왕복 get_lifestyle_score(period=days_ago:0) → ok") == 4
    assert out.count(ANSWER) == 4
    assert "| 오늘 점수 몇 점이야? | get_lifestyle_score(period=days_ago:0) (2) |" in out
    # 답변과 비교할 수 있게 질문마다 LLM 에 전달한 도구 결과를 호출별로 한 번씩 출력한다
    assert out.count("### 도구 결과") == 2 and out.count("- get_lifestyle_score(period=days_ago:0)\n") == 2
    assert out.count('"display": "30점"') == 2
    # 실행마다 질문 호출과 도구 결과가 붙은 호출로 LLM 을 두 번 부른다
    assert len(llm.bodies) == 8
    records = [
        json.loads(line)
        for line in next(tmp_path.glob("*_score_answers_records.jsonl")).read_text(encoding="utf-8").splitlines()
    ]
    assert [(r["name"], r["run"]) for r in records] == [("today", 1), ("today", 2), ("reason", 1), ("reason", 2)]
    assert all(r["finish_reason"] == "stop" and r["answer"] == ANSWER for r in records)
    [result] = records[0]["tool_results"]
    assert result["call"] == "get_lifestyle_score(period=days_ago:0)"
    assert json.loads(result["content"])["result"]["total"]["display"] == "30점"
    assert next(tmp_path.glob("*_score_answers.md")).read_text(encoding="utf-8") == out


@respx.mock
def test_without_key_stops_before_calling_llm(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(sa, "get_settings", lambda: Settings(_env_file=None))
    # 등록한 경로가 없어 LLM 을 부르면 실패한다
    with pytest.raises(SystemExit, match="LLM_API_KEY"):
        sa.main(["--runs", "1", "--only", "today"])
