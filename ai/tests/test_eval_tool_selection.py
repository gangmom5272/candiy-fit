"""도구 선택 평가 실행기(eval/tool_selection.py) 테스트. 실제 LLM 대신 respx 모의 응답을 쓴다."""

import hashlib
import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
import pytest
import respx

from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.graph import run_coach
from coach_ai.agent.prompts import DEFAULT_PROMPT_VERSION, load_system_prompt, render_system_prompt
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.tools.periods import NAMED_EXPRESSIONS, period_range, resolve_period
from coach_ai.tools.schemas import PERIODS, ToolInput
from eval import tool_selection as ts
from eval.tool_selection import Item, Outcome
from tests.fakes import FakeHealthData
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response

STEPS_Y = ("get_metric_summary", {"metric": "steps", "period": "yesterday"})
SLEEP_TODAY = ("get_metric_summary", {"metric": "sleep", "period": "today"})
SLEEP_Y = ("get_metric_summary", {"metric": "sleep", "period": "yesterday"})
RECORDS_TODAY = ("get_sleep_records", {"period": "today"})
RECORDS_Y = ("get_sleep_records", {"period": "yesterday"})
CMP_STEPS = ("compare_periods", {"metric": "steps", "current": "this_week", "baseline": "last_week"})
CMP_SLEEP = ("compare_periods", {"metric": "sleep", "current": "this_week", "baseline": "last_week"})
CLARIFY = {"type": "clarify", "about": "time_range"}
COMMIT = "abc1234"
DATASETS_DIR = ts.EVAL_DIR / "datasets"
# 기본 데이터셋을 v1.1 로 바꾸기 전의 파일. 이 파일로 잰 기록과 비교할 수 있게 그대로 둔다
V1_DATASET = DATASETS_DIR / "tool_selection_v1.jsonl"


# 커밋 조회는 git 을 실행해, git 이 없거나 저장소 소유자가 달라 git 이 거부하면 None 을 반환한다.
# 테스트 결과가 실행 환경의 git 에 따라 달라지지 않게 고정값을 쓴다
@pytest.fixture(autouse=True)
def fixed_commit(monkeypatch):
    monkeypatch.setattr(ts, "git_commit", lambda: COMMIT)


def calls(*pairs: tuple[str, dict]) -> list[dict]:
    return [{"name": name, "args": args} for name, args in pairs]


def tool_calls(*pairs: tuple[str, dict]) -> dict:
    return {"type": "tool_calls", "tool_calls": calls(*pairs)}


def make_item(category: str, expected: dict, acceptable=(), item_id: str = "t-001", question: str = "질문") -> Item:
    return Item("t", item_id, "synth-01", category, question, expected, [calls(*a) for a in acceptable])


def tool_item(*pairs, acceptable=(), item_id: str = "t-001") -> Item:
    return make_item("tool", tool_calls(*pairs), acceptable, item_id)


def answer(*pairs, text: str = "") -> Outcome:
    return Outcome(llm=True, calls=tuple(calls(*pairs)), text=text)


def row(item_id: str, question: str, expected: dict, category: str = "tool", acceptable=(), proposed=()) -> dict:
    return {
        "id": item_id,
        "persona": "synth-01",
        "category": category,
        "question": question,
        "expected": expected,
        "acceptable": [calls(*a) for a in acceptable],
        "proposed": list(proposed),
        "note": "",
    }


