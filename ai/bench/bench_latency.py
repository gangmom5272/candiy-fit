from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import math
import statistics
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from bench.probe_llm import RESULTS_DIR, WARMUP, fmt, git_commit, pct
from coach_ai.config import Settings, get_settings
from coach_ai.llm.client import CoachChatModel, LLMError, create_chat_model
from coach_ai.llm.metrics import SENSITIVE_HEADERS, LLMCallRecord, MetricsRecorder, response_headers
from coach_ai.tools.schemas import TOOL_INPUTS

CONTEXT_LIMIT = 16_384
LENGTH_TARGETS = [200, 2_000, 8_000]
LENGTH_MAX_TOKENS = 50  # 첫 글자 시간만 보므로 답변은 짧게 끊는다
CACHE_PREFIX_TOKENS = 4_000
CACHE_MAX_TOKENS = 30
# 정렬한 첫 글자 시간에서 이웃한 두 값의 비가 이 이상이면 분포가 두 무리로 갈린다고 적는다
CLUSTER_RATIO = 2.0

# 질문은 요청마다 달라야 해서 주제와 문형을 조합해 만든다. 같은 질문을 반복하면 결과가 캐시 효과와 섞인다
# 질문과 참고 문장은 합성 문장만 쓴다
_TOPICS = [
    "잠드는 시간",
    "하루 걸음 수",
    "안정 시 심박수",
    "수면 중 산소포화도",
    "주말 늦잠",
    "낮잠",
    "저녁 운동",
    "오후 카페인",
    "물 마시기",
    "아침 햇빛",
    "자기 전 휴대폰",
    "출퇴근 걷기",
]
_FORMS = [
    "{} 관련해서 오늘 바로 해볼 만한 습관 하나만 알려줘.",
    "{}, 왜 신경 써야 하는지 짧게 설명해줘.",
    "{} 관리에서 사람들이 흔히 하는 실수는 뭐야?",
    "{} 이야기를 할 때 기억할 점 두 가지만 알려줘.",
]
QUESTIONS = [form.format(topic) for form, topic in itertools.product(_FORMS, _TOPICS)]
# 캐시 항목은 기준선과 다른 순서로 써서 기준선에서 보낸 요청과 앞뒤가 겹치지 않게 한다
CACHE_QUESTIONS = QUESTIONS[::-1]

SERVICE_SYSTEM = (
    "너는 사용자의 웨어러블 건강데이터를 바탕으로 생활 습관을 코칭하는 건강 코치다.\n"
    "- 사용자의 수치가 필요하면 반드시 도구를 호출하고, 도구 결과에 없는 수치는 만들지 않는다.\n"
    "- 진단·처방·약 추천은 하지 않는다. 위급 증상이 의심되면 의료기관 방문을 권한다.\n"
    "- 답변은 한국어 존댓말로 3~5문장, 실천할 수 있는 행동 하나를 포함한다.\n"
    "- 수치를 말할 때는 기간과 단위를 함께 말한다.\n\n"
    "사용할 수 있는 도구:\n"
)
FILLER_UNIT = "규칙적인 취침 시간과 아침 햇빛, 꾸준한 걷기는 회복과 컨디션 유지에 도움이 된다고 알려져 있다. "
LENGTH_QUESTION = "위 참고 자료를 바탕으로 수면 습관 하나를 한 문장으로 추천해줘."


def _tool_text() -> str:
    tools = []
    for name, schema in TOOL_INPUTS.items():
        tool = convert_to_openai_tool(schema)
        tool["function"]["name"] = name
        tools.append(tool)
    return json.dumps(tools, ensure_ascii=False, indent=1)


# 시스템 프롬프트와 도구 설명 분량의 고정 앞부분. 실제 도구 스키마를 펼쳐 두어 스키마가 바뀌면 길이도 따라간다.
# 도구를 tools 로 넘기면 응답이 도구 호출로 와서 첫 글자 시간을 잴 수 없다
SERVICE_PREFIX = SERVICE_SYSTEM + _tool_text()


