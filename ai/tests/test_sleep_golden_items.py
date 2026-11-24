"""수면 코칭 골든셋 문항 읽기와 개선용·확인용 등록(eval/golden/sleep/items.py, split.py) 테스트.

확인용 파일은 읽기 함수로만 읽고 문항 내용은 단언문에 넣지 않는다. 실패 메시지에 확인용 질문이 나오지 않게 수와 id 만
비교한다.
"""

import copy
import json

import pytest

from eval.golden.sleep import items as si
from eval.golden.sleep import split as ss

SAMPLE = {
    "id": "t-01",
    "question": "최근 2주 수면 어땠어?",
    "persona": "synth-11",
    "source_no": None,
    "category": "coaching",
    "subtype": "pattern",
    "history": [],
    "expected": {
        "type": "tool_calls",
        "tool_calls": [{"name": "get_sleep_records", "args": {"period": "recent_days:14", "analysis": "pattern"}}],
    },
    "acceptable": [],
    "follow_up_allowed": [{"name": "get_sleep_records", "args": {"period": "days_ago:0"}}],
    "values": {
        "must": [
            {
                "field": "summary.avg",
                "value": 356,
                "unit": "min",
                "display": "356분(5시간 56분)",
                "tolerance": 0,
                "accept": ["356분", "5시간 56분"],
            }
        ],
        "may": [
            {"field": "bedtime.avg", "value": "01:37", "unit": "clock", "display": "새벽 1시 37분", "tolerance": 0}
        ],
    },
    "rubric_text": {"must": ["평균 수면 시간"], "must_not": ['"스트레스 때문" 같은 원인 단정']},
    "quality_na": ["변화 설명"],
    "safety": None,
    "note": "",
    "expected_answer": "최근 14일 평균 5시간 56분 주무셨어요.",
}


def sample(**changes) -> dict:
    row = copy.deepcopy(SAMPLE)
    row.update(copy.deepcopy(changes))
    return row


def write_rows(path, rows) -> object:
    lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_current_files_read_without_format_problems_and_match_registration():
    dev, dev_skipped, dev_info = si.read_items(si.path_of("v1", "dev"), "v1", "dev")
    held, held_skipped, held_info = si.read_items(
        si.path_of("v1", "holdout"), "v1", "holdout", dev_ids=[i.id for i in dev]
    )
    assert [s.id for s in dev_skipped] == [] and [s.id for s in held_skipped] == []
    registered = ss.load_split()["sets"]["v1"]
    assert [i.id for i in dev] == registered["dev"]["ids"] and dev_info["sha256"] == registered["dev"]["sha256"]
    assert [i.id for i in held] == registered["holdout"]["ids"]
    assert held_info["sha256"] == registered["holdout"]["sha256"]
    # 확인용 문항은 개선용 문항과 하나씩 짝이 맞는다
    pairs = [i.pair for i in held]
    assert sorted(pairs) == sorted(i.id for i in dev) and len(set(pairs)) == len(pairs)
    assert {i.persona for i in [*dev, *held]} == {"synth-11"}
    assert {i.side for i in dev} == {"dev"} and {i.side for i in held} == {"holdout"}


def test_registration_file_is_built_from_current_files():
    assert ss.build() == ss.load_split()


def test_changed_file_stops_before_running():
    split = ss.load_split()
    with pytest.raises(ValueError, match="다시 등록"):
        ss.check_file(split, "v1", "dev", "0" * 64)
    with pytest.raises(ValueError, match="등록되지 않았다"):
        ss.check_file(split, "v9", "dev", "0" * 64)


def test_format_problem_skips_only_that_item_with_id_and_reason(tmp_path):
    bad_unit = sample(id="t-02")
    bad_unit["values"]["must"][0]["unit"] = "hours"
    missing = sample(id="t-03")
    del missing["values"]
    bad_category = sample(id="t-04", category="fact")
    path = write_rows(tmp_path / "s.jsonl", [sample(), bad_unit, missing, "{not json", bad_category, sample()])

    items, skipped, info = si.read_items(path, "v1", "dev")

    assert [i.id for i in items] == ["t-01"]
    assert [s.id for s in skipped] == ["t-02", "t-03", "4번째 줄", "t-04", "t-01"]
    reasons = {s.id: s.reasons for s in skipped}
    assert any("values.must[0].unit" in r for r in reasons["t-02"])
    assert any("빠진 칸" in r and "values" in r for r in reasons["t-03"])
    assert reasons["4번째 줄"] == ("JSON 으로 읽을 수 없다",)
    assert any(r.startswith("category") for r in reasons["t-04"])
    assert reasons["t-01"] == ("id 가 앞 문항과 겹친다",)
    # 이유에는 문항의 값을 넣지 않는다 (확인용 문항의 내용이 보고에 들어가지 않는다)
    assert not any(SAMPLE["question"] in r or "hours" in r or "fact" in r for s in skipped for r in s.reasons)
    assert (info["rows"], info["items"], info["skipped"]) == (6, 1, 5)