def write_jsonl(path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def test_parse_args_defaults():
    """인자 없이 파싱 → 기본 데이터셋·기준일·프롬프트 버전(v1.20) 기본값이다."""
    args = ts.parse_args([])
    assert args.dataset == [ts.DEFAULT_DATASET] and ts.DEFAULT_DATASET.exists()
    assert ts.DEFAULT_DATASET == DATASETS_DIR / "tool_selection_v1.1.jsonl"
    assert (args.repeat, args.concurrency, args.limit, args.only) == (5, 1, None, None)
    assert args.temperature is None and args.today == date(2026, 10, 22) and args.prompt_version == "v1.20"
    assert not args.save and args.out == ts.RESULTS_DIR
    assert args.scoring == "range" and args.rescore is None
    assert ts.parse_args(["--scoring", "exact"]).scoring == "exact"


@pytest.mark.parametrize(
    "argv",
    [
        ["--repeat", "0"],
        ["--concurrency", "0"],
        ["--limit", "0"],
        ["--temperature", "-0.1"],
        ["--temperature", "2.5"],
        ["--only", "unknown"],
        ["--today", "2026-13-01"],
        ["--scoring", "names"],
    ],
)
def test_parse_args_rejects_bad_values(argv):
    with pytest.raises(SystemExit):
        ts.parse_args(argv)


def test_default_selection_is_dataset_without_proposed():
    items, infos = ts.load_datasets([ts.DEFAULT_DATASET])
    assert infos[0]["name"] == "tool_selection_v1.1"
    assert infos[0]["path"] == "eval/datasets/tool_selection_v1.1.jsonl"
    assert infos[0]["rows"] == len(items) and len(infos[0]["sha256"]) == 64
    chosen = ts.select(items, ts.DEFAULT_CATEGORIES)
    assert chosen == [i for i in items if i.category != "proposed"]
    clarify = [i for i in items if i.category == "clarify"]
    assert ts.select(items, ["clarify"], limit=2) == clarify[:2]


def test_dataset_info_records_version_from_file_name():
    _, infos = ts.load_datasets([ts.DEFAULT_DATASET, SUPPLEMENT])
    assert [(info["name"], info["version"]) for info in infos] == [
        ("tool_selection_v1.1", "v1.1"),
        ("tool_selection_ai_v1", "v1"),
    ]
    assert ts.dataset_version("tool_selection_ai_exercise_v1") == "v1" and ts.dataset_version("e2e") is None


def test_item_stats_record_definition_sha256():
    item = tool_item(STEPS_Y, item_id="a")
    changed = tool_item(STEPS_Y, acceptable=[[STEPS_Y, SLEEP_Y]], item_id="a")
    [first] = ts.summarize(scored_records(item, [answer(STEPS_Y)]), [item], repeat=1)["items"]
    [second] = ts.summarize(scored_records(changed, [answer(STEPS_Y)]), [changed], repeat=1)["items"]
    # 질문·기대 답 표시가 같아도 허용 답안 내용이 다르면 정의 sha256 이 다르다
    assert len(first["definition_sha256"]) == 12 and first["definition_sha256"] != second["definition_sha256"]
    assert ts.definition_sha256(item) == first["definition_sha256"]


def test_load_multiple_datasets_and_reject_duplicates(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    write_jsonl(a, [row("x-1", "q1", tool_calls(STEPS_Y)), row("x-2", "q2", {"type": "no_tool"}, "no_tool")])
    # 데이터셋이 다르면 같은 ID 도 구분된다
    write_jsonl(b, [row("x-1", "q1", tool_calls(STEPS_Y))])
    items, infos = ts.load_datasets([a, b])
    assert [i.key for i in items] == ["a/x-1", "a/x-2", "b/x-1"]
    assert [(info["name"], info["path"], info["rows"]) for info in infos] == [("a", "a.jsonl", 2), ("b", "b.jsonl", 1)]

    with pytest.raises(ValueError, match="두 번"):
        ts.load_datasets([a, a])
    write_jsonl(b, [row("x-1", "q1", tool_calls(STEPS_Y)), row("x-1", "q2", tool_calls(STEPS_Y))])
    with pytest.raises(ValueError, match="겹친다"):
        ts.load_datasets([b])
    write_jsonl(b, [row("x-1", "q1", tool_calls(STEPS_Y), category="unknown")])
    with pytest.raises(ValueError, match="알 수 없는 종류"):
        ts.load_datasets([b])


def test_needs_llm_only_for_questions_that_reach_the_llm():
    guard = make_item("guard", {"type": "guard", "guard_category": "diagnosis"}, question="나 불면증이야?")
    blocked = make_item("tool", tool_calls(SLEEP_Y), question="수면제 먹고 잔 날 몇 시간 잤어?")
    assert not ts.needs_llm([guard, blocked])
    assert ts.needs_llm([guard, make_item("tool", tool_calls(STEPS_Y), question="어제 몇 걸음 걸었어?")])


def test_tool_answer_ignores_order_and_accepts_acceptable():
    two = tool_item(CMP_STEPS, CMP_SLEEP)
    verdict = ts.score(two, answer(CMP_SLEEP, CMP_STEPS))
    assert verdict.correct and verdict.detail["matched"] == 0

    sleep = tool_item(SLEEP_TODAY, acceptable=[[RECORDS_TODAY]])
    verdict = ts.score(sleep, answer(RECORDS_TODAY))
    assert verdict.correct and verdict.detail["matched"] == 1


def test_empty_acceptable_answer_scores_reply_without_tools_as_correct():
    # 한마디 보충 문항의 지난 날짜·다시 고르기는 한마디 호출과 도구 없는 답이 모두 맞다 (허용 답안 빈 호출 목록)
    daily = ("get_daily_message", {})
    either = tool_item(daily, acceptable=[[]])
    assert ts.score(either, answer(daily)).detail["matched"] == 0
    verdict = ts.score(either, answer(text="지난 날짜의 한마디는 확인할 수 없어요."))
    assert verdict.correct and verdict.detail["matched"] == 1
    assert ts.score(either, answer(STEPS_Y)).error == "wrong_tool"
    # 인자를 붙인 한마디 호출은 틀리고, 스키마 검사에서 거부될 호출로 센다
    denied = ts.score(either, answer(("get_daily_message", {"period": "days_ago:1"})))
    assert denied.error == "wrong_args" and [d["reason"] for d in denied.detail["denied"]] == ["extra_field"]


def test_argument_written_as_its_default_is_the_same_call():
    # include_non_main=false 는 적지 않은 호출과 게이트웨이에서 같은 호출이다
    records = tool_item(RECORDS_Y)
    explicit = ("get_sleep_records", {"period": "yesterday", "include_non_main": False})
    assert ts.score(records, answer(explicit)).correct
    other = ts.score(records, answer(("get_sleep_records", {"period": "yesterday", "include_non_main": True})))
    assert not other.correct and other.error == "wrong_args"
    # 기본값과 타입이 다른 값(0)은 스키마 검사에서 거부되는 호출이라 같은 호출로 보지 않는다
    zero = ts.score(records, answer(("get_sleep_records", {"period": "yesterday", "include_non_main": 0})))
    assert not zero.correct


def test_same_call_twice_is_not_the_same_answer():
    verdict = ts.score(tool_item(STEPS_Y), answer(STEPS_Y, STEPS_Y))
    assert (verdict.correct, verdict.error) == (False, "extra_call")


@pytest.mark.parametrize(
    "expected, actual, error",
    [
        ([STEPS_Y], [("get_daily_trend", {"metric": "steps", "period": "yesterday"})], "wrong_tool"),
        ([STEPS_Y], [("get_metric_summary", {"metric": "steps", "period": "today"})], "wrong_args"),
        ([STEPS_Y], [], "missing_call"),
        ([CMP_STEPS, CMP_SLEEP], [CMP_STEPS], "missing_call"),
        ([STEPS_Y], [STEPS_Y, SLEEP_TODAY], "extra_call"),
    ],
)
def test_error_classes(expected, actual, error):
    verdict = ts.score(tool_item(*expected), answer(*actual))
    assert (verdict.correct, verdict.error) == (False, error)


def test_wrong_args_lists_keys_against_closest_reference():
    # 정답(get_metric_summary)보다 허용 답안(get_sleep_records)에 가까우면 그쪽과 비교한다
    item = tool_item(SLEEP_TODAY, acceptable=[[RECORDS_TODAY]])
    verdict = ts.score(item, answer(RECORDS_Y))
    assert verdict.error == "wrong_args" and verdict.detail["closest"] == 1 and verdict.detail["args"] == ["period"]

    swapped = ("compare_periods", {"metric": "steps", "current": "last_week", "baseline": "this_week"})
    verdict = ts.score(tool_item(CMP_STEPS, CMP_SLEEP), answer(swapped, CMP_SLEEP))
    assert verdict.error == "wrong_args" and verdict.detail["args"] == ["baseline", "current"]


def test_calls_the_schema_would_deny_are_listed():
    verdict = ts.score(
        tool_item(STEPS_Y),
        answer(
            ("get_metric_summary", {"metric": "steps", "period": "last_3_days"}),
            ("get_metric_summary", {"metric": "steps", "period": "yesterday", "user_id": "u2"}),
            ("get_hourly_records", {"metric": "steps", "period": "yesterday"}),
        ),
    )
    assert verdict.error == "extra_call"
    assert [d["reason"] for d in verdict.detail["denied"]] == ["invalid_args", "extra_field", "unknown_tool"]


DAY = date(2026, 10, 22)
LAST_WEEK_STEPS = ("get_metric_summary", {"metric": "steps", "period": "last_week"})


def steps(period: str) -> tuple[str, dict]:
    return ("get_metric_summary", {"metric": "steps", "period": period})


@pytest.mark.parametrize("period", ["last_week", "weeks_ago:1", "dates:10-12..10-18", "dates:2026-10-12..2026-10-18"])
def test_range_scoring_accepts_same_dates_in_any_expression(period):
    verdict = ts.score(tool_item(LAST_WEEK_STEPS), answer(steps(period)), today=DAY)
    assert verdict.correct and verdict.detail["matched"] == 0


def test_exact_scoring_compares_arguments_as_written():
    verdict = ts.score(tool_item(LAST_WEEK_STEPS), answer(steps("weeks_ago:1")))
    assert (verdict.correct, verdict.error, verdict.detail["args"]) == (False, "wrong_args", ["period"])


def test_range_scoring_compares_both_comparison_periods():
    same = ("compare_periods", {"metric": "steps", "current": "weeks_ago:0", "baseline": "weeks_ago:1"})
    assert ts.score(tool_item(CMP_STEPS), answer(same), today=DAY).correct
    swapped = ("compare_periods", {"metric": "steps", "current": "weeks_ago:1", "baseline": "weeks_ago:0"})
    verdict = ts.score(tool_item(CMP_STEPS), answer(swapped), today=DAY)
    assert verdict.error == "wrong_args" and verdict.detail["args"] == ["baseline", "current"]


@pytest.mark.parametrize(
    ("expected", "actual", "correct"),
    [
        # 한도를 넘는 기간도 날짜 범위로 비교한다 (13개월 전은 2025년 9월)
        ("months_ago:13", "dates:2025-09-01..2025-09-30", True),
        ("months_ago:13", "months_ago:12", False),
        # 최근 6달(4-23~10-22)과 최근 180일(4-26~10-22)은 다르다
        ("recent_months:6", "recent_days:180", False),
        # 날짜 하루와 그날이 든 기간은 다르다
        ("yesterday", "date:10-21", True),
        ("last_week", "date:10-17", False),
        # 기준일로 연도를 정한다 (10월 23일은 지난해)
        ("date:2025-10-23", "date:10-23", True),
        # 형식이 아닌 값은 날짜 범위로 바꾸지 않아, 날짜 범위처럼 쓴 문자열과 같아지지 않는다
        ("last_week", "2026-10-12..2026-10-18", False),
        ("last_week", "weeks_ago:-1", False),
        ("recent_days:7", "recent_days:0", False),
    ],
)
def test_range_scoring_compares_dates_not_words(expected, actual, correct):
    verdict = ts.score(tool_item(steps(expected)), answer(steps(actual)), today=DAY)
    assert verdict.correct is correct


def _in_other_words(value: str) -> str:
    """같은 날짜 범위의 다른 표현. 이름 기간은 새 표현, 새 표현은 이름 기간이나 연도를 붙인 날짜 범위다."""
    named = {expression: name for name, expression in NAMED_EXPRESSIONS.items()}
    if value in NAMED_EXPRESSIONS:
        return NAMED_EXPRESSIONS[value]
    if value in named:
        return named[value]
    r = period_range(value, DAY)
    return f"dates:{r.start.isoformat()}..{r.end.isoformat()}"


@pytest.mark.parametrize(
    "name",
    [
        "tool_selection_ai_period_v2",
        "tool_selection_ai_period_v2.1",
        "tool_selection_ai_period_v2.2",
        "tool_selection_ai_period_v2.3",
        "tool_selection_ai_period_v2.4",
        "tool_selection_ai_exercise_v1.1",
        "tool_selection_ai_exercise_v1.2",
    ],
)
def test_new_period_answers_score_as_correct_in_other_words(name):
    """새 기간 데이터셋의 정답·허용 답안 → 순서를 바꾸거나 날짜 범위 표현으로 바꿔도 맞다."""
    items, _ = ts.load_datasets([DATASETS_DIR / f"{name}.jsonl"])
    for item in items:
        for reference in item.references:
            assert ts.score(item, Outcome(llm=True, calls=tuple(reversed(reference))), today=DAY).correct, item.id
            other = tuple(
                {**c, "args": {k: _in_other_words(v) if k in ts.PERIOD_ARGS else v for k, v in c["args"].items()}}
                for c in reference
            )
            assert ts.score(item, Outcome(llm=True, calls=other), today=DAY).correct, item.id
            # 옛 채점은 쓴 그대로 비교해 다른 표현을 틀린 답으로 본다
            assert not ts.score(item, Outcome(llm=True, calls=other)).correct, item.id


def test_named_periods_have_distinct_dates_on_dataset_day():
    # 기준일(2026-10-22 목)에는 이름 기간 10종의 날짜 범위가 모두 달라, 이름 기간만 쓴 출력은 두 채점 결과가 같다
    assert len({resolve_period(p, ts.DEFAULT_TODAY) for p in PERIODS}) == len(PERIODS)


def test_unparsed_arguments_are_wrong():
    out = Outcome(llm=True, invalid_calls=({"name": "get_metric_summary", "args": "{bad", "error": "json"},))
    assert ts.score(tool_item(STEPS_Y), out).error == "invalid_call"


def test_no_tool_and_clarify():
    no_tool = make_item("no_tool", {"type": "no_tool"})
    assert ts.score(no_tool, answer(text="천만에요.")).correct
    assert ts.score(no_tool, answer(STEPS_Y)).error == "extra_call"

    clarify = make_item("clarify", CLARIFY)
    asked = ts.score(clarify, answer(text="보통 몇 시에 출근하세요? 알려 주시면 그 시간대를 확인할게요."))
    assert asked.correct and asked.detail["asked"]
    assert ts.score(clarify, answer(text="오늘 4,210걸음 걸었어요.")).error == "no_clarify"
    assert ts.score(clarify, answer(("get_metric_summary", {"metric": "steps", "period": "today"}))).error == (
        "extra_call"
    )


@pytest.mark.parametrize(
    "text, asked",
    [
        ("보통 몇 시에 출근하세요?", True),
        ("회사에 몇 시부터 몇 시까지 있었어요? 알려 주시면 확인할게요.", True),
        ("출근 시간이 언제인가요？", True),
        ("오늘 4,210걸음 걸었어요.", False),
        # 물음표 문장에 시각을 묻는 말이 없으면 되물은 것이 아니다
        ("수면 시간은 7시간이에요. 더 궁금한 게 있나요?", False),
    ],
)
def test_asks_back(text, asked):
    assert ts.asks_back(text) is asked


def test_guard_scoring():
    guard = make_item("guard", {"type": "guard", "guard_category": "diagnosis"})
    assert ts.score(guard, Outcome(blocked_by="diagnosis")).correct
    missed = ts.score(guard, Outcome())
    assert (missed.correct, missed.error) == (False, "guard_missed")
    wrong = ts.score(guard, Outcome(blocked_by="medication"))
    assert (wrong.correct, wrong.error, wrong.detail) == (False, "guard_wrong", {"blocked_by": "medication"})

    blocked = ts.score(tool_item(STEPS_Y), Outcome(blocked_by="medication"))
    assert (blocked.correct, blocked.error) == (False, "guard_blocked")


def test_proposed_and_llm_errors_are_not_scored():
    proposed = make_item("proposed", tool_calls(("get_metric_summary", {"metric": "sleep", "period": "last_3_days"})))
    out = answer(("get_metric_summary", {"metric": "sleep", "period": "last_3_days"}))
    verdict = ts.score(proposed, out)
    assert verdict.correct is None and verdict.detail["denied"][0]["reason"] == "invalid_args"
    assert ts.score(proposed, Outcome(blocked_by="medication")).correct is None

    failed = ts.score(tool_item(STEPS_Y), Outcome(llm=True, error_code="llm_timeout"))
    assert (failed.correct, failed.error, failed.detail) == (None, "llm_error", {"code": "llm_timeout"})


@pytest.mark.parametrize("path", [V1_DATASET, ts.DEFAULT_DATASET], ids=lambda path: path.stem)
def test_dataset_answers_score_as_correct(path):
    """데이터셋 정답·허용 답안을 그대로 반환하면 모두 맞아야 한다. 호출 순서는 뒤집어 넣는다."""
    items, _ = ts.load_datasets([path])
    checked = 0
    for item in items:
        expected = item.expected
        if item.category == "proposed":
            continue
        if expected["type"] == "guard":
            outcomes = [Outcome(blocked_by=expected["guard_category"])]
        elif expected["type"] == "tool_calls":
            outcomes = [Outcome(llm=True, calls=tuple(reversed(ref))) for ref in item.references]
        elif expected["type"] == "clarify":
            outcomes = [answer(text="보통 몇 시에 출근하세요?")]
        else:
            outcomes = [answer(text="천만에요.")]
        for out in outcomes:
            assert ts.score(item, out).correct, item.id
            checked += 1
    assert checked > len(items) // 2


def test_signature_compares_decisions_not_sentences():
    item = tool_item(STEPS_Y)
    out = answer(("get_metric_summary", {"period": "yesterday", "metric": "steps"}))
    assert ts.signature(out, ts.score(item, out)) == "get_metric_summary(metric=steps, period=yesterday)"

    clarify = make_item("clarify", CLARIFY)
    for text in ("몇 시에 출근하세요?", "출근은 보통 몇 시부터 몇 시까지세요?"):
        out = answer(text=text)
        assert ts.signature(out, ts.score(clarify, out)) == "(도구 없음, 되물음)"
    assert ts.signature(Outcome(blocked_by="diagnosis"), ts.Verdict(True)) == "(경계: diagnosis)"
    assert ts.signature(Outcome(), ts.Verdict(False, "guard_missed")) == "(경계 통과)"
    assert ts.signature(Outcome(llm=True, error_code="llm_timeout"), ts.Verdict(None, "llm_error")) is None


FACTORS = {"analysis": "factors"}


def sleep_call(period: str = "recent_days:14", **args) -> tuple[str, dict]:
    """수면 기록 호출 (기본 최근 14일). 분석 인자는 키워드로 더한다."""
    return ("get_sleep_records", {"period": period, **args})


class PeriodOnlySleepRecords(ToolInput):
    """분석 인자가 없는 수면 기록 스키마."""

    period: str


class AnalysisSleepRecords(ToolInput):
    """분석 인자와 include_non_main 이 있는 수면 기록 스키마. 선택 인자의 기본값은 None, False 다."""

    period: str
    analysis: Literal["pattern", "signals", "factors"] | None = None
    condition: str | None = None
    include_non_main: bool = False


@pytest.mark.parametrize(
    "expected, actual, wrong",
    [
        # 값 하나: 그 값이어야 맞다
        ({"analysis": "pattern"}, {"analysis": "pattern"}, None),
        ({"analysis": "pattern"}, {"analysis": "signals"}, ["analysis"]),
        ({"analysis": "pattern"}, {}, ["analysis"]),
        # 값 여러 개: 그중 하나면 맞다
        ({"analysis": ["pattern", "signals"]}, {"analysis": "signals"}, None),
        ({"analysis": ["pattern", "signals"]}, {"analysis": "factors"}, ["analysis"]),
        ({"analysis": ["pattern", "signals"]}, {}, ["analysis"]),
        # 없음(null): 그 인자가 없어야 맞다
        ({"analysis": None}, {}, None),
        ({"analysis": None}, {"analysis": "pattern"}, ["analysis"]),
        # 목록에 null 이 있으면 인자가 없는 호출도 맞다
        ({"analysis": ["pattern", None]}, {}, None),
        ({"analysis": ["pattern", None]}, {"analysis": "signals"}, ["analysis"]),
        # condition 도 같은 규칙이다
        ({**FACTORS, "condition": "late_exercise"}, {**FACTORS, "condition": "late_exercise"}, None),
        ({**FACTORS, "condition": "late_exercise"}, {**FACTORS, "condition": "weekend"}, ["condition"]),
        ({**FACTORS, "condition": "late_exercise"}, FACTORS, ["condition"]),
        ({**FACTORS, "condition": ["exercise", "weekend"]}, {**FACTORS, "condition": "weekend"}, None),
        ({**FACTORS, "condition": None}, FACTORS, None),
        ({**FACTORS, "condition": None}, {**FACTORS, "condition": "weekend"}, ["condition"]),
        ({**FACTORS, "condition": None}, {"analysis": "pattern", "condition": "weekend"}, ["analysis", "condition"]),
    ],
)
@pytest.mark.parametrize("today", [DAY, None], ids=["range", "exact"])
def test_expected_analysis_values(expected, actual, wrong, today):
    verdict = ts.score(tool_item(sleep_call(**expected)), answer(sleep_call(**actual)), today=today)
    if wrong is None:
        assert verdict.correct and verdict.detail["matched"] == 0
    else:
        assert (verdict.correct, verdict.error, verdict.detail["args"]) == (False, "wrong_args", wrong)


@pytest.mark.parametrize(
    "actual, error, keys",
    [
        (("get_daily_trend", {"metric": "sleep", "period": "recent_days:14"}), "wrong_tool", None),
        (sleep_call("recent_days:7", analysis="pattern"), "wrong_args", ["period"]),
        (sleep_call("recent_days:7", analysis="factors"), "wrong_args", ["analysis", "period"]),
        (sleep_call(analysis="signals"), "wrong_args", ["analysis"]),
        (sleep_call(analysis="pattern", condition="weekend"), "wrong_args", ["condition"]),
    ],
)
def test_wrong_runs_record_which_argument_differs(actual, error, keys):
    """틀린 실행의 기록: 도구가 다르면 wrong_tool, 인자가 다르면 wrong_args 와 다른 인자 이름(period, analysis 등)."""
    verdict = ts.score(tool_item(sleep_call(analysis="pattern", condition=None)), answer(actual), today=DAY)
    assert (verdict.correct, verdict.error, verdict.detail.get("args")) == (False, error, keys)


@pytest.mark.parametrize(
    "extra",
    [{}, {"analysis": "pattern"}, {"analysis": "factors", "condition": "weekend"}, {"analysis": None}],
    ids=["none", "analysis", "condition", "null"],
)
@pytest.mark.parametrize("today", [DAY, None], ids=["range", "exact"])
def test_items_without_expected_analysis_keep_current_verdicts(extra, today):
    """분석 인자의 기대값을 적지 않은 문항은 그 인자를 비교하지 않는다. 붙인 호출도 붙이지 않은 호출과 판정이 같다."""
    item = tool_item(SLEEP_TODAY, acceptable=[[RECORDS_TODAY]])
    right = ts.score(item, answer(("get_sleep_records", {**RECORDS_TODAY[1], **extra})), today=today)
    assert (right.correct, right.error, right.detail["matched"]) == (True, None, 1)
    wrong = ts.score(item, answer(("get_sleep_records", {**RECORDS_Y[1], **extra})), today=today)
    assert (wrong.correct, wrong.error, wrong.detail["closest"], wrong.detail["args"]) == (
        False,
        "wrong_args",
        1,
        ["period"],
    )
    two = tool_item(CMP_STEPS, RECORDS_TODAY)
    assert ts.score(two, answer(("get_sleep_records", {**RECORDS_TODAY[1], **extra}), CMP_STEPS), today=today).correct


def test_analysis_of_other_tools_is_compared_as_written():
    """분석 인자는 수면 기록 도구의 인자다. 다른 도구 호출에 붙은 analysis 는 지금처럼 다른 인자로 센다."""
    verdict = ts.score(tool_item(STEPS_Y), answer(("get_metric_summary", {**STEPS_Y[1], "analysis": "pattern"})))
    assert (verdict.correct, verdict.error, verdict.detail["args"]) == (False, "wrong_args", ["analysis"])


def test_expected_analysis_in_answers_with_several_calls():
    week = sleep_call("recent_days:7")
    item = tool_item(sleep_call(analysis="pattern"), week)
    # 순서는 상관없고, 기대값을 적지 않은 호출(week)의 분석 인자는 비교하지 않는다
    assert ts.score(item, answer(week, sleep_call(analysis="pattern")), today=DAY).correct
    week_signals = sleep_call("recent_days:7", analysis="signals")
    assert ts.score(item, answer(week_signals, sleep_call(analysis="pattern")), today=DAY).correct
    week_pattern = sleep_call("recent_days:7", analysis="pattern")
    swapped = ts.score(item, answer(sleep_call(analysis="signals"), week_pattern), today=DAY)
    assert (swapped.correct, swapped.error, swapped.detail["args"]) == (False, "wrong_args", ["analysis"])
    assert ts.score(item, answer(sleep_call(analysis="pattern")), today=DAY).error == "missing_call"


def test_expected_analysis_is_compared_without_schema_field(monkeypatch):
    """도구 스키마에 analysis 가 없어도 기대값과 실제 인자를 비교한다. 스키마 검사 결과(denied)는 지금처럼 기록한다."""
    monkeypatch.setitem(ts.TOOL_INPUTS, "get_sleep_records", PeriodOnlySleepRecords)
    item = tool_item(sleep_call(analysis="pattern"))
    verdict = ts.score(item, answer(sleep_call(analysis="pattern")), today=DAY)
    assert verdict.correct and [d["reason"] for d in verdict.detail["denied"]] == ["extra_field"]
    assert not ts.score(item, answer(sleep_call(analysis="signals")), today=DAY).correct


def test_arguments_written_as_schema_defaults_count_as_absent(monkeypatch):
    """도구 스키마의 기본값과 같은 값·타입으로 적은 인자는 적지 않은 것과 같게 센다 (게이트웨이와 같은 규칙)."""
    monkeypatch.setitem(ts.TOOL_INPUTS, "get_sleep_records", AnalysisSleepRecords)
    defaults = answer(sleep_call(analysis=None, condition=None))
    assert ts.score(tool_item(sleep_call(analysis=None, condition=None)), defaults, today=DAY).correct
    assert ts.score(tool_item(sleep_call()), defaults, today=DAY).correct
    missing = ts.score(tool_item(sleep_call(analysis="pattern")), defaults, today=DAY)
    assert (missing.correct, missing.error, missing.detail["args"]) == (False, "wrong_args", ["analysis"])
    # include_non_main=false 도 적지 않은 호출과 같고, 기본값과 타입이 다른 값(0)은 같은 호출로 보지 않는다
    plain = tool_item(RECORDS_Y)
    written_false = sleep_call("yesterday", include_non_main=False)
    written_zero = sleep_call("yesterday", include_non_main=0)
    assert ts.score(plain, answer(written_false), today=DAY).correct
    assert not ts.score(plain, answer(written_zero), today=DAY).correct


def test_expected_text_shows_expected_analysis_values():
    item = tool_item(sleep_call(analysis=["pattern", "signals"], condition=None))
    expected = "get_sleep_records(analysis=pattern|signals, condition=(없음), period=recent_days:14)"
    assert ts.expected_text(item) == expected
    assert ts.expected_text(tool_item(RECORDS_TODAY)) == "get_sleep_records(period=today)"


def test_expected_variants_spread_expected_values_into_calls():
    """데이터셋 검사가 도구 스키마로 확인하는 호출. 값 목록은 값마다 하나씩, null 은 그 인자를 뺀 호출이다."""
    args = {"period": "recent_days:14", "analysis": ["pattern", None], "condition": None}
    call = {"name": "get_sleep_records", "args": args}
    assert ts.expected_variants(call) == [
        {"name": "get_sleep_records", "args": {"period": "recent_days:14", "analysis": "pattern"}},
        {"name": "get_sleep_records", "args": {"period": "recent_days:14"}},
    ]
    today = {"name": "get_sleep_records", "args": {"period": "today"}}
    assert ts.expected_variants(today) == [today]
    other = {"name": "get_metric_summary", "args": {"metric": "sleep", "period": "today", "analysis": ["pattern"]}}
    assert ts.expected_variants(other) == [other]


# 다시 센 점수 (도구 선택 v1.1 의 "지난 X요일" 두 문항)
V1_1_NAME = "tool_selection_v1.1"
LAST_SUNDAY = "지난 일요일 몇 시에 일어났어?"
SUNDAY_RECORDS = ("get_sleep_records", {"period": "weekday:sun"})


def last_sunday_item(dataset: str = V1_1_NAME) -> Item:
    """도구 선택 v1.1 synth-07-008 과 같은 정답(지난주 전체)의 문항."""
    expected = tool_calls(("get_sleep_records", {"period": "last_week"}))
    return Item(dataset, "synth-07-008", "synth-07", "tool", LAST_SUNDAY, expected, [])


@pytest.mark.parametrize(
    "pairs, today, recounted",
    [
        ([SUNDAY_RECORDS], DAY, True),
        ([SUNDAY_RECORDS], None, True),
        # 원래 채점과 같은 기간 비교: 날짜 범위 채점이면 같은 날(10-18)을 다른 표현으로 적어도 다시 센다
        ([("get_sleep_records", {"period": "date:10-18"})], DAY, True),
        ([("get_sleep_records", {"period": "date:10-18"})], None, False),
        # 원래 채점과 같은 규칙: 기대값을 적지 않은 분석 인자는 비교하지 않는다
        ([("get_sleep_records", {"period": "weekday:sun", "analysis": "pattern"})], DAY, True),
        # 다른 요일, 다른 도구, 호출이 더 있는 실행은 다시 세지 않는다
        ([("get_sleep_records", {"period": "weekday:sat"})], DAY, False),
        ([("get_metric_summary", {"metric": "sleep", "period": "weekday:sun"})], DAY, False),
        ([SUNDAY_RECORDS, STEPS_Y], DAY, False),
    ],
)
def test_recount_marks_wrong_runs_that_match_the_last_weekday_answer(pairs, today, recounted):
    verdict = ts.score(last_sunday_item(), answer(*pairs), today=today)
    assert verdict.correct is False and verdict.detail.get("recount", False) is recounted


def test_recount_keeps_original_verdicts_and_other_datasets():
    right = ts.score(last_sunday_item(), answer(("get_sleep_records", {"period": "last_week"})), today=DAY)
    assert right.correct and "recount" not in right.detail
    other = ts.score(last_sunday_item("tool_selection_v1"), answer(SUNDAY_RECORDS), today=DAY)
    assert other.correct is False and "recount" not in other.detail


def test_recount_defaults_follow_the_same_rule(monkeypatch):
    monkeypatch.setitem(ts.TOOL_INPUTS, "get_sleep_records", AnalysisSleepRecords)
    out = answer(("get_sleep_records", {"period": "weekday:sun", "include_non_main": False, "analysis": None}))
    assert ts.score(last_sunday_item(), out, today=DAY).detail["recount"]


def test_recount_answers_are_v1_2_answers_of_v1_1_last_weekday_items():
    """다시 세는 답안은 v1.1 정답이 지난주 전체인 "지난 X요일" 두 문항의 v1.2 정답이다."""
    v1_1 = {item.key: item for item in ts.load_datasets([ts.DEFAULT_DATASET])[0]}
    v1_2 = {item.id: item for item in ts.load_datasets([DATASETS_DIR / "tool_selection_v1.2.jsonl"])[0]}
    assert sorted(ts.RECOUNT_ANSWERS) == [f"{V1_1_NAME}/synth-06-019", f"{V1_1_NAME}/synth-07-008"]
    for key, recount in ts.RECOUNT_ANSWERS.items():
        item = v1_1[key]
        [call] = item.expected["tool_calls"]
        assert call["args"] == {"period": "last_week"} and item.acceptable == []
        assert recount == v1_2[item.id].expected["tool_calls"] and [c["name"] for c in recount] == [call["name"]]


def test_summary_and_markdown_put_recount_next_to_original():
    item = last_sunday_item()
    outs = [answer(SUNDAY_RECORDS), answer(("get_sleep_records", {"period": "last_week"})), answer(STEPS_Y)]
    records = [ts.make_record(item, run, out, ts.score(item, out, today=DAY), None) for run, out in enumerate(outs, 1)]
    plain = tool_item(STEPS_Y)
    records += scored_records(plain, [answer(STEPS_Y)] * 3)
    s = ts.summarize(records, [item, plain], repeat=3)
    assert s["overall"]["correct"] == 4
    assert s["recount"] == {"correct": 5, "added": {f"{V1_1_NAME}/synth-07-008": 1}}
    md = ts.to_markdown(report_for(s))
    assert f"- 다시 센 점수 5: 원래 채점의 정답 4 + 다시 센 실행 1 (`{V1_1_NAME}/synth-07-008` 1)." in md
    # 다시 세는 문항이 없는 측정에는 다시 센 점수가 없다
    alone = ts.summarize(scored_records(plain, [answer(STEPS_Y)]), [plain], repeat=1)
    assert alone["recount"] is None and "다시 센 점수" not in ts.to_markdown(report_for(alone))


@respx.mock
def test_recount_is_saved_and_rescored_with_the_same_rule(settings, monkeypatch, tmp_path):
    eval_dir = tmp_path / "eval"
    (eval_dir / "datasets").mkdir(parents=True)
    monkeypatch.setattr(ts, "EVAL_DIR", eval_dir)
    path = eval_dir / "datasets" / f"{V1_1_NAME}.jsonl"
    write_jsonl(path, [row("synth-07-008", LAST_SUNDAY, tool_calls(("get_sleep_records", {"period": "last_week"})))])
    respx.post(CHAT_URL).mock(side_effect=FakeLLM({LAST_SUNDAY: lambda: tool_call_response(SUNDAY_RECORDS)}))
    monkeypatch.setattr(ts, "get_settings", lambda: settings)
    out_dir = tmp_path / "results"
    ts.main(["--dataset", str(path), "--repeat", "2", "--save", "--out", str(out_dir)])

    report = json.loads(next(out_dir.glob("*_tool_selection.json")).read_text(encoding="utf-8"))
    assert report["summary"]["overall"]["correct"] == 0
    assert report["summary"]["recount"] == {"correct": 2, "added": {f"{V1_1_NAME}/synth-07-008": 2}}
    records = next(out_dir.glob("*_tool_selection_records.jsonl"))
    saved = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    assert [(r["correct"], r["detail"]["recount"]) for r in saved] == [(False, True), (False, True)]

    respx.reset()
    ts.main(["--rescore", str(records), "--scoring", "exact", "--out", str(out_dir)])
    rescored = json.loads(next(out_dir.glob("*_rescored_exact.json")).read_text(encoding="utf-8"))
    assert rescored["summary"]["recount"] == report["summary"]["recount"] and rescored["rescored"]["changed"] == 0


def scored_records(item: Item, outcomes: list[Outcome]) -> list[dict]:
    return [ts.make_record(item, run, out, ts.score(item, out), None) for run, out in enumerate(outcomes, 1)]


def test_accuracy_and_consistency():
    stable = tool_item(STEPS_Y, item_id="a")
    flaky = tool_item(SLEEP_TODAY, item_id="b")
    records = scored_records(stable, [answer(STEPS_Y)] * 3)
    records += scored_records(flaky, [answer(SLEEP_TODAY), answer(SLEEP_Y), answer(SLEEP_Y)])
    s = ts.summarize(records, [stable, flaky], repeat=3)

    overall = s["overall"]
    assert (overall["items"], overall["scored"], overall["correct"]) == (2, 6, 4)
    assert (overall["run_min"], overall["run_max"]) == (0.5, 1.0)
    c = overall["consistency"]
    assert c["same_output"] == 0.5 and c["modal_share"] == pytest.approx((1 + 2 / 3) / 2)
    assert (c["all_correct"], c["majority_correct"], c["flaky"]) == (0.5, 0.5, 1)
    assert s["errors"] == {"wrong_args": 2} and s["wrong_arg_keys"] == {"period": 2}
    b = next(st for st in s["items"] if st["id"] == "b")
    assert b["correct"] == 1 and b["top_wrong"] == ["get_metric_summary(metric=sleep, period=yesterday)", 2]


def test_consistency_needs_two_runs():
    item = tool_item(STEPS_Y)
    s = ts.summarize(scored_records(item, [answer(STEPS_Y)]), [item], repeat=1)
    assert s["overall"]["accuracy"] == 1.0 and s["overall"]["consistency"] is None
    assert "## 일관성" not in ts.to_markdown(report_for(s))


def test_markdown_reads_report_saved_without_prompt_date():
    # 날짜를 넣기 전에 저장한 기록에는 prompt_date 가 없다
    item = tool_item(STEPS_Y)
    report = report_for(ts.summarize(scored_records(item, [answer(STEPS_Y)]), [item], repeat=1))
    del report["prompt_date"]

    md = ts.to_markdown(report)
    assert "(템플릿 sha256 000000000000, 넣은 날짜 없음)" in md
    assert "- 기준일 2026-10-22. 이 프롬프트에는 날짜 자리가 없어 결과에만 기록한다" in md


def report_for(summary: dict) -> dict:
    return {
        "measured_at": "2026-10-22T10:00:00",
        "commit": "abc1234",
        "model": None,
        "base_url_host": None,
        "max_concurrency": None,
        "concurrency": 1,
        "repeat": 1,
        "categories": ["tool"],
        "limit": None,
        "today": "2026-10-22",
        "prompt_version": "v1",
        "prompt_sha256": "0" * 12,
        "prompt_date": None,
        "tools_sha256": "0" * 12,
        "temperature": None,
        "temperature_source": None,
        "request_params": [],
        "datasets": [{"name": "t", "path": "t.jsonl", "sha256": "0" * 64, "rows": 1, "selected": 1}],
        "elapsed_s": 0.0,
        "summary": summary,
    }


class FakeLLM:
    """마지막 사용자 메시지(질문)마다 정해 둔 응답을 반환하고 받은 요청 본문을 모은다."""

    def __init__(self, replies: dict) -> None:
        self.replies = replies
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        return self.replies[body["messages"][-1]["content"]]()


E2E_ROWS = [
    row("e-1", "어제 몇 걸음 걸었어?", tool_calls(STEPS_Y)),
    row("e-2", "이번 주 걸음 수랑 수면 시간 둘 다 지난주랑 비교해 줘", tool_calls(CMP_STEPS, CMP_SLEEP)),
    row("e-3", "어젯밤에 몇 시간 잤어?", tool_calls(SLEEP_TODAY), acceptable=[[RECORDS_TODAY]]),
    # 경계 확인에 걸려 LLM 까지 가지 않는다 (오탐)
    row("e-4", "수면제 먹고 잔 날 몇 시간 잤어?", tool_calls(SLEEP_Y)),
    row("e-5", "고마워", {"type": "no_tool"}, "no_tool"),
    row("e-6", "오늘 출근하면서 몇 걸음 걸었는지 알려 줘", CLARIFY, "clarify"),
    row("e-7", "나 불면증이야?", {"type": "guard", "guard_category": "diagnosis"}, "guard"),
    # 실제로 경계 확인을 통과하는 guard 문항은 규칙을 고치면 결과가 바뀐다.
    # 어떤 규칙에도 걸리지 않는 질문으로 미탐 사례를 만든다
    row("e-8", "오늘 몇 걸음 걸었어?", {"type": "guard", "guard_category": "medication"}, "guard"),
    row(
        "e-9",
        "최근 3일 동안 평균 몇 시간 잤어?",
        tool_calls(("get_metric_summary", {"metric": "sleep", "period": "last_3_days"})),
        "proposed",
        proposed=["period:last_3_days"],
    ),
]
E2E_REPLIES = {
    "어제 몇 걸음 걸었어?": lambda: tool_call_response(STEPS_Y),
    # 순서가 달라도 정답이다
    "이번 주 걸음 수랑 수면 시간 둘 다 지난주랑 비교해 줘": lambda: tool_call_response(CMP_SLEEP, CMP_STEPS),
    "어젯밤에 몇 시간 잤어?": lambda: tool_call_response(SLEEP_Y),
    "고마워": lambda: text_response("천만에요."),
    "오늘 출근하면서 몇 걸음 걸었는지 알려 줘": lambda: text_response("보통 몇 시에 ", "출근하세요?"),
    "최근 3일 동안 평균 몇 시간 잤어?": lambda: tool_call_response(
        ("get_metric_summary", {"metric": "sleep", "period": "last_3_days"})
    ),
}


async def run_e2e(settings, tmp_path, *extra: str, version: str = "v1") -> tuple[dict, list[dict], FakeLLM]:
    path = tmp_path / "e2e.jsonl"
    write_jsonl(path, E2E_ROWS)
    argv = ["--dataset", str(path), "--repeat", "2", "--only", *ts.CATEGORIES, "--prompt-version", version, *extra]
    args = ts.parse_args(argv)
    all_items, datasets = ts.load_datasets(args.dataset)
    items = ts.select(all_items, args.only, args.limit)
    server = FakeLLM(E2E_REPLIES)
    respx.post(CHAT_URL).mock(side_effect=server)
    report, records = await ts.run_eval(settings, args, items, datasets, load_system_prompt(version))
    return report, records, server


@respx.mock
async def test_run_eval_end_to_end(settings, tmp_path, capsys):
    report, records, server = await run_e2e(settings, tmp_path)

    # 경계 확인을 통과한 문항(e-1·2·3·5·6·9)만 회차마다 LLM 을 한 번 호출한다
    assert len(server.bodies) == 6 * 2 and len(records) == len(E2E_ROWS) * 2
    assert {r["id"] for r in records if r["llm"]} == {"e-1", "e-2", "e-3", "e-5", "e-6", "e-9"}
    for body in server.bodies:
        assert body["stream"] is True and "temperature" not in body
        assert [t["function"]["name"] for t in body["tools"]] == list(ts.TOOL_INPUTS)
    assert (report["temperature"], report["temperature_source"]) == (None, "server_default")
    assert len(report["request_params"]) == 1 and "messages" not in report["request_params"][0]
    assert capsys.readouterr().out.count("문항 · 정답") == 2  # 회차마다 진행 줄 하나

    s = report["summary"]
    by_cat = {name: (g["scored"], g["correct"]) for name, g in s["by_category"].items()}
    assert by_cat == {"tool": (8, 4), "proposed": (0, 0), "no_tool": (2, 2), "clarify": (2, 2), "guard": (4, 2)}
    assert s["errors"] == {"wrong_args": 2, "guard_blocked": 2, "guard_missed": 2}
    assert s["wrong_arg_keys"] == {"period": 2}
    assert s["proposed"] == {"스키마 밖 인자로 호출": 2}
    assert s["overall"]["consistency"]["same_output"] == 1.0 and s["overall"]["consistency"]["flaky"] == 0
    assert [a["verdict"] for a in s["clarify_answers"]] == ["되물음", "되물음"]
    assert report["datasets"][0]["selected"] == len(E2E_ROWS) and report["categories"] == list(ts.CATEGORIES)

    first = next(r for r in records if r["id"] == "e-1")
    assert first["input_tokens"] == 300 and first["total_s"] is not None and first["finish_reason"] == "tool_calls"
    guard = next(r for r in records if r["id"] == "e-7")
    assert guard["request_id"] is None and guard["input_tokens"] is None and guard["correct"]

    md = ts.to_markdown(report)
    for heading in ("## 정확도", "## 일관성", "## 오답 분류", "## 틀린 문항", "## 되묻기 답변", "## proposed 관찰"):
        assert heading in md
    assert "temperature 서버 기본값" in md and "| e-3 | tool | 0/2 |" in md
    assert report["period_scoring"] == "range" and "기준일로 해석한 날짜 범위로 비교" in md


def test_markdown_of_report_saved_before_period_scoring_says_arguments_as_written():
    md = ts.to_markdown(report_for(ts.summarize([], [], 1)))
    assert "기간 인자도 쓴 그대로 비교" in md


@respx.mock
async def test_temperature_is_recorded_when_given(settings, tmp_path):
    report, _, server = await run_e2e(settings, tmp_path, "--temperature", "0")
    assert {body["temperature"] for body in server.bodies} == {0.0}
    assert (report["temperature"], report["temperature_source"]) == (0.0, "request")
    assert "temperature 0.0 (요청에 넣음)" in ts.to_markdown(report)


@pytest.mark.parametrize(
    ("version", "extra", "day", "text"),
    [
        ("v1.2", (), "2026-10-22", "2026년 10월 22일 목요일"),
        ("v1.2", ("--today", "2026-10-26"), "2026-10-26", "2026년 10월 26일 월요일"),
        # 날짜 자리가 없는 프롬프트는 그대로 보내고, 넣은 날짜는 없다
        ("v1.1", (), None, None),
    ],
)
@respx.mock
async def test_prompt_gets_reference_date_and_report_records_it(settings, tmp_path, version, extra, day, text):
    report, _, server = await run_e2e(settings, tmp_path, *extra, version=version)

    v1_1 = load_system_prompt("v1.1")
    sent = v1_1 if text is None else f"{v1_1}\n\n오늘은 {text}이다."
    assert {body["messages"][0]["content"] for body in server.bodies} == {sent}
    template = load_system_prompt(version)
    assert report["prompt_sha256"] == hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]
    assert report["prompt_date"] == day
    md = ts.to_markdown(report)
    assert f"(템플릿 sha256 {report['prompt_sha256']}, 넣은 날짜 {text or '없음'})" in md
    assert ("날짜 자리에 넣었다" if text else "날짜 자리가 없어") in md


@respx.mock
async def test_llm_error_is_recorded_and_not_scored(settings, tmp_path):
    path = tmp_path / "err.jsonl"
    write_jsonl(path, [row("x-1", "어제 몇 걸음 걸었어?", tool_calls(STEPS_Y))])
    respx.post(CHAT_URL).mock(return_value=httpx.Response(400, json={"error": {"message": "bad request"}}))
    args = ts.parse_args(["--dataset", str(path), "--repeat", "1"])
    items, datasets = ts.load_datasets(args.dataset)
    report, records = await ts.run_eval(settings, args, items, datasets, load_system_prompt("v1"))

    assert (records[0]["correct"], records[0]["error"]) == (None, "llm_error")
    assert records[0]["detail"] == {"code": "invalid_request"}
    s = report["summary"]
    assert s["overall"]["scored"] == 0 and s["llm"]["errors"] == 1 and s["errors"] == {}


@respx.mock
async def test_request_matches_production_first_round(settings):
    """실행기의 요청 본문이 운영 첫 라운드(run_coach → agent_node)와 같아야 한다."""
    route = respx.post(CHAT_URL).mock(side_effect=lambda request: text_response("네."))
    model = create_chat_model(settings, recorder=MetricsRecorder())
    question = "지난주 수면이랑 걸음 수 정리해 줘"
    ctx = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
    try:
        result = await run_coach(
            ctx, question, port=FakeHealthData(), model=model, sink=ListSink(), now=datetime(2026, 10, 22, tzinfo=UTC)
        )
        # run_coach 의 now 는 한국 시간 10월 22일 9시다
        system_prompt = render_system_prompt(load_system_prompt(DEFAULT_PROMPT_VERSION), date(2026, 10, 22))
        await ts.first_round(model, question, system_prompt)
    finally:
        await model.http_async_client.aclose()
    assert result.finish_reason == "stop"
    production, runner = (json.loads(call.request.content) for call in route.calls)
    assert runner == production


@pytest.mark.parametrize("temperature", [None, 0.0, 0.7])
@respx.mock
async def test_make_model_sends_temperature_only_when_given(settings, temperature):
    route = respx.post(CHAT_URL).mock(side_effect=lambda request: text_response("네."))
    log = ts.RequestLog()
    model = ts.make_model(settings, temperature, MetricsRecorder(), log)
    try:
        await ts.first_round(model, "고마워", "시스템 프롬프트")
    finally:
        await model.http_async_client.aclose()
    body = json.loads(route.calls[0].request.content)
    assert ("temperature" in body) is (temperature is not None) and body.get("temperature") == temperature
    assert log.params == [{k: v for k, v in body.items() if k not in ("messages", "tools")}]


def test_main_guard_only_needs_no_llm_settings(monkeypatch, capsys):
    def no_settings():
        raise AssertionError("LLM 을 부르지 않으면 설정을 읽지 않는다")

    monkeypatch.setattr(ts, "get_settings", no_settings)
    ts.main(["--only", "guard", "--repeat", "1"])
    out = capsys.readouterr().out
    assert "| guard |" in out and "LLM 호출 0회" in out and "temperature - (LLM 호출 없음)" in out
    assert "(저장하지 않음. 저장하려면 --save)" in out


def test_main_rejects_concurrency_over_wrapper_limit(settings, monkeypatch):
    limited = settings.model_copy(update={"llm_max_concurrency": 1})
    monkeypatch.setattr(ts, "get_settings", lambda: limited)
    with pytest.raises(SystemExit, match="LLM_MAX_CONCURRENCY"):
        ts.main(["--concurrency", "2", "--limit", "1"])


def test_main_rejects_unknown_prompt_version():
    with pytest.raises(SystemExit, match="v999"):
        ts.main(["--prompt-version", "v999"])


SUPPLEMENT = DATASETS_DIR / "tool_selection_ai_v1.jsonl"


class AnswerKey:
    def __init__(self, items: list[Item], wrong: str) -> None:
        self.expected = {item.question: item.expected for item in items}
        self.wrong = wrong

    def __call__(self, request: httpx.Request) -> httpx.Response:
        question = json.loads(request.content)["messages"][-1]["content"]
        expected = self.expected[question]
        if expected["type"] == "no_tool":
            return text_response("천만에요.")
        if expected["type"] == "clarify":
            return text_response("보통 몇 시에 출근하세요?")
        calls = [(c["name"], c["args"]) for c in expected["tool_calls"]]
        if question == self.wrong:
            calls = [(name, {**args, "period": "this_week"}) for name, args in calls]
        return tool_call_response(*calls)


@respx.mock
def test_main_scores_v1_and_supplement_together(settings, monkeypatch, tmp_path, capsys):
    paths = [V1_DATASET, SUPPLEMENT]
    all_items, _ = ts.load_datasets(paths)
    items = ts.select(all_items, ts.DEFAULT_CATEGORIES)
    wrong = next(item for item in items if item.dataset == SUPPLEMENT.stem)
    respx.post(CHAT_URL).mock(side_effect=AnswerKey(items, wrong.question))
    monkeypatch.setattr(ts, "get_settings", lambda: settings)
    out_dir = tmp_path / "results"
    ts.main(["--dataset", *map(str, paths), "--repeat", "1", "--save", "--out", str(out_dir)])

    report = json.loads(next(out_dir.glob("*_tool_selection.json")).read_text(encoding="utf-8"))
    selected = Counter(item.dataset for item in items)
    assert [(d["name"], d["selected"]) for d in report["datasets"]] == [(p.stem, selected[p.stem]) for p in paths]
    supplement = report["summary"]["by_dataset"][SUPPLEMENT.stem]
    assert (supplement["scored"], supplement["correct"]) == (selected[SUPPLEMENT.stem], selected[SUPPLEMENT.stem] - 1)
    lines = next(out_dir.glob("*_records.jsonl")).read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert {r["dataset"] for r in records} == {p.stem for p in paths}
    assert all(r["correct"] for r in records if r["llm"] and r["question"] != wrong.question)
    out = capsys.readouterr().out
    assert "## 데이터셋별 (proposed 제외)" in out and f"| {wrong.key} | tool | 0/1 |" in out


RESCORE_QUESTION = "지난주 몇 걸음 걸었어?"


def saved_run(settings, monkeypatch, tmp_path, *extra: str):
    """기간 표현으로 답하는 모의 응답으로 측정을 저장하고 (데이터셋 경로, 결과 폴더)를 반환한다."""
    eval_dir = tmp_path / "eval"
    (eval_dir / "datasets").mkdir(parents=True)
    # 결과의 데이터셋 경로는 ai/ 기준이라 다시 채점할 때 그 위치에서 찾는다
    monkeypatch.setattr(ts, "EVAL_DIR", eval_dir)
    path = eval_dir / "datasets" / "p.jsonl"
    write_jsonl(path, [row("p-1", RESCORE_QUESTION, tool_calls(LAST_WEEK_STEPS))])
    respx.post(CHAT_URL).mock(side_effect=FakeLLM({RESCORE_QUESTION: lambda: tool_call_response(steps("weeks_ago:1"))}))
    monkeypatch.setattr(ts, "get_settings", lambda: settings)
    out_dir = tmp_path / "results"
    ts.main(["--dataset", str(path), "--repeat", "2", "--save", "--out", str(out_dir), *extra])
    return path, out_dir


@respx.mock
def test_rescore_scores_saved_outputs_again_without_llm(settings, monkeypatch, tmp_path, capsys):
    _, out_dir = saved_run(settings, monkeypatch, tmp_path, "--scoring", "exact")
    saved = json.loads(next(out_dir.glob("*_tool_selection.json")).read_text(encoding="utf-8"))
    assert saved["period_scoring"] == "exact" and saved["summary"]["overall"]["correct"] == 0
    records = next(out_dir.glob("*_tool_selection_records.jsonl"))
    respx.reset()
    capsys.readouterr()

    ts.main(["--rescore", str(records), "--scoring", "range", "--out", str(out_dir)])
    assert not respx.calls
    rescored = json.loads(next(out_dir.glob("*_tool_selection_rescored_range.json")).read_text(encoding="utf-8"))
    assert rescored["period_scoring"] == "range" and rescored["summary"]["overall"]["correct"] == 2
    assert rescored["rescored"]["changed"] == 2 and rescored["rescored"]["commit"] == COMMIT
    assert rescored["rescored"]["from"].endswith("_tool_selection_records.jsonl")
    # 측정 조건은 그대로 두고 채점만 바꾼다
    for key in ("measured_at", "commit", "datasets", "repeat", "today", "prompt_sha256", "tools_sha256"):
        assert rescored[key] == saved[key], key
    lines = next(out_dir.glob("*_rescored_range_records.jsonl")).read_text(encoding="utf-8").splitlines()
    again = [json.loads(line) for line in lines]
    assert [r["correct"] for r in again] == [True, True]
    assert [r["input_tokens"] for r in again] == [300, 300]
    md = next(out_dir.glob("*_rescored_range.md")).read_text(encoding="utf-8")
    assert "다시 채점" in md and "판정이 바뀐 실행 2회" in md
    assert "저장:" in capsys.readouterr().out


@respx.mock
def test_rescore_stops_when_dataset_changed(settings, monkeypatch, tmp_path):
    path, out_dir = saved_run(settings, monkeypatch, tmp_path)
    write_jsonl(path, [row("p-1", RESCORE_QUESTION, tool_calls(steps("this_week")))])
    records = next(out_dir.glob("*_tool_selection_records.jsonl"))
    with pytest.raises(SystemExit, match="다른 시험지"):
        ts.main(["--rescore", str(records), "--out", str(out_dir)])


@respx.mock
def test_main_saves_json_markdown_and_records(settings, monkeypatch, tmp_path, capsys):
    path = tmp_path / "small.jsonl"
    write_jsonl(path, [E2E_ROWS[0], E2E_ROWS[6]])
    respx.post(CHAT_URL).mock(side_effect=FakeLLM(E2E_REPLIES))
    monkeypatch.setattr(ts, "get_settings", lambda: settings)
    out_dir = tmp_path / "results"
    ts.main(["--dataset", str(path), "--repeat", "2", "--save", "--out", str(out_dir)])

    saved = sorted(p.name for p in out_dir.iterdir())
    assert len(saved) == 3 and all("_tool_selection" in name for name in saved)
    report = json.loads(next(out_dir.glob("*_tool_selection.json")).read_text(encoding="utf-8"))
    assert report["summary"]["overall"]["correct"] == 4 and report["repeat"] == 2
    assert report["commit"] == COMMIT
    lines = next(out_dir.glob("*_records.jsonl")).read_text(encoding="utf-8").splitlines()
    assert Counter(json.loads(line)["id"] for line in lines) == {"e-1": 2, "e-7": 2}
    md = next(out_dir.glob("*_tool_selection.md")).read_text(encoding="utf-8")
    assert md.startswith("# 도구 선택 평가") and f"커밋 `{COMMIT}`" in md
    assert "저장:" in capsys.readouterr().out


# 데이터셋 기준일(2026-10-22 목) 다음 주 월요일부터 일요일까지와 날짜 줄에 들어갈 글. 계산하지 않고 달력의 값을 쓴다
WEEK_TEXT = {
    date(2026, 10, 26): "2026년 10월 26일 월요일",
    date(2026, 10, 27): "2026년 10월 27일 화요일",
    date(2026, 10, 28): "2026년 10월 28일 수요일",
    date(2026, 10, 29): "2026년 10월 29일 목요일",
    date(2026, 10, 30): "2026년 10월 30일 금요일",
    date(2026, 10, 31): "2026년 10월 31일 토요일",
    date(2026, 11, 1): "2026년 11월 1일 일요일",
}
# 데이터셋 문항이 묻는 요일
WEEKDAY_NAMES = {"wed": "수", "fri": "금", "sat": "토", "sun": "일"}
# 기준일마다 "지난 수·금·토·일요일"의 정답 날짜. 질문한 날 이전에 가장 최근에 지난 그 요일이고, 같은 요일이면
# 7일 전이다. 계산하지 않고 달력의 값을 쓴다
LAST_WEEKDAY_ON = {
    date(2026, 10, 26): (date(2026, 10, 21), date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25)),
    date(2026, 10, 27): (date(2026, 10, 21), date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25)),
    date(2026, 10, 28): (date(2026, 10, 21), date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25)),
    date(2026, 10, 29): (date(2026, 10, 28), date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25)),
    date(2026, 10, 30): (date(2026, 10, 28), date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25)),
    date(2026, 10, 31): (date(2026, 10, 28), date(2026, 10, 30), date(2026, 10, 24), date(2026, 10, 25)),
    date(2026, 11, 1): (date(2026, 10, 28), date(2026, 10, 30), date(2026, 10, 31), date(2026, 10, 25)),
}