def cold_tag(seq: int) -> str:
    """요청 번호와 난수로 만든 첫 줄. 서버 캐시가 앞부분을 재사용하지 못하게 한다."""
    return f"[cold {seq:04d}-{uuid.uuid4().hex}]"


def service_messages(question: str) -> list[dict]:
    return [{"role": "system", "content": SERVICE_PREFIX}, {"role": "user", "content": question}]


def cold_messages(question: str, seq: int) -> list[dict]:
    return [
        {"role": "system", "content": f"{cold_tag(seq)}\n{SERVICE_PREFIX}"},
        {"role": "user", "content": question},
    ]


def _filler(repeats: int) -> str:
    return FILLER_UNIT * repeats


def _length_messages(repeats: int, seq: int) -> list[dict]:
    # cold 조건과 같이 첫 줄을 매번 다르게 해 입력 처리 비용 자체를 잰다
    system = f"{cold_tag(seq)}\n참고 자료: {_filler(repeats)}"
    return [{"role": "system", "content": system}, {"role": "user", "content": LENGTH_QUESTION}]


class Bench:
    def __init__(self, model: CoachChatModel, recorder: MetricsRecorder, callbacks: list | None = None) -> None:
        self.model = model
        self.recorder = recorder
        self.callbacks = callbacks or []
        self._seq = 0

    @property
    def next_seq(self) -> int:
        """다음 call 이 쓸 요청 번호. cold 첫 줄에 넣는다."""
        return self._seq + 1

    async def call(self, messages: list[dict], *, node: str, stream: bool, **bind: Any) -> LLMCallRecord | None:
        """한 번 호출하고 그 호출의 계측 기록을 반환한다. 실패한 호출도 기록된다."""
        self._seq += 1
        config = {"metadata": {"request_id": f"bench-{self._seq}", "node": node}, "callbacks": self.callbacks}
        model = self.model.bind(**bind) if bind else self.model
        try:
            if stream:
                async for _ in model.astream(messages, config=config):
                    pass
            else:
                await model.ainvoke(messages, config=config)
        except LLMError as e:
            print(f"  ! {node} 실패: {e.code} ({e.detail})")
        records = [r for r in self.recorder.records("llm") if r.request_id == f"bench-{self._seq}"]
        return records[-1] if records else None


def _ok(records: list[LLMCallRecord | None]) -> list[LLMCallRecord]:
    return [r for r in records if r is not None and r.error_code is None]


# 표에서 앞 몇 자만 보일 헤더. 64자 해시라 표가 넓어지고, 앞 8자로도 백엔드가 구분된다. 원본 기록은 전체 값을 둔다
SHORT_LABEL_HEADERS = {"x-litellm-model-id": 8}


def instance_label(rec: LLMCallRecord, keys: list[str] | None = None) -> str | None:
    """계측 기록의 구분값을 한 문자열로 묶는다. 구분값이 하나도 없으면 None.

    허용 헤더 값이 있으면 그것만 쓴다 (system_fingerprint 는 같은 백엔드에서도 응답마다 있다 없다 함).
    """
    instance = {k: v for k, v in (rec.instance or {}).items() if keys is None or k in keys}
    if instance:
        return ", ".join(f"{k}={v[: SHORT_LABEL_HEADERS.get(k, len(v))]}" for k, v in sorted(instance.items()))
    if rec.system_fingerprint:
        return f"fingerprint={rec.system_fingerprint}"
    return None


def two_clusters(values: list[float]) -> list[list[float]] | None:
    """정렬한 값에서 비가 가장 큰 틈이 CLUSTER_RATIO 이상이면 두 무리의 [최소, 최대] 를 반환한다.

    분포를 서술하는 용도라 무리별 통계는 내지 않는다 (시간만으로는 어느 인스턴스였는지 알 수 없음).
    """
    vals = sorted(values)
    min_side = max(2, math.ceil(len(vals) * 0.1))
    best: tuple[float, int] | None = None
    for i in range(min_side, len(vals) - min_side + 1):
        lo, hi = vals[i - 1], vals[i]
        if lo > 0 and hi / lo >= CLUSTER_RATIO and (best is None or hi / lo > best[0]):
            best = (hi / lo, i)
    if best is None:
        return None
    i = best[1]
    return [[vals[0], vals[i - 1]], [vals[i], vals[-1]]]


