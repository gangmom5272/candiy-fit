"""코칭 답변의 마크다운 읽기 (S15P31A403-239).

화면은 답변을 마크다운(문단·목록·굵게·표)으로 그린다 (frontend/web/src/lib/features/coaching/markdown.ts, marked
GFM). 채점기는 plain_answer 로 기호를 지운 글을 읽는다.

- 굵게 기호(**, __)를 지운다.
- 줄 앞의 목록 기호(-, *, +, 1., 1))를 지운다. 기호 뒤에 띄어쓰기가 있어야 목록이다 (-15분은 그대로 둔다).
- 표는 머리 행 다음 줄이 머리 행과 칸 수가 같은 구분선일 때만 표로 읽는다 (화면과 같다). 구분선을 빼고 행마다 칸을
  CELL_SEPARATOR 로 이어 한 줄로 만든다. 행은 | 가 있는 줄이 이어지는 데까지다.
- 코드 블록 안은 그대로 둔다.

마크다운 기호가 없는 답변은 글자 하나 바꾸지 않고 반환한다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

Span = tuple[int, int]

# 표 칸을 잇는 글자. 수치·범위·부호 판정에 쓰는 글자(~, -, +, 쉼표, 마침표)와 띄어쓰기만으로 이으면 앞뒤 칸의
# 수치가 범위나 부호로 읽힐 수 있어 가운뎃점을 쓴다
CELL_SEPARATOR = " · "
_BOLD = re.compile(r"\*\*|__")
_LIST_MARKER = re.compile(r"^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+")
_FENCE = re.compile(r"^ {0,3}(?:`{3,}|~{3,})")
_PIPE = re.compile(r"(?<!\\)\|")
_DELIMITER_CELL = re.compile(r"^:?-+:?$")


@dataclass(frozen=True, slots=True)
class PlainAnswer:
    """기호를 지운 답변.

    rows 는 표 행(머리 행 포함)의 구간이다. 한 행을 한 문장으로 보고 행 안에서는 나누지 않는다.
    values 는 수치로 읽지 않는 구간(표 머리 행과 첫 열)을 같은 길이의 공백으로 바꾼 글이라 위치가 text 와 같다.
    """

    text: str
    values: str
    rows: tuple[Span, ...] = ()


def cells(line: str) -> list[str]:
    """표 한 줄의 칸. 줄 앞뒤의 | 는 칸을 나누지 않고, \\| 는 칸 안의 글자다."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.strip() for cell in _PIPE.split(body)]


def is_delimiter(line: str, columns: int) -> bool:
    """머리 행 다음 줄이 머리 행과 칸 수가 같은 구분선(|---|---|)인지. 칸 수가 다르면 화면은 표로 그리지 않는다."""
    if "|" not in line and ":" not in line:
        return False
    parts = cells(line)
    return len(parts) == columns and all(_DELIMITER_CELL.match(part) for part in parts)


def code_lines(lines: Sequence[str]) -> list[bool]:
    """줄마다 코드 블록(울타리 줄 포함)인지."""
    inside, out = False, []
    for line in lines:
        fence = bool(_FENCE.match(line))
        out.append(inside or fence)
        if fence:
            inside = not inside
    return out


def tables(lines: Sequence[str]) -> list[Span]:
    """표의 (머리 행 줄 번호, 끝 줄 번호). 끝 줄은 표에 들지 않는다."""
    code = code_lines(lines)
    found: list[Span] = []
    i = 0
    while i + 1 < len(lines):
        head = lines[i]
        if code[i] or code[i + 1] or "|" not in head or not is_delimiter(lines[i + 1], len(cells(head))):
            i += 1
            continue
        end = i + 2
        while end < len(lines) and not code[end] and "|" in lines[end] and lines[end].strip():
            end += 1
        found.append((i, end))
        i = end
    return found


def plain_answer(answer: str) -> PlainAnswer:
    """답변의 마크다운 기호를 지운 글. 기준은 모듈 설명에 있다."""
    lines = answer.split("\n")
    code = code_lines(lines)
    table_end = dict(tables(lines))
    out: list[str] = []
    rows: list[Span] = []
    labels: list[Span] = []
    pos = 0

    def add(line: str) -> int:
        nonlocal pos
        start = pos
        out.append(line)
        pos += len(line) + 1
        return start

    i = 0
    while i < len(lines):
        if i in table_end:
            for j in range(i, table_end[i]):
                if j == i + 1:
                    continue
                row = [_BOLD.sub("", cell) for cell in cells(lines[j])]
                line = CELL_SEPARATOR.join(row)
                start = add(line)
                rows.append((start, start + len(line)))
                labels.append((start, start + (len(line) if j == i else len(row[0]))))
            i = table_end[i]
            continue
        line = lines[i]
        add(line if code[i] else _BOLD.sub("", _LIST_MARKER.sub("", line, count=1)))
        i += 1
    text = "\n".join(out)
    chars = list(text)
    for start, end in labels:
        chars[start:end] = " " * (end - start)
    return PlainAnswer(text, "".join(chars), tuple(rows))


def split_spans(text: str, breaks: re.Pattern[str], rows: Sequence[Span] = (), *, keep: bool = False) -> list[Span]:
    """breaks 가 찾은 자리에서 나눈 구간. 표 행 안에서는 나누지 않는다.

    keep 이면 나눈 자리의 글자를 앞 구간에 넣는다.
    """
    spans: list[Span] = []
    start = 0
    for m in breaks.finditer(text):
        if any(a <= m.start() < b for a, b in rows):
            continue
        spans.append((start, m.end() if keep else m.start()))
        start = m.end()
    spans.append((start, len(text)))
    return spans
