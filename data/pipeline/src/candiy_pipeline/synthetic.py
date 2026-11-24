"""개발·시연용 합성 건강데이터 (S15P31A403-46).

실데이터는 개발용 AI 도구에 넣을 수 없어서, 실데이터 특성만 흉내 낸 가짜 사람 10명을 만든다.
CANDiY API와 같은 형식의 레코드를 만들어 지금 파이프라인(적재 → 걸음 버전 → 집계)을 그대로 태운다.
그래서 이상치·나뉜 수면·시간대별 걸음 규칙이 실데이터와 똑같이 적용된다.

사용법 (data/pipeline 에서, 로컬 DB를 띄운 뒤):
    APP_ENV를 local 또는 dev로 명시해야 한다. --member는 PIPELINE_INCLUDE_MEMBERS=false도 필요하다.
    uv run python -m candiy_pipeline.synthetic --reset         # 10명 모두 지우고 최근 90일 다시 (09:00 갱신)
    uv run python -m candiy_pipeline.synthetic                 # 오늘·어제만 다시 (15분 갱신. 처음이면 90일)
    uv run python -m candiy_pipeline.synthetic --today 2026-10-22 --reset --classic   # 골든셋용 예전 방식
    uv run python -m candiy_pipeline.synthetic --people synth-01 synth-03
    uv run python -m candiy_pipeline.synthetic --member <users.id> --people synth-02
    uv run python -m candiy_pipeline.synthetic --people synth-11        # 발표 시연용 (기본 실행에는 없음)

반영한 실데이터 특성 (data/docs/candiy-api.md 6장)
- 걸음: 하루 누적 1건. 동기화(약 15~20분)마다 누적값이 갱신된 버전이 따로 온다
- 칼로리·거리: 운동 중에만 온다
- 산소포화도: 수면 중에만 측정된다
- 심박: 운동·수면 중 연속 측정(샘플 여러 개), 평소에는 30분마다 샘플 1개
- 수면: 단계 목록이 있고, 가끔 새벽에 깨서 둘로 나뉜다
- 같은 시드라 누가 언제 실행해도 같은 날짜에는 같은 값이 나온다
- 사람다운 흐트러짐 (S15P31A403-235): 가끔 늦게 자고, 운동을 쉬는 주·컨디션 나쁜 날·주말 나들이가 있다.
  별도 난수로 만들어 --classic(35일, 흐트러짐 없음)은 예전과 같은 값이 나온다. 골든셋 고정 데이터는 --classic으로 만든다

users 테이블이 있으면 10명을 회원으로도 넣는다 (id = 건강 테이블 end_user_id). 실제 소셜 계정이 없는 개발용 회원이라
로그인은 할 수 없고, social_user_id가 "synthetic-"으로 시작해 평소 파이프라인의 CANDiY 조회에서 빠진다 (targets.py).
"""

import argparse
import json
import logging
import random
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

import psycopg

from .aggregator import AGGREGATE_TABLES, aggregate_and_mark_ready, dates_to_aggregate
from .config import load_settings
from .loader import sync_metric
from .metrics import METRICS, NO_END_PARAM
from .records import KST
from .rules import day_start
from .sync import member_lock
from .targets import SYNTHETIC_SOCIAL_PREFIX, Target, target_for_test_user
from .windows import INITIAL_DAYS

# 개발 서버 합성 데이터 기간과 15분 갱신 범위 (S15P31A403-235). 실데이터 첫 연동은 INITIAL_DAYS(35일) 그대로
SYNTHETIC_DAYS = 90
REFRESH_DAYS = 2  # 오늘·어제. 어젯밤 수면과 자정 직후 늦게 동기화된 어제 기록까지 (sync.SYNC_MEMBER_DAYS와 같음)

log = logging.getLogger("candiy_pipeline.synthetic")

APP = "com.sec.android.app.shealth"  # 실제 출처와 같은 삼성 헬스
DATA_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://candiy-fit/synthetic")


@dataclass(frozen=True)
class Workout:
    code: int  # Health Connect 운동 코드 (exercise_code)
    start_hour: float  # KST. 19.5 = 19:30
    minutes: int
    hr: int  # 운동 중 평균 심박
    kcal_per_min: float
    speed_kmh: float | None = None  # 걷기·달리기·자전거·등산만. 없으면 속도·거리 기록이 없다
    cadence: int = 0  # 분당 걸음 (걷기·달리기·등산). 그날 걸음에 더한다


@dataclass(frozen=True)
class Sleep:
    bed_hour: float  # 깬 날 0시 기준. -1 = 전날 23시, 2 = 그날 2시, 9 = 그날 9시
    hours: float  # 실제 잔 시간 평균


@dataclass(frozen=True)
class PinnedNight:
    """정한 날짜에 깬 밤을 정한 값 그대로 만든다 (광고 영상용 synth-13, S15P31A403-410).

    deep 이 있으면 잠든 시각~깬 시각·잔 시간·깊은 잠을 분 단위 그대로 만든다 (wake 필요).
    deep 이 없으면 잠든 시각과 잔 시간만 정하고 단계는 평소처럼 만든다 (평균 맞추기용).
    """

    bed: float  # 깬 날 0시 기준 시간. 1 + 44 / 60 = 01:44, -1 = 전날 23시
    asleep: int  # 잔 시간 (분, 깸 제외)
    deep: int | None = None  # 깊은 잠 (분)
    wake: float | None = None  # 깬 시각 (깬 날 0시 기준 시간)
    sleep_hr: float | None = None  # 수면 중 평균 심박 (없으면 평소)


@dataclass(frozen=True)
class Persona:
    user_id: str
    name: str
    steps: tuple[int, int]  # (평일, 주말) 운동 말고 평소에 걷는 걸음
    step_hours: tuple[int, ...]  # 평소 걸음이 생기는 시간대 (KST)
    sleep: tuple[Sleep, Sleep]  # (평일, 주말). 깬 날의 요일 기준
    workouts: dict[int, tuple[Workout, ...]]  # 요일(월 0 ~ 일 6) → 운동
    sleep_hr: float  # 수면 중 평균 심박
    recent_hr_shift: float = 0.0  # 최근 7일 수면 중 심박 변화 (심박 감점 확인용)
    wear_rate: float = 1.0  # 워치를 찬 날 비율. 안 찬 날은 수면·심박·운동이 없다 (걸음은 폰이 센다)
    split_sleep_rate: float = 0.0  # 새벽에 깨서 수면이 둘로 나뉘는 비율
    sync_minutes: int = 18  # 폰 동기화 간격 (상시 연결 실데이터 중앙값 18분)
    looseness: float = 1.0  # 사람다운 흐트러짐 정도 (0 없음 ~ 1 보통). 모범형은 작게
    unsynced_days: int = 0  # 최근 며칠 폰이 동기화되지 않음
    body: bool = False  # 일요일 아침 체성분 측정
    body_values: tuple[float, float, float, float] = (
        72.0,
        12.0,
        1750.0,
        1.78,
    )  # 체중 kg, 체지방 %, 기초대사 kcal, 키 m
    special_days: dict[int, str] = field(default_factory=dict)  # 며칠 전 → 경계 상황 (SPECIAL_KINDS)
    arc: str | None = None  # 기준일까지 몇 주에 걸쳐 바뀌는 이야기 (ARC_KINDS, 시연용)
    pinned_nights: dict[date, PinnedNight] = field(default_factory=dict)  # 깬 날 → 정한 밤 (광고용)


# 운동 코드: 5 농구, 8 자전거, 37 등산, 56 달리기, 70 근력 운동, 79 걷기
BRISK_WALK = Workout(79, 18.5, 35, hr=112, kcal_per_min=5.0, speed_kmh=5.6, cadence=115)
RUN = Workout(56, 18.5, 30, hr=148, kcal_per_min=10.0, speed_kmh=9.5, cadence=160)
GYM = Workout(70, 19.5, 60, hr=118, kcal_per_min=6.5)
LONG_RUN = Workout(56, 9, 45, hr=150, kcal_per_min=10.5, speed_kmh=9.8, cadence=162)
NIGHT_GYM = Workout(70, 17.5, 50, hr=120, kcal_per_min=6.5)
EARLY_GYM = Workout(70, 18.5, 60, hr=118, kcal_per_min=6.5)  # 퇴근 뒤 바로. 19:30쯤 끝나 늦은 운동이 아니다 (11.4)
# 토요일 오전 등산 (시연 synth-11)
WEEKEND_HIKE = Workout(37, 9, 150, hr=125, kcal_per_min=6.5, speed_kmh=3.0, cadence=95)
# 시연 synth-12: 평일 저녁 7시 헬스(이야기에서 밤 9시 반으로 옮긴다), 주말 오전 헬스
GYM_19 = Workout(70, 19, 70, hr=125, kcal_per_min=7.0)
DAY_GYM = Workout(70, 10.5, 80, hr=125, kcal_per_min=7.0)
SCHOOL_PE = Workout(5, 14, 45, hr=145, kcal_per_min=8.0)  # 체육 시간 농구

