"""운동이 잠에 준 효과를 묻는 질문의 다시 조회 (agent/rules/exercise_effect.py, nodes/model.py)."""

from __future__ import annotations

from dataclasses import replace

import pytest
import respx

from coach_ai.agent.context import CoachContext
from coach_ai.agent.nodes import model as model_node
from coach_ai.agent.rules.exercise_effect import asks_exercise_effect
from coach_ai.history.types import StoredSummary
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response
from tests.test_agent_graph import (
    CTX,
    SLEEP_TURN,
    SleepAnalysisHandler,
    bodies,
    history_turn,
    last_user_text,
    recheck_logs,
    run,
    tokens,
)

# 수치 없는 앞 대화. 앞 질문에 잠을 가리키는 말이 있다
SLEEP_QUESTION_TURN = history_turn(1, "지난주 잠은 어땠어?", 2, "지난주 수면 기록을 정리해 드렸어요.")
SUMMARY = StoredSummary(text="사용자는 수면 기록을 물었다.", until_seq=2)


def ctx_with(*turns, summary: StoredSummary | None = None) -> CoachContext:
    return replace(CTX, turns=turns, summary=summary)


@pytest.mark.parametrize(
    ("question", "ctx"),
    [
        ("내가 저녁마다 헬스를 하는데 그게 잠에 영향을 줄까?", CTX),
        ("요즘 요가를 시작했는데 수면이 나아진 게 그 덕분일까?", CTX),
        ("제가 수영을 다녀온 날 푹 자는 건 수영 때문인가요?", CTX),
        ("나 요새 잠을 설치는데 웨이트 탓일까?", CTX),
        ("요새 숙면을 못 하는 원인이 운동 부족일까?", CTX),
        ("러닝 시간을 늘린 게 효과가 있었어?", ctx_with(SLEEP_QUESTION_TURN)),
        ("저녁 운동이 깊은 잠에 도움이 됐을까?", ctx_with(summary=SUMMARY)),
    ],
    ids=["self_word", "lately", "self_polite", "blame", "cause", "follow_up_sleep_question", "summary_only"],
)
def test_own_exercise_effect_on_sleep_is_found(question, ctx):
    assert asks_exercise_effect(question, ctx)


@pytest.mark.parametrize(
    ("question", "ctx"),
    [
        # 사용자 자신을 가리키는 말도 앞 대화도 없는 일반 지식 질문
        ("운동이 수면에 어떤 영향을 줘?", CTX),
        ("저녁 달리기가 잠에 도움이 될까?", CTX),
        # 저녁·내일 안의 글자는 사용자 자신을 가리키는 말이 아니다
        ("내일 운동하면 잠에 영향이 있어?", CTX),
        # 일반을 가리키는 말
        ("일반적으로 운동이 잠에 도움이 돼?", ctx_with(SLEEP_QUESTION_TURN)),
        ("사람들은 운동하면 잠이 잘 온다는데 나도 효과가 있어?", CTX),
        # 운동 방법을 묻는 질문
        ("그럼 잠에 도움이 되는 운동 추천해 줘", ctx_with(SLEEP_QUESTION_TURN)),
        ("어떤 운동을 해야 수면에 효과가 있어?", ctx_with(SLEEP_QUESTION_TURN)),
        ("운동은 언제 하는 게 숙면에 도움이 돼?", ctx_with(SLEEP_QUESTION_TURN)),
        ("자기 몇 시간 전까지 운동을 끝내야 잠에 영향이 없어?", ctx_with(SLEEP_QUESTION_TURN)),
        # 이유를 말하며 잇는 표현과 다른 뜻으로 많이 쓰는 말
        ("나 회식 때문에 요가를 쉬었는데 수면 팁 하나만 줄래?", CTX),
        ("나 운동 덕분에 잘 자고 있어", CTX),
        ("나 저녁 운동하는 거 잠에 좋아?", CTX),
        # 조회하지 말라는 질문
        ("기록 보지 말고, 내가 운동하는 게 잠에 도움이 될까?", CTX),
    ],
    ids=[
        "general_no_self",
        "general_no_self_2",
        "word_inside_other_word",
        "general_word",
        "people_word",
        "recommend",
        "which_exercise",
        "when",
        "how_long_before",
        "reason_given",
        "thanks_to_reason_given",
        "good_word",
        "no_lookup",
    ],
)
def test_general_method_and_other_questions_are_not_found(question, ctx):
    assert not asks_exercise_effect(question, ctx)


@pytest.mark.parametrize(
    ("question", "ctx"),
    [
        # 효과를 묻는 대상이 잠이 아니다 (잠깐의 "잠"은 잠이 아니다)
        ("나 요즘 헬스를 하는데 피로가 그 때문일까?", CTX),
        ("나 오늘 잠깐 요가를 했는데 체력에 효과가 있어?", CTX),
        # 잠을 가리키는 말이 앞 답에만 있다
        (
            "러닝을 늘린 게 효과가 있었어?",
            ctx_with(history_turn(1, "이번 주 걸음 수는 어때?", 2, "수면은 짧았지만 걸음 수는 늘었어요.")),
        ),
        # 운동을 뜻하는 말이 앞 질문에만 있다
        ("그게 잠에 영향이 있어?", ctx_with(history_turn(1, "요즘 운동을 밤에 해", 2, "운동 기록을 볼게요."))),
        # 운동을 뜻하는 말이 없다
        ("요즘 커피를 줄인 게 잠에 영향이 있어?", CTX),
    ],
    ids=["fatigue", "short_while", "sleep_only_in_answer", "exercise_only_in_previous_question", "no_exercise"],
)
def test_questions_without_exercise_effect_on_sleep_are_not_found(question, ctx):
    assert not asks_exercise_effect(question, ctx)


