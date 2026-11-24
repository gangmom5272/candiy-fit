from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from pydantic import ValidationError

from bench.probe_llm import SAMPLE_TOOL, body_for, chat_once, make_client
from coach_ai.config import Settings, get_settings

ANSWER_MAX_TOKENS = 1000  # 답변 출력 토큰 상한. 초과하면 답변이 잘리고 finish_reason이 length가 된다

SYSTEM = (
    "너는 웨어러블 건강데이터를 근거로 생활 습관을 조언하는 건강 코치다. "
    "질병 여부를 판단하거나 약을 권하지 않고, 그런 요청에는 의사·약사 상담을 안내한다. "
    "건강 수치가 필요하면 도구를 호출한다."
)

# 도구 결과로 사용하는 합성 데이터다. 실제 사용자 데이터가 아니다.
# CANDiY 수면 레코드(sleep_duration_total, stages)를 서버가 한국 시간 기준으로 집계해 모델에 전달하는 형식이다.
# - 날짜는 수면 종료일(KST) 기준이다. 기간 문자열(PT5H37M28S)은 분 단위 값과 표시용 문자열로 변환한다
# - 수면 단계 코드(4 얕은 수면, 5 깊은 수면, 6 REM, 1·7 깨어 있음)는 단계별 분으로 합산한다
# - 데이터가 없는 날은 missing_days에 명시한다
FAKE_SLEEP_RESULT = {
    "metric": "sleep",
    "period": {"start": "2026-10-07", "end": "2026-10-13", "basis": "수면 종료일(KST)"},
    "nights": [
        {
            "date": "2026-10-07",
            "bedtime": "00:40",
            "wake": "07:21",
            "total_min": 383,
            "total": "6시간 23분",
            "deep_min": 64,
            "rem_min": 88,
            "light_min": 231,
            "awake_min": 18,
        },
        {
            "date": "2026-10-08",
            "bedtime": "01:10",
            "wake": "07:15",
            "total_min": 340,
            "total": "5시간 40분",
            "deep_min": 55,
            "rem_min": 72,
            "light_min": 213,
            "awake_min": 25,
        },
        {
            "date": "2026-10-10",
            "bedtime": "01:55",
            "wake": "07:20",
            "total_min": 291,
            "total": "4시간 51분",
            "deep_min": 41,
            "rem_min": 60,
            "light_min": 190,
            "awake_min": 34,
        },
        {
            "date": "2026-10-11",
            "bedtime": "00:58",
            "wake": "07:10",
            "total_min": 352,
            "total": "5시간 52분",
            "deep_min": 58,
            "rem_min": 79,
            "light_min": 215,
            "awake_min": 20,
        },
        {
            "date": "2026-10-12",
            "bedtime": "23:50",
            "wake": "07:35",
            "total_min": 450,
            "total": "7시간 30분",
            "deep_min": 81,
            "rem_min": 102,
            "light_min": 267,
            "awake_min": 15,
        },
        {
            "date": "2026-10-13",
            "bedtime": "00:35",
            "wake": "07:05",
            "total_min": 368,
            "total": "6시간 8분",
            "deep_min": 60,
            "rem_min": 84,
            "light_min": 224,
            "awake_min": 22,
        },
    ],
    "summary": {
        "avg_total_min": 364,
        "avg_total": "6시간 4분",
        "min": {"date": "2026-10-10", "total": "4시간 51분"},
        "max": {"date": "2026-10-12", "total": "7시간 30분"},
        "days_with_data": 6,
        "missing_days": ["2026-10-09"],
    },
    "source_app": "com.sec.android.app.shealth",
    "note": "합성 데이터",
}


def finish_note(payload: dict) -> str:
    """종료 사유와 출력 토큰 수를 반환한다. 출력 토큰 상한에 도달해 잘린 답변이면 경고 문구를 붙인다."""
    reason = ((payload.get("choices") or [{}])[0]).get("finish_reason")
    tokens = (payload.get("usage") or {}).get("completion_tokens")
    note = f" · 종료 사유 {reason} · 출력 {tokens}토큰"
    return note + (" · [주의] 최대 토큰에 걸려 잘린 답변" if reason == "length" else "")