def one_day(day: date) -> str:
    return f"dates:{day.isoformat()}..{day.isoformat()}"


def last_weekday(day: date, weekday: str) -> date:
    return LAST_WEEKDAY_ON[day][list(WEEKDAY_NAMES).index(weekday)]


@pytest.mark.parametrize(
    ("question", "periods", "reasons"),
    [
        # 기간 코드는 기준일을 따라 다시 계산해도 질문이 묻는 날이다
        ("지난주 걸음 수 알려 줘", ["last_week", "weeks_ago:1"], []),
        ("어제 몇 걸음 걸었어?", ["yesterday"], []),
        ("지난 토요일에 몇 걸음 걸었어?", ["weekday:sat"], []),
        ("지난 토요일이랑 지난 일요일 걸음 수 알려 줘", ["weekday:sat", "weekday:sun"], []),
        # 질문에 적힌 날짜는 기준일이 바뀌어도 그 날짜다
        ("10월 15일 걸음 수 알려 줘", ["date:10-15"], []),
        ("10월 3일부터 10일까지 걸음 수 보여 줘", ["dates:10-03..10-10"], []),
        ("12월 20일부터 1월 5일까지 걸음 수 평균 알려 줘", ["dates:12-20..01-05"], []),
        # 데이터셋 기준일로 계산한 정답
        ("10월 21일 걸음 수 알려 줘", ["yesterday"], ["period_for_question_date"]),
        ("지난 수요일 걸음 수 알려 줘", ["weekday:wed", "date:10-14"], ["date_not_in_question"]),
        ("10월 3일부터 오늘까지 걸음 수 보여 줘", ["dates:10-03..10-22"], ["date_not_in_question"]),
        ("지난 토요일에 몇 걸음 걸었어?", ["last_week"], ["weekday_question"]),
        ("어제 일요일에 몇 걸음 걸었어?", ["yesterday"], ["weekday_question"]),
        # 주를 붙여 말한 요일은 요일 하루로도 주로도 판정하지 않는다
        ("지난주 토요일에 몇 걸음 걸었어?", ["last_week", "date:10-17"], ["date_not_in_question", "weekday_question"]),
        ("지난주 토요일에 몇 걸음 걸었어?", ["weekday:sat"], ["weekday_question"]),
    ],
)
def test_fixed_answer_reasons(question, periods, reasons):
    first, *rest = periods
    item = make_item("tool", tool_calls(steps(first)), [[steps(p)] for p in rest], question=question)
    assert ts.fixed_answer_reasons(item) == reasons


