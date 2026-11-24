"""건강 테이블 10개. 설계와 컬럼 의미는 data/docs/health-tables.md.

파이프라인(data/pipeline)이 psycopg SQL로 쓰고, 백엔드·AI는 읽기만 한다.
회원(users)과는 end_user_id로 논리 연결만 하고 외래 키를 걸지 않는다 (health-tables.md 2.5).
"""

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

METRICS = (
    "'steps', 'heart_rates', 'oxygen_saturations', 'sleeps', 'exercise_sessions', 'calories_burned', "
    "'distances', 'speeds', 'vo2_maxes', 'weights', 'body_fats', 'basal_metabolic_rates', 'heights'"
)
GRADES = "'excellent', 'stable', 'attention', 'care'"
NOW = text("now()")


def created_now() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=NOW)


# ----- 원본·동기화 -----


class RawHealthRecord(Base):
    """CANDiY API 레코드 1개당 1행."""

    __tablename__ = "raw_health_record"
    __table_args__ = (
        UniqueConstraint("end_user_id", "metric", "data_id"),
        CheckConstraint(f"metric IN ({METRICS})", name="metric"),
        Index("ix_raw_user_metric_start", "end_user_id", "metric", "start_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    metric: Mapped[str] = mapped_column(String(32))
    end_user_id: Mapped[UUID]
    data_id: Mapped[str] = mapped_column(String(64))
    app: Mapped[str] = mapped_column(String(128))
    start_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    last_modified_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    source_created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[dt.datetime] = created_now()


class SyncState(Base):
    """동기화 상태: (사용자, 지표)마다 1행."""

    __tablename__ = "sync_state"
    __table_args__ = (
        CheckConstraint(f"metric IN ({METRICS})", name="metric"),
        CheckConstraint("last_status IN ('success', 'fail')", name="last_status"),
    )

    end_user_id: Mapped[UUID] = mapped_column(primary_key=True)
    metric: Mapped[str] = mapped_column(String(32), primary_key=True)
    initial_sync_done: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_data_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    first_data_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)


class StepSnapshot(Base):
    """걸음 누적값 버전. 버전 사이 차이로 시간대별 걸음을 만든다 (health-tables.md 3.9)."""

    __tablename__ = "step_snapshot"

    end_user_id: Mapped[UUID] = mapped_column(primary_key=True)
    data_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    modified_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    step_count: Mapped[int] = mapped_column(Integer)
    ingested_at: Mapped[dt.datetime] = created_now()


# ----- 집계 -----


class ActivityDaily(Base):
    """활동: (사용자, 하루)마다 1행. 칼로리·거리는 운동 중 값만 온다 (candiy-api.md 6.6)."""

    __tablename__ = "activity_daily"

    end_user_id: Mapped[UUID] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    steps: Mapped[int | None] = mapped_column(Integer)
    hourly_steps: Mapped[list[int | None] | None] = mapped_column(ARRAY(Integer))
    exercise_calories_kcal: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    exercise_distance_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    exercise_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    exercise_minutes: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    computed_at: Mapped[dt.datetime] = created_now()


class ExerciseSession(Base):
    """활동: (사용자, 운동 1회)마다 1행."""

    __tablename__ = "exercise_session"
    __table_args__ = (Index("ix_exercise_user_date", "end_user_id", "date"),)

    end_user_id: Mapped[UUID] = mapped_column(primary_key=True)
    data_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date)
    code: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(128))
    start_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    calories_kcal: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    distance_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    avg_speed_mps: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    hr_avg: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    hr_max: Mapped[int | None] = mapped_column(Integer)
    # 운동 시간에 넣는 세션인지 (S15P31A403-116). 걷기는 평균 시속 4.0km 이상만 (score-rules.md 2.3).
    # 파이프라인이 항상 판정해 넣는다. 기본값 true는 판정 없이 넣은 행(이전 데이터·테스트)이 예전 의미를 유지하게 한다
    counts_as_exercise: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )
    computed_at: Mapped[dt.datetime] = created_now()


class ExerciseCode(Base):
    """기준표: 운동 종류 (Health Connect EXERCISE_TYPE_*). 행은 마이그레이션이 넣는다."""

    __tablename__ = "exercise_code"
    __table_args__ = (
        CheckConstraint(
            "category IN ('cardio', 'strength', 'flexibility', 'other')",
            name="category",
        ),
    )

    code: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name_en: Mapped[str] = mapped_column(String(64))
    name_ko: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(16))


