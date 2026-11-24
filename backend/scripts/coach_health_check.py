"""합성 전용 DB 와 사내 LLM 으로 어댑터를 거친 코칭 답변을 확인한다: python -m scripts.coach_health_check

실제 LLM 을 호출한다. 질문 4개의 답변과 근거 snapshot 의 평균을 health-query-service.md 의 기대값과 비교해 출력한다.
합성 데이터는 scripts.health_query_example 과 같고, 실행 시점의 KST 오늘을 기준으로 날짜를 만든다.
"""

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import URL, delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.coaching.health_port import HealthQueryPort
from app.core.db import create_sessionmaker
from app.health.models import ActivityDaily, HeartDaily, SleepSession, SyncState
from app.health.schemas import KST
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.graph import run_coach
from coach_ai.config import Settings, get_settings
from coach_ai.llm.client import CoachChatModel, create_chat_model
from coach_ai.security.tracing import TracingEnabledError, ensure_tracing_disabled

METRICS = ("steps", "heart_rates", "sleeps", "oxygen_saturations")
SYNTHETIC_ROWS = (ActivityDaily, HeartDaily, SleepSession, SyncState)


@dataclass(frozen=True)
class Check:
    label: str
    question: str
    # 근거의 공개 지표 코드
    metric: str
    # health-query-service.md 의 기대 평균
    average: Decimal
    # 처리 함수가 반올림해 LLM 에 전달하는 표시값. 답변에 이 중 하나가 나와야 한다
    shown: tuple[str, ...]


CHECKS = (
    Check(
        "걸음",
        "지난 7일 걸음 수 평균 알려 줘",
        "steps",
        Decimal("7465"),
        ("7,465", "7465"),
    ),
    Check(
        "수면",
        "지난 7일 평균 수면 시간은 어땠어?",
        "sleeps",
        Decimal("395.8"),
        ("6시간 36분", "396분"),
    ),
    Check(
        "심박",
        "지난 7일 평균 심박수 알려 줘",
        "heart_rates",
        Decimal("73.83"),
        ("73.8",),
    ),
    Check(
        "산소포화도",
        "지난 7일 수면 중 산소포화도 평균은?",
        "oxygen_saturations",
        Decimal("95.73"),
        ("95.7",),
    ),
)


def check_tracing_disabled() -> None:
    try:
        ensure_tracing_disabled()
    except TracingEnabledError as e:
        raise SystemExit(
            f"LangSmith 추적이 켜져 있어 실행하지 않습니다: {', '.join(e.sources)}"
        ) from None


def checked_database_url(raw_url: str | None) -> URL:
    if not raw_url:
        raise SystemExit(
            "HEALTH_TEST_DATABASE_URL 에 Alembic 을 적용한 합성 전용 DB 를 지정하세요"
        )
    url = make_url(raw_url)
    if (
        url.drivername != "postgresql+asyncpg"
        or not (url.database or "").startswith("candiy_health_test")
        or url.host not in ("127.0.0.1", "localhost")
    ):
        raise SystemExit("localhost 의 candiy_health_test 접두사 DB 만 허용합니다")
    return url


def check_llm_base_url(base_url: str) -> None:
    internal = urlsplit(Settings.model_fields["llm_base_url"].default).hostname
    configured = urlsplit(base_url)
    # 도구 결과(건강 수치)가 프롬프트에 들어가므로 사내 LLM 이 아니면 호출하지 않는다
    if configured.scheme != "https" or configured.hostname != internal:
        raise SystemExit(
            f"LLM_BASE_URL 이 사내 LLM({internal})이 아니어서 실행하지 않습니다"
        )


def llm_settings() -> Settings:
    try:
        settings = get_settings()
    except ValidationError:
        raise SystemExit(
            "LLM_API_KEY 가 없습니다. 저장소 루트 .env 에 넣은 뒤 다시 실행하세요"
        ) from None
    check_llm_base_url(settings.llm_base_url)
    return settings