def answer_text(payload: dict) -> str:
    return (((payload.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""


async def sample_plain(client: httpx.AsyncClient, settings: Settings) -> dict:
    question = "요즘 잠을 잘 못 자는데 생활 습관에서 바꿔 볼 만한 걸 알려줘."
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    status, payload, elapsed = await chat_once(client, body_for(settings, messages, max_tokens=ANSWER_MAX_TOKENS))
    return {
        "title": "1. 일반 답변 (비스트리밍)",
        "desc": f"질문: {question} · HTTP {status} · {elapsed:.2f}초" + finish_note(payload),
        "text": answer_text(payload),
        "json": payload,
    }


async def sample_stream(client: httpx.AsyncClient, settings: Settings) -> dict:
    question = "하루 30분 걷기의 장점을 두 문장으로 말해줘."
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    body = {**body_for(settings, messages, max_tokens=150), "stream": True, "stream_options": {"include_usage": True}}
    lines: list[str] = []
    text = ""
    t0 = time.perf_counter()
    async with client.stream("POST", "/chat/completions", json=body) as r:
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            lines.append(line)
            data = line[5:].strip()
            if data == "[DONE]":
                break
            for ch in json.loads(data).get("choices") or []:
                text += (ch.get("delta") or {}).get("content") or ""
    elapsed = time.perf_counter() - t0
    shown = lines[:4] + ["... (중간 청크 생략) ..."] + lines[-3:] if len(lines) > 7 else lines
    return {
        "title": "2. 스트리밍 원문 (SSE)",
        "desc": f"질문: {question} · 청크 {len(lines)}개 · {elapsed:.2f}초. 앞 4줄과 마지막 3줄만 표시",
        "text": text,
        "raw": "\n".join(shown),
    }


async def sample_tool_flow(client: httpx.AsyncClient, settings: Settings) -> list[dict]:
    question = "지난 7일 동안 잠은 어땠어?"
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    status, payload, elapsed = await chat_once(
        client, body_for(settings, messages, tools=[SAMPLE_TOOL], tool_choice="auto", max_tokens=200)
    )
    message = ((payload.get("choices") or [{}])[0]).get("message") or {}
    first = {
        "title": "3. 도구 호출 응답",
        "desc": f"질문: {question} · HTTP {status} · {elapsed:.2f}초. 모델이 답 대신 도구 호출을 돌려준다",
        "json": message,
    }
    calls = message.get("tool_calls") or []
    if not calls:
        first["desc"] += " · (도구 호출이 오지 않아 4번 샘플은 생략)"
        return [first]

    follow = messages + [
        {"role": "assistant", "content": message.get("content") or "", "tool_calls": calls},
        {
            "role": "tool",
            "tool_call_id": calls[0]["id"],
            "content": json.dumps(FAKE_SLEEP_RESULT, ensure_ascii=False),
        },
    ]
    status, payload, elapsed = await chat_once(
        client, body_for(settings, follow, tools=[SAMPLE_TOOL], max_tokens=ANSWER_MAX_TOKENS)
    )
    second = {
        "title": "4. 도구 결과를 넣은 최종 답변",
        "desc": f"서버가 수면 레코드를 집계한 결과(합성 데이터)를 넣어 다시 호출 · HTTP {status} · {elapsed:.2f}초"
        + finish_note(payload),
        "input": FAKE_SLEEP_RESULT,
        "text": answer_text(payload),
    }
    return [first, second]


async def sample_boundary(client: httpx.AsyncClient, settings: Settings) -> dict:
    question = "나 불면증이야? 수면제 먹어야 해?"
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    status, payload, elapsed = await chat_once(client, body_for(settings, messages, max_tokens=ANSWER_MAX_TOKENS))
    return {
        "title": "5. 진단·처방 요청에 대한 답변",
        "desc": f"질문: {question} · HTTP {status} · {elapsed:.2f}초. 위 시스템 프롬프트만 넣은 상태"
        + finish_note(payload),
        "text": answer_text(payload),
    }


async def sample_structured(client: httpx.AsyncClient, settings: Settings) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "metric": {"type": "string", "enum": ["steps", "heart_rate", "oxygen_saturation", "sleep"]},
            "period": {"type": "string", "enum": ["today", "yesterday", "last_7_days", "last_30_days"]},
        },
        "required": ["metric", "period"],
        "additionalProperties": False,
    }
    question = "어제 몇 걸음 걸었어?"
    messages = [{"role": "user", "content": f"다음 질문의 지표와 기간을 JSON으로만 답해: '{question}'"}]
    status, payload, elapsed = await chat_once(
        client,
        body_for(
            settings,
            messages,
            max_tokens=60,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "intent", "schema": schema, "strict": True},
            },
        ),
    )
    return {
        "title": "6. 구조화 출력 (json_schema)",
        "desc": f"질문: {question} · HTTP {status} · {elapsed:.2f}초. content에 JSON 문자열이 담겨 온다",
        "text": answer_text(payload),
    }


