"""측정 결과 비교(eval/compare.py) 테스트. LLM 을 호출하지 않는다."""

import json

import pytest

from eval import compare as cmp
from eval.golden import runner as gr
from eval.golden.split import load_split

WARNING = "다른 시험지라 점수를 비교할 수 없다"
TOOL = "get_metric_summary(metric=steps, period=yesterday)"


def tool_item(dataset: str, item_id: str, correct: int, *, question="어제 몇 걸음 걸었어?", expected=TOOL, **extra):
    return {
        "key": f"{dataset}/{item_id}",
        "id": item_id,
        "persona": "synth-01",
        "category": extra.pop("category", "tool"),
        "question": question,
        "expected": expected,
        "runs": 5,
        "scored": 5,
        "correct": correct,
        **extra,
    }


def tool_report(name: str, sha: str, items: list[dict], *, categories=("tool", "no_tool", "clarify", "guard")):
    scored = sum(i["scored"] for i in items)
    correct = sum(i["correct"] for i in items)
    overall = {"items": len(items), "scored": scored, "correct": correct, "accuracy": correct / scored}
    return {
        "measured_at": "2026-10-30T15:00:00",
        "commit": "abc1234",
        "model": "m",
        "prompt_version": "v1.4",
        "prompt_sha256": "0" * 12,
        "tools_sha256": "1" * 12,
        "repeat": 5,
        "categories": list(categories),
        "limit": None,
        "datasets": [{"name": name, "path": f"eval/datasets/{name}.jsonl", "sha256": sha, "rows": 300, "selected": 3}],
        "summary": {"overall": overall, "by_dataset": {name: overall}, "items": items},
    }


def write(tmp_path, name: str, report: dict):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


def body(md: str) -> list[str]:
    """제목 다음 줄부터 빈 줄을 뺀 줄."""
    return [line for line in md.splitlines()[1:] if line.strip()]


def test_same_paper_has_no_warning(tmp_path):
    items = [tool_item("tool_selection_v1.1", "synth-01-001", 5), tool_item("tool_selection_v1.1", "synth-01-002", 3)]
    before = tool_report("tool_selection_v1.1", "a" * 64, items)
    after = tool_report("tool_selection_v1.1", "a" * 64, [{**items[0]}, {**items[1], "correct": 4}])
    md = cmp.to_markdown(cmp.compare(before, after, ("이전.json", "이후.json")))
    assert WARNING not in md
    assert "| 전체 | 2 | 8/10 (80.0%) | 9/10 (90.0%) |" in md
    assert "| tool_selection_v1.1/synth-01-002 | 어제 몇 걸음 걸었어? | 3/5 | 4/5 |" in md


def test_different_version_puts_warning_first_and_compares_same_items_only():
    before = tool_report(
        "tool_selection_v1",
        "a" * 64,
        [
            tool_item("tool_selection_v1", "synth-01-001", 5),
            tool_item("tool_selection_v1", "synth-02-008", 5, expected=f"{TOOL} (허용 답안 1)"),
            tool_item("tool_selection_v1", "synth-06-007", 0, category="proposed"),
        ],
    )
    after = tool_report(
        "tool_selection_v1.1",
        "b" * 64,
        [
            tool_item("tool_selection_v1.1", "synth-01-001", 4),
            tool_item("tool_selection_v1.1", "synth-02-008", 5, expected=f"{TOOL} (허용 답안 2)"),
            tool_item("tool_selection_v1.1", "synth-06-007", 5),
            tool_item("tool_selection_v1.1", "synth-07-031", 5, question="새 질문"),
        ],
    )
    result = cmp.compare(before, after, ("이전.json", "이후.json"))
    assert result["paper_changes"] == ["데이터셋 tool_selection_v1"]
    md = cmp.to_markdown(result)
    # 경고는 결과 맨 위(제목 바로 다음 줄)에 있다
    assert body(md)[0].startswith(WARNING)
    # 버전이 달라도 데이터셋 계열(tool_selection_v1)과 문항 ID 로 짝짓고, 정의가 같은 문항만 따로 비교한다
    assert "| 전체 | 1 | 5/5 (100.0%) | 4/5 (80.0%) |" in md
    excluded = md.split("### 정의가 달라 뺀 문항", 1)[1].split("\n#", 1)[0]
    assert "tool_selection_v1.1/synth-02-008" in excluded and "tool_selection_v1.1/synth-06-007" in excluded
    assert "tool_selection_v1.1/synth-07-031" in md.split("### 한쪽에만 있는 문항", 1)[1]


