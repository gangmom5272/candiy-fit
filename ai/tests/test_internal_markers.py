from dataclasses import replace

import pytest
import respx

from coach_ai.agent.nodes import model as model_node
from coach_ai.agent.rules.internal_markers import MarkerFilter, clean, has_markers
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response
from tests.test_agent_graph import (
    CTX,
    REUSED_PARTS,
    STEP_TURN,
    STEPS_CALL,
    bodies,
    last_user_text,
    recheck_logs,
    run,
    tokens,
)

# 개발 서버에서 화면에 그대로 나온 답의 형식. 끝 문장은 합성이다
LEAD = "스트레스나 수면 부족이 산소포화도 수치에 영향을 주는 조건이 될 수 있는지 기록을 통해 확인해 보겠습니다."
ANSWER = "스트레스나 수면 부족이 산소포화도 수치 변화의 원인이라고 단정할 수는 없으나, 기록으로 함께 살펴볼 수 있어요."
LEAKED = f"{LEAD}\n}}\n<|channel>thought\n<channel|>{ANSWER}"
# 내부 표시 없이 괄호 줄과 "<" 가 든 답. 한 글자도 바뀌면 안 된다
PLAIN = (
    "글 속의 3 < 5 와 [표시] 는 지우지 않아요.\n(\n괄호만 있는 줄도 바꾸지 않아요)\n}\n"
    "<b>표시처럼 보이는 글</b>과 a<|b, <chan 도 그대로예요.\n\n  [ ]\n끝"
)
QUESTION = "그럼 오늘은?"


def pieces(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def stream(text: str, size: int) -> tuple[str, MarkerFilter]:
    markers = MarkerFilter()
    out = "".join(markers.feed(p) for p in pieces(text, size)) + markers.finish()
    return out, markers


def test_held_answer_keeps_only_text_after_last_close():
    assert clean(LEAKED) == ANSWER


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"{LEAD}\n}}\n<|channel>thought\n<channel|>", ""),
        (f"{LEAD}<|channel>thought\n생각 중", LEAD),
        (f"앞 글<channel|>\n\n{ANSWER}\n)\n", f"{ANSWER}\n"),
        ('수면 <|"|>기록<|"|>을 볼게요.', "수면 기록을 볼게요."),
        (f"<|channel>a<channel|>앞<|channel>b<channel|>{ANSWER}", ANSWER),
    ],
    ids=["empty", "unclosed_thought", "stray_close_and_bracket_line", "token", "two_sections"],
)
def test_held_answer_cleanup(text, expected):
    assert has_markers(text)
    assert clean(text) == expected


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 11, len(LEAKED)])
def test_streamed_markers_split_into_pieces_are_not_sent(size):
    out, markers = stream(LEAKED, size)

    # 이미 보낸 앞 글은 그대로 두고, 괄호 줄과 사고 채널 구간은 내보내지 않는다
    assert out == f"{LEAD}\n{ANSWER}"
    assert markers.found


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 11, len(PLAIN)])
def test_text_without_markers_is_unchanged(size):
    out, markers = stream(PLAIN, size)

    assert not has_markers(PLAIN)
    assert clean(PLAIN) == PLAIN
    assert out == PLAIN and not markers.found


@pytest.fixture
def recorder() -> MetricsRecorder:
    return MetricsRecorder()


@pytest.fixture
def model(settings, recorder):
    return create_chat_model(settings, recorder=recorder, retry_base_delay=0.0)


def cleanup_logs(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "internal markers removed" in r.getMessage()]


# 첫 호출은 앞 대화가 없어도 모아 두었다가 보낸다
@pytest.mark.parametrize("turns", [(STEP_TURN,), ()], ids=["after_previous_conversation", "first_turn"])
@respx.mock
async def test_held_first_answer_is_sent_after_cleanup(model, caplog, turns):
    route = respx.post(CHAT_URL).mock(return_value=text_response(*pieces(LEAKED, 4)))
    result, events = await run(model, QUESTION, ctx=replace(CTX, turns=turns))

    assert route.call_count == 1
    assert "".join(tokens(events)) == ANSWER == result.answer
    assert result.messages[-1].content == ANSWER
    assert (result.marker_cleanups, result.answer_rechecks, result.finish_reason) == (1, 0, "stop")
    [log] = cleanup_logs(caplog)
    assert "request_id=r-1" in log and "held=True" in log and "산소포화도" not in log


