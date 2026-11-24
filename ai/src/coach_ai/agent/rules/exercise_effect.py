"""운동이 잠에 준 효과를 사용자 자신의 기록으로 묻는 질문인지 정한다.

모델 호출 노드는 이런 질문에 도구 없이 끝난 첫 답을 보내지 않고, 생활 요인 비교를 조회하라는 안내를 붙여 한 번 다시
부른다. 기록으로 확인할 수 있는 운동에 한해 좁게 잡는다. 아래를 모두 갖춘 질문이다.

- 이번 질문에 운동을 뜻하는 말(exercise_words.py)과 효과를 묻는 말(영향, 때문, 효과, 도움이 되다, 원인, 탓, 덕분)이
  있다. "때문에"·"탓에"·"덕분에"·"영향으로"처럼 이유를 말하며 잇는 표현과 "좋아"처럼 다른 뜻으로 많이 쓰는 말은
  넣지 않는다
- 잠을 가리키는 말이 이번 질문이나, 이어진 대화면 바로 앞 사용자 질문에 있다. 앞 답과 요약은 찾지 않는다
- 사용자 자신을 가리키는 말(낱말 하나로 쓴 나·내·저·제·난, 요즘·요새)이 있거나 앞 대화(이전 대화 쌍이나 요약)에
  이어진 질문이다
- 일반을 가리키는 말(일반, 사람들, 남들, 다른 사람, 보통 사람, 연구), 운동 방법을 묻는 말(어떤 운동, 추천, 방법,
  루틴, 언제, 해야 등), 조회하지 말라는 말이 없다
"""

from __future__ import annotations

import re

from coach_ai.agent.context import CoachContext
from coach_ai.agent.rules.exercise_words import mentions_exercise

_EFFECT = re.compile(r"영향(?!으로)|때문(?!에)|효과|도움[이가]?\s?(?:되|돼|됐|될|됩)|원인|탓(?!에)|덕분(?!에)")
# 잠깐·잠시·잠재·잠금의 "잠"은 잠이 아니다
_SLEEP = re.compile(r"잠(?![깐시재금])|수면|숙면|불면|취침|기상|잤|(?:잘|푹)\s?자")
# 저녁·제일·내일·나이 같은 낱말 안의 글자는 찾지 않는다
_SELF = re.compile(
    r"(?<![가-힣])(?:(?:나|내|저|제)(?:는|가|도|를|의|게|만|랑|한테|에게)?|난)(?![가-힣])|요즘|요즈음|요새|요사이"
)
_GENERAL = re.compile(r"일반|사람들|남들|다른\s?사람|보통\s?사람|연구")
_METHOD = re.compile(
    r"(?:어떤|무슨|어느)\s?운동|추천|방법|요령|루틴|자세|강도|어떻게\s?(?:해야|하면|하는)|언제|몇\s?(?:시|분|번|회)|해야"
)
_NO_LOOKUP = re.compile(r"조회\s?(?:없이|하지|말고)|기록\s?(?:없이|말고|보지)")


def asks_exercise_effect(question: str, ctx: CoachContext) -> bool:
    """운동이 잠에 준 효과를 사용자 자신의 기록으로 묻는 질문이면 참이다."""
    if not (mentions_exercise([question]) and _EFFECT.search(question)):
        return False
    if _GENERAL.search(question) or _METHOD.search(question) or _NO_LOOKUP.search(question):
        return False
    texts = (question, ctx.turns[-1].question) if ctx.turns else (question,)
    if not any(_SLEEP.search(text) for text in texts):
        return False
    follow_up = bool(ctx.turns) or ctx.summary is not None
    return follow_up or _SELF.search(question) is not None
