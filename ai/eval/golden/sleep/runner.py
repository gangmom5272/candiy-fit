"""수면 코칭 골든셋 문항을 운영과 같은 run_coach 경로로 실행하고 자동 채점한다.

ai/ 디렉터리에서 실행한다.
    uv run python -m eval.golden.sleep --mock                          # 모의 응답으로 개선용 문항 (사내 LLM 호출 없음)
    uv run python -m eval.golden.sleep --mock --save                   # eval/results/ 에 기록·집계·요약·평가표 저장
    uv run python -m eval.golden.sleep --only sleep1-01 sleep1-04 --mock
    uv run python -m eval.golden.sleep --repeat 3 --save               # 개선 작업 측정 (개선용, 문항마다 3회)
    uv run python -m eval.golden.sleep --split all --repeat 3 --save   # 마일스톤 측정 (개선용·확인용 모두)

- 문항은 등록(split.json)한 개선용·확인용 파일에서 읽는다. 파일이 등록할 때와 다르면 실행하지 않고 멈춘다.
  형식이 잘못된 문항은 문항 id 와 이유를 알리고 그 문항만 건너뛴다.
- 문항 페르소나의 고정 데이터를 메모리 어댑터에 넣고 기준 시각은 2026-10-22 21:00(KST)다. 시스템 프롬프트의 날짜도
  이날이다.
- history 가 있는 문항은 앞 대화를 CoachContext.turns 로 넣고 마지막 질문만 실행한다. 경계 확인은 운영처럼 이번 질문과
  바로 앞 사용자 질문을 함께 검사한다.
- 고정 데이터에 생활 습관 점수 행이 없으면 점수 문항(items.needs_scores)은 실행하지 않고 "점수 행 없음"으로 센다.
- 실행마다 첫 왕복 호출, 모든 호출과 도구 결과, 최종 답, 끝난 이유, 첫 답변 글자까지 걸린 시간을 기록하고 채점한다.
- 반복은 문항 전체를 한 번 실행한 뒤 다음 회차로 간다.
- 확인용 문항은 진행 줄과 결과 json·md 에 수와 판정만 넣는다. 실행별 기록에도 질문·답·호출을 넣지 않는다.
- --save 이면 --out 폴더(기본 eval/results/, git 제외)에 실행별 기록(jsonl), 집계(json), 요약(md), 사람 평가표
  (_rubric.csv, 개선용만)를 저장한다.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import dataclasses
import functools
import hashlib
import json
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import httpx
from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from bench.probe_llm import git_commit
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import tool_specs
from coach_ai.agent.graph import run_coach
from coach_ai.agent.prompts import DEFAULT_PROMPT_VERSION, TODAY_SLOT, load_system_prompt
from coach_ai.config import Settings, get_settings
from coach_ai.llm.client import make_http_client
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.tracing import TracingEnabledError, ensure_tracing_disabled
from coach_ai.tools.schemas import TOOL_INPUTS
from eval.golden.fixtures import NOW, Fixture, FixtureError, compare_with_truth, files_sha256, load_fixtures, load_truth
from eval.golden.mock import mock_http_client, mock_settings
from eval.golden.runner import LLM_FIELDS, RESULTS_DIR, build_model, tool_exchanges
from eval.golden.sleep import report as sleep_report
from eval.golden.sleep.items import (
    LATEST,
    SETS,
    SIDE_LABELS,
    SIDES,
    SPLITS,
    Skipped,
    SleepItem,
    display_path,
    history_turns,
    needs_scores,
    path_of,
    read_items,
    row_ids,
)
from eval.golden.sleep.mock import SleepMockLLM
from eval.golden.sleep.scoring import ScoreContext, analysis_in_schema, score_record, score_view
from eval.golden.sleep.split import SPLIT_FILE, check_file, load_split, split_sha256
from eval.repeated_question import calls_signature
from eval.tool_selection import RequestLog

SOURCE_DIR = Path(__file__).resolve().parent
_RECORDS_SUFFIX = "_records.jsonl"
# 확인용 실행별 기록의 칸. 질문·답·호출·도구 결과는 넣지 않고 판정과 수만 둔다
HOLDOUT_FIELDS = frozenset(
    {
        "version",
        "id",
        "split",
        "pair",
        "run",
        "order",
        "executed",
        "skip_reason",
        "finish_reason",
        "error_code",
        "blocked_by",
        "tool_rounds",
        "answer_rechecks",
        "unsupported_rechecks",
        "exercise_rechecks",
        "hedged_sentences",
        "marker_cleanups",
        "llm_call_count",
        "first_token_s",
        "elapsed_s",
        "score",
    }
)


class TimingSink:
    """이벤트를 모으고 첫 답변 글자(token)가 나온 시각을 started 부터의 초로 기록한다."""

    def __init__(self, started: float) -> None:
        self.started = started
        self.events: list[dict[str, Any]] = []
        self.first_token_s: float | None = None

    async def emit(self, event: dict[str, Any]) -> None:
        if self.first_token_s is None and event.get("type") == "token" and event.get("delta"):
            self.first_token_s = time.perf_counter() - self.started
        self.events.append(event)


def _llm_calls(recorder: MetricsRecorder, request_id: str) -> list[dict[str, Any]]:
    return [
        {name: getattr(r, name) for name in LLM_FIELDS} for r in recorder.records("llm") if r.request_id == request_id
    ]


def _base_record(item: SleepItem, run: int, order: int, request_id: str) -> dict[str, Any]:
    return {
        "version": item.version,
        "id": item.id,
        "split": item.side,
        "pair": item.pair,
        "persona": item.persona,
        "category": item.category,
        "subtype": item.subtype,
        "run": run,
        "order": order,
        "request_id": request_id,
        "question": item.question,
        "history_turns": len(item.history),
    }


def _skipped_record(item: SleepItem, run: int, order: int, request_id: str, reason: str) -> dict[str, Any]:
    return {
        **_base_record(item, run, order, request_id),
        "executed": False,
        "skip_reason": reason,
        "answer": None,
        "finish_reason": None,
        "error_code": None,
        "exception": None,
        "blocked_by": None,
        "tool_rounds": 0,
        "answer_rechecks": 0,
        "unsupported_rechecks": 0,
        "exercise_rechecks": 0,
        "hedged_sentences": 0,
        "marker_cleanups": 0,
        "first_round_calls": [],
        "tool_calls": [],
        "calls_signature": None,
        "history_usage": None,
        "llm_calls": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "first_token_s": None,
        "elapsed_s": 0.0,
    }


async def run_once(
    item: SleepItem,
    run: int,
    order: int,
    *,
    fixture: Fixture,
    model: BaseChatModel,
    recorder: MetricsRecorder,
    prompt_version: str,
) -> dict[str, Any]:
    """문항 하나를 한 번 실행하고 실행별 기록 한 줄을 반환한다. 어댑터와 호출 기록기는 실행마다 새로 만든다.

    고정 데이터에 점수 행이 없으면 점수 문항은 실행하지 않고 skip_reason 이 no_scores 인 기록을 반환한다.
    """
    request_id = f"sleep-golden-{item.version}-{item.id}-{run}"
    if needs_scores(item) and not fixture.scores:
        return _skipped_record(item, run, order, request_id, "no_scores")
    ctx = CoachContext(
        user_id=fixture.user_id,
        conversation_id=f"sleep-golden-{item.id}",
        request_id=request_id,
        turns=history_turns(item),
    )
    audit = MemoryAuditSink()
    started = time.perf_counter()
    sink = TimingSink(started)
    result = await run_coach(
        ctx,
        item.question,
        port=fixture.port(),
        model=model,
        sink=sink,
        now=NOW,
        message_id=f"{request_id}-answer",
        audit=audit,
        prompt_version=prompt_version,
    )
    elapsed = time.perf_counter() - started
    calls = tool_exchanges(result, audit)
    llm = _llm_calls(recorder, request_id)
    return {
        **_base_record(item, run, order, request_id),
        "executed": True,
        "skip_reason": None,
        "answer": result.answer,
        "finish_reason": result.finish_reason,
        "error_code": result.error_code,
        "exception": None if result.exception is None else type(result.exception).__name__,
        "blocked_by": result.blocked_by,
        "tool_rounds": result.tool_rounds,
        # 앞 답의 건강 수치를 도구 없이 다시 써 되돌린 횟수. 되돌린 답은 결과 메시지에 없어 first_round_calls 는
        # 다시 받은 응답의 호출이다. 두 칸이 없는 이전 앱 코드는 그 기능이 없어 0 으로 기록한다
        "answer_rechecks": getattr(result, "answer_rechecks", 0),
        # 앞 답에도 질문에도 없는 건강 수치를 도구 없이 말해 되돌린 횟수. first_round_calls 는 위와 같이 다시 받은
        # 응답의 호출이고, 이 칸이 없는 옛 앱 코드는 0 으로 기록한다
        "unsupported_rechecks": getattr(result, "unsupported_rechecks", 0),
        # 운동이 잠에 준 효과를 물은 질문에 도구 없이 답해 되돌린 횟수. first_round_calls 는 위와 같이 다시 받은
        # 응답의 호출이고, 이 칸이 없는 옛 앱 코드는 0 으로 기록한다
        "exercise_rechecks": getattr(result, "exercise_rechecks", 0),
        "hedged_sentences": getattr(result, "hedged_sentences", 0),
        # 답 글의 내부 표시를 정리한 응답 수. 이 칸이 없는 이전 앱 코드는 0 으로 기록한다
        "marker_cleanups": getattr(result, "marker_cleanups", 0),
        "first_round_calls": [
            {key: c[key] for key in ("name", "args", "executed_args", "args_changed", "added") if key in c}
            for c in calls
            if c["round"] == 1
        ],
        "tool_calls": calls,
        "calls_signature": calls_signature(calls),
        # 마지막 LLM 호출에 넣은 이전 대화의 양. LLM 을 부르지 않았으면 None 이다
        "history_usage": None if result.history_usage is None else dataclasses.asdict(result.history_usage),
        "llm_calls": llm,
        "input_tokens": sum(c["input_tokens"] or 0 for c in llm),
        "output_tokens": sum(c["output_tokens"] or 0 for c in llm),
        "first_token_s": None if sink.first_token_s is None else round(sink.first_token_s, 3),
        "elapsed_s": round(elapsed, 3),
    }


def stored(record: dict[str, Any]) -> dict[str, Any]:
    """실행별 기록 파일에 넣는 한 줄. 확인용 문항은 질문·답·호출·도구 결과를 빼고 판정과 수만 둔다."""
    if record["split"] != "holdout":
        return record
    kept = {name: record[name] for name in HOLDOUT_FIELDS if name in record}
    kept["llm_call_count"] = len(record.get("llm_calls") or [])
    kept["score"] = score_view(record["score"])
    return kept


def _progress_line(record: dict[str, Any], total: int) -> str:
    if record["split"] == "holdout":
        # 확인용 문항은 실행 중에도 문항 ID 와 판정을 보이지 않는다
        return f"[{record['order']}/{total}] 확인용 문항 {record['run']}회 · {record['elapsed_s']:.1f}초"
    if not record["executed"]:
        state = f"건너뜀({sleep_report.SKIP_LABELS.get(record['skip_reason'], record['skip_reason'])})"
    elif record["error_code"]:
        state = f"오류 {record['error_code']}"
    elif record["blocked_by"]:
        state = f"경계 {record['blocked_by']}"
    else:
        state = f"왕복 {record['tool_rounds']}"
    verdict = record["score"]["verdict"]
    if verdict not in ("skipped", "error"):
        state += f" · {sleep_report.VERDICT_LABELS[verdict]}"
    return f"[{record['order']}/{total}] {record['id']} {record['run']}회 · {state} · {record['elapsed_s']:.1f}초"


async def run_items(
    items: Sequence[SleepItem],
    fixtures: dict[str, Fixture],
    *,
    repeat: int,
    model: BaseChatModel,
    recorder: MetricsRecorder,
    prompt_version: str,
    ctx: ScoreContext,
    on_record: Callable[[dict[str, Any]], None] | None = None,
    progress: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """문항마다 repeat 회 실행해 채점한 기록을 회차·문항 순서로 반환한다. 기록이 생길 때마다 on_record 를 부른다."""
    total = len(items) * repeat
    records: list[dict[str, Any]] = []
    for run in range(1, repeat + 1):
        for item in items:
            record = await run_once(
                item,
                run,
                len(records) + 1,
                fixture=fixtures[item.persona],
                model=model,
                recorder=recorder,
                prompt_version=prompt_version,
            )
            record["score"] = score_record(item, record, ctx)
            records.append(record)
            if on_record is not None:
                on_record(stored(record))
            progress(_progress_line(record, total))
    return records


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scoring_sha256() -> str:
    """채점 코드의 sha256. 수면 채점, 첫 호출 채점(도구 선택), 숫자 읽기(골든 채점·표현 목록·마크다운 읽기)를 묶는다."""
    eval_dir = SOURCE_DIR.parents[1]
    sources = [
        SOURCE_DIR / "scoring.py",
        eval_dir / "tool_selection.py",
        eval_dir / "golden" / "scoring.py",
        eval_dir / "golden" / "phrases.py",
        eval_dir / "answer_markdown.py",
    ]
    return files_sha256(sources)


async def fixture_check(fixtures: dict[str, Fixture]) -> dict[str, dict[str, Any]]:
    """고정 데이터마다 data_sha256, 점수 행 수, 고정 데이터로 조회한 원형이 정답 원형과 같은지."""
    out = {}
    for persona, fixture in sorted(fixtures.items()):
        truth = load_truth(persona)
        out[persona] = {
            "data_sha256": fixture.data_sha256,
            "scores": len(fixture.scores),
            "truth_compared": len(truth),
            "truth_diffs": await compare_with_truth(fixture, truth),
        }
    return out


def measurement_header(
    args: argparse.Namespace,
    settings: Settings,
    *,
    command: str,
    template: str,
    datasets: Sequence[dict[str, Any]],
    items: Sequence[SleepItem],
    registration: dict[str, Any] | None,
    skipped: Sequence[Skipped],
) -> dict[str, Any]:
    """실행 기록 머리말. 측정 조건(커밋, 모델, 프롬프트, 도구 스키마, 데이터셋, 등록, 기준 시각)을 담는다."""
    selected = {(d["version"], d["side"]): 0 for d in datasets}
    for item in items:
        selected[(item.version, item.side)] = selected.get((item.version, item.side), 0) + 1
    return {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "commit": git_commit(),
        "command": command,
        "mock": args.mock,
        "model": settings.llm_model,
        "base_url_host": httpx.URL(settings.llm_base_url).host,
        "prompt_version": args.prompt_version,
        "prompt_sha256": _sha256(template)[:12],
        "prompt_date": NOW.date().isoformat() if TODAY_SLOT in template else None,
        "tools_sha256": _sha256(json.dumps(tool_specs(), ensure_ascii=False, sort_keys=True))[:12],
        "analysis_in_schema": analysis_in_schema(TOOL_INPUTS),
        "now": NOW.isoformat(),
        "scoring_sha256": scoring_sha256()[:12],
        "set": args.set,
        "split": args.split,
        "only": args.only,
        "repeat": args.repeat,
        "datasets": [{**d, "selected": selected[(d["version"], d["side"])]} for d in datasets],
        "registration": registration,
        # 형식이 잘못돼 건너뛴 문항. 이유에는 칸 이름과 형식만 있고 문항의 값은 없다
        "format_problems": [{"id": s.id, "side": s.side, "reasons": list(s.reasons)} for s in skipped],
    }


async def run_measurement(
    args: argparse.Namespace,
    settings: Settings,
    items: Sequence[SleepItem],
    datasets: Sequence[dict[str, Any]],
    fixtures: dict[str, Fixture],
    *,
    command: str,
    http_client: httpx.AsyncClient,
    records_path: Path | None,
    progress: Callable[[str], None] = print,
    skipped: Sequence[Skipped] = (),
    registration: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """문항을 실행하고 (보고서, 실행별 기록)을 반환한다. records_path 가 있으면 실행마다 한 줄씩 추가한다.

    반환하는 실행별 기록은 파일에 넣는 모양(stored)이라 확인용 문항은 판정과 수만 있다.
    """
    template = load_system_prompt(args.prompt_version)
    header = measurement_header(
        args,
        settings,
        command=command,
        template=template,
        datasets=datasets,
        items=items,
        registration=registration,
        skipped=skipped,
    )
    header["fixtures"] = await fixture_check({p: fixtures[p] for p in sorted({item.persona for item in items})})
    recorder = MetricsRecorder(max_records=max(1000, len(items) * args.repeat * 5))
    log = RequestLog()
    model = build_model(settings, args.temperature, recorder, log, http_client)
    started = time.perf_counter()
    with contextlib.ExitStack() as stack:
        on_record = None
        if records_path is not None:
            records_path.parent.mkdir(parents=True, exist_ok=True)
            # 이미 있는 파일에 이어 쓰면 다른 측정의 기록과 섞이므로 새 파일만 만든다
            on_record = functools.partial(_append, stack.enter_context(records_path.open("x", encoding="utf-8")))
        try:
            records = await run_items(
                items,
                fixtures,
                repeat=args.repeat,
                model=model,
                recorder=recorder,
                prompt_version=args.prompt_version,
                ctx=ScoreContext(today=NOW.date()),
                on_record=on_record,
                progress=progress,
            )
        finally:
            await http_client.aclose()
    params = log.params[0] if log.params else None
    report = {
        **header,
        # 실제 요청에 들어간 값. 요청에 없으면 서버 기본값을 쓴 것이다 (LLM 을 부르지 않았으면 둘 다 None)
        "temperature": None if params is None else params.get("temperature"),
        "temperature_source": None if params is None else ("request" if "temperature" in params else "server_default"),
        "request_params": log.params,
        "elapsed_s": round(time.perf_counter() - started, 1),
        "records_file": None if records_path is None else display_path(records_path),
        "summary": sleep_report.summarize(records, items),
    }
    return report, [stored(r) for r in records]


def _append(f: TextIO, record: dict[str, Any]) -> None:
    f.write(json.dumps(record, ensure_ascii=False) + "\n")
    f.flush()


def write_outputs(
    report: dict[str, Any], records: Sequence[dict[str, Any]], items: Sequence[SleepItem], out: Path, name: str
) -> list[Path]:
    """집계 json, 요약 md, 사람 평가표 csv(개선용 실행이 있을 때만)를 저장하고 저장한 경로를 반환한다."""
    out.mkdir(parents=True, exist_ok=True)
    rows = sleep_report.rubric_rows(records, {item.key: item for item in items})
    paths = []
    if rows:
        csv_path = out / f"{name}_rubric.csv"
        # 표 계산 프로그램이 한글을 읽도록 BOM 을 붙인다
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=sleep_report.RUBRIC_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        report = {**report, "rubric_file": display_path(csv_path), "rubric_rows": len(rows)}
        paths.append(csv_path)
    json_path, md_path = out / f"{name}.json", out / f"{name}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(sleep_report.to_markdown(report), encoding="utf-8")
    return [json_path, md_path, *paths]


def load_selected(
    args: argparse.Namespace,
) -> tuple[list[SleepItem], list[Skipped], list[dict[str, Any]], dict[str, Any]]:
    """--set·--split·--only 로 고른 문항, 건너뛴 문항, 파일 정보, 등록 정보를 반환한다.

    파일이 등록할 때와 다르거나 --only 의 ID 가 등록에 없거나 고른 쪽에 없으면 ValueError 를 발생시킨다.
    """
    split = load_split()
    registered = split["sets"].get(args.set)
    if registered is None:
        raise ValueError(f"수면 골든셋 {args.set} 이 등록되지 않았다")
    sides = SIDES if args.split == "all" else (args.split,)
    if args.only:
        known = {item_id: side for side in SIDES for item_id in registered[side]["ids"]}
        if unknown := [i for i in args.only if i not in known]:
            raise ValueError(f"등록한 문항에 없는 ID: {', '.join(unknown)}")
        if other := [i for i in args.only if known[i] not in sides]:
            hint = " 확인용 문항은 --split holdout 이나 all 로만 실행한다" if args.split == "dev" else ""
            label = SIDE_LABELS.get(args.split, args.split)
            raise ValueError(f"고른 나누기(--split {args.split}, {label})에 없는 문항 ID: {', '.join(other)}.{hint}")
    items: list[SleepItem] = []
    skipped: list[Skipped] = []
    datasets: list[dict[str, Any]] = []
    dev_ids = row_ids(path_of(args.set, "dev"))
    for side in sides:
        loaded, problems, info = read_items(path_of(args.set, side), args.set, side, dev_ids=dev_ids)
        check_file(split, args.set, side, info["sha256"])
        items += loaded
        skipped += problems
        datasets.append(info)
    if args.only:
        wanted = set(args.only)
        items = [item for item in items if item.id in wanted]
        skipped = [s for s in skipped if s.id in wanted]
    registration = {"path": display_path(SPLIT_FILE), "sha256": split_sha256(split)}
    return items, skipped, datasets, registration


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("1 이상이어야 한다")
    return value


def _temperature(text: str) -> float:
    value = float(text)
    if not 0 <= value <= 2:
        raise argparse.ArgumentTypeError("0~2 사이여야 한다")
    return value


def _command(argv: Sequence[str]) -> str:
    args = [a if a and not any(ch.isspace() for ch in a) else f'"{a}"' for a in argv]
    return " ".join(["python -m eval.golden.sleep", *args])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="수면 코칭 골든셋 문항을 run_coach 로 실행해 답을 저장하고 자동 채점한다")
    p.add_argument("--set", choices=tuple(SETS), default=LATEST, help=f"문항 버전 (기본 {LATEST})")
    p.add_argument(
        "--split",
        choices=SPLITS,
        default="dev",
        help="실행할 문항: dev 개선용(기본), holdout 확인용, all 모두. all·holdout 은 마일스톤 측정에서만 쓴다",
    )
    p.add_argument("--repeat", type=_positive, default=1, help="문항마다 반복 횟수 (기본 1)")
    p.add_argument("--only", nargs="+", metavar="ID", help="이 문항 ID 만 실행 (여러 개 가능)")
    p.add_argument("--mock", action="store_true", help="사내 LLM 대신 모의 응답으로 실행 (.env 를 읽지 않음)")
    p.add_argument("--save", action="store_true", help="--out 폴더에 실행별 기록·집계·요약·사람 평가표 저장 (git 제외)")
    p.add_argument("--out", type=Path, default=RESULTS_DIR, help="결과 저장 폴더 (기본 eval/results/)")
    p.add_argument("--temperature", type=_temperature, help="생략하면 요청에 넣지 않아 서버 기본값을 쓴다")
    p.add_argument(
        "--prompt-version",
        default=DEFAULT_PROMPT_VERSION,
        help=f"시스템 프롬프트 버전 (agent/prompts/system_<버전>.md, 기본 {DEFAULT_PROMPT_VERSION})",
    )
    return p.parse_args(argv)


def output_name(started: datetime, args: argparse.Namespace) -> str:
    return f"{started:%Y%m%d-%H%M%S}_sleep_golden_{args.set}_{args.split}" + ("_mock" if args.mock else "")


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    started = datetime.now()
    try:
        load_system_prompt(args.prompt_version)
    except FileNotFoundError:
        raise SystemExit(f"프롬프트 {args.prompt_version!r} 가 없습니다 (agent/prompts/system_<버전>.md)") from None
    try:
        items, skipped, datasets, registration = load_selected(args)
        fixtures = load_fixtures(item.persona for item in items)
    except (ValueError, FixtureError) as e:
        raise SystemExit(str(e)) from None
    for problem in skipped:
        print(f"형식 오류로 건너뜀: {SIDE_LABELS[problem.side]} {problem.id} ({'; '.join(problem.reasons)})")
    try:
        ensure_tracing_disabled()
    except TracingEnabledError as e:
        raise SystemExit(f"LangSmith 추적이 켜져 있어 실행하지 않습니다: {', '.join(e.sources)}") from None
    if args.mock:
        settings = mock_settings()
        http_client = mock_http_client(SleepMockLLM(items))
    else:
        try:
            settings = get_settings()
        except ValidationError:
            raise SystemExit("LLM_API_KEY가 없습니다. LLM_API_KEY를 넣은 뒤 다시 실행하세요.") from None
        http_client = make_http_client(settings)
    name = output_name(started, args)
    records_path = args.out / f"{name}{_RECORDS_SUFFIX}" if args.save else None
    report, records = asyncio.run(
        run_measurement(
            args,
            settings,
            items,
            datasets,
            fixtures,
            command=_command(argv),
            http_client=http_client,
            records_path=records_path,
            skipped=skipped,
            registration=registration,
        )
    )
    print("\n" + sleep_report.to_markdown(report))
    if records_path is None:
        print("(저장하지 않음. 저장하려면 --save)")
        return
    paths = write_outputs(report, records, items, args.out, name)
    print("\n".join([f"실행별 기록: {records_path}", *(f"저장: {path}" for path in paths)]))
