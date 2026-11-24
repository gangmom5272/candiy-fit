"""코칭 골든셋 문항을 운영과 같은 run_coach 경로로 실행하고 결과를 저장한다.

ai/ 디렉터리에서 실행한다.
    uv run python -m eval.golden --mock                          # 모의 응답으로 v1.1 개선용 문항 (사내 LLM 호출 없음)
    uv run python -m eval.golden --set all --mock --save         # v1.1·v2.1 개선용 모의 실행, eval/results/ 에 저장
    uv run python -m eval.golden --only golden-synth-01-001 --mock
    uv run python -m eval.golden --repeat 3 --save               # 실제 측정 (LLM_API_KEY 필요)
    uv run python -m eval.golden --set v1.1 --split all --repeat 3 --save    # 마일스톤 측정 (개선용·확인용 모두)
    uv run python -m eval.golden --rescore eval/results/<측정>_records.jsonl   # LLM 없이 다시 채점
    uv run python -m eval.golden --check-answers --set all --save            # LLM 없이 정답 검사만

- --set 의 기본은 v1.1 이고 all 은 최신 버전 v1.1·v2.1 이다. 이전 버전(v1, v2)도 --set 으로 고를 수 있다.
- --split 은 개선용(dev, 기본)·확인용(holdout)·전체(all) 중 실행할 문항이다 (split.json). 개선 작업은 dev 로 하고,
  all 과 holdout 은 마일스톤 측정에서만 쓴다. 나누기가 없는 이전 버전은 --split all 로만 실행한다.
  확인용 문항은 진행 줄과 보고서에 문항별 답변·판정·실패 이유를 넣지 않고 집계만 넣는다.
- 문항 페르소나의 고정 데이터를 메모리 어댑터에 넣고 기준 시각은 README 의 2026-10-22 21:00(KST)로 고정한다.
  시스템 프롬프트의 날짜 자리에는 이 시각의 날짜가 들어간다.
- 문항마다 질문 하나를 실행해 답변, 도구 호출과 도구 결과, 근거, LLM 호출 기록을 실행별 기록에 담는다.
- 이전 대화(history)가 있는 v2 문항은 history 쌍을 CoachContext.turns 로 넣어 실행한다 (items.history_turns).
- 반복은 문항 전체를 한 번 실행한 뒤 다음 회차로 간다.
- 실행이 끝날 때마다 scoring.score_record 로 채점해 기록의 score 에 넣는다. 채점 규칙은 scoring.SCORING_RULES 다.
- --save 이면 실행이 끝날 때마다 eval/results/ 의 jsonl 에 한 줄을 추가하고, 모두 끝나면 집계 json·md 를 저장한다.
  이 폴더는 git 에 올라가지 않는다. 답변 원문은 실행별 기록과 md 에만 들어간다.
- --rescore 는 저장한 실행별 기록을 측정 때 버전의 문항 파일과 지금 채점 규칙으로 다시 채점해 같은 형식의
  기록·집계를 새로 저장한다. 측정 조건은 같은 측정의 집계 json 에서 읽고, 다시 채점한 시각·커밋·바뀐 판정을
  rescores 에 추가한다. 확인용 문항은 바뀐 판정의 수만 적는다.
- 측정·다시 채점 보고서에는 정답 검사(answer_check) 결과를 함께 넣어, 정답 검사에서 걸린 문항과 모델 답과 정답이
  다른 문항을 나눠 적는다. --check-answers 는 LLM 없이 정답 검사만 실행한다.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
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
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from bench.probe_llm import git_commit
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.executor import ToolCall, tool_specs
from coach_ai.agent.graph import CoachResult, run_coach
from coach_ai.agent.prompts import DEFAULT_PROMPT_VERSION, TODAY_SLOT, load_system_prompt
from coach_ai.config import Settings, get_settings
from coach_ai.llm.client import CoachChatModel, create_chat_model, make_http_client
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import CALL_LIMIT_MESSAGE, DENIED_MESSAGE
from coach_ai.security.tracing import TracingEnabledError, ensure_tracing_disabled
from coach_ai.tools.schemas import TOOL_INPUTS
from eval.compare import paper_differences, warning_line
from eval.golden import answer_check
from eval.golden import report as golden_report
from eval.golden.fixtures import (
    NOW,
    Fixture,
    FixtureError,
    compare_with_truth,
    files_sha256,
    fixture_paths,
    load_fixtures,
    load_truth,
    truth_paths,
)
from eval.golden.items import (
    EVAL_DIR,
    LATEST,
    VERSIONS,
    GoldenFormatError,
    GoldenItem,
    Version,
    definition_sha256,
    history_turns,
    load_sets,
)
from eval.golden.mock import MockLLM, mock_http_client, mock_settings
from eval.golden.scoring import ScoreContext, score_record
from eval.golden.split import SPLIT_LABELS, SPLITS, load_split, membership, split_sha256
from eval.repeated_question import added_lookups, calls_signature
from eval.tool_selection import RequestLog

RESULTS_DIR = EVAL_DIR / "results"
SET_CHOICES = (*LATEST, "all", *(v for v in VERSIONS if v not in LATEST))
# LLM 호출 계측 기록(LLMCallRecord)에서 실행별 기록으로 옮기는 값
LLM_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "ttft_s",
    "total_s",
    "queue_wait_s",
    "attempts",
    "finish_reason",
    "error_code",
)
GOLDEN_SOURCE_DIR = Path(__file__).resolve().parent
_RECORDS_SUFFIX = "_records.jsonl"


def versions_of(name: str) -> tuple[Version, ...]:
    """--set 값이 가리키는 문항 버전. all 은 최신 버전들이다."""
    return LATEST if name == "all" else tuple(v for v in VERSIONS if v == name)


def select_items(items: Sequence[GoldenItem], only: Sequence[str] | None) -> list[GoldenItem]:
    """--only 로 받은 문항 ID 만 파일 순서대로 고른다. 고른 버전에 없는 ID 가 있으면 ValueError."""
    if not only:
        return list(items)
    known = {item.id for item in items}
    if unknown := [i for i in only if i not in known]:
        raise ValueError(f"고른 버전에 없는 문항 ID: {', '.join(unknown)}")
    wanted = set(only)
    return [item for item in items if item.id in wanted]


def item_sides(
    items: Sequence[GoldenItem], datasets: Sequence[dict[str, Any]], split: dict[str, Any]
) -> dict[str, str | None]:
    """문항 키 → 개선용(dev)·확인용(holdout). 나누기가 없는 버전(v1, v2)의 문항은 None 이다.

    문항 파일이 나눌 때와 다르면 split.membership 이 ValueError 를 발생시킨다.
    """
    sides: dict[str, str | None] = {}
    for info in datasets:
        members = membership(split, info["version"], info["sha256"])
        for item in items:
            if item.version == info["version"]:
                sides[item.key] = None if members is None else members.get(item.id)
    return sides


def select_split(
    items: Sequence[GoldenItem], sides: dict[str, str | None], split: str, only: Sequence[str] | None
) -> list[GoldenItem]:
    """--split 에 맞는 문항을 고른다. dev·holdout 이면 나누기가 없는 버전과 --only 로 고른 다른 쪽 문항을 거부한다."""
    if split == "all":
        return list(items)
    if missing := sorted({item.version for item in items if sides[item.key] is None}):
        raise ValueError(f"{', '.join(missing)} 에는 개선용·확인용 나누기가 없다. --split all 로 실행한다")
    if only and (other := [item.id for item in items if sides[item.key] != split]):
        hint = " 확인용 문항은 --split holdout 이나 all 로만 실행한다" if split == "dev" else ""
        raise ValueError(
            f"고른 나누기(--split {split}, {SPLIT_LABELS[split]})에 없는 문항 ID: {', '.join(other)}.{hint}"
        )
    return [item for item in items if sides[item.key] == split]


def split_header(
    split: dict[str, Any], name: str, items: Sequence[GoldenItem], sides: dict[str, str | None]
) -> dict[str, Any] | None:
    """측정 결과의 나누기 정보. 고른 문항에 나누기가 있는 버전이 없으면 None 이다."""
    counts: dict[str, dict[str, int]] = {}
    for item in items:
        if (side := sides.get(item.key)) is not None:
            counts.setdefault(item.version, {"dev": 0, "holdout": 0})[side] += 1
    if not counts:
        return None
    return {"name": name, "seed": split["seed"], "sha256": split_sha256(split)[:12], "counts": counts}


def reply_kind(content: Any) -> str:
    """도구 메시지 본문의 종류. ok, 처리기 오류 이름(not_supported 등), 게이트웨이 거부(denied), unknown 중 하나다."""
    if content in (DENIED_MESSAGE, CALL_LIMIT_MESSAGE):
        return "denied"
    try:
        body = json.loads(content)
    except (TypeError, ValueError):
        return "unknown"
    if not isinstance(body, dict):
        return "unknown"
    return "ok" if "result" in body else str(body.get("error", "unknown"))


def _parsed(content: Any) -> Any:
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return content


def tool_exchanges(result: CoachResult, audit: MemoryAuditSink) -> list[dict[str, Any]]:
    """LLM 응답의 도구 호출을 왕복 순서로 정리하고, 호출마다 받은 도구 메시지 본문(result)과 종류(reply)를 붙인다.

    args 는 모델이 낸 인자, executed_args 는 에이전트가 실행한 인자다. 에이전트가 인자를 바꿔 실행한 호출은
    args_changed 가 참이고, 바꾸지 않은 호출은 두 인자가 같다. 처리 함수가 인자의 기간 밖 기간까지 계산한 호출은
    extra_period 에 그 이름과 시작·끝 날짜가 있고, 다른 호출은 그 칸이 없다.
    에이전트가 더해 함께 실행한 호출은 그 왕복의 호출 뒤에 added 가 참인 호출로 넣는다. 모델이 낸 인자가 없어 args 에도
    실행한 인자를 넣고, result 는 이어 붙인 결과 본문 가운데 그 호출의 몫이다. 더한 호출이 아니면 added 칸이 없다.
    오류로 끝난 실행은 결과에 메시지가 없어 게이트웨이 호출 기록에서 이름과 실행한 인자만 가져온다. 모델이 낸 인자를
    알 수 없어 args 에도 실행한 인자를 넣고 args_changed·round·reply·result 는 None 이다.
    """
    if not result.messages:
        return [
            {
                "round": None,
                "name": r.tool_name,
                "args": r.args,
                "executed_args": r.args,
                "args_changed": None,
                "reply": None,
                "result": None,
            }
            for r in audit.records()
        ]
    calls: list[dict[str, Any]] = []
    pending: list[tuple[str, dict[str, Any]]] = []
    rounds = 0
    for message in result.messages:
        if isinstance(message, AIMessage) and (message.tool_calls or message.invalid_tool_calls):
            rounds += 1
            pending = []
            # 도구 메시지의 tool_call_id 는 tools_node 가 정한 ID 다 (없으면 도구 이름, 해석 실패면 "invalid")
            for call in message.tool_calls:
                entry = {"round": rounds, "name": call["name"], "args": call["args"], "reply": None, "result": None}
                pending.append(
                    (call["id"] or call["name"], {**entry, "executed_args": call["args"], "args_changed": False})
                )
            for bad in message.invalid_tool_calls:
                entry = {"round": rounds, "name": bad.get("name") or "", "args": bad.get("args"), "reply": None}
                changed = {"executed_args": bad.get("args"), "args_changed": False}
                pending.append((bad.get("id") or "invalid", {**entry, "result": None, **changed}))
            calls += [entry for _, entry in pending]
        elif isinstance(message, ToolMessage):
            index = next((i for i, (call_id, _) in enumerate(pending) if call_id == message.tool_call_id), None)
            if index is not None:
                entry = pending.pop(index)[1]
                entry["reply"] = reply_kind(message.content)
                entry["result"] = _parsed(message.content)
                # tools_node 는 인자를 바꿔 실행한 호출에 실행한 호출(ToolCall)을, 호출을 더했으면 실행한 호출과 더한
                # 호출(executed, added)을, 처리 함수가 인자 밖 기간까지 계산했으면 그 기간(extra_period)도
                # artifact 로 넣는다
                artifact = message.artifact
                ran = artifact if isinstance(artifact, ToolCall) else getattr(artifact, "executed", None)
                if ran is not None:
                    executed = None if ran.args is None else dict(ran.args)
                    entry["executed_args"] = executed
                    entry["args_changed"] = executed != entry["args"]
                extra = getattr(artifact, "extra_period", None)
                if extra is not None:
                    entry["extra_period"] = {
                        "name": extra.name,
                        "start": extra.period.start.isoformat(),
                        "end": extra.period.end.isoformat(),
                    }
                calls += [
                    {
                        "round": rounds,
                        "name": a["name"],
                        "args": a["args"],
                        "reply": a["reply"],
                        "result": a["result"],
                        "executed_args": a["args"],
                        "args_changed": False,
                        "added": True,
                    }
                    for a in added_lookups(message)
                ]
    return calls


def _evidence(result: CoachResult) -> list[dict[str, Any]]:
    # 공통 원형(snapshot)은 고정 데이터로 다시 만들 수 있어 넣지 않는다
    return [
        {
            "ref": e.ref,
            "tool": e.tool,
            "metric": e.metric,
            "period": e.period.model_dump(mode="json"),
            "summary": e.summary,
        }
        for e in result.evidence
    ]


def _llm_calls(recorder: MetricsRecorder, request_id: str) -> list[dict[str, Any]]:
    return [
        {name: getattr(r, name) for name in LLM_FIELDS} for r in recorder.records("llm") if r.request_id == request_id
    ]


def _base_record(item: GoldenItem, run: int, order: int, request_id: str, split: str | None) -> dict[str, Any]:
    return {
        "version": item.version,
        "id": item.id,
        "definition_sha256": definition_sha256(item),
        # 개선용(dev)·확인용(holdout). 나누기가 없는 버전은 None 이다
        "split": split,
        "persona": item.persona,
        "category": item.category,
        "subtype": item.subtype,
        "run": run,
        "order": order,
        "request_id": request_id,
        "question": item.question,
    }


async def run_once(
    item: GoldenItem,
    run: int,
    order: int,
    *,
    fixture: Fixture,
    model: BaseChatModel,
    recorder: MetricsRecorder,
    prompt_version: str,
    split: str | None = None,
) -> dict[str, Any]:
    """문항 하나를 한 번 실행하고 실행별 기록 한 줄을 반환한다. 어댑터와 호출 기록기는 실행마다 새로 만든다."""
    request_id = f"golden-{item.version}-{item.id}-{run}"
    ctx = CoachContext(
        user_id=fixture.user_id,
        conversation_id=f"golden-{item.id}",
        request_id=request_id,
        turns=history_turns(item),
    )
    audit = MemoryAuditSink()
    started = time.perf_counter()
    result = await run_coach(
        ctx,
        item.question,
        port=fixture.port(),
        model=model,
        sink=ListSink(),
        now=NOW,
        message_id=f"{request_id}-answer",
        audit=audit,
        prompt_version=prompt_version,
    )
    elapsed = time.perf_counter() - started
    calls = tool_exchanges(result, audit)
    llm = _llm_calls(recorder, request_id)
    return {
        **_base_record(item, run, order, request_id, split),
        "executed": True,
        "skip_reason": None,
        "answer": result.answer,
        "finish_reason": result.finish_reason,
        "error_code": result.error_code,
        "exception": None if result.exception is None else type(result.exception).__name__,
        "blocked_by": result.blocked_by,
        "tool_rounds": result.tool_rounds,
        # 앞 답의 건강 수치를 도구 없이 다시 써 되돌린 횟수와 효과 단정 끝맺음을 바꾼 문장 수.
        # 두 칸이 없는 이전 앱 코드는 그 기능이 없어 0 으로 기록한다
        "answer_rechecks": getattr(result, "answer_rechecks", 0),
        # 앞 답에도 질문에도 없는 건강 수치를 도구 없이 말해 되돌린 횟수. 이 칸이 없는 옛 앱 코드는 0 으로 기록한다
        "unsupported_rechecks": getattr(result, "unsupported_rechecks", 0),
        # 운동이 잠에 준 효과를 물은 질문에 도구 없이 답해 되돌린 횟수. 이 칸이 없는 옛 앱 코드는 0 으로 기록한다
        "exercise_rechecks": getattr(result, "exercise_rechecks", 0),
        "hedged_sentences": getattr(result, "hedged_sentences", 0),
        # 답 글의 내부 표시를 정리한 응답 수. 이 칸이 없는 이전 앱 코드는 0 으로 기록한다
        "marker_cleanups": getattr(result, "marker_cleanups", 0),
        "history_turns": len(item.history),
        # 마지막 LLM 호출에 넣은 이전 대화의 양. LLM 을 부르지 않았으면 None 이다
        "history_usage": None if result.history_usage is None else dataclasses.asdict(result.history_usage),
        "tool_calls": calls,
        "calls_signature": calls_signature(calls),
        "evidence": _evidence(result),
        "llm_calls": llm,
        "input_tokens": sum(c["input_tokens"] or 0 for c in llm),
        "output_tokens": sum(c["output_tokens"] or 0 for c in llm),
        "elapsed_s": round(elapsed, 3),
    }


def _progress_line(record: dict[str, Any], total: int) -> str:
    if record.get("split") == "holdout":
        # 확인용 문항은 실행 중에도 문항 ID 와 판정을 보이지 않는다
        return f"[{record['order']}/{total}] 확인용 문항 {record['run']}회 · {record['elapsed_s']:.1f}초"
    if record["skip_reason"]:
        state = f"건너뜀({record['skip_reason']})"
    elif record["error_code"]:
        state = f"오류 {record['error_code']}"
    elif record["blocked_by"]:
        state = f"경계 {record['blocked_by']}"
    else:
        state = f"왕복 {record['tool_rounds']}"
    if score := record.get("score"):
        state += f" · {score['verdict']}"
    return f"[{record['order']}/{total}] {record['id']} {record['run']}회 · {state} · {record['elapsed_s']:.1f}초"


def _append(f: TextIO, record: dict[str, Any]) -> None:
    f.write(json.dumps(record, ensure_ascii=False) + "\n")
    f.flush()


async def run_items(
    items: Sequence[GoldenItem],
    fixtures: dict[str, Fixture],
    *,
    repeat: int,
    model: BaseChatModel,
    recorder: MetricsRecorder,
    prompt_version: str,
    score: Callable[[GoldenItem, dict[str, Any]], dict[str, Any]] | None = None,
    on_record: Callable[[dict[str, Any]], None] | None = None,
    progress: Callable[[str], None] = print,
    sides: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """문항마다 repeat 회 실행한 기록을 회차·문항 순서로 반환한다.

    score 가 있으면 실행마다 채점해 기록의 score 에 넣고, 기록이 생길 때마다 on_record 를 부른다.
    sides 는 문항 키 → 개선용·확인용이다 (item_sides).
    """
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
                split=(sides or {}).get(item.key),
            )
            if score is not None:
                record["score"] = score(item, record)
            records.append(record)
            if on_record is not None:
                on_record(record)
            progress(_progress_line(record, total))
    return records


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(EVAL_DIR.parent).as_posix()
    except ValueError:
        return path.name


def load_truths(personas: Sequence[str]) -> dict[str, dict[str, dict[str, Any]]]:
    return {persona: load_truth(persona) for persona in sorted(set(personas))}


def score_context(truths: dict[str, dict[str, dict[str, Any]]]) -> ScoreContext:
    return ScoreContext(truth=truths, tools=frozenset(TOOL_INPUTS), today=NOW.date())


def scoring_sha256() -> str:
    """채점 코드(scoring.py), 표현 목록(phrases.py), 답변 마크다운 읽기(answer_markdown.py)의 sha256.

    다시 채점한 결과가 어느 규칙인지 구분한다.
    """
    sources = [GOLDEN_SOURCE_DIR / "scoring.py", GOLDEN_SOURCE_DIR / "phrases.py"]
    return files_sha256([*sources, GOLDEN_SOURCE_DIR.parent / "answer_markdown.py"])


async def data_check(
    personas: Sequence[str], fixtures: dict[str, Fixture], truths: dict[str, dict[str, dict[str, Any]]]
) -> dict[str, Any]:
    """고정 데이터·정답 원형 파일의 sha256 과, 고정 데이터로 조회한 원형이 정답 원형과 같은지 확인한 결과."""
    diffs: list[dict[str, Any]] = []
    compared = 0
    for persona in sorted(set(personas)):
        compared += len(truths[persona])
        diffs += await compare_with_truth(fixtures[persona], truths[persona])
    return {
        "personas": sorted(set(personas)),
        "fixtures_sha256": files_sha256(fixture_paths(personas)),
        "truth_sha256": files_sha256(truth_paths(personas)),
        "data_sha256": {p: fixtures[p].data_sha256 for p in sorted(set(personas))},
        "truth_compared": compared,
        "truth_diffs": diffs,
    }


def build_model(
    settings: Settings,
    temperature: float | None,
    recorder: MetricsRecorder,
    log: RequestLog,
    http_client: httpx.AsyncClient,
) -> CoachChatModel:
    """운영과 같은 create_chat_model 로 만든다. temperature 는 주었을 때만 요청에 넣는다 (생략하면 서버 기본값)."""
    http_client.event_hooks["request"].append(log)
    overrides: dict[str, Any] = {} if temperature is None else {"temperature": temperature}
    return create_chat_model(settings, recorder=recorder, http_client=http_client, **overrides)


def measurement_header(
    args: argparse.Namespace,
    settings: Settings,
    *,
    command: str,
    template: str,
    datasets: Sequence[dict[str, Any]],
    items: Sequence[GoldenItem],
    data: dict[str, Any],
    split: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """실행 기록 머리말. 측정 조건(커밋, 모델, 프롬프트, 도구 스키마, 데이터셋, 나누기, 기준 시각)을 담는다."""
    selected = {info["version"]: sum(item.version == info["version"] for item in items) for info in datasets}
    return {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "commit": git_commit(),
        "command": command,
        "mock": args.mock,
        "model": settings.llm_model,
        "base_url_host": httpx.URL(settings.llm_base_url).host,
        "prompt_version": args.prompt_version,
        "prompt_sha256": _sha256(template)[:12],
        # 날짜 자리가 없는 템플릿(v1, v1.1)이면 None 이다
        "prompt_date": NOW.date().isoformat() if TODAY_SLOT in template else None,
        "tools_sha256": _sha256(json.dumps(tool_specs(), ensure_ascii=False, sort_keys=True))[:12],
        "now": NOW.isoformat(),
        "scoring_sha256": scoring_sha256()[:12],
        "set": args.set,
        "only": args.only,
        "split": split,
        "repeat": args.repeat,
        "datasets": [{**info, "selected": selected[info["version"]]} for info in datasets],
        "data": data,
    }


async def run_measurement(
    args: argparse.Namespace,
    settings: Settings,
    items: Sequence[GoldenItem],
    datasets: Sequence[dict[str, Any]],
    fixtures: dict[str, Fixture],
    *,
    command: str,
    http_client: httpx.AsyncClient,
    records_path: Path | None,
    progress: Callable[[str], None] = print,
    sides: dict[str, str | None] | None = None,
    split: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """문항을 실행하고 (보고서, 실행별 기록)을 반환한다. records_path 가 있으면 실행마다 한 줄씩 추가한다.

    sides 는 문항 키 → 개선용·확인용, split 은 측정 결과에 기록할 나누기 정보(split_header)다.
    정답 검사는 확인용이 아닌 문항만 한다. 확인용 문항만 실행하면 정답 검사가 없다.
    """
    sides = sides or {}
    template = load_system_prompt(args.prompt_version)
    truths = load_truths([item.persona for item in items])
    data = await data_check([item.persona for item in items], fixtures, truths)
    visible = [item for item in items if sides.get(item.key) != "holdout"]
    check = await answer_check.check_answers(visible, fixtures, NOW.date()) if visible else None
    ctx = score_context(truths)
    header = measurement_header(
        args, settings, command=command, template=template, datasets=datasets, items=items, data=data, split=split
    )
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
                score=lambda item, record: score_record(item, record, ctx),
                on_record=on_record,
                progress=progress,
                sides=sides,
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
        "records_file": None if records_path is None else _display_path(records_path),
        "answer_check": check,
        "summary": golden_report.summarize(records),
    }
    return report, records


def write_report(report: dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(golden_report.to_markdown(report), encoding="utf-8")


def load_measurement(records_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """실행별 기록과 같은 측정의 집계 json 을 읽어 (집계, 실행별 기록)을 반환한다.

    측정 조건(커밋, 모델, 프롬프트, 문항 파일 등)은 실행별 기록에 없어 집계 json 에서 가져온다.
    두 파일이 같은 측정이 아니면 멈춘다.
    """
    if not records_path.name.endswith(_RECORDS_SUFFIX):
        raise SystemExit(f"실행별 기록 파일(*{_RECORDS_SUFFIX})이 아닙니다: {records_path}")
    report_path = records_path.with_name(records_path.name.removesuffix(_RECORDS_SUFFIX) + ".json")
    if not report_path.exists():
        raise SystemExit(f"같은 측정의 집계 파일이 없습니다: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if Path(report.get("records_file") or "").name != records_path.name:
        raise SystemExit(f"집계 파일과 실행별 기록이 같은 측정이 아닙니다: {report_path}")
    if len(records) != report["summary"]["runs"]:
        raise SystemExit(f"실행별 기록 수({len(records)})가 측정 실행 수({report['summary']['runs']})와 다릅니다")
    return report, records


def rescore_records(
    records: Sequence[dict[str, Any]], items: dict[str, GoldenItem], ctx: ScoreContext
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """실행별 기록을 지금 문항·채점 규칙으로 다시 채점한다. (다시 채점한 기록, 판정이 바뀐 실행)을 반환한다.

    다시 채점한 기록의 문항 정의 sha256 은 채점에 쓴 지금 문항의 값이다.
    """
    rescored: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for record in records:
        item = items[f"{record['version']}/{record['id']}"]
        score = score_record(item, record, ctx)
        rescored.append({**record, "definition_sha256": definition_sha256(item), "score": score})
        before = (record.get("score") or {}).get("verdict")
        if before != score["verdict"]:
            where = {k: record[k] for k in ("version", "id", "run")}
            changes.append({**where, "before": before, "after": score["verdict"]})
    return rescored, changes


def run_rescore(args: argparse.Namespace, started: datetime, command: str) -> None:
    """--rescore 로 받은 실행별 기록을 다시 채점해 실행별 기록·집계 json·md 를 새 이름으로 저장한다.

    문항은 측정 때 버전(집계 json 의 datasets)의 파일을 지금 읽는다. --set all 이 가리키는 버전이 바뀌어도 측정 때
    버전으로 채점한다. 기록의 문항이 없어졌거나 질문이 바뀌었으면 답변을 다시 채점할 수 없어 멈춘다.
    지금 문항 파일·고정 데이터·정답 원형의 sha256 이 측정 때와 다르면 다시 채점은 하고 결과 맨 위에 경고를 넣는다.
    """
    source, records = load_measurement(args.rescore)
    try:
        items, datasets = load_sets([d["version"] for d in source["datasets"]])
    except (GoldenFormatError, ValueError) as e:
        raise SystemExit(str(e)) from None
    by_key = {item.key: item for item in items}
    for record in records:
        item = by_key.get(f"{record['version']}/{record['id']}")
        if item is None or item.question != record["question"]:
            raise SystemExit(f"{record['id']}: 문항이 없거나 질문이 측정 때와 달라 다시 채점할 수 없습니다")
    personas = [record["persona"] for record in records]
    try:
        truths = load_truths(personas)
        fixtures = load_fixtures(personas)
    except FixtureError as e:
        raise SystemExit(str(e)) from None
    # 정답 검사는 확인용이 아닌 문항만 한다 (측정 때와 같다)
    visible = [r for r in records if r.get("split") != "holdout"]
    measured = list({f"{r['version']}/{r['id']}": by_key[f"{r['version']}/{r['id']}"] for r in visible}.values())
    check = asyncio.run(answer_check.check_answers(measured, fixtures, NOW.date())) if measured else None
    rescored, changes = rescore_records(records, by_key, score_context(truths))
    # 지금 시험지. 문항 선택(selected)과 나누기는 실행별 기록에 정해진 측정 때 값이라 그대로 둔다
    current = {
        "datasets": [{**old, **new} for old, new in zip(source["datasets"], datasets, strict=True)],
        "data": {
            "fixtures_sha256": files_sha256(fixture_paths(personas)),
            "truth_sha256": files_sha256(truth_paths(personas)),
        },
        "split": source.get("split"),
    }
    paper_changes = paper_differences(source, current)
    # 확인용 문항은 문항별 판정을 적지 않고 바뀐 판정의 수만 센다
    held = {(r["version"], r["id"]) for r in records if r.get("split") == "holdout"}
    shown = [c for c in changes if (c["version"], c["id"]) not in held]
    name = f"{started:%Y%m%d-%H%M%S}_golden_{source['set']}" + ("_mock" if source["mock"] else "") + "_rescored"
    records_path = args.out / f"{name}{_RECORDS_SUFFIX}"
    records_path.parent.mkdir(parents=True, exist_ok=True)
    with records_path.open("x", encoding="utf-8") as f:
        f.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in rescored)
    entry = {
        "rescored_at": started.isoformat(timespec="seconds"),
        "commit": git_commit(),
        "command": command,
        "source_records": _display_path(args.rescore),
        "scoring_sha256": scoring_sha256()[:12],
        "datasets": [{"name": d["name"], "sha256": d["sha256"]} for d in datasets],
        "paper_changes": paper_changes,
        "paper_warning": warning_line(source, current, paper_changes, ("측정", "지금")) if paper_changes else None,
        "changed": len(changes),
        "holdout_changed": len(changes) - len(shown),
        "changes": shown,
    }
    report = {
        **source,
        "records_file": _display_path(records_path),
        "answer_check": check,
        "summary": golden_report.summarize(rescored),
        "rescores": [*source.get("rescores", []), entry],
    }
    print("\n" + golden_report.to_markdown(report))
    json_path, md_path = args.out / f"{name}.json", args.out / f"{name}.md"
    write_report(report, json_path, md_path)
    print(f"실행별 기록: {records_path}\n집계: {json_path}\n요약: {md_path}")


def run_answer_check(args: argparse.Namespace, started: datetime, command: str) -> None:
    """--check-answers: LLM 없이 고정 데이터만으로 정답을 다시 계산해 문항 정답과 대조한다.

    모델 답변을 쓰지 않아 개선용·확인용을 나누지 않고 고른 버전의 모든 문항을 검사한다.
    """
    try:
        all_items, datasets = load_sets(versions_of(args.set))
        items = select_items(all_items, args.only)
        fixtures = load_fixtures(item.persona for item in items)
    except (GoldenFormatError, FixtureError, ValueError) as e:
        raise SystemExit(str(e)) from None
    selected = {info["version"]: sum(item.version == info["version"] for item in items) for info in datasets}
    report = {
        "measured_at": started.isoformat(timespec="seconds"),
        "commit": git_commit(),
        "command": command,
        "now": NOW.isoformat(),
        "set": args.set,
        "only": args.only,
        "datasets": [{**info, "selected": selected[info["version"]]} for info in datasets],
        "fixtures_sha256": files_sha256(fixture_paths([item.persona for item in items])),
        "answer_check": asyncio.run(answer_check.check_answers(items, fixtures, NOW.date())),
    }
    markdown = answer_check.to_markdown(report)
    print("\n" + markdown)
    if not args.save:
        print("(저장하지 않음. 저장하려면 --save)")
        return
    name = f"{started:%Y%m%d-%H%M%S}_golden_{args.set}_answer_check"
    json_path, md_path = args.out / f"{name}.json", args.out / f"{name}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    print(f"정답 검사: {json_path}\n요약: {md_path}")


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
    return " ".join(["python -m eval.golden", *args])


# 측정 조건 옵션과 기본값. --rescore 는 측정 때 값을 집계 json 에서 읽으므로 이 옵션을 받지 않는다
_MEASURE_OPTIONS: dict[str, Any] = {
    "set": LATEST[0],
    "split": "dev",
    "repeat": 1,
    "only": None,
    "temperature": None,
    "prompt_version": DEFAULT_PROMPT_VERSION,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="코칭 골든셋 문항을 run_coach 로 실행해 답변을 저장하고 자동 채점한다")
    p.add_argument(
        "--set",
        choices=SET_CHOICES,
        help=f"실행할 문항 파일 버전 (기본 {LATEST[0]}, all 은 {'·'.join(LATEST)}). 이전 버전도 고를 수 있다",
    )
    p.add_argument(
        "--split",
        choices=SPLITS,
        help="실행할 문항: dev 개선용(기본), holdout 확인용, all 모두. all·holdout 은 마일스톤 측정에서만 쓴다",
    )
    p.add_argument("--repeat", type=_positive, help="문항마다 반복 횟수 (기본 1)")
    p.add_argument("--only", nargs="+", metavar="ID", help="이 문항 ID 만 실행 (여러 개 가능)")
    p.add_argument("--mock", action="store_true", help="사내 LLM 대신 모의 응답으로 실행 (.env 를 읽지 않음)")
    p.add_argument("--save", action="store_true", help="--out 폴더에 실행별 jsonl·집계 json·md 저장 (git 제외)")
    p.add_argument("--out", type=Path, default=RESULTS_DIR, help="결과 저장 폴더 (기본 eval/results/)")
    p.add_argument("--temperature", type=_temperature, help="생략하면 요청에 넣지 않아 서버 기본값을 쓴다")
    p.add_argument(
        "--prompt-version",
        help=f"시스템 프롬프트 버전 (agent/prompts/system_<버전>.md, 기본 {DEFAULT_PROMPT_VERSION})",
    )
    p.add_argument(
        "--rescore",
        type=Path,
        metavar="JSONL",
        help="저장한 실행별 기록(*_records.jsonl)을 LLM 호출 없이 지금 채점 규칙으로 다시 채점해 --out 에 저장한다",
    )
    p.add_argument(
        "--check-answers",
        action="store_true",
        help="LLM 없이 고정 데이터만으로 정답을 다시 계산해 문항 정답과 대조한다 (--set, --only, --save 와 함께 쓴다)",
    )
    args = p.parse_args(argv)
    if args.check_answers:
        options = ("repeat", "temperature", "prompt_version", "rescore", "split")
        given = next((n for n in options if getattr(args, n)), None)
        if given is None and args.mock:
            given = "mock"
        if given is not None:
            p.error(f"--check-answers 는 LLM 을 부르지 않아 --{given.replace('_', '-')} 를 함께 쓰지 않는다")
        args.set = args.set or _MEASURE_OPTIONS["set"]
        return args
    if args.rescore is not None:
        given = next((name for name in _MEASURE_OPTIONS if getattr(args, name) is not None), None)
        if given is None and args.mock:
            given = "mock"
        if given is not None:
            p.error(f"--rescore 는 측정 때 값을 쓰므로 --{given.replace('_', '-')} 를 함께 쓰지 않는다")
        return args
    for name, default in _MEASURE_OPTIONS.items():
        if getattr(args, name) is None:
            setattr(args, name, default)
    return args


def output_name(started: datetime, args: argparse.Namespace) -> str:
    return f"{started:%Y%m%d-%H%M%S}_golden_{args.set}" + ("_mock" if args.mock else "")


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    started = datetime.now()
    if args.rescore is not None:
        run_rescore(args, started, _command(argv))
        return
    if args.check_answers:
        run_answer_check(args, started, _command(argv))
        return
    try:
        load_system_prompt(args.prompt_version)
    except FileNotFoundError:
        raise SystemExit(f"프롬프트 {args.prompt_version!r} 가 없습니다 (agent/prompts/system_<버전>.md)") from None
    try:
        all_items, datasets = load_sets(versions_of(args.set))
        split = load_split()
        sides = item_sides(all_items, datasets, split)
        items = select_split(select_items(all_items, args.only), sides, args.split, args.only)
        fixtures = load_fixtures(item.persona for item in items)
    except (GoldenFormatError, FixtureError, ValueError) as e:
        raise SystemExit(str(e)) from None
    try:
        ensure_tracing_disabled()
    except TracingEnabledError as e:
        raise SystemExit(f"LangSmith 추적이 켜져 있어 실행하지 않습니다: {', '.join(e.sources)}") from None
    if args.mock:
        settings = mock_settings()
        http_client = mock_http_client(MockLLM(items))
    else:
        try:
            settings = get_settings()
        except ValidationError:
            raise SystemExit("LLM_API_KEY가 없습니다. LLM_API_KEY를 넣은 뒤 다시 실행하세요.") from None
        http_client = make_http_client(settings)
    name = output_name(started, args)
    records_path = args.out / f"{name}{_RECORDS_SUFFIX}" if args.save else None
    report, _ = asyncio.run(
        run_measurement(
            args,
            settings,
            items,
            datasets,
            fixtures,
            command=_command(argv),
            http_client=http_client,
            records_path=records_path,
            sides=sides,
            split=split_header(split, args.split, items, sides),
        )
    )
    print("\n" + golden_report.to_markdown(report))
    if records_path is None:
        print("(저장하지 않음. 저장하려면 --save)")
        return
    json_path, md_path = args.out / f"{name}.json", args.out / f"{name}.md"
    write_report(report, json_path, md_path)
    print(f"실행별 기록: {records_path}\n집계: {json_path}\n요약: {md_path}")
