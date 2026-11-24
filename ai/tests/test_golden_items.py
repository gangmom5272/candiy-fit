"""코칭 골든셋 문항 읽기와 형식 검사(eval/golden/items.py) 테스트."""

import copy
import datetime as dt
import hashlib
import json

import pytest

from coach_ai.agent.context import CoachContext
from coach_ai.agent.rules.guard import GuardCategory
from eval.golden import items as gi

V1_PATH = gi.GOLDEN_DIR / gi.SETS["v1"].file
V2_PATH = gi.GOLDEN_DIR / gi.SETS["v2"].file
V2_1_PATH = gi.GOLDEN_DIR / gi.SETS["v2.1"].file


def rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def row(path, item_id: str) -> dict:
    return copy.deepcopy(next(r for r in rows(path) if r["id"] == item_id))


def write(tmp_path, *lines: dict):
    path = tmp_path / "golden.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines), encoding="utf-8")
    return path


def by_id(items) -> dict[str, gi.GoldenItem]:
    return {item.id: item for item in items}


def test_reads_v1_and_v2_files():
    items, infos = gi.load_sets(["v1", "v2"])
    assert [(info["version"], info["rows"]) for info in infos] == [("v1", 58), ("v2", 30)]
    assert infos[0]["path"] == "eval/datasets/golden/coaching_golden_v1.jsonl"
    assert infos[0]["sha256"] == hashlib.sha256(V1_PATH.read_bytes()).hexdigest()
    v1 = [item for item in items if item.version == "v1"]
    v2 = [item for item in items if item.version == "v2"]
    assert sum(len(item.facts) for item in v1) == 70 and sum(len(item.facts) for item in v2) == 26
    assert sum(len(item.statuses) for item in v1) == 32 and sum(len(item.statuses) for item in v2) == 11


def test_latest_versions_are_v1_1_and_v2_1():
    assert gi.LATEST == ("v1.1", "v2.1")
    items, infos = gi.load_sets(gi.LATEST)
    assert [(info["version"], info["rows"]) for info in infos] == [("v1.1", 58), ("v2.1", 31)]
    assert [info["name"] for info in infos] == ["coaching_golden_v1.1", "coaching_golden_v2.1"]
    assert infos[0]["path"] == "eval/datasets/golden/coaching_golden_v1.1.jsonl"
    latest = by_id(items)
    first = latest["golden-synth-01-001"]
    assert (first.version, first.form, first.key) == ("v1.1", "v1", "v1.1/golden-synth-01-001")
    c19 = latest["golden2-c19"]
    assert (c19.version, c19.form, c19.must) == ("v2.1", "v2", ("few_days_hedged",))
    # 이전 버전은 계속 읽을 수 있고, 형식은 같은 계열의 최신 버전과 같다
    old = by_id(gi.load_sets(["v1"])[0])["golden-synth-01-001"]
    assert (old.version, old.form, old.key) == ("v1", "v1", "v1/golden-synth-01-001")


def test_definition_sha256_ignores_version_and_note():
    old = by_id(gi.load_sets(["v1"])[0])
    new = by_id(gi.load_sets(["v1.1"])[0])
    # 바뀌지 않은 문항은 버전이 달라도 정의가 같다
    assert gi.definition_sha256(old["golden-synth-01-001"]) == gi.definition_sha256(new["golden-synth-01-001"])
    assert len(gi.definition_sha256(old["golden-synth-01-001"])) == 12
    # 상태가 바뀐 문항(진행 중 상태 제외)과 질문이 바뀐 문항(07-008)은 정의가 다르다
    for item_id in ("golden-synth-01-005", "golden-synth-07-008"):
        assert gi.definition_sha256(old[item_id]) != gi.definition_sha256(new[item_id]), item_id


def test_same_family_versions_cannot_be_loaded_together():
    with pytest.raises(ValueError, match="겹친다"):
        gi.load_sets(["v1", "v1.1"])


def test_v1_facts_and_v2_grounding_have_same_structure():
    items = by_id(gi.load_sets(["v1", "v2"])[0])
    this_week, last_week, diff = items["golden-synth-01-005"].facts
    assert (this_week.field, this_week.value, this_week.unit, this_week.tolerance) == ("avg", 9787.67, "steps", 0)
    assert (diff.field, diff.period, diff.baseline, diff.value) == ("diff", "this_week", "last_week", 712.67)
    [grounding] = items["golden2-c01"].facts
    assert isinstance(grounding, gi.Fact) and type(grounding) is type(this_week)
    assert (grounding.metric, grounding.period, grounding.field, grounding.value) == (
        "sleep",
        "last_7_days",
        "avg",
        316.29,
    )
    clock = items["golden-synth-03-008"].facts[0]
    assert (clock.value, clock.unit, clock.tolerance, clock.date) == ("02:21", "time_kst", 5, dt.date(2026, 10, 21))
    progress = items["golden-synth-01-002"].facts[0]
    assert progress.as_of == dt.datetime(2026, 10, 22, 11, 55, 48, tzinfo=dt.UTC)
    measured = items["golden-synth-edge-001"].facts[1]
    assert measured.requires == ("value:oxygen_saturation.measured_min",)
    # 기록이 없는 날은 값이 None 이다
    assert items["golden-synth-09-001"].facts[0].value is None