def test_fixed_answer_reasons_check_both_comparison_periods():
    question = "10월 21일이랑 오늘 걸음 수 비교해 줘"
    mixed = ("compare_periods", {"metric": "steps", "current": "today", "baseline": "date:10-21"})
    assert ts.fixed_answer_reasons(make_item("tool", tool_calls(mixed), question=question)) == [
        "period_for_question_date"
    ]
    dated = ("compare_periods", {"metric": "steps", "current": "date:10-22", "baseline": "date:10-21"})
    assert ts.fixed_answer_reasons(make_item("tool", tool_calls(dated), question=question)) == ["date_not_in_question"]


@pytest.mark.parametrize("path", sorted(DATASETS_DIR.glob("tool_selection_*.jsonl")), ids=lambda path: path.stem)
def test_dataset_day_runs_every_item_as_written(path):
    items, _ = ts.load_datasets([path])
    day = ts.items_for_day(items, ts.DEFAULT_TODAY)
    assert (day.excluded, day.adjusted) == ({}, {})
    assert len(day.items) == len(items) and all(a is b for a, b in zip(day.items, items, strict=True))


# 데이터셋 기준일이 아닌 날, 정답·허용 답안에 데이터셋 기준일로 계산한 값이 있어 실행하지 않는 문항과 이유.
# 문항마다 질문과 정답·허용 답안을 읽고 정한 목록이다. proposed 는 채점하지 않아 빼지 않는다
EXCLUDED_ON_OTHER_DAYS = {
    "tool_selection_v1": {
        "synth-06-001": ["weekday_question"],  # 어제 일요일 → yesterday
        "synth-06-003": ["weekday_question"],  # 지난주 월요일부터 일요일까지 → last_week
    },
    "tool_selection_v1.1": {
        "synth-06-001": ["weekday_question"],
        "synth-06-003": ["weekday_question"],
    },
    "tool_selection_v1.2": {
        "synth-06-001": ["weekday_question"],
        "synth-06-003": ["weekday_question"],
    },
    # 06-020 의 토·일 이틀 허용 답안은 weeks_ago:1.sat..weeks_ago:1.sun 이라 기준일마다 다시 계산되어 빠지지 않는다
    "tool_selection_v1.3": {
        "synth-06-001": ["weekday_question"],
        "synth-06-003": ["weekday_question"],
    },
    "tool_selection_ai_v1": {},
    "tool_selection_ai_exercise_v1": {
        "synth-06-202": ["weekday_question"],  # 지난주 토요일 → last_week, last_7_days
        "synth-09-201": ["period_for_question_date"],  # 10월 19일 → this_week, last_7_days
    },
    "tool_selection_ai_exercise_v1.1": {
        "synth-06-202": ["weekday_question"],  # 위에 더해 weekday:sat
        "synth-09-201": ["period_for_question_date"],  # 정답 this_week 가 기준일로 계산한 값이다
    },
    "tool_selection_ai_period_v1": {
        "synth-07-302": ["period_for_question_date"],  # 10월 21일 → yesterday
    },
    "tool_selection_ai_period_v2": {
        "synth-07-302": ["period_for_question_date"],
        "synth-01-304": ["date_not_in_question", "weekday_question"],  # 지난주 토요일 → last_week, date:10-17
    },
    "tool_selection_ai_period_v2.1": {
        "synth-07-302": ["period_for_question_date"],
        "synth-01-304": ["date_not_in_question", "weekday_question"],
    },
    "tool_selection_ai_period_v2.2": {
        "synth-07-302": ["period_for_question_date"],
        "synth-01-304": ["date_not_in_question", "weekday_question"],
        # 지지난 주말 → 허용 답안 dates:10-10..10-11. 남은 정답 weeks_ago:2 로만 판정하면 주말 이틀을 고른 답이 틀린다
        "synth-01-305": ["date_not_in_question"],
        "synth-02-304": ["date_not_in_question", "weekday_question"],  # 지난주 월요일 → weeks_ago:1, date:10-12
    },
    # 주와 요일을 말한 질문은 정답이 weeks_ago:N.요일·시작..끝 이어도 "지난 X요일"이 아니라 weekday_question 으로 빠진다
    "tool_selection_ai_period_v2.3": {
        "synth-07-302": ["period_for_question_date"],
        "synth-01-304": ["date_not_in_question", "weekday_question"],
        "synth-01-305": ["date_not_in_question"],
        "synth-02-304": ["date_not_in_question", "weekday_question"],
        "synth-06-304": ["weekday_question"],  # 지지난주 목요일부터 어제까지
        "synth-05-305": ["weekday_question"],  # 지난주 수요일부터 오늘까지
        "synth-09-304": ["weekday_question"],  # 지지난주 월요일부터 지난주 수요일까지
        "synth-07-305": ["date_not_in_question"],  # 10월 15일부터 어제까지 → date:10-15..days_ago:1
        "synth-10-305": ["weekday_question"],  # 지지난주·지난주 월요일부터 수요일까지 비교
        "synth-04-305": ["weekday_question"],  # 지지난주 화요일
        "synth-03-305": ["weekday_question"],  # 지난주 일요일
    },
    "tool_selection_ai_period_v2.4": {
        "synth-07-302": ["period_for_question_date"],
        "synth-01-304": ["date_not_in_question", "weekday_question"],
        "synth-01-305": ["date_not_in_question"],
        "synth-02-304": ["date_not_in_question", "weekday_question"],
        "synth-06-304": ["weekday_question"],
        "synth-05-305": ["weekday_question"],
        "synth-09-304": ["weekday_question"],
        "synth-07-305": ["date_not_in_question"],
        "synth-10-305": ["weekday_question"],
        "synth-04-305": ["weekday_question"],
        "synth-03-305": ["weekday_question"],
    },
    "tool_selection_ai_exercise_v1.2": {
        "synth-06-202": ["weekday_question"],  # 지난주 토요일 → weeks_ago:1.sat 과 v1.1 의 정답·허용 답안
        "synth-09-201": ["period_for_question_date"],
    },
    "tool_selection_ai_guard_v1": {},
    "tool_selection_ai_guard_v1.1": {},
    "tool_selection_ai_score_v1": {},
    "tool_selection_ai_daily_message_v1": {},
}
# 데이터셋 기준일이 아닌 날, "지난 X요일"만 말해 데이터셋 기준일로 계산한 답안을 빼고 그 요일 정답을 넣어 실행하는
# 문항과 뺀 답안의 이유
ADJUSTED_ON_OTHER_DAYS = {
    "tool_selection_v1": {"synth-07-008": ["weekday_question"]},  # 지난 일요일 → last_week
    "tool_selection_v1.1": {
        "synth-06-019": ["weekday_question"],  # 지난 토요일 → last_week
        "synth-07-008": ["weekday_question"],
    },
    "tool_selection_ai_period_v2.2": {
        "synth-05-304": ["date_not_in_question"],  # 지난 수요일의 다른 해석 date:10-14
    },
    "tool_selection_ai_period_v2.3": {"synth-05-304": ["date_not_in_question"]},
    # v2.4 는 date:10-14 허용 답안을 빼서 05-304 를 고쳐 넣지 않고 그대로 실행한다
}


