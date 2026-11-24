from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from coach_ai.adapters.exercise_activity import ExerciseActivityDay
from coach_ai.adapters.memory import (
    DailyMessageDay,
    ExerciseSession,
    HeartDay,
    InMemoryHealthData,
    ScoreDay,
    SleepSession,
    StepDay,
    SyncState,
)
from coach_ai.tools.periods import DEFAULT_TZ

_KST = ZoneInfo(DEFAULT_TZ)
DAYS = 14

# 오늘에서 며칠 전인지 → 값. 빠진 날은 기록이 없는 날이다
_STEPS = {13: 7420, 12: 9105, 11: 5230, 10: 8840, 9: 6610, 8: 11240, 7: 3105, 6: 7980, 5: 6450, 4: 0, 3: 8120, 1: 7310}
# (하루 평균 심박, 유효 시간대 수)
_HEART = {
    13: (71.2, 16),
    12: (70.4, 15),
    11: (72.8, 16),
    10: (69.9, 14),
    9: (73.5, 16),
    8: (70.1, 13),
    7: (74.2, 15),
    6: (71.7, 16),
    4: (72.0, 12),
    3: (70.6, 16),
    2: (71.9, 15),
    1: (72.3, 16),
}
# (취침 시:분, 전날 밤인지, 기상 시:분, 실제 수면 분, 산소포화도 평균)
_NIGHTS = {
    13: (23, 40, True, 7, 10, 425, 95.9),
    12: (0, 20, False, 7, 0, 382, 96.2),
    11: (1, 5, False, 7, 30, 360, None),
    10: (23, 55, True, 6, 50, 398, 95.4),
    9: (0, 40, False, 7, 20, 371, 96.0),
    8: (23, 30, True, 7, 5, None, 95.7),
    7: (1, 20, False, 7, 15, 330, 94.8),
    6: (0, 10, False, 7, 40, 418, 96.3),
    5: (23, 50, True, 7, 35, 441, 95.5),
    4: (0, 35, False, 7, 5, 368, None),
    2: (1, 10, False, 7, 15, 340, 95.1),
    1: (0, 5, False, 7, 25, 412, 96.0),
}
# 오늘에서 며칠 전인지 → [(코드, 시작 시:분, 운동 분, 운동 시간 포함 여부, 칼로리, 거리 m, 평균·최고 심박,
# 평균 속도 m/s)]. 빠진 날은 운동하지 않은 날(0분)이다. 활동 기록이 없는 2일 전에는 운동도 없다.
# 근력 운동·요가·기준표에 없는 코드는 거리 기록이 없고, 5일 전 느린 걷기는 저장된 거리가 0이다
_WORKOUTS = {
    13: [(56, 7, 10, 32, True, 310.5, 5571.4, (152.0, 171), 2.9)],
    12: [(8, 10, 30, 45, True, 280.0, 15118.6, (128.0, 150), 5.6)],
    # 시속 4km 미만 걷기는 운동 시간에 들어가지 않는다
    10: [(79, 8, 5, 20, False, 58.0, 1103.6, (94.0, 104), 0.92)],
    9: [(70, 19, 0, 40, True, 180.0, None, (118.5, 142), None)],
    8: [(79, 18, 30, 30, True, 120.0, 2498.7, (105.0, 121), 1.39)],
    6: [(56, 7, 20, 28, True, 270.0, 4790.2, (150.0, 168), 2.85)],
    5: [(84, 10, 0, 50, True, None, None, (None, None), None), (79, 18, 20, 22, False, 65.0, 0.0, (96.0, 108), 0.89)],
    4: [(83, 21, 0, 30, True, 95.0, None, (88.0, 102), None)],
    1: [(56, 19, 5, 35, True, 330.0, 6201.8, (154.0, 172), 2.95)],
}
# 오늘 진행 중인 걸음 수
_TODAY_STEPS = 4210
# 주 수면의 잠든 동안 심박 (평균, 최저, 최고) bpm. 평균은 점수 행의 수면 중 평균 심박과 같은 값이고 8일 전에 깬
# 수면은 측정이 없다
_SLEEP_HEART = {
    13: (58.2, 50, 74), 12: (57.6, 49, 73), 11: (59.4, 51, 77), 10: (58.8, 50, 75), 9: (57.9, 49, 72),
    7: (60.1, 52, 79), 6: (58.5, 50, 76), 5: (57.4, 49, 71), 4: (61.0, 53, 80), 2: (66.3, 57, 88), 1: (59.0, 51, 76),
}  # fmt: skip
# 산소포화도 90% 미만 측정이 있는 수면 → (그 수면의 최저 %, 90% 미만 측정 수). 다른 수면은 최저가 90% 이상이라 0회다
_LOW_SPO2 = {2: (88.6, 3)}
# 90% 미만으로 기록된 시각 (깬 날의 시:분, 이른 순). 다른 수면은 90% 미만 기록이 없어 빈 목록이다
_LOW_SPO2_TIMES = {2: ((3, 12), (3, 40), (4, 5))}
# 오늘에서 며칠 전인지 → (오늘의 점수, (활동 점수, 등급, 걸음 점수, 운동 점수), (수면 점수, 등급),
# (심박 점수, 등급), 평소 수면 중 심박, 평소 밤 수). 위 합성 값과 _SLEEP_HEART 평균으로 파이프라인 점수 함수
# (candiy_pipeline.score.day_score)를 실행한 저장값이고, 활동 행이나 주 수면이 있는 날마다 한 행이 있다
_SCORES = {
    13: (91, (100, "excellent", 60, 40), (82, "excellent"), (None, None), None, 0),
    12: (89, (100, "excellent", 60, 40), (66, "stable"), (100, "excellent"), 58.2, 1),
    11: (68, (48, "care", 48, 0), (55, "attention"), (100, "excellent"), 57.9, 2),
    10: (78, (60, "attention", 60, 0), (73, "stable"), (100, "excellent"), 58.4, 3),
    9: (83, (88, "excellent", 48, 40), (61, "attention"), (100, "excellent"), 58.5, 4),
    8: (100, (100, "excellent", 60, 40), (None, None), (None, None), 58.4, 5),
    7: (57, (30, "care", 30, 0), (40, "care"), (100, "excellent"), 58.4, 5),
    6: (92, (97, "excellent", 60, 37), (79, "stable"), (100, "excellent"), 58.7, 6),
    5: (92, (88, "excellent", 48, 40), (87, "excellent"), (100, "excellent"), 58.6, 7),
    4: (80, (None, None, None, None), (59, "attention"), (100, "excellent"), 58.5, 8),
    3: (60, (60, "attention", 60, 0), (None, None), (None, None), 58.8, 9),
    2: (50, (None, None, None, None), (45, "care"), (55, "attention"), 58.8, 9),
    1: (92, (100, "excellent", 60, 40), (77, "stable"), (100, "excellent"), 59.5, 10),
    0: (30, (30, "care", 30, 0), (None, None), (None, None), 59.5, 11),
}
# 오늘의 한마디 (코드, 제목, 설명). 오늘 걸음은 0보다 많고 7,000보다 적고 오늘 운동은 0분이며 오늘 깬 수면은
# 동기화 전이라, 연동 쪽 규칙(API 명세 5.5)으로는 걸음 기록으로 고른 7번 문구다
_TODAY_MESSAGE = (
    "STEPS_PROGRESS",
    "한 걸음씩, 내 페이스대로 가볼까요?",
    "오늘 남겨진 걸음도 소중해요. 움직이고 싶은 때에 편한 만큼 이어가 보세요.",
)
# 운동 코드 기준표의 이름·분류. 84는 기준표에 없는 코드다
_CODES = {
    8: ("자전거", "cardio"),
    56: ("달리기", "cardio"),
    70: ("근력 운동", "strength"),
    79: ("걷기", "cardio"),
    83: ("요가", "flexibility"),
}