async def sample_embedding(client: httpx.AsyncClient, settings: Settings) -> dict:
    t0 = time.perf_counter()
    r = await client.post("/embeddings", json={"model": settings.llm_embed_model, "input": ["어제 잠을 몇 시간 잤어?"]})
    elapsed = time.perf_counter() - t0
    payload = r.json()
    for item in payload.get("data") or []:
        vector = item.get("embedding") or []
        item["embedding"] = [round(v, 4) for v in vector[:5]] + [f"... 총 {len(vector)}개"]
    return {
        "title": "7. 임베딩 응답",
        "desc": f"입력 1문장 · HTTP {r.status_code} · {elapsed:.2f}초. 벡터는 앞 5개만 표시",
        "json": payload,
    }


async def collect(settings: Settings, client: httpx.AsyncClient | None = None) -> list[dict]:
    own = client is None
    client = client or make_client(settings)
    sections: list[dict] = []
    try:
        for fn in (sample_plain, sample_stream, sample_tool_flow, sample_boundary, sample_structured, sample_embedding):
            try:
                result = await fn(client, settings)
            except Exception as e:  # 한 샘플이 실패해도 나머지 샘플은 계속 수집한다
                result = {"title": fn.__name__, "desc": f"실패: {type(e).__name__}: {e}"[:300]}
            sections.extend(result if isinstance(result, list) else [result])
    finally:
        if own:
            await client.aclose()
    return sections


def render(sections: list[dict], settings: Settings) -> str:
    out = [
        "# 사내 LLM 응답 샘플",
        "",
        f"- 받은 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')} · 모델 `{settings.llm_model}`",
        "- 질문과 도구 결과 수치는 합성 데이터다. 시스템 프롬프트는 샘플용 임시 문장이다.",
        "- 1·4·5번은 비스트리밍 호출이라 표시된 시간은 답변 전체가 끝날 때까지의 시간이다. "
        "실제 서비스는 스트리밍이라 첫 글자는 훨씬 빨리 보인다.",
        "",
        "```text",
        SYSTEM,
        "```",
    ]
    for s in sections:
        out += ["", f"## {s['title']}", "", s.get("desc", "")]
        if "input" in s:
            out += [
                "",
                "도구 결과로 넣은 값:",
                "",
                "```json",
                json.dumps(s["input"], ensure_ascii=False, indent=2),
                "```",
            ]
        if s.get("text"):
            out += ["", "답변:", "", "> " + s["text"].strip().replace("\n", "\n> ")]
        if "raw" in s:
            out += ["", "원문:", "", "```text", s["raw"], "```"]
        if "json" in s:
            out += ["", "응답 JSON:", "", "```json", json.dumps(s["json"], ensure_ascii=False, indent=2), "```"]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="사내 LLM 응답 샘플 정리")
    p.add_argument("--out", type=Path, help="마크다운 파일로 저장할 경로")
    args = p.parse_args(argv)
    try:
        settings = get_settings()
    except ValidationError:
        raise SystemExit("LLM_API_KEY가 없습니다. 저장소 루트 .env에 넣은 뒤 다시 실행하세요.") from None
    md = render(asyncio.run(collect(settings)), settings)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"저장: {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
