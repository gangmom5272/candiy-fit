"""건강 조회 함수 대신 가짜 조회 함수를 넣어 DB 없이 어댑터의 변환을 검사한다."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from app.coaching import health_port
from app.coaching.health_port import HealthQueryPort
from app.health import errors as health_errors
from app.health.calculations import exercise_records
from app.health.schemas import (
    KST,
    ExerciseSessionDetail,
    LifestyleScoreRecord,
    MissingLifestyleScore,
)
from coach_ai import ports
from coach_ai.adapters.memory import (
    ExerciseSession,
    InMemoryHealthData,
    StepDay,
    SyncState,
)
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall
from coach_ai.security.gateway import ToolGateway
from tests.health.test_calculations import STATUS, TODAY, calculate, steps
from tests.health.test_exercise import DAY, activity_result, exercise
from tests.health.test_schemas import USER
from tests.health.test_score_contract import stored_score

START = date(2026, 10, 16)
# 세션 팩토리는 조회 함수에 그대로 넘기기만 하므로 기본 테스트에서는 표식 객체로 대신한다
FACTORY = object()


class FakeLookup:
    """건강 조회 함수 자리에 넣어 호출 인자를 기록하고 정해진 결과나 예외를 반환한다."""

    def __init__(self, outcome=None) -> None:
        self.outcome = outcome
        self.calls = []

    async def __call__(self, user_id, metric, start, end, *, session_factory):
        self.calls.append((user_id, metric, start, end, session_factory))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if self.outcome is not None:
            return self.outcome
        return calculate([], metric=metric, start=start, end=end)


def test_health_query_port_instance_satisfies_coach_port():
    assert isinstance(HealthQueryPort(FACTORY), ports.HealthDataPort)


def test_session_factory_constructor_factory_returns_same_object():
    assert HealthQueryPort(FACTORY).session_factory is FACTORY


@pytest.mark.parametrize("distance", [None, Decimal("0.0"), Decimal("1234.5")])
async def test_exercise_distance_reaches_ai_port_unchanged(distance):
    found = calculate(
        exercise_records([exercise(distance_m=distance)]), metric="exercises"
    )
    lookup = FakeLookup(found)
    result = await HealthQueryPort(FACTORY, lookup).get_metric_result(
        str(USER), "exercise", DAY, TODAY
    )
    assert lookup.calls == [(USER, "exercises", DAY, TODAY, FACTORY)]
    assert result.snapshot == found.to_public().model_dump(mode="json")
    assert result.detail.sessions[0]["distance_m"] == (
        None if distance is None else float(distance)
    )


def test_session_factory_assignment_raises_attribute_error():
    port = HealthQueryPort(FACTORY)
    with pytest.raises(AttributeError):
        port.session_factory = object()
    assert port.session_factory is FACTORY


async def test_exercise_activity_passes_port_unchanged_including_zero_and_null():
    found = activity_result()
    lookup = FakeLookup(found)
    result = await HealthQueryPort(FACTORY, lookup).get_metric_result(
        str(USER), "exercise", found.period.start, found.period.end
    )
    assert lookup.calls == [
        (USER, "exercises", found.period.start, found.period.end, FACTORY)
    ]
    assert result.snapshot == found.to_public().model_dump(mode="json")
    detail = result.snapshot["detail"]["exercise_activity"]
    assert detail["days"][0]["exercise_count"] == 1
    assert detail["days"][0]["exercise_minutes"] == 30
    assert detail["days"][0]["exercise_distance_m"] == 4200
    assert detail["days"][1]["exercise_calories_kcal"] == 0
    assert detail["days"][1]["exercise_distance_m"] is None
    assert "user_id" not in json.dumps(result.snapshot)


@pytest.mark.parametrize("tool", ["get_metric_summary", "get_daily_trend"])
async def test_exercise_activity_reaches_ai_and_evidence_validation(tool):
    from app.coaching.schemas import evidence_data, partition_evidence

    found = activity_result()
    lookup = FakeLookup(found)
    gateway = ToolGateway(HealthQueryPort(FACTORY, lookup), TODAY)
    replies = await gateway.execute(
        [
            ToolCall(
                "activity",
                tool,
                {
                    "metric": "exercise",
                    "period": f"dates:{found.period.start.isoformat()}..{found.period.end.isoformat()}",
                },
            )
        ],
        CoachContext(user_id=str(USER), conversation_id="c1", request_id="r1"),
    )
    assert len(lookup.calls) == 1
    assert "exercise_activity" in json.loads(replies[0].content)["result"]
    evidence = replies[0].evidence[0]
    partition = partition_evidence([evidence.event_item()])
    assert partition.skipped == 0
    assert len(partition.valid) == 1
    assert evidence_data(partition.valid)[0].snapshot == found.to_public().model_dump(
        mode="json"
    )


@pytest.mark.parametrize(
    ("metric", "public"),
    [
        ("steps", "steps"),
        ("heart_rate", "heart_rates"),
        ("oxygen_saturation", "oxygen_saturations"),
        ("sleep", "sleeps"),
        ("exercise", "exercises"),
    ],
)
async def test_get_metric_result_tool_metric_passes_public_code(metric, public):
    lookup = FakeLookup()
    await HealthQueryPort(FACTORY, lookup).get_metric_result(
        str(USER), metric, START, TODAY
    )
    assert lookup.calls == [(USER, public, START, TODAY, FACTORY)]


@pytest.mark.parametrize("metric", ["exercises", "heart_rates", "weight"])
async def test_get_metric_result_unsupported_metric_raises_internal_error(metric):
    lookup = FakeLookup()
    with pytest.raises(ValueError) as exc:
        await HealthQueryPort(FACTORY, lookup).get_metric_result(
            str(USER), metric, START, TODAY
        )
    # 포트 오류가 아니라서 코칭 응답은 internal 로 끝난다
    assert not isinstance(exc.value, ports.HealthDataError)
    assert lookup.calls == []


@pytest.mark.parametrize(
    "user_id",
    [
        "",
        "not-a-uuid",
        "00000000000040008000000000000001",
        "{00000000-0000-4000-8000-000000000001}",
    ],
)
async def test_get_metric_result_malformed_user_id_raises_internal_error(user_id):
    lookup = FakeLookup()
    with pytest.raises(ValueError) as exc:
        await HealthQueryPort(FACTORY, lookup).get_metric_result(
            user_id, "steps", START, TODAY
        )
    assert not isinstance(exc.value, ports.HealthDataError)
    assert lookup.calls == []


async def test_get_metric_result_lookup_result_returns_owner_and_public_snapshot():
    found = calculate(steps())
    result = await HealthQueryPort(FACTORY, FakeLookup(found)).get_metric_result(
        str(USER), "steps", START, TODAY
    )
    public = found.to_public().model_dump(mode="json")
    assert isinstance(result, ports.MetricResult)
    assert result.user_id == "00000000-0000-4000-8000-000000000001"
    # JSON 으로 비교해 int·float 구분까지 같은지 확인한다
    assert json.dumps(result.snapshot, sort_keys=True) == json.dumps(
        public, sort_keys=True
    )
    assert "user_id" not in result.snapshot
    assert result.summary.avg == 7465 and result.in_progress.value == 4210


@pytest.mark.parametrize(
    ("raised", "expected", "code"),
    [
        (
            health_errors.HealthDataNotReadyError,
            ports.HealthDataNotReadyError,
            "data_not_ready",
        ),
        (
            health_errors.HealthDataLookupError,
            ports.HealthDataUnavailableError,
            "data_unavailable",
        ),
        (
            health_errors.HealthDataForbiddenError,
            ports.HealthDataForbiddenError,
            "forbidden",
        ),
        (
            health_errors.HealthDataNotLinkedError,
            ports.HealthDataNotLinkedError,
            "user_not_linked",
        ),
    ],
)
async def test_get_metric_result_health_error_raises_port_error_without_cause(
    raised, expected, code
):
    original = raised(("heart_rates",))
    # 조회 실패의 원인에는 SQL·측정값이 들어 있을 수 있다
    original.__cause__ = RuntimeError("SELECT hr_avg FROM heart_daily -- 72.3")
    with pytest.raises(ports.HealthDataError) as exc:
        await HealthQueryPort(FACTORY, FakeLookup(original)).get_metric_result(
            str(USER), "heart_rate", START, TODAY
        )
    error = exc.value
    assert type(error) is expected
    # 준비 전(DATA_NOT_READY)과 조회 실패(LOOKUP_FAILED)는 코칭 오류 code 로 구분된다
    assert (error.code, error.metrics) == (code, ("heart_rate",))
    assert error.__cause__ is None and error.__context__ is None
    assert error.args == () and original.code not in str(error)


@pytest.mark.parametrize(
    ("public", "tool"),
    [
        ((), ()),
        (("steps", "sleeps"), ("steps", "sleep")),
        (("oxygen_saturations",), ("oxygen_saturation",)),
        (("exercises",), ("exercise",)),
    ],
)
async def test_get_metric_result_error_metrics_returns_tool_metric_names(public, tool):
    lookup = FakeLookup(health_errors.HealthDataNotReadyError(public))
    with pytest.raises(ports.HealthDataNotReadyError) as exc:
        await HealthQueryPort(FACTORY, lookup).get_metric_result(
            str(USER), "steps", START, TODAY
        )
    assert exc.value.metrics == tool


async def test_get_metric_result_unknown_error_metric_raises_internal_error():
    # 명세의 다섯 지표는 모두 변환되므로 변환표에 없는 지표 자리에 명세에 없는 코드를 넣는다
    lookup = FakeLookup(health_errors.HealthDataLookupError(("weights",)))
    with pytest.raises(ValueError) as exc:
        await HealthQueryPort(FACTORY, lookup).get_metric_result(
            str(USER), "steps", START, TODAY
        )
    assert not isinstance(exc.value, ports.HealthDataError)


async def test_get_metric_result_invalid_period_propagates_validation_error():
    # 기본 조회 함수는 DB 를 열기 전에 기간을 검증하므로 세션 팩토리를 쓰지 않는다
    with pytest.raises(ValidationError):
        await HealthQueryPort(FACTORY).get_metric_result(
            str(USER), "steps", TODAY, START
        )


async def test_get_metric_result_validation_error_from_lookup_is_not_converted():
    with pytest.raises(ValidationError) as invalid:
        calculate([], start=TODAY, end=START)
    lookup = FakeLookup(invalid.value)
    with pytest.raises(ValidationError) as exc:
        await HealthQueryPort(FACTORY, lookup).get_metric_result(
            str(USER), "steps", START, TODAY
        )
    assert exc.value is invalid.value


async def test_get_metric_result_cancelled_lookup_propagates_cancellation():
    started = asyncio.Event()

    async def blocked(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        HealthQueryPort(FACTORY, blocked).get_metric_result(
            str(USER), "steps", START, TODAY
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def summary_call() -> ToolCall:
    return ToolCall(
        "call_1", "get_metric_summary", {"metric": "steps", "period": "last_7_days"}
    )


async def test_execute_owner_result_returns_summary_and_public_evidence():
    found = calculate(steps())
    gateway = ToolGateway(HealthQueryPort(FACTORY, FakeLookup(found)), TODAY)
    ctx = CoachContext(user_id=str(USER), conversation_id="c-1", request_id="r-1")
    [reply] = await gateway.execute([summary_call()], ctx)
    assert json.loads(reply.content)["result"]["avg"]["display"] == "7,465걸음"
    [evidence] = reply.evidence
    assert evidence.snapshot == found.to_public().model_dump(mode="json")


async def test_execute_other_owner_result_raises_forbidden():
    # 소유자 확인은 어댑터가 아니라 코칭 게이트웨이가 한다
    other = calculate(steps()).model_copy(
        update={"user_id": UUID("00000000-0000-4000-8000-000000000002")}
    )
    gateway = ToolGateway(HealthQueryPort(FACTORY, FakeLookup(other)), TODAY)
    ctx = CoachContext(user_id=str(USER), conversation_id="c-1", request_id="r-1")
    with pytest.raises(ports.HealthDataForbiddenError):
        await gateway.execute([summary_call()], ctx)


def memory_session(
    day: int,
    start: tuple[int, int],
    minutes: int,
    code: int,
    kind: tuple[str, str] | None,
    counted: bool = True,
    **values,
) -> ExerciseSession:
    """10월 day일(KST)에 시작한 운동. 기준표에 없는 코드는 kind 가 None 이다."""
    begin = datetime(2026, 10, day, *start, tzinfo=KST)
    name, category = (None, None) if kind is None else kind
    end = begin + timedelta(minutes=minutes)
    return ExerciseSession(
        date(2026, 10, day),
        begin,
        end,
        minutes,
        code,
        name,
        category,
        counted,
        **values,
    )


# 코드 9999 는 기준표에 없다. 19일 걷기는 운동 시간에 들어가지 않고, 22일(오늘) 달리기는 거리 0이다
SESSIONS = [
    memory_session(
        16,
        (7, 10),
        32,
        56,
        ("달리기", "cardio"),
        calories_kcal=310.5,
        distance_m=5571.4,
        avg_heart_rate=152.0,
        max_heart_rate=171,
        avg_speed_mps=2.9,
    ),
    memory_session(17, (10, 0), 50, 0, ("기타 운동", "other")),
    memory_session(18, (10, 0), 20, 9999, None),
    memory_session(
        19,
        (8, 5),
        20,
        79,
        ("걷기", "cardio"),
        False,
        calories_kcal=58.0,
        distance_m=1103.6,
        avg_heart_rate=94.0,
        max_heart_rate=104,
        avg_speed_mps=0.92,
    ),
    memory_session(
        21,
        (19, 0),
        40,
        70,
        ("근력 운동", "strength"),
        title="하체 운동",
        calories_kcal=180.0,
        avg_heart_rate=118.5,
        max_heart_rate=142,
    ),
    memory_session(22, (9, 0), 30, 56, ("달리기", "cardio"), distance_m=0.0),
]


def backend_session(session: ExerciseSession) -> ExerciseSessionDetail:
    """같은 저장 값을 조회 함수가 읽은 세션으로 만든다.

    numeric 컬럼은 Decimal 로 읽히고, 기준표에 없는 코드는 "기타 운동"(other)으로 읽힌다.
    """

    def number(value: float | None) -> Decimal | None:
        return None if value is None else Decimal(str(value))

    return ExerciseSessionDetail(
        record_id="synthetic-exercise",
        date=session.date,
        start_at=session.start_at,
        end_at=session.end_at,
        duration_minutes=session.duration_minutes,
        exercise_type=session.exercise_type,
        type_name=session.type_name or "기타 운동",
        title=session.title,
        category=session.category or "other",
        calories_kcal=number(session.calories_kcal),
        distance_m=number(session.distance_m),
        avg_heart_rate=number(session.avg_heart_rate),
        max_heart_rate=session.max_heart_rate,
        counts_as_exercise=session.counts_as_exercise,
        avg_speed_mps=number(session.avg_speed_mps),
    )


async def test_exercise_sessions_tool_same_for_memory_and_backend_adapters():
    found = calculate(
        exercise_records([backend_session(s) for s in SESSIONS]), metric="exercises"
    )
    owner = str(USER)
    memory = InMemoryHealthData(
        steps={owner: [StepDay(s.date, 1) for s in SESSIONS]},
        exercise={owner: SESSIONS},
        sync={
            owner: {
                "exercise": SyncState(
                    STATUS.last_data_at,
                    STATUS.last_sync_status,
                    STATUS.last_success_at,
                )
            }
        },
        linked_from={owner: date(2026, 10, 9)},
        now=STATUS.checked_at,
    )
    ctx = CoachContext(user_id=owner, conversation_id="c-1", request_id="r-1")
    call = ToolCall("call_1", "get_exercise_sessions", {"period": "last_7_days"})
    port = HealthQueryPort(FACTORY, FakeLookup(found))
    [backend] = await ToolGateway(port, TODAY).execute([call], ctx)
    [fake] = await ToolGateway(memory, TODAY).execute([call], ctx)
    assert backend.content == fake.content
    # 기존 합성 어댑터에는 원본 ID가 없다. 새 DB 근거에만 ID가 추가되고 나머지는 같다.
    backend_snapshot = json.loads(json.dumps(backend.evidence[0].snapshot))
    for item in backend_snapshot["detail"]["sessions"]:
        assert item.pop("record_id") == "synthetic-exercise"
    assert backend_snapshot == fake.evidence[0].snapshot
    # 기준표에 없는 코드 9999 는 코드 0 과 같은 "기타 운동" 한 묶음이다
    types = json.loads(backend.content)["result"]["types"]
    assert [(t["type"], t["count"]) for t in types] == [
        ("기타 운동", 2),
        ("달리기", 2),
        ("근력 운동", 1),
    ]


SCORE_DAY = date(2026, 10, 20)
# 명세 5.3 예시는 2026-10-20 의 진행 중 점수라서 어댑터의 현재 시각을 그날 14:00(KST)로 둔다
SCORE_NOW = datetime(2026, 10, 20, 14, tzinfo=KST)
# API 명세 5.3 합성 응답 예시 세 개의 data 를 AI 포트 계약의 snake_case 로 옮긴 값이다.
# 구현의 변환 함수와 독립된 기대값이라 별칭이나 값이 바뀌면 실패한다
SPEC_IN_PROGRESS = {
    "date": "2026-10-20",
    "is_in_progress": True,
    "reason": None,
    "total": {"score": 67, "missing_areas": []},
    "areas": {
        "activity": {
            "score": 80,
            "grade": 1,
            "steps": {"score": 48, "value": 5400},
            "exercise": {"score": 32, "minutes": 24},
        },
        "sleep": {"score": 65, "grade": 2, "sleep_minutes": 380},
        "heart": {
            "score": 55,
            "grade": 3,
            "sleep_hr_avg": 66.0,
            "baseline_hr_avg": 58.0,
            "baseline_nights": 20,
            "check_wear": False,
        },
    },
    "computed_at": "2026-10-20T03:15:00Z",
}
SPEC_WITHOUT_SLEEP = {
    **SPEC_IN_PROGRESS,
    "total": {"score": 80, "missing_areas": ["sleep", "heart"]},
    "areas": {
        "activity": SPEC_IN_PROGRESS["areas"]["activity"],
        "sleep": {"score": None, "grade": None, "sleep_minutes": None},
        "heart": {
            "score": None,
            "grade": None,
            "sleep_hr_avg": None,
            "baseline_hr_avg": 58.0,
            "baseline_nights": 20,
            "check_wear": False,
        },
    },
}
SPEC_NO_ROW = {
    "date": "2026-10-15",
    "is_in_progress": False,
    "reason": "NO_RECORD",
    "total": {"score": None, "missing_areas": ["activity", "sleep", "heart"]},
    "areas": {
        "activity": {
            "score": None,
            "grade": None,
            "steps": {"score": None, "value": None},
            "exercise": {"score": None, "minutes": None},
        },
        "sleep": {"score": None, "grade": None, "sleep_minutes": None},
        "heart": {
            "score": None,
            "grade": None,
            "sleep_hr_avg": None,
            "baseline_hr_avg": None,
            "baseline_nights": None,
            "check_wear": None,
        },
    },
    "computed_at": None,
}


class FakeScoreLookup:
    """점수 조회 함수 자리에 넣어 호출 인자를 기록하고 정해진 결과나 예외를 반환한다."""

    def __init__(self, outcome=None) -> None:
        self.outcome = outcome
        self.calls = []

    async def __call__(self, user_id, score_date, *, session_factory):
        self.calls.append((user_id, score_date, session_factory))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class UnmappedHealthError(health_errors.HealthDataError):
    code = "UNMAPPED_HEALTH_ERROR"


def set_clock(monkeypatch, instant: datetime) -> list:
    """어댑터가 읽는 현재 시각을 instant 로 고정한다.

    시각을 읽을 때 받은 시간대를 순서대로 담은 목록을 반환한다.
    """
    reads = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            reads.append(tz)
            return instant.astimezone(tz)

    monkeypatch.setattr(health_port, "datetime", Clock, raising=False)
    return reads


def score_record(owner: UUID = USER, **changes) -> LifestyleScoreRecord:
    """명세 5.3 첫 예시와 같은 저장 행. changes 로 칸 값을 바꾼다."""
    computed_at = datetime(2026, 10, 20, 12, 15, tzinfo=KST)
    return LifestyleScoreRecord(
        **stored_score(
            end_user_id=owner, date=SCORE_DAY, computed_at=computed_at, **changes
        )
    )


def score_call() -> ToolCall:
    return ToolCall("call_1", "get_lifestyle_score", {"period": "days_ago:0"})


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, SPEC_IN_PROGRESS),
        (
            {
                "total_score": 80,
                "sleep_score": None,
                "sleep_grade": None,
                "sleep_minutes": None,
                "heart_score": None,
                "heart_grade": None,
                "sleep_hr_avg": None,
            },
            SPEC_WITHOUT_SLEEP,
        ),
    ],
)
async def test_get_lifestyle_score_stored_row_returns_spec_snake_case(
    monkeypatch, changes, expected
):
    set_clock(monkeypatch, SCORE_NOW)
    lookup = FakeScoreLookup(score_record(**changes))
    result = await HealthQueryPort(FACTORY, score_lookup=lookup).get_lifestyle_score(
        str(USER), SCORE_DAY
    )
    assert lookup.calls == [(USER, SCORE_DAY, FACTORY)]
    assert isinstance(result, ports.LifestyleScoreResult)
    assert result.user_id == "00000000-0000-4000-8000-000000000001"
    # JSON 으로 비교해 int·float 구분까지 같은지 확인한다
    assert json.dumps(result.snapshot, sort_keys=True) == json.dumps(
        expected, sort_keys=True
    )


@pytest.mark.parametrize("reason", ["BEFORE_LINK", "UNSYNCED", "NO_RECORD"])
async def test_get_lifestyle_score_missing_row_returns_reason_and_null_values(
    monkeypatch, reason
):
    set_clock(monkeypatch, SCORE_NOW)
    day = date(2026, 10, 15)
    lookup = FakeScoreLookup(
        MissingLifestyleScore(end_user_id=USER, date=day, reason=reason)
    )
    result = await HealthQueryPort(FACTORY, score_lookup=lookup).get_lifestyle_score(
        str(USER), day
    )
    assert lookup.calls == [(USER, day, FACTORY)]
    assert result.user_id == str(USER)
    assert json.dumps(result.snapshot, sort_keys=True) == json.dumps(
        SPEC_NO_ROW | {"reason": reason}, sort_keys=True
    )


@pytest.mark.parametrize(
    ("raised", "expected", "code"),
    [
        (
            health_errors.HealthDataNotReadyError,
            ports.HealthDataNotReadyError,
            "data_not_ready",
        ),
        (
            health_errors.HealthDataLookupError,
            ports.HealthDataUnavailableError,
            "data_unavailable",
        ),
        (
            health_errors.HealthDataForbiddenError,
            ports.HealthDataForbiddenError,
            "forbidden",
        ),
        (
            health_errors.HealthDataNotLinkedError,
            ports.HealthDataNotLinkedError,
            "user_not_linked",
        ),
    ],
)
async def test_get_lifestyle_score_health_error_raises_port_error_without_metrics(
    monkeypatch, raised, expected, code
):
    set_clock(monkeypatch, SCORE_NOW)
    original = raised()
    # 조회 실패의 원인에는 SQL·저장값이 들어 있을 수 있다
    original.__cause__ = RuntimeError("SELECT total_score FROM lifestyle_score -- 67")
    with pytest.raises(ports.HealthDataError) as exc:
        await HealthQueryPort(
            FACTORY, score_lookup=FakeScoreLookup(original)
        ).get_lifestyle_score(str(USER), SCORE_DAY)
    error = exc.value
    assert type(error) is expected
    # 점수 조회 실패에는 지표가 없다 (명세 5.3 오류의 metrics 도 빈 목록이다)
    assert (error.code, error.metrics) == (code, ())
    assert error.__cause__ is None and error.__context__ is None
    assert error.args == () and original.code not in str(error)


async def test_get_lifestyle_score_unmapped_health_error_propagates_unchanged(
    monkeypatch,
):
    set_clock(monkeypatch, SCORE_NOW)
    original = UnmappedHealthError()
    with pytest.raises(UnmappedHealthError) as exc:
        await HealthQueryPort(
            FACTORY, score_lookup=FakeScoreLookup(original)
        ).get_lifestyle_score(str(USER), SCORE_DAY)
    # 포트 오류가 아니라서 코칭 응답은 internal 로 끝난다
    assert exc.value is original


def invalid_score_error() -> ValidationError:
    """저장 계약 밖의 값으로 만든 합성 ValidationError."""
    with pytest.raises(ValidationError) as invalid:
        LifestyleScoreRecord(**stored_score(total_score=101))
    return invalid.value


def failing_conversion(monkeypatch, error: BaseException) -> None:
    """어댑터의 응답 변환 함수를 error 를 발생시키는 함수로 바꾼다."""

    def convert(record, *, today):
        raise error

    monkeypatch.setattr(health_port, "lifestyle_score_response", convert)


@pytest.mark.parametrize(
    "make_error",
    [
        invalid_score_error,
        lambda: PydanticSerializationError("synthetic-private 67"),
    ],
    ids=["validation", "serialization"],
)
async def test_get_lifestyle_score_conversion_error_raises_unavailable_without_cause(
    monkeypatch, make_error
):
    set_clock(monkeypatch, SCORE_NOW)
    failing_conversion(monkeypatch, make_error())
    with pytest.raises(ports.HealthDataError) as exc:
        await HealthQueryPort(
            FACTORY, score_lookup=FakeScoreLookup(score_record())
        ).get_lifestyle_score(str(USER), SCORE_DAY)
    error = exc.value
    # 홈 점수 API 가 503 LOOKUP_FAILED 로 바꾸는 실패와 같은 경계다
    assert type(error) is ports.HealthDataUnavailableError
    assert (error.code, error.metrics) == ("data_unavailable", ())
    assert error.__cause__ is None and error.__context__ is None
    assert error.args == () and "synthetic-private" not in str(error)


async def test_get_lifestyle_score_other_conversion_error_propagates_unchanged(
    monkeypatch,
):
    set_clock(monkeypatch, SCORE_NOW)
    original = KeyError("excellent")
    failing_conversion(monkeypatch, original)
    with pytest.raises(KeyError) as exc:
        await HealthQueryPort(
            FACTORY, score_lookup=FakeScoreLookup(score_record())
        ).get_lifestyle_score(str(USER), SCORE_DAY)
    # 홈 점수 API 도 이 예외는 바꾸지 않고 500 으로 끝난다. 코칭에서는 internal 이다
    assert exc.value is original


@pytest.mark.parametrize(
    ("instant", "day"),
    [
        # KST 10월 20일 23:59:59 에 10월 21일은 아직 미래다
        (datetime(2026, 10, 20, 14, 59, 59, tzinfo=UTC), date(2026, 10, 21)),
        (SCORE_NOW, date(2027, 10, 20)),
    ],
)
async def test_get_lifestyle_score_future_day_raises_internal_error(
    monkeypatch, instant, day
):
    set_clock(monkeypatch, instant)
    lookup = FakeScoreLookup()
    with pytest.raises(ValueError) as exc:
        await HealthQueryPort(FACTORY, score_lookup=lookup).get_lifestyle_score(
            str(USER), day
        )
    # 라우터의 422 와 같은 경계다. 포트 오류가 아니라서 코칭 응답은 internal 로 끝난다
    assert not isinstance(exc.value, ports.HealthDataError)
    assert lookup.calls == []


@pytest.mark.parametrize(
    ("instant", "day", "in_progress"),
    [
        (datetime(2026, 10, 20, 14, 59, 59, tzinfo=UTC), date(2026, 10, 20), True),
        # UTC 15:00 은 KST 다음 날 0시다
        (datetime(2026, 10, 20, 15, tzinfo=UTC), date(2026, 10, 21), True),
        (datetime(2026, 10, 20, 15, tzinfo=UTC), date(2026, 10, 20), False),
    ],
)
async def test_get_lifestyle_score_kst_today_reads_clock_once(
    monkeypatch, instant, day, in_progress
):
    reads = set_clock(monkeypatch, instant)
    lookup = FakeScoreLookup(
        MissingLifestyleScore(end_user_id=USER, date=day, reason="UNSYNCED")
    )
    result = await HealthQueryPort(FACTORY, score_lookup=lookup).get_lifestyle_score(
        str(USER), day
    )
    # 미래 날짜 검사와 is_in_progress 에 같은 KST 오늘을 쓴다
    assert reads == [KST]
    assert lookup.calls == [(USER, day, FACTORY)]
    assert result.is_in_progress is in_progress


@pytest.mark.parametrize(
    "user_id", ["not-a-uuid", "00000000-0000-4000-8000-00000000000A"]
)
async def test_get_lifestyle_score_malformed_user_id_raises_internal_error(
    monkeypatch, user_id
):
    set_clock(monkeypatch, SCORE_NOW)
    lookup = FakeScoreLookup()
    with pytest.raises(ValueError) as exc:
        await HealthQueryPort(FACTORY, score_lookup=lookup).get_lifestyle_score(
            user_id, SCORE_DAY
        )
    assert not isinstance(exc.value, ports.HealthDataError)
    assert lookup.calls == []


async def test_execute_owner_score_returns_result_without_evidence(monkeypatch):
    set_clock(monkeypatch, SCORE_NOW)
    port = HealthQueryPort(FACTORY, score_lookup=FakeScoreLookup(score_record()))
    ctx = CoachContext(user_id=str(USER), conversation_id="c-1", request_id="r-1")
    [reply] = await ToolGateway(port, SCORE_DAY).execute([score_call()], ctx)
    assert json.loads(reply.content)["result"]["total"]["score"] == 67
    # 근거 검증이 지표 원형만 받아 점수 결과는 근거를 만들지 않는다
    assert reply.evidence == ()


async def test_execute_other_owner_score_raises_forbidden(monkeypatch):
    set_clock(monkeypatch, SCORE_NOW)
    other = UUID("00000000-0000-4000-8000-000000000002")
    port = HealthQueryPort(FACTORY, score_lookup=FakeScoreLookup(score_record(other)))
    # 어댑터는 조회한 행의 소유자를 그대로 전달하고 소유자 확인은 게이트웨이가 한다
    assert (await port.get_lifestyle_score(str(USER), SCORE_DAY)).user_id == str(other)
    ctx = CoachContext(user_id=str(USER), conversation_id="c-1", request_id="r-1")
    with pytest.raises(ports.HealthDataForbiddenError):
        await ToolGateway(port, SCORE_DAY).execute([score_call()], ctx)