@pytest.fixture
def recorder() -> MetricsRecorder:
    return MetricsRecorder()


@pytest.fixture
def model(settings, recorder):
    return create_chat_model(settings, recorder=recorder, retry_base_delay=0.0)


EFFECT_QUESTION = "러닝 시간을 늘린 게 효과가 있었어?"
# 수치 없이 조회하지 않은 경향을 말한 답. 결과에 없는 수치 검사에는 걸리지 않는다
TREND_PARTS = ("러닝을 한 날에는 ", "깊은 잠이 짧은 경향이 있어요.")
FACTORS_CALL = ("get_sleep_records", {"analysis": "factors"})
FACTORS_ANSWER = "생활 요인 비교에서 러닝을 한 날의 수면 효율이 조금 높았어요."


def exercise_logs(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "exercise effect rechecked" in r.getMessage()]


@respx.mock
async def test_exercise_effect_answer_without_tools_is_sent_back_once_with_notice(model, caplog):
    handler = SleepAnalysisHandler()
    route = respx.post(CHAT_URL).mock(
        side_effect=[text_response(*TREND_PARTS), tool_call_response(FACTORS_CALL), text_response(FACTORS_ANSWER)]
    )
    result, events = await run(
        model, EFFECT_QUESTION, ctx=ctx_with(SLEEP_QUESTION_TURN), handlers={"get_sleep_records": handler}
    )

    assert result.answer == FACTORS_ANSWER
    # 되돌린 답은 화면으로 나가지 않는다
    assert tokens(events) == [FACTORS_ANSWER]
    assert (result.exercise_rechecks, result.answer_rechecks, result.unsupported_rechecks) == (1, 0, 0)
    assert result.tool_rounds == 1 and handler.calls == [(None, "factors")]
    first, again, after_tool = bodies(route)
    # 첫 요청 본문은 그대로이고, 안내는 다시 보낸 요청의 이번 질문 끝에만 붙는다
    assert last_user_text(first) == EFFECT_QUESTION
    assert last_user_text(again) == f"{EFFECT_QUESTION}\n\n{model_node.EXERCISE_EFFECT_NOTICE}"
    assert first["messages"][:-1] == again["messages"][:-1] and first["tools"] == again["tools"]
    assert last_user_text(after_tool) == EFFECT_QUESTION
    # 안내와 되돌린 답은 상태와 결과 메시지에 포함되지 않는다
    assert all(model_node.EXERCISE_EFFECT_NOTICE not in str(m.content) for m in result.messages)
    assert all("짧은 경향" not in str(m.content) for m in result.messages)
    [log] = exercise_logs(caplog)
    assert "request_id=r-1" in log and "러닝" not in log
    assert recheck_logs(caplog) == []


@respx.mock
async def test_exercise_effect_recheck_happens_only_once(model):
    question = "내가 요가를 다녀온 날 잠에 도움이 됐을까?"
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(*TREND_PARTS), text_response(*TREND_PARTS)])
    result, events = await run(model, question)

    # 앞 대화가 없어도 사용자 자신을 가리키는 말이 있으면 검사한다. 다시 받은 답에도 도구 호출이 없으면 그대로 보낸다
    assert route.call_count == 2
    assert last_user_text(bodies(route)[1]) == f"{question}\n\n{model_node.EXERCISE_EFFECT_NOTICE}"
    assert tokens(events) == list(TREND_PARTS)
    assert (result.answer, result.exercise_rechecks, result.tool_rounds) == ("".join(TREND_PARTS), 1, 0)


@pytest.mark.parametrize(
    ("question", "ctx"),
    [
        ("요가가 수면에 어떤 영향을 줘?", CTX),
        ("그럼 잠에 도움이 되는 운동 추천해 줘", ctx_with(SLEEP_QUESTION_TURN)),
        ("나 요즘 헬스를 하는데 피로가 그 때문일까?", CTX),
    ],
    ids=["general", "recommend", "not_sleep"],
)
@respx.mock
async def test_other_questions_answered_without_tools_are_sent_as_received(model, caplog, question, ctx):
    route = respx.post(CHAT_URL).mock(return_value=text_response(*TREND_PARTS))
    result, events = await run(model, question, ctx=ctx)

    assert route.call_count == 1
    assert tokens(events) == list(TREND_PARTS)
    assert result.exercise_rechecks == 0 and exercise_logs(caplog) == []


