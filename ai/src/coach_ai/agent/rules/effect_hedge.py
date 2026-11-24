"""답 글에서 생활 요령의 효과를 단정한 끝맺음을 완곡한 말로 바꾼다 (시스템 프롬프트 진단과 약 절).

바꾸는 말은 저장된 답과 회귀 기록에 실제로 나온 다섯 가지뿐이고, 문장 끝(마침표·물음표·느낌표·줄바꿈·글 끝 앞)에
올 때만 바꾼다. 부정문은 형식이 달라 바꾸지 않는다. 따옴표 안, 앞에 "수 있"·"수도 있"·
"지도 모르"·"것 같"이 든 이미 완곡한 문장, 상담·진료를 권하는 문장도 바꾸지 않는다.
답 조각은 받는 대로 내보내고, 바꿀 말의 앞부분일 수 있는 끝 글자와 문장 끝인지 아직 모르는 바꿀 말만 잡아 둔다.
"""

from __future__ import annotations

import re

HEDGES: dict[str, str] = {
    "도움이 됩니다": "도움이 될 수 있어요",
    "도움을 줍니다": "도움을 줄 수 있어요",
    "효과적입니다": "효과적일 수 있어요",
    "수월해집니다": "수월해질 수 있어요",
    "효과를 기대할 수 있습니다": "효과가 있을 수 있어요",
}
_TARGET = re.compile("|".join(re.escape(word) for word in sorted(HEDGES, key=len, reverse=True)))
_ENDS = ".?!\n"
_HEDGED = re.compile(r"수\s?(?:도\s?)?있|지도\s?모르|것\s?같")
_CONSULT = re.compile(r"상담|진료|의료진|전문의|병원|의사|약사")
_QUOTES = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"))
# 문장 시작을 찾을 때 쓰는, 내보낸 글 끝부분의 길이
_SEEN = 500


def _in_quote(text: str) -> bool:
    for open_mark, close_mark in _QUOTES:
        if open_mark == close_mark:
            if text.count(open_mark) % 2:
                return True
        elif text.count(open_mark) > text.count(close_mark):
            return True
    return False


def _keeps(sentence: str) -> bool:
    """바꿀 말 앞까지의 문장 글로 그대로 둘 문장인지 정한다."""
    return _in_quote(sentence) or bool(_HEDGED.search(sentence) or _CONSULT.search(sentence))


def _sentence_start(text: str) -> int:
    """text 안에서 마지막 문장이 시작하는 자리. 문장 끝이 없으면 0 이다. 소수점(7.5)은 문장 끝이 아니다."""
    for i in range(len(text) - 1, -1, -1):
        if text[i] in _ENDS and not (text[i] == "." and text[i + 1 : i + 2].isdigit()):
            return i + 1
    return 0


def _prefix_len(text: str) -> int:
    """text 끝이 바꿀 말의 앞부분과 겹치는 길이."""
    best = 0
    for word in HEDGES:
        for size in range(min(len(word) - 1, len(text)), best, -1):
            if text.endswith(word[:size]):
                best = size
                break
    return best


class EffectHedge:
    """답 조각을 차례로 받아 효과 단정 끝맺음을 바꾼 글을 반환한다. changed 는 바꾼 문장 수다."""

    def __init__(self) -> None:
        self._pending = ""
        # 이미 내보낸 글의 끝부분. 조각이 소수점(7.)에서 끊겨도 다음 조각과 이어 문장 시작을 찾는다
        self._seen = ""
        self.changed = 0

    def feed(self, text: str) -> str:
        self._pending += text
        return self._take(final=False)

    def flush(self) -> str:
        """잡아 둔 글을 모두 내보낸다. 답 글이 끝났을 때 부른다."""
        return self._take(final=True)

    def _take(self, *, final: bool) -> str:
        out: list[str] = []
        while (m := _TARGET.search(self._pending)) is not None:
            self._emit(out, self._pending[: m.start()])
            if m.end() == len(self._pending) and not final:
                # 문장 끝인지는 다음 조각을 받아야 안다
                self._pending = self._pending[m.start() :]
                return "".join(out)
            word = m.group()
            at_end = m.end() == len(self._pending) or self._pending[m.end()] in _ENDS
            if at_end and not _keeps(self._seen[_sentence_start(self._seen) :]):
                word = HEDGES[word]
                self.changed += 1
            self._emit(out, word)
            self._pending = self._pending[m.end() :]
        keep = 0 if final else _prefix_len(self._pending)
        self._emit(out, self._pending[: len(self._pending) - keep])
        self._pending = self._pending[len(self._pending) - keep :]
        return "".join(out)

    def _emit(self, out: list[str], text: str) -> None:
        if not text:
            return
        out.append(text)
        self._seen = (self._seen + text)[-_SEEN:]


def hedge_text(text: str) -> tuple[str, int]:
    """글 전체를 한 번에 바꾼다. 바꾼 글과 바꾼 문장 수를 반환한다."""
    hedge = EffectHedge()
    out = hedge.feed(text) + hedge.flush()
    return out, hedge.changed
