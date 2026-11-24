"""전용 로컬 k6 DB의 합성 데이터 준비와 실제 앱 실행. 운영 설정을 읽지 않는다."""

import argparse
import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import uvicorn
from sqlalchemy import func, select

from app.auth.tokens import issue_access_token
from app.core.config import Settings
from app.core.db import create_engine, create_sessionmaker
from app.health.models import (
    ActivityDaily,
    ExerciseSession,
    HeartDaily,
    SleepSession,
    SyncState,
)
from app.health.schemas import KST
from app.main import create_app
from app.users.models import User

LOCAL = Path(__file__).resolve().parents[1] / "local" / "k6"


def settings() -> Settings:
    config = json.loads((LOCAL / "credentials.json").read_text(encoding="utf-8"))
    return Settings(
        _env_file=None,
        app_env="test",
        postgres_host="127.0.0.1",
        postgres_port=55468,
        postgres_db="candiy_health_test_k6_290",
        postgres_user="k6_synthetic",
        postgres_password=config["db_password"],
        auth_signing_key=config["signing_key"],
        pipeline_enabled=False,
        pipeline_include_members=False,
        pipeline_include_sample_app=False,
    )


async def prepare() -> None:
    LOCAL.mkdir(parents=True, exist_ok=True)
    credential_file = LOCAL / "credentials.json"
    if not credential_file.exists():
        credential_file.write_text(
            json.dumps(
                {
                    "db_password": "k6-local-synthetic-only",
                    "signing_key": secrets.token_urlsafe(48),
                }
            ),
            encoding="utf-8",
        )
    config = settings()
    engine = create_engine(config)
    factory = create_sessionmaker(engine)
    now = datetime.now(UTC)
    end = now.astimezone(KST).date() - timedelta(days=1)
    start = end - timedelta(days=365)
    owners = [uuid4() for _ in range(10)]
    new_owners = [uuid4() for _ in range(10)]
    try:
        async with factory.begin() as db:
            if await db.scalar(select(func.count()).select_from(User)):
                raise RuntimeError(
                    "전용 DB가 비어 있지 않습니다. seed를 중복 실행하지 마세요."
                )
            for owner in new_owners:
                db.add(
                    User(
                        id=owner,
                        social_type="GOOGLE",
                        social_user_id=str(owner),
                        display_name="k6신규합성",
                        birth_year=2000,
                        gender="FEMALE",
                        height_cm=165,
                        weight_kg=55,
                    )
                )
            for index, owner in enumerate(owners):
                db.add(
                    User(
                        id=owner,
                        social_type="GOOGLE",
                        social_user_id=str(owner),
                        display_name=f"k6합성{index}",
                        birth_year=2000,
                        gender="FEMALE",
                        height_cm=165,
                        weight_kg=55,
                    )
                )
                for metric in ("steps", "heart_rates", "sleeps", "exercise_sessions"):
                    db.add(
                        SyncState(
                            end_user_id=owner,
                            metric=metric,
                            initial_sync_done=True,
                            last_status="success",
                            first_data_at=datetime.combine(
                                start, datetime.min.time(), KST
                            ),
                            last_data_at=now,
                            last_success_at=now,
                        )
                    )
                for offset in range(366):
                    day = start + timedelta(days=offset)
                    hour_steps = 250 + index
                    hr = 65 + index
                    wake = datetime.combine(day, datetime.min.time(), KST) + timedelta(
                        hours=7
                    )
                    exercise_start = wake + timedelta(hours=11)
                    db.add_all(
                        [
                            ActivityDaily(
                                end_user_id=owner,
                                date=day,
                                steps=hour_steps * 24,
                                hourly_steps=[hour_steps] * 24,
                                exercise_minutes=30,
                                exercise_count=1,
                            ),
                            HeartDaily(
                                end_user_id=owner,
                                date=day,
                                hr_avg=hr,
                                hr_min=55,
                                hr_max=100,
                                sample_count=1440,
                                hourly_hr={
                                    str(h): {
                                        "avg": hr,
                                        "min": 55,
                                        "max": 100,
                                        "count": 60,
                                    }
                                    for h in range(24)
                                },
                            ),
                            SleepSession(
                                end_user_id=owner,
                                data_id=f"k6-sleep-{offset}",
                                date=day,
                                start_at=wake - timedelta(hours=8),
                                end_at=wake,
                                is_main=True,
                                session_minutes=480,
                                sleep_minutes=450,
                                awake_minutes=30,
                                light_minutes=270,
                                deep_minutes=90,
                                rem_minutes=90,
                                unspecified_minutes=0,
                                unknown_minutes=0,
                            ),
                            ExerciseSession(
                                end_user_id=owner,
                                data_id=f"k6-exercise-{offset}",
                                date=day,
                                code=56,
                                start_at=exercise_start,
                                end_at=exercise_start + timedelta(minutes=30),
                                duration_minutes=30,
                                counts_as_exercise=True,
                            ),
                        ]
                    )
        (LOCAL / "members.json").write_text(
            json.dumps(
                {
                    "owners": [str(owner) for owner in owners],
                    "new_owners": [str(owner) for owner in new_owners],
                    "start": str(start),
                    "end": str(end),
                }
            ),
            encoding="utf-8",
        )
    finally:
        await engine.dispose()
    refresh_tokens()
    print(
        "Prepared 10 recorded + 10 new synthetic members; tokens saved locally (not printed)."
    )


def refresh_tokens() -> None:
    from uuid import UUID

    config = settings()
    fixture = json.loads((LOCAL / "members.json").read_text(encoding="utf-8"))
    now = datetime.now(UTC)
    fixture["tokens"] = [
        issue_access_token(UUID(owner), config, now=now).token
        for owner in fixture.pop("owners")
    ]
    fixture["new_tokens"] = [
        issue_access_token(UUID(owner), config, now=now).token
        for owner in fixture.pop("new_owners")
    ]
    fixture["issued_at"] = now.isoformat()
    (LOCAL / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "tokens", "serve"))
    command = parser.parse_args().command
    if command == "seed":
        asyncio.run(prepare())
    elif command == "tokens":
        refresh_tokens()
    else:
        uvicorn.run(
            create_app(settings()), host="127.0.0.1", port=18064, access_log=False
        )
