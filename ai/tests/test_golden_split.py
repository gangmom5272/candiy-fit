"""골든셋 개선용·확인용 나누기(eval/golden/split.py) 테스트."""

import math
from collections import Counter

import pytest

from eval.golden import split as gsplit
from eval.golden.items import LATEST, load_set
from eval.golden.scoring import question_kind

SPLIT = gsplit.load_split()


def test_split_file_is_reproducible_from_seed():
    # 나누기 규칙·옮긴 문항까지 split.py 로 다시 만든 결과와 같다
    assert SPLIT == gsplit.build()
    for version in LATEST:
        assert SPLIT["sets"][version]["sha256"] == load_set(version)[1]["sha256"]
    assert any("기준 코드" in rule and "하나뿐이면 개선용에 둔다" in rule for rule in SPLIT["rules"])


@pytest.mark.parametrize("version", LATEST)
def test_items_with_sole_rubric_codes_stay_in_dev(version):
    items, _ = load_set(version)
    sole = gsplit.sole_code_items(items)
    assert SPLIT["sets"][version]["sole_code_items"] == sole
    assert set(sole) <= set(SPLIT["sets"][version]["dev"])


def test_c19_moves_to_dev_and_c14_takes_its_place():
    items, _ = load_set("v2.1")
    # c05 는 in_progress_as_total 을 혼자 쓰지만 처음부터 개선용이라 옮기지 않는다
    assert gsplit.sole_code_items(items) == {
        "golden2-c05": ["in_progress_as_total"],
        "golden2-c19": ["few_days_hedged"],
    }
    base_dev, base_holdout = gsplit.make_split(items, SPLIT["seed"])
    assert "golden2-c19" in base_holdout and {"golden2-c05", "golden2-c14"} <= set(base_dev)
    entry = SPLIT["sets"]["v2.1"]
    # c19 가 빠져 확인용이 없는 묶음(synth-09, trend)을 문항이 적은 묶음부터 채우는 c14 를 확인용으로 옮긴다
    assert entry["moves"] == [
        {
            "to_dev": "golden2-c19",
            "codes": ["few_days_hedged"],
            "to_holdout": "golden2-c14",
            "filled": ["persona:synth-09", "kind:trend"],
        }
    ]
    assert len(entry["holdout"]) == len(base_holdout)
    assert set(entry["holdout"]) == set(base_holdout) - {"golden2-c19"} | {"golden2-c14"}
    assert SPLIT["sets"]["v1.1"]["moves"] == []


def test_item_moved_to_holdout_keeps_its_question_kind_in_dev():
    items, _ = load_set("v2.1")
    dev = set(SPLIT["sets"]["v2.1"]["dev"])
    for move in SPLIT["sets"]["v2.1"]["moves"]:
        moved = next(item for item in items if item.id == move["to_holdout"])
        assert any(question_kind(item) == question_kind(moved) for item in items if item.id in dev)


def test_keep_in_dev_stops_when_no_item_can_take_the_place():
    items, _ = load_set("v2.1")
    dev, holdout = gsplit.make_split(items, SPLIT["seed"])
    every = {item.id: ["x"] for item in items}
    with pytest.raises(ValueError, match="확인용으로 옮길 문항이 없다"):
        gsplit.keep_in_dev(items, dev, holdout, every, SPLIT["seed"])


@pytest.mark.parametrize(("version", "holdout"), [("v1.1", 19), ("v2.1", 10)])
def test_every_item_is_in_one_side_and_holdout_is_about_a_third(version, holdout):
    items, _ = load_set(version)
    entry = SPLIT["sets"][version]
    assert sorted(entry["dev"] + entry["holdout"]) == sorted(item.id for item in items)
    assert not set(entry["dev"]) & set(entry["holdout"])
    assert len(entry["holdout"]) == holdout == round(len(items) / 3)


@pytest.mark.parametrize("version", LATEST)
def test_groups_are_spread_over_both_sides(version):
    items, _ = load_set(version)
    holdout = set(SPLIT["sets"][version]["holdout"])
    size = Counter(g for item in items for g in gsplit.groups(item))
    held = Counter(g for item in items if item.id in holdout for g in gsplit.groups(item))
    for group, n in size.items():
        # 묶음마다 확인용은 3분의 1 올림 이하이고 개선용에도 하나 이상 있다
        assert held[group] <= math.ceil(n / 3) and held[group] < n, group
        # 문항이 2개 이상인 질문 종류·경계 범주는 확인용에도 있다
        if n >= 2 and not group.startswith("persona:"):
            assert held[group] >= 1, group
    # 페르소나는 확인용 문항 수가 허락하는 만큼 확인용에 들어간다 (v2.1 은 확인용 10문항, 페르소나 11명)
    personas = [g for g, n in size.items() if g.startswith("persona:") and n >= 2]
    assert sum(held[g] >= 1 for g in personas) == min(len(personas), len(holdout))


def test_v2_1_safety_items_are_on_both_sides():
    items, _ = load_set("v2.1")
    holdout = set(SPLIT["sets"]["v2.1"]["holdout"])
    safety = [item for item in items if item.category == "safety"]
    assert {item.id in holdout for item in safety} == {True, False}
    for subtype in ("guard_hit", "guard_miss", "benign"):
        assert {item.id in holdout for item in safety if item.subtype == subtype} == {True, False}, subtype


def test_membership_checks_items_file():
    _, info = load_set("v1.1")
    sides = gsplit.membership(SPLIT, "v1.1", info["sha256"])
    assert Counter(sides.values()) == {"dev": 39, "holdout": 19}
    assert gsplit.membership(SPLIT, "v1", "0" * 64) is None
    with pytest.raises(ValueError, match="v1.1"):
        gsplit.membership(SPLIT, "v1.1", "0" * 64)
