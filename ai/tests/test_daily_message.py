"""오늘의 한마디(API 명세 5.5)의 포트 계약, 메모리 어댑터, 결과 처리, 게이트웨이."""

import asyncio
import copy
import inspect
import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from coach_ai.adapters.memory import DailyMessageDay, InMemoryHealthData, SyncState
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall, ToolRequest
from coach_ai.agent.nodes.tools import TOOL_STATUS
from coach_ai.ports import (
    DailyMessageResult,
    HealthDataForbiddenError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    HealthDataPort,
    HealthDataUnavailableError,
    ResultContractError,
)
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import DENIED_MESSAGE, ToolGateway
from coach_ai.tools import daily_message
from coach_ai.tools.daily_message import get_daily_message
from coach_ai.tools.handlers import HANDLERS
from coach_ai.tools.schemas import TOOL_INPUTS, GetDailyMessage

KST = timezone(timedelta(hours=9))
TODAY = date(2026, 10, 29)
NOW = datetime(2026, 10, 29, 15, 30, tzinfo=KST)
CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
MESSAGE_INPUTS = ("steps", "exercise", "sleep")
# LLM 에 지시하는 문장 끝. 한마디 결과에는 사실만 적는다
INSTRUCTION_ENDING = re.compile(r"(주세요|한다|않는다)$")
# 홈 화면 카드 문구. 사용자에게 하는 말을 그대로 옮기는 칸이라 문장 끝 검사에서 뺀다
CARD_FIELDS = ("title", "description")
# 아직 정해지지 않아 이유 문장에 넣지 않는 기준값 (걸음 7,000걸음, 인정 운동 30분, 수면 점수 80점)
THRESHOLDS = ("7,000", "7000", "30분", "80점")
AT_15 = "2026-10-29T06:00:00Z"
NOTE = (
    "홈 화면 오늘의 한마디 카드와 같은 문구다. "
    "오늘 기록에 정해진 규칙을 적용해 고르며, 기록이 동기화되면 바뀔 수 있다. "
    "지난 날짜의 오늘의 한마디만 저장되지 않아 조회할 수 없고, "
    "걸음·수면 같은 지난 기록은 한마디와 별개로 조회할 수 있다"
)

# backend/docs/daily-message-api-verification.md 의 합성 응답 예시 네 개(연동 쪽 선택 함수와 공개 변환기로 만든 값)를
# 포트가 반환하는 snake_case 로 옮기고 공개 응답에 없는 used_metrics 를 더한 값이다
READY = {
    "date": "2026-10-29",
    "status": "READY",
    "code": "BALANCED_RECORDS",
    "area": None,
    "title": "오늘은 나에게 칭찬 한마디 건네볼까요?",
    "description": "수면과 활동 기록이 함께 쌓였어요. 오늘 남긴 작은 성취들을 돌아보세요.",
    "data_as_of": {"steps": AT_15, "exercises": AT_15, "sleeps": AT_15},
    "score_computed_at": "2026-10-29T06:10:00Z",
    "notices": [],
    "used_metrics": ["steps", "exercises", "sleeps"],
}
PARTIAL = {
    "date": "2026-10-29",
    "status": "PARTIAL",
    "code": "ACTIVE_DAY",
    "area": "activity",
    "title": "차곡차곡 쌓은 움직임, 뿌듯해해도 좋아요",
    "description": "운동과 걸음 기록에 오늘의 움직임이 담겼어요.",
    "data_as_of": {"steps": AT_15, "exercises": AT_15, "sleeps": None},
    "score_computed_at": None,
    "notices": [{"metric": "sleeps", "code": "LOOKUP_FAILED", "message": "수면 기록은 불러오지 못했어요."}],
    "used_metrics": ["steps", "exercises"],
}
EMPTY = {
    "date": "2026-10-29",
    "status": "EMPTY",
    "code": "NO_RECORDS",
    "area": None,
    "title": "오늘은 기록 없이도 내 하루를 돌아볼까요?",
    "description": "지금 확인할 수 있는 활동·수면 기록은 없어요.",
    "data_as_of": {"steps": AT_15, "exercises": AT_15, "sleeps": AT_15},
    "score_computed_at": None,
    "notices": [],
    "used_metrics": [],
}
WAITING = {
    **EMPTY,
    "status": "WAITING",
    "code": "PENDING_RECORDS",
    "title": "오늘의 기록을 아직 확인하지 못했어요",
    "description": "괜찮다면 앱의 동기화 상태를 확인해 주세요.",
}
SPEC_EXAMPLES = {"READY": READY, "PARTIAL": PARTIAL, "EMPTY": EMPTY, "WAITING": WAITING}


