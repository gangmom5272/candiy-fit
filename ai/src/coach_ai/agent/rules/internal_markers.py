"""답 글에 섞여 온 내부 표시를 화면에 나가기 전에 정리한다.

도구 호출이 정상 형식으로 나뉘지 않은 응답에서는 사고 채널 표시와 괄호만 있는 줄이 답 중간에 글로 온다.
래퍼(llm/client.py)의 ThoughtFilter 는 응답 맨 앞의 사고 채널만 제외한다. 표시가 없는 답은 한 글자도 바꾸지 않는다.
"""

from __future__ import annotations

import re

OPEN = "<|channel>"
CLOSE = "<channel|>"
# <|"|>, <|im_end|> 처럼 <| 와 |> 로 감싼 표시
_TOKEN = r"<\|[^<>|\s]{1,32}\|>"
MARKER = re.compile(f"{re.escape(OPEN)}|{re.escape(CLOSE)}|{_TOKEN}")
_TOKEN_RE = re.compile(_TOKEN)
# 조각 끝에 온 표시의 앞부분 ("<", "<|", "<|cha", "<|im_end|")
_TOKEN_HEAD = re.compile(r"<(?:\|[^<>|\s]{0,32}\|?)?")
_BRACKETS = frozenset("(){}[]")
# 줄 맨 앞부터 이 글자만 있는 끝부분은 괄호만 있는 줄이 될 수 있어 다음 조각까지 잡아 둔다
_HOLDABLE = _BRACKETS | frozenset(" \t\r\n")
_LINE_END = re.compile(r"(?<=\n)")


def has_markers(text: str) -> bool:
    return MARKER.search(text) is not None


def _lines(text: str) -> list[str]:
    return [line for line in _LINE_END.split(text) if line]


def _bracket_line(line: str) -> bool:
    body = line.strip()
    return bool(body) and all(c in _BRACKETS or c in " \t" for c in body)


def clean(text: str) -> str:
    """모아 두었다가 보내는 답의 내부 표시를 정리한다. 표시가 없으면 text 를 그대로 반환한다.

    마지막 CLOSE 뒤만 남기고, 남은 OPEN 부터 끝(닫는 표시 없이 끝난 사고 구간)과 <|…|> 표시를 지운 뒤
    괄호만 있는 줄과 앞쪽 공백을 지운다.
    """
    if not has_markers(text):
        return text
    end = text.rfind(CLOSE)
    if end >= 0:
        text = text[end + len(CLOSE) :]
    start = text.find(OPEN)
    if start >= 0:
        text = text[:start]
    text = _TOKEN_RE.sub("", text)
    return "".join(line for line in _lines(text) if not _bracket_line(line)).lstrip()


def _partial_len(text: str) -> int:
    """text 끝이 표시의 앞부분일 수 있는 길이. 표시는 첫 글자 말고는 "<" 가 없어 마지막 "<" 만 확인한다."""
    start = text.rfind("<")
    if start < 0:
        return 0
    tail = text[start:]
    if OPEN.startswith(tail) or CLOSE.startswith(tail) or _TOKEN_HEAD.fullmatch(tail):
        return len(tail)
    return 0


def _close_prefix_len(text: str) -> int:
    for n in range(min(len(text), len(CLOSE) - 1), 0, -1):
        if text.endswith(CLOSE[:n]):
            return n
    return 0


class MarkerFilter:
    """바로 흘려보내는 답 조각에서 내부 표시를 제외한다.

    OPEN 부터 CLOSE 까지와 <|…|> 표시는 내보내지 않는다. 표시가 여러 조각으로 나뉘어 와도 되도록 표시의 앞부분일 수
    있는 끝 글자와, 줄 맨 앞부터 괄호·공백만 있는 끝부분은 다음 조각까지 잡아 둔다. 표시 앞이나 표시를 찾은 뒤의
    괄호만 있는 줄은 지우고, 이미 내보낸 글은 그대로 둔다. 표시가 없으면 내보낸 글을 이어 붙인 글이 받은 글과 같다.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False
        # CLOSE 바로 뒤이고 줄 맨 앞이면 앞쪽 공백을 지운다
        self._after_close = False
        self._line_start = True
        self.found = False

    def feed(self, text: str) -> str:
        """받은 조각을 넣고 내보낼 문자열을 반환한다."""
        self._buf += text
        return self._drain(final=False)

    def finish(self) -> str:
        """응답이 끝났을 때 잡아 둔 문자열 중 내보낼 것을 반환한다."""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        out: list[str] = []
        while self._buf:
            if self._inside:
                end = self._buf.find(CLOSE)
                if end < 0:
                    # 닫는 표시 없이 끝나면 남은 것은 모두 사고 내용이다
                    keep = 0 if final else _close_prefix_len(self._buf)
                    self._buf = self._buf[len(self._buf) - keep :]
                    break
                self._buf = self._buf[end + len(CLOSE) :]
                self._inside, self._after_close = False, True
                continue
            if self._after_close and self._line_start:
                self._buf = self._buf.lstrip()
                if not self._buf:
                    break
            self._after_close = False
            m = MARKER.search(self._buf)
            if m:
                out.append(self._plain(self._buf[: m.start()], drop=True))
                self.found = True
                self._inside = m.group() == OPEN
                self._after_close = m.group() == CLOSE
                self._buf = self._buf[m.end() :]
                continue
            if final:
                out.append(self._plain(self._buf, drop=self.found))
                self._buf = ""
                break
            cut = self._hold_start(self._buf)
            out.append(self._plain(self._buf[:cut], drop=self.found))
            self._buf = self._buf[cut:]
            break
        return "".join(out)

    def _hold_start(self, text: str) -> int:
        cut = len(text) - _partial_len(text)
        start = cut
        while start > 0 and text[start - 1] in _HOLDABLE:
            start -= 1
        for i in range(start, cut + 1):
            if (i == 0 and self._line_start) or (i > 0 and text[i - 1] == "\n"):
                return i
        return cut

    def _plain(self, text: str, *, drop: bool) -> str:
        """표시가 없는 글을 내보낼 형식으로 바꾼다. drop 이면 괄호만 있는 줄을 지운다."""
        out: list[str] = []
        for line in _lines(text):
            if drop and self._line_start and _bracket_line(line):
                continue
            out.append(line)
            self._line_start = line.endswith("\n")
        return "".join(out)