def _stats(measured: list[LLMCallRecord]) -> dict:
    ttft = [r.ttft_s for r in measured if r.ttft_s is not None]
    total = [r.total_s for r in measured if r.total_s is not None]
    return {
        "n": len(measured),
        "ttft_p50": pct(ttft, 50),
        "ttft_p95": pct(ttft, 95),
        "total_p50": pct(total, 50),
        "total_p95": pct(total, 95),
    }


NO_LABEL = "(구분값 없음)"


def breakdown(measured: list[LLMCallRecord], keys: list[str] | None = None) -> dict:
    """구분값이 있으면 구분값별 통계를 내고, 구분값이 둘 이상으로 갈리지 않으면 분포가 두 무리인지만 적는다."""
    groups: dict[str, list[LLMCallRecord]] = defaultdict(list)
    for r in measured:
        groups[instance_label(r, keys) or NO_LABEL].append(r)
    labeled = [label for label in groups if label != NO_LABEL]
    result: dict[str, Any] = {"groups": [], "clusters": None}
    if labeled:
        result["groups"] = [{"label": label, **_stats(rs)} for label, rs in sorted(groups.items())]
    if len(labeled) < 2:
        result["clusters"] = two_clusters([r.ttft_s for r in measured if r.ttft_s is not None])
    return result


def _summary(records: list[LLMCallRecord | None], expected: int, keys: list[str] | None = None) -> dict:
    measured = _ok(records)
    ttft = [r.ttft_s for r in measured if r.ttft_s is not None]
    total = [r.total_s for r in measured if r.total_s is not None]
    inputs = [r.input_tokens for r in measured if r.input_tokens is not None]
    cached = [r.cached_tokens for r in measured if r.cached_tokens is not None]
    return {
        "n": len(measured),
        "errors": expected - len(measured),
        "input_tokens": int(statistics.median(inputs)) if inputs else None,
        "ttft_p50": pct(ttft, 50),
        "ttft_p95": pct(ttft, 95),
        "total_p50": pct(total, 50),
        "total_p95": pct(total, 95),
        "ttft_median": statistics.median(ttft) if ttft else None,
        "total_median": statistics.median(total) if total else None,
        "cached_median": statistics.median(cached) if cached else None,
        "wait_p95": pct([r.queue_wait_s or 0.0 for r in measured], 95),
        # 서버가 캐시 적중 토큰을 보고한 응답 수. 인스턴스마다 보고 설정이 다를 수 있다
        "cached_reported": len(cached),
        "breakdown": breakdown(measured, keys),
    }


# 실측과 저장된 기록으로 다시 만들 때가 같은 계산을 쓰도록 결과 조립을 분리한다
def latency_result(service: list, cold: list, n: int, keys: list[str] | None = None) -> dict:
    return {"service": _summary(service, n, keys), "cold": _summary(cold, n, keys)}


def length_row(target: int, records: list, len_n: int, keys: list[str] | None = None) -> dict:
    return {"target": target, **_summary(records, len_n, keys)}


def cache_result(cold: list, warm: list, cache_n: int, keys: list[str] | None = None) -> dict:
    c, w = _summary(cold, cache_n, keys), _summary(warm, cache_n, keys)
    if c["ttft_median"] is None or w["ttft_median"] is None:
        verdict = "측정 실패"
    else:
        verdict = f"같은 접두 반복 시 첫 글자 시간 중앙값 {c['ttft_median']:.3f}초 → {w['ttft_median']:.3f}초"
    cached = "서버 미제공" if w["cached_median"] is None else f"{w['cached_median']:g}"
    return {"cold": c, "shared": w, "cached_tokens": cached, "verdict": verdict}