def at(hh: int, mm: int, day: int = 29) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=KST)


def message(**changes) -> DailyMessageDay:
    """READY 예시와 같은 저장 값."""
    values = dict(
        date=TODAY, status="READY", code="BALANCED_RECORDS", area=None, title=READY["title"],
        description=READY["description"], used_metrics=("steps", "exercises", "sleeps"),
        data_as_of={"steps": at(15, 0), "exercises": at(15, 0), "sleeps": at(15, 0)}, score_computed_at=at(15, 10),
    )  # fmt: skip
    return DailyMessageDay(**{**values, **changes})


def health(*rows: DailyMessageDay, inputs: tuple[str, ...] = MESSAGE_INPUTS) -> InMemoryHealthData:
    """세 지표의 동기화 기준 시각이 10월 29일 15:00 인 회원."""
    return InMemoryHealthData(
        daily_messages={"u1": list(rows)},
        sync={"u1": {metric: SyncState(at(15, 0)) for metric in inputs}},
        now=NOW,
    )


class SnapshotPort(InMemoryHealthData):
    """정해 둔 응답과 소유자를 그대로 반환한다."""

    def __init__(self, snapshot: dict, owner: str = "u1") -> None:
        super().__init__(now=NOW)
        self.snapshot = snapshot
        self.owner = owner

    async def get_daily_message(self, user_id, day, *, request_id):
        self.calls.append(("get_daily_message", user_id, day, request_id))
        return DailyMessageResult(self.owner, self.snapshot)


async def run(port: InMemoryHealthData, today: date = TODAY, request_id: str = "r-1"):
    return await get_daily_message(GetDailyMessage(), ToolRequest("u1", today, port, request_id=request_id))


def _strings(value: object, skip: tuple[str, ...] = ()) -> list[str]:
    if isinstance(value, dict):
        return [s for key, item in value.items() if key not in skip for s in _strings(item, skip)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item, skip)]
    return [value] if isinstance(value, str) else []


def _sentences(text: str) -> list[str]:
    return [part.strip(" .") for part in re.split(r"\.\s", text) if part.strip(" .")]


def test_port_takes_question_date_and_request_id_by_keyword():
    parameters = inspect.signature(HealthDataPort.get_daily_message).parameters
    assert list(parameters) == ["self", "user_id", "day", "request_id"]
    assert parameters["request_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert isinstance(InMemoryHealthData(), HealthDataPort)


@pytest.mark.parametrize("name", list(SPEC_EXAMPLES))
def test_result_reads_spec_examples(name):
    example = SPEC_EXAMPLES[name]
    result = DailyMessageResult("u1", example)
    assert (result.user_id, result.date, result.status, result.code) == (
        "u1",
        TODAY,
        example["status"],
        example["code"],
    )
    assert (result.title, result.description) == (example["title"], example["description"])
    assert result.used_metrics == tuple(example["used_metrics"])
    assert [(n.metric, n.code, n.message) for n in result.notices] == [
        (n["metric"], n["code"], n["message"]) for n in example["notices"]
    ]
    assert result.data_as_of.exercises == (None if example["data_as_of"]["exercises"] is None else at(15, 0))
    assert result.snapshot == example


def test_result_keeps_snapshot_as_copy():
    raw = copy.deepcopy(PARTIAL)
    result = DailyMessageResult("u1", raw)
    raw["notices"].clear()
    raw["title"] = "바뀐 문구"
    assert result.snapshot == PARTIAL
    assert result.notices[0].metric == "sleeps"


def test_unknown_message_code_is_read_as_text():
    # 공개 계약에서 문구 코드는 문자열이라 새 코드가 생겨도 읽는다
    assert DailyMessageResult("u1", {**READY, "code": "NEW_CODE"}).code == "NEW_CODE"


@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": "u1"},
        {"status": "DONE"},
        {"area": "heart"},
        {"used_metrics": ["heart_rates"]},
        {"notices": [{"metric": "heart_rates", "code": "LOOKUP_FAILED", "message": "-"}]},
        {"notices": [{"metric": "sleeps", "code": "TIMEOUT", "message": "-"}]},
        {"data_as_of": {"steps": "2026-10-29T15:00:00", "exercises": None, "sleeps": None}},
        {"score_computed_at": "2026-10-29T15:10:00"},
    ],
)
def test_result_outside_contract_is_contract_error(changes):
    with pytest.raises(ResultContractError):
        DailyMessageResult("u1", {**READY, **changes})


