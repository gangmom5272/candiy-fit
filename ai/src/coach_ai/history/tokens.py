"""LLM 입력 토큰 수 추정.

사내 LLM 토크나이저를 쓸 수 없어 글자로 추정한다. ASCII 숫자는 한 글자를 1토큰으로 세고(숫자를 한 자리씩 나누는
토크나이저로 가정한다), 한글 음절·ASCII 영문과 문장 부호·공백·가운뎃점은 글자당 REST_RATIO 토큰으로 센다.
그 밖의 글자(이모지, 기호, 한글 자모, 한자, ASCII 밖 숫자 등)는 UTF-8 바이트 수만큼 센다. 어휘에 없는 글자를
바이트마다 한 토큰으로 나누는 경우의 최댓값이다.
요청 하나는 메시지마다 채팅 템플릿 표시(MESSAGE_TOKENS)를 더하고, 도구 정의는 count_tool_definition_tokens 로 센다.
계수를 정한 측정은 docs/benchmarks/2026-10-28-token-estimate.md 에 있다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

# 토큰 계수 방식(메시지·도구 정의 고정분 포함)과 요약 대상 선정 기준(2,400·1,500·2,400·800·9,800)을 함께 나타낸다.
# 둘 중 하나라도 바뀌면 값을 바꾼다. 기록한 토큰 수와 요약 대상이 어느 기준으로 정해졌는지 구분한다
TOKEN_POLICY_VERSION = "estimate-v6"
# 날짜 지정 질문 측정(docs/evals/2026-10-26-repeated-question-before*.md)의 실제 입력 토큰으로 맞춘
# 네 쌍(0.539~0.556)의 중앙값. 맞추는 방법은 bench.trend_tokens.calibrate 에 있다
REST_RATIO = 0.544
# 채팅 템플릿이 메시지마다 붙이는 표시(역할, 시작·끝 표시)의 토큰 수. 같은 질문에 이전 대화 메시지 4개·6개를 더한
# 실제 증가분에서 count_tokens 로 센 글의 몫을 빼면 메시지당 약 8토큰이다
MESSAGE_TOKENS = 8
# 도구 정의에서 설명 문장을 비운 JSON 구조(이름·키·타입·선택지)에 곱하는 비율. 구조는 같은 글자 수의 문장보다
# 토큰이 적다. 설명 문장은 count_tokens 로 그대로 센다
TOOL_STRUCTURE_RATIO = 0.5


# REST_RATIO 로 세지 않는 글자: ASCII, 한글 음절, 공백, 가운뎃점 밖의 글자. 가운뎃점은 시스템 프롬프트와 도구 설명에
# 많이 들어 있어 REST_RATIO 를 맞춘 측정에 포함된 글자다
_OTHER = re.compile(r"[^\x00-\x7f가-힣\s·]")


def estimate_tokens(text: str, *, rest_ratio: float) -> int:
    digits = sum(map(text.count, "0123456789"))
    others = _OTHER.findall(text)
    rest = len(text) - digits - len(others)
    # 형식이 잘못된 글자는 그대로 UTF-8 로 바꾸면 오류가 나서, 한 글자를 3바이트로 센다
    return digits + sum(len(ch.encode("utf-8", "surrogatepass")) for ch in others) + round(rest_ratio * rest)


def count_tokens(text: str) -> int:
    """TOKEN_POLICY_VERSION 방식으로 센 토큰 수."""
    return estimate_tokens(text, rest_ratio=REST_RATIO)


def count_tool_definition_tokens(specs: Sequence[Mapping[str, Any]]) -> int:
    """LLM 요청에 묶는 도구 정의(OpenAI 형식 목록)의 토큰 수.

    description 문자열은 count_tokens 로 세고, description 을 빈 문자열로 바꾼 JSON 구조는 count_tokens 에
    TOOL_STRUCTURE_RATIO 를 곱해 센다.
    """
    descriptions: list[str] = []
    structure = json.dumps(_blank_descriptions(specs, descriptions), ensure_ascii=False, separators=(",", ":"))
    return sum(map(count_tokens, descriptions)) + round(TOOL_STRUCTURE_RATIO * count_tokens(structure))


def _blank_descriptions(value: Any, descriptions: list[str]) -> Any:
    """description 문자열을 빈 문자열로 바꾼 사본을 반환하고, 바꾼 문자열은 descriptions 에 추가한다."""
    if isinstance(value, Mapping):
        copied = {}
        for key, item in value.items():
            if key == "description" and isinstance(item, str):
                descriptions.append(item)
                copied[key] = ""
            else:
                copied[key] = _blank_descriptions(item, descriptions)
        return copied
    if isinstance(value, list | tuple):
        return [_blank_descriptions(item, descriptions) for item in value]
    return value
