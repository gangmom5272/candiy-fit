"""코칭 골든셋 고정 데이터 내보내기 (S15P31A403-163).

합성 데이터를 기준일로 넣은 DB에서 페르소나별 집계 행을 그대로 JSON으로 뽑는다. AI 평가 실행기는 이 파일을
InMemoryHealthData(ai/src/coach_ai/adapters/memory.py)에 넣어 쓴다. 형식은 ai/eval/datasets/README.md "코칭 골든셋".

사용법 (data/pipeline 에서, 로컬 DB에 같은 기준일로 합성 데이터를 넣은 뒤):
    uv run python -m candiy_pipeline.synthetic --today 2026-10-22 --reset --classic
    uv run python -m candiy_pipeline.synthetic --today 2026-10-22 --reset --classic --people synth-edge
    uv run python -m candiy_pipeline.golden --today 2026-10-22
    # 발표 시연 페르소나 synth-11 (수면 코칭 골든셋, S15P31A403-305): --classic 없이 90일, 같은 기준일
    uv run python -m candiy_pipeline.synthetic --today 2026-10-22 --reset --people synth-11
    uv run python -m candiy_pipeline.golden --today 2026-10-22 --people synth-11

- 값 없는 날은 DB 그대로다. 행이 없으면 행 없음, 행은 있는데 값이 비면 null, 0보는 0
- 같은 DB 내용이면 같은 파일이 나온다 (정렬·소수점 고정). data_sha256은 그 칸을 뺀 파일 전체의 sha256
- 시연 페르소나(DEMO_PERSONAS)는 기본 내보내기에 없고 --people로만 고른다. 수면 코칭 계산(calculation-rules.md 11장)이
  최근 14일 + 기준 28일 + 비교 28일을 보므로 90일 데이터로 만들고, 수면 중 심박·산소포화도 기록 수·90% 미만 기록 수와
  하루 심박 측정 수를 더 넣는다. 기존 페르소나 파일은 칸을 늘리지 않는다 (커밋된 파일·해시가 그대로다)
"""

import argparse
import hashlib
import json
import logging
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .config import ROOT, load_settings
from .records import KST
from .synthetic import DEMO_PERSONAS, EDGE_PERSONAS, PERSONAS, SYNTHETIC_DAYS, _at
from .targets import SYNTHETIC_SOCIAL_PREFIX, target_for_test_user
from .windows import INITIAL_DAYS

log = logging.getLogger("candiy_pipeline.golden")

OUT_DIR = ROOT / "ai" / "eval" / "datasets" / "golden" / "fixtures"
# InMemoryHealthData.sync에 넣는 지표. 읽는 쪽이 도구 지표 이름으로 바꾼다
SYNC_METRICS = ("steps", "heart_rates", "oxygen_saturations", "sleeps", "exercise_sessions")

STEPS_SQL = "SELECT date, steps FROM activity_daily WHERE end_user_id = %s AND date <= %s ORDER BY date"
HEART_SQL = """
SELECT date, hr_avg, hr_min, hr_max, hourly_hr FROM heart_daily
WHERE end_user_id = %s AND date <= %s ORDER BY date
"""
SLEEP_SQL = """
SELECT date, start_at, end_at, is_main, sleep_minutes, deep_minutes, rem_minutes, light_minutes, awake_minutes,
       spo2_avg, spo2_min, spo2_max
FROM sleep_session WHERE end_user_id = %s AND date <= %s ORDER BY start_at
"""
# 시연 페르소나만: AI 어댑터(InMemoryHealthData.SleepSession)의 칸 이름으로 더 넣는다.
# spo2_low_times는 어댑터에 칸이 아직 없어 넣지 않는다 (어댑터가 받으면 추가)
SLEEP_EXTRA_SQL = """
SELECT date, start_at, end_at, is_main, sleep_minutes, deep_minutes, rem_minutes, light_minutes, awake_minutes,
       spo2_avg, spo2_min, spo2_max, unspecified_minutes, unknown_minutes,
       hr_avg AS avg_heart_rate, hr_min AS min_heart_rate, hr_max AS max_heart_rate,
       spo2_count, spo2_low_count, spo2_low_times
FROM sleep_session WHERE end_user_id = %s AND date <= %s ORDER BY start_at
"""
HEART_EXTRA_SQL = """
SELECT date, hr_avg, hr_min, hr_max, hourly_hr, sample_count FROM heart_daily
WHERE end_user_id = %s AND date <= %s ORDER BY date
"""
DEMO_IDS = frozenset(p.user_id for p in DEMO_PERSONAS)
EXERCISE_SQL = """
SELECT s.date, s.start_at, s.end_at, s.duration_minutes, s.code, c.name_ko, c.category, s.counts_as_exercise,
       s.title, s.calories_kcal, s.distance_m, s.hr_avg, s.hr_max, s.avg_speed_mps
FROM exercise_session s LEFT JOIN exercise_code c ON c.code = s.code
WHERE s.end_user_id = %s AND s.date <= %s ORDER BY s.start_at
"""
# 시연 페르소나만: 생활 습관 점수 (S15P31A403-357). 칸 이름은 DB lifestyle_score 와 같고
# AI 어댑터(InMemoryHealthData.ScoreDay)가 그대로 읽는다. computed_at 은 넣은 시각이라 내보낼 때마다 달라져
# 고정 데이터의 기준 시각(now)으로 바꿔 넣는다
SCORES_SQL = """
SELECT date, total_score, activity_score, activity_grade, steps_score, exercise_score, sleep_score, sleep_grade,
       heart_score, heart_grade, steps, exercise_minutes, sleep_minutes, sleep_hr_avg, hr_baseline,
       hr_baseline_nights, check_wear
FROM lifestyle_score WHERE end_user_id = %s AND date <= %s ORDER BY date
"""
SYNC_SQL = """
SELECT metric, last_data_at, last_status, last_success_at, first_data_at FROM sync_state
WHERE end_user_id = %s AND metric = ANY(%s)
"""


