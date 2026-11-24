import asyncio
import dataclasses
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from coach_ai.adapters.memory import ExerciseSession, HeartDay, SleepSession, StepDay, SyncState
from coach_ai.adapters.memory import InMemoryHealthData as FakeHealthData
from coach_ai.agent.executor import ToolRequest
from coach_ai.ports import HealthDataNotReadyError

# 연동 모듈의 공통 조회 함수가 합성 입력으로 만든 공개 원형(to_public 결과)과 소유자.
# 같은 입력의 메모리 어댑터 결과와 test_context_ports.py 에서 비교한다
COMMON_RESULTS = json.loads((Path(__file__).parent / "data" / "common_query_results.json").read_text(encoding="utf-8"))

# API 명세 5.3 의 합성 응답 예시 세 개를 점수 포트가 반환하는 snake_case 로 옮긴 값이다.
# 오늘 진행 중인 점수(걸음 5,400보·운동 24분·수면 380분·수면 중 심박 66.0 대 평소 58.0, 20밤)
SCORE_IN_PROGRESS = {
    "date": "2026-10-20",
    "is_in_progress": True,
    "reason": None,
    "total": {"score": 67, "missing_areas": []},
    "areas": {
        "activity": {
            "score": 80, "grade": 1, "steps": {"score": 48, "value": 5400}, "exercise": {"score": 32, "minutes": 24},
        },
        "sleep": {"score": 65, "grade": 2, "sleep_minutes": 380},
        "heart": {
            "score": 55, "grade": 3, "sleep_hr_avg": 66.0, "baseline_hr_avg": 58.0, "baseline_nights": 20,
            "check_wear": False,
        },
    },
    "computed_at": "2026-10-20T03:15:00Z",
}  # fmt: skip
# 워치를 차지 않고 잔 날. 수면·심박이 기록 없음이고 오늘의 점수는 활동 점수만의 평균이다
SCORE_WITHOUT_SLEEP = {
    **SCORE_IN_PROGRESS,
    "total": {"score": 80, "missing_areas": ["sleep", "heart"]},
    "areas": {
        "activity": SCORE_IN_PROGRESS["areas"]["activity"],
        "sleep": {"score": None, "grade": None, "sleep_minutes": None},
        "heart": {
            "score": None, "grade": None, "sleep_hr_avg": None, "baseline_hr_avg": 58.0, "baseline_nights": 20,
            "check_wear": False,
        },
    },
}  # fmt: skip
# 점수 행이 없는 날짜 (최초 집계 완료, 기록 시작일 이후·동기화 기준일 이전)
SCORE_NO_ROW = {
    "date": "2026-10-15",
    "is_in_progress": False,
    "reason": "NO_RECORD",
    "total": {"score": None, "missing_areas": ["activity", "sleep", "heart"]},
    "areas": {
        "activity": {
            "score": None, "grade": None, "steps": {"score": None, "value": None},
            "exercise": {"score": None, "minutes": None},
        },
        "sleep": {"score": None, "grade": None, "sleep_minutes": None},
        "heart": {
            "score": None, "grade": None, "sleep_hr_avg": None, "baseline_hr_avg": None, "baseline_nights": None,
            "check_wear": None,
        },
    },
    "computed_at": None,
}  # fmt: skip


KST = timezone(timedelta(hours=9))
TODAY = date(2026, 10, 15)  # 목요일
D = date


def at(d: int, hh: int, mm: int) -> datetime:
    return datetime(2026, 10, d, hh, mm, tzinfo=KST)


NOW = at(15, 12, 10)


def night(wake_day: int, bed: tuple[int, int, int], wake: tuple[int, int], minutes: int | None, main=True):
    return SleepSession(
        date=D(2026, 10, wake_day),
        start_at=at(*bed),
        end_at=at(wake_day, *wake),
        is_main=main,
        sleep_minutes=minutes,
        deep_minutes=None if minutes is None else 60,
        rem_minutes=None if minutes is None else 80,
        light_minutes=None if minutes is None else minutes - 140,
        awake_minutes=None if minutes is None else 20,
    )


