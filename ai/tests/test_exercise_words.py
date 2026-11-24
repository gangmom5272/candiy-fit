"""운동을 뜻하는 말 찾기 (agent/rules/exercise_words.py)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from coach_ai.agent.context import CoachContext
from coach_ai.agent.rules.exercise_words import exercise_texts, mentions_exercise
from coach_ai.history.types import StoredSummary
from tests.test_agent_graph import history_turn

CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")


@pytest.mark.parametrize(
    "text",
    [
        "운동량이 줄면 잠이 달라져?",
        "헬스 다녀온 날 깊은 잠",
        "러닝 뒤에 잠이 잘 와?",
        "저녁 달리기가 수면에 영향 있어?",
        "아침 조깅을 시작했는데",
        "마라톤 준비 중인데 잠은 괜찮아?",
        "수영 다녀오면 푹 자는 것 같아",
        "요가한 밤",
        "필라테스 끝나고 잤어",
        "자전거로 출퇴근한 날",
        "등산 다녀온 주말",
        "웨이트 하는 날",
        "홈트 한 날은 잘 자?",
        "Workout days and sleep",
        "gym 간 날",
    ],
)
def test_general_exercise_words_are_found(text):
    assert mentions_exercise([text])


@pytest.mark.parametrize(
    "text",
    [
        "주말에 늦게 일어난 날",
        "수면 사이클이 궁금해",
        "산책한 날은 잘 자?",
        "걸음 수가 많은 날",
        "카페인을 줄인 날",
    ],
)
def test_texts_without_exercise_words(text):
    assert not mentions_exercise([text])


def test_this_question_and_the_previous_user_question_are_searched():
    previous = history_turn(1, "요즘 운동을 저녁에 해", 2, "최근 운동 기록을 볼게요.")
    assert exercise_texts("그럼 잠은 어때?", CTX) == ("그럼 잠은 어때?",)
    texts = exercise_texts("그럼 잠은 어때?", replace(CTX, turns=(previous,)))
    assert texts == ("그럼 잠은 어때?", "요즘 운동을 저녁에 해")
    assert mentions_exercise(texts)


def test_previous_answer_and_summary_are_not_searched():
    # 앞 답에만 운동 말이 있거나 앞 질문이 요약에만 있으면 이번 질문만 찾는다
    previous = history_turn(1, "어제 잠 어땠어?", 2, "운동한 날보다 짧게 잤어요.")
    assert not mentions_exercise(exercise_texts("왜 그랬지?", replace(CTX, turns=(previous,))))
    summary = StoredSummary(text="사용자는 저녁 운동과 수면을 물었다.", until_seq=4)
    assert exercise_texts("왜 그랬지?", replace(CTX, summary=summary)) == ("왜 그랬지?",)
