"""코칭 답변이 화면과 정한 답변 형식을 지켰는지 센다.

약속은 docs/agent-interface.md 5장 "답변 형식"에 있다. 답변은 문단·목록·표·### 소제목을 쓰고 굵게·큰 제목(#, ##)·링크·
코드 블록·HTML 은 쓰지 않는다. 소제목은 첫 문단 뒤에 둔다. 표는 앞뒤에 빈 줄을 두고, 머리 행 다음 줄에 머리 행과 칸 수가
같은 구분선을 둔다.
골든셋 보고서의 형식 집계에만 쓰고 통과·실패 판정에는 쓰지 않는다. 표를 찾는 기준은 채점과 같다 (answer_markdown).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from eval.answer_markdown import code_lines, tables

# 형식 이름과 보고서 칸 이름. 약속한 형식, 약속 밖 형식, 표 모양을 어긴 것 순서다
AGREED = {"table": "표", "list": "목록", "subheading": "소제목"}
# 굵게는 쓰지 않는다. 화면이 첫 문단 첫 줄을 큰 요약 글씨로 그려 강조는 그쪽에 맡긴다. 큰 제목은 #, ##, 밑줄형이다
OUTSIDE = {"bold": "굵게", "heading": "큰 제목", "link": "링크", "code_block": "코드 블록", "html": "HTML"}
TABLE_SHAPE = {
    "no_blank_before": "표 앞 빈 줄 없음",
    "no_blank_after": "표 뒤 빈 줄 없음",
    "no_delimiter": "구분선 없음",
}
# 첫 덩어리가 소제목이면 화면이 첫 문단 첫 줄의 요약 글씨를 그리지 못한다
ORDER = {"subheading_first": "소제목으로 시작"}
LABELS = AGREED | OUTSIDE | TABLE_SHAPE | ORDER

_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\S")
_BOLD = re.compile(r"\*\*(?=\S).+?(?<=\S)\*\*|__(?=\S).+?(?<=\S)__")
# ### 이하는 화면이 소제목으로 그리고, # 와 ## 는 약속 밖의 큰 제목이다
_SUBHEADING = re.compile(r"^ {0,3}#{3,6}(?:[ \t]|$)")
_HEADING = re.compile(r"^ {0,3}#{1,2}(?:[ \t]|$)")
# 문장 바로 아래 줄의 === 나 --- 는 그 문장을 제목으로 만든다
_UNDERLINE = re.compile(r"^ {0,3}(?:={3,}|-{3,})[ \t]*$")
_LINK = re.compile(r"!?\[[^\]\n]*\]\([^)\n]*\)|<https?://[^>\s]+>|https?://\S|www\.\S")
# 태그 이름 바로 뒤가 띄어쓰기, / , > 일 때만 태그다. 사고 채널 표시(<|channel>, <channel|>)는 태그가 아니다
_HTML = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>|<!--")


def answer_forms(answer: str) -> list[str]:
    """답변에 쓴 형식과 어긴 표 모양. LABELS 의 이름을 LABELS 순서로 반환한다."""
    lines = answer.split("\n")
    code = code_lines(lines)
    found: set[str] = set()
    in_table = [False] * len(lines)
    for start, end in tables(lines):
        found.add("table")
        in_table[start:end] = [True] * (end - start)
        if start > 0 and lines[start - 1].strip():
            found.add("no_blank_before")
        if end < len(lines) and lines[end].strip():
            found.add("no_blank_after")
    first_seen = False
    for i, line in enumerate(lines):
        if not first_seen and not code[i] and line.strip():
            first_seen = True
            if _SUBHEADING.match(line):
                found.add("subheading_first")
        if code[i]:
            found.add("code_block")
            continue
        if not in_table[i] and line.strip().startswith("|"):
            found.add("no_delimiter")
        if _LIST_ITEM.match(line) and not in_table[i]:
            found.add("list")
        if _SUBHEADING.match(line) and not in_table[i]:
            found.add("subheading")
        if (_HEADING.match(line) and not in_table[i]) or (
            _UNDERLINE.match(line) and _paragraph_line(lines, i - 1, code, in_table)
        ):
            found.add("heading")
        for name, pattern in (("bold", _BOLD), ("link", _LINK), ("html", _HTML)):
            if pattern.search(line):
                found.add(name)
    return [name for name in LABELS if name in found]


def _paragraph_line(lines: list[str], i: int, code: list[bool], in_table: list[bool]) -> bool:
    """i 번째 줄이 문단 글인지. 빈 줄, 코드, 표, 목록 줄은 아니다."""
    if i < 0 or code[i] or in_table[i] or not lines[i].strip():
        return False
    return not _LIST_ITEM.match(lines[i]) and not _UNDERLINE.match(lines[i])


def day_by_day_result(tool_calls: Sequence[dict[str, Any]] | None) -> bool:
    """실행 기록의 도구 호출 중 이틀 이상의 날짜별 값을 받은 결과가 있는지. 모양이 다르면 False 다."""
    for call in tool_calls or []:
        if not isinstance(call, dict) or call.get("reply") != "ok":
            continue
        body = call.get("result")
        inner = body.get("result") if isinstance(body, dict) else None
        if not isinstance(inner, dict):
            continue
        if call.get("name") == "get_daily_trend":
            points = inner.get("points")
            if isinstance(points, list) and sum(isinstance(p, dict) and bool(p.get("display")) for p in points) >= 2:
                return True
        elif call.get("name") == "get_sleep_records":
            nights = inner.get("nights")
            if isinstance(nights, list) and len(nights) >= 2:
                return True
    return False
