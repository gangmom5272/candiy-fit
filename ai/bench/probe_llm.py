from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from coach_ai.config import Settings, get_settings

RESULTS_DIR = Path(__file__).resolve().parent / "results"
WARMUP = 3

METRICS = ["steps", "heart_rate", "oxygen_saturation", "sleep"]  # 적재 데이터의 지표 이름과 같다
PERIODS = ["today", "yesterday", "last_7_days", "last_30_days"]

SAMPLE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_metric_summary",
        "description": "지표 하나의 기간 요약을 조회한다. 조회 대상 사용자는 서버가 정한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": METRICS},
                "period": {"type": "string", "enum": PERIODS},
            },
            "required": ["metric", "period"],
            "additionalProperties": False,
        },
    },
}
# 질문은 합성 문장만 쓴다. 실제 건강데이터는 쓰지 않는다
TOOL_SYSTEM = "너는 건강 코치다. 사용자의 건강 수치가 필요하면 반드시 도구를 호출한다."
TOOL_QUESTION = "지난 7일 동안 잠을 얼마나 잤는지 알려줘"
TOOL_EXPECTED = {"metric": "sleep", "period": "last_7_days"}


# ---------------------------------------------------------------------------
# 호출 유틸
# ---------------------------------------------------------------------------
@dataclass
class StreamResult:
    status: int = 0
    first_byte: float | None = None  # 첫 SSE 청크 수신 시각. curl time_starttransfer에 해당한다
    ttft: float | None = None  # 내용이 있는 첫 content 수신 시각. 첫 글자 시간에 해당한다
    total: float = 0.0
    content: str = ""
    reasoning_chars: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str | None = None
    error: str | None = None


def body_for(
    settings: Settings, messages: list[dict], *, thinking: bool | None = False, max_tokens: int = 200, **extra: Any
) -> dict:
    """thinking=None이면 chat_template_kwargs를 전송하지 않으며 서버 기본값이 적용된다."""
    body: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
        **extra,
    }
    if thinking is not None:
        body["chat_template_kwargs"] = {"enable_thinking": thinking}
    return body


async def chat_stream(client: httpx.AsyncClient, body: dict) -> StreamResult:
    body = {**body, "stream": True, "stream_options": {"include_usage": True}}
    res = StreamResult()
    calls: dict[int, dict] = {}
    t0 = time.perf_counter()
    try:
        async with client.stream("POST", "/chat/completions", json=body) as r:
            res.status = r.status_code
            if r.status_code != 200:
                res.error = (await r.aread()).decode("utf-8", "replace")[:300]
                return res
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if res.first_byte is None:
                    res.first_byte = time.perf_counter() - t0
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    res.usage = chunk["usage"]
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    text = delta.get("content")
                    if text:
                        if res.ttft is None:
                            res.ttft = time.perf_counter() - t0
                        res.content += text
                    thought = delta.get("reasoning") or delta.get("reasoning_content")
                    if thought:
                        res.reasoning_chars += len(thought)
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "arguments": ""})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
                    if ch.get("finish_reason"):
                        res.finish_reason = ch["finish_reason"]
    except httpx.HTTPError as e:  # 연결 끊김·타임아웃
        res.error = f"{type(e).__name__}: {e}"[:300]
    finally:
        res.total = time.perf_counter() - t0
        res.tool_calls = [calls[i] for i in sorted(calls)]
    return res


async def chat_once(client: httpx.AsyncClient, body: dict) -> tuple[int, dict, float]:
    t0 = time.perf_counter()
    r = await client.post("/chat/completions", json={**body, "stream": False})
    elapsed = time.perf_counter() - t0
    try:
        payload = r.json()
    except ValueError:
        payload = {"raw": r.text[:300]}
    return r.status_code, payload, elapsed