def sample_port() -> FakeHealthData:
    steps = {9: 8000, 10: 0, 11: 6000, 13: 7000, 15: 4210}  # 10일은 0보라 결측, 12·14일은 기록 없음
    return FakeHealthData(
        steps={
            "u1": [StepDay(D(2026, 10, d), v) for d, v in steps.items()],
            "u2": [StepDay(D(2026, 10, d), 99999) for d in range(9, 16)],
        },
        heart={
            "u1": [HeartDay(D(2026, 10, d), 70.0 + d / 10, 50 + d, 120 + d, sample_count=120) for d in range(9, 16)]
        },
        sleep={
            "u1": [
                night(12, (11, 23, 50), (7, 35), 450),
                night(13, (13, 0, 35), (7, 5), 368),
                night(13, (13, 14, 0), (14, 40), 40, main=False),  # 낮잠
                night(14, (14, 1, 10), (7, 15), None),  # 실제 수면 시간을 모르는 밤
            ]
        },
        sync={"u1": {"steps": SyncState(at(15, 12, 5)), "heart_rate": SyncState(at(15, 11, 58))}},
        linked_from={"u1": D(2026, 9, 20)},
        now=NOW,
    )


def request(port) -> ToolRequest:
    return ToolRequest(user_id="u1", today=TODAY, port=port)


def workout(d: int, start: tuple[int, int], minutes: int, *, walk: bool = False) -> ExerciseSession:
    """달리기는 운동 시간에 들어가고 느린 걷기는 들어가지 않는다."""
    begin = at(d, *start)
    name, counted = ("걷기", False) if walk else ("달리기", True)
    return ExerciseSession(
        date=D(2026, 10, d), start_at=begin, end_at=begin + timedelta(minutes=minutes), duration_minutes=minutes,
        exercise_type=79 if walk else 56, type_name=name, category="cardio", counts_as_exercise=counted,
    )  # fmt: skip


def exercise_port(*today: ExerciseSession) -> FakeHealthData:
    """12일은 30분, 13일은 느린 걷기만 있어 0분, 14일은 활동 기록이 없다.

    운동 상태의 기준 시각은 마지막 운동이 끝난 시각이고, 동기화 기준 시각은 걸음 수의 오늘 12:05다.
    """
    sessions = [workout(12, (19, 0), 30), workout(13, (18, 0), 20, walk=True), *today]
    return FakeHealthData(
        steps={"u1": [StepDay(D(2026, 10, d), 5000) for d in (12, 13, 15)]},
        exercise={"u1": sessions},
        sync={"u1": {"steps": SyncState(at(15, 12, 5)), "exercise": SyncState(max(s.end_at for s in sessions))}},
        linked_from={"u1": D(2026, 9, 20)},
        now=NOW,
    )


class SlowPort(FakeHealthData):
    """조회마다 정해 둔 시간만큼 기다린다. 두 조회가 동시에 시작했는지 기록한다."""

    def __init__(self, base: FakeHealthData, delay: float) -> None:
        super().__init__(**vars(base))
        self.delay = delay
        self.running = 0
        self.peak = 0

    async def get_metric_result(self, user_id, metric, start, end):
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(self.delay)
            return await super().get_metric_result(user_id, metric, start, end)
        finally:
            self.running -= 1


class FailFastPort(FakeHealthData):
    """정해 둔 기간 시작일이나 지표의 조회는 바로 실패하고, 나머지 조회는 오래 기다린다. 취소된 조회 수를 기록한다."""

    def __init__(self, base: FakeHealthData, *, fail_start: date | None = None, fail_metric: str | None = None) -> None:
        super().__init__(**vars(base))
        self.fail_start = fail_start
        self.fail_metric = fail_metric
        self.cancelled = 0

    async def get_metric_result(self, user_id, metric, start, end):
        if start == self.fail_start or metric == self.fail_metric:
            raise HealthDataNotReadyError(metrics=(metric,))
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return await super().get_metric_result(user_id, metric, start, end)


# 되돌림과 완곡 바꾸기의 CoachResult 칸. 이전 앱 코드의 결과에는 없다
ADDED_RESULT_FIELDS = ("answer_rechecks", "hedged_sentences", "unsupported_rechecks", "exercise_rechecks")


def old_app_run_coach(run_coach):
    """run_coach 를 감싸 ADDED_RESULT_FIELDS 칸이 없는 결과를 반환한다. 실행기로 옛 앱 코드를 잴 때를 꾸민다."""

    async def run(*args, **kwargs):
        result = await run_coach(*args, **kwargs)
        names = [f.name for f in dataclasses.fields(result) if f.name not in ADDED_RESULT_FIELDS]
        old = dataclasses.make_dataclass("CoachResult", names, frozen=True)
        return old(**{name: getattr(result, name) for name in names})

    return run


__all__ = [
    "COMMON_RESULTS",
    "D",
    "KST",
    "NOW",
    "SCORE_IN_PROGRESS",
    "SCORE_NO_ROW",
    "SCORE_WITHOUT_SLEEP",
    "TODAY",
    "FailFastPort",
    "FakeHealthData",
    "SlowPort",
    "at",
    "exercise_port",
    "night",
    "old_app_run_coach",
    "request",
    "sample_port",
    "workout",
]
