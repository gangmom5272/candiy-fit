"""골든셋 실행 기록을 집계하고 md 보고서를 만든다."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from bench.probe_llm import fmt, pct
from coach_ai.agent.prompts import today_text
from eval import answer_format
from eval.golden import answer_check
from eval.golden.items import SETS
from eval.golden.scoring import SCORING_RULES
from eval.golden.split import SPLIT_LABELS

VERDICT_LABELS = {
    "pass": "통과",
    "fail": "실패",
    "check": "사람 확인",
    "pending": "2단계",
    "none": "-",
    "skipped": "건너뜀",
    "error": "오류",
}
EXCLUDED_LABELS = {
    "skipped": "실행하지 않은 기록",
    "error": "실행 오류",
    "requires": "문항 requires (따로 집계)",
    "no_auto": "자동 채점 항목 없음",
}
STATUS_NAMES = {
    "missing_days": "기록 없음",
    "unsynced": "동기화 전",
    "in_progress": "오늘 진행 중",
    "no_data": "기간 내 기록 없음",
    "before_link": "연동 전",
    "slow_walk_excluded": "느린 걷기 제외",
}
MUST_NOT_NAMES = {
    "invented_number": "지어낸 수치",
    "diagnosis": "진단 표현",
    "missing_as_zero": "결측을 0으로",
    "in_progress_as_total": "진행 중 값을 합계처럼",
    "prescription": "처방",
    "causal_claim": "원인 단정",
    "unsafe_reassurance": "위험 신호에 안심",
    "blame": "탓함",
    "generic_only": "일반론만",
}
MUST_NAMES = {
    "personalized": "개인 데이터 근거",
    "small_action": "작은 행동",
    "no_blame": "탓하지 않음",
    "hedged_relation": "관계를 단정하지 않음",
    "few_days_hedged": "기록 적은 기간 단정하지 않음",
    "medical_referral": "의료 안내",
    "answers_normally": "막히지 않고 답함",
    "expected_guard": "경계 llm 문항의 안전한 답변",
}
# 자동 채점이 확인하지 않는 것. 실행 보고서 머리말에 적는다
LIMITATION_LINE = (
    "- 한계: 답변의 수치를 어느 기간·지표 값인지 연결하지 않아, 같은 단위 수치 중 하나만 정답과 맞아도 통과다 "
    "(이번 주와 지난주 값을 바꿔 말해도 통과). 상태 표현도 어느 날짜·지표에 대한 것인지 연결하지 않고 답변 전체에서 "
    "찾는다."
)
_REASONS = {
    "fact_missing": "수치 없음",
    "fact_wrong": "수치 틀림",
    "fact_direction": "차이 방향 반대",
    "value_present": "기록 없는 사실에 수치 있음",
    "guard_missed": "경계 미탐",
    "guard_wrong": "경계 범주 다름",
    "guard_overblock": "과차단",
    "guard_check": "경계 범주 확인",
    "few_days_missing": "기록이 적다는 말 없음",
    "few_days_asserted": "기록 적은 기간 증감 단정",
    "few_days_check": "기록 적은 기간 단정 표현 확인",
}


def reason_label(reason: str, *, failure: bool = True) -> str:
    if reason.startswith("status_"):
        name = STATUS_NAMES.get(reason.removeprefix("status_"), reason)
        return f"{name} {'누락' if failure else '표현 확인'}"
    if reason.startswith("must_not_"):
        return MUST_NOT_NAMES.get(reason.removeprefix("must_not_"), reason)
    return _REASONS.get(reason, reason)


def _md(text: Any, limit: int | None = None) -> str:
    """표·목록 한 줄에 넣을 수 있게 줄바꿈을 공백으로 바꾸고 | 를 이스케이프한다."""
    s = " ".join(str(text).split()).replace("|", "\\|")
    return s if limit is None or len(s) <= limit else s[: limit - 1] + "…"


def _key(record: dict[str, Any]) -> str:
    return f"{record['version']}/{record['id']}"


def _ratio(n: int, d: int) -> float | None:
    return n / d if d else None


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _score(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("score") or {}


def _counts(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """실행 단위 통과율. 채점은 통과율에 넣는 실행(counted)이다."""
    scored = [r for r in records if _score(r).get("counted")]
    verdicts = Counter(_score(r)["verdict"] for r in scored)
    return {
        "items": len({_key(r) for r in records}),
        "runs": len(records),
        "scored": len(scored),
        "pass": verdicts["pass"],
        "fail": verdicts["fail"],
        "check": verdicts["check"],
        "rate": _ratio(verdicts["pass"], len(scored)),
        "rate_with_check": _ratio(verdicts["pass"] + verdicts["check"], len(scored)),
    }


def _grouped(records: Sequence[dict[str, Any]], key: Callable[[dict[str, Any]], str]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[key(r)].append(r)
    return {name: _counts(rs) for name, rs in sorted(groups.items())}


def _item_stats(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    first = records[0]
    executed = [r for r in records if r["executed"]]
    signatures = Counter(r["calls_signature"] for r in executed if not r["error_code"] and not r["blocked_by"])
    scores = [_score(r) for r in records]
    return {
        "key": _key(first),
        "version": first["version"],
        "id": first["id"],
        # 문항 정의를 기록하기 전에 저장한 실행별 기록에는 없다
        "definition_sha256": first.get("definition_sha256"),
        "persona": first["persona"],
        "category": first["category"],
        "subtype": first["subtype"],
        "kind": scores[0].get("kind"),
        "question": first["question"],
        "runs": len(records),
        "executed": len(executed),
        "skip_reason": first["skip_reason"],
        "history_turns": first.get("history_turns", 0),
        "errors": sum(bool(r["error_code"]) for r in executed),
        "blocked": dict(Counter(r["blocked_by"] for r in executed if r["blocked_by"])),
        "calls": dict(signatures.most_common()),
        **{v: sum(s.get("counted", False) and s["verdict"] == v for s in scores) for v in ("pass", "fail", "check")},
        "excluded": dict(Counter(s["excluded"] for s in scores if s.get("excluded"))),
        "failures": dict(Counter(f for s in scores for f in s.get("failures", [])).most_common()),
        "checks": dict(Counter(c for s in scores for c in s.get("checks", [])).most_common()),
    }


def _fact_rows(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for r in records:
        for index, fact in enumerate(_score(r).get("facts", []), 1):
            keep = ("metric", "period", "field", "unit", "baseline", "date", "expected", "expected_display")
            rows.append(
                {
                    "key": _key(r),
                    "id": r["id"],
                    "run": r["run"],
                    "fact": index,
                    **{k: fact[k] for k in keep},
                    "tolerance": fact["tolerance"],
                    "answer": fact["answer"],
                    "answer_text": fact["answer_text"],
                    "verdict": fact["verdict"],
                    "reason": fact["reason"],
                    "separate": fact["separate"],
                }
            )
    return rows


def _check_stats(records: Sequence[dict[str, Any]], part: str, name: str) -> dict[str, dict[str, int]]:
    """상태·기준·금지 항목별 판정 수. 따로 세는(requires) 항목은 넣지 않는다."""
    stats: dict[str, Counter[str]] = defaultdict(Counter)
    for r in records:
        if not _score(r).get("counted"):
            continue
        for row in _score(r).get(part, []):
            if not row["separate"]:
                stats[row[name]][row["verdict"]] += 1
    return {code: dict(c) for code, c in sorted(stats.items())}


def _requires_rows(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for r in records:
        s = _score(r)
        req = s.get("requires") or {}
        if not req.get("unmet") and not req.get("met"):
            continue
        row = rows.setdefault(
            _key(r),
            {
                "key": _key(r),
                "id": r["id"],
                "unmet": req["unmet"],
                "met": req["met"],
                "excluded": s.get("excluded") == "requires",
                "targets": [],
                "verdicts": Counter(),
            },
        )
        separate = [f"사실 {f['metric']}.{f['field']}" for f in s.get("facts", []) if f["separate"]]
        separate += [f"상태 {x['status']}" for x in s.get("statuses", []) if x["separate"]]
        separate += [f"기준 {x['code']}" for x in s.get("must", []) if x["separate"]]
        row["targets"] = sorted(set(row["targets"]) | set(separate))
        if req.get("verdict"):
            row["verdicts"][req["verdict"]] += 1
    return [{**row, "verdicts": dict(row["verdicts"])} for row in rows.values()]


def _human_checks(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in records:
        s = _score(r)
        requires_check = (s.get("requires") or {}).get("verdict") == "check"
        if s.get("verdict") != "check" and not requires_check:
            continue
        reasons = [reason_label(c, failure=False) for c in s.get("checks", [])]
        if requires_check:
            reasons.append("requires 항목")
        out.append({"key": _key(r), "id": r["id"], "run": r["run"], "reasons": reasons, "answer": r["answer"] or ""})
    return out


def _pending(records: Sequence[dict[str, Any]]) -> dict[str, list[str]]:
    items: dict[str, set[str]] = defaultdict(set)
    for r in records:
        for code in _score(r).get("pending", []):
            items[code].add(r["id"])
    return {code: sorted(ids) for code, ids in sorted(items.items())}


def _model_mismatches(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """모델 답변이 정답과 다른 문항. 채점에 넣은 실행 중 실패한 실행의 이유와 틀린 사실의 정답·답변 값이다."""
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        s = _score(r)
        if not s.get("counted") or s.get("verdict") != "fail":
            continue
        row = out.setdefault(
            _key(r), {"key": _key(r), "id": r["id"], "runs": 0, "reasons": Counter(), "facts": defaultdict(list)}
        )
        row["runs"] += 1
        row["reasons"].update(s["failures"])
        for f in s.get("facts", []):
            if f["verdict"] == "fail" and not f["separate"]:
                name = f"{f['metric']} {f['period']} {f['field']}"
                row["facts"][f"{name} 정답 {f['expected_display']}"].append(
                    f["answer"] if f["answer"] is not None else "-"
                )
    return [{**row, "reasons": dict(row["reasons"]), "facts": dict(row["facts"])} for row in out.values()]


def _item_checks(records: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """채점 항목별 판정 수. 사실은 하나로 묶고 상태·기준·금지 항목은 코드마다 센다. 따로 세는 항목은 넣지 않는다."""
    counted = [_score(r) for r in records if _score(r).get("counted")]
    out: dict[str, dict[str, int]] = {
        "facts": dict(Counter(f["verdict"] for s in counted for f in s.get("facts", []) if not f["separate"]))
    }
    for part, name, prefix in (("statuses", "status", "status"), ("must", "code", "must"), ("must_not", "code", "not")):
        out |= {f"{prefix}:{code}": c for code, c in _check_stats(records, part, name).items()}
    guard = Counter(s["guard"]["verdict"] for s in counted if s.get("guard") and not s["guard"]["separate"])
    if guard:
        out["guard"] = dict(guard)
    return out


def _format_stats(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """답변 형식 집계 (answer_format). 통과·실패 판정에는 넣지 않는다.

    경계 확인에 걸렸거나 오류로 끝난 실행은 모델 답변이 아니라 세지 않는다. counts 는 묶음(all, dev, holdout)마다
    답변 수와 형식별로 그 형식이 나온 답변 수다. items 는 형식이 하나라도 나온 실행이고 확인용 실행은 넣지 않는다.
    day_by_day 는 날짜별 결과를 받은 답변만 따로 센 것이다. counts 는 묶음마다 그 답변 수와 표를 쓴 답변 수이고,
    no_table 은 표가 없는 개선용 실행이다.
    """
    answered = [
        (r, answer_format.answer_forms(r["answer"] or ""))
        for r in records
        if r["executed"] and not r["error_code"] and not r["blocked_by"]
    ]
    groups = {"all": [forms for _, forms in answered]}
    for side in SPLIT_LABELS:
        if any(r.get("split") == side for r in records):
            groups[side] = [forms for r, forms in answered if r.get("split") == side]
    counts = {
        name: {"answers": len(rows), **{form: sum(form in forms for forms in rows) for form in answer_format.LABELS}}
        for name, rows in groups.items()
    }
    items = [
        {"key": _key(r), "id": r["id"], "run": r["run"], "question": r["question"], "forms": forms}
        for r, forms in answered
        if forms and r.get("split") != "holdout"
    ]
    dated = [(r, forms) for r, forms in answered if answer_format.day_by_day_result(r.get("tool_calls"))]
    day_groups = {"all": [forms for _, forms in dated]}
    for side in SPLIT_LABELS:
        if any(r.get("split") == side for r in records):
            day_groups[side] = [forms for r, forms in dated if r.get("split") == side]
    day_by_day = {
        "counts": {
            name: {"answers": len(rows), "table": sum("table" in forms for forms in rows)}
            for name, rows in day_groups.items()
        },
        "no_table": [
            {"id": r["id"], "run": r["run"], "question": r["question"]}
            for r, forms in dated
            if "table" not in forms and r.get("split") != "holdout"
        ],
    }
    return {"counts": counts, "items": items, "day_by_day": day_by_day}


def summarize(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """실행별 기록을 집계한다.

    확인용(holdout) 실행은 결과 요약(overall, by_set, by_split), 채점 항목별 집계(checks_by_split), 답변 형식의
    수(format.counts)에만 넣는다. 문항별 결과·답변·실패 이유와 영역별 통과율은 나머지 실행(visible)만 담는다.
    """
    visible = [r for r in records if r.get("split") != "holdout"]
    split_records = [r for r in records if r.get("split")]
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in visible:
        by_key[_key(r)].append(r)
    executed = [r for r in records if r["executed"]]
    calls = [c for r in executed for c in r["llm_calls"]]
    totals = [c["total_s"] for c in calls if c["total_s"] is not None]
    elapsed = [r["elapsed_s"] for r in executed]
    counted = [r for r in visible if _score(r).get("counted")]
    failures: dict[str, Counter[str]] = defaultdict(Counter)
    for r in counted:
        for reason in _score(r).get("failures", []):
            failures[reason][r["id"]] += 1
    return {
        "runs": len(records),
        "visible_runs": len(visible),
        "executed": len(executed),
        "skipped": dict(Counter(r["skip_reason"] for r in records if not r["executed"])),
        "errors": dict(Counter(r["error_code"] for r in executed if r["error_code"]).most_common()),
        "blocked": dict(Counter(r["blocked_by"] for r in executed if r["blocked_by"]).most_common()),
        # JSON 으로 저장하면 키가 문자열이 되므로 처음부터 문자열로 둔다
        "tool_rounds": {str(n): c for n, c in sorted(Counter(r["tool_rounds"] for r in executed).items())},
        "elapsed_p50": pct(elapsed, 50),
        "elapsed_p95": pct(elapsed, 95),
        "llm": {
            "calls": len(calls),
            "errors": sum(bool(c["error_code"]) for c in calls),
            "input_tokens": sum(c["input_tokens"] or 0 for c in calls),
            "output_tokens": sum(c["output_tokens"] or 0 for c in calls),
            "total_p50": pct(totals, 50),
            "total_p95": pct(totals, 95),
        },
        "scoring": {
            "overall": _counts(records),
            "by_set": _grouped(records, lambda r: r["version"]),
            "by_split": _grouped(split_records, lambda r: r["split"]),
            "by_set_split": _grouped(split_records, lambda r: f"{r['version']} {r['split']}"),
            "checks_by_split": {
                side: _item_checks([r for r in split_records if r["split"] == side])
                for side in sorted({r["split"] for r in split_records})
            },
            "by_persona": _grouped(visible, lambda r: r["persona"]),
            "by_kind": _grouped(visible, lambda r: f"{r['version']} {_score(r).get('kind')}"),
            "by_category": _grouped(visible, lambda r: f"{r['version']} {r['category']}"),
            "excluded": dict(Counter(_score(r).get("excluded") for r in records if not _score(r).get("counted"))),
            "failure_types": {
                reason: {"runs": sum(c.values()), "items": dict(c)}
                for reason, c in sorted(failures.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
            },
            "statuses": _check_stats(visible, "statuses", "status"),
            "must": _check_stats(visible, "must", "code"),
            "must_not": _check_stats(visible, "must_not", "code"),
            "facts": _fact_rows(visible),
            "requires": _requires_rows(visible),
            "model_mismatches": _model_mismatches(visible),
            "human_checks": _human_checks(visible),
            "pending": _pending(visible),
        },
        "format": _format_stats(records),
        "items": [_item_stats(rs) for rs in by_key.values()],
    }


def dataset_line(d: dict[str, Any]) -> str:
    return f"- 데이터셋 `{d['path']}` (버전 {d['version']}) {d['selected']}/{d['rows']}문항 · sha256 {d['sha256'][:12]}"


def _temperature_text(report: dict[str, Any]) -> str:
    if report["temperature_source"] is None:
        return "- (LLM 호출 없음)"
    if report["temperature_source"] == "server_default":
        return "서버 기본값 (요청에 넣지 않음)"
    return f"{report['temperature']} (요청에 넣음)"


def _prompt_text(report: dict[str, Any]) -> str:
    day = report.get("prompt_date")
    inserted = "없음" if day is None else today_text(date.fromisoformat(day))
    return f"`{report['prompt_version']}` (템플릿 sha256 {report['prompt_sha256']}, 넣은 날짜 {inserted})"


def header_lines(report: dict[str, Any]) -> list[str]:
    s = report["summary"]
    llm = s["llm"]
    data = report["data"]
    model = f"모델 `{report['model']}` · 호스트 `{report['base_url_host']}`"
    if report["mock"]:
        model = f"모의 응답 (--mock, 사내 LLM 호출 없음) · {model}"
    diffs = data["truth_diffs"]
    fields = sum(len(d["fields"]) for d in diffs)
    lines = [
        f"- 커밋 `{report['commit']}` · {model}",
        f"- 실행 명령 `{report['command']}`",
        f"- 프롬프트 {_prompt_text(report)} · 도구 스키마 sha256 {report['tools_sha256']}",
        *(dataset_line(d) for d in report["datasets"]),
        *_split_lines(report),
        f"- 고정 데이터 {len(data['personas'])}개 sha256 {data['fixtures_sha256'][:12]} · "
        f"정답 원형 sha256 {data['truth_sha256'][:12]} · 고정 데이터로 조회한 원형이 정답 원형과 "
        f"{data['truth_compared'] - len(diffs)}/{data['truth_compared']}개 같음"
        + (f' (다른 원형 {len(diffs)}개, 다른 칸 {fields}개는 아래 "고정 데이터 대조")' if diffs else ""),
        f"- 기준 시각 {report['now']} · 반복 {report['repeat']}회 · temperature {_temperature_text(report)} · "
        f"채점 규칙 sha256 {report['scoring_sha256']}",
        f"- 실행 {s['runs']}회 (실행 {s['executed']}, 건너뜀 {s['runs'] - s['executed']}) · "
        f"LLM 호출 {llm['calls']}회 · 실패 {llm['errors']} · 입력 토큰 {llm['input_tokens']:,} · "
        f"출력 토큰 {llm['output_tokens']:,} · 호출 시간 p50 {fmt(llm['total_p50'])} p95 {fmt(llm['total_p95'])} · "
        f"전체 {report['elapsed_s']}초",
    ]
    if report.get("records_file"):
        lines.append(f"- 실행별 기록 `{report['records_file']}` (git 제외, 답변 원문 포함)")
    for r in report.get("rescores", []):
        paper = f" · 측정 때와 다른 시험지: {', '.join(r['paper_changes'])}" if r.get("paper_changes") else ""
        held = f" (확인용 {r['holdout_changed']}건은 문항을 적지 않음)" if r.get("holdout_changed") else ""
        lines.append(
            f"- 다시 채점 {r['rescored_at']} · 커밋 `{r['commit']}` · 채점 규칙 sha256 {r['scoring_sha256']} · "
            f"`{r['source_records']}` 를 LLM 호출 없이 다시 채점 · 바뀐 판정 {r['changed']}건{held}{paper}"
        )
        lines += [
            f"  - {c['id']} {c['run']}회 · {VERDICT_LABELS.get(c['before'], c['before'])} → "
            f"{VERDICT_LABELS.get(c['after'], c['after'])}"
            for c in r["changes"]
        ]
    lines += [
        "- 채점: 자동 채점 1단계(사실 수치, 상태 표현, 기록이 적은 기간 비교, 금지 항목 4종, guard). v2 형식은 이 "
        '항목만 채점하고 나머지 rubric 기준은 2단계 대상으로 따로 센다. 기준은 아래 "판정 기준"',
        LIMITATION_LINE,
    ]
    if report.get("answer_check"):
        lines.append(answer_check.summary_line(report["answer_check"]))
    return lines


def _split_lines(report: dict[str, Any]) -> list[str]:
    split = report.get("split")
    if not split:
        return []
    counts = ", ".join(f"{v} 개선용 {c['dev']}·확인용 {c['holdout']}" for v, c in split["counts"].items())
    lines = [
        f"- 문항 나누기 `--split {split['name']}` · seed {split['seed']} · 나누기 sha256 {split['sha256']} · {counts}"
    ]
    s = report["summary"]
    if s.get("visible_runs", s["runs"]) < s["runs"]:
        lines.append(
            "- 확인용 문항은 결과 요약과 채점 항목별 집계에만 넣었다. 문항별 결과·답변·실패 이유와 영역별 통과율은 "
            "개선용 문항만 다룬다"
        )
    return lines


def _model_lines(report: dict[str, Any]) -> list[str]:
    rows = report["summary"]["scoring"]["model_mismatches"]
    check = (report.get("answer_check") or {}).get("summary", {})
    flagged = dict.fromkeys(check.get("two_readings", []), "두 가지로 읽힘") | dict.fromkeys(
        check.get("problems", []), "걸림"
    )
    lines = [
        "## 모델 답과 정답이 다른 문항",
        "",
        "모델 답변을 정답으로 채점해 실패한 문항이다. 정답 검사 칸이 걸림이나 두 가지로 읽힘이면 "
        "정답 쪽 문제일 수 있어 위 정답 검사 절을 함께 본다.",
        "",
    ]
    if not rows:
        return [*lines, "없음"]
    lines += ["| 문항 | 실패 실행 | 이유 | 틀린 사실 (답변 값) | 정답 검사 |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        reasons = ", ".join(f"{reason_label(r)} {n}" for r, n in row["reasons"].items())
        facts = "; ".join(f"{name} ({', '.join(map(str, answers))})" for name, answers in row["facts"].items())
        lines.append(
            f"| {row['id']} | {row['runs']} | {_md(reasons)} | {_md(facts or '-')} | {flagged.get(row['id'], '-')} |"
        )
    return lines


def _rate_table(title: str, groups: dict[str, Any], label: str) -> list[str]:
    lines = [
        f"## {title}",
        "",
        f"| {label} | 문항 | 실행 | 채점 | 통과 | 실패 | 사람 확인 | 통과율 | 사람 확인 포함 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, g in groups.items():
        lines.append(
            f"| {name} | {g['items']} | {g['runs']} | {g['scored']} | {g['pass']} | {g['fail']} | {g['check']} | "
            f"{_pct(g['rate'])} | {_pct(g['rate_with_check'])} |"
        )
    return lines


def _set_label(version: str, side: str | None = None) -> str:
    label = version if side is None else f"{version} {SPLIT_LABELS[side]}"
    return f"{label} (자동 채점 항목만)" if SETS[version].form == "v2" else label


def _summary_lines(report: dict[str, Any]) -> list[str]:
    sc = report["summary"]["scoring"]
    groups = {"전체": sc["overall"]}
    groups |= {_set_label(k): v for k, v in sc["by_set"].items()}
    for key, value in sc.get("by_set_split", {}).items():
        version, side = key.split(" ", 1)
        groups[_set_label(version, side)] = value
    lines = _rate_table("결과 요약", groups, "묶음")
    lines += [
        "",
        "- 통과율 = 통과 / 채점. 사람 확인 포함 = (통과 + 사람 확인) / 채점",
    ]
    if sc["excluded"]:
        excluded = ", ".join(f"{EXCLUDED_LABELS.get(k, k)} {n}" for k, n in sc["excluded"].items())
        lines.append(f"- 채점에서 뺀 실행: {excluded}")
    return lines


def _failure_lines(report: dict[str, Any]) -> list[str]:
    types = report["summary"]["scoring"]["failure_types"]
    lines = ["## 실패 유형", ""]
    if not types:
        return [*lines, "실패 없음"]
    lines += ["| 유형 | 실행 | 문항 (실행 수) |", "| --- | --- | --- |"]
    for reason, t in types.items():
        items = ", ".join(f"{i} ({n})" for i, n in t["items"].items())
        lines.append(f"| {reason_label(reason)} | {t['runs']} | {_md(items)} |")
    return lines


def _item_lines(report: dict[str, Any]) -> list[str]:
    lines = [
        "## 문항별 결과",
        "",
        "| 문항 | 페르소나 | 질문 종류 | 통과 | 실패 | 사람 확인 | 따로 셈 | 실패·확인 이유 | 도구 호출 (횟수) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for st in report["summary"]["items"]:
        reasons = [f"{reason_label(r)} {n}" for r, n in st["failures"].items()]
        reasons += [f"{reason_label(c, failure=False)} {n}" for c, n in st["checks"].items()]
        excluded = ", ".join(f"{EXCLUDED_LABELS.get(k, k)} {n}" for k, n in st["excluded"].items()) or "-"
        calls = "; ".join(f"{sig} ({n})" for sig, n in st["calls"].items())
        calls = "; ".join(filter(None, [calls, *(f"경계 {c} ({n})" for c, n in st["blocked"].items())])) or "-"
        lines.append(
            f"| {st['id']} | {st['persona']} | {st['kind']} | {st['pass']} | {st['fail']} | {st['check']} | "
            f"{_md(excluded)} | {_md(', '.join(reasons) or '-')} | {_md(calls)} |"
        )
    return lines


def _fact_lines(report: dict[str, Any]) -> list[str]:
    rows = report["summary"]["scoring"]["facts"]
    lines = [
        "## 사실별 정답 값과 답변 값",
        "",
        "정답은 원형 값, 비교 값은 표시 자리수로 반올림한 값이다. 답변 값은 맞은 수치, 없으면 가장 가까운 수치다.",
        "",
        "| 문항 | 회 | 사실 | 정답 | 비교 값 | 허용 오차 | 답변 값 | 답변 표기 | 판정 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for f in rows:
        what = f"{f['metric']} {f['period']} {f['field']}" + (f" (기준 {f['baseline']})" if f["baseline"] else "")
        what += f" {f['date']}" if f["date"] else ""
        verdict = VERDICT_LABELS[f["verdict"]] + (f" ({reason_label(f['reason'])})" if f["reason"] else "")
        verdict += " · requires 따로 셈" if f["separate"] else ""
        answer = "-" if f["answer"] is None else f["answer"]
        lines.append(
            f"| {f['id']} | {f['run']} | {_md(what)} | {f['expected']} | {f['expected_display']} | "
            f"{f['tolerance']:g} | {answer} | {_md(f['answer_text'] or '-')} | {_md(verdict)} |"
        )
    return lines


def _stat_lines(report: dict[str, Any]) -> list[str]:
    sc = report["summary"]["scoring"]
    lines = [
        "## 상태·기준·금지 항목",
        "",
        "| 항목 | 통과 | 실패 | 사람 확인 | 2단계 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for code, c in sc["statuses"].items():
        name = f"상태: {STATUS_NAMES.get(code, code)}"
        lines.append(f"| {name} | {c.get('pass', 0)} | {c.get('fail', 0)} | {c.get('check', 0)} | - |")
    for code, c in sc.get("must", {}).items():
        name = f"기준: {MUST_NAMES.get(code, code)}"
        lines.append(f"| {name} | {c.get('pass', 0)} | {c.get('fail', 0)} | {c.get('check', 0)} | - |")
    for code, c in sc["must_not"].items():
        name = f"금지: {MUST_NOT_NAMES.get(code, code)}"
        lines.append(
            f"| {name} | {c.get('pass', 0)} | {c.get('fail', 0)} | {c.get('check', 0)} | {c.get('pending', 0)} |"
        )
    return lines


def _check_label(key: str) -> str:
    if key == "facts":
        return "사실 수치"
    if key == "guard":
        return "guard"
    prefix, code = key.split(":", 1)
    if prefix == "status":
        return f"상태: {STATUS_NAMES.get(code, code)}"
    if prefix == "must":
        return f"기준: {MUST_NAMES.get(code, code)}"
    return f"금지: {MUST_NOT_NAMES.get(code, code)}"


def _split_check_lines(report: dict[str, Any]) -> list[str]:
    checks = report["summary"]["scoring"].get("checks_by_split") or {}
    if not checks:
        return []
    sides = [side for side in SPLIT_LABELS if side in checks]
    head = " | ".join(f"{SPLIT_LABELS[side]} {v}" for side in sides for v in ("통과", "실패", "사람 확인"))
    lines = [
        "## 채점 항목별 (개선용·확인용)",
        "",
        "채점에 넣은 실행의 항목별 판정 수다. 확인용 문항은 문항별 결과를 넣지 않고 이 표와 결과 요약에만 센다.",
        "",
        f"| 항목 | {head} |",
        "| --- |" + " --- |" * (3 * len(sides)),
    ]
    # 2단계 대상이라 1단계 판정이 없는 항목(pending 만 있음)은 넣지 않는다
    verdicts = ("pass", "fail", "check")
    keys = list(
        dict.fromkeys(key for side in sides for key, c in checks[side].items() if any(c.get(v, 0) for v in verdicts))
    )
    order = {"facts": 0, "status": 1, "must": 2, "not": 3, "guard": 4}
    for key in sorted(keys, key=lambda k: (order[k.split(":", 1)[0]], keys.index(k))):
        cells = " | ".join(str(checks[side].get(key, {}).get(v, 0)) for side in sides for v in verdicts)
        lines.append(f"| {_check_label(key)} | {cells} |")
    return lines


def _format_lines(report: dict[str, Any]) -> list[str]:
    """답변 형식 절. 이전에 저장한 집계에 day_by_day 가 없으면 그 하위 절만 뺀다."""
    stats = report["summary"].get("format")
    if not stats:
        return []
    labels = answer_format.LABELS
    names = {"all": "전체", **SPLIT_LABELS}
    lines = [
        "## 답변 형식",
        "",
        "화면과 정한 답변 형식(docs/agent-interface.md 5장)을 지켰는지 센 수다. 칸은 그 형식이 나온 답변 수이고, "
        "통과·실패 판정에는 넣지 않는다. 경계 확인에 걸렸거나 오류로 끝난 실행은 세지 않는다.",
        "",
        "| 묶음 | 답변 | " + " | ".join(labels.values()) + " |",
        "| --- |" + " --- |" * (len(labels) + 1),
    ]
    for group, c in stats["counts"].items():
        lines.append(f"| {names[group]} | {c['answers']} | " + " | ".join(str(c[form]) for form in labels) + " |")
    agreed, outside = "·".join(answer_format.AGREED.values()), "·".join(answer_format.OUTSIDE.values())
    lines += [
        "",
        f"- 약속한 형식은 {agreed}, 약속 밖 형식은 {outside} 이다. 구분선 없음은 머리 행 다음 줄에 "
        "머리 행과 칸 수가 같은 구분선이 없어 화면이 표로 그리지 않는 | 줄이다. "
        f"{labels['subheading_first']}은 첫 덩어리가 소제목이라 화면이 요약 글씨를 그리지 않는 답이다",
    ]
    # 확인용 문항만 실행했으면 문항 목록을 넣지 않는다
    if not report["summary"].get("visible_runs", report["summary"]["runs"]):
        return lines
    runs: dict[str, list[int]] = defaultdict(list)
    questions: dict[str, str] = {}
    for row in stats["items"]:
        if "table" in row["forms"]:
            runs[row["id"]].append(row["run"])
            questions[row["id"]] = row["question"]
    lines += ["", "### 표를 쓴 답변 (개선용)", ""]
    if runs:
        lines += ["| 문항 | 실행 | 질문 |", "| --- | --- | --- |"]
        lines += [f"| {i} | {', '.join(map(str, rs))} | {_md(questions[i])} |" for i, rs in runs.items()]
    else:
        lines.append("없음")
    day = stats.get("day_by_day")
    if day:
        lines += ["", "### 날짜별 결과를 받은 답변의 표", ""]
        lines += [
            "날짜별 결과는 값이 있는 날 2일 이상의 날짜별 추이나 밤 2개 이상의 수면 기록을 받은 경우다.",
            "",
            "| 묶음 | 날짜별 결과 답변 | 표를 쓴 답변 |",
            "| --- | --- | --- |",
        ]
        lines += [f"| {names[group]} | {c['answers']} | {c['table']} |" for group, c in day["counts"].items()]
        lines += ["", "표를 쓰지 않은 개선용 실행:", ""]
        if day["no_table"]:
            lines += ["| 문항 | 회 | 질문 |", "| --- | --- | --- |"]
            lines += [f"| {row['id']} | {row['run']} | {_md(row['question'])} |" for row in day["no_table"]]
        else:
            lines.append("없음")
    off = [(row, [f for f in row["forms"] if f not in answer_format.AGREED]) for row in stats["items"]]
    off = [(row, forms) for row, forms in off if forms]
    lines += ["", "### 약속 밖 형식이나 표 모양을 어긴 답변 (개선용)", ""]
    if off:
        lines += ["| 문항 | 회 | 형식 |", "| --- | --- | --- |"]
        lines += [f"| {row['id']} | {row['run']} | {', '.join(labels[f] for f in forms)} |" for row, forms in off]
    else:
        lines.append("없음")
    return lines


def _requires_lines(report: dict[str, Any]) -> list[str]:
    rows = report["summary"]["scoring"]["requires"]
    if not rows:
        return []
    lines = [
        "## requires 문항 (따로 집계)",
        "",
        "지금 도구로 알 수 없는 사실·상태다. 지금 있는 도구로 채울 수 있는 requires 는 일반 채점에 넣었다.",
        "",
        "| 문항 | 없는 것 | 지금 있음 | 따로 센 항목 | 따로 센 결과 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        targets = "문항 전체" if row["excluded"] else (", ".join(row["targets"]) or "-")
        verdicts = ", ".join(f"{VERDICT_LABELS[v]} {n}" for v, n in row["verdicts"].items()) or "-"
        lines.append(
            f"| {row['id']} | {_md(', '.join(row['unmet']) or '-')} | {_md(', '.join(row['met']) or '-')} | "
            f"{_md(targets)} | {verdicts} |"
        )
    return lines


def _human_lines(report: dict[str, Any]) -> list[str]:
    checks = report["summary"]["scoring"]["human_checks"]
    lines = ["## 사람 확인이 필요한 답변", ""]
    if not checks:
        return [*lines, "없음"]
    lines += [
        f"- `{c['id']}` {c['run']}회 · {_md(', '.join(c['reasons']) or '-')} · {_md(c['answer'], 300) or '(본문 없음)'}"
        for c in checks
    ]
    return lines


def _pending_lines(report: dict[str, Any]) -> list[str]:
    pending = report["summary"]["scoring"]["pending"]
    if not pending:
        return []
    lines = ["## 2단계 채점 대상", "", "| 기준 | 문항 수 | 문항 |", "| --- | --- | --- |"]
    for code, ids in pending.items():
        name = MUST_NAMES.get(code) or MUST_NOT_NAMES.get(code, code)
        lines.append(f"| {code} ({name}) | {len(ids)} | {_md(', '.join(ids))} |")
    return lines


def _execution_lines(report: dict[str, Any]) -> list[str]:
    s = report["summary"]
    rounds = ", ".join(f"{n}회 {c}" for n, c in s["tool_rounds"].items()) or "-"
    lines = [
        "## 실행",
        "",
        f"- 도구 왕복: {rounds}",
        f"- 실행 시간 p50 {fmt(s['elapsed_p50'])} p95 {fmt(s['elapsed_p95'])}",
    ]
    if s["errors"]:
        lines.append("- 오류로 끝난 실행: " + ", ".join(f"{code} {n}" for code, n in s["errors"].items()))
    if s["blocked"]:
        lines.append("- 경계 확인에 걸린 실행: " + ", ".join(f"{code} {n}" for code, n in s["blocked"].items()))
    follow_ups = [st for st in s["items"] if st.get("history_turns")]
    if follow_ups:
        names = ", ".join(f"`{st['id']}` (쌍 {st['history_turns']}개)" for st in follow_ups)
        lines.append(f"- 이전 대화를 CoachContext.turns 로 넣어 실행한 문항: {names}")
    skipped = [st for st in s["items"] if st["skip_reason"]]
    if skipped:
        names = ", ".join(f"`{st['id']}`" for st in skipped)
        lines.append(f"- 실행하지 않은 기록이 있는 문항: {names}")
    return lines


def _data_lines(report: dict[str, Any]) -> list[str]:
    diffs = report["data"]["truth_diffs"]
    if not diffs:
        return []
    lines = [
        "## 고정 데이터 대조",
        "",
        "도구는 고정 데이터로 조회하고 채점은 정답 원형 값으로 한다. 아래 칸은 두 원형이 다르다.",
        "",
        "| 페르소나 | 원형 | 다른 칸 |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {d['persona']} | {d['key']} | {_md(', '.join(d['fields']))} |" for d in diffs]
    return lines


def _rules_lines() -> list[str]:
    return ["## 판정 기준", "", *(f"- {rule}" for rule in SCORING_RULES)]


def to_markdown(report: dict[str, Any]) -> str:
    sc = report["summary"]["scoring"]
    check = report.get("answer_check")
    lines = [f"# 코칭 골든셋 평가 ({report['measured_at']})", ""]
    # 마지막 다시 채점이 측정 때와 다른 시험지로 채점했으면 경고를 맨 위에 넣는다
    if warning := (report.get("rescores") or [{}])[-1].get("paper_warning"):
        lines += [warning, ""]
    lines += [*header_lines(report), ""]
    sections = [_summary_lines(report), _split_check_lines(report), _format_lines(report)]
    # 확인용 문항만 실행했으면 문항별 결과·답변 절을 넣지 않는다
    if report["summary"].get("visible_runs", report["summary"]["runs"]):
        sections += [
            *(answer_check.section_lines(check) if check else []),
            _model_lines(report),
            answer_check.candidate_lines(check) if check else [],
            _rate_table("페르소나별", sc["by_persona"], "페르소나"),
            _rate_table("질문 종류별", sc["by_kind"], "질문 종류"),
            _rate_table("종류별", sc["by_category"], "종류"),
            _failure_lines(report),
            _item_lines(report),
            _fact_lines(report),
            _stat_lines(report),
            _requires_lines(report),
            _human_lines(report),
            _pending_lines(report),
        ]
    sections += [_execution_lines(report), _data_lines(report), _rules_lines()]
    for section in sections:
        if section:
            lines += [*section, ""]
    return "\n".join(lines).rstrip("\n") + "\n"
