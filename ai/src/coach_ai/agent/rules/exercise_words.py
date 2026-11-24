"""운동을 묻는 질문에서만 짧은 기간의 생활 요인 비교에 덧붙이는 최근 28일 운동 조건 비교.

14일보다 짧은 기간의 생활 요인 비교는 조건별 비교를 하지 않는다. 운동을 묻는 질문은 그 기간만으로 답할 수 없어
처리 함수가 최근 28일의 운동 조건 비교를 따로 덧붙인다. 운동을 묻지 않는 질문(주말 수면 등)에도 덧붙이면 결과가
그만큼 길어지고 묻지 않은 비교가 들어간다. 그래서 이번 질문과, 이어진 대화면 바로 앞 사용자 질문에 운동을 뜻하는
말이 있는지를 도구 노드가 정해 처리 함수에 알린다 (ToolRequest.asks_exercise). 앞 답과 이전 대화 요약은 찾지 않는다.
운동을 뜻하는 말은 운동과 흔한 운동 종류의 일반적인 이름이다. 걷기·산책은 걸음 조건에 가깝고, 수면 사이클처럼 다른
뜻으로 자주 쓰는 말은 넣지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from coach_ai.agent.context import CoachContext

_EXERCISE_WORDS = re.compile(
    r"운동|헬스|러닝|달리기|조깅|마라톤|수영|요가|필라테스|자전거|등산|웨이트|홈트"
    r"|exercise|workout|gym|running|jogging|yoga|swim|hiking",
    re.IGNORECASE,
)


def mentions_exercise(texts: Sequence[str]) -> bool:
    return any(_EXERCISE_WORDS.search(text) is not None for text in texts)


def exercise_texts(question: str, ctx: CoachContext) -> tuple[str, ...]:
    """운동을 뜻하는 말을 찾을 글. 이번 질문과, 이어진 대화면 바로 앞 사용자 질문이다.

    앞 질문이 요약에만 있으면 이번 질문만 찾는다.
    """
    return (question, ctx.turns[-1].question) if ctx.turns else (question,)