PERSONAS = (
    Persona(
        "synth-01",
        "모범형",
        steps=(5500, 6500),
        step_hours=(7, 8, 12, 13, 18, 19, 20, 21),
        sleep=(Sleep(-1.0, 7.5), Sleep(-0.5, 7.8)),
        workouts={0: (BRISK_WALK,), 1: (RUN,), 2: (BRISK_WALK,), 3: (RUN,), 5: (LONG_RUN,)},
        sleep_hr=56,
        looseness=0.5,  # 대체로 성실하지만 가끔 쉬고 늦게 잔다
    ),
    Persona(
        "synth-02",
        "직장인 평균",
        steps=(5500, 5000),
        step_hours=(8, 9, 12, 13, 18, 19, 20, 22),
        sleep=(Sleep(-0.5, 6.5), Sleep(0.5, 7.5)),
        workouts={1: (GYM,), 3: (GYM,)},
        sleep_hr=60,
        split_sleep_rate=0.15,
    ),
    Persona(
        "synth-03",
        "수면 부족",
        steps=(4500, 4000),
        step_hours=(9, 10, 13, 14, 19, 20, 21, 22, 23),
        sleep=(Sleep(2.0, 4.8), Sleep(2.5, 6.0)),
        workouts={5: (Workout(79, 15, 30, 105, 4.5, 4.8, 110),)},
        sleep_hr=62,
        recent_hr_shift=7,
        split_sleep_rate=0.2,
    ),
    Persona(
        "synth-04",
        "운동 안 함",
        steps=(1800, 2000),
        step_hours=(9, 12, 13, 18, 19, 20),
        sleep=(Sleep(-0.5, 7.0), Sleep(0.0, 7.5)),
        # 출퇴근 길이 걷기로 자동 인식되지만 느려서 운동 점수에는 들어가지 않는다 (score-rules.md 2.3)
        workouts=dict.fromkeys(range(5), (Workout(79, 8.2, 14, 92, 3.0, 3.2, 85),)),
        sleep_hr=64,
    ),
    Persona(
        "synth-05",
        "운동선수",
        steps=(7000, 6000),
        step_hours=tuple(range(7, 22)),
        sleep=(Sleep(-1.5, 8.0), Sleep(-1.0, 8.5)),
        workouts={
            0: (Workout(56, 8, 70, 150, 13.0, 12.0, 170),),
            1: (Workout(8, 9, 90, 138, 10.0, 28.0),),
            2: (Workout(56, 8, 60, 152, 13.0, 12.5, 172), Workout(70, 17, 60, 120, 7.0)),
            3: (Workout(56, 8, 70, 148, 13.0, 11.5, 168),),
            4: (Workout(70, 17, 60, 122, 7.0), Workout(56, 8, 40, 145, 12.0, 11.0, 166)),
            5: (Workout(56, 9, 100, 155, 13.5, 12.0, 170),),
            6: (Workout(8, 9, 120, 135, 10.0, 26.0),),
        },
        sleep_hr=47,
        body=True,
    ),
    Persona(
        "synth-06",
        "주말 몰아 운동",
        steps=(3500, 4000),
        step_hours=(8, 12, 13, 18, 19, 21),
        sleep=(Sleep(-0.5, 7.0), Sleep(-0.5, 7.0)),
        workouts={
            5: (Workout(37, 8.5, 180, 122, 7.0, 3.5, 95),),
            6: (Workout(8, 9, 120, 132, 8.5, 20.0),),
        },
        sleep_hr=58,
    ),
    Persona(
        "synth-07",
        "주말 몰아 잠",
        steps=(6000, 4000),
        step_hours=(8, 9, 12, 13, 18, 19, 20, 22),
        sleep=(Sleep(0.5, 5.5), Sleep(1.0, 10.5)),
        workouts={2: (Workout(56, 20, 30, 150, 10.0, 9.0, 160),)},
        sleep_hr=61,
    ),
    Persona(
        "synth-08",
        "야간 근무",
        steps=(6000, 5000),
        step_hours=(0, 1, 2, 3, 6, 7, 18, 19, 20, 22, 23),
        sleep=(Sleep(9.0, 6.8), Sleep(9.0, 7.5)),
        workouts={0: (NIGHT_GYM,), 3: (NIGHT_GYM,)},
        sleep_hr=59,
    ),
    Persona(
        "synth-09",
        "워치 자주 안 참 (연동 끊김)",
        steps=(5500, 5000),
        step_hours=(8, 9, 12, 13, 18, 19, 20, 22),
        sleep=(Sleep(-0.5, 7.0), Sleep(0.0, 7.5)),
        workouts={2: (Workout(79, 19, 40, 110, 4.5, 5.2, 112),)},
        sleep_hr=60,
        wear_rate=0.6,
        sync_minutes=84,  # 필요할 때만 연결한 실데이터 계정의 간격 (candiy-api.md 6.7)
        unsynced_days=2,
    ),
    Persona(
        "synth-10",
        "고등학생 평균",
        steps=(7000, 5000),
        step_hours=(7, 8, 12, 13, 16, 17, 18, 21),
        sleep=(Sleep(0.5, 5.8), Sleep(1.5, 8.5)),
        workouts={1: (SCHOOL_PE,), 3: (SCHOOL_PE,)},
        sleep_hr=58,
    ),
)
# 코칭 골든셋용 경계 상황 (S15P31A403-163). 기본 10명에는 없는 날을 정해진 날짜에 일부러 만든다.
#   no_record        폰·워치 모두 기록 없음 → 그날 행 없음 (NO_RECORD)
#   zero_steps       걸음 기록이 0보 하나 → ZERO_STEPS
#   null_steps       운동은 했는데 걸음 기록이 없음 → 활동 행은 있고 걸음만 NULL (NULL_VALUE)
#   nap_only         밤잠 없이 오후 쪽잠만 → 쪽잠이 그날 주 수면 (calculation-rules.md 3장)
#   slow_walk_only   느린 걷기만 → 운동 시간에서 빠져 운동 0분 (score-rules.md 2.3)
#   unknown_exercise 운동 기준표에 없는 코드 → 종류 이름 없음
#   low_spo2         그날 깬 수면 중 산소포화도 90% 미만 측정 (calculation-rules.md 7.5)
SPECIAL_KINDS = frozenset(
    {"no_record", "zero_steps", "null_steps", "nap_only", "slow_walk_only", "unknown_exercise", "low_spo2"}
)
SLOW_WALK = Workout(79, 12.5, 40, hr=95, kcal_per_min=3.0, speed_kmh=3.0, cadence=80)
UNKNOWN_CODE = 9999  # Health Connect에 없는 코드
UNKNOWN_WORKOUT = Workout(UNKNOWN_CODE, 19, 40, hr=115, kcal_per_min=5.5)