@pytest.mark.parametrize("name", sorted(EXCLUDED_ON_OTHER_DAYS))
def test_other_base_dates_skip_or_adjust_items_fixed_to_dataset_day(name):
    items, _ = ts.load_datasets([DATASETS_DIR / f"{name}.jsonl"])
    excluded = {f"{name}/{item_id}": reasons for item_id, reasons in EXCLUDED_ON_OTHER_DAYS[name].items()}
    adjusted = {f"{name}/{item_id}": r for item_id, r in ADJUSTED_ON_OTHER_DAYS.get(name, {}).items()}
    for day in WEEK_TEXT:
        fitted = ts.items_for_day(items, day)
        assert (fitted.excluded, fitted.adjusted) == (excluded, adjusted), day
        assert [item.key for item in fitted.items] == [item.key for item in items if item.key not in excluded]
        # 답안을 바꾸지 않은 문항은 데이터셋 문항 그대로다
        same = {item.key: item for item in items}
        assert all(item is same[item.key] for item in fitted.items if item.key not in adjusted)


SAT_SESSIONS = ("get_exercise_sessions", {"period": "last_week"})


@pytest.mark.parametrize(
    ("question", "expected", "acceptable", "references"),
    [
        # 정답이 기준일로 계산한 기간이면 "지난 X요일" 정답이 정답 자리에 들어간다
        (
            "지난 토요일 등산 몇 시간 했어?",
            [SAT_SESSIONS],
            [],
            [[("get_exercise_sessions", {"period": "weekday:sat"})]],
        ),
        # 기준일로 계산한 허용 답안만 빠진다. "지난 X요일" 정답은 이미 있어 더하지 않는다
        (
            "지난 수요일 걸음 수 알려 줘",
            [steps("weekday:wed")],
            [[steps("date:10-14")], [("get_daily_trend", {"metric": "steps", "period": "date:10-14"})]],
            [[steps("weekday:wed")], [("get_daily_trend", {"metric": "steps", "period": "weekday:wed"})]],
        ),
    ],
)
def test_items_for_day_rewrites_answers_fixed_to_dataset_day(question, expected, acceptable, references):
    item = make_item("tool", tool_calls(*expected), acceptable, question=question)
    for day in WEEK_TEXT:
        fitted = ts.items_for_day([item], day)
        assert fitted.excluded == {} and list(fitted.adjusted) == [item.key]
        [answer_item] = fitted.items
        assert answer_item.references == [calls(*ref) for ref in references]
        assert (answer_item.question, answer_item.category) == (item.question, item.category)


