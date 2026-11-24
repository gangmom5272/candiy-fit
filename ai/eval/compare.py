"""저장한 측정 결과 두 개를 비교한다. 도구 선택(eval.tool_selection)과 골든셋(eval.golden)의 집계 json 을 읽는다.

ai/ 디렉터리에서 실행한다.
    uv run python -m eval.compare <이전 집계>.json <이후 집계>.json
    uv run python -m eval.compare <이전 집계>.json <이후 집계>.json --save <비교>.md

- 시험지(데이터셋 이름·sha256, 문항 선택, 도구 선택의 채점 종류와 기간 채점, 골든셋의 고정 데이터·정답 원형 sha256 과
  개선용·확인용 나누기)가 다르면 결과 맨 위에 "다른 시험지라 점수를 비교할 수 없다"를 적는다. 비교는 그대로 한다.
- 점수는 전체와 데이터셋 계열별로 나란히 적는다. 계열은 파일 이름에서 v1.1 의 .1 을 뺀 이름이라 v1 과 v1.1 이 같다.
- 같은 문항: 계열과 문항 ID 로 짝짓고 정의가 같은 문항만 모아 따로 비교한다. 두 결과에 문항 정의 sha256 이 있으면
  그것으로, 없으면 종류·질문·기대 답 표시로 확인한다(도구 선택). 골든셋은 문항 정의 sha256 이 있어야 확인할 수 있다.
- 골든셋 결과에는 확인용(holdout) 문항의 문항별 결과가 없어 같은 문항 비교에 들어가지 않는다. 확인용은 점수 표의
  개선용·확인용 행으로만 비교한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

WARNING = "다른 시험지라 점수를 비교할 수 없다"
KIND_LABELS = {"tool_selection": "도구 선택", "golden": "골든셋"}
# 도구 선택의 기간 인자 비교 방식. 이 칸을 기록하기 전 결과(period_scoring 없음)는 인자를 쓴 그대로 비교한 결과다
PERIOD_SCORING_LABELS = {"range": "날짜 범위", "exact": "인자 그대로"}
SPLIT_LABELS = {"dev": "개선용", "holdout": "확인용"}
_MINOR = re.compile(r"(_v\d+)(?:\.\d+)+$")


def family(name: str) -> str:
    """데이터셋 계열. 같은 문항 파일을 고친 버전(v1, v1.1)은 같은 계열이다."""
    return _MINOR.sub(r"\1", name)


def kind_of(report: dict[str, Any]) -> str:
    return "golden" if "scoring" in report["summary"] else "tool_selection"


def paper(report: dict[str, Any]) -> dict[str, str]:
    """시험지를 정하는 값. 항목 이름 → 표시 문자열이다. 결과에 없는 항목은 넣지 않는다."""
    datasets = report["datasets"]
    out = {f"데이터셋 {family(d['name'])}": f"{d['name']} · sha256 {d['sha256'][:12]}" for d in datasets}
    if datasets and all("selected" in d for d in datasets):
        out["문항 선택"] = ", ".join(f"{family(d['name'])} {d['selected']}/{d['rows']}" for d in datasets)
    if "categories" in report:
        limit = f" (앞 {report['limit']}문항)" if report.get("limit") else ""
        out["채점 종류"] = ", ".join(report["categories"]) + limit
        out["기간 채점"] = PERIOD_SCORING_LABELS[report.get("period_scoring", "exact")]
    if data := report.get("data"):
        out["고정 데이터"] = f"sha256 {data['fixtures_sha256'][:12]}"
        out["정답 원형"] = f"sha256 {data['truth_sha256'][:12]}"
    if split := report.get("split"):
        out["나누기"] = f"--split {split['name']} · seed {split['seed']} · sha256 {split['sha256']}"
    return out


def paper_differences(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """시험지 항목 중 두 결과에서 다른 항목 이름. 한쪽에만 있는 항목도 다른 항목으로 센다."""
    a, b = paper(before), paper(after)
    names = [*a, *(name for name in b if name not in a)]
    return [name for name in names if a.get(name) != b.get(name)]


def warning_line(
    before: dict[str, Any], after: dict[str, Any], changes: Sequence[str], labels: tuple[str, str] = ("이전", "이후")
) -> str:
    """결과 맨 위에 넣을 경고 한 줄. 다른 항목마다 두 결과의 값을 적는다."""
    a, b = paper(before), paper(after)
    detail = "; ".join(
        f"{name}: {labels[0]} {a.get(name, '없음')}, {labels[1]} {b.get(name, '없음')}" for name in changes
    )
    return f"{WARNING}. {detail}"


def scores(report: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """묶음 → (맞음, 채점). 도구 선택은 정답 수(proposed 제외), 골든셋은 자동 채점 통과 수다."""
    s = report["summary"]
    if kind_of(report) == "tool_selection":
        out = {"전체": (s["overall"]["correct"], s["overall"]["scored"])}
        return out | {family(name): (g["correct"], g["scored"]) for name, g in s["by_dataset"].items()}
    sc = s["scoring"]
    names = {d["version"]: d["name"] for d in report["datasets"]}
    out = {"전체": (sc["overall"]["pass"], sc["overall"]["scored"])}
    out |= {family(names.get(v, v)): (g["pass"], g["scored"]) for v, g in sc["by_set"].items()}
    return out | {SPLIT_LABELS[side]: (g["pass"], g["scored"]) for side, g in sc.get("by_split", {}).items()}


def items(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """짝짓는 키(계열/문항 ID) → 문항 결과. definition 은 문항 정의 sha256, fallback 은 그것이 없을 때 쓰는 정의다."""
    golden = kind_of(report) == "golden"
    names = {d["version"]: d["name"] for d in report["datasets"]} if golden else {}
    out: dict[str, dict[str, Any]] = {}
    for st in report["summary"]["items"]:
        if golden:
            name = names.get(st["version"], st["version"])
            right, scored, fallback = st["pass"], st["pass"] + st["fail"] + st["check"], None
        else:
            name = st["key"].split("/", 1)[0]
            right, scored = st["correct"], st["scored"]
            fallback = [st["category"], st["question"], st["expected"]]
        out[f"{family(name)}/{st['id']}"] = {
            "key": f"{name}/{st['id']}",
            "family": family(name),
            "question": st["question"],
            "right": right,
            "scored": scored,
            "definition": st.get("definition_sha256"),
            "fallback": fallback,
        }
    return out


def _same_definition(a: dict[str, Any], b: dict[str, Any]) -> bool | None:
    """두 문항의 정의가 같은지. 확인할 수 없으면 None 이다."""
    if a["definition"] and b["definition"]:
        return a["definition"] == b["definition"]
    if a["fallback"] is None or b["fallback"] is None:
        return None
    return a["fallback"] == b["fallback"]


def compare(
    before: dict[str, Any], after: dict[str, Any], labels: tuple[str, str] = ("이전", "이후")
) -> dict[str, Any]:
    """두 측정 결과를 비교한다. 종류(도구 선택, 골든셋)가 다르면 SystemExit 를 발생시킨다."""
    kind = kind_of(before)
    if kind_of(after) != kind:
        raise SystemExit(f"두 결과의 종류가 다르다 ({KIND_LABELS[kind]}, {KIND_LABELS[kind_of(after)]})")
    changes = paper_differences(before, after)
    a_items, b_items = items(before), items(after)
    same, changed, undetermined, unscored = [], [], [], 0
    for key, a in a_items.items():
        b = b_items.get(key)
        if b is None:
            continue
        verdict = _same_definition(a, b)
        if verdict is None:
            undetermined.append(b["key"])
        elif not verdict:
            changed.append({"key": b["key"], "before_key": a["key"]})
        elif a["scored"] and b["scored"]:
            same.append(
                {
                    "key": b["key"],
                    "family": b["family"],
                    "question": b["question"],
                    "before": [a["right"], a["scored"]],
                    "after": [b["right"], b["scored"]],
                }
            )
        else:
            unscored += 1
    return {
        "kind": kind,
        "labels": list(labels),
        "reports": [_conditions(before), _conditions(after)],
        "paper": [paper(before), paper(after)],
        "paper_changes": changes,
        "warning": warning_line(before, after, changes, labels) if changes else None,
        "scores": [scores(before), scores(after)],
        "same_items": same,
        "changed_definition": changed,
        "undetermined": undetermined,
        "unscored": unscored,
        "only_before": [a["key"] for key, a in a_items.items() if key not in b_items],
        "only_after": [b["key"] for key, b in b_items.items() if key not in a_items],
    }


def _conditions(report: dict[str, Any]) -> dict[str, Any]:
    keys = ("measured_at", "commit", "prompt_version", "prompt_sha256", "tools_sha256", "repeat")
    return {k: report.get(k) for k in keys}


def _rate(pair: Sequence[int] | None) -> str:
    if not pair or not pair[1]:
        return "-"
    right, scored = pair
    return f"{right}/{scored} ({right / scored * 100:.1f}%)"


def _md(text: Any) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _condition_line(label: str, c: dict[str, Any]) -> str:
    return (
        f"- {label}: 측정 {c['measured_at']} · 커밋 `{c['commit']}` · 프롬프트 `{c['prompt_version']}` "
        f"(sha256 {c['prompt_sha256']}) · 도구 스키마 sha256 {c['tools_sha256']} · 반복 {c['repeat']}회"
    )


def _same_lines(result: dict[str, Any]) -> list[str]:
    before, after = result["labels"]
    same = result["same_items"]
    lines = [
        "## 같은 문항만",
        "",
        f"두 결과에 모두 있고 정의가 같은 문항 {len(same)}개만 모았다. 정의가 달라 뺀 문항 "
        f"{len(result['changed_definition'])}개, 정의를 확인할 수 없는 문항 {len(result['undetermined'])}개, "
        f"한쪽에만 있는 문항 {len(result['only_before']) + len(result['only_after'])}개, "
        f"채점하지 않은 문항 {result['unscored']}개는 넣지 않았다.",
        "",
        f"| 묶음 | 문항 | {before} | {after} |",
        "| --- | --- | --- | --- |",
    ]
    groups: dict[str, list[dict[str, Any]]] = {"전체": same}
    for row in same:
        groups.setdefault(row["family"], []).append(row)
    for name, rows in groups.items():
        totals = [[sum(r[side][i] for r in rows) for i in (0, 1)] for side in ("before", "after")]
        lines.append(f"| {name} | {len(rows)} | {_rate(totals[0])} | {_rate(totals[1])} |")
    moved = [row for row in same if row["before"][0] * row["after"][1] != row["after"][0] * row["before"][1]]
    lines += ["", "### 결과가 바뀐 문항", ""]
    if moved:
        lines += [f"| 문항 | 질문 | {before} | {after} |", "| --- | --- | --- | --- |"]
        lines += [
            f"| {row['key']} | {_md(row['question'])} | {row['before'][0]}/{row['before'][1]} | "
            f"{row['after'][0]}/{row['after'][1]} |"
            for row in moved
        ]
    else:
        lines.append("없음")
    lines += ["", "### 정의가 달라 뺀 문항", ""]
    lines += [f"- `{row['key']}`" for row in result["changed_definition"]] or ["없음"]
    if result["undetermined"]:
        lines += ["", "### 정의를 확인할 수 없는 문항", "", ", ".join(f"`{key}`" for key in result["undetermined"])]
    lines += ["", "### 한쪽에만 있는 문항", ""]
    lines.append(f"- {before}에만: " + (", ".join(f"`{key}`" for key in result["only_before"]) or "없음"))
    lines.append(f"- {after}에만: " + (", ".join(f"`{key}`" for key in result["only_after"]) or "없음"))
    return lines


def to_markdown(result: dict[str, Any]) -> str:
    before, after = result["labels"]
    lines = ["# 측정 결과 비교", ""]
    if result["warning"]:
        lines += [result["warning"], ""]
    lines += [
        f"- 종류: {KIND_LABELS[result['kind']]}",
        *(_condition_line(label, c) for label, c in zip(result["labels"], result["reports"], strict=True)),
        "",
        "## 시험지",
        "",
        f"| 항목 | {before} | {after} | 같음 |",
        "| --- | --- | --- | --- |",
    ]
    a, b = result["paper"]
    for name in [*a, *(n for n in b if n not in a)]:
        same = "다름" if name in result["paper_changes"] else "같음"
        lines.append(f"| {name} | {_md(a.get(name, '없음'))} | {_md(b.get(name, '없음'))} | {same} |")
    lines += ["", "## 점수", "", f"| 묶음 | {before} | {after} |", "| --- | --- | --- |"]
    sa, sb = result["scores"]
    for name in [*sa, *(n for n in sb if n not in sa)]:
        lines.append(f"| {name} | {_rate(sa.get(name))} | {_rate(sb.get(name))} |")
    lines += ["", *_same_lines(result)]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="저장한 측정 결과(집계 json) 두 개를 비교한다")
    p.add_argument("before", type=Path, help="이전 측정의 집계 json")
    p.add_argument("after", type=Path, help="이후 측정의 집계 json")
    p.add_argument("--save", type=Path, help="비교 md 를 이 파일로 저장한다")
    args = p.parse_args(argv)
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in (args.before, args.after)]
    markdown = to_markdown(compare(*reports, (args.before.name, args.after.name)))
    print(markdown)
    if args.save is not None:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(markdown, encoding="utf-8")
        print(f"저장: {args.save}")


if __name__ == "__main__":
    main()