# 기본 실행(--people 없음)과 개발 서버 매일 갱신에는 들어가지 않는다. --people synth-edge로만 넣는다
EDGE_PERSONAS = (
    Persona(
        "synth-edge",
        "경계 상황 (골든셋용)",
        steps=(5500, 5000),
        step_hours=(8, 9, 12, 13, 18, 19, 20, 22),
        sleep=(Sleep(-0.5, 6.8), Sleep(0.0, 7.5)),
        workouts={1: (GYM,), 3: (GYM,)},
        sleep_hr=60,
        # 기준일 2026-10-22(목) 기준: 0 = 10-22 … 5 = 10-17(토), 8·9 = 10-14·10-13
        special_days={
            0: "low_spo2",
            1: "slow_walk_only",
            2: "null_steps",
            3: "zero_steps",
            4: "nap_only",
            5: "unknown_exercise",
            8: "no_record",
            9: "no_record",
        },
    ),
)
# 발표 시연용 (S15P31A403-304). 기본 실행·매일 갱신·골든셋에는 들어가지 않는다.
# --people synth-11로 넣거나, 시연 계정에 연결한다 (--member, PIPELINE_SYNTHETIC_MEMBERS)
DEMO_PERSONAS = (
    Persona(
        "synth-11",
        "야근 늘며 수면 악화 (시연용)",
        steps=(6000, 5500),
        step_hours=(8, 9, 12, 13, 18, 19, 20, 22),
        sleep=(Sleep(-0.5, 7.0), Sleep(0.0, 7.6)),
        workouts={0: (EARLY_GYM,), 2: (EARLY_GYM,), 4: (EARLY_GYM,), 5: (WEEKEND_HIKE,)},
        sleep_hr=60,
        split_sleep_rate=0.1,
        looseness=0.6,
        body=True,
        body_values=(52.0, 27.0, 1230.0, 1.63),
        arc="overwork",
    ),
    Persona(
        "synth-12",
        "밤 운동으로 깊은 잠 감소 (시연용)",
        steps=(9000, 10000),
        step_hours=(8, 9, 12, 13, 18, 19, 20),
        sleep=(Sleep(-0.5, 7.3), Sleep(-0.3, 7.6)),
        workouts={
            **dict.fromkeys(range(5), (GYM_19,)),
            5: (DAY_GYM,),
            6: (DAY_GYM,),
        },
        sleep_hr=56,
        looseness=0.3,
        arc="late_gym",
    ),
    # 광고 영상 「캔디 탐정 사무소: 피로 사건」 녹화용 (S15P31A403-410). 대사의 수치와 맞춘다.
    # synth-11 과 같은 생활이지만 수면 중 심박 상승·산소포화도 저하가 없어 상담 권유가 붙지 않는다.
    # 날짜를 정해 둔 밤이 있어 --today 2026-11-16 으로만 넣는다 (매일 갱신 목록에 넣지 않는다)
    Persona(
        "synth-13",
        "광고 영상용 (2026-11-16 녹화)",
        steps=(6000, 5500),
        step_hours=(8, 9, 12, 13, 18, 19, 20, 22),
        sleep=(Sleep(-0.5, 7.0), Sleep(0.0, 7.6)),
        workouts={0: (EARLY_GYM,), 2: (EARLY_GYM,), 4: (EARLY_GYM,), 5: (WEEKEND_HIKE,)},
        sleep_hr=60,
        looseness=0.6,
        body=True,
        body_values=(52.0, 27.0, 1230.0, 1.63),
        arc="ad",
        pinned_nights={
            # 단서 1: 잠든 시각 01:44, 5시간 29분, 깊은 잠 33분
            date(2026, 11, 6): PinnedNight(1 + 44 / 60, 329, deep=33, wake=7 + 22 / 60),
            # 단서 3: 화요일(11-10) 밤. 이 하룻밤만 수면 중 심박이 높고 깊은 잠이 짧다
            date(2026, 11, 11): PinnedNight(56 / 60, 345, deep=20, wake=6 + 52 / 60, sleep_hr=70),
            # 단서 2: 최근 28일 운동한 날 밤 평균 444분(7시간 24분), 안 한 날 밤 371분(6시간 11분).
            # 28일 양 끝에 드나드는 밤(10-19·10-20·10-21·11-16)을 각 평균과 같게 두어,
            # 질문한 날이 11-15·11-16·11-17 어느 날이어도 평균이 같다
            date(2026, 10, 19): PinnedNight(14 / 60, 371),  # 안 한 날 밤
            date(2026, 10, 20): PinnedNight(-21 / 60, 444),  # 운동한 날 밤
            date(2026, 10, 21): PinnedNight(-7 / 60, 371),  # 안 한 날 밤
            date(2026, 11, 3): PinnedNight(29 / 60, 382),  # 운동한 날 밤
            date(2026, 11, 14): PinnedNight(48 / 60, 401),  # 안 한 날 밤
            date(2026, 11, 16): PinnedNight(42 / 60, 371),  # 안 한 날 밤
        },
    ),
)
PERSONA_BY_ID = {p.user_id: p for p in PERSONAS + EDGE_PERSONAS + DEMO_PERSONAS}

# ----- 시연 이야기 (arc) -----
#   overwork  기준일 기준 2주 전부터 야근으로 잠이 줄고(5~6시간, 늦고 들쭉날쭉), 1주 전부터 더 나빠진다
#             (5시간대, 깸 증가, 깊은잠 감소, 수면 중 심박 상승). 수면 중 산소포화도 90% 미만 기록이
#             2주 전부터 생기고 최근 1주에 반복된다(새벽 3~5시, 주말에 길게 잔 밤에도). 체중은 6주에 걸쳐 2kg 는다.
#             야근 전은 평소라 본인 기준 28일(11장, 분석 14일 바로 전)이 평소 구간이다. 평소엔 월·수·금 저녁 헬스와
#             토 오전 등산을 하고, 헬스는 잠들기 3시간보다 일찍 끝난다(늦은 운동 아님). 야근 뒤로는 운동을 하지 않는다.
#             산소포화도 기록은 실데이터처럼 밤마다 1~2개다 (calculation-rules.md 7.5 "기록 빈도", S15P31A403-303)
#             수면 부족과 산소포화도 저하는 따로 만든 두 신호다 (하나가 다른 하나의 원인처럼 만들지 않는다)
#   late_gym  매일 운동하고 잠드는 시각이 일정한 사람. 2주 전부터 평일 운동을 밤 9시 반으로 옮겨(잠들기 3시간 안에 끝남)
#             그다음 밤 깊은 잠이 준다. 수면 시간·잠드는 시각·산소포화도·수면 중 심박은 평소와 같다.
#             주말 운동은 오전이라 그날 밤은 평소처럼 잔다. 생활 요인 비교 28일 안에 늦은 운동·이른 운동이
#             모두 4밤 이상 있다 (S15P31A403-385)
#   ad        광고 영상용 synth-13. overwork 와 같은데 수면 중 심박 상승·산소포화도 저하가 없다 (S15P31A403-410)
#   운동 다음 밤 (_after_workout): overwork·ad 는 운동한 다음 밤 깊은 잠이 늘고 덜 깨고 수면 중 심박이 낮다.
#             ad 는 더 오래 잔다. late_gym 은 밤 9시 뒤에 끝난 운동 다음 밤만 깊은 잠이 준다
ARC_KINDS = frozenset({"overwork", "late_gym", "ad"})
AD_AFTER_WORKOUT_HOURS = 0.0  # ad: 운동한 다음 밤 더 자는 시간
ARC_STORY_DAYS = 42  # 이야기 시작 (6주 전). 그 전은 평소
ARC_BUSY_DAYS = 14  # 야근 시작 (2주 전)
ARC_WORSE_DAYS = 7  # 악화 (1주 전)
ARC_WEIGHT_GAIN = 2.0  # kg
ARC_LATE_GYM_DAYS = 14  # late_gym: 평일 운동을 밤으로 옮긴 날 (2주 전)
ARC_LATE_GYM_SHIFT = 2.5  # late_gym: 저녁 7시 → 밤 9시 반
LATE_WORKOUT_END_HOUR = 21  # 이 시각(KST) 뒤에 끝난 운동은 늦은 운동으로 본다 (잠들기 3시간 안)


@dataclass(frozen=True)
class ArcDay:
    """이야기에서 그날 달라지는 것. 기본값이면 평소와 같다."""

    bed_shift: float = 0.0  # 잠드는 시각 (시간)
    hours_shift: float = 0.0  # 잔 시간 (시간)
    bed_spread: float = 0.0  # 잠드는 시각 흔들림 (표준편차, 시간)
    skip_workout: float = 0.0  # 운동을 거를 확률
    step_factor: float = 1.0
    deep_factor: float = 1.0  # 깊은 잠 길이 배율
    wake_prob: float = 0.4  # 주기 사이에 깰 확률 (_plan_stages 기본값과 같음)
    wake_scale: float = 1.0  # 깬 시간 길이 배율
    sleep_hr_shift: float = 0.0  # 수면 중 심박 (bpm)
    low_spo2: int = 0  # 그날 깬 수면 중 90% 미만 기록 수 (0~2)
    weight_delta: float = 0.0  # 체중 (kg)
    workout_shift: float = 0.0  # 운동 시작 시각 (시간)


def _arc_day(persona: Persona, d: date, today: date) -> ArcDay | None:
    if persona.arc is None:
        return None
    if persona.arc == "late_gym":
        ago = (today - d).days
        return ArcDay(workout_shift=ARC_LATE_GYM_SHIFT) if ago < ARC_LATE_GYM_DAYS and d.weekday() < 5 else ArcDay()
    day = _overwork_day(persona, d, today)
    return replace(day, sleep_hr_shift=0.0, low_spo2=0) if persona.arc == "ad" else day


