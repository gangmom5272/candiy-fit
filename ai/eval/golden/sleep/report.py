"""수면 코칭 골든셋 결과 집계, 요약 md, 사람 평가표(CSV).

개선용 문항은 문항별로 적고, 확인용 문항은 개선용·확인용별 집계의 수와 판정에만 넣는다. 확인용 문항의 id·질문·답·
실패 이유는 넣지 않는다.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from bench.probe_llm import fmt, pct
from coach_ai.agent.prompts import today_text
from eval import tool_selection as ts
from eval.golden.sleep.items import QUALITY_ITEMS, SIDE_LABELS, SIDES, SleepItem
from eval.golden.sleep.scoring import FIXED_MARK, SCORING_RULES, quoted, score_view

VERDICT_LABELS = {"pass": "통과", "fail": "실패", "check": "사람 확인", "error": "오류", "skipped": "건너뜀"}
SKIP_LABELS = {"no_scores": "점수 행 없음"}
FINISH_LABELS = {
    "stop": "답변",
    "length": "길이 한도",
    "guard": "경계",
    "tool_limit": "도구 왕복 한도",
    "error": "오류",
}
ANALYSIS_LABELS = {"compared": "비교", "not_in_schema": "스키마에 없음"}
RUBRIC_COLUMNS = (
    "문항",
    "회차",
    "구분",
    "번호",
    "항목",
    "판정 방식",
    "자동 판정",
    "사람 판정",
    "메모",
    "질문",
    "답변",
    "기대 답변",
)


def _ratio(n: int, d: int) -> str:
    return f"{n}/{d}" if d else "-"


def _median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _scored(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r["executed"] and (r.get("score") or {}).get("verdict") not in ("error", "skipped")]


def _first_token_times(records: Sequence[dict[str, Any]]) -> list[float]:
    return [r["first_token_s"] for r in records if r.get("first_token_s") is not None]


def split_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """실행 묶음의 수와 판정. 채점 결과는 판정과 수만 담은 사본(score_view)만 읽어 확인용 기록으로도 같게 센다."""
    views = [score_view(r["score"]) for r in records if r.get("score")]
    scored = [v for v in views if v["verdict"] not in ("error", "skipped")]
    safety = [v["safety"] for v in scored if v.get("safety") and v["safety"].get("kind")]
    blocks = [s for s in safety if s["kind"] == "block"]
    passes = [s for s in safety if s["kind"] == "pass"]
    times = _first_token_times([r for r in records if r["executed"] and not r.get("error_code")])
    return {
        "items": len({r["id"] for r in records}),
        "runs": len(records),
        "executed": sum(r["executed"] for r in records),
        "skipped": sum(not r["executed"] for r in records),
        "errors": sum(v["verdict"] == "error" for v in views),
        "scored": len(scored),
        "verdicts": dict(Counter(v["verdict"] for v in scored)),
        "must_all": sum(v["must_all"] for v in scored),
        "first_call": {
            "correct": sum(v["first_call"]["verdict"] == "pass" for v in scored),
            "errors": dict(Counter(v["first_call"]["error"] for v in scored if v["first_call"]["error"])),
            "analysis": dict(Counter(v["first_call"]["analysis"] for v in scored if v["first_call"]["analysis"])),
        },
        "values": {
            "must_missing_runs": sum(v["values"]["must_missing"] > 0 for v in scored),
            "invented_fail_runs": sum(v["values"]["invented_fail"] > 0 for v in scored),
            "invented_check_runs": sum(v["values"]["invented_check"] > 0 for v in scored),
        },
        "rubric": {
            "forbidden_hits": sum(v["rubric"]["forbidden_hits"] for v in scored),
            "forbidden_runs": sum(v["rubric"]["forbidden_hits"] > 0 for v in scored),
            "fixed_runs": sum(v["rubric"]["fixed"] > 0 for v in scored),
            "fixed_same_runs": sum(0 < v["rubric"]["fixed"] == v["rubric"]["fixed_found"] for v in scored),
        },
        "follow_up": {
            "runs_with_extra": sum(v["follow_up"]["extra"] > 0 for v in scored),
            "extra_calls": sum(v["follow_up"]["extra"] for v in scored),
        },
        "safety": {
            "block_runs": len(blocks),
            "blocked": sum(s["blocked"] for s in blocks),
            "exact": sum(bool(s["exact"]) for s in blocks),
            "same_as_current": sum(s["same_as_current"] for s in safety),
            "pass_runs": len(passes),
            "passed": sum(s["passed"] for s in passes),
            "reverted": sum(s["reverted"] for s in passes),
        },
        "finish_reasons": dict(Counter(r["finish_reason"] for r in records if r["executed"])),
        "first_token_s": {"p50": pct(times, 50), "p95": pct(times, 95)},
    }


def item_summary(item: SleepItem, records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """개선용 문항 하나의 반복 결과. first_calls 는 첫 호출 모양별 실행 수다."""
    scored = _scored(records)
    scores = [r["score"] for r in scored]
    safety = [s["safety"] for s in scores if s.get("safety") and s["safety"].get("kind")]
    return {
        "id": item.id,
        "category": item.category,
        "subtype": item.subtype,
        "runs": len(records),
        "skipped": dict(Counter(r["skip_reason"] for r in records if not r["executed"])),
        "errors": sum(r["executed"] and r["score"]["verdict"] == "error" for r in records),
        "verdicts": dict(Counter(s["verdict"] for s in scores)),
        "first_call_correct": sum(s["first_call"]["verdict"] == "pass" for s in scores),
        "first_calls": dict(Counter(s["first_call"]["signature"] for s in scores)),
        "first_call_errors": dict(Counter(s["first_call"]["error"] for s in scores if s["first_call"]["error"])),
        "analysis": dict(Counter(s["first_call"]["analysis"] for s in scores if s["first_call"]["analysis"])),
        "must_all": sum(s["must_all"] for s in scores),
        "must_missing": dict(
            Counter(row["display"] for s in scores for row in s["values"]["must"] if row["found"] is False)
        ),
        "invented": dict(Counter(row["text"] for s in scores for row in s["values"]["invented"])),
        "forbidden_hits": sum(s["rubric"]["forbidden_hits"] for s in scores),
        "forbidden_runs": sum(s["rubric"]["forbidden_hits"] > 0 for s in scores),
        "forbidden": dict(Counter(row["phrase"] for s in scores for row in s["rubric"]["forbidden"] if row["found"])),
        "extra_runs": sum(bool(s["follow_up"]["extra"]) for s in scores),
        "extra_calls": dict(Counter(call for s in scores for call in s["follow_up"]["extra"])),
        "safety": {
            "runs": len(safety),
            "blocked": sum(bool(s.get("blocked")) for s in safety),
            "exact": sum(bool(s.get("exact")) for s in safety),
            "passed": sum(bool(s.get("passed")) for s in safety),
            "reverted": sum(bool(s.get("reverted")) for s in safety),
            "same_as_current": sum(s["same_as_current"] for s in safety),
        },
        "finish_reasons": dict(Counter(r["finish_reason"] for r in records if r["executed"])),
        "first_token_s": _median(_first_token_times(scored)),
    }


def summarize(records: Sequence[dict[str, Any]], items: Sequence[SleepItem]) -> dict[str, Any]:
    """측정 전체의 집계. 개선용 문항만 문항별 결과(items)와 실패 이유(failures)를 담는다."""
    llm = [c for r in records for c in r.get("llm_calls") or []]
    totals = [c["total_s"] for c in llm if c.get("total_s") is not None]
    by_key: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_key.setdefault(f"{r['version']}/{r['id']}", []).append(r)
    dev_items = [item for item in items if item.side == "dev"]
    return {
        "runs": len(records),
        "executed": sum(r["executed"] for r in records),
        "skipped": dict(Counter(r["skip_reason"] for r in records if not r["executed"])),
        "llm": {
            "calls": len(llm),
            "errors": sum(bool(c.get("error_code")) for c in llm),
            "input_tokens": sum(c.get("input_tokens") or 0 for c in llm),
            "output_tokens": sum(c.get("output_tokens") or 0 for c in llm),
            "total_p50": pct(totals, 50),
            "total_p95": pct(totals, 95),
        },
        "by_split": {
            side: split_summary([r for r in records if r["split"] == side])
            for side in SIDES
            if any(r["split"] == side for r in records)
        },
        "items": [item_summary(item, by_key.get(item.key, [])) for item in dev_items],
        "failures": [
            {"id": r["id"], "run": r["run"], "verdict": r["score"]["verdict"], "reasons": r["score"]["failures"]}
            for r in records
            if r["split"] == "dev" and r["executed"] and r["score"]["verdict"] in ("fail", "check")
        ],
    }


def _forbidden_mode(line: str) -> str:
    phrases = quoted(line)
    if not phrases:
        return "사람"
    rest = line
    for phrase in phrases:
        rest = rest.replace(f'"{phrase}"', "").replace(f"“{phrase}”", "")
    return "자동" if not rest.strip() else "자동+사람"


def rubric_rows(records: Sequence[dict[str, Any]], items: Mapping[str, SleepItem]) -> list[dict[str, str]]:
    """사람 평가표의 행. 개선용 실행마다 꼭·금지 줄과 퀄리티 다섯 항목을 한 행씩 둔다.

    판정 방식은 자동(실행기가 셈), 자동+사람(따옴표 표현은 실행기가 세고 나머지 설명은 사람이 매김), 사람, 해당 없음
    (quality_na)이다. 오류로 끝났거나 건너뛴 실행은 넣지 않는다.
    """
    rows = []
    for record in records:
        if record["split"] != "dev" or not record["executed"] or record["score"]["verdict"] == "error":
            continue
        item = items[f"{record['version']}/{record['id']}"]
        rubric = record["score"]["rubric"]
        fixed = {row["line"]: row["found"] for row in rubric["fixed"]}
        base = {
            "문항": item.id,
            "회차": str(record["run"]),
            "메모": "",
            "질문": item.question,
            "답변": record["answer"] or "",
            "기대 답변": item.expected_answer or "",
        }
        for index, line in enumerate(item.rubric_must):
            if index in fixed:
                auto = f"{FIXED_MARK} {'같음' if fixed[index] else '다름'}"
                rows.append(_sheet_row(base, "꼭", index + 1, line, "자동", auto))
            else:
                rows.append(_sheet_row(base, "꼭", index + 1, line, "사람"))
        for index, line in enumerate(item.rubric_must_not):
            mode = _forbidden_mode(line)
            found = [row["phrase"] for row in rubric["forbidden"] if row["line"] == index and row["found"]]
            auto = "" if mode == "사람" else (f"금지 낱말 있음: {', '.join(found)}" if found else "금지 낱말 없음")
            rows.append(_sheet_row(base, "금지", index + 1, line, mode, auto))
        for index, name in enumerate(QUALITY_ITEMS):
            if name in item.quality_na:
                rows.append(_sheet_row(base, "퀄리티", index + 1, name, "해당 없음", human="해당 없음"))
            else:
                rows.append(_sheet_row(base, "퀄리티", index + 1, name, "사람"))
    return [{column: row[column] for column in RUBRIC_COLUMNS} for row in rows]


def _sheet_row(
    base: dict[str, str], kind: str, number: int, text: str, mode: str, auto: str = "", human: str = ""
) -> dict[str, str]:
    return {
        **base,
        "구분": kind,
        "번호": str(number),
        "항목": text,
        "판정 방식": mode,
        "자동 판정": auto,
        "사람 판정": human,
    }


def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _counts(counter: Mapping[str, int], labels: Mapping[str, str] | None = None) -> str:
    if not counter:
        return "-"
    return ", ".join(f"{(labels or {}).get(k, k)} {n}" for k, n in sorted(counter.items(), key=lambda kv: -kv[1]))


def _temperature_text(report: dict[str, Any]) -> str:
    if report["temperature_source"] is None:
        return "(LLM 호출 없음)"
    if report["temperature_source"] == "server_default":
        return "서버 기본값 (요청에 넣지 않음)"
    return f"{report['temperature']} (요청에 넣음)"


def header_lines(report: dict[str, Any]) -> list[str]:
    s = report["summary"]
    llm = s["llm"]
    model = f"모델 `{report['model']}` · 호스트 `{report['base_url_host']}`"
    if report["mock"]:
        model = f"모의 응답 (--mock, 사내 LLM 호출 없음) · {model}"
    day = report.get("prompt_date")
    inserted = "없음" if day is None else today_text(date.fromisoformat(day))
    analysis = "스키마에 있음 (기대값과 비교)" if report["analysis_in_schema"] else "스키마에 없음 (빼고 비교, 따로 셈)"
    lines = [
        f"- 커밋 `{report['commit']}` · {model}",
        f"- 실행 명령 `{report['command']}`",
        f"- 프롬프트 `{report['prompt_version']}` (템플릿 sha256 {report['prompt_sha256']}, 넣은 날짜 {inserted}) · "
        f"도구 스키마 sha256 {report['tools_sha256']} · get_sleep_records 의 analysis 인자: {analysis}",
    ]
    for d in report["datasets"]:
        lines.append(
            f"- 데이터셋 `{d['path']}` ({d['version']} {SIDE_LABELS[d['side']]}) "
            f"실행 {d['selected']}/{d['items']}문항 · 형식 오류로 건너뜀 {d['skipped']} · sha256 {d['sha256'][:12]}"
        )
    if report.get("registration"):
        reg = report["registration"]
        lines.append(f"- 등록 `{reg['path']}` · sha256 {reg['sha256'][:12]} · `--split {report['split']}`")
    for persona, f in report["fixtures"].items():
        diffs = len(f["truth_diffs"])
        scores = f"생활 습관 점수 행 {f['scores']}개" + (" (점수 문항은 실행하지 않음)" if not f["scores"] else "")
        lines.append(
            f"- 고정 데이터 {persona} data_sha256 {f['data_sha256'][:12]} · 정답 원형과 같은 원형 "
            f"{f['truth_compared'] - diffs}/{f['truth_compared']} · {scores}"
        )
    lines += [
        f"- 기준 시각 {report['now']} · 반복 {report['repeat']}회 · temperature {_temperature_text(report)} · "
        f"채점 규칙 sha256 {report['scoring_sha256']}",
        f"- 실행 {s['runs']}회 (실행 {s['executed']}, 건너뜀 {s['runs'] - s['executed']}) · "
        f"LLM 호출 {llm['calls']}회 · 실패 {llm['errors']} · 입력 토큰 {llm['input_tokens']:,} · "
        f"출력 토큰 {llm['output_tokens']:,} · 호출 시간 p50 {fmt(llm['total_p50'])} p95 {fmt(llm['total_p95'])} · "
        f"전체 {report['elapsed_s']}초",
    ]
    if report.get("records_file"):
        lines.append(
            f"- 실행별 기록 `{report['records_file']}` (git 제외. 개선용은 답·호출·도구 결과를 담고, "
            "확인용은 판정과 수만 담는다)"
        )
    if report.get("rubric_file"):
        lines.append(f"- 사람 평가표 `{report['rubric_file']}` (개선용, {report['rubric_rows']}행)")
    return lines


def _skipped_lines(report: dict[str, Any]) -> list[str]:
    s = report["summary"]
    problems = report.get("format_problems") or []
    lines = ["## 건너뛴 문항", ""]
    if not problems and not s["skipped"]:
        return [*lines, "없음"]
    for problem in problems:
        lines.append(f"- 형식 오류 {SIDE_LABELS[problem['side']]} {problem['id']}: {'; '.join(problem['reasons'])}")
    dev_skipped = [i for i in s["items"] if i["skipped"]]
    for item in dev_skipped:
        reasons = ", ".join(f"{SKIP_LABELS.get(k, k)} {n}회" for k, n in item["skipped"].items())
        lines.append(f"- 개선용 {item['id']}: {reasons}")
    held = s["by_split"].get("holdout")
    if held and held["skipped"]:
        lines.append(f"- 확인용: {held['skipped']}회 (점수 행 없음)")
    return lines


def _summary_lines(report: dict[str, Any]) -> list[str]:
    lines = [
        "## 결과 요약",
        "",
        "| 구분 | 문항 | 실행 | 채점 | 통과 | 실패 | 사람 확인 | 오류 | 건너뜀 | 첫 호출 맞음 | 꼭 모두 | must 빠짐 | "
        "결과에 없는 숫자 (실패·확인) | 금지 표현 (실행·건수) | 더 부른 호출 (실행·건수) | 첫 글자 p50·p95 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for side, g in report["summary"]["by_split"].items():
        v = g["verdicts"]
        values = g["values"]
        times = g["first_token_s"]
        lines.append(
            f"| {SIDE_LABELS[side]} | {g['items']} | {g['runs']} | {g['scored']} | {v.get('pass', 0)} | "
            f"{v.get('fail', 0)} | {v.get('check', 0)} | {g['errors']} | {g['skipped']} | "
            f"{_ratio(g['first_call']['correct'], g['scored'])} | {_ratio(g['must_all'], g['scored'])} | "
            f"{values['must_missing_runs']} | {values['invented_fail_runs']}·{values['invented_check_runs']} | "
            f"{g['rubric']['forbidden_runs']}·{g['rubric']['forbidden_hits']} | "
            f"{g['follow_up']['runs_with_extra']}·{g['follow_up']['extra_calls']} | "
            f"{fmt(times['p50'])}·{fmt(times['p95'])} |"
        )
    lines.append("")
    for side, g in report["summary"]["by_split"].items():
        errors = _counts(g["first_call"]["errors"], ts.ERROR_LABELS)
        analysis = _counts(g["first_call"]["analysis"], ANALYSIS_LABELS)
        finish = _counts(g["finish_reasons"], FINISH_LABELS)
        lines.append(f"- {SIDE_LABELS[side]}: 첫 호출 오답 {errors} · 분석 인자 {analysis} · 끝난 이유 {finish}")
    return lines


def _safety_lines(report: dict[str, Any]) -> list[str]:
    groups = {side: g["safety"] for side, g in report["summary"]["by_split"].items()}
    if not any(s["block_runs"] or s["pass_runs"] for s in groups.values()):
        return []
    lines = [
        "## 안전 문항 (바뀐 뒤 동작과 비교)",
        "",
        "| 구분 | 차단 문항 실행 | LLM 전 차단 | 고정 답 글자 그대로 | 통과 문항 실행 | 경계 확인 통과 | 되돌림 | "
        "지금 동작과 같은 답 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for side, s in groups.items():
        lines.append(
            f"| {SIDE_LABELS[side]} | {s['block_runs']} | {s['blocked']} | {s['exact']} | {s['pass_runs']} | "
            f"{s['passed']} | {s['reverted']} | {s['same_as_current']} |"
        )
    return lines


def _item_lines(report: dict[str, Any]) -> list[str]:
    items = report["summary"]["items"]
    if not items:
        return []
    lines = [
        "## 문항별 결과 (개선용)",
        "",
        "| 문항 | 종류 | 실행 | 판정 | 첫 호출 맞음 | 첫 호출 분포 | 꼭 모두 | 금지 표현 | 더 부른 호출 | 안전 | "
        "끝난 이유 | 첫 글자 중앙값(초) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i in items:
        scored = sum(i["verdicts"].values())
        if i["skipped"]:
            verdict = ", ".join(f"{SKIP_LABELS.get(k, k)} {n}" for k, n in i["skipped"].items())
        else:
            verdict = _counts(i["verdicts"], VERDICT_LABELS)
        safety = i["safety"]
        safety_text = "-"
        if safety["runs"]:
            safety_text = (
                f"차단 {safety['blocked']} · 글자 그대로 {safety['exact']} · 통과 {safety['passed']} · "
                f"되돌림 {safety['reverted']} · 지금과 같음 {safety['same_as_current']}"
            )
        first_token = "-" if i["first_token_s"] is None else f"{i['first_token_s']:.2f}"
        lines.append(
            f"| {i['id']} | {i['subtype']} | {i['runs']} | {_md(verdict)} | "
            f"{_ratio(i['first_call_correct'], scored)} | {_md(_counts(i['first_calls']))} | "
            f"{_ratio(i['must_all'], scored)} | {_md(_counts(i['forbidden']))} | {_md(_counts(i['extra_calls']))} | "
            f"{safety_text} | "
            f"{_md(_counts(i['finish_reasons'], FINISH_LABELS))} | {first_token} |"
        )
    return lines


def _failure_lines(report: dict[str, Any]) -> list[str]:
    failures = report["summary"]["failures"]
    if not report["summary"]["items"]:
        return []
    lines = ["## 실패·사람 확인 이유 (개선용)", ""]
    if not failures:
        return [*lines, "없음"]
    lines += ["| 문항 | 회차 | 판정 | 이유 |", "| --- | --- | --- | --- |"]
    for f in failures:
        reasons = "; ".join(f["reasons"]) or "결과에 없는 숫자 사람 확인"
        lines.append(f"| {f['id']} | {f['run']} | {VERDICT_LABELS[f['verdict']]} | {_md(reasons)} |")
    return lines


def to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# 수면 코칭 골든셋 평가 ({report['measured_at']})", "", *header_lines(report), ""]
    for section in (_skipped_lines, _summary_lines, _safety_lines, _item_lines, _failure_lines):
        part = section(report)
        if part:
            lines += [*part, ""]
    lines += ["## 판정 기준", "", *(f"- {rule}" for rule in SCORING_RULES), ""]
    return "\n".join(lines)
