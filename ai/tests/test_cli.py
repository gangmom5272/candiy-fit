import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
import respx

from coach_ai import cli
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.executor import ToolRequest
from coach_ai.llm import metrics
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.tools.handlers import get_metric_summary, get_sleep_records
from coach_ai.tools.schemas import GetMetricSummary, GetSleepRecords
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response

NOW = datetime(2026, 10, 15, 3, 12, tzinfo=UTC)  # 2026-10-15 12:12 KST
TODAY = NOW.date()


async def test_sample_data_has_missing_unsynced_and_in_progress():
    port = sample_health_data("demo", NOW)
    req = ToolRequest(user_id="demo", today=TODAY, port=port)

    steps = (await get_metric_summary(GetMetricSummary(metric="steps", period="last_7_days"), req)).payload
    assert steps["missing_days"]  # 0보인 날과 기록이 없는 날
    assert str(TODAY - timedelta(days=4)) in steps["missing_days"]
    assert steps["in_progress"]["display"] == "오늘 진행 중 4,210걸음 (12:05까지 기록 기준)"

    sleep = (await get_sleep_records(GetSleepRecords(period="last_14_days"), req)).payload
    assert sleep["summary"]["unsynced_days"] == [str(TODAY)]
    unknown = [n["date"] for n in sleep["nights"] if n["total_min"] is None]
    assert unknown == [str(TODAY - timedelta(days=8))] and unknown[0] in sleep["summary"]["missing_days"]
    assert sleep["summary"]["bedtime"]["earliest"]["time"] > "23:00"  # 자정 전 취침이 가장 이르다

    month = (await get_metric_summary(GetMetricSummary(metric="sleep", period="last_30_days"), req)).payload
    assert month["linked_from"] == str(TODAY - timedelta(days=13))
    assert month["completed_days"] == 14  # 연동 전 날짜는 빠진다
    linked = TODAY - timedelta(days=13)
    assert month["coverage"].startswith(f"연동일 {linked.month}/{linked.day} 이후 14일 중 ")


async def test_sample_data_other_user_is_empty():
    port = sample_health_data("demo", NOW)
    req = ToolRequest(user_id="someone-else", today=TODAY, port=port)
    out = await get_metric_summary(GetMetricSummary(metric="steps", period="last_7_days"), req)
    assert out.payload["days_with_data"] == 0


@pytest.fixture
def cli_env(settings, monkeypatch):
    # 실제 .env 를 읽지 않도록 설정과 기본 계측 기록기를 테스트용으로 바꾼다
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(metrics, "_default_recorder", MetricsRecorder())


@respx.mock
def test_cli_prints_events_in_order(cli_env, capsys):
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_sleep_records", {"period": "last_7_days"})),
            text_response("지난 7일 수면을 정리했어요."),
        ]
    )
    cli.main(["지난 7일 잠은 어땠어?"])

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    types = [e["type"] for e in lines]
    assert types[0] == "status" and types[-1] == "done"
    assert types.index("evidence") < types.index("token") < types.index("done")
    evidence = next(e for e in lines if e["type"] == "evidence")
    assert evidence["items"][0]["tool"] == "get_sleep_records"
    assert all(e["conversation_id"] == "cli" for e in lines)


@respx.mock
def test_cli_summary_goes_into_context_summary(cli_env):
    route = respx.post(CHAT_URL).mock(return_value=text_response("네."))
    result = asyncio.run(cli.run("잠을 잘 자려면?", "수면 시간을 자주 묻는다.", "demo-user"))

    system = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert system.endswith("\n\n## 이전 대화 요약\n\n수면 시간을 자주 묻는다.")
    # 대화 API 처럼 CoachContext.summary 로 넘겨 호출마다 예산 검사를 거친다
    assert result.history_usage.summary_used is True


@respx.mock
def test_cli_exercise_uses_extended_sample(cli_env):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "exercise", "period": "last_7_days"})),
            text_response("운동 기록이에요."),
        ]
    )
    asyncio.run(cli.run("지난 7일 운동 몇 번 했어?", None, "demo-user"))
    messages = json.loads(route.calls[1].request.content)["messages"]
    result = json.loads(next(m["content"] for m in messages if m["role"] == "tool"))["result"]
    assert "exercise_activity_note" not in result
    assert result["exercise_activity"]["summary"]["exercise_count"]["total"]["display"].endswith("회")


@pytest.mark.parametrize("summary", ["", "  "])
def test_cli_rejects_blank_summary(summary):
    with pytest.raises(SystemExit):
        cli.parse_args(["잠을 잘 자려면?", "--summary", summary])