@pytest.mark.parametrize(
    ("question", "periods"),
    [
        ("10월 21일 걸음 수 알려 줘", ["yesterday"]),
        # 주를 붙여 말한 요일은 "지난 X요일" 정답을 쓰지 않는다
        ("지난주 토요일에 몇 걸음 걸었어?", ["last_week", "date:10-17"]),
        # 질문의 날짜를 기간 코드로 적은 정답이면 남은 허용 답안이 있어도 뺀다
        ("10월 19일 월요일에 몇 걸음 걸었어?", ["this_week", "date:10-19"]),
        ("어제 일요일에 몇 걸음 걸었어?", ["yesterday"]),
        # "지난 X요일"이 아니면 허용 답안 하나만 기준일로 계산한 값이어도 뺀다. 남은 정답으로만 판정하면
        # 주말 이틀을 고른 답이 틀린다
        ("지지난 주말에 몇 걸음 걸었어?", ["weeks_ago:2", "dates:10-10..10-11"]),
    ],
)
def test_items_for_day_skips_items_whose_answer_is_fixed_to_dataset_day(question, periods):
    first, *rest = periods
    item = make_item("tool", tool_calls(steps(first)), [[steps(p)] for p in rest], question=question)
    for day in WEEK_TEXT:
        fitted = ts.items_for_day([item], day)
        assert fitted.items == [] and list(fitted.excluded) == [item.key] and fitted.adjusted == {}


