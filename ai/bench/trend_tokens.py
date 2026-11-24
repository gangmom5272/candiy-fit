"""30일 일별 추이 조회가 LLM 입력에 더하는 양을 LLM 호출 없이 잰다.

ai/ 디렉터리에서 실행한다.
    uv run python -m bench.trend_tokens                                            # 결과 출력
    uv run python -m bench.trend_tokens --calibrate eval/results/<측정>_records.jsonl --save-doc

- 도구 메시지는 날짜 지정 질문 반복 검증(eval.repeated_question)의 합성 데이터·기준 시각으로 게이트웨이를 거쳐 만든다.
  LLM 에 전달하는 도구 메시지 본문 그대로다.
- 사내 LLM 토크나이저를 쓸 수 없어 토큰 수는 추정한다. 숫자는 한 글자를 1토큰으로 세고(숫자를 한 자리씩 나누는
  토크나이저로 가정한다), 나머지 글자는 글자당 rest_ratio 토큰으로 센다.
- --calibrate 는 rest_ratio 를 고치기 전 측정의 실제 입력 토큰으로 다시 맞춘다. 맞추는 방법은 calibrate 에 있다.
- 증가량은 고치기 전 측정 때 get_daily_trend 가 반환한 not_supported 메시지(BEFORE_REPLY)와 비교한다.
- --save-doc 이면 docs/benchmarks/<날짜>-daily-trend-tokens.md·json 으로 저장한다. 같은 날 기록이 있으면 이전 측정은
  그대로 두고 뒤에 붙인다(json 은 측정 목록). 직전 측정과 기간·rest_ratio·비율 맞추기 여부가 같을 때만 비교 표를
  넣는다. --note 는 무엇을 바꾼 뒤 잰 값인지 기록에 적는다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any

from bench.probe_llm import git_commit
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall, tool_specs
from coach_ai.history.tokens import REST_RATIO, estimate_tokens
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import ToolGateway
from coach_ai.tools.periods import resolve_period
from coach_ai.tools.schemas import METRICS, PERIODS
from eval import repeated_question as rq

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs" / "benchmarks"
DEFAULT_PERIOD = "last_30_days"
# 고치기 전 실행에서 기간 문자열이 도구 호출 인자에 들어간 횟수. 첫 왕복 호출 2개는 둘째·셋째 호출에,
# 둘째 왕복 호출 2개는 셋째 호출에 들어간다
PERIOD_REPEATS = 6
# 비율을 맞추는 고치기 전 실행 구조. 첫 왕복의 추이 호출 두 개는 not_supported 를 받는다
_BEFORE_SHAPE = [
    ("get_daily_trend", 1, "oxygen_saturation"),
    ("get_daily_trend", 1, "sleep"),
    ("get_metric_summary", 2, "oxygen_saturation"),
    ("get_sleep_records", 2, None),
]
# 글자 수 차이가 이보다 작은 쌍은 토큰 차이가 정수로 반올림되어 비율이 크게 흔들린다
_MIN_OTHERS_DIFF = 50
# 고치기 전 측정 때 get_daily_trend 가 반환한 도구 메시지. not_supported 문구가 바뀌어도 증가량의 기준은 같다
BEFORE_REPLY = json.dumps(
    {
        "error": "not_supported",
        "tool": "get_daily_trend",
        "message": "아직 지원하지 않는 도구다. 다른 도구로 답하거나 지원하지 않는다고 말한다.",
    },
    ensure_ascii=False,
)


def composition(text: str) -> dict[str, int]:
    digits = sum(ch.isdigit() for ch in text)
    return {"chars": len(text), "digits": digits, "others": len(text) - digits}


async def tool_contents(calls: Sequence[tuple[str, dict[str, Any]]]) -> list[str]:
    """합성 데이터로 한 응답의 도구 호출을 게이트웨이에 넣고 LLM 에 전달할 도구 메시지 본문을 반환한다."""
    gateway = ToolGateway(rq.synthetic_health_data(), rq.TODAY, audit=MemoryAuditSink())
    ctx = CoachContext(user_id=rq.USER_ID, conversation_id="bench-trend-tokens", request_id="bench-trend-tokens")
    replies = await gateway.execute([ToolCall(f"call_{i}", name, args) for i, (name, args) in enumerate(calls)], ctx)
    return [reply.content for reply in replies]


def _sized(text: str, rest_ratio: float) -> dict[str, int]:
    return {**composition(text), "tokens": estimate_tokens(text, rest_ratio=rest_ratio)}


async def measure(period: str, rest_ratio: float) -> dict[str, Any]:
    """지표마다 추이 도구 메시지 하나의 크기와 추정 토큰, 지원하지 않던 때의 메시지 대비 증가량을 잰다."""
    unsupported = _sized(BEFORE_REPLY, rest_ratio)
    metrics = {}
    for metric in METRICS:
        [content] = await tool_contents([("get_daily_trend", {"metric": metric, "period": period})])
        sized = _sized(content, rest_ratio)
        metrics[metric] = {**sized, "points": len(json.loads(content)["result"]["points"])}
        metrics[metric]["increase"] = sized["tokens"] - unsupported["tokens"]
    pair = ("oxygen_saturation", "sleep")
    span = resolve_period(period, rq.TODAY)
    return {
        "period": period,
        "start": span.start.isoformat(),
        "end": span.end.isoformat(),
        "now": rq.NOW.isoformat(),
        "data_sha256": rq.data_sha256(rq.synthetic_health_data())[:12],
        "rest_ratio": rest_ratio,
        "not_supported": unsupported,
        "metrics": metrics,
        "date_question": {
            "metrics": list(pair),
            "tokens": sum(metrics[m]["tokens"] for m in pair),
            "increase": sum(metrics[m]["increase"] for m in pair),
        },
    }


def _before_shape(record: dict[str, Any]) -> str | None:
    """고치기 전 실행 구조이면 그 기간을, 아니면 None 을 반환한다."""
    args = [c["args"] if isinstance(c["args"], dict) else {} for c in record["tool_calls"]]
    periods = {a.get("period") for a in args}
    shape = [(c["name"], c["round"], a.get("metric")) for c, a in zip(record["tool_calls"], args, strict=True)]
    if record["llm_calls"] != 3 or len(periods) != 1 or sorted(shape, key=str) != sorted(_BEFORE_SHAPE, key=str):
        return None
    return periods.pop()


async def calibrate(paths: Sequence[Path]) -> dict[str, Any]:
    """고치기 전 측정 기록의 실제 입력 토큰으로 rest_ratio 를 맞춘다.

    같은 문항·조건에서 기간만 다른 실행은 첫 호출이 같다. 입력 토큰 합의 차이는 셋째 호출에 들어간 둘째 왕복 도구
    결과(산소포화도 요약, 수면 기록)와 도구 호출 인자의 기간 문자열 PERIOD_REPEATS 번의 차이다. 숫자를 1토큰으로 빼고
    남은 토큰 차이를 숫자 밖 글자 수 차이로 나눈다. 둘째 왕복 도구 결과는 지금 코드로 다시 만든다.
    """
    tokens: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            period = _before_shape(record)
            if period in PERIODS:
                tokens[(record["question"], record["condition"])][period].append(record["input_tokens"])
    sizes: dict[str, dict[str, int]] = {}
    for period in sorted({p for by_period in tokens.values() for p in by_period}):
        summary, sleep = await tool_contents(
            [
                ("get_metric_summary", {"metric": "oxygen_saturation", "period": period}),
                ("get_sleep_records", {"period": period}),
            ]
        )
        sizes[period] = composition(summary + sleep + PERIOD_REPEATS * period)
    pairs = []
    for (question, condition), by_period in sorted(tokens.items()):
        medians = {p: statistics.median(v) for p, v in by_period.items()}
        for a, b in combinations(sorted(medians, key=lambda p: sizes[p]["chars"]), 2):
            d_digits = sizes[b]["digits"] - sizes[a]["digits"]
            d_others = sizes[b]["others"] - sizes[a]["others"]
            if d_others < _MIN_OTHERS_DIFF:
                continue
            d_tokens = medians[b] - medians[a]
            pairs.append(
                {
                    "question": question,
                    "condition": condition,
                    "longer": b,
                    "shorter": a,
                    "token_diff": d_tokens,
                    "digit_diff": d_digits,
                    "other_diff": d_others,
                    "ratio": round((d_tokens - d_digits) / d_others, 3),
                }
            )
    if not pairs:
        raise SystemExit("비율을 맞출 실행 쌍이 없습니다 (고치기 전 실행 구조이고 기간만 다른 실행이 필요합니다)")
    return {
        "records": [p.as_posix() for p in paths],
        "pairs": pairs,
        "rest_ratio": round(statistics.median(p["ratio"] for p in pairs), 3),
    }


def _changed_conditions(report: dict[str, Any], previous: dict[str, Any]) -> list[str]:
    """직전 측정과 다른 측정 조건. 기간·rest_ratio·비율 맞추기 여부가 모두 같아야 추정 토큰을 비교할 수 있다."""

    def conditions(r: dict[str, Any]) -> dict[str, str]:
        return {
            "기간": f"`{r['period']}` ({r['start']}~{r['end']})",
            "rest_ratio": str(r["rest_ratio"]),
            "비율 맞추기": "안 함" if r.get("calibration") is None else "함",
        }

    old, new = conditions(previous), conditions(report)
    return [f"{name} {old[name]} → {new[name]}" for name in new if old[name] != new[name]]


def _comparison(report: dict[str, Any], previous: dict[str, Any]) -> list[str]:
    def row(name: str, old: int, new: int) -> str:
        return f"| {name} | {old:,} | {new:,} | {new - old:+,} |"

    dq, old_dq = report["date_question"], previous["date_question"]
    return [
        f"이전 측정({previous['measured_at']})과 같은 방법으로 잰 추정 토큰이다.",
        "",
        "| 결과 | 이전 추정 토큰 | 이번 추정 토큰 | 차이 |",
        "| --- | --- | --- | --- |",
        row("지원하지 않던 때 (not_supported)", previous["not_supported"]["tokens"], report["not_supported"]["tokens"]),
        *(
            row(f"get_daily_trend({metric})", previous["metrics"][metric]["tokens"], m["tokens"])
            for metric, m in report["metrics"].items()
            if metric in previous["metrics"]
        ),
        row(f"두 지표 추이 ({' · '.join(dq['metrics'])})", old_dq["tokens"], dq["tokens"]),
        row("두 지표 증가량 (지원하지 않던 때 대비)", old_dq["increase"], dq["increase"]),
    ]


def to_markdown(report: dict[str, Any], *, level: int = 1, previous: dict[str, Any] | None = None) -> str:
    """측정 하나의 기록. level 2 는 같은 날 기록 뒤에 붙이는 형식이다.

    previous 가 있으면 기간·rest_ratio·비율 맞추기 여부가 같을 때만 비교 표를 넣고, 다르면 다른 조건을 표 위에 적는다.
    """
    head, sub = "#" * level, "#" * (level + 1)
    title = "일별 추이 조회 입력량" if level == 1 else "다시 잰 값"
    ns = report["not_supported"]
    dq = report["date_question"]
    changed = [] if previous is None else _changed_conditions(report, previous)
    lines = [
        f"{head} {title} ({report['measured_at']})",
        "",
        *([f"- 메모: {report['note']}"] if report.get("note") else []),
        f"- 커밋 `{report['commit']}` · 실행 명령 `{report['command']}`",
        f"- 기간 `{report['period']}` ({report['start']}~{report['end']}) · 기준 시각 {report['now']} · "
        f"합성 데이터 sha256 {report['data_sha256']} (eval.repeated_question 과 같은 데이터)",
        f"- 도구 스키마 sha256 {report['tools_sha256']} · LLM 호출 없음",
        f"- 토큰은 추정이다. 숫자 한 글자 1토큰 + 나머지 글자 × {report['rest_ratio']}",
        *(
            [
                f"- 직전 측정({previous['measured_at']})과 조건이 달라 비교 표를 넣지 않는다. "
                f"다른 조건(직전 → 이번): {' · '.join(changed)}"
            ]
            if previous is not None and changed
            else []
        ),
        "",
        f"{sub} 도구 메시지 하나",
        "",
        "| 결과 | 날짜 수 | 글자 | 숫자 | 나머지 | 추정 토큰 | 지원하지 않던 때 대비 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        f"| 지원하지 않던 때 (not_supported) | - | {ns['chars']:,} | {ns['digits']:,} | {ns['others']:,} | "
        f"{ns['tokens']:,} | - |",
        *(
            f"| get_daily_trend({metric}) | {m['points']} | {m['chars']:,} | {m['digits']:,} | {m['others']:,} | "
            f"{m['tokens']:,} | +{m['increase']:,} |"
            for metric, m in report["metrics"].items()
        ),
        "",
        f"날짜 지정 질문처럼 {' · '.join(dq['metrics'])} 추이를 한 왕복에 함께 조회하면 "
        f"도구 메시지가 약 {dq['tokens']:,}토큰이고, 지원하지 않던 때보다 약 {dq['increase']:,}토큰 늘어난다. "
        "도구 메시지는 그 뒤 같은 요청의 LLM 호출마다 다시 들어간다.",
    ]
    calibration = report["calibration"]
    if calibration is not None:
        lines += [
            "",
            f"{sub} 비율 맞추기",
            "",
            "고치기 전 측정에서 기간만 다른 실행 쌍의 입력 토큰 차이로 맞춘다. "
            "둘째 왕복 도구 결과(산소포화도 요약, 수면 기록)와 "
            f"도구 호출 인자의 기간 문자열 {PERIOD_REPEATS}번이 차이의 전부다.",
            "",
            f"- 기록: {', '.join(f'`{p}`' for p in calibration['records'])}",
            "",
            "| 문항 | 조건 | 기간 | 토큰 차이 | 숫자 차이 | 나머지 글자 차이 | 비율 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            *(
                f"| {p['question']} | {p['condition']} | {p['longer']} − {p['shorter']} | {p['token_diff']:g} | "
                f"{p['digit_diff']} | {p['other_diff']} | {p['ratio']} |"
                for p in calibration["pairs"]
            ),
            "",
            f"중앙값 {calibration['rest_ratio']} 를 쓴다.",
        ]
    if previous is not None and not changed:
        lines += ["", f"{sub} 이전 측정과 비교", "", *_comparison(report, previous)]
    return "\n".join(lines) + "\n"


def save_doc(report: dict[str, Any], doc_dir: Path) -> tuple[Path, Path]:
    """측정을 docs 기록에 저장한다. 같은 날 기록이 있으면 이전 측정은 그대로 두고 뒤에 붙인다."""
    stem = f"{report['measured_at'][:10]}-daily-trend-tokens"
    md_path, json_path = doc_dir / f"{stem}.md", doc_dir / f"{stem}.json"
    if md_path.exists() != json_path.exists():
        raise SystemExit(f"같은 날 기록 파일이 하나만 있습니다: {md_path if md_path.exists() else json_path}")
    measurements: list[dict[str, Any]] = []
    markdown = to_markdown(report)
    if json_path.exists():
        saved = json.loads(json_path.read_text(encoding="utf-8"))
        # 측정 목록으로 저장하기 전 기록은 측정 하나를 dict 로 담는다
        measurements = saved if isinstance(saved, list) else [saved]
        added = to_markdown(report, level=2, previous=measurements[-1])
        markdown = md_path.read_text(encoding="utf-8") + "\n" + added
    doc_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps([*measurements, report], ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return md_path, json_path


def _command(argv: Sequence[str]) -> str:
    args = [a if a and not any(ch.isspace() for ch in a) else f'"{a}"' for a in argv]
    return " ".join(["python -m bench.trend_tokens", *args])


def _commit() -> str | None:
    """HEAD 커밋. 추적 중인 파일에 커밋하지 않은 변경이 있으면 측정 코드가 HEAD 와 다르다고 표시한다."""
    head = git_commit()
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return head
    return f"{head} (커밋 전 변경 포함)" if head and status.stdout.strip() else head


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="일별 추이 조회가 LLM 입력에 더하는 양을 LLM 호출 없이 잰다")
    p.add_argument("--period", choices=PERIODS, default=DEFAULT_PERIOD, help=f"추이 기간 (기본 {DEFAULT_PERIOD})")
    p.add_argument(
        "--calibrate",
        nargs="+",
        type=Path,
        metavar="JSONL",
        help="고치기 전 측정의 실행별 기록으로 비율을 다시 맞춘다 (eval/results/*_records.jsonl)",
    )
    p.add_argument(
        "--save-doc", action="store_true", help="docs/benchmarks/<날짜>-daily-trend-tokens.md·json 으로 저장"
    )
    p.add_argument("--doc-dir", type=Path, default=DOCS_DIR, help="--save-doc 저장 폴더 (기본 docs/benchmarks/)")
    p.add_argument("--note", help="기록에 함께 적을 한 줄 메모 (예: 무엇을 바꾼 뒤 잰 값인지)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    started = datetime.now()

    async def run() -> dict[str, Any]:
        calibration = None if args.calibrate is None else await calibrate(args.calibrate)
        ratio = REST_RATIO if calibration is None else calibration["rest_ratio"]
        return {"calibration": calibration, **await measure(args.period, ratio)}

    measured = asyncio.run(run())
    specs = json.dumps(tool_specs(), ensure_ascii=False, sort_keys=True)
    report = {
        "measured_at": started.isoformat(timespec="seconds"),
        "commit": _commit(),
        "command": _command(argv),
        "note": args.note,
        "tools_sha256": hashlib.sha256(specs.encode("utf-8")).hexdigest()[:12],
        **measured,
    }
    print(to_markdown(report))
    if args.save_doc:
        md_path, json_path = save_doc(report, args.doc_dir)
        print(f"기록: {md_path}\n기록 json: {json_path}")


if __name__ == "__main__":
    main()