def sample_health_data(
    user_id: str, now: dt.datetime, *, include_exercise_activity: bool = False
) -> InMemoryHealthData:
    """now 기준 최근 14일의 합성 건강데이터를 만든다.

    결측일, 0보인 날, 동기화 전 날, 오늘 진행 중 값, 자정을 넘는 취침, 낮잠, 실제 수면 시간을 모르는 밤이 들어 있다.
    주 수면에는 잠든 동안 심박과 수면 중 산소포화도가 있고, 심박만 있는 수면 기록, 산소포화도만 있는 수면 기록,
    90% 미만 측정이 있는 수면 기록과 그 시각이 들어 있다.
    운동은 운동 시간에 들어가는 운동과 들어가지 않는 느린 걷기, 기준표에 없는 코드, 0분인 날, 활동 기록이 없는 날,
    운동이 없는 오늘이 들어 있다. 세션 거리는 소수 값, 기록 없음(None), 0이 들어 있다.
    생활 습관 점수는 매일 한 행이 있다. 활동만 있는 오늘(관리), 활동 기록이 없는 날, 수면이 관리인 날, 평소 값이 없어
    심박 점수가 없는 연동일이 들어 있다. 오늘의 한마디는 오늘 걸음 기록으로 고른 문구 한 건이다.
    """
    local_now = now.astimezone(_KST)
    today = local_now.date()

    def day(ago: int) -> dt.date:
        return today - dt.timedelta(days=ago)

    def at(d: dt.date, hh: int, mm: int) -> dt.datetime:
        return dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=_KST)

    sessions: list[SleepSession] = []
    for ago, (bh, bm, prev_night, wh, wm, minutes, spo2) in _NIGHTS.items():
        wake_day = day(ago)
        bed_day = wake_day - dt.timedelta(days=1) if prev_night else wake_day
        start, end = at(bed_day, bh, bm), at(wake_day, wh, wm)
        stages = (None, None, None, None) if minutes is None else (70, 85, minutes - 155, 18)
        oxygen = {}
        if spo2 is not None:
            lowest, low_count = _LOW_SPO2.get(ago, (round(spo2 - 4.2, 1), 0))
            # 합성 값은 1분마다 측정한 것으로 보고 측정 수를 세션 분과 같게 둔다
            oxygen = dict(
                spo2_avg=spo2, spo2_min=lowest, spo2_max=round(min(spo2 + 2.1, 100.0), 1),
                spo2_count=int((end - start).total_seconds()) // 60, spo2_low_count=low_count,
                spo2_low_times=tuple(at(wake_day, hh, mm) for hh, mm in _LOW_SPO2_TIMES.get(ago, ())),
            )  # fmt: skip
        heart = {}
        if ago in _SLEEP_HEART:
            avg, lowest_hr, highest_hr = _SLEEP_HEART[ago]
            heart = dict(avg_heart_rate=avg, min_heart_rate=lowest_hr, max_heart_rate=highest_hr)
        sessions.append(SleepSession(wake_day, start, end, True, minutes, *stages, **oxygen, **heart))
    # 주 수면이 아닌 낮잠. 하루 값과 밤별 기록에 들어가지 않는다
    nap_day = day(6)
    sessions.append(SleepSession(nap_day, at(nap_day, 14, 0), at(nap_day, 14, 40), False, 38, 0, 0, 38, 2))

    steps = [StepDay(day(ago), v) for ago, v in _STEPS.items()]
    heart = [
        HeartDay(day(ago), v, round(v) - 19, round(v) + 55, hours, sample_count=120)
        for ago, (v, hours) in _HEART.items()
    ]
    # 오늘 진행 중인 걸음 수·심박. 오늘 아침 수면 기록은 동기화 전이다. 오늘은 아직 운동하지 않아 운동 0분이다
    steps.append(StepDay(today, _TODAY_STEPS))
    heart.append(HeartDay(today, 74.0, 58, 112, 9, sample_count=90))

    workouts = []
    for ago, items in _WORKOUTS.items():
        for code, hh, mm, minutes, counted, kcal, distance, (hr_avg, hr_max), speed in items:
            start = at(day(ago), hh, mm)
            name, category = _CODES.get(code, (None, None))
            workouts.append(
                ExerciseSession(
                    day(ago), start, start + dt.timedelta(minutes=minutes), minutes, code, name, category, counted,
                    calories_kcal=kcal, distance_m=distance, avg_heart_rate=hr_avg, max_heart_rate=hr_max,
                    avg_speed_mps=speed,
                )
            )  # fmt: skip

    as_of = local_now - dt.timedelta(minutes=7)
    # 적재·집계가 마지막으로 성공한 시각. 점수도 이때 계산했다
    success = local_now - dt.timedelta(minutes=5)
    sleep_as_of = at(day(1), 7, 25)
    # 운동 기준 시각은 마지막 운동이 끝난 시각이라 오늘 운동이 없으면 이전 날이다
    exercise_as_of = max(w.end_at for w in workouts)

    counted = {ago: sum(item[3] for item in items if item[4]) for ago, items in _WORKOUTS.items()}
    scores = []
    for ago, (total, activity_area, sleep_area, heart_area, base, nights) in _SCORES.items():
        activity, activity_grade, steps_score, exercise_score = activity_area
        night = _NIGHTS.get(ago)
        scores.append(
            ScoreDay(
                date=day(ago), total_score=total, activity_score=activity, activity_grade=activity_grade,
                steps_score=steps_score, exercise_score=exercise_score, sleep_score=sleep_area[0],
                sleep_grade=sleep_area[1], heart_score=heart_area[0], heart_grade=heart_area[1],
                steps=_TODAY_STEPS if ago == 0 else _STEPS.get(ago),
                exercise_minutes=None if activity is None else counted.get(ago, 0),
                sleep_minutes=None if night is None else night[5],
                sleep_hr_avg=_SLEEP_HEART[ago][0] if ago in _SLEEP_HEART else None,
                hr_baseline=base, hr_baseline_nights=nights, computed_at=success,
            )
        )  # fmt: skip
    code, title, description = _TODAY_MESSAGE
    # 기준 시각은 지표마다 동기화 상태의 마지막 기록 시각이다
    message = DailyMessageDay(
        date=today, status="READY", code=code, area="activity", title=title, description=description,
        used_metrics=("steps",), data_as_of={"steps": as_of, "exercises": exercise_as_of, "sleeps": sleep_as_of},
    )  # fmt: skip
    port = InMemoryHealthData(
        steps={user_id: steps},
        heart={user_id: heart},
        sleep={user_id: sessions},
        exercise={user_id: workouts},
        scores={user_id: scores},
        daily_messages={user_id: [message]},
        sync={
            user_id: {
                "steps": SyncState(as_of, "success", success),
                "heart_rate": SyncState(as_of, "success", success),
                "sleep": SyncState(sleep_as_of, "success", success),
                "oxygen_saturation": SyncState(sleep_as_of, "success", success),
                "exercise": SyncState(exercise_as_of, "success", success),
            }
        },
        # 14일 전부터 연동해 지난 30일을 물으면 그 앞 날짜는 연동 전으로 빠진다
        linked_from={user_id: day(DAYS - 1)},
        now=now,
    )
    if include_exercise_activity:
        rows = []
        for step in steps:
            found = [item for item in workouts if item.date == step.date]
            counted = [item for item in found if item.counts_as_exercise]
            calories = [Decimal(str(item.calories_kcal)) for item in found if item.calories_kcal is not None]
            distances = [Decimal(str(item.distance_m)) for item in found if item.distance_m is not None]
            rows.append(
                ExerciseActivityDay(
                    step.date,
                    sum(item.duration_minutes for item in counted),
                    len(counted),
                    sum(calories) if calories else None,
                    sum(distances) if distances else None,
                    success,
                )
            )
        port.exercise_activity[user_id] = rows
        port.exercise_activity_statuses[user_id] = {
            name: {
                "ready": True,
                "last_sync_status": "success",
                "last_data_at": as_of.isoformat(),
                "last_success_at": success.isoformat(),
            }
            for name in ("exercise_sessions", "calories_burned", "distances")
        }
    return port
