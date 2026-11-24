"""기간을 말하지 않은 수면 분석 질문의 조회 기간.

데이터 쪽은 기간이 없는 분석 질문을 패턴·이상 신호 14일, 생활 요인 28일로 정했다 (tools/sleep_coaching.py 의 기본
기간). 모델은 이런 질문에도 수면 분석을 최근 7일로 부르는 일이 많다. 그래서 이번 질문, 바로 앞 사용자 질문, 바로
앞 답 어디에도 기간 말이 없으면 그 호출의 기간을 빼고 실행해 분석의 기본 기간을 쓴다. 기간 말은 넓게 찾고, 하나라도
있으면 모델이 넣은 기간을 그대로 둔다. 기간 말을 놓치면 사용자가 말한 기간을 지우게 되므로 애매한 말도 기간 말로 본다.
때를 가리키지 않는 말(새벽·한밤중 같은 하루 안의 때, 매일·계속 같은 버릇, "자기 전" 같은 "~기 전")은 기간 말이 아니다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import get_args

from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall
from coach_ai.tools.schemas import SleepAnalysis

# 기간을 말하지 않은 분석 질문에 모델이 넣는 기간. 이 값만 뺀다
_RECENT_7_DAYS = "recent_days:7"
_ANALYSES: tuple[SleepAnalysis, ...] = get_args(SleepAnalysis)
_PERIOD_WORDS = re.compile(
    # 숫자와 단위: 7일, 2주, 3달, 6개월, 1년, 5밤, 1박, 2학기, 1분기, 10월 21일
    r"\d+\s*(?:일|주|달|개월|년|밤|박|월|학기|분기)"
    # 숫자로 쓴 날짜: 10/21, 10.21, 2026-10-21. 7.5시간, 1-2시간, 90-95% 같은 수는 빼고 찾는다
    r"|\d+\s?[/.-]\s?\d+(?!\d|\s?(?:시간|분|%))"
    # 글로 쓴 날 수: 하루, 이틀, 일주일, 보름, 며칠, 한 달, 몇 주, 두 밤, 일 년, 이 주 동안, 삼일째, 수개월
    r"|하루|하룻밤|이틀|사흘|나흘|열흘|보름|며칠|일주일|반년|몇\s?(?:일|년|개월)"
    r"|(?<![가-힣])(?:한두|한|두|세|석|네|넉|몇)\s?(?:주|달|해)|(?<![가-힣])(?:두|세|네|몇)\s?밤"
    r"|(?<![가-힣])(?:일|이|삼|사|오|육|칠|팔|구|십)\s?(?:년|개월|주일|(?:일|주)(?:째|간|\s?동안|\s?전))"
    r"|(?<![가-힣])수(?:일|주|개월|년|달)|온종일|밤새|나날"
    # 날: 오늘, 어제, 그제, 어젯밤, 그날, 그 뒤, 다음 날, 첫날
    r"|오늘|어제|어저께|그제|그저께|어젯밤|엊저녁|간밤|내일|모레|전날|당일|이날"
    r"|그\s?(?:날|때|주|달|후|뒤|전)|다음\s?(?:주|달|날)|(?:첫|마지막|둘째|셋째|넷째)\s?(?:주|날|달|밤)"
    # 주·달·해: 이번 주, 지난주, 저번 달, 금주, 올해, 월말, 상반기, 시월
    r"|이번\s?(?:주|달)|이번에|요번|지난|저번|이달|금주|금월|전주|전월|차주|익월|주간|월간|연간"
    r"|올해|올\s?들어|작년|내년|금년|새해|주초|월초|월말|연초|연말|초순|중순|하순|상반기|하반기|분기|학기"
    r"|(?<![가-힣])(?:일|이|삼|사|오|유|칠|팔|구|시|십|십일|십이)월(?!요일)"
    # 요즘·최근: 요새, 근래, 한동안. "요 며칠"은 며칠로 찾는다
    r"|요즘|요즈음|요새|요사이|최근|근래|즈음|맘때|무렵|한동안|한참|그동안|그간|그새|여태|여지껏"
    r"|이제|이때까지|지금|현재|일전|접때|아까|방금|(?:조금|좀|잠시|얼마)\s?전|오래\s?전|오랫동안|오랜만|오래간만"
    # 지난 때와 견주는 말: 예전, 평소, 이전, 그 뒤로, 전에는. 자기 전처럼 "~기 전"은 하루 안의 때라 뺀다
    r"|예전|옛날|과거|시절|평소|처음에는|처음엔|이전|이후|후로|뒤로|(?<!기)(?<!기\s)전(?:에|엔|보다)"
    r"|어릴\s?때|어렸을\s?때|젊었을\s?때|언제|기간|시기"
    # 주말·평일·요일·계절과 이름으로 부르는 기간
    r"|주말|평일|주중|휴일|쉬는\s?날|요일|[월화수목금토일]욜|불금|휴가|연휴|방학|명절|추석|설날"
    r"|여름|가을|겨울|(?<![가-힣])봄"
)


def has_period_word(text: str) -> bool:
    return _PERIOD_WORDS.search(text) is not None


def period_texts(question: str, ctx: CoachContext) -> tuple[str, ...]:
    """기간 말을 찾을 글. 이번 질문, 바로 앞 사용자 질문, 바로 앞 답이다.

    앞 질문·답이 요약에만 있으면 요약 글을 대신 넣는다.
    """
    if ctx.turns:
        return (question, ctx.turns[-1].question, ctx.turns[-1].answer)
    return (question,) if ctx.summary is None else (question, ctx.summary.text)


def with_default_period(call: ToolCall, texts: Sequence[str]) -> ToolCall:
    """수면 분석을 최근 7일로 부른 호출이고 texts 어디에도 기간 말이 없으면 기간을 뺀 호출을 반환한다.

    그 밖의 호출은 call 을 그대로 반환한다.
    """
    args = call.args
    if (
        call.name != "get_sleep_records"
        or not isinstance(args, Mapping)
        or args.get("analysis") not in _ANALYSES
        or args.get("period") != _RECENT_7_DAYS
        or any(has_period_word(text) for text in texts)
    ):
        return call
    return ToolCall(call.id, call.name, {key: value for key, value in args.items() if key != "period"})
