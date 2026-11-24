"""도구 없이 끝난 답의 건강 수치 찾기 (agent/rules/reused_numbers.py)."""

import pytest

from coach_ai.agent.rules.reused_numbers import health_values, reused_values, unsupported_values


def test_reused_value_in_other_unit_is_found():
    previous = ["기록으로는 그날 6시간 10분 주무셨어요."]
    assert reused_values("370분밖에 못 주무셨으니 오늘은 일찍 누워 보세요.", previous, []) == [("minutes", 370)]


def test_nights_and_times_are_the_same_count():
    previous = ["2주 동안 4밤은 자정이 넘어 잠들었어요."]
    assert reused_values("늦게 잠든 날이 4번이라 잠드는 시각부터 당겨 보세요.", previous, []) == [("count", 4)]


def test_clock_in_two_forms_is_the_same_value():
    previous = ["평균 23:40 에 잠드셨어요."]
    assert reused_values("밤 11시 40분보다 조금 일찍 누워 보세요.", previous, []) == [("clock", 700)]


@pytest.mark.parametrize(
    ("before", "after", "value"),
    [
        ("어제는 8,000걸음을 걸었어요.", "어제 8000걸음을 걸으셨으니", ("steps", 8000.0)),
        ("자는 동안 평균 94% 였어요.", "평균 94%라 괜찮은 편이에요.", ("percent", 94.0)),
        ("자는 동안 심박은 58bpm 이었어요.", "58bpm 이라 낮은 편이에요.", ("bpm", 58.0)),
        ("어제 운동으로 320kcal 를 썼어요.", "320kcal 를 쓰셨으니", ("kcal", 320.0)),
        ("어제 4.2km 를 걸었어요.", "4.2km 를 걸으셨으니", ("km", 4.2)),
        ("오늘 점수는 72점이에요.", "72점이라 보통이에요.", ("score", 72.0)),
        ("평균 7.5시간 주무셨어요.", "7시간 30분 주무셨으니", ("minutes", 450)),
    ],
)
def test_each_kind_is_matched(before, after, value):
    assert reused_values(after, [before], []) == [value]


def test_value_not_in_previous_answer_is_not_reused():
    previous = ["어제는 8,000걸음을 걸었어요."]
    assert reused_values("오늘은 저녁에 20분 걸어 보세요.", previous, []) == []
    # 7시간 30분(450분)과 30분은 다른 값이다
    assert reused_values("30분 일찍 누워 보세요.", ["7시간 30분 주무셨어요."], []) == []


@pytest.mark.parametrize(
    "text",
    [
        "하루 7~9시간 자는 게 좋아요.",
        "7시간 이상 주무셔 보세요.",
        "30분 정도 걸어 보세요.",
        "90% 아래로 내려간 밤이 있었어요.",
        "하루 20분씩 걸어 보세요.",
        "성인은 하루 8시간 수면이 권장돼요.",
        "일반적으로 하루 8,000걸음을 목표로 해요.",
    ],
)
def test_general_knowledge_numbers_are_not_targets(text):
    assert health_values(text) == set()
    assert reused_values(text, [text], []) == []


def test_dates_and_periods_are_not_values_but_day_counts_are():
    # 날짜와 기간 길이는 수치가 아니고, 기록한 날 수처럼 개수로 쓴 N일은 밤 수와 같은 개수다
    text = "10월 21일까지 최근 14일 중 9일 기록이 있고, 2주 동안 3개월 전보다 줄었어요."
    assert health_values(text) == {("count", 9)}


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("산소포화도를 기록한 14일 중 90% 미만으로 기록된 날은 7일이에요.", {("count", 14), ("count", 7)}),
        ("14밤 중 7밤은 90% 아래였어요.", {("count", 14), ("count", 7)}),
        ("5시간 미만으로 잔 날이 3일 연달아 이어졌어요.", {("count", 3)}),
        ("10월 21일 수면이 가장 낮았어요.", set()),
        ("21일 수면이 가장 낮았어요.", set()),
        ("10월 9일-22일 수면을 봤어요.", set()),
        ("최근 7일 동안 기록을 봤어요.", set()),
        ("7일 동안 기록을 봤어요.", set()),
        ("최근 14일(10월 9일-22일)을 봤어요.", set()),
        ("90% 미만이 3일 이상 되풀이됐어요.", set()),
    ],
)
def test_day_counts_are_counts_but_dates_and_period_lengths_are_not(text, values):
    assert health_values(text) == values


def test_day_count_matches_night_count_of_previous_answer():
    # 앞 답이 결과 글을 따라 "7일"로 쓰고 이번 답이 "7밤"으로 써도 같은 값이라 결과에 없는 수치가 아니다
    previous = ["산소포화도를 기록한 14일 중 90% 미만으로 기록된 날은 7일이에요."]
    assert unsupported_values("14밤 중 7밤이 90% 아래였어요.", previous, []) == []
    assert reused_values("14밤 중 7밤이 90% 아래였어요.", previous, []) == [("count", 7), ("count", 14)]
    assert unsupported_values("8일은 90% 아래였어요.", previous, []) == [("count", 8)]