def test_definition_sha256_decides_same_item_when_both_have_it():
    # 질문과 기대 답 표시가 같아도 허용 답안 내용이 바뀌면 정의 sha256 이 달라 같은 문항이 아니다
    before = tool_report("t_v1", "a" * 64, [tool_item("t_v1", "x-1", 5, definition_sha256="1" * 12)])
    after = tool_report("t_v1", "a" * 64, [tool_item("t_v1", "x-1", 5, definition_sha256="2" * 12)])
    result = cmp.compare(before, after, ("a", "b"))
    assert result["same_items"] == [] and [row["key"] for row in result["changed_definition"]] == ["t_v1/x-1"]


def test_categories_differ_is_a_different_paper():
    items = [tool_item("t_v1", "x-1", 5)]
    result = cmp.compare(
        tool_report("t_v1", "a" * 64, items), tool_report("t_v1", "a" * 64, items, categories=["tool"])
    )
    assert result["paper_changes"] == ["채점 종류"]


def test_period_scoring_is_part_of_the_paper():
    items = [tool_item("tool_selection_v1.1", "synth-01-001", 5)]
    old = tool_report("tool_selection_v1.1", "a" * 64, items)
    ranged = {**tool_report("tool_selection_v1.1", "a" * 64, items), "period_scoring": "range"}
    # 기간 채점 칸이 없는 옛 결과는 기간 인자를 쓴 그대로 비교한 결과로 읽는다
    assert cmp.paper(old)["기간 채점"] == "인자 그대로"
    assert cmp.paper(ranged)["기간 채점"] == "날짜 범위"
    assert cmp.compare(old, ranged, ("이전.json", "이후.json"))["paper_changes"] == ["기간 채점"]
    assert cmp.compare(ranged, {**ranged}, ("이전.json", "이후.json"))["paper_changes"] == []


def golden_run(tmp_path, name: str, *argv: str) -> dict:
    out = tmp_path / name
    gr.main(["--set", "v1.1", *argv, "--mock", "--save", "--out", str(out)])
    return json.loads(next(out.glob("*_golden_v1.1_mock.json")).read_text(encoding="utf-8"))


def test_golden_reports_compare_by_definition(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "git_commit", lambda: "abc1234")
    dev = load_split()["sets"]["v1.1"]["dev"][:2]
    a = golden_run(tmp_path, "a", "--only", *dev)
    b = golden_run(tmp_path, "b", "--only", *dev)
    result = cmp.compare(a, b, ("a", "b"))
    assert result["kind"] == "golden" and result["paper_changes"] == []
    assert [row["key"] for row in result["same_items"]] == [f"coaching_golden_v1.1/{item_id}" for item_id in dev]
    changed = {**b, "data": {**b["data"], "fixtures_sha256": "0" * 64}}
    result = cmp.compare(a, changed, ("a", "b"))
    assert result["paper_changes"] == ["고정 데이터"]
    assert body(cmp.to_markdown(result))[0].startswith(WARNING)


def test_golden_split_is_part_of_the_paper(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "git_commit", lambda: "abc1234")
    dev = load_split()["sets"]["v1.1"]["dev"][:2]
    result = cmp.compare(
        golden_run(tmp_path, "dev", "--only", *dev), golden_run(tmp_path, "all", "--split", "all", "--only", *dev)
    )
    assert result["paper_changes"] == ["나누기"]
    assert "| 개선용 |" in cmp.to_markdown(result)


def test_kinds_must_match():
    golden = {"datasets": [], "data": {}, "summary": {"scoring": {}}}
    with pytest.raises(SystemExit, match="종류"):
        cmp.compare(tool_report("t_v1", "a" * 64, [tool_item("t_v1", "x-1", 5)]), golden)


def test_main_prints_and_saves_markdown(tmp_path, capsys):
    items = [tool_item("t_v1", "x-1", 5)]
    a = write(tmp_path, "a", tool_report("t_v1", "a" * 64, items))
    b = write(tmp_path, "b", tool_report("t_v1.1", "b" * 64, items))
    out = tmp_path / "compare.md"
    cmp.main([str(a), str(b), "--save", str(out)])
    md = out.read_text(encoding="utf-8")
    assert md.startswith("# 측정 결과 비교") and body(md)[0].startswith(WARNING)
    assert WARNING in capsys.readouterr().out
