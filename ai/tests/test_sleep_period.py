"""기간을 말하지 않은 수면 분석 질문의 조회 기간(coach_ai.agent.rules.sleep_period) 테스트.

sleep1-01·03·05·11 은 수면 골든셋 개선용 질문 그대로이고, sleep1-02·12 는 기간 말이 든 자리가 같은 다른 글이다.
"""

import uuid

import pytest

from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall
from coach_ai.agent.rules.guard import medication_answer
from coach_ai.agent.rules.sleep_period import has_period_word, period_texts, with_default_period
from coach_ai.history.types import HistoryTurn, StoredSummary

WEEK = "recent_days:7"
# sleep1-12 형식의 앞 대화. 약 질문을 차단한 고정 답의 제안 문장에만 "최근"이 있다
DRUG_QUESTION = "잠 오는 약을 먹어 봐도 될까?"
DRUG_ANSWER = medication_answer(sleep=True)


def turn(seq: int, question: str, answer: str) -> HistoryTurn:
    def uid(kind: str, n: int) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic-{kind}-{n}"))

    return HistoryTurn(
        request_id=uid("request", seq),
        question_message_id=uid("message", seq),
        answer_message_id=uid("message", seq + 1),
        question_seq=seq,
        answer_seq=seq + 1,
        question=question,
        answer=answer,
    )


def context(*turns: HistoryTurn, summary: StoredSummary | None = None) -> CoachContext:
    return CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1", summary=summary, turns=turns)


def sleep_call(**args) -> ToolCall:
    return ToolCall("call_1", "get_sleep_records", args)


def ran(call: ToolCall, question: str, ctx: CoachContext | None = None) -> ToolCall:
    return with_default_period(call, period_texts(question, ctx or context()))


@pytest.mark.parametrize(
    ("question", "analysis"),
    [
        ("잘 자고 싶은데 어떻게 해야 해?", "pattern"),
        ("내 산소포화도는 왜 이렇게 들쭉날쭉해?", "signals"),
        ("내 산소포화도는 왜 이렇게 들쭉날쭉해?", "factors"),
    ],
)
def test_sleep_analysis_for_7_days_drops_period_when_question_has_no_period_words(question, analysis):
    done = ran(sleep_call(analysis=analysis, period=WEEK), question)
    assert done == ToolCall("call_1", "get_sleep_records", {"analysis": analysis})


def test_dropping_period_keeps_other_args():
    done = ran(sleep_call(analysis="pattern", period=WEEK, include_non_main=True), "잘 자고 싶은데 어떻게 해야 해?")
    assert done.args == {"analysis": "pattern", "include_non_main": True}


@pytest.mark.parametrize(
    "question",
    [
        "나 요즘 왜 이렇게 못 자지?",
        "최근 수면 습관 알려 줘",
        "지난주보다도 오래 잤는데 아직 졸려",
        "그럼 오늘부터 뭘 하면 돼?",
        "요새 잠이 안 와",
        "근래 들어 자주 깨",
        "일주일 내내 피곤해",
        "보름째 늦게 자",
        "며칠째 잠을 설쳐",
        "한동안 잘 잤는데 왜 이래?",
        "이번 달 수면 어때?",
        "요 며칠 잠이 얕아",
    ],
)
def test_sleep_analysis_is_kept_when_question_has_period_words(question):
    call = sleep_call(analysis="pattern", period=WEEK)
    assert ran(call, question) is call


def test_follow_up_is_kept_when_only_previous_answer_says_recent():
    call = sleep_call(analysis="pattern", period=WEEK)
    assert ran(call, "응, 알려 줘", context(turn(1, DRUG_QUESTION, DRUG_ANSWER))) is call
    # 앞 답에서 "최근"을 빼면 세 글 어디에도 기간 말이 없어 기간을 뺀다
    plain = context(turn(1, DRUG_QUESTION, DRUG_ANSWER.replace("최근 ", "")))
    assert ran(call, "응, 알려 줘", plain).args == {"analysis": "pattern"}


def test_follow_up_is_kept_when_previous_question_has_period_words():
    call = sleep_call(analysis="pattern", period=WEEK)
    ctx = context(turn(1, "지난주 수면 어땠어?", "평균 6시간 10분 주무셨어요."))
    assert ran(call, "그 이유가 궁금해", ctx) is call


def test_only_the_latest_previous_pair_is_checked():
    call = sleep_call(analysis="pattern", period=WEEK)
    ctx = context(
        turn(1, "지난주 수면 어땠어?", "평균 6시간 10분 주무셨어요."),
        turn(3, "잠이 잘 안 와", "잠들기 전 습관을 같이 볼까요?"),
    )
    assert ran(call, "응, 알려 줘", ctx).args == {"analysis": "pattern"}