class SleepSession(Base):
    """수면: (사용자, 수면 1회)마다 1행. date는 깬 날짜 (KST)."""

    __tablename__ = "sleep_session"
    __table_args__ = (
        Index("ix_sleep_user_date", "end_user_id", "date"),
        # 시각 목록이 있으면 길이가 90% 미만 측정 수와 같다 (S15P31A403-302)
        CheckConstraint(
            "spo2_low_times IS NULL OR "
            "(spo2_low_count IS NOT NULL AND cardinality(spo2_low_times) = spo2_low_count)",
            name="spo2_low_times",
        ),
    )

    end_user_id: Mapped[UUID] = mapped_column(primary_key=True)
    data_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date)
    start_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    is_main: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    session_minutes: Mapped[int] = mapped_column(Integer)
    sleep_minutes: Mapped[int | None] = mapped_column(Integer)
    awake_minutes: Mapped[int | None] = mapped_column(Integer)
    light_minutes: Mapped[int | None] = mapped_column(Integer)
    deep_minutes: Mapped[int | None] = mapped_column(Integer)
    rem_minutes: Mapped[int | None] = mapped_column(Integer)
    unspecified_minutes: Mapped[int | None] = mapped_column(Integer)
    unknown_minutes: Mapped[int | None] = mapped_column(Integer)
    sleep_timeline: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    spo2_count: Mapped[int | None] = mapped_column(Integer)
    spo2_avg: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    spo2_min: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    spo2_max: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    spo2_low_count: Mapped[int | None] = mapped_column(Integer)
    # 90% 미만 측정 시각, 이른 순. NULL = 측정 없음 또는 아직 집계 전, [] = 0회 (S15P31A403-302)
    spo2_low_times: Mapped[list[dt.datetime] | None] = mapped_column(
        ARRAY(DateTime(timezone=True))
    )
    hr_avg: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    hr_min: Mapped[int | None] = mapped_column(Integer)
    hr_max: Mapped[int | None] = mapped_column(Integer)
    computed_at: Mapped[dt.datetime] = created_now()


class HeartDaily(Base):
    """심장: (사용자, 하루)마다 1행."""

    __tablename__ = "heart_daily"

    end_user_id: Mapped[UUID] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    hr_avg: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    hr_min: Mapped[int | None] = mapped_column(Integer)
    hr_max: Mapped[int | None] = mapped_column(Integer)
    sample_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # 샘플이 있는 시간만 키로 둔다: {"9": {"avg", "min", "max", "count"}, ...}
    hourly_hr: Mapped[dict[str, dict[str, Any]] | None] = mapped_column(JSONB)
    computed_at: Mapped[dt.datetime] = created_now()


class BodyDaily(Base):
    """신체: (사용자, 지표, 하루)마다 1행."""

    __tablename__ = "body_daily"
    __table_args__ = (
        CheckConstraint(
            "metric IN ('weights', 'body_fats', 'basal_metabolic_rates', 'heights')",
            name="metric",
        ),
        CheckConstraint("unit IN ('kg', '%', 'kcal/day', 'm')", name="unit"),
    )

    end_user_id: Mapped[UUID] = mapped_column(primary_key=True)
    metric: Mapped[str] = mapped_column(String(32), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    unit: Mapped[str] = mapped_column(String(16))
    measured_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    data_id: Mapped[str] = mapped_column(String(64))
    computed_at: Mapped[dt.datetime] = created_now()


# ----- 파생 -----


class LifestyleScore(Base):
    """생활 습관 점수: (사용자, 하루)마다 1행. 규칙은 data/docs/score-rules.md.

    영역(활동·수면·심박)마다 0~100점과 등급, total_score는 점수가 있는 영역의 평균이며 등급이 없다.
    NULL은 데이터 없음이다 (0점과 구분).
    """

    __tablename__ = "lifestyle_score"
    __table_args__ = (
        CheckConstraint(f"activity_grade IN ({GRADES})", name="activity_grade"),
        CheckConstraint(f"sleep_grade IN ({GRADES})", name="sleep_grade"),
        CheckConstraint(f"heart_grade IN ({GRADES})", name="heart_grade"),
    )

    end_user_id: Mapped[UUID] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    total_score: Mapped[int | None] = mapped_column(Integer)
    steps_score: Mapped[int | None] = mapped_column(Integer)
    exercise_score: Mapped[int | None] = mapped_column(Integer)
    activity_score: Mapped[int | None] = mapped_column(Integer)
    activity_grade: Mapped[str | None] = mapped_column(String(16))
    sleep_score: Mapped[int | None] = mapped_column(Integer)
    sleep_grade: Mapped[str | None] = mapped_column(String(16))
    heart_score: Mapped[int | None] = mapped_column(Integer)
    heart_grade: Mapped[str | None] = mapped_column(String(16))
    check_wear: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    steps: Mapped[int | None] = mapped_column(Integer)
    exercise_minutes: Mapped[int | None] = mapped_column(Integer)
    sleep_minutes: Mapped[int | None] = mapped_column(Integer)
    sleep_hr_avg: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    hr_baseline: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    hr_baseline_nights: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[dt.datetime] = created_now()