# ---------------------------------------------------------------------------
# 항목
# ---------------------------------------------------------------------------
async def run_latency(bench: Bench, args: argparse.Namespace) -> dict:
    """서비스형과 cold 를 각각 워밍업 + n 회 잰다. 한 조건 안에서 질문은 겹치지 않는다."""
    questions = QUESTIONS[: WARMUP + args.n]
    records: dict[str, list] = {}
    for cond in ("service", "cold"):
        records[cond] = []
        for q in questions:
            messages = service_messages(q) if cond == "service" else cold_messages(q, bench.next_seq)
            records[cond].append(await bench.call(messages, node=f"bench_latency_{cond}", stream=True))
    result = latency_result(records["service"][WARMUP:], records["cold"][WARMUP:], args.n)
    for cond, row in result.items():
        print(
            f"[latency:{cond}] n={row['n']} 실패={row['errors']} 입력 {row['input_tokens']}토큰 "
            f"TTFT p50={fmt(row['ttft_p50'])} p95={fmt(row['ttft_p95'])}, "
            f"total p50={fmt(row['total_p50'])} p95={fmt(row['total_p95'])}, 대기 p95={fmt(row['wait_p95'])}"
        )
    return result


async def calibrate(bench: Bench) -> tuple[float, float]:
    """참고 문장 한 단위가 몇 토큰인지 잰다. 모델 토크나이저를 로컬에서 쓸 수 없어 서버 usage 로 잰다."""
    base = await bench.call(_length_messages(0, bench.next_seq), node="bench_calibrate", stream=False, max_tokens=1)
    probe = await bench.call(_length_messages(20, bench.next_seq), node="bench_calibrate", stream=False, max_tokens=1)
    if not base or not probe or base.input_tokens is None or probe.input_tokens is None:
        raise RuntimeError("토큰 수 보정 실패: usage 를 받지 못했습니다")
    per_unit = (probe.input_tokens - base.input_tokens) / 20
    print(f"[calibrate] 기본 입력 {base.input_tokens}토큰, 참고 문장 1단위 {per_unit:.1f}토큰")
    return float(base.input_tokens), per_unit


def repeats_for(target: int, base: float, per_unit: float) -> int:
    return max(0, round((target - base) / per_unit))


async def run_lengths(bench: Bench, args: argparse.Namespace, base: float, per_unit: float) -> dict:
    rows = []
    for target in LENGTH_TARGETS:
        if target + LENGTH_MAX_TOKENS > CONTEXT_LIMIT:
            raise ValueError(f"목표 입력 {target}토큰은 컨텍스트 상한 {CONTEXT_LIMIT}토큰을 넘는다")
        repeats = repeats_for(target, base, per_unit)
        records = []
        for _ in range(1 + args.len_n):  # 길이마다 워밍업 1회
            records.append(
                await bench.call(
                    _length_messages(repeats, bench.next_seq),
                    node="bench_lengths",
                    stream=True,
                    max_tokens=LENGTH_MAX_TOKENS,
                )
            )
        row = length_row(target, records[1:], args.len_n)
        rows.append(row)
        print(
            f"[lengths] 목표 {target}토큰 (실제 {row['input_tokens']}) n={row['n']} "
            f"TTFT p50={fmt(row['ttft_p50'])} p95={fmt(row['ttft_p95'])}"
        )
    return {"rows": rows}


async def run_cache(bench: Bench, args: argparse.Namespace, base: float, per_unit: float) -> dict:
    """약 4,000토큰 접두로 cold 와 같은 접두 반복을 번갈아 cache_n 회씩 재서 첫 글자 시간 중앙값을 비교한다.

    번갈아 보내 서버 부하 변화를 두 조건에 고르게 섞는다. 같은 접두 쪽은 캐시를 채우는 첫 호출을 집계에서 뺀다.
    """
    filler = _filler(repeats_for(CACHE_PREFIX_TOKENS, base, per_unit))
    shared = f"[shared {uuid.uuid4().hex}]\n참고 자료: {filler}"

    def shared_messages(q: str) -> list[dict]:
        return [{"role": "system", "content": shared}, {"role": "user", "content": q}]

    def cold_prefix_messages(q: str) -> list[dict]:
        system = f"{cold_tag(bench.next_seq)}\n참고 자료: {filler}"
        return [{"role": "system", "content": system}, {"role": "user", "content": q}]

    opts = {"stream": True, "max_tokens": CACHE_MAX_TOKENS}
    await bench.call(shared_messages(CACHE_QUESTIONS[0]), node="bench_cache_seed", **opts)
    cold, warm = [], []
    for q in CACHE_QUESTIONS[1 : 1 + args.cache_n]:
        cold.append(await bench.call(cold_prefix_messages(q), node="bench_cache_cold", **opts))
        warm.append(await bench.call(shared_messages(q), node="bench_cache_shared", **opts))
    result = cache_result(cold, warm, args.cache_n)
    print(
        f"[cache] 입력 {result['shared']['input_tokens']}토큰, {result['verdict']}, "
        f"cached_tokens 중앙값 {result['cached_tokens']}"
    )
    return result