# 첫 호출은 모아 두므로 흘려보내는 답은 도구 왕복 뒤의 호출로 검사한다
@pytest.mark.parametrize("size", [1, 3, 8])
@respx.mock
async def test_streamed_answer_does_not_send_markers(model, size):
    route = respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(STEPS_CALL), text_response(*pieces(LEAKED, size))]
    )
    result, events = await run(model, "오늘은 뭘 하면 좋아?")

    assert route.call_count == 2
    assert all("<" not in t and "|" not in t and "}" not in t for t in tokens(events))
    assert result.answer == f"{LEAD}\n{ANSWER}" == result.messages[-1].content
    assert (result.marker_cleanups, result.answer_rechecks) == (1, 0)


@respx.mock
async def test_held_answer_empty_after_cleanup_is_rechecked_once(model, caplog):
    route = respx.post(CHAT_URL).mock(
        side_effect=[text_response(f"{LEAD}\n}}\n<|channel>thought\n<channel|>"), text_response(ANSWER)]
    )
    result, events = await run(model, QUESTION, ctx=replace(CTX, turns=(STEP_TURN,)))

    first, again = bodies(route)
    # 앞 답 수치 되돌림과 같이 이번 질문 끝에 안내를 붙여 한 번 다시 부른다. 정리한 빈 답은 화면에 나가지 않는다
    assert last_user_text(first) == QUESTION
    assert last_user_text(again) == f"{QUESTION}\n\n{model_node.RECHECK_NOTICE}"
    assert tokens(events) == [ANSWER] and result.answer == ANSWER
    assert (result.answer_rechecks, result.marker_cleanups, result.tool_rounds) == (1, 1, 0)
    [log] = recheck_logs(caplog)
    assert "reason=internal_markers" in log


@respx.mock
async def test_streamed_answer_empty_after_cleanup_is_rechecked_after_sent_lead(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(STEPS_CALL),
            text_response(f"{LEAD}\n}}\n<|channel>thought\n<channel|>"),
            text_response(ANSWER),
        ]
    )
    result, _ = await run(model, "오늘은 뭘 하면 좋아?")

    # 이미 보낸 앞 글은 되돌릴 수 없어 다시 받은 답이 그 뒤에 이어진다
    assert route.call_count == 3
    assert result.answer == f"{LEAD}\n{ANSWER}"
    assert (result.answer_rechecks, result.marker_cleanups) == (1, 1)


@respx.mock
async def test_empty_after_cleanup_follows_empty_response_rule_once_recheck_is_used(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            text_response(*REUSED_PARTS),
            text_response("}\n<|channel>thought\n<channel|>"),
            text_response(ANSWER),
        ]
    )
    result, events = await run(model, QUESTION, ctx=replace(CTX, turns=(STEP_TURN,)))

    # 되돌림은 요청마다 한 번이라 두 번째 빈 답은 빈 응답 규칙대로 같은 요청을 한 번 다시 보낸다
    _, again, resent = bodies(route)
    assert again == resent
    assert tokens(events) == [ANSWER]
    assert (result.answer_rechecks, result.marker_cleanups) == (1, 1)


@respx.mock
async def test_answers_without_markers_are_sent_unchanged(model):
    parts = pieces(PLAIN, 6)
    respx.post(CHAT_URL).mock(
        side_effect=[text_response(*parts), tool_call_response(STEPS_CALL), text_response(*parts)]
    )
    held, held_events = await run(model, QUESTION, ctx=replace(CTX, turns=(STEP_TURN,)))
    # 흘려보내는 답은 도구 왕복 뒤의 호출이다
    streamed, streamed_events = await run(model, "오늘은 뭘 하면 좋아?")

    # 조각 경계는 래퍼가 표시 앞부분을 잡아 두는 만큼 달라질 수 있고, 이어 붙인 글은 같다
    assert "".join(tokens(held_events)) == "".join(tokens(streamed_events)) == PLAIN
    assert held.answer == streamed.answer == PLAIN
    assert held.marker_cleanups == streamed.marker_cleanups == 0
