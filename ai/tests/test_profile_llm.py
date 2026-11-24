"""실제 사내 LLM 프로필 회귀. 합성 값만 전송하고 원문은 git 제외 경로에 저장한다."""

import json
import re
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from coach_ai.agent.context import CoachProfile
from coach_ai.agent.events import ListSink
from coach_ai.agent.graph import run_coach
from coach_ai.agent.prompts import DEFAULT_PROMPT_VERSION, load_system_prompt, render_system_prompt
from coach_ai.config import Settings
from coach_ai.history.types import StoredSummary
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from tests.fakes import FakeHealthData
from tests.test_profile_context import CTX, NOW, old_turn


@pytest.fixture(scope="module")
def profile_live_output():
    path = Path("eval/results") / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + "-profile392")
    path.mkdir(parents=True)
    return path


@pytest.mark.llm
@pytest.mark.parametrize("attempt", range(3))
@pytest.mark.parametrize(
    "case", ["height", "weight", "bmi", "all", "updated", "decimal", "trimmed", "mixed", "other", "fat"]
)
async def test_current_profile_answer(case, attempt, profile_live_output):
    ctx = CTX
    question = {
        "height": "내 키가 몇 cm야?",
        "weight": "내 몸무게가 몇 kg야?",
        "bmi": "내 BMI 알려줘.",
        "all": "내 키, 몸무게, BMI 알려줘.",
        "updated": "프로필 수정했어. 지금 내 키, 몸무게, BMI 다시 알려줘.",
        "decimal": "내 키, 몸무게, BMI 알려줘.",
        "trimmed": "현재 등록된 내 키, 몸무게, BMI 알려줘.",
        "mixed": "어제 걸음 수와 내 키, 몸무게, BMI 알려줘.",
        "other": "친구의 키, 몸무게, BMI 알려줘.",
        "fat": "내 체지방률도 알려줘.",
    }[case]
    expected = {"height": ("170cm",), "weight": ("65kg",), "bmi": ("22.5",)}.get(case, ("170cm", "65kg", "22.5"))
    if case == "updated":
        ctx = replace(
            ctx,
            profile=CoachProfile(height_cm=Decimal("170"), weight_kg=Decimal("68"), bmi=Decimal("23.5")),
            summary=StoredSummary(text="이전에 등록한 몸무게는 65kg, BMI는 22.5였다.", until_seq=2),
            turns=(replace(old_turn("등록된 몸무게는 65kg, BMI는 22.5예요."), question_seq=3, answer_seq=4),),
        )
        expected = ("170cm", "68kg", "23.5")
    elif case == "decimal":
        ctx = replace(
            ctx, profile=CoachProfile(height_cm=Decimal("165.25"), weight_kg=Decimal("56.75"), bmi=Decimal("20.8"))
        )
        expected = ("165.25cm", "56.75kg", "20.8")
    elif case == "trimmed":
        ctx = replace(ctx, turns=(old_turn("예전 몸무게는 80kg이었다. " * 1800),))
    recorder = MetricsRecorder()
    settings = Settings()
    result = await run_coach(
        ctx,
        question,
        port=FakeHealthData(),
        model=create_chat_model(settings, recorder=recorder),
        sink=ListSink(),
        now=NOW,
    )
    calls = [call for message in result.messages if isinstance(message, AIMessage) for call in message.tool_calls]
    record = {
        "case": case,
        "attempt": attempt,
        "prompt_version": DEFAULT_PROMPT_VERSION,
        "system_prompt_sha256": sha256(
            render_system_prompt(load_system_prompt(), NOW.date(), ctx.profile).encode()
        ).hexdigest(),
        "model": settings.llm_model,
        "question": question,
        "answer": result.answer,
        "calls": calls,
        "finish_reason": result.finish_reason,
        "error": result.error_code,
        "history_usage": asdict(result.history_usage),
        "metrics": [asdict(item) for item in recorder.records()],
    }
    (profile_live_output / f"{case}-{attempt}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert result.error_code is None and result.finish_reason == "stop"
    answer = re.sub(r"\s|\*", "", result.answer).lower()
    if case in ("other", "fat"):
        assert not calls
        assert not re.search(r"\d+(?:\.\d+)?(?:cm|kg|%)", answer)
        assert any(word in answer for word in ("없", "불가", "어렵"))
    else:
        assert all(value in answer for value in expected), expected
        if case != "bmi":
            assert any(word in answer for word in ("등록", "프로필", "입력"))
        if case not in ("height", "weight"):
            assert "서버" in answer and "계산" in answer
        if case == "mixed":
            assert any(call["args"].get("metric") == "steps" for call in calls)
        else:
            assert not calls
        if case == "trimmed":
            assert result.history_usage.excluded_turns == 1
    # 수치/출처 외 문장 의미·과거 값의 현재값 오인·체지방/등급 추정은 저장 원문을 함께 검토한다.
