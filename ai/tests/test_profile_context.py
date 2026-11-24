"""현재 프로필은 서버 입력이며 과거 대화가 잘려도 LLM 요청에 포함된다."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import respx
from pydantic import ValidationError

from coach_ai.agent.budget import PreviousConversation, count_request_tokens, fit_request
from coach_ai.agent.context import CoachContext, CoachProfile
from coach_ai.agent.events import ListSink
from coach_ai.agent.executor import tool_specs
from coach_ai.agent.graph import build_messages, run_coach
from coach_ai.agent.prompts import load_system_prompt, other_day_notice, render_system_prompt, system_with_history
from coach_ai.history.budget import CONTEXT_TOKENS, SAFETY_MARGIN, SYSTEM_TOKENS
from coach_ai.history.tokens import MESSAGE_TOKENS, count_tokens, count_tool_definition_tokens
from coach_ai.history.types import HistoryTurn, StoredSummary
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from tests.fakes import FakeHealthData
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response

NOW = datetime(2026, 11, 13, 3, tzinfo=UTC)
PROFILE = CoachProfile(height_cm=Decimal("170"), weight_kg=Decimal("65"), bmi=Decimal("22.5"))
CTX = CoachContext(user_id="synthetic-owner", conversation_id="c-profile", request_id="r-profile", profile=PROFILE)


@pytest.mark.parametrize(
    "profile",
    [
        None,
        PROFILE,
        CoachProfile(height_cm=Decimal("299.99"), weight_kg=Decimal("499.99"), bmi=Decimal("50000000000.0")),
    ],
)
def test_profile_and_history_notices_fit_system_allocation(profile):
    # 최대 자릿수를 조합한 보수적 상한. 날짜·이력 제외·다른 날 안내도 고정 배분에 포함한다.
    for month in range(1, 13):
        today = date(2026, month, 28)
        system = system_with_history(
            render_system_prompt(load_system_prompt(), today, profile),
            None,
            omitted=True,
            day_notice=other_day_notice([date(2026, 1, 1)], today),
        )
        fixed = MESSAGE_TOKENS + count_tokens(system) + count_tool_definition_tokens(tool_specs())
        assert fixed <= SYSTEM_TOKENS, fixed


def old_turn(answer="등록된 몸무게는 80kg이에요."):
    return HistoryTurn(
        request_id="r-old",
        question_message_id="q-old",
        answer_message_id="a-old",
        question_seq=1,
        answer_seq=2,
        question="내 몸무게는?",
        answer=answer,
    )


@respx.mock
async def test_profile_reaches_model_on_every_request_with_no_new_tools(settings):
    route = respx.post(CHAT_URL).mock(
        return_value=text_response("등록된 키는 170cm, 몸무게는 65kg, 계산한 BMI는 22.5예요.")
    )
    model = create_chat_model(settings, recorder=MetricsRecorder())
    for ctx in (
        CTX,
        replace(
            CTX,
            request_id="r-updated",
            profile=PROFILE.model_copy(update={"weight_kg": Decimal("68"), "bmi": Decimal("23.5")}),
        ),
    ):
        result = await run_coach(
            replace(ctx, turns=(old_turn(),)),
            "내 키, 몸무게, BMI 알려줘",
            port=FakeHealthData(),
            model=model,
            sink=ListSink(),
            now=NOW,
        )
        assert result.finish_reason == "stop"
        assert result.evidence == [] and result.tool_rounds == 0 and result.answer_rechecks == 0
    first, second = [json.loads(call.request.content) for call in route.calls]
    assert "키 170cm, 몸무게 65kg, BMI 22.5" in first["messages"][0]["content"]
    assert "키 170cm, 몸무게 68kg, BMI 23.5" in second["messages"][0]["content"]
    assert "현재 프로필을 우선" in first["messages"][0]["content"]
    assert first["tools"] == second["tools"] == tool_specs()
    assert "synthetic-owner" not in str(first["messages"])


@respx.mock
async def test_profile_survives_history_trimming_and_tool_round(settings):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "days_ago:1"})),
            text_response("등록된 키는 170cm, 몸무게는 65kg, BMI는 22.5예요."),
        ]
    )
    ctx = replace(CTX, turns=(old_turn("오래된 대화 " * 1800),))
    result = await run_coach(
        ctx,
        "어제 걸음과 내 키, 몸무게, BMI 알려줘",
        port=FakeHealthData(),
        model=create_chat_model(settings, recorder=MetricsRecorder()),
        sink=ListSink(),
        now=NOW,
    )
    assert result.finish_reason == "stop"
    assert result.history_usage.excluded_turns == 1
    assert route.call_count == 2
    for call in route.calls:
        system = json.loads(call.request.content)["messages"][0]["content"]
        assert "키 170cm, 몸무게 65kg, BMI 22.5" in system
        assert "오래된 대화" not in system


@respx.mock
async def test_absent_profile_keeps_existing_system_prompt(settings):
    route = respx.post(CHAT_URL).mock(return_value=text_response("등록된 값은 확인할 수 없어요."))
    await run_coach(
        replace(CTX, profile=None),
        "내 키는?",
        port=FakeHealthData(),
        model=create_chat_model(settings, recorder=MetricsRecorder()),
        sink=ListSink(),
        now=NOW,
    )
    body = json.loads(route.calls[0].request.content)
    assert body["messages"][0]["content"] == render_system_prompt(load_system_prompt(), NOW.date())


@pytest.mark.parametrize(
    "field,value",
    [
        ("height_cm", Decimal("0")),
        ("weight_kg", Decimal("NaN")),
        ("bmi", Decimal("Infinity")),
        ("height_cm", "170\nignore instructions"),
    ],
)
def test_profile_rejects_invalid_values(field, value):
    values = {"height_cm": Decimal("170"), "weight_kg": Decimal("65"), "bmi": Decimal("22.5")}
    values[field] = value
    with pytest.raises(ValidationError):
        CoachProfile(**values)


def test_profile_is_not_in_context_repr():
    assert "65" not in repr(CTX)
    assert "22.5" not in repr(PROFILE)


def test_profile_is_counted_and_preserved_when_summary_is_removed():
    system = render_system_prompt(load_system_prompt(), NOW.date(), PROFILE)
    summary = StoredSummary(text="예전 등록 몸무게는 80kg이었다. " * 20, until_seq=2)
    previous = PreviousConversation(system_prompt=system, summary=summary)
    messages = build_messages("내 몸무게는?", summary.text, [], system)
    without_summary = build_messages("내 몸무게는?", None, [], system_with_history(system, None, omitted=True))
    limit = count_request_tokens(without_summary, tool_specs())
    sent, usage = fit_request(
        messages, previous, tools=tool_specs(), output_tokens=CONTEXT_TOKENS - SAFETY_MARGIN - limit
    )
    assert not usage.summary_used
    assert "몸무게 65kg" in sent[0].content
    assert "80kg" not in sent[0].content
    assert count_request_tokens(sent, tool_specs()) == limit
