"""생활 습관 점수 (data/docs/score-rules.md).

DB와 상관없는 순수 함수만 둔다. 활동·수면·심박을 각각 0~100점으로 매기고 영역마다 등급을 붙인다.
오늘의 점수는 점수가 있는 영역의 평균이며 등급이 없다. 데이터가 없는 영역은 None이다 (0점과 다르다).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# 2.1 걸음 (60점): (이상, 점수). 7,000보 만점은 Ding 2025, 중간 구간은 팀 제안
STEPS_BANDS = ((7000, 60), (5000, 48), (3000, 30), (2000, 15))
# 2.2 운동 (40점): 하루 30분 만점 (Haskell 2007)
EXERCISE_FULL_MINUTES = 30
EXERCISE_MAX_POINTS = 40
# 2.3 걷기(Health Connect 79)는 평균 시속 4.0km 이상만 운동으로 인정 (CDC, Compendium)
WALKING_CODE = 79
WALKING_MIN_SPEED_MPS = Decimal("4.0") / Decimal("3.6")
# 3장 수면: LE8 구간 가운데 값 기준점 (시간, 점수). 그 사이는 비례. 팀 제안
SLEEP_ANCHORS = ((3.5, 0), (4.5, 20), (5.5, 40), (6.5, 70), (8.0, 100))
# 4장 심박: 수면 중 심박 - 평소. 구간은 팀 제안
HEART_CHECK_WEAR_DROP = Decimal(10)
# 6.2 등급: (이상, 코드)
GRADES = ((80, "excellent"), (65, "stable"), (50, "attention"), (0, "care"))


def round_half_up(x: float | Decimal) -> int:
    """사사오입. 파이썬 round()는 0.5를 짝수 쪽으로 보내서(round(2.5) == 2) 쓰지 않는다 (1.2)."""
    return int(Decimal(str(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def grade(points: int | None) -> str | None:
    if points is None:
        return None
    return next(code for lower, code in GRADES if points >= lower)


def steps_points(steps: int | None) -> int | None:
    """걸음 기록이 없거나 0보면 None (활동 데이터 없음, 6.1)."""
    if not steps:
        return None
    return next((p for lower, p in STEPS_BANDS if steps >= lower), 0)


def counts_as_exercise(code: int, avg_speed_mps: Decimal | float | None) -> bool:
    """걷기는 평균 속도가 시속 4km 이상일 때만. 속도가 없는 걷기는 넣지 않는다 (2.3)."""
    if code != WALKING_CODE:
        return True
    return avg_speed_mps is not None and Decimal(str(avg_speed_mps)) >= WALKING_MIN_SPEED_MPS


def exercise_points(minutes: int) -> int:
    return round_half_up(min(minutes / EXERCISE_FULL_MINUTES, 1) * EXERCISE_MAX_POINTS)


def sleep_points(sleep_minutes: int | None) -> int | None:
    """그날 깬 주 수면의 실제 수면 시간. 9시간 이상은 LE8 그대로, 그 아래는 기준점 사이 비례 (3장)."""
    if sleep_minutes is None:
        return None
    hours = sleep_minutes / 60
    if hours >= 10:
        return 40
    if hours >= 9:
        return 90
    if hours >= SLEEP_ANCHORS[-1][0]:
        return 100
    if hours <= SLEEP_ANCHORS[0][0]:
        return 0
    for (h0, p0), (h1, p1) in zip(SLEEP_ANCHORS, SLEEP_ANCHORS[1:], strict=False):
        if h0 <= hours <= h1:
            return round_half_up(p0 + (p1 - p0) * (hours - h0) / (h1 - h0))
    raise AssertionError("unreachable")


def baseline(night_hr_avgs: Sequence[Decimal]) -> Decimal | None:
    """평소 수면 중 심박: 이전 28일 주 수면의 수면 중 평균 심박 평균, 소수 첫째 자리 (4장)."""
    if not night_hr_avgs:
        return None
    avg = sum(night_hr_avgs, Decimal(0)) / len(night_hr_avgs)
    return avg.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def heart_points(sleep_hr_avg: Decimal | None, hr_baseline: Decimal | None) -> tuple[int | None, bool]:
    """(점수, 착용 확인 안내). 평소보다 높아질 때만 감점한다 (4장).

    저장·화면에 보이는 값(소수 첫째 자리)끼리 비교해, 화면의 차이로 점수를 다시 설명할 수 있게 한다.
    """
    if sleep_hr_avg is None or hr_baseline is None:
        return None, False
    diff = Decimal(str(sleep_hr_avg)) - hr_baseline
    check_wear = diff <= -HEART_CHECK_WEAR_DROP
    if diff <= 5:
        return 100, check_wear
    if diff <= 7:
        return 70, check_wear
    if diff < 10:
        return 55, check_wear
    return 40, check_wear


@dataclass(frozen=True)
class Exercise:
    code: int
    duration_minutes: int
    avg_speed_mps: Decimal | float | None


@dataclass(frozen=True)
class DayScore:
    total_score: int | None
    activity_score: int | None
    activity_grade: str | None
    steps_score: int | None
    exercise_score: int | None
    sleep_score: int | None
    sleep_grade: str | None
    heart_score: int | None
    heart_grade: str | None
    check_wear: bool
    steps: int | None
    exercise_minutes: int | None
    sleep_minutes: int | None
    sleep_hr_avg: Decimal | None
    hr_baseline: Decimal | None
    hr_baseline_nights: int


def day_score(
    steps: int | None,
    exercises: Sequence[Exercise],
    sleep_minutes: int | None,
    sleep_hr_avg: Decimal | None,
    baseline_hr_avgs: Sequence[Decimal],
) -> DayScore:
    """하루 점수. 입력은 그날 집계 값 (이상치 운동은 이미 빠져 있다)."""
    st = steps_points(steps)
    if st is None:
        ex_minutes = ex = activity = None
    else:
        ex_minutes = sum(e.duration_minutes for e in exercises if counts_as_exercise(e.code, e.avg_speed_mps))
        ex = exercise_points(ex_minutes)
        activity = st + ex
    sl = sleep_points(sleep_minutes)
    base = baseline(baseline_hr_avgs)
    hr, check_wear = heart_points(sleep_hr_avg, base)
    areas = [p for p in (activity, sl, hr) if p is not None]
    return DayScore(
        total_score=round_half_up(sum(areas) / len(areas)) if areas else None,
        activity_score=activity,
        activity_grade=grade(activity),
        steps_score=st,
        exercise_score=ex,
        sleep_score=sl,
        sleep_grade=grade(sl),
        heart_score=hr,
        heart_grade=grade(hr),
        check_wear=check_wear,
        steps=steps,
        exercise_minutes=ex_minutes,
        sleep_minutes=sleep_minutes,
        sleep_hr_avg=sleep_hr_avg,
        hr_baseline=base,
        hr_baseline_nights=len(baseline_hr_avgs),
    )