def _overwork_day(persona: Persona, d: date, today: date) -> ArcDay:
    rng = _rng(persona, d, "arc")
    ago = (today - d).days
    weekend = d.weekday() >= 5  # 깬 날 기준 (토·일 아침)
    weight = ARC_WEIGHT_GAIN * min(1.0, max(0.0, (ARC_STORY_DAYS - ago) / ARC_STORY_DAYS))
    if ago >= ARC_BUSY_DAYS:
        # 평소: 90% 미만은 아주 드물게 한 번
        return ArcDay(low_spo2=1 if rng.random() < 0.03 else 0, weight_delta=weight)
    if ago >= ARC_WORSE_DAYS:
        # 야근 시작: 평일 5~6시간, 늦고 들쭉날쭉. 90% 미만 밤이 생기기 시작한다
        low = 1 if rng.random() < 0.2 else 0
        if weekend:
            return ArcDay(0.8, 0.0, 0.5, 1.0, 0.85, 1.0, 0.5, 1.0, 2.0, low, weight)
        return ArcDay(1.2, -1.3, 0.7, 1.0, 0.75, 1.0, 0.5, 1.0, 2.0, low, weight)
    # 악화: 평일 5시간대, 깸이 늘고 깊은 잠이 준다. 주말엔 길게 자는데도 90% 미만이 있다
    if weekend:
        return ArcDay(0.5, 0.8, 0.5, 1.0, 0.7, 0.6, 0.7, 1.8, 5.0, rng.randint(1, 2), weight)
    low = rng.randint(1, 2) if rng.random() < 0.5 else 0
    return ArcDay(1.6, -1.9, 0.8, 1.0, 0.7, 0.6, 0.7, 1.8, 5.0, low, weight)


# ----- 하루 계획 -----


@dataclass
class Night:
    stages: list[tuple[datetime, datetime, int]]  # 조각 하나의 (시작, 끝, 단계)

    @property
    def start(self) -> datetime:
        return self.stages[0][0]

    @property
    def end(self) -> datetime:
        return self.stages[-1][1]


@dataclass
class DayPlan:
    worn: bool
    sleeps: list[Night] = field(default_factory=list)  # 그날 깬 수면 조각 (나뉘면 둘)
    workouts: list[tuple[Workout, datetime, datetime]] = field(default_factory=list)
    step_factor: float = 1.0  # 평소 걸음 배율 (사람다운 흐트러짐. 기본 1.0이면 예전과 같다)


@dataclass(frozen=True)
class HumanDay:
    """그날의 사람다운 흐트러짐 (S15P31A403-235). 예전 생성 난수와 따로 뽑아 --classic 결과를 바꾸지 않는다."""

    late_hours: float = 0.0  # 전날 밤 늦게 잔 시간 (0이면 평소대로)
    bed_jitter: float = 0.0  # 평소 잠드는 시각 흔들림에 더하는 값 (시간)
    busy_week: bool = False  # 운동을 쉬는 주 (월~일 같은 값)
    sick: bool = False  # 컨디션 나쁜 날
    step_factor: float = 1.0


def _human_day(persona: Persona, d: date) -> HumanDay:
    loose = persona.looseness
    rng = _rng(persona, d, "human")
    busy_week = _rng(persona, d - timedelta(days=d.weekday()), "busy-week").random() < 0.25 * loose
    sick = rng.random() < 0.04 * loose
    # 금·토 밤(깬 날 토·일)에 더 자주 늦게 잔다
    late = rng.random() < (0.2 if d.weekday() >= 5 else 0.07) * loose
    late_hours = rng.uniform(1.5, 3.0) if late else 0.0
    bed_jitter = rng.gauss(0, 0.45) * loose
    outing = rng.random()
    step_factor = 1.0
    if sick:
        step_factor = 0.35
    elif busy_week:
        step_factor = 0.8
    elif d.weekday() >= 5 and outing < 0.2 * loose:
        step_factor = 1.5  # 주말 나들이
    elif d.weekday() >= 5 and outing < 0.35 * loose:
        step_factor = 0.55  # 주말에 집에만
    return HumanDay(late_hours, bed_jitter, busy_week, sick, step_factor)


def _rng(persona: Persona, d: date, what: str) -> random.Random:
    # 날짜별 시드라 기간이 바뀌어도 같은 날짜에는 같은 값이 나온다
    return random.Random(f"{persona.user_id}/{d.isoformat()}/{what}")


def _at(d: date, hour: float) -> datetime:
    return day_start(d) + timedelta(hours=hour)


def _plan_stages(
    rng: random.Random,
    bed: datetime,
    asleep_minutes: float,
    *,
    deep_factor: float = 1.0,
    wake_prob: float = 0.4,
    wake_scale: float = 1.0,
) -> list[tuple[datetime, datetime, int]]:
    """잠들기 전 깨어 있음 → (얕은 잠, 깊은 잠, 얕은 잠, REM) 주기 반복. 주기 사이 가끔 깸.

    deep_factor·wake_prob·wake_scale은 시연 이야기(arc)만 바꾼다. 기본값이면 난수 순서와 값이 예전과 같다.
    """
    stages = []
    t = bed

    def add(stage: int, minutes: float) -> None:
        nonlocal t
        end = t + timedelta(minutes=round(minutes))
        if end > t:
            stages.append((t, end, stage))
        t = end

    add(1, rng.uniform(3, 12))
    asleep_minutes = round(asleep_minutes)
    asleep = 0
    cycle = 0
    while asleep < asleep_minutes:
        # 깊은 잠은 앞쪽 주기에 많고 REM은 뒤쪽 주기에 많다
        deep = (rng.uniform(15, 30) if cycle < 2 else rng.uniform(0, 10)) * deep_factor
        rem = rng.uniform(10, 20) + 5 * cycle
        for stage, minutes in ((4, rng.uniform(25, 45)), (5, deep), (4, rng.uniform(10, 20)), (6, rem)):
            minutes = round(min(minutes, asleep_minutes - asleep))
            if minutes < 1:
                continue
            add(stage, minutes)
            asleep += minutes
        if asleep < asleep_minutes and rng.random() < wake_prob:
            add(1, rng.uniform(2, 8) * wake_scale)
        cycle += 1
    return stages


def _split(total: int, weights: tuple[float, ...]) -> list[int]:
    """total 분을 비율대로 나눈다. 반올림 나머지는 마지막에 더해 합이 total 과 같다."""
    parts = [round(total * w / sum(weights)) for w in weights[:-1]]
    return [*parts, total - sum(parts)]


def _pinned_stages(pin: PinnedNight, d: date) -> list[tuple[datetime, datetime, int]]:
    """잠든 시각~깬 시각을 분 단위 그대로, 잔 시간·깊은 잠을 정한 값 그대로 만든다 (주기 4번).

    깸은 잠들기 전과 가운데에 나눠 둔다. REM 은 잔 시간의 22%, 나머지는 얕은 잠이다.
    """
    assert pin.deep is not None and pin.wake is not None
    start, end = _at(d, pin.bed).replace(second=0), _at(d, pin.wake).replace(second=0)
    awake = round((end - start).total_seconds() / 60) - pin.asleep
    if awake < 0 or pin.deep > pin.asleep:
        raise ValueError(f"{d} 정한 밤의 시간이 맞지 않습니다 (잔 시간·깊은 잠이 잠든~깬 시각보다 김)")
    rem = round(pin.asleep * 0.22)
    deeps = _split(pin.deep, (0.45, 0.35, 0.15, 0.05))
    rems = _split(rem, (0.15, 0.22, 0.28, 0.35))
    lights = _split(pin.asleep - pin.deep - rem, (1,) * 8)
    awakes = _split(awake, (1, 1))
    pieces = [(1, awakes[0])]
    for c in range(4):
        pieces += [(4, lights[2 * c]), (5, deeps[c]), (4, lights[2 * c + 1]), (6, rems[c])]
        if c == 1:
            pieces.append((1, awakes[1]))
    stages, t = [], start
    for stage, minutes in pieces:
        if minutes > 0:
            stages.append((t, t + timedelta(minutes=minutes), stage))
            t += timedelta(minutes=minutes)
    return stages


def _after_workout(persona: Persona, arc: ArcDay | None, prev: DayPlan | None) -> ArcDay | None:
    """운동한 다음 밤의 수면 (시연 이야기만). 운동은 저녁에 하고 그 밤 잠은 다음 날 깬 수면이다."""
    if arc is None or prev is None or not prev.workouts:
        return arc
    if persona.arc in ("overwork", "ad"):
        return replace(
            arc,
            deep_factor=arc.deep_factor * 1.35,
            wake_prob=arc.wake_prob * 0.5,
            sleep_hr_shift=arc.sleep_hr_shift - 2,
            hours_shift=arc.hours_shift + (AD_AFTER_WORKOUT_HOURS if persona.arc == "ad" else 0.0),
        )
    if persona.arc == "late_gym" and max(end for _, _, end in prev.workouts).hour >= LATE_WORKOUT_END_HOUR:
        return replace(arc, deep_factor=arc.deep_factor * 0.5)
    return arc