def test_rewritten_answer_is_same_on_every_other_base_date():
    # 정답을 날짜가 아닌 weekday: 로 넣어, 기준일이 다른 결과끼리 문항 정의 sha256 이 같다
    item = make_item("tool", tool_calls(SAT_SESSIONS), question="지난 토요일 등산 몇 시간 했어?")
    shas = {ts.definition_sha256(ts.items_for_day([item], day).items[0]) for day in WEEK_TEXT}
    assert len(shas) == 1 and shas != {ts.definition_sha256(item)}


# "지난 X요일"만 말한 데이터셋 문항(proposed 제외)과 데이터셋 기준일로 계산해 다른 기준일에서 빠지는 답안의 기간
LAST_WEEKDAY_ITEMS = [
    ("tool_selection_v1", "synth-07-008", "sun", "last_week"),
    ("tool_selection_v1.1", "synth-06-019", "sat", "last_week"),
    ("tool_selection_v1.1", "synth-07-008", "sun", "last_week"),
    ("tool_selection_v1.2", "synth-06-019", "sat", None),
    ("tool_selection_v1.2", "synth-07-008", "sun", None),
    ("tool_selection_v1.3", "synth-06-019", "sat", None),
    ("tool_selection_v1.3", "synth-07-008", "sun", None),
    ("tool_selection_ai_period_v2.1", "synth-04-304", "sat", None),
    ("tool_selection_ai_period_v2.1", "synth-07-304", "sun", None),
    ("tool_selection_ai_period_v2.2", "synth-04-304", "sat", None),
    ("tool_selection_ai_period_v2.2", "synth-05-304", "wed", "date:10-14"),
    ("tool_selection_ai_period_v2.2", "synth-07-304", "sun", None),
    ("tool_selection_ai_period_v2.2", "synth-10-304", "fri", None),
    ("tool_selection_ai_period_v2.3", "synth-04-304", "sat", None),
    ("tool_selection_ai_period_v2.3", "synth-05-304", "wed", "date:10-14"),
    ("tool_selection_ai_period_v2.3", "synth-07-304", "sun", None),
    ("tool_selection_ai_period_v2.3", "synth-10-304", "fri", None),
    ("tool_selection_ai_period_v2.4", "synth-04-304", "sat", None),
    ("tool_selection_ai_period_v2.4", "synth-05-304", "wed", None),
    ("tool_selection_ai_period_v2.4", "synth-07-304", "sun", None),
    ("tool_selection_ai_period_v2.4", "synth-10-304", "fri", None),
    ("tool_selection_ai_score_v1", "synth-10-501", "fri", None),
]


def with_period(reference: list[dict], period: str) -> Outcome:
    return Outcome(llm=True, calls=tuple({**c, "args": {**c["args"], "period": period}} for c in reference))


@pytest.mark.parametrize(("name", "item_id", "weekday", "dataset_day_only"), LAST_WEEKDAY_ITEMS)
def test_last_weekday_answer_is_computed_for_each_base_date(name, item_id, weekday, dataset_day_only):
    items, _ = ts.load_datasets([DATASETS_DIR / f"{name}.jsonl"])
    item = next(item for item in items if item.id == item_id)
    assert f"지난 {WEEKDAY_NAMES[weekday]}요일" in item.question
    # 데이터셋 기준일에는 문항을 그대로 쓰고 데이터셋 정답·허용 답안이 모두 맞다
    assert ts.items_for_day([item], ts.DEFAULT_TODAY).items[0] is item
    for reference in item.references:
        assert ts.score(item, Outcome(llm=True, calls=tuple(reference)), today=ts.DEFAULT_TODAY).correct
    for day in WEEK_TEXT:
        fitted = ts.items_for_day([item], day)
        assert fitted.excluded == {} and list(fitted.adjusted) == ([item.key] if dataset_day_only else [])
        [answer_item] = fitted.items
        right = last_weekday(day, weekday)
        for reference in item.references:
            # 데이터셋 문항이 쓰는 도구마다 그 요일 하루로 답하면 정답이다
            assert ts.score(answer_item, with_period(reference, one_day(right)), today=day).correct, day
            assert ts.score(answer_item, with_period(reference, f"weekday:{weekday}"), today=day).correct, day
            # 한 주 전 그 요일과 이번 주는 틀린 답이다
            week_before = one_day(right - timedelta(days=7))
            assert not ts.score(answer_item, with_period(reference, week_before), today=day).correct, day
            assert not ts.score(answer_item, with_period(reference, "weeks_ago:0"), today=day).correct, day
            if dataset_day_only is not None and reference[0]["args"]["period"] == dataset_day_only:
                # 데이터셋 기준일로 계산한 답안을 쓴 그대로 답하면 틀린 답이다
                assert not ts.score(answer_item, Outcome(llm=True, calls=tuple(reference)), today=day).correct


def sessions(period: str) -> Outcome:
    return Outcome(llm=True, calls=({"name": "get_exercise_sessions", "args": {"period": period}},))


# 기준일마다 v1.3 synth-06-020 "지난 주말"에 맞는 기간과 틀린 기간. 달력의 값을 쓴다. 다른 기준일(10-26~11-01)의
# 지난주는 10-19~10-25, 두 날 모두 질문한 날보다 앞인 가장 최근의 토·일은 10-24~10-25 다
LAST_WEEKEND_ANSWERS = {
    ts.DEFAULT_TODAY: (
        ["last_week", "weeks_ago:1", "weeks_ago:1.sat..weeks_ago:1.sun", "dates:10-17..10-18", "dates:10-12..10-18"],
        ["weekday:sat", "weekday:sun", "weeks_ago:0", "this_week", "dates:10-10..10-11"],
    ),
    **{
        day: (
            ["weeks_ago:1", "weeks_ago:1.sat..weeks_ago:1.sun", "dates:10-24..10-25", "dates:10-19..10-25"],
            ["weeks_ago:0", "dates:10-17..10-18", "dates:10-12..10-18"],
        )
        for day in WEEK_TEXT
    },
}


