"""지난 X요일 최종 조회 확인 실행기(eval/weekday_final_lookup.py) 테스트. 실제 LLM 대신 respx 모의 응답을 쓴다."""

import json
from datetime import date

import pytest
import respx

from eval import weekday_final_lookup as wf
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response

THU = date(2026, 10, 15)
SAT_STEPS = "tool_selection_ai_period_v2.2/synth-04-304"


def call(round_: int, period: str, reply: str = "ok", name: str = "get_metric_summary") -> dict:
    return {"round": round_, "name": name, "args": {"metric": "steps", "period": period}, "reply": reply}


def test_items_are_last_weekday_questions_of_period_v2_2_and_v1_1():
    items = wf.select_items()
    assert [(item.key, wf.weekday_of(item)) for item in items] == [
        ("tool_selection_ai_period_v2.2/synth-07-304", "sun"),
        ("tool_selection_ai_period_v2.2/synth-04-304", "sat"),
        ("tool_selection_ai_period_v2.2/synth-05-304", "wed"),
        ("tool_selection_ai_period_v2.2/synth-10-304", "fri"),
        ("tool_selection_v1.1/synth-06-019", "sat"),
        ("tool_selection_v1.1/synth-07-008", "sun"),
    ]
    assert [b.isoformat() for b in wf.BASE_DATES] == [f"2026-10-{d}" for d in range(12, 19)]


@pytest.mark.parametrize(("asked", "day"), [("sat", 10), ("wed", 14), ("thu", 8), ("fri", 9)])
def test_answer_day_is_latest_weekday_before_base_date(asked, day):
    assert wf.answer_day(asked, THU) == date(2026, 10, day)


@pytest.mark.parametrize(
    ("periods", "kind"),
    [
        (["weekday:sat"], "exact"),
        (["weeks_ago:1.sat..weeks_ago:1.sat"], "exact"),
        (["weeks_ago:0.sat"], "future"),
        (["weeks_ago:1"], "contains"),
        (["days_ago:0"], "other"),
        (["weeks_ago:2.Sat"], "invalid"),
        (["weeks_ago:1", "weekday:sat"], "exact"),
        (["weeks_ago:0.sun", "weeks_ago:1"], "future"),
        ([], "none"),
    ],
)
def test_kind_of_call_group_follows_rule_order(periods, kind):
    calls = [call(1, p) for p in periods]
    assert wf.kind_of(calls, date(2026, 10, 10), THU) == kind


def test_compare_call_is_judged_by_both_periods():
    compare = {
        "round": 1,
        "name": "compare_periods",
        "args": {"metric": "steps", "current": "weekday:sat", "baseline": "weeks_ago:2"},
        "reply": "ok",
    }
    assert wf.kind_of([compare], date(2026, 10, 10), THU) == "exact"


def test_judge_reads_first_round_last_success_and_last_attempt():
    rejected_then_day = [call(1, "weeks_ago:0.sat", "unavailable_period"), call(2, "weekday:sat")]
    assert wf.judge(rejected_then_day, "sat", THU) == {
        "answer_day": "2026-10-10",
        "first": "future",
        "final_success": "exact",
        "final_attempt": "exact",
        "final_attempt_future": False,
        "final_success_periods": ["weekday:sat"],
        "final_attempt_periods": ["weekday:sat"],
    }
    # 아직 오지 않은 날을 조회하려다 끝난 실행
    future_only = wf.judge([call(1, "weeks_ago:0.sat", "unavailable_period")], "sat", THU)
    assert (future_only["final_success"], future_only["final_attempt"], future_only["final_attempt_future"]) == (
        "none",
        "future",
        True,
    )
    # 마지막 성공 왕복에 호출이 둘이면 둘 중 정답 날짜 하루가 있는지 확인한다
    two = wf.judge([call(1, "weeks_ago:1"), call(1, "weekday:sat", name="get_daily_trend")], "sat", THU)
    assert (two["first"], two["final_success"]) == ("exact", "exact")
    assert wf.judge([], "sat", THU)["first"] == "none"
    message_only = [{"round": 1, "name": "get_daily_message", "args": {}, "reply": "ok"}]
    assert (wf.judge(message_only, "sat", THU)["first"], wf.judge(message_only, "sat", THU)["final_success"]) == (
        "other",
        "none",
    )


def test_run_order_visits_each_base_date_and_item_once_per_run():
    items = wf.select_items()[:2]
    order = wf.run_order(items, wf.BASE_DATES[:2], 2)
    assert [(run, base.day, item.id) for run, base, item in order] == [
        (run, day, item.id) for run in (1, 2) for day in (12, 13) for item in items
    ]


@respx.mock
def test_runs_to_the_end_and_saves_report(settings, monkeypatch, tmp_path, capsys):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "weeks_ago:0.sat"})),
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "weekday:sat"})),
            text_response("지난 토요일(10월 10일)에는 걸음 기록이 있어요."),
        ]
    )
    monkeypatch.setattr(wf, "get_settings", lambda: settings)
    monkeypatch.setattr(wf, "git_commit", lambda: "abc1234")
    wf.main(["--runs", "1", "--only", SAT_STEPS, "--bases", "2026-10-15", "--save", "--out", str(tmp_path)])

    # 이번 주의 아직 오지 않은 토요일(10-17)은 조회하지 않고 안내를 받고, 다음 왕복에서 정답 날짜를 조회한다
    second = json.loads(route.calls[1].request.content)
    reply = json.loads(second["messages"][-1]["content"])
    assert reply["error"] == "unavailable_period" and "아직 오지 않은 날짜" in reply["message"]
    [record] = [
        json.loads(line)
        for line in next(tmp_path.glob("*_weekday_final_records.jsonl")).read_text(encoding="utf-8").splitlines()
    ]
    assert (record["key"], record["base"], record["answer_day"]) == (SAT_STEPS, "2026-10-15", "2026-10-10")
    assert (record["first"], record["final_success"], record["final_attempt_future"]) == ("future", "exact", False)
    assert [c["reply"] for c in record["tool_calls"]] == ["unavailable_period", "ok"]
    out = capsys.readouterr().out
    assert "| 전체 | 1 | 0 | 1 | 0 | 0 | 0 |" in out and "| 2026-10-15(목) | 1 | 0 | 1 | 0 | 0 | 0 |" in out
    report = json.loads(next(tmp_path.glob("*_weekday_final.json")).read_text(encoding="utf-8"))
    assert report["commit"] == "abc1234" and report["total"]["final_exact"] == 1 and report["llm_calls"] == 3
    assert "answer" not in json.dumps(report["by_item"])
    calls_md = next(tmp_path.glob("*_weekday_final_calls.md")).read_text(encoding="utf-8")
    assert "get_metric_summary(metric=steps, period=weeks_ago:0.sat) → get_metric_summary(" in calls_md


def test_unknown_item_key_stops_before_calling_llm():
    with pytest.raises(SystemExit, match="요일 문항에 없는 키"):
        wf.main(["--runs", "1", "--only", "tool_selection_v1.1/synth-01-001"])