@pytest.mark.parametrize("field", ["used_metrics", "notices", "data_as_of", "score_computed_at", "title"])
def test_result_missing_field_is_contract_error(field):
    with pytest.raises(ResultContractError):
        DailyMessageResult("u1", {k: v for k, v in READY.items() if k != field})


async def test_memory_returns_stored_message_in_spec_form():
    result = await health(message()).get_daily_message("u1", TODAY, request_id="r-1")
    assert result.user_id == "u1"
    assert result.snapshot == READY


async def test_memory_records_date_and_request_id_of_each_call():
    port = health(message())
    await port.get_daily_message("u1", TODAY, request_id="r-9")
    assert port.calls == [("get_daily_message", "u1", TODAY, "r-9")]


async def test_memory_rejects_future_day_and_unlinked_member():
    with pytest.raises(ValueError):
        await health(message()).get_daily_message("u1", TODAY + timedelta(days=1), request_id="r-1")
    unlinked = health(message())
    unlinked.not_linked.add("u1")
    with pytest.raises(HealthDataNotLinkedError):
        await unlinked.get_daily_message("u1", TODAY, request_id="r-1")


@pytest.mark.parametrize(
    "changes",
    [
        {"data_as_of": {"steps": datetime(2026, 10, 29, 15, 0)}},
        {"score_computed_at": datetime(2026, 10, 29, 15, 10)},
    ],
)
def test_memory_message_rejects_times_without_timezone(changes):
    # 시간대 없는 시각은 RFC 3339 UTC 로 바꿀 때 실행 환경의 시간대로 읽혀 값이 달라진다
    with pytest.raises(ValueError):
        message(**changes)


async def test_memory_without_row_needs_sync_state_of_three_metrics():
    # 심박 동기화 상태는 한마디 입력이 아니다
    port = health(inputs=("steps", "sleep", "heart_rate"))
    with pytest.raises(HealthDataNotReadyError) as missing:
        await port.get_daily_message("u1", TODAY, request_id="r-1")
    assert missing.value.metrics == ("exercise",)


async def test_memory_without_row_returns_no_records_default():
    # 그날 행이 없는 회원은 소개할 기록이 없을 때의 응답(NO_RECORDS)을 받는다. 다른 날 행은 쓰지 않는다
    port = health(message(date=TODAY - timedelta(days=1)))
    result = await port.get_daily_message("u1", TODAY, request_id="r-1")
    assert result.snapshot == EMPTY


async def test_sample_today_message_is_steps_progress_chosen_from_today_records():
    now = datetime(2026, 10, 22, 14, 0, tzinfo=KST)
    today = now.date()
    sample = sample_health_data("u1", now)
    result = await sample.get_daily_message("u1", today, request_id="r-1")
    assert (result.status, result.code, result.area, result.used_metrics) == (
        "READY",
        "STEPS_PROGRESS",
        "activity",
        ("steps",),
    )
    assert (result.score_computed_at, result.notices) == (None, ())
    # 명세 5.5 우선순위에서 STEPS_PROGRESS 가 되는 오늘 기록인지 확인한다
    steps, exercise, sleep = [await sample.get_metric_result("u1", metric, today, today) for metric in MESSAGE_INPUTS]
    assert 0 < steps.days[0].value < 7000 and not steps.days[0].missing
    assert (exercise.days[0].value, exercise.days[0].missing) == (0, False)
    assert (sleep.days[0].missing, sleep.days[0].reason) == (True, "UNSYNCED")
    assert (result.data_as_of.steps, result.data_as_of.exercises, result.data_as_of.sleeps) == (
        steps.data_status.last_data_at,
        exercise.data_status.last_data_at,
        sleep.data_status.last_data_at,
    )


async def test_sample_today_result_states_card_basis_and_record_times():
    now = datetime(2026, 10, 22, 14, 0, tzinfo=KST)
    outcome = await run(sample_health_data("u1", now), today=now.date())
    assert (outcome.status, outcome.evidence) == ("ok", ())
    assert outcome.payload == {
        "date": "2026-10-22",
        "display": "오늘 (10월 22일)",
        "title": "한 걸음씩, 내 페이스대로 가볼까요?",
        "description": "오늘 남겨진 걸음도 소중해요. 움직이고 싶은 때에 편한 만큼 이어가 보세요.",
        "basis": "오늘 걸음 기록으로 고른 문구다",
        "recorded_at": "걸음 13:53, 운동 10월 21일 19:40, 수면 10월 21일 07:25",
        "note": NOTE,
    }
    assert outcome.to_message([]) == {"evidence_ids": [], "result": outcome.payload}