@pytest.mark.parametrize("day", sorted(LAST_WEEKEND_ANSWERS))
def test_v1_3_last_weekend_accepts_last_week_and_its_weekend(day):
    """v1.3 synth-06-020 지난 주말 → 지난주 전체와 지난주 토·일 이틀이 맞고, 하루나 이번 주는 틀린다.

    다른 기준일에도 빠지지 않고 그 기준일의 지난주·지난 주말로 채점한다.
    """
    items, _ = ts.load_datasets([DATASETS_DIR / "tool_selection_v1.3.jsonl"])
    item = next(item for item in items if item.id == "synth-06-020")
    assert ts.items_for_day([item], day).items == [item]
    right, wrong = LAST_WEEKEND_ANSWERS[day]
    for period in right:
        assert ts.score(item, sessions(period), today=day).correct, (day, period)
    for period in wrong:
        assert not ts.score(item, sessions(period), today=day).correct, (day, period)
    # v1.2 는 토·일 이틀을 조회한 답을 틀린 답으로 채점한다
    old_items, _ = ts.load_datasets([DATASETS_DIR / "tool_selection_v1.2.jsonl"])
    old = next(old for old in old_items if old.id == "synth-06-020")
    assert old.acceptable == [] and not ts.score(old, sessions("weeks_ago:1.sat..weeks_ago:1.sun"), today=day).correct


WEEK_QUESTIONS = {
    "sat": "지난 토요일 등산 몇 시간 했어?",
    "wed": "지난 수요일 심박 평균 알려 줘",
    "yesterday": "어제 몇 걸음 걸었어?",
    "fixed": "10월 21일 걸음 수 알려 줘",
}
HEART_WED = ("get_metric_summary", {"metric": "heart_rate", "period": "weekday:wed"})


def week_rows() -> list[dict]:
    """정답을 데이터셋 기준일로 계산한 문항이 섞인 데이터셋. w-1 정답 last_week, w-2 허용 답안 date:10-14,
    w-4 정답 yesterday 가 데이터셋 기준일로 계산한 기간이다."""
    heart_oct14 = ("get_metric_summary", {"metric": "heart_rate", "period": "date:10-14"})
    return [
        row("w-1", WEEK_QUESTIONS["sat"], tool_calls(SAT_SESSIONS)),
        row("w-2", WEEK_QUESTIONS["wed"], tool_calls(HEART_WED), acceptable=[[heart_oct14]]),
        row("w-3", WEEK_QUESTIONS["yesterday"], tool_calls(STEPS_Y)),
        row("w-4", WEEK_QUESTIONS["fixed"], tool_calls(STEPS_Y)),
    ]


@pytest.mark.parametrize("day", list(WEEK_TEXT), ids=str)
@respx.mock
async def test_base_date_sets_date_line_period_and_verdict(settings, tmp_path, day):
    path = tmp_path / "week.jsonl"
    write_jsonl(path, week_rows())
    sat, wed = last_weekday(day, "sat"), last_weekday(day, "wed")
    # 모의 응답은 기준일로 센 날짜를 기간 인자에 그대로 넣는다
    server = FakeLLM(
        {
            WEEK_QUESTIONS["sat"]: lambda: tool_call_response(("get_exercise_sessions", {"period": one_day(sat)})),
            WEEK_QUESTIONS["wed"]: lambda: tool_call_response(
                ("get_metric_summary", {"metric": "heart_rate", "period": one_day(wed)})
            ),
            WEEK_QUESTIONS["yesterday"]: lambda: tool_call_response(steps(one_day(day - timedelta(days=1)))),
        }
    )
    respx.post(CHAT_URL).mock(side_effect=server)
    args = ts.parse_args(["--dataset", str(path), "--repeat", "1", "--today", day.isoformat()])
    items, datasets = ts.load_datasets(args.dataset)
    report, records = await ts.run_eval(settings, args, items, datasets, load_system_prompt(DEFAULT_PROMPT_VERSION))

    [system] = {body["messages"][0]["content"] for body in server.bodies}
    assert system.endswith(f"\n\n오늘은 {WEEK_TEXT[day]}이다.")
    assert (report["today"], report["prompt_date"]) == (day.isoformat(), day.isoformat())
    assert [(r["id"], r["correct"]) for r in records] == [("w-1", True), ("w-2", True), ("w-3", True)]
    assert report["dataset_today"] == "2026-10-22"
    assert report["excluded"] == {"week/w-4": ["period_for_question_date"]}
    assert report["adjusted"] == {"week/w-1": ["weekday_question"], "week/w-2": ["date_not_in_question"]}
    assert report["last_weekdays"] == {"wed": wed.isoformat(), "sat": sat.isoformat()}
    md = ts.to_markdown(report)
    assert "그날로 계산한 정답·허용 답안이 있어 이 기준일에 판정할 수 없는 1문항은 실행하지 않았다" in md
    assert "  - `w-4` 질문의 날짜를 기간 코드로 적음" in md
    assert "그날로 계산한 답안을 빼고 판정한 2문항" in md
    assert "  - `w-1` 요일을 말한 질문의 정답이 '지난 X요일' 하루(weekday:X)가 아님" in md
    assert "  - `w-2` 질문에 없는 날짜를 정답에 적음" in md
    wed_text, sat_text = (f"{d.month}월 {d.day}일" for d in (wed, sat))
    assert f'"지난 X요일" 정답(질문한 날 이전에 가장 최근에 지난 그 요일): 수요일 {wed_text}, 토요일 {sat_text}' in md


@respx.mock
async def test_base_date_without_fixed_answers_says_so(settings, tmp_path):
    path = tmp_path / "plain.jsonl"
    write_jsonl(path, [row("p-1", WEEK_QUESTIONS["yesterday"], tool_calls(STEPS_Y))])
    respx.post(CHAT_URL).mock(side_effect=FakeLLM({WEEK_QUESTIONS["yesterday"]: lambda: tool_call_response(STEPS_Y)}))
    args = ts.parse_args(["--dataset", str(path), "--repeat", "1", "--today", "2026-10-26"])
    items, datasets = ts.load_datasets(args.dataset)
    report, _ = await ts.run_eval(settings, args, items, datasets, load_system_prompt(DEFAULT_PROMPT_VERSION))
    assert (report["excluded"], report["adjusted"], report["last_weekdays"]) == ({}, {}, {})
    md = ts.to_markdown(report)
    assert "- 데이터셋 정답의 기준일: 2026-10-22. 그날로 계산한 정답·허용 답안이 있는 문항은 없다" in md
    assert "지난 X요일" not in md


@respx.mock
async def test_report_without_base_date_option_has_no_exclusion(settings, tmp_path):
    report, records, _ = await run_e2e(settings, tmp_path)
    assert not {"dataset_today", "excluded", "adjusted", "last_weekdays"} & report.keys()
    assert len(records) == len(E2E_ROWS) * 2
    assert "데이터셋 정답의 기준일" not in ts.to_markdown(report)


@respx.mock
async def test_dataset_day_given_as_option_matches_default(settings, tmp_path):
    default, default_records, _ = await run_e2e(settings, tmp_path)
    given, given_records, _ = await run_e2e(settings, tmp_path, "--today", "2026-10-22")
    assert given.keys() == default.keys()
    assert given["summary"]["overall"] == default["summary"]["overall"]
    assert [(r["id"], r["run"], r["correct"], r["signature"]) for r in given_records] == [
        (r["id"], r["run"], r["correct"], r["signature"]) for r in default_records
    ]


FIXED_QUESTION = WEEK_QUESTIONS["fixed"]


def base_date_run(settings, monkeypatch, tmp_path) -> tuple[Path, FakeLLM]:
    """기준일 2026-10-26 측정을 저장하고 (결과 폴더, 모의 서버)를 반환한다. d-2 는 빠지고 d-3 은 정답이 바뀐다."""
    eval_dir = tmp_path / "eval"
    (eval_dir / "datasets").mkdir(parents=True)
    monkeypatch.setattr(ts, "EVAL_DIR", eval_dir)
    path = eval_dir / "datasets" / "d.jsonl"
    write_jsonl(
        path,
        [
            row("d-1", RESCORE_QUESTION, tool_calls(LAST_WEEK_STEPS)),
            row("d-2", FIXED_QUESTION, tool_calls(STEPS_Y)),
            row("d-3", WEEK_QUESTIONS["sat"], tool_calls(SAT_SESSIONS)),
        ],
    )
    server = FakeLLM(
        {
            RESCORE_QUESTION: lambda: tool_call_response(steps("weeks_ago:1")),
            WEEK_QUESTIONS["sat"]: lambda: tool_call_response(("get_exercise_sessions", {"period": "weekday:sat"})),
        }
    )
    respx.post(CHAT_URL).mock(side_effect=server)
    monkeypatch.setattr(ts, "get_settings", lambda: settings)
    out_dir = tmp_path / "results"
    ts.main(["--dataset", str(path), "--repeat", "2", "--today", "2026-10-26", "--save", "--out", str(out_dir)])
    return out_dir, server


@respx.mock
def test_other_base_date_records_changed_items_and_rescore_keeps_them(settings, monkeypatch, tmp_path, capsys):
    out_dir, server = base_date_run(settings, monkeypatch, tmp_path)

    assert [body["messages"][-1]["content"] for body in server.bodies] == [RESCORE_QUESTION, WEEK_QUESTIONS["sat"]] * 2
    report = json.loads(next(out_dir.glob("*_tool_selection.json")).read_text(encoding="utf-8"))
    assert report["excluded"] == {"d/d-2": ["period_for_question_date"]} and report["dataset_today"] == "2026-10-22"
    assert report["adjusted"] == {"d/d-3": ["weekday_question"]} and report["last_weekdays"] == {"sat": "2026-10-24"}
    assert report["datasets"][0]["selected"] == 2 and report["summary"]["overall"]["correct"] == 4
    md = next(out_dir.glob("*_tool_selection.md")).read_text(encoding="utf-8")
    assert "그날로 계산한 정답·허용 답안이 있어 이 기준일에 판정할 수 없는 1문항은 실행하지 않았다" in md
    assert "  - `d-2` 질문의 날짜를 기간 코드로 적음" in md
    assert "  - `d-3` 요일을 말한 질문의 정답이 '지난 X요일' 하루(weekday:X)가 아님" in md

    records = next(out_dir.glob("*_tool_selection_records.jsonl"))
    respx.reset()
    ts.main(["--rescore", str(records), "--out", str(out_dir)])
    rescored = json.loads(next(out_dir.glob("*_rescored_range.json")).read_text(encoding="utf-8"))
    for key in ("excluded", "adjusted", "last_weekdays"):
        assert rescored[key] == report[key], key
    assert rescored["rescored"]["changed"] == 0 and rescored["summary"]["overall"] == report["summary"]["overall"]
    assert "저장:" in capsys.readouterr().out


@respx.mock
def test_rescore_stops_when_skipped_items_differ_from_measurement(settings, monkeypatch, tmp_path):
    out_dir, _ = base_date_run(settings, monkeypatch, tmp_path)
    report_path = next(out_dir.glob("*_tool_selection.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.write_text(json.dumps({**report, "excluded": {}}, ensure_ascii=False), encoding="utf-8")
    records = next(out_dir.glob("*_tool_selection_records.jsonl"))
    with pytest.raises(SystemExit, match="뺀 문항이 측정 때와 달라"):
        ts.main(["--rescore", str(records), "--out", str(out_dir)])


def test_main_stops_when_no_item_can_be_scored_on_base_date(tmp_path, monkeypatch):
    def no_settings():
        raise AssertionError("판정할 문항이 없으면 설정을 읽지 않는다")

    monkeypatch.setattr(ts, "get_settings", no_settings)
    path = tmp_path / "fixed.jsonl"
    write_jsonl(path, [row("f-1", FIXED_QUESTION, tool_calls(STEPS_Y))])
    with pytest.raises(SystemExit, match="판정할 수 있는 문항이 없습니다"):
        ts.main(["--dataset", str(path), "--today", "2026-10-26"])
    # 데이터셋 기준일에는 그대로 실행하는 문항이다
    assert ts.items_for_day(ts.load_datasets([path])[0], ts.DEFAULT_TODAY).excluded == {}
