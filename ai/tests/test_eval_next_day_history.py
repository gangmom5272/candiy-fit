"""다음 날 이어진 대화 확인 실행기(eval/next_day_history.py) 테스트. 실제 LLM 대신 respx 모의 응답을 쓴다."""

import json
from datetime import date

import pytest
import respx

from coach_ai.agent.nodes.model import UNSUPPORTED_NOTICE
from coach_ai.agent.rules.guard import check
from coach_ai.config import Settings
from eval import next_day_history as nd
from tests.llm_fakes import CHAT_URL, text_response

YESTERDAY = date(2026, 10, 22)
# dated 조건의 시스템 메시지 끝에 붙는 안내. 기준 시각의 오늘은 10월 23일 금요일이다
NOTICE = (
    "이전 대화에는 다른 날 나눈 내용이 있고, 그 대화의 오늘·어제는 그날 기준이다. 오늘은 2026년 10월 23일 금요일이다."
)
ANSWER = "오늘은 10월 23일 금요일이고, 16시 32분까지 기록 기준 4,210걸음 걸으셨어요."


def items_by_id() -> dict[str, nd.Item]:
    return {item.id: item for item in nd.load_items()}


def test_items_ask_about_today_after_yesterday_answers_with_that_date():
    items = nd.load_items()
    assert [(i.id, i.kind) for i in items] == [
        ("nd-01", "today_date"), ("nd-02", "today_date"), ("nd-03", "today_steps"), ("nd-04", "today_steps"),
    ]  # fmt: skip
    for item in items:
        # 이전 대화는 기준 시각 전날 나눈 대화이고, 답에 그날 날짜가 있다
        assert {pair.answered_on for pair in item.history} == {YESTERDAY}
        assert any("오늘은 10월 22일" in pair.answer for pair in item.history)
        # 안전 규칙에 걸리면 LLM 을 부르지 않아 답변을 판정할 수 없다
        assert check(item.question) is None
    assert [item.stale_steps for item in items] == [(), (), (6880,), (6880,)]
    assert (nd.NOW.date(), nd.TODAY.weekday()) == (date(2026, 10, 23), 4)
    assert nd.DEFAULT_RUNS == 5


FOLLOWUP = nd.DATASET.with_name("next_day_history_v2.jsonl")


async def test_followup_items_ask_without_saying_the_date_after_yesterday_values():
    """v2 → 어제 답에 그날의 오늘 값(진행 중 걸음 수·요일)이 있고, 이번 질문은 날짜를 다시 말하지 않는다."""
    items = nd.load_items(FOLLOWUP)
    assert [(i.id, i.kind) for i in items] == [
        ("nd-05", "today_steps"), ("nd-06", "today_steps"), ("nd-07", "today_steps"), ("nd-08", "today_steps"),
        ("nd-09", "today_date"),
    ]  # fmt: skip
    assert not {i.id for i in items} & {i.id for i in nd.load_items()}
    port = nd.synthetic_health_data(nd.USER_ID)
    yesterday = await port.get_metric_result(nd.USER_ID, "steps", YESTERDAY, YESTERDAY)
    for item in items:
        assert {pair.answered_on for pair in item.history} == {YESTERDAY}
        assert any("오늘" in pair.answer for pair in item.history)
        assert not any(word in item.question for word in ("오늘", "10월", "23일", "금요일"))
        assert check(item.question) is None
        # 이전 답의 걸음 수는 합성 데이터의 오늘 진행 중 값·어제 합계와 달라, 옮겨 썼는지 구분된다
        assert not set(item.stale_steps) & {await nd.today_steps(port), round(yesterday.days[0].value)}
    assert [i.stale_steps for i in items] == [(6880,), (6880,), (6880, 1120), (6880,), ()]


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"id": "x", "kind": "today_date", "question": "q", "history": [], "stale_steps": [], "note": ""}, "history"),
        ({"id": "x", "kind": "yesterday", "question": "q", "history": [], "stale_steps": [], "note": ""}, "kind"),
        (
            {"id": "x", "kind": "today_date", "question": "q", "stale_steps": [], "note": "",
             "history": [{"answered_on": "10-22", "question": "q", "answer": "a"}]},
            "answered_on",
        ),
        ({"id": "x", "kind": "today_date", "question": "q", "history": []}, "칸은"),
    ],
)  # fmt: skip
def test_load_items_rejects_malformed_rows(tmp_path, row, message):
    path = tmp_path / "items.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        nd.load_items(path)


def test_load_items_rejects_duplicate_ids(tmp_path):
    line = (nd.DATASET.read_text(encoding="utf-8").splitlines()[0]) + "\n"
    path = tmp_path / "items.jsonl"
    path.write_text(line * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="ID"):
        nd.load_items(path)


def test_only_dated_condition_puts_question_dates_on_pairs():
    item = items_by_id()["nd-04"]
    dated, undated = nd.context_turns(item, "dated"), nd.context_turns(item, "undated")
    assert [turn.answered_on for turn in dated] == [YESTERDAY, YESTERDAY]
    assert [turn.answered_on for turn in undated] == [None, None]
    assert [(t.question, t.answer, t.question_seq, t.answer_seq) for t in dated] == [
        (t.question, t.answer, t.question_seq, t.answer_seq) for t in undated
    ]
    assert [(t.question_seq, t.answer_seq) for t in dated] == [(1, 2), (3, 4)]