@pytest.mark.parametrize(
    ("example", "basis"),
    [
        (EMPTY, "오늘 한마디로 소개할 활동·수면 기록이 없어 나온 안내 문구다"),
        (WAITING, "오늘 기록이 아직 동기화되지 않아 나온 안내 문구다"),
    ],
)
async def test_guide_card_results_say_why_the_guide_was_chosen(example, basis):
    payload = (await run(SnapshotPort(example))).payload
    assert payload == {
        "date": "2026-10-29",
        "display": "오늘 (10월 29일)",
        "title": example["title"],
        "description": example["description"],
        "basis": basis,
        "recorded_at": "걸음 15:00, 운동 15:00, 수면 15:00",
        "note": NOTE,
    }


async def test_message_chosen_with_sleep_score_names_score_and_its_time():
    payload = (await run(SnapshotPort(READY))).payload
    assert payload["basis"] == "오늘 걸음·운동·수면 기록과 수면 점수로 고른 문구다"
    assert payload["recorded_at"] == "걸음 15:00, 운동 15:00, 수면 15:00, 수면 점수 계산 15:10"
    assert "notices" not in payload


@pytest.mark.parametrize(
    ("notices", "used", "facts"),
    [
        (
            [
                {"metric": "steps", "code": "SYNC_FAILED", "message": "-"},
                {"metric": "sleeps", "code": "LOOKUP_FAILED", "message": "-"},
            ],
            ["steps", "exercises"],
            [
                "걸음 기록은 최근 동기화에 실패해 이전에 저장된 기록으로 골랐다",
                "수면 기록은 불러오지 못해 문구를 고를 때 쓰지 않았다",
            ],
        ),
        (
            [
                {"metric": "exercises", "code": "DATA_NOT_READY", "message": "-"},
                {"metric": "sleeps", "code": "SYNC_FAILED", "message": "-"},
            ],
            ["steps"],
            ["운동 기록은 아직 준비 중이라 문구를 고를 때 쓰지 않았다", "수면 기록은 최근 동기화에 실패했다"],
        ),
    ],
)
async def test_notices_become_facts_about_records_used_for_the_message(notices, used, facts):
    snapshot = {**PARTIAL, "notices": notices, "used_metrics": used}
    payload = (await run(SnapshotPort(snapshot))).payload
    assert payload["notices"] == facts
    # 화면용 안내 문장은 LLM 에 전달하지 않는다
    assert "-" not in _strings(payload)


async def test_partial_spec_example_keeps_only_times_of_read_records():
    payload = (await run(SnapshotPort(PARTIAL))).payload
    assert payload["basis"] == "오늘 걸음·운동 기록으로 고른 문구다"
    assert payload["recorded_at"] == "걸음 15:00, 운동 15:00"
    assert payload["notices"] == ["수면 기록은 불러오지 못해 문구를 고를 때 쓰지 않았다"]


async def test_unknown_code_is_explained_by_used_records():
    payload = (await run(SnapshotPort({**READY, "code": "NEW_CODE", "used_metrics": ["sleeps"],
                                       "score_computed_at": None}))).payload  # fmt: skip
    assert payload["basis"] == "오늘 수면 기록으로 고른 문구다"


async def test_result_for_another_day_is_contract_error():
    with pytest.raises(ResultContractError):
        await run(SnapshotPort(READY), today=TODAY + timedelta(days=1))


async def test_handler_passes_question_date_and_request_id_to_port():
    port = health(message())
    await run(port, request_id="r-9")
    assert port.calls == [("get_daily_message", "u1", TODAY, "r-9")]


async def test_condition_sentence_comes_only_from_condition_table(monkeypatch):
    # 문구를 고르는 기준값은 정해지면 조건 표 한 곳에 더한다
    assert daily_message.CODE_CONDITIONS == {}
    assert "condition" not in (await run(SnapshotPort(READY))).payload
    monkeypatch.setitem(daily_message.CODE_CONDITIONS, "BALANCED_RECORDS", "수면 점수와 운동·걸음이 모두 기준에 닿았다")
    payload = (await run(SnapshotPort(READY))).payload
    assert payload["condition"] == "수면 점수와 운동·걸음이 모두 기준에 닿았다"
    assert "condition" not in (await run(SnapshotPort(PARTIAL))).payload