def pct(values: list[float], q: float) -> float | None:
    """nearest-rank 방식 백분위수. 표본이 적어도 계산할 수 있다."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = max(0, min(len(vals) - 1, math.ceil(q / 100 * len(vals)) - 1))
    return vals[k]


def fmt(sec: float | None) -> str:
    return "-" if sec is None else f"{sec:.3f}s"


def check_tool_args(call: dict) -> tuple[bool, bool, str]:
    """(스키마 유효 여부, 기대 인자 일치 여부, 사유)를 반환한다."""
    if call.get("name") != "get_metric_summary":
        return False, False, f"unexpected tool {call.get('name')!r}"
    try:
        args = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        return False, False, "arguments is not valid JSON"
    if not isinstance(args, dict) or set(args) != {"metric", "period"}:
        return False, False, f"keys={sorted(args) if isinstance(args, dict) else type(args)}"
    if args["metric"] not in METRICS or args["period"] not in PERIODS:
        return False, False, f"enum violation {args}"
    return True, args == TOOL_EXPECTED, "ok"


def message_tool_calls(payload: dict) -> list[dict]:
    msg = ((payload.get("choices") or [{}])[0]).get("message") or {}
    return [{"id": tc.get("id"), **(tc.get("function") or {})} for tc in msg.get("tool_calls") or []]


# ---------------------------------------------------------------------------
# 점검 항목. 각 함수는 {"status": ok|warn|fail|error|skip, "summary": str, ...}를 반환한다.
# ---------------------------------------------------------------------------
Check = Callable[[httpx.AsyncClient, Settings, argparse.Namespace], Awaitable[dict]]


async def check_models(client, settings, args) -> dict:
    r = await client.get("/models")
    if r.status_code != 200:
        return {"status": "fail", "summary": f"HTTP {r.status_code}", "body": r.text[:300]}
    models = r.json().get("data") or []
    ids = [m.get("id") for m in models]
    ours = next((m for m in models if m.get("id") == settings.llm_model), None)
    max_len = ours.get("max_model_len") if ours else None
    status = "ok" if ours else "warn"
    return {
        "status": status,
        "summary": f"models={ids}, {settings.llm_model} max_model_len={max_len}",
        "ids": ids,
        "max_model_len": max_len,
    }


CONTEXT_STEPS = [40_000, 140_000, 270_000]  # 입력 토큰 수 근사값. 상한을 넘으면 서버가 추론 전에 거절한다
CONTEXT_RE = re.compile(r"maximum (?:context|model) length (?:is|of) (\d+)")


async def check_context_limit(client, settings, args) -> dict:
    """/models에 max_model_len이 없으므로 입력 길이를 늘려 가며 서버 거절 메시지에서 상한을 추출한다."""
    tried = []
    for tokens in CONTEXT_STEPS:
        filler = "a " * tokens  # 'a ' 하나가 약 1토큰이다
        messages = [{"role": "user", "content": filler + "\n위 내용은 무시하고 '확인'이라고만 답해."}]
        status, payload, _ = await chat_once(client, body_for(settings, messages, max_tokens=1))
        text = json.dumps(payload, ensure_ascii=False)
        found = CONTEXT_RE.search(text)
        if found:
            return {
                "status": "ok",
                "summary": f"컨텍스트 상한 {int(found.group(1))}토큰 (약 {tokens}토큰 입력에서 거절, 오류 메시지 기준)",
                "max_model_len": int(found.group(1)),
            }
        if status != 200:
            return {"status": "warn", "summary": f"약 {tokens}토큰 입력에서 HTTP {status}: {text[:200]}"}
        tried.append(tokens)
    return {"status": "warn", "summary": f"약 {tried[-1]}토큰까지 거절 없음 → 상한은 그 이상 (서버 설정 확인 필요)"}


async def check_basic(client, settings, args) -> dict:
    messages = [
        {"role": "system", "content": "모든 답변을 '[코치]'로 시작한다."},
        {"role": "user", "content": "물을 자주 마시면 좋은 이유를 한 문장으로 말해줘."},
    ]
    status, payload, elapsed = await chat_once(client, body_for(settings, messages, max_tokens=120))
    if status != 200:
        return {"status": "fail", "summary": f"HTTP {status}", "body": payload}
    content = (((payload.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
    system_ok = content.strip().startswith("[코치]")
    return {
        "status": "ok" if system_ok else "warn",
        "summary": f"{elapsed:.2f}s, system 지시 반영={system_ok}, "
        f"fingerprint={payload.get('system_fingerprint')}, usage={payload.get('usage')}",
        "content": content[:200],
        "system_role_followed": system_ok,
    }


async def check_stream(client, settings, args) -> dict:
    messages = [{"role": "user", "content": "잠을 잘 자기 위한 습관 세 가지를 짧게 알려줘."}]
    runs: list[StreamResult] = []
    for _ in range(WARMUP + args.n):
        runs.append(await chat_stream(client, body_for(settings, messages, max_tokens=200)))
    measured = [r for r in runs[WARMUP:] if r.status == 200 and not r.error]
    if not measured:
        return {"status": "fail", "summary": f"성공 0건, 첫 오류={runs[-1].error}"}
    ttft = [r.ttft for r in measured]
    total = [r.total for r in measured]
    first_byte = [r.first_byte for r in measured]
    usage_ok = all(r.usage for r in measured)
    return {
        "status": "ok" if usage_ok else "warn",
        "summary": f"n={len(measured)} TTFT p50={fmt(pct(ttft, 50))} p95={fmt(pct(ttft, 95))}, "
        f"total p50={fmt(pct(total, 50))} p95={fmt(pct(total, 95))}, "
        f"first byte p50={fmt(pct(first_byte, 50))}, 스트리밍 usage={usage_ok}",
        "ttft": ttft,
        "total": total,
        "first_byte": first_byte,
        "completion_tokens": [(r.usage or {}).get("completion_tokens") for r in measured],
    }


async def check_thinking(client, settings, args) -> dict:
    messages = [{"role": "user", "content": "하루 30분 걷기의 장점을 두 문장으로 말해줘."}]
    default = await chat_stream(client, body_for(settings, messages, thinking=None))
    off = await chat_stream(client, body_for(settings, messages, thinking=False))
    on_by_default = default.reasoning_chars > 0
    return {
        "status": "warn" if on_by_default else "ok",
        "summary": f"서버 기본값 reasoning={default.reasoning_chars}자 (TTFT {fmt(default.ttft)}), "
        f"enable_thinking=false reasoning={off.reasoning_chars}자 (TTFT {fmt(off.ttft)})",
        "default_reasoning_chars": default.reasoning_chars,
        "off_reasoning_chars": off.reasoning_chars,
    }


async def check_tools(client, settings, args) -> dict:
    messages = [{"role": "system", "content": TOOL_SYSTEM}, {"role": "user", "content": TOOL_QUESTION}]
    modes = {
        "auto": "auto",
        "required": "required",
        "named": {"type": "function", "function": {"name": "get_metric_summary"}},
    }
    report: dict[str, Any] = {}
    worst = "ok"
    for mode, choice in modes.items():
        latencies, valid, exact, errors = [], 0, 0, []
        for _ in range(args.tool_n):
            status, payload, elapsed = await chat_once(
                client, body_for(settings, messages, tools=[SAMPLE_TOOL], tool_choice=choice)
            )
            latencies.append(elapsed)
            if status != 200:
                errors.append(f"HTTP {status}: {str(payload)[:160]}")
                continue
            calls = message_tool_calls(payload)
            if not calls:
                errors.append("tool_calls 없음")
                continue
            ok, match, reason = check_tool_args(calls[0])
            valid += ok
            exact += match
            if not ok:
                errors.append(reason)
        rest = latencies[1:] or latencies
        report[mode] = {
            "valid": valid,
            "exact": exact,
            "n": args.tool_n,
            "first_call": latencies[0] if latencies else None,
            "median_rest": pct(rest, 50),
            "errors": errors[:3],
        }
        if valid < args.tool_n:
            worst = "fail" if valid == 0 else ("warn" if worst == "ok" else worst)
    summary = ", ".join(
        f"{m}: 유효 {v['valid']}/{v['n']}·정답 {v['exact']}/{v['n']} "
        f"(첫 호출 {fmt(v['first_call'])}, 이후 p50 {fmt(v['median_rest'])})"
        for m, v in report.items()
    )
    return {"status": worst, "summary": summary, "modes": report}


async def check_tools_stream(client, settings, args) -> dict:
    messages = [{"role": "system", "content": TOOL_SYSTEM}, {"role": "user", "content": TOOL_QUESTION}]
    res = await chat_stream(client, body_for(settings, messages, tools=[SAMPLE_TOOL], tool_choice="auto"))
    if res.status != 200:
        return {"status": "fail", "summary": f"HTTP {res.status}: {res.error}"}
    if not res.tool_calls:
        return {"status": "fail", "summary": f"tool_calls delta 없음, content={res.content[:80]!r}"}
    ok, match, reason = check_tool_args(res.tool_calls[0])
    return {
        "status": "ok" if ok else "warn",
        "summary": f"조립된 호출 {len(res.tool_calls)}개, 스키마 유효={ok}, 정답={match} ({reason}), "
        f"finish_reason={res.finish_reason}",
        "tool_calls": res.tool_calls,
    }


async def check_parallel(client, settings, args) -> dict:
    messages = [
        {"role": "system", "content": TOOL_SYSTEM},
        {"role": "user", "content": "오늘 걸음 수랑 지난 7일 수면을 같이 알려줘"},
    ]
    status, payload, elapsed = await chat_once(
        client, body_for(settings, messages, tools=[SAMPLE_TOOL], tool_choice="auto")
    )
    if status != 200:
        return {"status": "fail", "summary": f"HTTP {status}"}
    calls = message_tool_calls(payload)
    return {
        "status": "ok" if len(calls) >= 2 else "warn",
        "summary": f"한 응답의 tool_calls {len(calls)}개 ({elapsed:.2f}s): {[c.get('arguments') for c in calls]}",
    }


async def check_json_schema(client, settings, args) -> dict:
    schema = {
        "type": "object",
        "properties": {"metric": {"type": "string", "enum": METRICS}, "period": {"type": "string", "enum": PERIODS}},
        "required": ["metric", "period"],
        "additionalProperties": False,
    }
    messages = [{"role": "user", "content": "다음 질문의 지표와 기간을 분류해 JSON으로만 답해: '어제 몇 걸음 걸었어?'"}]
    valid = exact = 0
    errors: list[str] = []
    for _ in range(args.tool_n):
        status, payload, _ = await chat_once(
            client,
            body_for(
                settings,
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "intent", "schema": schema, "strict": True},
                },
            ),
        )
        if status != 200:
            errors.append(f"HTTP {status}")
            continue
        text = (((payload.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            errors.append(f"JSON 아님: {text[:60]!r}")
            continue
        if (
            isinstance(obj, dict)
            and set(obj) == {"metric", "period"}
            and obj["metric"] in METRICS
            and obj["period"] in PERIODS
        ):
            valid += 1
            exact += obj == {"metric": "steps", "period": "yesterday"}
        else:
            errors.append(f"스키마 위반: {obj}")
    status = "ok" if valid == args.tool_n else ("fail" if valid == 0 else "warn")
    return {
        "status": status,
        "summary": f"스키마 유효 {valid}/{args.tool_n}, 정답 {exact}/{args.tool_n}",
        "errors": errors[:3],
    }


async def check_prefix_cache(client, settings, args) -> dict:
    """같은 긴 접두로 두 번 호출하고 2회차 usage의 cached_tokens로 캐시 적중 여부를 판정한다.

    스트리밍 응답의 usage에는 prompt_tokens_details가 없을 수 있어 비스트리밍 호출을 사용한다.
    """
    nonce = uuid.uuid4().hex  # 이전 실행의 캐시가 적중하지 않도록 접두를 매번 다르게 한다
    filler = " ".join(["Sleep regularity, daylight exposure and steady activity help recovery."] * 330)
    system = f"[probe {nonce}] 참고 자료: {filler}"
    runs = []
    for q in ["걷기의 장점을 한 문장으로 말해줘.", "물 마시기의 장점을 한 문장으로 말해줘."]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": q}]
        status, payload, elapsed = await chat_once(client, body_for(settings, messages, max_tokens=30))
        if status != 200:
            return {"status": "fail", "summary": f"HTTP {status}: {str(payload)[:160]}"}
        usage = payload.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        runs.append(
            {"elapsed": elapsed, "prompt_tokens": usage.get("prompt_tokens"), "cached": details.get("cached_tokens")}
        )
    cold, warm = runs
    prompt_tokens = warm["prompt_tokens"]
    if warm["cached"] is None:
        status, verdict = "warn", "usage에 cached_tokens 없음 → 판단 불가 (서버 설정 확인 필요)"
    elif warm["cached"] > 0:
        status, verdict = "ok", f"2회차 캐시 적중 {warm['cached']}/{prompt_tokens}토큰 → 캐시 켜져 있음"
    else:
        status, verdict = "ok", f"2회차 캐시 적중 0/{prompt_tokens}토큰 → 캐시 꺼져 있음"
    return {
        "status": status,
        "summary": f"입력 {prompt_tokens}토큰, 응답 시간 1회차 {cold['elapsed']:.3f}s / 2회차 {warm['elapsed']:.3f}s, "
        f"1회차 캐시 {cold['cached']}토큰, {verdict}",
        "runs": runs,
    }


async def check_long(client, settings, args) -> dict:
    messages = [{"role": "user", "content": "수면 위생 원칙을 항목별로 아주 자세하고 길게 설명해줘."}]
    res = await chat_stream(client, body_for(settings, messages, max_tokens=1500))
    tokens = (res.usage or {}).get("completion_tokens")
    status = "ok" if res.status == 200 and not res.error else "fail"
    return {
        "status": status,
        "summary": f"total {fmt(res.total)}, completion {tokens}토큰, "
        f"finish_reason={res.finish_reason}, 오류={res.error}",
    }


async def check_embeddings(client, settings, args) -> dict:
    texts = [
        "어제 잠을 몇 시간 잤어?",
        "오늘 걸음 수 알려줘",
        "심박수가 높은 편이야?",
        "주식 추천해줘",
        "수면제 뭐 먹으면 돼?",
        "가슴이 답답하고 숨이 차",
        "이번 주 운동량 비교해줘",
        "산소포화도 평균은?",
    ]
    t0 = time.perf_counter()
    r = await client.post("/embeddings", json={"model": settings.llm_embed_model, "input": texts})
    elapsed = time.perf_counter() - t0
    if r.status_code != 200:
        return {"status": "fail", "summary": f"HTTP {r.status_code}: {r.text[:160]}"}
    data = r.json().get("data") or []
    dims = len((data[0] or {}).get("embedding") or []) if data else 0
    return {
        "status": "ok" if len(data) == len(texts) else "warn",
        "summary": f"{len(data)}개 임베딩, 차원 {dims}, 배치 {len(texts)}개 {elapsed:.2f}s",
    }


async def check_burst(client, settings, args) -> dict:
    messages = [{"role": "user", "content": "물을 마시면 좋은 이유를 한 문장으로."}]
    started = time.perf_counter()

    async def one(i: int) -> dict:
        r = await chat_stream(client, body_for(settings, messages, max_tokens=60))
        return {"i": i, "status": r.status, "ttft": r.ttft, "total": r.total, "error": r.error}

    results = await asyncio.gather(*(one(i) for i in range(args.burst)))
    wall = time.perf_counter() - started
    codes = [r["status"] for r in results]
    rejected = [c for c in codes if c != 200]
    ttfts = sorted(r["ttft"] for r in results if r["ttft"] is not None)
    if rejected:
        verdict = f"초과분 거절 {len(rejected)}건 (코드 {sorted(set(rejected))})"
    elif ttfts and ttfts[-1] > 2 * ttfts[0]:
        verdict = "전부 200, 뒤쪽 요청의 TTFT가 크게 늘어 대기열 방식으로 보임"
    else:
        verdict = "전부 200, 뚜렷한 대기 없음"
    return {
        "status": "ok",
        "summary": f"동시 {args.burst}건 {wall:.2f}s: {verdict}, TTFT {[round(t, 2) for t in ttfts]}",
        "runs": results,
    }


CHECKS: dict[str, Check] = {
    "models": check_models,
    "context_limit": check_context_limit,
    "basic": check_basic,
    "stream": check_stream,
    "thinking": check_thinking,
    "tools": check_tools,
    "tools_stream": check_tools_stream,
    "parallel": check_parallel,
    "json_schema": check_json_schema,
    "prefix_cache": check_prefix_cache,
    "long": check_long,
    "embeddings": check_embeddings,
    "burst": check_burst,  # 공유 서버에 한도를 넘는 요청을 보내므로 --burst N을 지정할 때만 실행한다
}


# ---------------------------------------------------------------------------
# 실행·보고
# ---------------------------------------------------------------------------
def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# 키는 .env에서만 읽고 출력하지 않는다
def make_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.llm_base_url,
        headers={"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}"},
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=30.0),
    )


async def run_probe(settings: Settings, args: argparse.Namespace, client: httpx.AsyncClient | None = None) -> dict:
    names = args.only or [n for n in CHECKS if n != "burst"]
    if args.burst and "burst" not in names:
        names.append("burst")
    own_client = client is None
    client = client or make_client(settings)
    results: dict[str, dict] = {}
    try:
        for name in names:
            if name == "burst" and not args.burst:
                results[name] = {"status": "skip", "summary": "--burst N으로만 실행"}
                continue
            try:
                results[name] = await CHECKS[name](client, settings, args)
            except Exception as e:  # 한 항목이 실패해도 나머지 항목은 계속 실행한다
                results[name] = {"status": "error", "summary": f"{type(e).__name__}: {e}"[:300]}
            print(f"[{results[name]['status']:>5}] {name:<13} {results[name]['summary']}")
    finally:
        if own_client:
            await client.aclose()
    return {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "commit": git_commit(),
        "model": settings.llm_model,
        "base_url_host": httpx.URL(settings.llm_base_url).host,
        "n": args.n,
        "tool_n": args.tool_n,
        "results": results,
    }


def to_markdown(report: dict) -> str:
    lines = [
        f"# 사내 LLM 실측 결과 ({report['measured_at']})",
        "",
        f"- 모델 `{report['model']}` · 호스트 `{report['base_url_host']}` · "
        f"커밋 `{report['commit']}` · 스트리밍 n={report['n']} · 도구 n={report['tool_n']}",
        "",
        "| 항목 | 결과 | 요약 |",
        "| --- | --- | --- |",
    ]
    for name, r in report["results"].items():
        summary = str(r.get("summary", "")).replace("|", "/")
        lines.append(f"| {name} | {r.get('status')} | {summary} |")
    return "\n".join(lines) + "\n"


def save(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    json_path = out_dir / f"{stamp}_probe.json"
    md_path = out_dir / f"{stamp}_probe.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="사내 LLM 제약 실측")
    p.add_argument("--only", nargs="+", choices=list(CHECKS), help="실행할 항목만 지정")
    p.add_argument("--n", type=int, default=30, help="스트리밍 TTFT 반복 횟수 (워밍업 3회 별도)")
    p.add_argument("--tool-n", type=int, default=10, help="tool calling·json_schema 반복 횟수")
    p.add_argument("--burst", type=int, default=0, help="동시 요청 수. 공유 서버라 사전 공지 후 사용")
    p.add_argument("--save", action="store_true", help="결과를 --out 폴더에 json·md로 저장 (git 제외)")
    p.add_argument("--out", type=Path, default=RESULTS_DIR)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    try:
        settings = get_settings()
    except ValidationError:
        raise SystemExit("LLM_API_KEY가 없습니다. LLM_API_KEY를 넣은 뒤 다시 실행하세요.") from None
    report = asyncio.run(run_probe(settings, args))
    if args.save:
        json_path, md_path = save(report, args.out)
        print(f"\n저장: {json_path}\n요약: {md_path}")
    else:
        print("\n(저장하지 않음. 저장하려면 --save)")


if __name__ == "__main__":
    main()