def _value(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.astimezone(KST).isoformat(timespec="seconds")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, list):
        # 산소포화도 90% 미만 기록 시각 목록 (KST ISO 8601 초 단위, 같은 시각은 그대로)
        return [_value(x) for x in v]
    return v


def _plain(row: dict) -> dict:
    return {k: _value(v) for k, v in row.items()}


def fixture(conn: psycopg.Connection, persona_id: str, now: datetime, member_id: uuid.UUID | None = None) -> dict:
    """한 페르소나의 고정 데이터. 기준 시각(now)의 KST 날짜까지만 넣는다. member_id는 테스트용 (기본: 합성 회원 ID)."""
    member_id = member_id or target_for_test_user(persona_id).member_id
    today = now.astimezone(KST).date()
    demo = persona_id in DEMO_IDS
    with conn.cursor(row_factory=dict_row) as cur:
        steps = cur.execute(STEPS_SQL, (member_id, today)).fetchall()
        heart = cur.execute(HEART_EXTRA_SQL if demo else HEART_SQL, (member_id, today)).fetchall()
        sleep = cur.execute(SLEEP_EXTRA_SQL if demo else SLEEP_SQL, (member_id, today)).fetchall()
        exercise = cur.execute(EXERCISE_SQL, (member_id, today)).fetchall()
        sync = {r["metric"]: r for r in cur.execute(SYNC_SQL, (member_id, list(SYNC_METRICS))).fetchall()}
        scores = cur.execute(SCORES_SQL, (member_id, today)).fetchall() if demo else []
    if not steps and not sleep:
        raise ValueError(f"{persona_id} 데이터가 없습니다. synthetic --today {today} 로 먼저 넣으세요")

    # 기록 시작일: 조회와 같은 규칙 (모든 지표 first_data_at 중 가장 이른 것의 KST 날짜, health-query-contract.md 4장)
    first = [r["first_data_at"] for r in sync.values() if r["first_data_at"] is not None]
    start = min(first).astimezone(KST).date() if first else None
    if demo:
        # 시연 페르소나는 90일 전체가 있어야 한다. --classic(35일)으로 넣으면 11장 기준 기간이 모자라 정답이 바뀐다
        if start is None or start > today - timedelta(days=SYNTHETIC_DAYS - 1):
            raise ValueError(
                f"{persona_id} 데이터가 {SYNTHETIC_DAYS}일보다 짧습니다. "
                f"synthetic --today {today} --reset --people {persona_id} 로 넣으세요 (--classic 없이)"
            )
    elif start is not None and start < today - timedelta(days=INITIAL_DAYS - 1):
        # 골든셋은 예전 방식(35일, 흐트러짐 없음)으로 만든 데이터다. 개발 서버용 90일 데이터로 만들면 정답이 바뀐다
        raise ValueError(
            f"{persona_id} 데이터가 35일보다 깁니다. synthetic --today {today} --reset --classic 로 넣으세요"
        )
    data = {
        "persona": persona_id,
        "user_id": str(member_id),
        "now": _value(now),
        "linked_from": start.isoformat() if start else None,
        "steps": [_plain(r) for r in steps],
        "heart": [
            # hours: hourly_hr의 유효 시간대 수. 기간 평균의 가중치다
            {**_plain({k: v for k, v in r.items() if k != "hourly_hr"}), "hours": len(r["hourly_hr"] or {})}
            for r in heart
        ],
        "sleep": [_plain(r) for r in sleep],
        "exercise": [
            {
                "date": _value(r["date"]),
                "start_at": _value(r["start_at"]),
                "end_at": _value(r["end_at"]),
                "duration_minutes": r["duration_minutes"],
                "exercise_type": r["code"],
                "type_name": r["name_ko"],  # 기준표에 없는 코드면 null
                "category": r["category"],
                "counts_as_exercise": r["counts_as_exercise"],
                "title": r["title"],
                "calories_kcal": _value(r["calories_kcal"]),
                "distance_m": _value(r["distance_m"]),  # 운동 세션 조회 도구의 종류별 거리 (S15P31A403-216)
                "avg_heart_rate": _value(r["hr_avg"]),
                "max_heart_rate": r["hr_max"],
                "avg_speed_mps": _value(r["avg_speed_mps"]),
            }
            for r in exercise
        ],
        "sync": {
            m: {
                "last_data_at": _value(sync[m]["last_data_at"]) if m in sync else None,
                "last_status": sync[m]["last_status"] if m in sync else None,
                "last_success_at": _value(sync[m]["last_success_at"]) if m in sync else None,
            }
            for m in SYNC_METRICS
        },
    }
    if demo:
        data["scores"] = [{**_plain(r), "computed_at": _value(now)} for r in scores]
    return {**data, "data_sha256": sha256(data)}