def test_statuses_keep_dates_and_times():
    items = by_id(gi.load_sets(["v1"])[0])
    missing, unsynced = items["golden-synth-09-005"].statuses
    assert missing.status == "missing_days" and len(missing.dates) == 11 and missing.dates[0] == dt.date(2026, 9, 26)
    assert unsynced.status == "unsynced" and unsynced.dates == (dt.date(2026, 10, 21), dt.date(2026, 10, 22))
    linked = items["golden-synth-10-010"].statuses[1]
    assert (linked.status, linked.linked_from) == ("before_link", dt.date(2026, 9, 18))
    slow = items["golden-synth-edge-002"].statuses[0]
    assert (slow.status, slow.requires) == ("slow_walk_excluded", ("tool:get_exercise_sessions",))


def test_v2_must_not_is_read_from_rubric():
    items = by_id(gi.load_sets(["v1", "v2"])[0])
    v1 = items["golden-synth-edge-003"]
    assert v1.must_not == ("invented_number", "diagnosis", "missing_as_zero", "in_progress_as_total")
    assert (v1.must, v1.subtype, v1.expected_guard, v1.requires) == ((), None, None, ())
    c01 = items["golden2-c01"]
    assert c01.must == ("no_blame", "small_action")
    assert c01.must_not == ("diagnosis", "causal_claim", "blame")
    assert (c01.category, c01.subtype, c01.expected_guard) == ("coaching", "goal_missed", None)
    assert items["golden2-c02"].requires == ("tool:compare_sleep_heart_rate",)


def test_v2_guard_expectations_use_guard_categories():
    items = by_id(gi.load_sets(["v2"])[0])
    assert items["golden2-s01"].expected_guard == gi.GuardExpectation("emergency", "rule")
    assert items["golden2-s05"].expected_guard == gi.GuardExpectation("emergency", "llm")
    assert items["golden2-s10"].expected_guard == gi.GuardExpectation(None, "none")
    assert gi.GUARD_CATEGORIES == (
        "emergency",
        "mental_crisis",
        "diagnosis",
        "medication",
        "out_of_scope",
        "adversarial",
    )
    assert set(gi.GUARD_CATEGORIES) == set(GuardCategory.__args__)


def test_history_pairs_become_history_turns():
    item = by_id(gi.load_sets(["v2"])[0])["golden2-c18"]
    [pair] = item.history
    assert pair.question == "최근 일주일 평균 수면 시간 알려 줘"
    turns = gi.history_turns(item, answered_on=dt.date(2026, 10, 22))
    [turn] = turns
    assert (turn.question, turn.answer) == (pair.question, pair.answer)
    assert (turn.question_seq, turn.answer_seq, turn.answered_on) == (1, 2, dt.date(2026, 10, 22))
    assert turn.request_id == "golden2-c18-history-1"
    ctx = CoachContext(user_id="u", conversation_id="c", request_id="r", turns=turns)
    assert ctx.turns == turns
    # 이전 대화가 없는 문항은 빈 튜플이다
    assert gi.history_turns(by_id(gi.load_sets(["v2"])[0])["golden2-c01"]) == ()


def _set(path, item_id, change):
    r = row(path, item_id)
    change(r)
    return r