def _plan_day(
    persona: Persona, d: date, special: str | None = None, human: bool = False, arc: ArcDay | None = None
) -> DayPlan:
    rng = _rng(persona, d, "plan")
    plan = DayPlan(worn=rng.random() < persona.wear_rate)
    # 경계 페르소나(special_days)는 정해 둔 상황만 보여야 해서 흐트러짐을 넣지 않는다
    h = _human_day(persona, d) if human and not persona.special_days else HumanDay()
    # 시연 이야기는 따로 뽑은 난수만 쓴다 (다른 페르소나 값이 바뀌지 않게)
    arc_rng = _rng(persona, d, "arc-plan") if arc else None
    plan.step_factor = h.step_factor * (arc.step_factor if arc else 1.0)
    if special in ("no_record", "zero_steps"):
        plan.worn = False
    if not plan.worn:
        return plan
    if special == "nap_only":
        # 밤잠 없이 오후 2시쯤 1시간 반 쪽잠만 잔다
        plan.sleeps = [Night(_plan_stages(rng, _at(d, 14 + rng.gauss(0, 0.3)), 90))]
        return plan

    s = persona.sleep[1 if d.weekday() >= 5 else 0]
    bed_hour = s.bed_hour + rng.gauss(0, 0.3)
    hours = s.hours + rng.gauss(0, 0.4)
    if arc:
        bed_hour += arc.bed_shift + arc_rng.gauss(0, arc.bed_spread)
        hours += arc.hours_shift
    if h.late_hours:
        # 늦게 자도 평일엔 일어나는 시각이 거의 같아 잔 시간이 줄고, 주말엔 조금 더 잔다
        bed_hour += h.late_hours
        hours -= h.late_hours * (0.4 if d.weekday() >= 5 else 0.75)
    bed = _at(d, bed_hour + h.bed_jitter)
    stage_kwargs = (
        {"deep_factor": arc.deep_factor, "wake_prob": arc.wake_prob, "wake_scale": arc.wake_scale} if arc else {}
    )
    stages = _plan_stages(rng, bed, max(60.0, hours * 60), **stage_kwargs)
    if rng.random() < persona.split_sleep_rate:
        # 새벽에 깨서 20~45분 뒤 다시 잠든다. 60분 안이라 집계에서 한 밤으로 묶인다 (calculation-rules.md 7.5)
        middle = bed + (stages[-1][1] - bed) * 0.45
        cut = next(i for i, (st, _, _) in enumerate(stages) if st >= middle)
        gap = timedelta(minutes=round(rng.uniform(20, 45)))
        plan.sleeps = [Night(stages[:cut]), Night([(a + gap, b + gap, k) for a, b, k in stages[cut:]])]
    else:
        plan.sleeps = [Night(stages)]
    pin = persona.pinned_nights.get(d)
    if pin is not None:
        # 위 계획은 난수 순서를 그대로 두려고 만든 것이고, 그날 밤은 정한 값으로 바꾼다 (운동은 평소대로)
        if pin.deep is not None:
            plan.sleeps = [Night(_pinned_stages(pin, d))]
        else:
            plan.sleeps = [Night(_plan_stages(_rng(persona, d, "pinned"), _at(d, pin.bed), pin.asleep, **stage_kwargs))]

    for w in persona.workouts.get(d.weekday(), ()):
        if rng.random() < 0.85:  # 가끔 거른다
            shift = arc.workout_shift if arc else 0.0
            start = _at(d, w.start_hour + shift + rng.gauss(0, 0.25)).replace(second=0, microsecond=0)
            minutes = round(w.minutes * rng.uniform(0.85, 1.15))
            if h.sick or (h.busy_week and _rng(persona, d, f"skip/{w.code}").random() < 0.6):
                continue  # 컨디션 나쁜 날, 바쁜 주에는 더 거른다
            if arc and arc_rng.random() < arc.skip_workout:
                continue  # 시연 이야기: 야근으로 운동을 거른다
            plan.workouts.append((w, start, start + timedelta(minutes=minutes)))
    forced = {"null_steps": GYM, "slow_walk_only": SLOW_WALK, "unknown_exercise": UNKNOWN_WORKOUT}.get(special or "")
    if forced is not None:
        # 그날 운동은 이것 하나만, 거르지 않고 정한 시각·길이 그대로
        start = _at(d, forced.start_hour)
        plan.workouts = [(forced, start, start + timedelta(minutes=forced.minutes))]
    return plan


# ----- 레코드 -----