async def seed(
    factory: async_sessionmaker[AsyncSession], owner: UUID, now: datetime
) -> None:
    today = now.astimezone(KST).date()
    async with factory.begin() as session:
        for metric in METRICS:
            session.add(
                SyncState(
                    end_user_id=owner,
                    metric=metric,
                    initial_sync_done=True,
                    first_data_at=now - timedelta(days=13),
                    last_data_at=now,
                    last_success_at=now,
                    last_status="success",
                )
            )
        for days_ago, value in {
            6: 7980,
            5: 6450,
            4: 0,
            3: 8120,
            1: 7310,
            0: 4210,
        }.items():
            session.add(
                ActivityDaily(
                    end_user_id=owner,
                    date=today - timedelta(days=days_ago),
                    steps=value,
                )
            )
        for days_ago, avg, hours, samples, low, high in [
            (3, 70, 14, 150, 50, 110),
            (2, 76, 15, 2000, 52, 140),
            (1, 95, 1, 8, 60, 130),
        ]:
            session.add(
                HeartDaily(
                    end_user_id=owner,
                    date=today - timedelta(days=days_ago),
                    hr_avg=Decimal(avg),
                    sample_count=samples,
                    hourly_hr={
                        str(hour): dict(avg=avg, min=low, max=high, count=1)
                        for hour in range(hours)
                    },
                    hr_min=low,
                    hr_max=high,
                )
            )
        for days_ago, minutes, oxygen in [
            (6, 418, "96.3"),
            (5, 441, "95.5"),
            (4, 368, None),
            (2, 340, "95.1"),
            (1, 412, "96.0"),
        ]:
            day = today - timedelta(days=days_ago)
            wake = datetime.combine(day, time(7, 25), tzinfo=KST)
            session.add(
                SleepSession(
                    end_user_id=owner,
                    date=day,
                    data_id=f"main-{days_ago}",
                    is_main=True,
                    start_at=wake - timedelta(hours=8),
                    end_at=wake,
                    session_minutes=480,
                    sleep_minutes=minutes,
                    deep_minutes=70,
                    rem_minutes=85,
                    light_minutes=minutes - 155,
                    awake_minutes=18,
                    spo2_avg=Decimal(oxygen) if oxygen else None,
                    spo2_min=Decimal(90) if oxygen else None,
                    spo2_max=Decimal(99) if oxygen else None,
                )
            )


async def cleanup(factory: async_sessionmaker[AsyncSession], owner: UUID) -> None:
    async with factory.begin() as session:
        for model in SYNTHETIC_ROWS:
            await session.execute(delete(model).where(model.end_user_id == owner))


async def run_check(
    check: Check,
    owner: UUID,
    port: HealthQueryPort,
    model: CoachChatModel,
    now: datetime,
) -> bool:
    ctx = CoachContext(
        user_id=str(owner),
        conversation_id="coach-health-check",
        request_id=f"check-{uuid4().hex[:8]}",
    )
    result = await run_coach(
        ctx, check.question, port=port, model=model, sink=ListSink(), now=now
    )
    print(f"[{check.label}] {check.question}")
    if result.finish_reason == "error":
        # 예외 문구에는 SQL·측정값이 섞일 수 있어 오류 code 만 출력한다
        print(f"  실패: {result.error_code}")
        return False
    print(f"  답변: {result.answer}")
    averages = [
        e.snapshot["summary"]["avg"]
        for e in result.evidence
        if e.metric == check.metric
    ]
    same = bool(averages) and all(
        a is not None and Decimal(str(a)) == check.average for a in averages
    )
    shown = next((text for text in check.shown if text in result.answer), None)
    print(
        f"  근거 평균: {averages or '근거 없음'} (기대 {check.average})"
        f" {'일치' if same else '불일치'}"
    )
    print(
        f"  답변 표시값: {shown or '없음'} (기대 {' 또는 '.join(check.shown)})"
        f" {'일치' if shown else '불일치'}"
    )
    return same and shown is not None


async def run_checks(
    factory: async_sessionmaker[AsyncSession], settings: Settings, now: datetime
) -> bool:
    """매번 새 합성 UUID 를 쓰고 끝나면 자신이 만든 행만 지운다."""
    owner = uuid4()
    model = create_chat_model(settings)
    port = HealthQueryPort(factory)
    passed = []
    try:
        await seed(factory, owner, now)
        for check in CHECKS:
            passed.append(await run_check(check, owner, port, model, now))
    finally:
        await cleanup(factory, owner)
        await model.http_async_client.aclose()
    return all(passed)


async def run(url: URL, settings: Settings) -> bool:
    engine = create_async_engine(url)
    try:
        return await run_checks(
            create_sessionmaker(engine), settings, datetime.now(UTC)
        )
    finally:
        await engine.dispose()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    check_tracing_disabled()
    url = checked_database_url(os.environ.get("HEALTH_TEST_DATABASE_URL"))
    settings = llm_settings()
    if not asyncio.run(run(url, settings)):
        raise SystemExit("기대값과 다른 항목이 있습니다")


if __name__ == "__main__":
    main()