def test_values_the_user_said_are_not_reused():
    previous = ["말씀하신 8시간과 달리 기록은 6시간 10분이에요."]
    questions = ["나 8시간은 잤는데 왜 졸리지?", "그럼 오늘은?"]
    assert reused_values("8시간 주무셨다고 하셨는데 오늘은 일찍 누워 보세요.", previous, questions) == []
    assert reused_values("기록은 6시간 10분이라 짧은 편이에요.", previous, questions) == [("minutes", 370)]


def test_numbers_are_not_split_inside():
    assert health_values("평균 7.5시간 주무셨어요.") == {("minutes", 450)}
    assert health_values("어제 1,234걸음이에요.") == {("steps", 1234.0)}


@pytest.mark.parametrize(
    "text", ["3번째 밤에 깼어요.", "2회차 기록이에요.", "3분의 1 정도예요.", "몸이 회복되는 중이에요."]
)
def test_ordinals_and_fractions_are_not_values(text):
    assert health_values(text) == set()


def test_summary_text_counts_as_previous_answer():
    summary = "앞 대화에서 어젯밤 5시간 50분 잤다고 기록을 알려 줬다."
    assert reused_values("어젯밤 5시간 50분이라 피곤하실 수 있어요.", [summary], []) == [("minutes", 350)]


# 앞 답에도 질문에도 없는 건강 수치
def test_value_without_previous_answer_is_unsupported():
    answer = "최근 7일 하루 평균 5,230걸음이라 조금 더 걸어 보세요."
    assert unsupported_values(answer, [], ["요즘 잠이 안 와"]) == [("steps", 5230.0)]


def test_each_value_is_reused_or_unsupported_but_not_both():
    previous = ["어젯밤 6시간 10분 주무셨어요."]
    answer = "어젯밤 6시간 10분 주무셨고 수면 중 평균 93.1% 였어요."
    assert reused_values(answer, previous, []) == [("minutes", 370)]
    assert unsupported_values(answer, previous, []) == [("percent", 93.1)]


def test_value_in_other_unit_of_previous_answer_is_not_unsupported():
    assert unsupported_values("370분밖에 못 주무셨어요.", ["그날 6시간 10분 주무셨어요."], []) == []


@pytest.mark.parametrize(
    "text",
    [
        "하루 7~9시간 자는 게 좋아요.",
        "30분 정도 걸어 보세요.",
        "성인은 하루 8시간 수면이 권장돼요.",
        "10월 21일까지 최근 14일 동안 기록이 있어요.",
        "몸이 회복되는 중이에요.",
    ],
)
def test_general_numbers_dates_and_no_numbers_are_not_unsupported(text):
    assert unsupported_values(text, [], []) == []


def test_values_the_user_said_are_not_unsupported():
    # 질문의 수는 기준·어림 표현이어도 사용자가 말한 값이다
    questions = ["나 어제 9,000걸음 넘게 걸었는데 괜찮아?", "그럼 오늘은?"]
    assert unsupported_values("9,000걸음이면 많이 걸으셨어요.", [], questions) == []
    assert unsupported_values("어제 9,000걸음이고 오늘은 3,100걸음이에요.", [], questions) == [("steps", 3100.0)]


# 단위나 지표의 뜻을 설명하는 문장에서 한 단위 기간으로 쓴 수
@pytest.mark.parametrize(
    "text",
    [
        "맥박은 1분 동안 혈관이 뛴 수를 뜻해요.",
        "bpm 은 1분당 박동 수를 나타내는 단위예요.",
        "활동 시간 지표는 1시간 동안 움직인 양을 가리켜요.",
        "칼로리 소모량은 1시간에 쓴 열량을 말합니다.",
    ],
)
def test_one_unit_in_definition_sentence_is_not_a_value(text):
    assert health_values(text) == set()
    assert unsupported_values(text, [], []) == []


def test_one_unit_outside_definition_sentence_is_a_value():
    # 뜻을 설명하는 말이 없으면 기록 수치로 본다
    assert unsupported_values("새벽에 1시간 동안 깨어 계셨어요.", [], []) == [("minutes", 60)]
    assert unsupported_values("1분 동안 숨이 찼다고 기록돼 있어요.", [], []) == [("minutes", 1)]


def test_other_values_in_definition_sentence_are_still_values():
    # 정의에 쓰인 한 단위만 빼고 같은 문장의 다른 수치는 찾는다
    text = "심박은 1분 동안 뛴 횟수를 말하고, 어젯밤 평균은 61bpm 이에요."
    assert unsupported_values(text, [], []) == [("bpm", 61.0)]
    assert unsupported_values("심박은 2분 동안 뛴 횟수를 말해요.", [], []) == [("minutes", 2)]
