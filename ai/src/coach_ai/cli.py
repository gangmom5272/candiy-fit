from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.graph import CoachResult, run_coach
from coach_ai.config import get_settings
from coach_ai.history.types import StoredSummary
from coach_ai.llm.client import create_chat_model


class PrintSink:
    async def emit(self, event: dict[str, Any]) -> None:
        print(json.dumps(event, ensure_ascii=False), flush=True)


async def run(question: str, summary: str | None, user_id: str) -> CoachResult:
    settings = get_settings()
    now = datetime.now(UTC)
    # 요약 뒤에 넘기는 쌍이 없어 until_seq 는 1 이상이면 어떤 값이어도 같다
    stored = None if summary is None else StoredSummary(text=summary, until_seq=1)
    ctx = CoachContext(user_id=user_id, conversation_id="cli", request_id=f"cli-{uuid.uuid4().hex[:8]}", summary=stored)
    model = create_chat_model(settings)
    try:
        return await run_coach(
            ctx,
            question,
            port=sample_health_data(user_id, now, include_exercise_activity=True),
            model=model,
            sink=PrintSink(),
            now=now,
            message_id=f"cli-{uuid.uuid4().hex[:8]}",
        )
    finally:
        await model.http_async_client.aclose()


def _summary(text: str) -> str:
    if not text.strip():
        raise argparse.ArgumentTypeError("빈 요약은 넣을 수 없다")
    return text


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="합성 데이터로 코칭 에이전트 실행 (실제 LLM 호출)")
    p.add_argument("question", help="사용자 질문 (합성 문장)")
    p.add_argument("--summary", type=_summary, help="이전 대화 요약")
    p.add_argument("--user", default="demo-user", help="합성 데이터 사용자 ID")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    try:
        get_settings()
    except ValidationError:
        raise SystemExit("LLM_API_KEY가 없습니다. 저장소 루트 .env 에 넣은 뒤 다시 실행하세요.") from None
    result = asyncio.run(run(args.question, args.summary, args.user))
    # 근거 이벤트로만 보내는 근거는 도구 메시지의 근거 번호가 없어 "-" 로 쓴다
    evidence = [f"{e.ref or '-'} {e.summary}" for e in result.evidence]
    print(
        f"\n종료 사유: {result.finish_reason}, 도구 왕복: {result.tool_rounds}회, 근거: {evidence}",
        file=sys.stderr,
    )
    if result.exception is not None:
        # run_coach 는 예외를 발생시키지 않아 원인을 여기서 출력한다. 합성 데이터라 문구를 그대로 쓴다
        traceback.print_exception(result.exception, file=sys.stderr)


if __name__ == "__main__":
    main()