def check_synthetic_member(conn: psycopg.Connection, persona_id: str) -> None:
    """내보낼 회원이 합성 회원(users.social_user_id = synthetic-<페르소나>)인지 확인한다. 아니면 ValueError."""
    member_id = target_for_test_user(persona_id).member_id
    if not conn.execute("SELECT to_regclass('public.users') IS NOT NULL").fetchone()[0]:
        raise ValueError("users 테이블이 없어 합성 회원인지 확인할 수 없습니다. 마이그레이션 후 다시 실행하세요")
    row = conn.execute("SELECT social_user_id FROM users WHERE id = %s", (member_id,)).fetchone()
    if row is None or row[0] != SYNTHETIC_SOCIAL_PREFIX + persona_id:
        raise ValueError(f"{persona_id}의 회원 ID가 합성 회원이 아닙니다. 합성 데이터만 있는 DB에서 실행하세요")


def sha256(data: dict) -> str:
    """data_sha256 칸을 뺀 내용의 sha256. 키 정렬·공백 없는 JSON으로 계산한다."""
    body = {k: v for k, v in data.items() if k != "data_sha256"}
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    people = [p.user_id for p in PERSONAS + EDGE_PERSONAS]
    parser = argparse.ArgumentParser(prog="candiy_pipeline.golden")
    parser.add_argument("--today", type=date.fromisoformat, required=True, help="synthetic에 준 기준 날짜")
    # 시연 페르소나는 기본값에 없고 직접 고를 때만 내보낸다 (기본 실행 결과는 예전과 같다)
    parser.add_argument("--people", nargs="+", choices=people + sorted(DEMO_IDS), default=people)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    # synthetic은 지난 날짜를 기준일로 주면 그날 21시까지 동기화된 것으로 만든다
    now = _at(args.today, 21)
    settings = load_settings(require_candiy=False)
    # DB 내용을 저장소 파일로 쓰는 경로라 합성 데이터 생성과 같은 환경에서만 돌린다 (운영·미설정 차단)
    if settings.app_env not in {"local", "dev"}:
        parser.error("고정 데이터 내보내기는 APP_ENV=local 또는 dev에서만 실행할 수 있습니다 (미설정·운영 환경 차단)")
    with psycopg.connect(settings.db_dsn) as conn:
        try:
            # 파일을 쓰기 전에 모두 합성 회원인지 먼저 확인한다 (하나라도 아니면 아무것도 쓰지 않는다)
            for persona_id in args.people:
                check_synthetic_member(conn, persona_id)
            for persona_id in args.people:
                data = fixture(conn, persona_id, now)
                write(args.out / f"{persona_id}.json", data)
                log.info(
                    "%s: 걸음 %d일, 수면 %d개, 운동 %d개, sha256 %s",
                    persona_id,
                    len(data["steps"]),
                    len(data["sleep"]),
                    len(data["exercise"]),
                    data["data_sha256"][:12],
                )
        except ValueError as error:
            log.error("%s", error)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