class HeaderCensus(AsyncCallbackHandler):
    """응답 헤더 이름별로 등장 횟수와 서로 다른 값의 수를 센다. 값은 해시로만 메모리에 두고 출력하지 않는다."""

    run_inline = True

    def __init__(self) -> None:
        self.responses = 0
        self.counts: dict[str, int] = defaultdict(int)
        self.digests: dict[str, set[str]] = defaultdict(set)
        self.lengths: dict[str, list[int]] = defaultdict(list)

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        self.responses += 1
        for name, value in response_headers(response).items():
            self.counts[name] += 1
            self.digests[name].add(hashlib.sha256(value.encode()).hexdigest())
            self.lengths[name].append(len(value))

    def rows(self) -> list[dict]:
        return [
            {
                "name": name,
                "responses": self.counts[name],
                "distinct": len(self.digests[name]),
                "length": [min(self.lengths[name]), max(self.lengths[name])],
                "sensitive": name in SENSITIVE_HEADERS,
            }
            for name in sorted(self.counts)
        ]


async def run_list_headers(settings: Settings, args: argparse.Namespace, recorder: MetricsRecorder) -> dict:
    """짧은 cold 요청을 보내 응답 헤더 이름과 서로 다른 값의 수를 모은다. 헤더 값은 출력하지 않는다."""
    model = create_chat_model(settings, recorder=recorder, max_tokens=1, include_response_headers=True)
    census = HeaderCensus()
    bench = Bench(model, recorder, callbacks=[census])
    try:
        for i in range(args.list_headers):
            messages = cold_messages(QUESTIONS[i % len(QUESTIONS)], bench.next_seq)
            await bench.call(messages, node="bench_headers", stream=True)
    finally:
        await model.http_async_client.aclose()
    fingerprints = sorted({r.system_fingerprint or "(없음)" for r in _ok(recorder.records("llm"))})
    return {"responses": census.responses, "headers": census.rows(), "fingerprints": fingerprints}


