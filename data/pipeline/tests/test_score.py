"""생활 습관 점수 규칙 (data/docs/score-rules.md). 문서의 표와 계산 예시를 그대로 확인한다."""

from decimal import Decimal

import pytest

from candiy_pipeline import score
from candiy_pipeline.score import Exercise, day_score

D = Decimal


def h(hours, minutes=0):
    return hours * 60 + minutes


@pytest.mark.parametrize(
    ("x", "expected"), [(2.5, 3), (16.5, 17), (72.5, 73), (66.67, 67), (0.49, 0), (13.333, 13), (26.667, 27)]
)
def test_round_half_up(x, expected):
    assert score.round_half_up(x) == expected


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        (100, "excellent"),
        (80, "excellent"),
        (79, "stable"),
        (65, "stable"),
        (64, "attention"),
        (50, "attention"),
        (49, "care"),
        (0, "care"),
        (None, None),
    ],
)
def test_grade(points, expected):
    assert score.grade(points) == expected


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        (None, None),
        (0, None),
        (800, 0),
        (1999, 0),
        (2000, 15),
        (3000, 30),
        (5000, 48),
        (6999, 48),
        (7000, 60),
        (15000, 60),
    ],
)
def test_steps_points(steps, expected):
    # 기록이 없거나 0보면 데이터 없음(None), 800보는 데이터가 있는 0점 (6.1)
    assert score.steps_points(steps) == expected


@pytest.mark.parametrize(("minutes", "expected"), [(0, 0), (10, 13), (20, 27), (24, 32), (30, 40), (90, 40)])
def test_exercise_points(minutes, expected):
    assert score.exercise_points(minutes) == expected


@pytest.mark.parametrize(
    ("code", "speed", "expected"),
    [
        (79, 1.12, True),
        (79, 4.0 / 3.6, True),
        (79, 1.10, False),
        (79, None, False),
        (56, None, True),
        (57, 0.5, True),
        (37, 0.9, True),
    ],
)
def test_counts_as_exercise(code, speed, expected):
    # 걷기만 시속 4km 이상, 속도 없는 걷기는 제외. 러닝머신·등산 등은 속도와 상관없음 (2.3)
    assert score.counts_as_exercise(code, speed) is expected


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (None, None),
        (h(3), 0),
        (h(3, 30), 0),
        (h(4), 10),
        (h(4, 30), 20),
        (h(5), 30),
        (h(5, 30), 40),
        (h(6), 55),
        (h(6, 20), 65),
        (h(6, 30), 70),
        (h(7), 80),
        (h(7, 30), 90),
        (h(8), 100),
        (h(8, 59), 100),
        (h(9), 90),
        (h(9, 59), 90),
        (h(10), 40),
        (h(11, 30), 40),
    ],
)
def test_sleep_points(minutes, expected):
    # 3장 표: 기준점에서는 LE8 점수와 같고 그 사이는 비례. 9시간 이상은 LE8 그대로
    assert score.sleep_points(minutes) == expected


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        (-12, (100, True)),
        (-10, (100, True)),
        (-9.9, (100, False)),
        (0, (100, False)),
        (5, (100, False)),
        (5.1, (70, False)),
        (7, (70, False)),
        (7.1, (55, False)),
        (9.9, (55, False)),
        (10, (40, False)),
        (15, (40, False)),
    ],
)
def test_heart_points(diff, expected):
    base = D("58.0")
    assert score.heart_points(base + D(str(diff)), base) == expected


def test_heart_without_data():
    assert score.heart_points(None, D("58.0")) == (None, False)
    assert score.heart_points(D("60.0"), None) == (None, False)


def test_baseline_rounds_to_one_decimal():
    assert score.baseline([]) is None
    assert score.baseline([D("58.0"), D("58.1"), D("58.1")]) == D("58.1")  # 58.0666… → 58.1


NORMAL_NIGHTS = [D("58.0")] * 20


