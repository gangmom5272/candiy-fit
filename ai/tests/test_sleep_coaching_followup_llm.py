"""수면 코칭 후속 질문에서 실제로 실패한 형태를 합성 데이터로 다시 검증한다. 답변 전문은 사람이 따로 검토한다."""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.graph import run_coach
from coach_ai.config import Settings
from coach_ai.llm.client import create_chat_model
from coach_ai.tools.periods import resolve_period
from eval.golden.fixtures import load_fixture
from tests.test_agent_graph import history_turn

CLAIM = "나 지난주보다도 더 자고 7시간 30분이나 잤는데도 왜 이렇게 피곤할까? 정말 자도 자도 피곤해"


@pytest.mark.llm
@pytest.mark.parametrize("attempt", range(3))
@pytest.mark.parametrize(
    "case",
    [
        "claim",
        "signals",
        "followup",
        "factors",
        "general",
        "past_claim",
        "same_period",
        "explicit_period",
        "general_plain",
        "general_weekend",
        "general_paraphrase",
        "closing",
        "closing_history",
        "ack_history",
        "background_exercise",
        "exercise_sleep",
        "background_exercise_paraphrase",
        "exercise_sleep_paraphrase",
    ],
)
async def test_sleep_coaching_routing_and_answer(case, attempt, tmp_path):
    fixture = load_fixture("synth-11")
    ctx = CoachContext(user_id=fixture.user_id, conversation_id="sleep317", request_id=f"{case}-{attempt}")
    question = {
        "claim": CLAIM,
        "signals": "내 산소포화도는 왜 이렇게 들쭉날쭉해?",
        "followup": "그럼 오늘부터 뭘 하면 돼?",
        "factors": "최근 28일 운동한 날과 안 한 날 수면 시간 차이를 비교해 줘.",
        "general": "건강 기록을 조회하지 말고 일반적인 수면 습관 하나만 알려줘.",
        "general_plain": "잠이 안 올 때 해 볼 만한 방법 알려 줘",
        "general_weekend": "주말에 몰아서 자는 습관 고치는 방법 있어?",
        "general_paraphrase": "숙면을 위한 생활습관을 알려줘.",
        "closing": "알겠어, 오늘은 일찍 자 볼게",
        "closing_history": "알겠어, 오늘은 일찍 자 볼게",
        "ack_history": "고마워, 알려준 대로 해볼게.",
        "background_exercise": "달리기를 꾸준히 했더니 심박수가 지난달보다 내려갔어?",
        "exercise_sleep": "등산 다녀온 날 잠을 더 잘 자는지 최근 2주 수면 기록 보여 줘",
        "background_exercise_paraphrase": "요즘 자전거를 타는데 지난주보다 이번 주 평균 심박수가 낮아졌어?",
        "exercise_sleep_paraphrase": "최근 2주에 수영한 날과 안 한 날의 수면이 어떤지 비교해 줘.",
        "past_claim": "지난주에는 매일 7시간 30분씩 잤는데 왜 피곤했을까? 지난주 기록만 분석해 줘.",
        "same_period": "그 기간의 수면 패턴을 다시 분석해서 개선할 점을 알려줘.",
        "explicit_period": "최근 7일 수면 패턴을 기준으로 오늘부터 뭘 하면 돼?",
    }[case]
    if case in ("followup", "closing_history", "ack_history"):
        ctx = replace(
            ctx,
            turns=(
                history_turn(
                    1,
                    CLAIM,
                    2,
                    "기록상 어젯밤 3시간 4분으로 지난주 평균 6시간 12분보다 짧습니다. "
                    "7시간 30분의 기록이 아직 동기화되지 않았거나 워치가 다르게 인식했을 수 있습니다.",
                ),
            ),
        )
    elif case in ("same_period", "explicit_period"):
        ctx = replace(
            ctx,
            turns=(history_turn(1, "지난주 수면 기록을 분석해 줘.", 2, "지난주 평균 수면은 6시간 23분입니다."),),
        )
    result = await run_coach(
        ctx, question, port=fixture.port(), model=create_chat_model(Settings()), sink=ListSink(), now=fixture.now
    )
    calls = [c for m in result.messages if isinstance(m, AIMessage) for c in m.tool_calls]
    records = [m.content for m in result.messages if isinstance(m, ToolMessage)]
    (tmp_path / "result.json").write_text(
        json.dumps(
            {
                "case": case,
                "attempt": attempt,
                "question": question,
                "calls": calls,
                "answer": result.answer,
                "tools": records,
                "error": result.error_code,
                "exception": repr(result.exception) if result.exception else None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    assert result.error_code is None, repr(result.exception)
    analyses = [c["args"].get("analysis") for c in calls if c["name"] == "get_sleep_records"]
    sleep_results = [
        json.loads(m.content)["result"]
        for m in result.messages
        if isinstance(m, ToolMessage) and m.name == "get_sleep_records" and "result" in json.loads(m.content)
    ]
    if case in (
        "general",
        "general_plain",
        "general_weekend",
        "general_paraphrase",
        "closing",
        "closing_history",
        "ack_history",
    ):
        assert not calls
    elif case in ("background_exercise", "background_exercise_paraphrase"):
        assert [c["name"] for c in calls] == ["compare_periods"]
        assert calls[0]["args"]["metric"] == "heart_rate"
    elif case in ("exercise_sleep", "exercise_sleep_paraphrase"):
        assert "factors" in analyses
        assert any(c["name"] == "get_exercise_sessions" for c in calls)
        # factors의 표본 부족 시 28일 확장과 구분해 모델이 요청한 기간을 검사한다.
        periods = [
            resolve_period(c["args"]["period"], fixture.now.date())
            for c in calls
            if c["name"] in ("get_sleep_records", "get_exercise_sessions")
        ]
        assert all((p.start.isoformat(), p.end.isoformat()) == ("2026-10-09", "2026-10-22") for p in periods)
    elif case == "signals":
        assert len(calls) == 1 and analyses == ["signals"]
    elif case == "factors":
        assert "factors" in analyses
    elif case in ("past_claim", "same_period", "explicit_period"):
        assert "pattern" in analyses
        expected = ("2026-10-16", "2026-10-22") if case == "explicit_period" else ("2026-10-12", "2026-10-18")
        assert sleep_results
        assert all((r["period"]["start"], r["period"]["end"]) == expected for r in sleep_results)
        if case != "explicit_period":
            assert all(not r.get("last_night_vs_week") for r in sleep_results)
            assert "어젯밤" not in result.answer
            assert "5시간 8분" not in result.answer and "308분" not in result.answer
        if case == "past_claim":
            assert "동기화" not in result.answer and "인식" not in result.answer
    else:
        assert "pattern" in analyses
        assert "동기화" not in result.answer and "인식" not in result.answer
        if case == "claim":
            assert "누워" in result.answer and "실제" in result.answer
            assert "5시간 8분" in result.answer or "308분" in result.answer
        else:
            assert "3시간 4분" not in result.answer
            assert all(r["period"]["start"] == "2026-10-09" for r in sleep_results)
            assert any("01:37" in r["text"] for r in sleep_results)
            # 단순 수면 시간/변동 폭 숫자가 아니라, 조회한 취침 시각을 행동과 같은 문장에 써야 한다.
            assert any(
                re.search(r"(?:0?1:37|1\s*시\s*37\s*분)", sentence)
                and any(word in sentence for word in ("일찍", "앞당", "준비"))
                for sentence in re.split(r"[.!?\n]", result.answer)
            )


class FirstReplyFromHistory:
    """첫 무조회 응답만 고정하고 재조회 요청부터는 실제 모델을 쓴다."""

    def __init__(self, model, answer):
        self.model = model
        self.answer = answer
        self.bound = model
        self.max_tokens = model.max_tokens

    def bind_tools(self, specs):
        self.bound = self.model.bind_tools(specs)
        return self

    def bind(self, **kwargs):
        self.bound = self.model.bind(**kwargs)
        return self

    async def astream(self, messages):
        if self.answer is not None:
            answer, self.answer = self.answer, None
            yield AIMessageChunk(content=answer)
        else:
            async for chunk in self.bound.astream(messages):
                yield chunk


@pytest.mark.llm
@pytest.mark.parametrize("attempt", range(3))
async def test_sleep_plan_rechecks_new_unqueried_clock(attempt, tmp_path):
    fixture = load_fixture("synth-11")
    ctx = CoachContext(
        user_id=fixture.user_id,
        conversation_id="sleep317-new-clock",
        request_id=f"new-clock-{attempt}",
        turns=(history_turn(1, CLAIM, 2, "기록상 어젯밤 3시간 4분으로 지난주 평균보다 짧습니다."),),
    )
    model = FirstReplyFromHistory(
        create_chat_model(Settings()), "평균 취침 시각인 오후 11시 45분에 맞춰 잠자리에 들 준비를 해 보세요."
    )
    result = await run_coach(
        ctx, "그럼 오늘부터 뭘 하면 돼?", port=fixture.port(), model=model, sink=ListSink(), now=fixture.now
    )
    calls = [c for m in result.messages if isinstance(m, AIMessage) for c in m.tool_calls]
    (tmp_path / "result.json").write_text(
        json.dumps({"calls": calls, "answer": result.answer, "error": result.error_code}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    assert result.error_code is None, repr(result.exception)
    assert result.answer_rechecks == 1
    assert any(c["name"] == "get_sleep_records" and c["args"].get("analysis") == "pattern" for c in calls)
    assert "11시 45분" not in result.answer
    assert re.search(r"(?:0?1:37|1\s*시\s*37\s*분)", result.answer)


@pytest.mark.llm
@pytest.mark.parametrize("attempt", range(3))
@pytest.mark.parametrize("forced_recheck", [False, True])
@pytest.mark.parametrize("case", ["claim", "signals"])
async def test_existing_error_conversation(case, forced_recheck, attempt, tmp_path):
    fixture = load_fixture("synth-11")
    question = CLAIM if case == "claim" else "내 산소포화도는 왜 이렇게 들쭉날쭉해?"
    # 시연 계정 synth-11에서 실제 실패한 기존 답변 전문. 수치는 현재 fixture와 달라 재사용도 검출한다.
    pairs = json.loads((Path(__file__).parent / "data/sleep317_error_history.json").read_text(encoding="utf-8"))[case]
    old_answer = pairs[0][1]
    turns = tuple(history_turn(2 * i + 1, q, 2 * i + 2, a) for i, (q, a) in enumerate(pairs))
    ctx = CoachContext(
        user_id=fixture.user_id,
        conversation_id="sleep317-history",
        request_id=f"{case}-{forced_recheck}-{attempt}",
        turns=turns,
    )
    model = create_chat_model(Settings())
    if forced_recheck:
        model = FirstReplyFromHistory(model, old_answer)
    result = await run_coach(ctx, question, port=fixture.port(), model=model, sink=ListSink(), now=fixture.now)
    calls = [c for m in result.messages if isinstance(m, AIMessage) for c in m.tool_calls]
    (tmp_path / "result.json").write_text(
        json.dumps(
            {
                "case": case,
                "forced_recheck": forced_recheck,
                "calls": calls,
                "answer": result.answer,
                "rechecks": result.answer_rechecks,
                "tools": [m.content for m in result.messages if isinstance(m, ToolMessage)],
                "error": result.error_code,
                "exception": repr(result.exception) if result.exception else None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    assert result.error_code is None, repr(result.exception)
    if forced_recheck:
        assert result.answer_rechecks == 1
    analyses = [c["args"].get("analysis") for c in calls if c["name"] == "get_sleep_records"]
    if case == "signals":
        assert len(calls) == 1 and analyses == ["signals"]
    else:
        assert "pattern" in analyses
        assert "동기화" not in result.answer and "인식" not in result.answer
        assert "누워" in result.answer and "실제" in result.answer
        first = re.split(r"[.!?\n]", result.answer)[0]
        assert "5시간 8분" in first or "308분" in first