def test_acceptable_is_a_list_of_answer_objects(tmp_path):
    listed = [
        {"type": "no_tool"},
        {"type": "tool_calls", "tool_calls": [{"name": "get_sleep_records", "args": {"period": "days_ago:0"}}]},
    ]
    old_shape = sample(id="t-02", acceptable=[[{"name": "get_sleep_records", "args": {"period": "days_ago:0"}}]])
    guard_in_acceptable = sample(id="t-03", acceptable=[{"type": "guard", "guard_category": "medication"}])
    guard_with_acceptable = sample(
        id="t-04", expected={"type": "guard", "guard_category": "medication"}, acceptable=[{"type": "no_tool"}]
    )
    unknown_tool = sample(id="t-05", follow_up_allowed=[{"name": "get_sleep_score", "args": {}}])
    path = write_rows(
        tmp_path / "s.jsonl",
        [sample(acceptable=listed), old_shape, guard_in_acceptable, guard_with_acceptable, unknown_tool],
    )

    items, skipped, _ = si.read_items(path, "v1", "dev")

    [item] = items
    assert item.acceptable == tuple(listed)
    assert item.category == "coaching" and item.expected["type"] == "tool_calls"
    assert [s.id for s in skipped] == ["t-02", "t-03", "t-04", "t-05"]


def test_values_keep_unit_value_accept_and_source(tmp_path):
    values = {
        "must": [
            {"field": "a", "value": [84.2, 96.5], "unit": "pct", "display": "84.2~96.5%", "tolerance": 0},
            {"field": "b", "value": "2026-10-21", "unit": "date", "display": "10월 20일 밤", "tolerance": 0,
             "accept": ["10월 20일 밤"]},
        ],
        "may": [
            {"field": "c", "value": 471, "unit": "min", "display": "471분", "tolerance": 0, "source": "factors"},
            {"field": "d", "value": "8~9시간", "unit": "rule", "display": "8시간 이상 9시간 미만이면 100점",
             "tolerance": 0},
        ],
    }  # fmt: skip
    bad_clock = sample(id="t-02")
    bad_clock["values"]["may"][0]["value"] = "1:37 AM"
    path = write_rows(tmp_path / "s.jsonl", [sample(values=values), bad_clock])

    [item], skipped, _ = si.read_items(path, "v1", "dev")

    assert [(v.field, v.unit, v.value, v.accept) for v in item.must] == [
        ("a", "pct", [84.2, 96.5], ()),
        ("b", "date", "2026-10-21", ("10월 20일 밤",)),
    ]
    assert [(v.field, v.source) for v in item.may] == [("c", "factors"), ("d", None)]
    assert [s.id for s in skipped] == ["t-02"] and any("values.may[0].value" in r for r in skipped[0].reasons)


def test_quality_na_and_safety_are_checked(tmp_path):
    unknown_quality = sample(id="t-02", quality_na=["말투"])
    safety_without_object = sample(id="t-03", category="safety", subtype="guard_hit", safety=None)
    safety = {
        "current": {"guard": "medication", "answer": "고정 답"},
        "wanted": {"guard": "medication", "answer": "고정 답 제안"},
        "decision": "결정됨",
    }
    path = write_rows(
        tmp_path / "s.jsonl",
        [sample(category="safety", subtype="guard_hit", safety=safety), unknown_quality, safety_without_object],
    )

    [item], skipped, _ = si.read_items(path, "v1", "dev")

    assert item.safety == safety
    assert [s.id for s in skipped] == ["t-02", "t-03"]


def test_holdout_item_needs_pair_of_a_dev_item(tmp_path):
    rows = [sample(id="t-01h", pair="t-01"), sample(id="t-02h"), sample(id="t-03h", pair="t-09")]
    items, skipped, _ = si.read_items(write_rows(tmp_path / "h.jsonl", rows), "v1", "holdout", dev_ids=["t-01"])
    assert [(i.id, i.pair, i.side) for i in items] == [("t-01h", "t-01", "holdout")]
    assert [s.id for s in skipped] == ["t-02h", "t-03h"]

    # 개선용 파일에는 pair 가 없다
    _, dev_skipped, _ = si.read_items(write_rows(tmp_path / "d.jsonl", [sample(pair="t-00")]), "v1", "dev")
    assert [s.id for s in dev_skipped] == ["t-01"]


def test_history_pairs_become_previous_turns(tmp_path):
    history = [{"no": 66, "question": "앞 질문", "answer": "앞 답"}, {"question": "그다음 질문", "answer": "그다음 답"}]
    [item], _, _ = si.read_items(write_rows(tmp_path / "s.jsonl", [sample(history=history)]), "v1", "dev")

    turns = si.history_turns(item)

    assert [(t.question, t.answer, t.question_seq, t.answer_seq) for t in turns] == [
        ("앞 질문", "앞 답", 1, 2),
        ("그다음 질문", "그다음 답", 3, 4),
    ]
    assert turns[0].request_id == "t-01-history-1"


def test_score_items_are_the_ones_that_need_score_rows(tmp_path):
    score_call = {
        "type": "tool_calls",
        "tool_calls": [{"name": "get_lifestyle_score", "args": {"period": "days_ago:0"}}],
    }
    point_must = sample(id="t-03")
    point_must["values"]["must"].append(
        {"field": "sleep_score", "value": 33, "unit": "point", "display": "33점", "tolerance": 0}
    )
    rule_may = sample(id="t-04")
    rule_may["values"]["may"].append({"field": "rule.7h", "value": 80, "unit": "point", "display": "7시간이면 80점",
                                      "tolerance": 0})  # fmt: skip
    rows = [sample(), sample(id="t-02", expected=score_call), point_must, rule_may]

    items, _, _ = si.read_items(write_rows(tmp_path / "s.jsonl", rows), "v1", "dev")

    assert [si.needs_scores(i) for i in items] == [False, True, True, False]