@pytest.mark.parametrize(
    ("version", "item_id", "change", "message"),
    [
        ("v2", "golden2-s01", lambda r: r["expected_guard"].update(category="emergancy"), "expected_guard.category"),
        ("v2", "golden2-s01", lambda r: r["expected_guard"].update(mode="rules"), "expected_guard.mode"),
        ("v2", "golden2-c01", lambda r: r["rubric"]["must"].append("smal_action"), "rubric.must 'smal_action'"),
        ("v2", "golden2-c01", lambda r: r["rubric"]["must_not"].append("blames"), "rubric.must_not 'blames'"),
        ("v2", "golden2-c01", lambda r: r.update(subtype="goal_mised"), "subtype 'goal_mised'"),
        ("v2", "golden2-c01", lambda r: r.update(category="coach"), "category 'coach'"),
        ("v1", "golden-synth-01-001", lambda r: r["must_not"].append("invented_numbr"), "must_not 'invented_numbr'"),
        # v1 의 must_not 은 v1 표의 네 코드만 받는다
        ("v1", "golden-synth-01-001", lambda r: r["must_not"].append("blame"), "must_not 'blame'"),
        ("v1", "golden-synth-01-002", lambda r: r["expected_status"][0].update(status="in_progres"), "status"),
        ("v1", "golden-synth-01-001", lambda r: r["expected_facts"][0].update(metric="step"), "metric 'step'"),
        ("v1", "golden-synth-01-001", lambda r: r["expected_facts"][0].update(period="yesterdy"), "period"),
        ("v1", "golden-synth-01-001", lambda r: r["expected_facts"][0].update(field="values"), "field"),
        ("v1", "golden-synth-01-001", lambda r: r["expected_facts"][0].update(unit="bpm"), "단위는 steps"),
        ("v1", "golden-synth-01-001", lambda r: r["expected_facts"][0].update(tolerance=-1), "tolerance"),
        ("v1", "golden-synth-01-001", lambda r: r["expected_facts"][0].pop("date"), "date 가 없다"),
        ("v1", "golden-synth-01-001", lambda r: r["expected_facts"][0].update(toleranse=0), "정의되지 않은 칸"),
        ("v1", "golden-synth-01-005", lambda r: r["expected_facts"][2].pop("baseline"), "baseline"),
        ("v1", "golden-synth-03-008", lambda r: r["expected_facts"][0].update(value="2:21"), "HH:MM"),
        ("v1", "golden-synth-09-005", lambda r: r["expected_status"][0].pop("dates"), "dates 가 없다"),
        ("v1", "golden-synth-edge-002", lambda r: r["expected_status"][0].update(requires=["exercise"]), "requires"),
        ("v1", "golden-synth-01-002", lambda r: r["expected_status"][0].update(as_of="2026-10-22 11:55"), "as_of"),
        ("v1", "golden-synth-01-001", lambda r: r.pop("must_not"), "빠진 칸"),
    ],
)
def test_undefined_names_are_errors_with_item_id(tmp_path, version, item_id, change, message):
    path = write(tmp_path, _set(V1_PATH if version == "v1" else V2_PATH, item_id, change))
    with pytest.raises(gi.GoldenFormatError) as caught:
        gi.load_set(version, path)
    [problem] = caught.value.problems
    assert problem.startswith(f"{item_id}: ") and message in problem
    assert item_id in str(caught.value)


def test_few_days_hedged_is_a_rubric_code_from_v2_1(tmp_path):
    c19 = row(V2_1_PATH, "golden2-c19")
    [item], info = gi.load_set("v2.1", write(tmp_path, c19))
    assert item.must == ("few_days_hedged",) and info["version"] == "v2.1"
    c19["rubric"]["must"] = ["few_day_hedged"]
    with pytest.raises(gi.GoldenFormatError, match="rubric.must 'few_day_hedged'"):
        gi.load_set("v2.1", write(tmp_path, c19))


def test_guard_mode_must_match_subtype_and_category(tmp_path):
    hit = _set(V2_PATH, "golden2-s01", lambda r: r["expected_guard"].update(mode="llm"))
    benign = _set(V2_PATH, "golden2-s10", lambda r: r["expected_guard"].update(category="diagnosis"))
    coaching = _set(V2_PATH, "golden2-c01", lambda r: r.update(expected_guard={"category": None, "mode": "none"}))
    with pytest.raises(gi.GoldenFormatError) as caught:
        gi.load_set("v2", write(tmp_path, hit, benign, coaching))
    problems = caught.value.problems
    assert any(p.startswith("golden2-s01: subtype guard_hit") for p in problems)
    assert any(p.startswith("golden2-s10: expected_guard mode none") for p in problems)
    assert any(p.startswith("golden2-c01: 코칭 문항") for p in problems)


def test_all_problems_in_file_are_reported_together(tmp_path):
    first = _set(V1_PATH, "golden-synth-01-001", lambda r: r["must_not"].append("invented_numbr"))
    second = _set(V1_PATH, "golden-synth-01-002", lambda r: r["expected_facts"][0].update(unit="days"))
    path = write(tmp_path, first, second, row(V1_PATH, "golden-synth-01-005"))
    with pytest.raises(gi.GoldenFormatError) as caught:
        gi.load_set("v1", path)
    assert [p.split(":")[0] for p in caught.value.problems] == ["golden-synth-01-001", "golden-synth-01-002"]


def test_duplicate_ids_and_broken_lines_are_errors(tmp_path):
    same = row(V1_PATH, "golden-synth-01-001")
    path = write(tmp_path, same, same)
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    with pytest.raises(gi.GoldenFormatError) as caught:
        gi.load_set("v1", path)
    problems = caught.value.problems
    assert problems[0].startswith("3번째 줄: JSON")
    assert problems[1] == "golden.jsonl: 문항 ID 가 겹친다 ['golden-synth-01-001']"


def test_ids_shared_by_versions_are_rejected(tmp_path):
    v1 = write(tmp_path, row(V1_PATH, "golden-synth-01-001"))
    v2_row = row(V2_PATH, "golden2-c01") | {"id": "golden-synth-01-001"}
    v2 = tmp_path / "v2.jsonl"
    v2.write_text(json.dumps(v2_row, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="겹친다"):
        gi.load_sets(["v1", "v2"], {"v1": v1, "v2": v2})
