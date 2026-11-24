"""'지난 X요일' 문항을 기준일 월~일 7개로 잰 도구 선택 결과를 기준일 요일 × 묻는 요일로 나눠 센다.

ai/ 디렉터리에서 실행한다. LLM 을 호출하지 않는다. 측정은 eval.tool_selection 의 --today 로 따로 한다.
실험 계획은 docs/evals/2026-11-04-weekday-base-date-plan.md 에 있다.
    uv run python -m eval.weekday_base_dates subset                    # 요일 문항만 담은 임시 문항 파일을 만든다
    uv run python -m eval.weekday_base_dates report <측정 json 7개>      # 기준일 요일 × 묻는 요일 표를 출력한다
    uv run python -m eval.weekday_base_dates report <측정 json 7개> --save   # md·json 도 저장한다 (git 제외)

- subset 은 tool_selection_v1.2 와 tool_selection_ai_period_v2.2 에서 채점하는 "지난 X요일" 문항 줄을 그대로 옮긴다.
  문항을 고르는 규칙은 tool_selection 이 기준일마다 정답을 다시 계산하는 문항과 같다 (last_weekday_dates).
- report 는 측정 결과(<시각>_tool_selection.json)와 같은 이름의 문항별 기록(<시각>_tool_selection_records.jsonl)을
  읽는다. 틀린 실행은 기간 인자로 나눈다 (FORM_RULES).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, get_args

from coach_ai.tools.periods import WEEKDAYS
from eval.tool_selection import DEFAULT_TODAY, PERIOD_ARGS, Item, last_weekday_dates, load_datasets

EVAL_DIR = Path(__file__).resolve().parent
DATASETS_DIR = EVAL_DIR / "datasets"
SOURCES = (DATASETS_DIR / "tool_selection_v1.2.jsonl", DATASETS_DIR / "tool_selection_ai_period_v2.2.jsonl")
OUT_DIR = EVAL_DIR / "results" / "weekday"
SUBSET = OUT_DIR / "weekday_items.jsonl"
# 기준일 월~일. 데이터셋 기준일(2026-10-22)이 든 주를 피해 7개 모두 같은 규칙으로 판정하고, 달이 바뀌지 않는다
BASE_DATES = tuple(date(2026, 10, 12 + i) for i in range(7))
REPEAT = 10
_DAY_NAMES = "월화수목금토일"

Form = Literal["correct", "this_week", "this_week_day", "direct_date", "other", "error"]
FORMS: tuple[Form, ...] = get_args(Form)
FORM_LABELS: dict[Form, str] = {
    "correct": "맞음",
    "this_week": "이번 주 전체",
    "this_week_day": "이번 주 그 요일",
    "direct_date": "날짜 직접",
    "other": "그 밖",
    "error": "오류",
}
FORM_RULES = (
    "맞음·오류는 측정 기록의 판정을 따른다 (correct, llm_error).",
    "틀린 실행 중 기간 인자(period, current, baseline)에 weeks_ago:0 이나 this_week 가 있으면 이번 주 전체다.",
    "그렇지 않고 묻는 요일의 weeks_ago:0.요일(지난 토요일이면 weeks_ago:0.sat)이 있으면 이번 주 그 요일이다. "
    "그 요일이 이번 주에 지났으면 정답 날짜라 측정 판정에서 이미 맞음이다.",
    "그렇지 않고 date:·dates: 가 있으면 날짜 직접이다. 맞는 날짜를 직접 쓴 실행은 측정 판정에서 이미 맞음이다.",
    "나머지 틀린 실행(다른 기간, 다른 도구, 도구 없음, 해석 실패)은 그 밖이다.",
)
# 묻는 요일이 기준일이 든 주(월~일)에서 어디에 있는지. 지났으면 이번 주 전체에 그날이 들어 있다
Position = Literal["passed", "today", "ahead"]
POSITION_LABELS: dict[Position, str] = {
    "passed": "이번 주에 지남",
    "today": "오늘과 같은 요일",
    "ahead": "이번 주에 아직 안 옴",
}
_THIS_WEEK = {"weeks_ago:0", "this_week"}


def weekday_of(item: Item) -> str | None:
    """채점하는 "지난 X요일" 문항이면 그 요일(WEEKDAYS 의 값), 아니면 None."""
    return next(iter(last_weekday_dates([item], DEFAULT_TODAY)), None)


def subset_lines(sources: Sequence[Path] = SOURCES) -> list[tuple[str, str]]:
    """원본 데이터셋에서 "지난 X요일" 문항 줄을 파일 순서대로 (문항 키, 줄 원문)으로 반환한다."""
    out: list[tuple[str, str]] = []
    for path in sources:
        items, _ = load_datasets([path])
        lines = [line for line in path.read_bytes().decode("utf-8-sig").splitlines() if line.strip()]
        out += [(item.key, line) for item, line in zip(items, lines, strict=True) if weekday_of(item)]
    return out


def write_subset(path: Path = SUBSET, sources: Sequence[Path] = SOURCES) -> list[str]:
    """임시 문항 파일을 쓰고 옮긴 문항 키를 반환한다."""
    chosen = subset_lines(sources)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for _, line in chosen), encoding="utf-8")
    return [key for key, _ in chosen]


def _periods(record: dict[str, Any]) -> list[str]:
    return [
        value
        for call in record.get("calls") or []
        if isinstance(call.get("args"), dict)
        for key in PERIOD_ARGS
        if isinstance(value := call["args"].get(key), str)
    ]


def form_of(record: dict[str, Any]) -> Form:
    """측정 기록 한 줄을 FORM_RULES 대로 나눈다."""
    if record.get("error") == "llm_error" or record.get("correct") is None:
        return "error"
    if record["correct"]:
        return "correct"
    periods = _periods(record)
    if any(p in _THIS_WEEK for p in periods):
        return "this_week"
    if (asked := weekday_of(_record_item(record))) and f"weeks_ago:0.{asked}" in periods:
        return "this_week_day"
    if any(p.startswith(("date:", "dates:")) for p in periods):
        return "direct_date"
    return "other"


def position(asked: str, base: date) -> Position:
    """묻는 요일(WEEKDAYS 의 값)이 기준일이 든 주에서 기준일보다 앞이면 passed, 같으면 today, 뒤면 ahead."""
    index = WEEKDAYS.index(asked)
    if index < base.weekday():
        return "passed"
    return "today" if index == base.weekday() else "ahead"


@dataclass
class Cell:
    """기준일 하나 × 묻는 요일 하나의 실행 수."""

    base: date
    asked: str
    counts: Counter[str] = field(default_factory=Counter)
    # 틀린 실행의 기간 값 (없으면 "(기간 없음)")
    wrong_periods: Counter[str] = field(default_factory=Counter)
    items: set[str] = field(default_factory=set)

    @property
    def runs(self) -> int:
        return sum(self.counts.values())


def _records_path(report_path: Path) -> Path:
    return report_path.with_name(f"{report_path.stem}_records.jsonl")


def load_measurement(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """측정 결과 json 이나 문항별 기록 jsonl 경로를 받아 (측정 결과, 문항별 기록)을 반환한다."""
    if path.name.endswith("_records.jsonl"):
        report_path, records_path = path.with_name(path.name.removesuffix("_records.jsonl") + ".json"), path
    else:
        report_path, records_path = path, _records_path(path)
    for p in (report_path, records_path):
        if not p.exists():
            raise SystemExit(f"같은 측정의 파일이 없습니다: {p}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    lines = records_path.read_text(encoding="utf-8").splitlines()
    return report, [json.loads(line) for line in lines if line.strip()]


def _record_item(record: dict[str, Any]) -> Item:
    persona = record.get("persona", "")
    return Item(record["dataset"], record["id"], persona, record["category"], record["question"], {}, [])


def aggregate(measurements: Sequence[tuple[dict[str, Any], Sequence[dict[str, Any]]]]) -> dict[tuple[date, str], Cell]:
    """측정마다 "지난 X요일" 문항 기록을 (기준일, 묻는 요일) 칸으로 모은다. 다른 문항 기록은 세지 않는다."""
    cells: dict[tuple[date, str], Cell] = {}
    for report, records in measurements:
        base = date.fromisoformat(report["today"])
        for record in records:
            asked = weekday_of(_record_item(record))
            if asked is None:
                continue
            cell = cells.setdefault((base, asked), Cell(base, asked))
            form = form_of(record)
            cell.counts[form] += 1
            cell.items.add(record["id"])
            if form not in ("correct", "error"):
                cell.wrong_periods[", ".join(_periods(record)) or "(기간 없음)"] += 1
    return dict(sorted(cells.items(), key=lambda kv: (kv[0][0], WEEKDAYS.index(kv[0][1]))))


# 기준일이 달라도 같아야 비교할 수 있는 측정 조건
_SAME_FIELDS = ("prompt_version", "prompt_sha256", "tools_sha256", "repeat", "period_scoring", "temperature")


def condition_differences(reports: Sequence[dict[str, Any]]) -> list[str]:
    """측정끼리 다른 조건 이름. 데이터셋은 이름과 sha256 을 함께 비교한다."""
    names = [name for name in _SAME_FIELDS if len({json.dumps(r.get(name)) for r in reports}) > 1]
    sets = {json.dumps([(d["name"], d["sha256"]) for d in r.get("datasets", [])]) for r in reports}
    return names + (["datasets"] if len(sets) > 1 else [])


def _day(d: date) -> str:
    return f"{d.isoformat()}({_DAY_NAMES[d.weekday()]})"


def _name(weekday: str) -> str:
    return _DAY_NAMES[WEEKDAYS.index(weekday)]


def _ratio(n: int, d: int) -> str:
    return "-" if d == 0 else f"{n}/{d} ({n / d:.0%})"


def by_position(cells: dict[tuple[date, str], Cell]) -> dict[tuple[str, Position], Counter[str]]:
    """묻는 요일 × 그 요일 위치로 실행 수를 합친다. 두 가설이 갈리는 표다."""
    out: dict[tuple[str, Position], Counter[str]] = {}
    for cell in cells.values():
        out.setdefault((cell.asked, position(cell.asked, cell.base)), Counter()).update(cell.counts)
    return dict(sorted(out.items(), key=lambda kv: (WEEKDAYS.index(kv[0][0]), get_args(Position).index(kv[0][1]))))


def to_json(measurements: Sequence[tuple[dict[str, Any], Sequence[dict[str, Any]]]]) -> dict[str, Any]:
    cells = aggregate(measurements)
    return {
        "measurements": [
            {k: r.get(k) for k in ("measured_at", "commit", "today", *_SAME_FIELDS)} for r, _ in measurements
        ],
        "differences": condition_differences([r for r, _ in measurements]),
        "cells": [
            {
                "base": cell.base.isoformat(),
                "base_weekday": WEEKDAYS[cell.base.weekday()],
                "asked": cell.asked,
                "position": position(cell.asked, cell.base),
                "items": sorted(cell.items),
                "runs": cell.runs,
                **{form: cell.counts[form] for form in FORMS},
                "wrong_periods": dict(cell.wrong_periods.most_common()),
            }
            for cell in cells.values()
        ],
        "by_position": [
            {"asked": asked, "position": pos, "runs": sum(c.values()), **{form: c[form] for form in FORMS}}
            for (asked, pos), c in by_position(cells).items()
        ],
    }


def to_markdown(measurements: Sequence[tuple[dict[str, Any], Sequence[dict[str, Any]]]]) -> str:
    cells = aggregate(measurements)
    reports = [r for r, _ in measurements]
    bases = sorted({cell.base for cell in cells.values()})
    asked_days = [w for w in WEEKDAYS if any(cell.asked == w for cell in cells.values())]
    form_head = " | ".join(FORM_LABELS[form] for form in FORMS)
    form_align = " | ".join("---:" for _ in FORMS)
    first = reports[0] if reports else {}
    lines = [
        "# 지난 X요일 기준일별 집계",
        "",
        f"- 측정 {len(reports)}개 · 기준일 {', '.join(_day(b) for b in bases) or '없음'}",
        f"- 프롬프트 `{first.get('prompt_version')}` (템플릿 sha256 {first.get('prompt_sha256')}) · 도구 스키마 sha256 "
        f"{first.get('tools_sha256')} · 반복 {first.get('repeat')}회",
    ]
    if differences := condition_differences(reports):
        lines.append(f"- 측정끼리 다른 조건이 있다: {', '.join(differences)}. 칸끼리 비교하기 전에 확인한다")
    lines += ["", "## 나누는 기준", "", *[f"- {rule}" for rule in FORM_RULES]]
    lines += [
        "",
        "## 이번 주 전체로 조회한 실행 (기준일 × 묻는 요일)",
        "",
        "| 기준일 | " + " | ".join(f"지난 {_name(w)}요일" for w in asked_days) + " |",
        "| --- | " + " | ".join("---:" for _ in asked_days) + " |",
    ]
    for base in bases:
        row = []
        for w in asked_days:
            cell = cells.get((base, w))
            row.append("-" if cell is None else _ratio(cell.counts["this_week"], cell.runs))
        lines.append(f"| {_day(base)} | " + " | ".join(row) + " |")
    lines += [
        "",
        "## 묻는 요일 × 그 요일 위치",
        "",
        "그 요일 위치는 묻는 요일이 기준일이 든 주(월~일)에서 기준일보다 앞(이번 주에 지남)인지, 같은지, "
        "뒤(아직 안 옴)인지다.",
        "",
        f"| 묻는 요일 | 그 요일 위치 | 실행 | {form_head} |",
        f"| --- | --- | ---: | {form_align} |",
    ]
    for (asked, pos), c in by_position(cells).items():
        total = sum(c.values())
        counts = " | ".join(str(c[form]) for form in FORMS)
        lines.append(f"| 지난 {_name(asked)}요일 | {POSITION_LABELS[pos]} | {total} | {counts} |")
    lines += [
        "",
        "## 칸별",
        "",
        f"| 기준일 | 묻는 요일 | 그 요일 위치 | 문항 | 실행 | {form_head} | 틀린 기간 |",
        f"| --- | --- | --- | --- | ---: | {form_align} | --- |",
    ]
    for cell in cells.values():
        counts = " | ".join(str(cell.counts[form]) for form in FORMS)
        wrong = ", ".join(f"`{p}` {n}" for p, n in cell.wrong_periods.most_common()) or "-"
        head = f"{_day(cell.base)} | {_name(cell.asked)} | {POSITION_LABELS[position(cell.asked, cell.base)]}"
        items = ", ".join(sorted(cell.items))
        lines.append(f"| {head} | {items} | {cell.runs} | {counts} | {wrong} |")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="'지난 X요일' 문항의 기준일별 도구 선택 결과를 센다 (LLM 호출 없음)")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("subset", help="요일 문항만 담은 임시 문항 파일을 만든다")
    s.add_argument("--out", type=Path, default=SUBSET, help="임시 문항 파일 (기본 results/weekday/weekday_items.jsonl)")
    r = sub.add_parser("report", help="측정 결과를 기준일 요일 × 묻는 요일로 센다")
    r.add_argument("paths", nargs="+", type=Path, help="측정 결과 <시각>_tool_selection.json 또는 문항별 기록 jsonl")
    r.add_argument("--save", action="store_true", help="--out 폴더에 md·json 을 저장 (git 제외)")
    r.add_argument("--out", type=Path, default=OUT_DIR, help="저장 폴더 (기본 eval/results/weekday/)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    if args.command == "subset":
        keys = write_subset(args.out)
        print(f"저장: {args.out} ({len(keys)}문항)\n" + "\n".join(f"- {key}" for key in keys))
        return
    measurements = [load_measurement(path) for path in args.paths]
    text = to_markdown(measurements)
    print(text, end="")
    if args.save:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "weekday_base_dates.md").write_text(text, encoding="utf-8")
        body = json.dumps(to_json(measurements), ensure_ascii=False, indent=2)
        (args.out / "weekday_base_dates.json").write_text(body, encoding="utf-8")
        print(f"저장: {args.out / 'weekday_base_dates.md'}", file=sys.stderr)


if __name__ == "__main__":
    main()