def test_example_mixed_day():
    # 6.3 예시: 5천 보대 + 운동 24분, 6시간 20분 수면, 수면 중 심박 +8
    s = day_score(5400, [Exercise(56, 24, 2.5)], h(6, 20), D("66.0"), NORMAL_NIGHTS)
    assert (s.steps_score, s.exercise_score, s.activity_score, s.activity_grade) == (48, 32, 80, "excellent")
    assert (s.sleep_score, s.sleep_grade) == (65, "stable")
    assert (s.heart_score, s.heart_grade, s.check_wear) == (55, "attention", False)
    assert s.total_score == 67
    assert (s.hr_baseline, s.hr_baseline_nights, s.exercise_minutes) == (D("58.0"), 20, 24)


def test_example_rest_day():
    # 운동 쉬는 날(7천 보 이상), 7시간 수면, 평소 심박 → 60 관심, 80, 100 → 80
    s = day_score(8000, [], h(7), D("58.0"), NORMAL_NIGHTS)
    assert (s.activity_score, s.activity_grade, s.exercise_minutes) == (60, "attention", 0)
    assert (s.sleep_score, s.heart_score, s.total_score) == (80, 100, 80)


def test_example_low_day():
    # 3천 보대 + 운동 없음, 5시간 수면, +10 이상 → 30, 30, 40 → 33
    s = day_score(3500, [], h(5), D("68.0"), NORMAL_NIGHTS)
    assert (s.activity_score, s.sleep_score, s.heart_score, s.total_score) == (30, 30, 40, 33)
    assert (s.activity_grade, s.sleep_grade, s.heart_grade) == ("care", "care", "care")


def test_example_no_sleep():
    # 워치 안 차고 잠: 수면·심박 데이터 없음, 오늘의 점수는 활동만의 평균
    s = day_score(5400, [Exercise(56, 24, 2.5)], None, None, NORMAL_NIGHTS)
    assert (s.sleep_score, s.sleep_grade, s.heart_score, s.heart_grade) == (None, None, None, None)
    assert s.total_score == 80


def test_example_first_day():
    # 가입 첫날(기준선 0밤): 심박 데이터 없음 → (80 + 65) / 2 = 72.5 → 73
    s = day_score(5400, [Exercise(56, 24, 2.5)], h(6, 20), D("60.0"), [])
    assert (s.heart_score, s.hr_baseline, s.hr_baseline_nights) == (None, None, 0)
    assert s.total_score == 73


def test_example_before_first_sync():
    # 아침 첫 동기화 전: 걸음 기록 없음 → 활동 데이터 없음 (0점이 아님)
    s = day_score(None, [Exercise(56, 30, 2.5)], h(6, 30), D("58.0"), NORMAL_NIGHTS)
    assert (s.activity_score, s.steps_score, s.exercise_score, s.exercise_minutes) == (None, None, None, None)
    assert s.total_score == 85  # (70 + 100) / 2


def test_zero_steps_is_no_data_but_low_steps_is_zero():
    assert day_score(0, [], None, None, []).activity_score is None
    s = day_score(800, [], None, None, [])
    assert (s.activity_score, s.activity_grade, s.total_score) == (0, "care", 0)


def test_all_missing_total_is_none():
    s = day_score(None, [], None, None, [])
    assert s.total_score is None


def test_slow_walk_excluded_from_exercise():
    # 04 운동 안 함: 자동 인식 걷기가 시속 3.2km라 운동 0분, 걸음 점수만
    s = day_score(2600, [Exercise(79, 14, 3.2 / 3.6)], None, None, [])
    assert (s.exercise_minutes, s.exercise_score, s.activity_score) == (0, 0, 15)


def test_check_wear_does_not_reduce_score():
    s = day_score(None, [], h(8), D("46.0"), NORMAL_NIGHTS)  # 평소보다 12 낮음
    assert (s.heart_score, s.check_wear) == (100, True)
