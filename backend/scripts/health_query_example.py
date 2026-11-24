"""합성 전용 DB에서 공통 함수를 직접 호출한다: python -m scripts.health_query_example."""

import asyncio
import json
import os
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import create_sessionmaker
from app.health.errors import HealthDataNotReadyError
from app.health.models import (
    ActivityDaily,
    ExerciseSession,
    HeartDaily,
    SleepSession,
    SyncState,
)
from app.health.schemas import KST
from app.health.service import get_metric_result


async def run_example(
    session_factory: async_sessionmaker[AsyncSession], now: datetime
) -> dict:
    """매번 새 합성 UUID를 쓰고 자신이 만든 행만 정리한다."""
    owner, pending = uuid4(), uuid4()
    today = now.astimezone(KST).date()
    metrics = ("steps", "heart_rates", "sleeps", "oxygen_saturations", "exercises")
    try:
        async with session_factory.begin() as session:
            for metric in metrics:
                session.add(
                    SyncState(
                        end_user_id=owner,
                        metric="exercise_sessions" if metric == "exercises" else metric,
                        initial_sync_done=True,
                        first_data_at=now - timedelta(days=13),
                        last_data_at=now,
                        last_success_at=now,
                        last_status="success",
                    )
                )
            session.add(
                SyncState(
                    end_user_id=pending,
                    metric="steps",
                    initial_sync_done=False,
                    last_status="fail",
                    last_error="aggregate:RuntimeError",
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
                exercise_minutes = {3: 30, 1: 31, 0: 15}.get(days_ago, 0)
                counted = bool(exercise_minutes) and days_ago != 1
                day = today - timedelta(days=days_ago)
                session.add(
                    ActivityDaily(
                        end_user_id=owner,
                        date=day,
                        steps=value,
                        exercise_count=int(counted),
                        exercise_minutes=exercise_minutes if counted else 0,
                    )
                )
                if exercise_minutes:
                    start = datetime.combine(day, time.min, tzinfo=KST)
                    session.add(
                        ExerciseSession(
                            end_user_id=owner,
                            data_id=f"exercise-{days_ago}",
                            date=day,
                            code=79,
                            start_at=start,
                            end_at=start + timedelta(minutes=exercise_minutes),
                            duration_minutes=exercise_minutes,
                            avg_speed_mps=Decimal("0.50" if days_ago == 1 else "1.50"),
                            counts_as_exercise=counted,
                        )
                    )
            for days_ago, avg, count, samples, low, high in [
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
                            for hour in range(count)
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
        results = []
        for metric in metrics:
            result = await get_metric_result(
                owner,
                metric,
                today - timedelta(days=6),
                today,
                session_factory=session_factory,
            )
            results.append(result.to_public().model_dump(mode="json"))
        try:
            await get_metric_result(
                pending, "steps", today, today, session_factory=session_factory
            )
        except HealthDataNotReadyError as exc:
            not_ready = dict(
                code=exc.code, reason=exc.reason, metrics=list(exc.metrics)
            )
        else:
            raise AssertionError("pending synthetic member must not return normal data")
        return dict(results=results, not_ready=not_ready)
    finally:
        async with session_factory.begin() as session:
            for model in (
                ActivityDaily,
                ExerciseSession,
                HeartDaily,
                SleepSession,
                SyncState,
            ):
                await session.execute(
                    delete(model).where(model.end_user_id.in_((owner, pending)))
                )


async def main() -> None:
    raw_url = os.environ.get("HEALTH_TEST_DATABASE_URL")
    if not raw_url:
        raise SystemExit(
            "Set HEALTH_TEST_DATABASE_URL to an Alembic-prepared synthetic test database"
        )
    url = make_url(raw_url)
    if (
        url.drivername != "postgresql+asyncpg"
        or not (url.database or "").startswith("candiy_health_test")
        or url.host not in ("127.0.0.1", "localhost")
    ):
        raise SystemExit("Only a local candiy_health_test database is allowed")
    engine = create_async_engine(url)
    try:
        output = await run_example(create_sessionmaker(engine), datetime.now(UTC))
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