def headers_to_text(found: dict) -> str:
    lines = [
        f"응답 {found['responses']}건의 헤더 (값은 출력하지 않는다)",
        "",
        "| 헤더 이름 | 등장 | 서로 다른 값 | 값 길이 | 비고 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for h in found["headers"]:
        if h["sensitive"]:
            note = "민감 헤더라 기록 불가"
        elif 1 < h["distinct"] < max(3, found["responses"] // 2):
            # 요청마다 값이 바뀌는 헤더(요청 ID 등)는 값이 응답 수만큼 많아 후보에서 뺀다
            note = "인스턴스 구분 후보"
        else:
            note = ""
        lo, hi = h["length"]
        lines.append(f"| {h['name']} | {h['responses']} | {h['distinct']} | {lo}~{hi} | {note} |")
    lines += ["", f"system_fingerprint: {', '.join(found['fingerprints'])}"]
    return "\n".join(lines)


ITEMS = ["latency", "lengths", "cache"]


async def run_bench(settings: Settings, args: argparse.Namespace, recorder: MetricsRecorder) -> dict:
    model = create_chat_model(settings, recorder=recorder, max_tokens=200)
    bench = Bench(model, recorder)
    names = args.only or ITEMS
    results: dict[str, Any] = {}
    try:
        if "latency" in names:
            results["latency"] = await run_latency(bench, args)
        if "lengths" in names or "cache" in names:
            base, per_unit = await calibrate(bench)
            results["calibration"] = {"base_tokens": base, "tokens_per_unit": per_unit}
            if "lengths" in names:
                results["lengths"] = await run_lengths(bench, args, base, per_unit)
            if "cache" in names:
                results["cache"] = await run_cache(bench, args, base, per_unit)
    finally:
        await model.http_async_client.aclose()
    return {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "commit": git_commit(),
        "model": settings.llm_model,
        "base_url_host": httpx.URL(settings.llm_base_url).host,
        "max_concurrency": settings.llm_max_concurrency,
        "n": args.n,
        "warmup": WARMUP,
        "len_n": args.len_n,
        "cache_n": args.cache_n,
        "results": results,
    }


def calls_path_for(json_path: Path) -> Path:
    """<시각>_latency.json 옆의 호출별 기록 <시각>_latency_calls.jsonl 경로."""
    return json_path.with_name(json_path.name.replace("_latency.json", "_latency_calls.jsonl"))


def rebuild_report(json_path: Path, calls_path: Path | None = None, keys: list[str] | None = None) -> dict:
    """저장된 결과 json(측정 조건)과 호출별 jsonl 로 결과를 다시 계산한다. 서버는 호출하지 않는다.

    호출별 기록은 보낸 순서대로 쌓이므로 노드 이름과 순서로 항목·조건을 나눈다.
    """
    meta = json.loads(json_path.read_text(encoding="utf-8"))
    calls_path = calls_path or calls_path_for(json_path)
    by_node: dict[str, list[LLMCallRecord]] = defaultdict(list)
    for line in calls_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            data = json.loads(line)
            if data.get("kind") == "llm":
                by_node[data["node"]].append(LLMCallRecord(**data))

    old = meta.get("results", {})
    warmup = meta.get("warmup", WARMUP)
    results: dict[str, Any] = {}
    if by_node["bench_latency_service"]:
        results["latency"] = latency_result(
            by_node["bench_latency_service"][warmup:], by_node["bench_latency_cold"][warmup:], meta["n"], keys
        )
    if "calibration" in old:
        results["calibration"] = old["calibration"]
    if by_node["bench_lengths"]:
        size = 1 + meta["len_n"]  # 길이마다 워밍업 1회
        targets = [row["target"] for row in old.get("lengths", {}).get("rows", [])] or LENGTH_TARGETS
        recs = by_node["bench_lengths"]
        rows = [length_row(t, recs[i * size : (i + 1) * size][1:], meta["len_n"], keys) for i, t in enumerate(targets)]
        results["lengths"] = {"rows": rows}
    if by_node["bench_cache_cold"]:
        results["cache"] = cache_result(
            by_node["bench_cache_cold"], by_node["bench_cache_shared"], meta["cache_n"], keys
        )
    return {**meta, "results": results, "rebuilt_from": calls_path.name, "label_headers": keys}


# ---------------------------------------------------------------------------
# 보고
# ---------------------------------------------------------------------------
def _cell(v: Any) -> str:
    return "-" if v is None else str(v)


def _breakdown_lines(items: list[tuple[str, dict]]) -> list[str]:
    rows, notes = [], []
    for cond, summary in items:
        bd = summary["breakdown"]
        for g in bd["groups"]:
            rows.append(
                f"| {cond} | {g['label']} | {g['n']} | {fmt(g['ttft_p50'])} | {fmt(g['ttft_p95'])} | "
                f"{fmt(g['total_p50'])} | {fmt(g['total_p95'])} |"
            )
        if bd["clusters"]:
            (a, b), (c, d) = bd["clusters"]
            reason = "구분값이 하나뿐이라 나누지 못했다" if bd["groups"] else "구분값을 얻지 못했다"
            notes.append(
                f"- {cond}: {reason}. 첫 글자 시간 분포가 두 무리로 갈린다 ({a:.3f}~{b:.3f}초, {c:.3f}~{d:.3f}초)"
            )
    lines = []
    if rows:
        lines += [
            "",
            "구분값별",
            "",
            "| 조건 | 구분값 | n | TTFT p50 | TTFT p95 | total p50 | total p95 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            *rows,
        ]
    if notes:
        lines += ["", *notes]
    return lines


def to_markdown(report: dict) -> str:
    res = report["results"]
    lines = [
        f"# LLM 래퍼 응답 시간 ({report['measured_at']})",
        "",
        f"- 측정 시각 {report['measured_at']} · 모델 `{report['model']}` · 호스트 `{report['base_url_host']}` · "
        f"커밋 `{report['commit']}`",
        f"- 동시성 설정 {report['max_concurrency']} · 호출은 모두 순차",
        "- 서비스형: 모든 요청이 같은 시스템 프롬프트·도구 설명을 앞에 두고 질문만 다르다 (대표값)",
        "- cold: 같은 내용이지만 시스템 메시지 첫 줄에 요청마다 고유한 문자열을 넣어 앞부분이 겹치지 않는다 (최악값)",
        f"- 반복: 기준선 조건마다 {report['n']}회 (워밍업 {report['warmup']}회 제외, 질문은 모두 다름) · "
        f"입력 길이별 길이마다 {report['len_n']}회 (워밍업 1회 제외) · 캐시 조건마다 {report['cache_n']}회",
        "- TTFT 는 동시성 슬롯을 얻은 뒤부터 내용이 있는 첫 토큰까지다. total 도 슬롯을 얻은 뒤부터 잰다.",
    ]
    if report.get("rebuilt_from"):
        keys = ", ".join(report.get("label_headers") or []) or "기록된 허용 헤더 전체"
        lines.append(f"- 저장된 호출별 기록 `{report['rebuilt_from']}` 로 다시 만든 표다. 구분 헤더: {keys}")
    lines.append("")
    if lat := res.get("latency"):
        lines += [
            "## 기준선",
            "",
            "| 조건 | 입력 토큰 | n | 실패 | TTFT p50 | TTFT p95 | total p50 | total p95 | cached_tokens 보고 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        conds = (("service", "서비스형"), ("cold", "cold"))
        for cond, label in conds:
            r = lat[cond]
            lines.append(
                f"| {label} | {_cell(r['input_tokens'])} | {r['n']} | {r['errors']} | {fmt(r['ttft_p50'])} | "
                f"{fmt(r['ttft_p95'])} | {fmt(r['total_p50'])} | {fmt(r['total_p95'])} | "
                f"{r['cached_reported']}/{r['n']} |"
            )
        lines += _breakdown_lines([(label, lat[cond]) for cond, label in conds])
        lines += ["", "- 한국어 합성 질문, 도구 설명은 텍스트로만 포함(도구 호출 없음), 스트리밍, max_tokens 200", ""]
    if lengths := res.get("lengths"):
        lines += [
            "## 입력 길이별 첫 글자 시간 (cold)",
            "",
            "| 목표 입력 | 실제 입력(중앙값) | n | 실패 | TTFT p50 | TTFT p95 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in lengths["rows"]:
            lines.append(
                f"| {row['target']:,} | {_cell(row['input_tokens'])} | {row['n']} | {row['errors']} | "
                f"{fmt(row['ttft_p50'])} | {fmt(row['ttft_p95'])} |"
            )
        lines += _breakdown_lines([(f"{row['target']:,}토큰", row) for row in lengths["rows"]])
        note = f"- 요청마다 첫 줄을 바꿔 캐시가 적중하지 않게 했다. 답변은 {LENGTH_MAX_TOKENS}토큰에서 끊었다."
        lines += ["", note, ""]
    if cache := res.get("cache"):
        c, w = cache["cold"], cache["shared"]
        lines += [
            "## 프리픽스 캐시",
            "",
            "| 조건 | 입력 토큰 | n | 실패 | TTFT 중앙값 | total 중앙값 |",
            "| --- | --- | --- | --- | --- | --- |",
            f"| cold | {_cell(c['input_tokens'])} | {c['n']} | {c['errors']} | {fmt(c['ttft_median'])} | "
            f"{fmt(c['total_median'])} |",
            f"| 같은 접두 반복 | {_cell(w['input_tokens'])} | {w['n']} | {w['errors']} | {fmt(w['ttft_median'])} | "
            f"{fmt(w['total_median'])} |",
            *_breakdown_lines([("cold", c), ("같은 접두 반복", w)]),
            "",
            f"- {cache['verdict']}",
            f"- 같은 접두 반복의 cached_tokens 중앙값: {cache['cached_tokens']}",
            f"- 두 조건을 번갈아 보냈다. 같은 접두 쪽은 캐시를 채우는 첫 호출 1회를 집계에서 뺐다. "
            f"답변은 {CACHE_MAX_TOKENS}토큰에서 끊었다.",
            "",
        ]
    return "\n".join(lines)


def save(report: dict, out_dir: Path, stamp: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stamp}_latency.json"
    md_path = out_dir / f"{stamp}_latency.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path


def _int_in(low: int, high: int):
    def parse(text: str) -> int:
        value = int(text)
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{low}~{high} 사이여야 한다")
        return value

    return parse


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM 래퍼 응답 시간 벤치마크")
    p.add_argument("--only", nargs="+", choices=ITEMS, help="실행할 항목만 지정")
    max_n = len(QUESTIONS) - WARMUP
    p.add_argument(
        "--n",
        type=_int_in(1, max_n),
        default=30,
        help=f"기준선 조건마다 반복 횟수 (워밍업 3회 별도). 질문이 겹치지 않도록 최대 {max_n}",
    )
    p.add_argument("--len-n", type=int, default=5, help="입력 길이별 반복 횟수 (길이마다 워밍업 1회 별도)")
    max_cache_n = len(CACHE_QUESTIONS) - 1
    p.add_argument(
        "--cache-n",
        type=_int_in(5, max_cache_n),
        default=5,
        help=f"캐시 비교 조건마다 반복 횟수 (5~{max_cache_n})",
    )
    p.add_argument(
        "--list-headers",
        type=int,
        default=0,
        metavar="N",
        help="짧은 요청 N회로 응답 헤더 이름과 서로 다른 값의 수만 출력하고 끝낸다 (값은 출력하지 않음)",
    )
    p.add_argument(
        "--rebuild",
        type=Path,
        metavar="JSON",
        help="저장된 <시각>_latency.json 과 옆의 호출별 jsonl 로 표만 다시 만든다 (서버 호출 없음)",
    )
    p.add_argument("--calls", type=Path, help="--rebuild 에 쓸 호출별 jsonl. 생략하면 json 옆 파일을 쓴다")
    p.add_argument(
        "--label-headers",
        type=lambda text: [h.strip().lower() for h in text.split(",") if h.strip()],
        help="--rebuild 에서 백엔드 구분에 쓸 헤더 (쉼표 구분). 생략하면 기록된 허용 헤더 전체",
    )
    p.add_argument("--save", action="store_true", help="결과를 --out 폴더에 json·md·호출별 jsonl 로 저장 (git 제외)")
    p.add_argument("--out", type=Path, default=RESULTS_DIR, help="결과 저장 폴더 (기본 bench/results/)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    if args.rebuild:
        report = rebuild_report(args.rebuild, args.calls, args.label_headers)
        print(to_markdown(report))
        if args.save:
            md_path = args.rebuild.with_name(args.rebuild.name.replace(".json", "_rebuilt.md"))
            md_path.write_text(to_markdown(report), encoding="utf-8")
            print(f"저장: {md_path}")
        return
    try:
        settings = get_settings()
    except ValidationError:
        raise SystemExit("LLM_API_KEY가 없습니다. LLM_API_KEY를 넣은 뒤 다시 실행하세요.") from None
    if args.list_headers:
        found = asyncio.run(run_list_headers(settings, args, MetricsRecorder()))
        print(headers_to_text(found))
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    recorder = MetricsRecorder(args.out / f"{stamp}_latency_calls.jsonl" if args.save else None, max_records=10_000)
    report = asyncio.run(run_bench(settings, args, recorder))
    print("\n" + to_markdown(report))
    if args.save:
        json_path, md_path = save(report, args.out, stamp)
        print(f"저장: {json_path}\n요약: {md_path}\n호출별: {recorder.jsonl_path}")
    else:
        print("(저장하지 않음. 저장하려면 --save)")


if __name__ == "__main__":
    main()