def _kst(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat(timespec="seconds")


def _utc(dt: datetime) -> str:
    # 측정값(data) 안 시각은 UTC (candiy-api.md 2장)
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration(minutes: float) -> str:
    m = round(minutes)
    return f"PT{m // 60}H{m % 60}M"


class _Records:
    """CANDiY 응답과 같은 형식의 레코드를 지표별로 모은다. 아직 동기화되지 않았을 기록(now 이후)은 뺀다."""

    def __init__(self, persona: Persona, now: datetime, cutoff: datetime | None):
        self.persona = persona
        self.now = now
        self.cutoff = cutoff  # 이 시각부터는 폰이 동기화하지 않음
        self.by_metric: dict[str, list[dict]] = defaultdict(list)

    def add(self, metric: str, key: str, start: datetime, end: datetime | None, data: dict, modified: datetime):
        if modified > self.now or (self.cutoff and modified >= self.cutoff):
            return
        data_id = str(uuid.uuid5(DATA_ID_NAMESPACE, f"{self.persona.user_id}/{metric}/{key}"))
        record = {
            "_id": uuid.uuid5(DATA_ID_NAMESPACE, f"{data_id}/{modified.isoformat()}").hex,
            "user_id": "synthetic",
            "app": APP,
            "data": json.dumps(data),
            "data_id": data_id,
            "start": _kst(start),
            "last_modified_time": _kst(modified),
            "created_at": _kst(modified + timedelta(minutes=1)),
            "updated_at": _kst(modified + timedelta(minutes=1)),
        }
        if end is not None:
            record["end"] = _kst(end)
        self.by_metric[metric].append(record)


def _synced_after(rng: random.Random, t: datetime) -> datetime:
    """기록이 끝난 뒤 다음 동기화 시각."""
    return t + timedelta(minutes=rng.uniform(3, 20))


def _heart_records(out: _Records, rng: random.Random, samples: list[tuple[datetime, float]], key: str) -> None:
    """연속 측정 샘플을 1시간 단위 레코드로 묶는다 (candiy-api.md 3장 심박 예시와 같음)."""
    by_hour: dict[datetime, list[tuple[datetime, float]]] = defaultdict(list)
    for t, v in samples:
        by_hour[t.astimezone(KST).replace(minute=0, second=0, microsecond=0)].append((t, v))
    for hour, items in sorted(by_hour.items()):
        out.add(
            "heart_rates",
            f"{key}/{hour.isoformat()}",
            items[0][0],
            items[-1][0],
            {"samples": [{"time": _utc(t), "beatsPerMinute": round(v)} for t, v in items]},
            _synced_after(rng, items[-1][0]),
        )


def _emit_sleep(
    out: _Records,
    persona: Persona,
    d: date,
    plan: DayPlan,
    today: date,
    low_spo2: bool = False,
    arc: ArcDay | None = None,
) -> None:
    rng = _rng(persona, d, "sleep")
    shift = persona.recent_hr_shift if (today - d).days < 7 else 0.0
    night_hr = persona.sleep_hr + shift + rng.gauss(0, 1.5) + (arc.sleep_hr_shift if arc else 0.0)
    pin = persona.pinned_nights.get(d)
    if pin is not None and pin.sleep_hr is not None:
        night_hr = pin.sleep_hr
    synced = _synced_after(rng, plan.sleeps[-1].end)  # 깨고 나서 한 번에 동기화된다
    for i, night in enumerate(plan.sleeps):
        asleep = sum((b - a).total_seconds() / 60 for a, b, k in night.stages if k in (4, 5, 6))
        out.add(
            "sleeps",
            f"{d}/{i}",
            night.start,
            night.end,
            {
                "sleep_duration_total": _duration(asleep),
                "stages": [{"stage": k, "startTime": _utc(a), "endTime": _utc(b)} for a, b, k in night.stages],
            },
            synced,
        )
        # 수면 중 심박: 2분마다. 깊은 잠에서 조금 낮고 REM·깸에서 조금 높다
        samples = []
        for a, b, k in night.stages:
            offset = {5: -3, 6: 2, 1: 5}.get(k, 0)
            t = a
            while t < b:
                samples.append((t, night_hr + offset + rng.gauss(0, 2.5)))
                t += timedelta(minutes=2)
        _heart_records(out, rng, samples, f"sleep/{d}/{i}")
        if arc is not None:
            continue  # 시연 이야기는 아래에서 밤 전체로 한 번에 만든다
        # 산소포화도: 수면 중에만, 밤마다 몇 번
        # 90% 미만인 밤은 측정을 6번으로 늘려, 평균은 정상이고 측정 두 번만 낮게 한다
        for j in range(max(rng.randint(2, 6), 6 if low_spo2 else 0)):
            t = (night.start + (night.end - night.start) * rng.uniform(0.1, 0.9)).replace(microsecond=0)
            spo2 = round(min(100.0, rng.gauss(96, 1.2)), 1)
            if low_spo2 and j < 2:
                spo2 = (87.5, 89.9)[j]  # 90% 미만 두 번. 90.0은 세지 않는다 (7.5)
            out.add("oxygen_saturations", f"{d}/{i}/{j}", t, None, {"percentage": spo2}, synced)
    if arc is not None:
        _emit_arc_spo2(out, persona, d, plan, arc, synced)


def _emit_arc_spo2(out: _Records, persona: Persona, d: date, plan: DayPlan, arc: ArcDay, synced: datetime) -> None:
    """시연 이야기의 수면 중 산소포화도. 정상 기록은 90.5% 이상, 90% 미만은 정한 수만큼 새벽 3~5시에 둔다.

    실데이터처럼 밤마다 기록 1~2개(대부분 1개)다 (calculation-rules.md 7.5 "기록 빈도", S15P31A403-303).
    나뉜 수면은 60분 안이라 집계에서 한 밤으로 묶이므로 밤 전체(첫 조각 시작~마지막 조각 끝)로 만든다.
    """
    rng = _rng(persona, d, "arc-spo2")
    start, end = plan.sleeps[0].start, plan.sleeps[-1].end

    def at(lo: datetime, hi: datetime) -> datetime:
        return (lo + (hi - lo) * rng.uniform(0, 1)).replace(microsecond=0)

    # 밤마다 기록 수는 날짜로만 정한다. 기준일이 지나 그 밤의 이야기 단계가 바뀌어도 기록 수가 같아야
    # 매일 갱신 때 같은 data_id를 덮어써 예전 기록(예: 예전 단계의 90% 미만)이 남지 않는다 (적재는 지우지 않는다)
    total = 1 if _rng(persona, d, "arc-spo2-count").random() < 0.7 else 2
    low = min(arc.low_spo2, total)
    values = [
        (
            at(start + (end - start) * 0.1, end - (end - start) * 0.1),
            round(max(90.5, min(100.0, rng.gauss(95.5, 1.3))), 1),
        )
        for _ in range(total - low)
    ]
    lo, hi = max(start, _at(d, 3)), min(end, _at(d, 5))
    if hi - lo < timedelta(minutes=30):
        lo, hi = start + (end - start) * 0.4, end - timedelta(minutes=5)  # 새벽 3~5시에 자고 있지 않은 밤
    values += [(at(lo, hi), round(rng.uniform(83.0, 89.8), 1)) for _ in range(low)]
    for j, (t, spo2) in enumerate(values):
        out.add("oxygen_saturations", f"{d}/arc/{j}", t, None, {"percentage": spo2}, synced)


def _emit_workouts(out: _Records, persona: Persona, d: date, plan: DayPlan) -> None:
    rng = _rng(persona, d, "workout")
    for i, (w, start, end) in enumerate(plan.workouts):
        key = f"{d}/{i}"
        synced = _synced_after(rng, end)
        seconds = (end - start).total_seconds()
        kcal = round(w.kcal_per_min * seconds / 60 * rng.uniform(0.9, 1.1), 1)
        out.add("exercise_sessions", key, start, end, {"exercise_type": w.code, "title": ""}, synced)
        out.add("calories_burned", key, start, end, {"energy_kcal": kcal}, synced)
        # 운동 중 심박: 10초마다 (실데이터는 1초 간격이지만 양을 줄였다). 처음 5분은 서서히 오른다
        samples = []
        t = start
        while t <= end:
            warmup = min(1.0, (t - start).total_seconds() / 300)
            samples.append((t, 85 + (w.hr - 85) * warmup + rng.gauss(0, 5)))
            t += timedelta(seconds=10)
        _heart_records(out, rng, samples, f"workout/{key}")
        if w.speed_kmh is not None:
            speeds = [
                (start + timedelta(minutes=m), w.speed_kmh / 3.6 * rng.uniform(0.85, 1.15))
                for m in range(int(seconds // 60) + 1)
            ]
            avg = sum(v for _, v in speeds) / len(speeds)
            out.add("distances", key, start, end, {"distance_m": round(avg * seconds, 1)}, synced)
            speed_samples = [{"time": _utc(t), "speed_mps": round(v, 2)} for t, v in speeds]
            out.add("speeds", key, start, end, {"samples": speed_samples}, synced)


def _emit_day_heart(out: _Records, persona: Persona, d: date, busy: list[tuple[datetime, datetime]]) -> None:
    """평소 심박: 30분마다 샘플 1개짜리 기록. 수면·운동 중에는 연속 측정이 따로 있다."""
    rng = _rng(persona, d, "heart")
    for slot in range(48):
        t = (_at(d, slot / 2) + timedelta(minutes=rng.uniform(0, 25))).replace(microsecond=0)
        if any(a <= t <= b for a, b in busy) or rng.random() > 0.9:
            continue
        active = 8 if t.astimezone(KST).hour in persona.step_hours else 0
        v = persona.sleep_hr + 14 + active + rng.gauss(0, 6)
        sample = {"samples": [{"time": _utc(t), "beatsPerMinute": round(v)}]}
        out.add("heart_rates", f"day/{d}/{slot}", t, t, sample, _synced_after(rng, t))


def _emit_steps(out: _Records, persona: Persona, d: date, plan: DayPlan) -> None:
    """하루 누적 1건. 동기화될 때마다 그때까지의 누적값으로 새 버전이 온다 (candiy-api.md 6.7)."""
    rng = _rng(persona, d, "steps")
    base = persona.steps[1 if d.weekday() >= 5 else 0]
    total = max(0.0, rng.gauss(base, base * 0.12)) * plan.step_factor
    weights = {h: rng.uniform(0.5, 1.5) for h in persona.step_hours}
    per_hour = [total * weights.get(h, 0) / sum(weights.values()) for h in range(24)]
    for w, start, end in plan.workouts:
        # 걷기·달리기 걸음은 운동한 시간대에 더한다
        t = start
        while t < end:
            per_hour[t.astimezone(KST).hour] += w.cadence
            t += timedelta(minutes=1)

    day_end = day_start(d + timedelta(days=1))

    def cumulative(t: datetime) -> int:
        if t >= day_end:
            return round(sum(per_hour))
        h = t.astimezone(KST)
        return round(sum(per_hour[: h.hour]) + per_hour[h.hour] * h.minute / 60)

    t = day_start(d) + timedelta(minutes=rng.uniform(0, persona.sync_minutes))
    last = 0
    while t < day_end + timedelta(minutes=persona.sync_minutes):
        t = t.replace(microsecond=0)
        count = cumulative(t)
        if count > last:
            out.add("steps", str(d), day_start(d), day_end - timedelta(seconds=1), {"count": count}, t)
            last = count
        t += timedelta(minutes=persona.sync_minutes * rng.uniform(0.6, 1.4))


def _emit_body(out: _Records, persona: Persona, d: date, weight_delta: float = 0.0) -> None:
    """워치 체성분 측정 한 번에 네 값이 같은 시각으로 온다 (candiy-api.md 6.6)."""
    rng = _rng(persona, d, "body")
    t = _at(d, 8 + rng.uniform(0, 1)).replace(microsecond=0)
    synced = _synced_after(rng, t)
    weight, fat, bmr, height = persona.body_values
    for metric, key, value in (
        ("weights", "weight_kg", round(rng.gauss(weight + weight_delta, 0.3), 1)),
        ("body_fats", "percentage", round(rng.gauss(fat, 0.5), 1)),
        ("basal_metabolic_rates", "bmr_kcal_per_day", round(rng.gauss(bmr, 10))),
        ("heights", "height_m", height),
    ):
        out.add(metric, str(d), t, None, {key: value}, synced)


def generate(
    persona: Persona, today: date, now: datetime, days: int = INITIAL_DAYS, *, human: bool = False
) -> dict[str, list[dict]]:
    """today까지 days일치 레코드 (지표 → CANDiY 응답 레코드 목록). now 이후에 동기화될 기록은 넣지 않는다.

    human: 사람다운 흐트러짐 (S15P31A403-235). False면 예전과 같은 값 (골든셋 고정 데이터)
    """
    first = today - timedelta(days=days - 1)
    cutoff = day_start(today - timedelta(days=persona.unsynced_days - 1)) if persona.unsynced_days else None
    out = _Records(persona, now, cutoff)

    def special(d: date) -> str | None:
        return persona.special_days.get((today - d).days)

    arcs = {first + timedelta(days=i): _arc_day(persona, first + timedelta(days=i), today) for i in range(-1, days + 1)}
    plans: dict[date, DayPlan] = {}
    for d in sorted(arcs):  # 운동한 다음 밤이 앞날 계획을 보므로 날짜 순서대로
        arcs[d] = _after_workout(persona, arcs[d], plans.get(d - timedelta(days=1)))
        plans[d] = _plan_day(persona, d, special(d), human, arcs[d])

    # 평소 심박을 재지 않는 시간: 모든 수면·운동 시간
    busy = [(n.start, n.end) for p in plans.values() for n in p.sleeps]
    busy += [(a, b) for p in plans.values() for _, a, b in p.workouts]

    for i in range(days):
        d = first + timedelta(days=i)
        plan = plans[d]
        kind = special(d)
        if kind == "zero_steps":
            # 폰이 0보로 한 번만 동기화했다
            out.add(
                "steps",
                str(d),
                day_start(d),
                day_start(d + timedelta(days=1)) - timedelta(seconds=1),
                {"count": 0},
                _at(d, 21),
            )
        elif kind not in ("no_record", "null_steps"):
            _emit_steps(out, persona, d, plan)  # 걸음은 폰이 세므로 워치를 안 찬 날에도 있다
        if not plan.worn:
            continue
        arc = arcs[d]
        _emit_sleep(out, persona, d, plan, today, low_spo2=kind == "low_spo2", arc=arc)
        _emit_workouts(out, persona, d, plan)
        _emit_day_heart(out, persona, d, busy)
        if persona.body and d.weekday() == 6:
            _emit_body(out, persona, d, arc.weight_delta if arc else 0.0)
    return dict(out.by_metric)


class SyntheticClient:
    """CandiyClient 대신 합성 레코드를 돌려준다. 실제 API처럼 시작·종료가 모두 기간 안인 기록만 준다 (6.5)."""

    def __init__(self, records: dict[str, dict[str, list[dict]]]):
        self._records = records  # candiy_id → 지표 → 레코드

    def fetch(self, metric: str, candiy_id: str, start: date, end: date) -> list[dict]:
        result = []
        for r in self._records.get(candiy_id, {}).get(metric, []):
            first, last = r["start"][:10], (r.get("end") or r["start"])[:10]
            if start.isoformat() <= first and (metric in NO_END_PARAM or last <= end.isoformat()):
                result.append(r)
        return result


# ----- 실행 -----


@dataclass(frozen=True)
class Profile:
    """users 테이블에 넣는 회원 정보. 닉네임 12자 이내 (users CHECK)."""

    display_name: str
    birth_year: int
    gender: str  # MALE / FEMALE
    height_cm: float
    weight_kg: float


PROFILES = {
    "synth-01": Profile("모범형", 1990, "FEMALE", 163.0, 55.0),
    "synth-02": Profile("직장인", 1992, "MALE", 175.0, 74.0),
    "synth-03": Profile("수면부족", 1996, "FEMALE", 160.0, 52.0),
    "synth-04": Profile("운동안함", 1985, "MALE", 172.0, 82.0),
    "synth-05": Profile("운동선수", 1998, "MALE", 178.0, 72.0),  # 체성분 기록(72kg, 1.78m)과 맞춘다
    "synth-06": Profile("주말운동", 1988, "MALE", 176.0, 76.0),
    "synth-07": Profile("주말잠", 1994, "FEMALE", 165.0, 58.0),
    "synth-08": Profile("야간근무", 1987, "MALE", 174.0, 78.0),
    "synth-09": Profile("연동끊김", 1991, "FEMALE", 158.0, 54.0),
    "synth-10": Profile("고등학생", 2009, "MALE", 170.0, 60.0),
    "synth-edge": Profile("경계상황", 1993, "FEMALE", 162.0, 56.0),
    "synth-11": Profile("바쁜직장인", 1991, "FEMALE", 163.0, 52.0),  # 체성분 기록 시작값(52kg, 1.63m)과 맞춘다
    "synth-12": Profile("헬스마니아", 1998, "MALE", 178.0, 76.0),
    "synth-13": Profile("지수", 1998, "FEMALE", 163.0, 52.0),  # 광고 속 지수 (20대 후반)
}

# 실제 소셜 계정이 없는 개발용 회원이다. social_user_id 접두어로 파이프라인 조회에서 빠진다 (targets.py)
UPSERT_USER_SQL = """
INSERT INTO users (id, social_type, social_user_id, display_name, birth_year, gender, height_cm, weight_kg)
VALUES (%(id)s, 'GOOGLE', %(social_user_id)s, %(display_name)s, %(birth_year)s, %(gender)s, %(height_cm)s,
        %(weight_kg)s)
ON CONFLICT (id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    birth_year = EXCLUDED.birth_year,
    gender = EXCLUDED.gender,
    height_cm = EXCLUDED.height_cm,
    weight_kg = EXCLUDED.weight_kg,
    updated_at = now()
"""


def _users_table_exists(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT to_regclass('public.users') IS NOT NULL").fetchone()[0]


def _upsert_user(conn: psycopg.Connection, persona: Persona, member_id: uuid.UUID) -> None:
    p = PROFILES[persona.user_id]
    conn.execute(
        UPSERT_USER_SQL,
        {
            "id": member_id,
            "social_user_id": SYNTHETIC_SOCIAL_PREFIX + persona.user_id,
            "display_name": p.display_name,
            "birth_year": p.birth_year,
            "gender": p.gender,
            "height_cm": p.height_cm,
            "weight_kg": p.weight_kg,
        },
    )


def _delete_health(conn: psycopg.Connection, member_ids: list[uuid.UUID]) -> None:
    for table in ("raw_health_record", "step_snapshot", "sync_state", "lifestyle_score", *AGGREGATE_TABLES):
        conn.execute(f"DELETE FROM {table} WHERE end_user_id = ANY(%s)", (member_ids,))


def _delete_stale_members(conn: psycopg.Connection, persona: Persona, member_id: uuid.UUID) -> None:
    """같은 합성 회원 이름인데 회원 ID가 다른 예전 회원을 지운다.

    회원 ID 규칙이 바뀌면(uuid5 → UUIDv4, S15P31A403-126) 예전 행이 social_user_id 고유 키에 걸려 새 회원을 넣지 못한다.
    """
    stale = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM users WHERE social_user_id = %s AND id <> %s",
            (SYNTHETIC_SOCIAL_PREFIX + persona.user_id, member_id),
        )
    ]
    if stale:
        _delete_health(conn, stale)
        conn.execute("DELETE FROM users WHERE id = ANY(%s)", (stale,))
        log.info("%s 예전 회원 ID %d개를 지웠습니다", persona.user_id, len(stale))


def seed_member(
    conn: psycopg.Connection,
    member_id: uuid.UUID,
    persona: Persona,
    today: date,
    now: datetime,
    *,
    full: bool = False,
) -> None:
    """기존 회원에 합성 데이터만 적재한다. 실패하면 회원의 이번 적재 전체를 롤백한다.

    full: 90일 전체 다시 (09:00 갱신). 아니면 처음·예전 35일 계정만 90일 전체, 나머지는 오늘·어제만 (15분 갱신).
    기준일에 따라 값이 바뀌는 날(최근 7일 심박 상승 등)은 하루 한 번 전체 갱신으로 맞춘다
    """
    with member_lock(conn, member_id) as locked:
        if not locked:
            raise ValueError("해당 회원의 동기화가 진행 중입니다. 완료 후 다시 실행하세요")
        with conn.transaction():
            if not conn.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (member_id,)).fetchone():
                raise ValueError(
                    "해당 DB에 회원이 없습니다. 로그인·가입 완료 후 /api/v1/users/me의 data.id를 확인하세요"
                )
            # 기존 생성기는 실제 앱 이름을 쓴다. 기존 회원에 넣는 데이터는 유형까지 구별해 혼합을 막는다.
            source = f"dev.candiy-fit.synthetic.{persona.user_id}"
            mixed = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM raw_health_record WHERE end_user_id = %s AND app <> %s)",
                (member_id, source),
            ).fetchone()[0]
            if mixed:
                raise ValueError(
                    "기존 건강 데이터 또는 다른 합성 유형이 있어 중단했습니다. 기존 데이터는 변경하지 않습니다"
                )
            records = generate(persona, today, now, SYNTHETIC_DAYS, human=True)
            for rows in records.values():
                for record in rows:
                    record["app"] = source
            client = SyntheticClient({persona.user_id: records})
            target = Target(member_id=member_id, candiy_id=persona.user_id)
            # 처음이거나 예전(35일)에 넣은 계정이면 90일 전체, 이미 90일이 있으면 오늘·어제만 (15분 갱신)
            first = conn.execute(
                "SELECT min(start_at) FROM raw_health_record WHERE end_user_id = %s", (member_id,)
            ).fetchone()[0]
            full = full or first is None or first > day_start(today - timedelta(days=SYNTHETIC_DAYS - 2))
            for metric in METRICS:
                result = sync_metric(
                    conn,
                    client,
                    target,
                    metric,
                    today,
                    full=full,
                    recent_days=REFRESH_DAYS,
                    initial_days=SYNTHETIC_DAYS,
                    refresh_source=source,
                )
                if result.status != "success":
                    raise ValueError(f"{metric} 적재 실패: {result.error}")
            dates = dates_to_aggregate(conn, member_id, all_dates=full)
            counts = aggregate_and_mark_ready(conn, member_id, dates, list(METRICS))
        log.info("member_id %s, %s: 집계 날짜 %d, 만든 행 %s", member_id, persona.user_id, len(dates), dict(counts))