def test_run_order_puts_each_condition_once_per_run_and_item():
    items = nd.load_items()
    order = nd.run_order(items, 2, nd.CONDITIONS)
    assert len(order) == 2 * len(items) * 2
    for run in (1, 2):
        for item in items:
            assert sorted(c for r, i, c in order if r == run and i == item) == ["dated", "undated"]
    assert order == nd.run_order(items, 2, nd.CONDITIONS)


@pytest.mark.parametrize(
    ("answer", "verdict", "wrong"),
    [
        ("오늘은 10월 23일 금요일이에요.", "correct", []),
        ("10월 23일 금요일이에요.", "correct", []),
        ("어제는 10월 22일이었고, 오늘은 2026년 10월 23일이에요.", "correct", []),
        ("오늘(10월 23일)은 금요일이에요.", "correct", []),
        ("오늘은 10월 22일 목요일이에요.", "wrong_date", ["오늘은 10월 22일 목요일"]),
        ("오늘은 10월 23일 목요일이에요.", "wrong_date", ["오늘은 10월 23일 목요일"]),
        ("오늘은 목요일이에요.", "wrong_date", ["오늘은 목요일"]),
        ("**10월 22일** 목요일이에요.", "wrong_date", ["10월 22일 목요일"]),
        ("어제(10월 22일)와 같은 질문이네요.", "other", []),
    ],
)
def test_judge_reads_dates_said_to_be_today(answer, verdict, wrong):
    judgement = nd.judge(answer, items_by_id()["nd-01"], steps_today=4210)
    assert (judgement.verdict, judgement.detail["wrong_today"]) == (verdict, wrong)


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [
        ("오늘 16시 32분까지 기록 기준 4,210걸음 걸으셨어요.", "correct"),
        ("오늘은 6,880걸음 걸으셨어요.", "stale_steps"),
        # 동기화 날짜 문장처럼 이전 날짜에 "오늘"을 붙인 경우
        ("오늘 10월 20일 23시 30분까지 기록 기준 4,210걸음이에요.", "wrong_date"),
        ("오늘(10월 22일) 16시 32분까지 기록 기준 4,210걸음이에요.", "wrong_date"),
        # 날짜 문항이 아니라 첫 날짜를 오늘로 읽지 않는다
        ("10월 22일에는 7,310걸음, 오늘은 4,210걸음이에요.", "correct"),
        ("4,210보다 많이 걸으셨어요.", "other"),
    ],
)
def test_judge_reads_today_steps(answer, verdict):
    judgement = nd.judge(answer, items_by_id()["nd-03"], steps_today=4210)
    assert judgement.verdict == verdict


def test_judge_marks_errors_before_reading_answer():
    judgement = nd.judge("", items_by_id()["nd-01"], steps_today=4210, error_code="timeout")
    assert (judgement.verdict, judgement.detail) == ("error", {"error_code": "timeout"})


class RecordingLLM:
    """모든 요청에 같은 답을 반환하고 요청 본문을 모은다."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def __call__(self, request):
        self.bodies.append(json.loads(request.content))
        return text_response(ANSWER)


@respx.mock
def test_runs_items_in_both_conditions_and_saves_report(settings, monkeypatch, tmp_path, capsys):
    llm = RecordingLLM()
    respx.post(CHAT_URL).mock(side_effect=llm)
    monkeypatch.setattr(nd, "get_settings", lambda: settings)
    monkeypatch.setattr(nd, "git_commit", lambda: "abc1234")
    nd.main(["--runs", "1", "--save", "--out", str(tmp_path)])

    records = [
        json.loads(line)
        for line in next(tmp_path.glob("*_next_day_history_records.jsonl")).read_text(encoding="utf-8").splitlines()
    ]
    # 모의 답은 도구 없이 앞 답에 없는 기록 수치를 말해 문항마다 한 번 되돌린다 (요청 두 번)
    assert len(records) == 8 and len(llm.bodies) == 16
    lasts = [body["messages"][-1]["content"] for body in llm.bodies]
    assert [last.endswith(f"\n\n{UNSUPPORTED_NOTICE}") for last in lasts] == [False, True] * 8
    assert {(r["id"], r["condition"]) for r in records} == {
        (f"nd-0{n}", condition) for n in range(1, 5) for condition in ("dated", "undated")
    }
    assert all(r["verdict"] == "correct" and r["day_notice"] == (r["condition"] == "dated") for r in records)
    # dated 조건 요청만 시스템 메시지 끝에 안내가 붙고, 나머지 메시지는 두 조건이 같다
    systems = [body["messages"][0]["content"] for body in llm.bodies]
    assert sum(system.endswith(f"\n\n{NOTICE}") for system in systems) == 8
    assert all(body["messages"][-1]["role"] == "user" for body in llm.bodies)
    out = capsys.readouterr().out
    assert "| dated | 4 | 0 | 0 | 4 | 0 | 0 |" in out and "| undated | 4 | 0 | 0 | 4 | 0 | 0 |" in out
    assert "오늘 진행 중 걸음 4,210걸음" in out and "`오늘은 10월 23일 금요일`" in out
    assert next(tmp_path.glob("*_next_day_history.md")).read_text(encoding="utf-8") == out


@respx.mock
def test_without_key_stops_before_calling_llm(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(nd, "get_settings", lambda: Settings(_env_file=None))
    # 등록한 경로가 없어 LLM 을 부르면 실패한다
    with pytest.raises(SystemExit, match="LLM_API_KEY"):
        nd.main(["--runs", "1", "--only", "nd-01"])


def test_unknown_item_id_stops_before_calling_llm():
    with pytest.raises(SystemExit, match="nd-99"):
        nd.main(["--only", "nd-99"])