@respx.mock
async def test_first_reply_with_tool_call_is_not_rechecked_for_exercise_effect(model):
    handler = SleepAnalysisHandler()
    route = respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(FACTORS_CALL, text=("생활 요인 비교를 볼게요. ",)), text_response("네.")]
    )
    result, events = await run(
        model, EFFECT_QUESTION, ctx=ctx_with(SLEEP_QUESTION_TURN), handlers={"get_sleep_records": handler}
    )

    assert route.call_count == 2
    assert tokens(events) == ["생활 요인 비교를 볼게요. ", "네."]
    assert (result.exercise_rechecks, result.tool_rounds) == (0, 1)


@respx.mock
async def test_tool_limit_call_is_not_checked_for_exercise_effect(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response(*TREND_PARTS))
    result, _ = await run(model, EFFECT_QUESTION, ctx=ctx_with(SLEEP_QUESTION_TURN), max_tool_rounds=0)

    assert route.call_count == 1
    assert (result.finish_reason, result.exercise_rechecks) == ("tool_limit", 0)


@pytest.mark.parametrize(
    ("ctx", "parts", "notice", "counts"),
    [
        (
            ctx_with(history_turn(1, "지난주 잠은 어땠어?", 2, "깊은 잠은 평균 58분이었어요.")),
            ("러닝을 한 날에도 깊은 잠이 ", "58분으로 비슷했어요."),
            "RECHECK_NOTICE",
            (1, 0, 0),
        ),
        (
            ctx_with(SLEEP_QUESTION_TURN),
            ("러닝을 한 날 깊은 잠이 ", "52분으로 짧았어요."),
            "UNSUPPORTED_NOTICE",
            (0, 1, 0),
        ),
    ],
    ids=["previous_number", "unsupported_number"],
)
@respx.mock
async def test_number_rechecks_come_before_exercise_effect(model, caplog, ctx, parts, notice, counts):
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(*parts), text_response("네.")])
    result, _ = await run(model, EFFECT_QUESTION, ctx=ctx)

    # 앞 답 수치 → 조회하지 않은 수치 → 운동 효과 순서로 하나만 따른다
    assert route.call_count == 2
    assert last_user_text(bodies(route)[1]) == f"{EFFECT_QUESTION}\n\n{getattr(model_node, notice)}"
    assert (result.answer_rechecks, result.unsupported_rechecks, result.exercise_rechecks) == counts
    assert exercise_logs(caplog) == []


@respx.mock
async def test_exercise_effect_comes_before_sleep_plan_lookup(model):
    # 수면 계획 후속에도 걸리는 질문이다. 운동 효과 안내로 한 번만 다시 부르고 pattern 은 부르지 않는다
    handler = SleepAnalysisHandler()
    question = "그러면 내가 요가를 하면 잠에 도움이 될까?"
    route = respx.post(CHAT_URL).mock(
        side_effect=[text_response("요가는 몸을 이완해 ", "잠드는 데 도움이 될 수 있어요."), text_response("네.")]
    )
    result, _ = await run(model, question, ctx=ctx_with(SLEEP_TURN), handlers={"get_sleep_records": handler})

    assert route.call_count == 2 and handler.calls == []
    assert last_user_text(bodies(route)[1]) == f"{question}\n\n{model_node.EXERCISE_EFFECT_NOTICE}"
    assert (result.answer, result.answer_rechecks, result.exercise_rechecks) == ("네.", 0, 1)


@respx.mock
async def test_exercise_effect_is_not_rechecked_after_cleanup_recheck(model, caplog):
    route = respx.post(CHAT_URL).mock(
        side_effect=[text_response("}\n<|channel>thought\n<channel|>"), text_response(*TREND_PARTS)]
    )
    result, events = await run(model, EFFECT_QUESTION, ctx=ctx_with(SLEEP_QUESTION_TURN))

    # 내부 표시를 정리했더니 빈 답을 먼저 되돌렸으면 다시 받은 답은 운동 효과로 다시 되돌리지 않는다
    assert route.call_count == 2
    assert "".join(tokens(events)) == "".join(TREND_PARTS) == result.answer
    assert (result.answer_rechecks, result.exercise_rechecks, result.marker_cleanups) == (1, 0, 1)
    assert exercise_logs(caplog) == []


@respx.mock
async def test_blank_after_cleanup_is_not_rechecked_after_exercise_effect_recheck(model):
    handler = SleepAnalysisHandler()
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            text_response(*TREND_PARTS),
            tool_call_response(FACTORS_CALL),
            text_response("}\n<|channel>thought\n<channel|>"),
            text_response(FACTORS_ANSWER),
        ]
    )
    result, _ = await run(
        model, EFFECT_QUESTION, ctx=ctx_with(SLEEP_QUESTION_TURN), handlers={"get_sleep_records": handler}
    )

    # 첫 호출에서 되돌림을 썼으면 도구 왕복 뒤 정리했더니 빈 답은 빈 응답 규칙대로 같은 요청을 한 번 다시 보낸다
    _, _, after_tool, resent = bodies(route)
    assert after_tool == resent
    assert result.answer == FACTORS_ANSWER
    assert (result.exercise_rechecks, result.answer_rechecks, result.marker_cleanups) == (1, 0, 1)