def parse_members(entries: tuple[str, ...]) -> list[tuple[uuid.UUID, Persona]]:
    """PIPELINE_SYNTHETIC_MEMBERS의 "회원 UUID:페르소나" 목록. 형식이 틀리면 어느 항목인지 알려 준다."""
    members = []
    for i, entry in enumerate(entries, 1):
        member, _, persona = entry.partition(":")
        try:
            members.append((uuid.UUID(member.strip()), PERSONA_BY_ID[persona.strip()]))
        except (ValueError, KeyError):
            raise ValueError(
                f"PIPELINE_SYNTHETIC_MEMBERS {i}번째 항목 형식이 틀렸습니다 (회원 UUID:페르소나, 예: <UUID>:synth-02)"
            ) from None
    return members


def refresh_members(
    conn: psycopg.Connection,
    members: list[tuple[uuid.UUID, Persona]],
    today: date,
    now: datetime,
    *,
    full: bool = False,
) -> int:
    """목록의 로그인 회원마다 합성 데이터를 다시 넣는다. 한 회원이 실패해도 나머지는 계속한다. 실패 수를 돌려준다."""
    failed = 0
    for member_id, persona in members:
        try:
            seed_member(conn, member_id, persona, today, now, full=full)
        except ValueError as error:
            failed += 1
            log.error("member_id %s, %s: %s", member_id, persona.user_id, error)
    return failed


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="candiy_pipeline.synthetic")
    parser.add_argument("--list-people", action="store_true", help="선택 가능한 페르소나 ID·이름 출력 (DB 연결 없음)")
    parser.add_argument(
        "--people", nargs="+", choices=list(PERSONA_BY_ID), help="합성 유형 (--member와 함께 쓸 때 정확히 하나)"
    )
    parser.add_argument("--member", type=uuid.UUID, help="기존 로그인 회원의 users.id (UUID)")
    parser.add_argument("--today", type=date.fromisoformat, help="기준 날짜 (기본: 오늘, KST)")
    parser.add_argument("--reset", action="store_true", help="이전에 넣은 합성 데이터를 지우고 다시 넣는다")
    parser.add_argument(
        "--classic",
        action="store_true",
        help="예전 방식: 35일, 흐트러짐 없음, 매번 전체 (골든셋 고정 데이터용)",
    )
    parser.add_argument(
        "--refresh-members",
        action="store_true",
        help="PIPELINE_SYNTHETIC_MEMBERS의 로그인 회원마다 다시 넣는다 (개발 서버 15분 갱신)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="--refresh-members와 함께: 로그인 회원도 90일 전체 다시 (09:00 갱신. 없으면 오늘·어제만)",
    )
    args = parser.parse_args(argv)
    if args.list_people:
        for persona in PERSONA_BY_ID.values():
            print(f"{persona.user_id}\t{persona.name}")
        return 0
    if args.member and (not args.people or len(args.people) != 1):
        parser.error("--member는 --people로 합성 유형을 정확히 하나 지정해야 합니다")
    if args.member and args.reset:
        parser.error("기존 회원의 데이터를 보호하기 위해 --member와 --reset은 함께 사용할 수 없습니다")
    if args.full and not args.refresh_members:
        parser.error("--full은 --refresh-members와 함께 쓴다 (합성 회원 전체는 --reset)")
    if args.classic and (args.member or args.refresh_members):
        parser.error("--classic은 합성 회원(--people)에만 쓴다 (골든셋 고정 데이터용)")
    if args.refresh_members and (args.member or args.people or args.reset):
        parser.error("--refresh-members는 --member·--people·--reset과 함께 쓸 수 없습니다 (목록은 .env에서 읽음)")

    real_now = datetime.now(KST)
    today = args.today or real_now.date()
    # 지난 날짜를 기준일로 주면 그날 21시까지 동기화된 것으로 본다
    now = real_now if today == real_now.date() else _at(today, 21)
    settings = load_settings(require_candiy=False)
    if settings.app_env not in {"local", "dev"}:
        parser.error("합성 데이터는 APP_ENV=local 또는 dev에서만 생성할 수 있습니다 (미설정·운영 환경 차단)")
    if (args.member or args.refresh_members) and settings.include_members:
        parser.error("--member 사용 시 PIPELINE_INCLUDE_MEMBERS=false로 실제 건강데이터 동기화를 꺼야 합니다")
    if args.refresh_members:
        try:
            members = parse_members(settings.synthetic_members)
        except ValueError as error:
            parser.error(str(error))
        if not members:
            log.info("PIPELINE_SYNTHETIC_MEMBERS가 비어 있어 다시 넣을 회원이 없습니다")
            return 0
        with psycopg.connect(settings.db_dsn, autocommit=True) as conn:
            failed = refresh_members(conn, members, today, now, full=args.full)
        log.info("로그인 회원 %d명 중 %d명 갱신", len(members), len(members) - failed)
        return 1 if failed else 0

    # --people가 없으면 기본 10명만 (경계용 synth-edge는 직접 지정할 때만)
    people = [PERSONA_BY_ID[p] for p in args.people] if args.people else list(PERSONAS)
    if args.member:
        with psycopg.connect(settings.db_dsn, autocommit=True) as conn:
            if not _users_table_exists(conn):
                parser.error("users 테이블이 없습니다. 개발 DB에 마이그레이션을 먼저 적용하세요")
            try:
                seed_member(conn, args.member, people[0], today, now)
            except ValueError as error:
                log.error("%s", error)
                return 1
        return 0
    # 기본: 90일·흐트러짐. --reset이면 90일 전체, 아니면 오늘·어제만 (15분 갱신, 처음이면 90일)
    # --classic: 예전과 같은 35일·흐트러짐 없음·매번 전체 (골든셋 고정 데이터가 그대로 다시 만들어진다)
    days = INITIAL_DAYS if args.classic else SYNTHETIC_DAYS
    full = args.classic or args.reset
    client = SyntheticClient({p.user_id: generate(p, today, now, days, human=not args.classic) for p in people})
    with psycopg.connect(settings.db_dsn, autocommit=True) as conn:
        with_users = _users_table_exists(conn)
        if not with_users:
            log.warning("users 테이블이 없어 건강 테이블에만 넣습니다. alembic upgrade head 후 다시 실행하세요")
        for p in people:
            target = target_for_test_user(p.user_id)
            # --reset은 건강 데이터만 지운다. users 행은 회원 ID가 고정이라 프로필만 갱신하고 남긴다
            # (코칭 대화·로그인 세션이 회원을 참조해 지울 수 없다)
            if args.reset:
                _delete_health(conn, [target.member_id])
            if with_users:
                _delete_stale_members(conn, p, target.member_id)
                _upsert_user(conn, p, target.member_id)
            fetched = 0
            for metric in METRICS:
                r = sync_metric(
                    conn, client, target, metric, today, full=full, recent_days=REFRESH_DAYS, initial_days=days
                )
                if r.status != "success":
                    log.error("%s %s 적재 실패: %s", p.user_id, metric, r.error)
                    return 1
                fetched += r.fetched
            dates = dates_to_aggregate(conn, target.member_id, all_dates=full)
            counts = aggregate_and_mark_ready(conn, target.member_id, dates, list(METRICS))
            if today != real_now.date():
                # 지난 기준일이면 적재·집계도 그 기준 시각에 한 것으로 둔다. 실행 시각이 남으면 기준 시각보다 늦은
                # 성공 시각이 생기고, 같은 기준일로 다시 넣어도 결과가 달라진다 (골든셋 고정 데이터, S15P31A403-163)
                conn.execute(
                    "UPDATE sync_state SET last_attempt_at = %(now)s, last_success_at = %(now)s "
                    "WHERE end_user_id = %(m)s AND last_success_at IS NOT NULL",
                    {"now": now, "m": target.member_id},
                )
            log.info(
                "%s %s (member_id %s): 레코드 %d, 집계 날짜 %d, 만든 행 %s",
                p.user_id,
                p.name,
                target.member_id,
                fetched,
                len(dates),
                dict(counts),
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
