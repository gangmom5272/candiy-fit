"""생활 습관 점수의 수면 점수 규칙 (data/docs/score-rules.md 3장).

수면 점수는 파이프라인(candiy_pipeline.score.sleep_points)이 계산해 저장하고, 점수 처리 함수는 저장값을 그대로
쓴다. AI 패키지는 파이프라인에 의존하지 않아, 점수 결과에 넣는 목표 수면 시간별 점수만 같은 규칙으로 여기서
계산한다. 기준점과 계산식은 파이프라인과 같아야 한다 (tests/test_lifestyle_score.py 가 3장 값으로 확인한다).
"""

from __future__ import annotations

from itertools import pairwise

from coach_ai.tools.results import format_minutes
from coach_ai.tools.rounding import round_half_up

# 3장 기준점 (시간, 점수). 기준점 사이는 시간에 비례한다
SLEEP_ANCHORS = ((3.5, 0), (4.5, 20), (5.5, 40), (6.5, 70), (8.0, 100))
# 지금보다 더 자는 시간(분)과 목표 수면 시간(시간)
TARGET_EXTRA_MINUTES = (30, 60)
TARGET_HOURS = (7, 8)

SLEEP_SCORE_RULE = (
    "수면 점수는 그날 깬 주 수면의 수면 시간만으로 매긴다. 근거는 Life's Essential 8(LE8) 수면 점수표다. "
    "기준점은 8시간 100점, 6시간 30분 70점, 5시간 30분 40점, 4시간 30분 20점, 3시간 30분 0점이고, "
    "기준점 사이는 시간에 비례해 계산된다. 3시간 30분 이하는 0점, 8시간 이상 9시간 미만은 100점, "
    "9시간 이상 10시간 미만은 90점, 10시간 이상은 40점이다. "
    "깊은 잠과 규칙성은 수면 점수에 들어가지 않는 값이다"
)


def sleep_points(sleep_minutes: int) -> int:
    """실제 수면 시간(분)의 수면 점수. 9시간 이상은 LE8 그대로, 그 아래는 기준점 사이 비례이고 1.2 반올림이다."""
    hours = sleep_minutes / 60
    if hours >= 10:
        return 40
    if hours >= 9:
        return 90
    if hours >= SLEEP_ANCHORS[-1][0]:
        return 100
    if hours <= SLEEP_ANCHORS[0][0]:
        return 0
    for (h0, p0), (h1, p1) in pairwise(SLEEP_ANCHORS):
        if h0 <= hours <= h1:
            return int(round_half_up(p0 + (p1 - p0) * (hours - h0) / (h1 - h0)))
    raise AssertionError("기준점 사이에 없는 수면 시간")


def sleep_targets(sleep_minutes: int, score: int) -> str:
    """이날 수면 시간과 점수, 30분·1시간 더 잤을 때와 7시간·8시간일 때의 하루 점수.

    이날 점수는 저장값이다. 목표 시간이 이날이나 더 잔 시간과 같으면 다시 적지 않는다.
    """
    parts = [f"이날 {format_minutes(sleep_minutes)} {score}점"]
    listed = {sleep_minutes}
    for extra in TARGET_EXTRA_MINUTES:
        minutes = sleep_minutes + extra
        listed.add(minutes)
        parts.append(f"{format_minutes(extra)} 더 자면 {format_minutes(minutes)} {sleep_points(minutes)}점")
    for hours in TARGET_HOURS:
        if hours * 60 not in listed:
            parts.append(f"{hours}시간이면 {sleep_points(hours * 60)}점")
    return f"하루 수면 시간별 수면 점수: {', '.join(parts)}"
