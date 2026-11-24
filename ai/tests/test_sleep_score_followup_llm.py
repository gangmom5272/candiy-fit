"""실제 LLM의 수면 점수 후속 재조회 회귀. 합성 포트만 사용한다."""

import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from coach_ai.agent.events import ListSink
from coach_ai.agent.graph import run_coach
from coach_ai.config import Settings
from coach_ai.llm.client import create_chat_model
from coach_ai.tools.sleep_score import SLEEP_SCORE_RULE
from tests.test_agent_graph import history_turn
from tests.test_lifestyle_score import CTX, NOW, port, row


@pytest.mark.llm
@pytest.mark.parametrize("attempt", range(3))
@pytest.mark.parametrize(
    "question",
    [
        "수면 점수는 어떤 기록으로 계산돼? 오늘 0점이 기록 없음인지 실제 점수인지도 구분해서 알려줘.",
        "그럼 그 점수는 어떻게 계산한 거야?",
    ],
)
async def test_sleep_score_explanation_refetches(question, attempt, tmp_path):
    ctx = replace(
        CTX,
        turns=(
            history_turn(
                1,
                "오늘 수면 점수와 수면 시간을 알려줘.",
                2,
                "오늘 수면 점수는 0점(관리 등급)이며, 수면 시간은 3시간 4분입니다.",
            ),
        ),
    )
    health = port(row(sleep_score=0, sleep_grade="care", sleep_minutes=184))
    result = await run_coach(
        ctx,
        question,
        port=health,
        model=create_chat_model(Settings()),
        sink=ListSink(),
        now=NOW,
    )
    calls = [c for m in result.messages if isinstance(m, AIMessage) for c in m.tool_calls]
    score_results = [m for m in result.messages if isinstance(m, ToolMessage) and m.name == "get_lifestyle_score"]
    record = {
        "question": question,
        "attempt": attempt,
        "calls": calls,
        "answer": result.answer,
        "error": result.error_code,
        "score_results": [m.content for m in score_results],
    }
    (tmp_path / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k != "score_results"}, ensure_ascii=False))
    assert result.error_code is None
    assert any(c["name"] == "get_lifestyle_score" and c["args"] == {"period": "days_ago:0"} for c in calls)
    assert any(json.loads(m.content)["result"]["sleep"]["rule"] == SLEEP_SCORE_RULE for m in score_results)
    assert "수면 시간" in result.answer and "0점" in result.answer
    # 정확한 문장 평가는 저장된 답변을 함께 검토한다. 이것만으로 자유 문장 전체를 판정하지 않는다.
    assert "종합하여 계산" not in result.answer


@pytest.mark.llm
@pytest.mark.parametrize(
    "question",
    [
        "친구의 수면 점수는 몇 점이야?",
        "건강 기록을 조회하지 말고 일반적인 산책 습관을 한 문장으로 알려줘.",
    ],
)
async def test_unrelated_questions_do_not_fetch_score(question):
    result = await run_coach(
        CTX,
        question,
        port=port(row()),
        model=create_chat_model(Settings()),
        sink=ListSink(),
        now=NOW,
    )
    assert result.error_code is None
    assert not [c for m in result.messages if isinstance(m, AIMessage) for c in m.tool_calls]
