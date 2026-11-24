"""도구 없이 끝난 답의 건강 수치를 찾는다.

reused_values 는 앞 답에 있던 수치를 다시 쓴 값이고, unsupported_values 는 앞 답에도 질문에도 없는 값이다. 둘 다
시스템 프롬프트 수치 절의 규칙을 어긴 수치다. 한 값은 두 함수 가운데 한쪽에만 들어간다.

건강 수치는 기록 단위가 붙은 수다: 시간 길이(N시간 M분, N분), 비율(N%), 걸음, 심박(bpm), 열량(kcal), 거리(km),
밤·번·회와 개수로 쓴 일, 점수, 시각(HH:MM, N시 M분). 같은 종류는 단위를 맞춰 견준다 (5시간 8분과 308분, 7밤·7번·
"7일 중"의 7일은 같은 값).
일반 지식 수치는 찾지 않는다. 범위(7~9시간), 기준·어림(90% 아래, 30분 정도, 7시간 이상), 권장·일반 기준을 말한
문장의 수가 그렇다. 단위나 지표의 뜻을 설명하는 문장에서 한 단위 기간으로 쓴 수(1분 동안, 1분당, 1시간 동안)도
일반 지식 수치로 본다. 수면 결과 글이 날 수를 "기록한 14일 중 7일"처럼 쓰므로 개수로 쓴 N일은 밤 수와 같은 개수로
본다. 날짜(10월 21일, 21일 수면, 9일-22일)와 기간 길이(최근 7일, 7일 동안, N주)는 수치로 보지 않는다.
사용자가 질문에서 말한 값은 앞 답에 있어도 뺀다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

ValueKind = Literal["minutes", "percent", "steps", "bpm", "kcal", "km", "count", "score", "clock"]
HealthValue = tuple[ValueKind, float]

_NUMBER = r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?"
# 숫자 중간(7.5 의 5, 1,234 의 234)에서 시작하는 수는 찾지 않는다
_VALUE = re.compile(
    r"(?<![\d.,])(?:"
    r"(?P<clock>\d{1,2}):(?P<clock_m>\d{2})"
    r"|(?P<at>\d{1,2})\s?시\s?(?P<at_m>\d{1,2})\s?분"
    r"|(?P<hours>\d+(?:\.\d+)?)\s?시간(?:\s?(?P<hours_m>\d+)\s?분)?"
    r"|(?P<minutes>\d+)\s?분(?!의)"
    rf"|(?P<percent>{_NUMBER})\s?%"
    rf"|(?P<steps>{_NUMBER})\s?걸음"
    r"|(?P<bpm>\d+)\s?(?:bpm|회/분)"
    rf"|(?P<kcal>{_NUMBER})\s?(?:kcal|킬로칼로리)"
    rf"|(?P<km>{_NUMBER})\s?(?:km|킬로미터)"
    r"|(?P<count>\d+)\s?(?:밤|번(?!째)|회(?!차|복|의))"
    r"|(?P<score>\d+)\s?점"
    r"|(?P<days>\d+)\s?일(?!요일|간격)"
    r")",
    re.IGNORECASE,
)
_KINDS: tuple[ValueKind, ...] = ("percent", "steps", "bpm", "kcal", "km", "count", "score")
# 범위의 한쪽 끝
_RANGE_BEFORE = re.compile(r"[~\-–]\s*$")
_RANGE_AFTER = re.compile(r"^\s*[~\-–]\s*\d")
# 기준·어림으로 쓴 수
_QUALIFIER = re.compile(
    r"^\s?(?:이상|이하|미만|초과|아래|밑|넘게|넘는|넘으면|넘지|정도|쯤|가량|안팎|내외|씩|마다|전후|남짓|이내)"
)
# 권장·일반 기준을 말한 문장
_GENERAL = re.compile(r"권장|권고|일반적|성인|전문가|연구")
# 단위나 지표의 뜻을 설명하는 문장과, 그 문장에서 한 단위 기간 뒤에 오는 말
_DEFINITION = re.compile(r"뜻|의미|말(?:해|합|하)|나타내|가리키|단위|지표|횟수")
_PER_UNIT = re.compile(r"^\s?(?:동안|당|에)")
# 개수로 쓴 N일이 아닌 것. 앞이 날짜의 월·날짜 범위·기간 말이거나, 뒤가 기간·날짜로 이어지는 말이다
_DAY_DATE_BEFORE = re.compile(r"(?:\d\s?월|/|\d\s?일\s?[-~–])\s?$")
_DAY_PERIOD_BEFORE = re.compile(r"(?:최근|지난|과거|이번|이전|앞선|그\s?전|첫|마지막)\s?$")
_DAY_NOT_COUNT_AFTER = re.compile(
    r"^\s?(?:[-~–]\s?\d|동안|간|째|부터|까지|전|뒤|후|만에|평균|\(|수면|밤|아침|낮|새벽|오전|오후|저녁|에 깬|에 잠)"
)
# 문장. 소수점(7.5)에서는 나누지 않는다
_SENTENCE = re.compile(r"(?:[^.?!\n]|\.(?=\d))+")


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _value(m: re.Match[str]) -> HealthValue:
    g = m.groupdict()
    if g["clock"] is not None:
        return "clock", int(g["clock"]) % 12 * 60 + int(g["clock_m"])
    if g["at"] is not None:
        return "clock", int(g["at"]) % 12 * 60 + int(g["at_m"])
    if g["hours"] is not None:
        return "minutes", round(float(g["hours"]) * 60) + int(g["hours_m"] or 0)
    if g["minutes"] is not None:
        return "minutes", int(g["minutes"])
    if g["days"] is not None:
        return "count", int(g["days"])
    for kind in _KINDS:
        if g[kind] is not None:
            return kind, _number(g[kind])
    raise AssertionError(m.group())


def _one_unit(m: re.Match[str]) -> bool:
    g = m.groupdict()
    if g["minutes"] is not None:
        return int(g["minutes"]) == 1
    return g["hours"] is not None and float(g["hours"]) == 1 and g["hours_m"] is None


def _day_count(sentence: str, m: re.Match[str]) -> bool:
    """N일이 개수인지. 날짜("10월 21일", "21일 수면")와 기간 길이("최근 14일 중"의 14, "7일 동안")는 개수가 아니다."""
    before, after = sentence[: m.start()], sentence[m.end() :]
    return not (
        _DAY_DATE_BEFORE.search(before) or _DAY_PERIOD_BEFORE.search(before) or _DAY_NOT_COUNT_AFTER.match(after)
    )


def health_values(text: str, *, general: bool = True) -> set[HealthValue]:
    """글에 있는 건강 수치. general 이 참이면 일반 지식 수치(범위·기준·어림, 권장 기준 문장, 정의의 한 단위)를 뺀다."""
    found: set[HealthValue] = set()
    for sentence in _SENTENCE.findall(text):
        if general and _GENERAL.search(sentence):
            continue
        definition = general and _DEFINITION.search(sentence) is not None
        for m in _VALUE.finditer(sentence):
            if m.group("days") is not None and not _day_count(sentence, m):
                continue
            if general and (
                _RANGE_BEFORE.search(sentence[: m.start()])
                or _RANGE_AFTER.match(sentence[m.end() :])
                or _QUALIFIER.match(sentence[m.end() :])
                or (definition and _one_unit(m) and _PER_UNIT.match(sentence[m.end() :]))
            ):
                continue
            found.add(_value(m))
    return found


def _earlier(previous_answers: Iterable[str]) -> set[HealthValue]:
    earlier: set[HealthValue] = set()
    for text in previous_answers:
        earlier |= health_values(text)
    return earlier


def _asked(questions: Iterable[str]) -> set[HealthValue]:
    # 질문의 수는 기준·어림 표현이어도 사용자가 말한 값으로 센다
    asked: set[HealthValue] = set()
    for text in questions:
        asked |= health_values(text, general=False)
    return asked


def reused_values(answer: str, previous_answers: Iterable[str], questions: Iterable[str]) -> list[HealthValue]:
    """answer 의 건강 수치 가운데 앞 답(previous_answers)에도 있는 값. 질문(questions)에 나온 값은 뺀다."""
    return sorted(health_values(answer) & (_earlier(previous_answers) - _asked(questions)))


def unsupported_values(answer: str, previous_answers: Iterable[str], questions: Iterable[str]) -> list[HealthValue]:
    """answer 의 건강 수치 가운데 앞 답(previous_answers)에도 질문(questions)에도 없는 값."""
    return sorted(health_values(answer) - _earlier(previous_answers) - _asked(questions))