def _results_of_all_cases() -> list[dict]:
    snapshots = [
        *SPEC_EXAMPLES.values(),
        {**PARTIAL, "notices": [{"metric": m, "code": c, "message": "-"} for m in ("steps", "exercises", "sleeps")
                                for c in ("LOOKUP_FAILED", "DATA_NOT_READY", "SYNC_FAILED")]},
        {**READY, "code": "NEW_CODE"},
        {**READY, "code": "STEPS_MILESTONE", "title": "한 걸음씩 쌓아온 오늘, 잘하셨어요",
         "description": "오늘 기록이 7,000보를 채웠어요. 더 채우기보다 지금까지의 걸음도 칭찬해 주세요.",
         "used_metrics": ["steps"], "score_computed_at": None},
    ]  # fmt: skip
    return [daily_message.daily_message_result(DailyMessageResult("u1", s), TODAY).model_dump(
        mode="json", exclude_none=True) for s in snapshots]  # fmt: skip


def test_results_state_facts_without_instructions_to_the_model():
    found = [
        s for payload in _results_of_all_cases() for text in _strings(payload, CARD_FIELDS) for s in _sentences(text)
    ]
    assert found
    assert [s for s in found if INSTRUCTION_ENDING.search(s)] == []
    # 카드 문구는 지시처럼 끝나기도 하지만 사용자에게 하는 말이라 그대로 옮긴다
    cards = [s for payload in _results_of_all_cases() for key in CARD_FIELDS for s in _sentences(payload[key])]
    assert any(INSTRUCTION_ENDING.search(s) for s in cards)


def test_results_have_no_threshold_values_outside_card_text():
    for payload in _results_of_all_cases():
        for text in _strings(payload, CARD_FIELDS):
            assert not any(value in text for value in THRESHOLDS), text


def test_tool_is_registered_with_handler_and_status():
    assert TOOL_INPUTS["get_daily_message"] is GetDailyMessage
    assert HANDLERS["get_daily_message"] is get_daily_message
    assert TOOL_STATUS["get_daily_message"] == "오늘의 한마디를 확인하고 있어요"


def daily_call(args: dict | None = None, call_id: str = "call-daily") -> ToolCall:
    return ToolCall(call_id, "get_daily_message", {} if args is None else args)


async def test_gateway_runs_argument_free_call_without_evidence():
    audit = MemoryAuditSink()
    port = health(message())
    [reply] = await ToolGateway(port, TODAY, audit=audit).execute([daily_call()], CTX)
    assert reply.evidence == ()
    content = json.loads(reply.content)
    assert content["evidence_ids"] == [] and content["result"]["title"] == READY["title"]
    # 포트가 받는 요청 ID 는 CoachContext 의 값이다
    assert port.calls == [("get_daily_message", "u1", TODAY, "r-1")]
    [record] = audit.records()
    assert (record.tool_name, record.args, record.reason, record.result_count, record.evidence_ids) == (
        "get_daily_message",
        {},
        None,
        1,
        (),
    )


async def test_gateway_denies_arguments_and_repeated_call():
    audit = MemoryAuditSink()
    calls = [daily_call({"period": "days_ago:0"}, "a"), daily_call(call_id="b"), daily_call(call_id="c")]
    replies = await ToolGateway(health(message()), TODAY, audit=audit).execute(calls, CTX)
    assert [r.content == DENIED_MESSAGE for r in replies] == [True, False, True]
    assert [r.reason for r in audit.records()] == ["extra_field", None, "duplicate"]


async def test_gateway_failures_carry_message_metrics():
    class SlowPort(InMemoryHealthData):
        async def get_daily_message(self, user_id, day, *, request_id):
            await asyncio.sleep(0.2)

    with pytest.raises(HealthDataUnavailableError) as slow:
        await ToolGateway(SlowPort(now=NOW), TODAY, timeout=0.05).execute([daily_call()], CTX)
    with pytest.raises(HealthDataForbiddenError) as other:
        await ToolGateway(SnapshotPort(READY, owner="u2"), TODAY).execute([daily_call()], CTX)
    # 홈 한마디 API 가 응답 변환 실패를 세 지표의 조회 실패로 보내는 것과 같은 지표다
    assert slow.value.metrics == other.value.metrics == ("steps", "exercise", "sleep")
    with pytest.raises(HealthDataNotReadyError) as missing:
        await ToolGateway(health(inputs=("steps", "exercise")), TODAY).execute([daily_call()], CTX)
    assert missing.value.metrics == ("sleep",)