def test_summary_is_checked_when_previous_pair_is_only_in_summary():
    call = sleep_call(analysis="pattern", period=WEEK)
    with_period = context(summary=StoredSummary(text="최근 수면 기록을 함께 확인했다.", until_seq=4))
    assert ran(call, "응, 알려 줘", with_period) is call
    without_period = context(summary=StoredSummary(text="잠드는 습관을 묻는다.", until_seq=4))
    assert ran(call, "응, 알려 줘", without_period).args == {"analysis": "pattern"}


@pytest.mark.parametrize(
    "call",
    [
        ToolCall("call_1", "get_sleep_records", {"period": WEEK}),
        ToolCall("call_1", "get_sleep_records", {"analysis": "pattern", "period": "recent_days:14"}),
        ToolCall("call_1", "get_sleep_records", {"analysis": "pattern", "period": "last_7_days"}),
        ToolCall("call_1", "get_sleep_records", {"analysis": "pattern"}),
        ToolCall("call_1", "get_sleep_records", {"analysis": "trend", "period": WEEK}),
        ToolCall("call_1", "get_metric_summary", {"metric": "sleep", "period": WEEK}),
        ToolCall("invalid", "get_sleep_records", None),
    ],
)
def test_calls_other_than_sleep_analysis_for_7_days_are_kept(call):
    assert ran(call, "잘 자고 싶은데 어떻게 해야 해?") is call


@pytest.mark.parametrize(
    "text",
    [
        "7일",
        "2주 동안",
        "3달",
        "6개월",
        "1년",
        "5밤",
        "10월 21일",
        "10/21",
        "10.21 수면",
        "2026-10-21",
        "하루",
        "이틀",
        "일주일",
        "보름",
        "며칠",
        "한 달",
        "한두 달",
        "몇 주",
        "오늘",
        "어제",
        "그제",
        "그저께",
        "어젯밤",
        "그날",
        "이번 주",
        "이번주",
        "지난달",
        "저번 달",
        "올해",
        "작년",
        "요즘",
        "요새",
        "최근",
        "지금",
        "주말",
        "평일",
        "월요일",
    ],
)
def test_period_words_are_found(text):
    assert has_period_word(text)


# 더한 기간 말. 애매한 말도 기간 말로 센다. 종류마다 | 로 나눈다
ADDED_PERIOD_WORDS = {
    "요즘·최근": (
        "요사이|이즈음|그즈음|이맘때|요맘때|그 무렵|한참|오랫동안|오래전|오래 전|오랜만|그새|"
        "여지껏|이때까지|이제|이제까지|일전에|접때|아까|방금|조금 전|좀 전"
    ),
    "날 수": (
        "수일|수주|수개월|수년|이 주 동안|이주일|일 년|일년|삼 개월|삼일째|사흘째|두 밤|몇 밤|"
        "나날이|온종일|밤새|1박|2학기|1분기"
    ),
    "날": "어저께|엊저녁|이날|다음 날|첫날|첫 주|마지막 주|그 후|그 뒤로|그 전에",
    "주·달·해": (
        "금주|금월|전주|전월|차주|익월|요번 주|이번에|주초|월초|월말|연초|연말|초순|중순|하순|상반기|하반기|"
        "시월|유월|새해|올 들어|주간|월간|연간"
    ),
    "지난 때와 견주는 말": (
        "평소|처음에는|이전|이후로|전에는|전에|전보다|과거|학창 시절|어릴 때|어렸을 때|언제|기간|시기"
    ),
    "이름으로 부르는 기간": "월욜|불금|휴일|쉬는 날|명절|추석|설날|여름|가을|겨울|봄에",
}


@pytest.mark.parametrize("text", [word for words in ADDED_PERIOD_WORDS.values() for word in words.split("|")])
def test_added_period_words_are_found(text):
    assert has_period_word(text)


@pytest.mark.parametrize(
    "text",
    [
        "잘 자고 싶은데 어떻게 해야 해?",
        "내 산소포화도는 왜 이렇게 들쭉날쭉해?",
        "수면 점수는 어떻게 계산돼?",
        "7.5시간 자는데도 피곤해",
        "1-2시간마다 깨",
        "산소포화도가 90-95%야",
        "이런 문제에 대한 해결책이 있어?",
        "수면제랑 수면 유도제는 뭐가 달라?",
        # 하루 안의 때, 버릇, "~기 전", 기간 말과 모양만 비슷한 말은 기간을 말하지 않는다
        "자기 전에 뭘 하면 좋아?",
        "잠들기 전에는 휴대폰을 봐",
        "새벽에 자꾸 깨",
        "한밤중에 자주 깨",
        "아침에 일어나기 힘들어",
        "매일 늦게 자는데 괜찮아?",
        "밤에 계속 깨",
        "수면 습관 중에 뭐부터 고쳐야 해?",
        "잠을 자도 왜 이래?",
    ],
)
def test_text_without_period_words_is_not_found(text):
    assert not has_period_word(text)
