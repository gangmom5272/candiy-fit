"""회원·지표 하나를 조회해 원본을 적재하고 sync_state를 기록한다 (PIPE-02~04).

규칙: data/docs/calculation-rules.md 7.2
- 고유 키 (end_user_id, metric, data_id). 이미 있으면 last_modified_at이 더 새로울 때만 덮어쓴다
- 회원·지표 하나의 적재는 한 트랜잭션. 실패하면 그 회원·지표는 하나도 바꾸지 않는다
- 걸음은 받은 버전을 모두 step_snapshot에도 남긴다 (시간대별 걸음 계산, 7.3)

sync_state는 두 번에 나눠 쓴다 (S15P31A403-78)
- 적재할 때 (sync_metric): last_attempt_at, last_status, last_error. 적재 성공·실패만 남긴다
- 그 회원 집계가 성공한 트랜잭션 안에서 (mark_ready): initial_sync_done, last_success_at, last_data_at, first_data_at
  조회 쪽은 initial_sync_done을 "조회 준비 완료"(API 명세 5.1 ready), last_data_at을 동기화 기준 시각으로 쓴다.
  적재 때 바로 바꾸면 집계 전·집계 실패 동안 준비 완료인데 집계가 비어 "기록 없음"으로 보인다
- last_data_at = max(least(coalesce(end_at, start_at), last_modified_at)), first_data_at = min(start_at)
"""

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from .candiy_client import CandiyClient, CandiyError
from .metrics import NO_END_PARAM
from .records import RawRecord, SkipRecord, in_range, parse_record
from .targets import Target
from .windows import INITIAL_DAYS, RECENT_DAYS, chunks, query_range


@dataclass
class SyncResult:
    metric: str
    status: str = "success"  # success / fail
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: Counter = field(default_factory=Counter)
    error: str | None = None


UPSERT_SQL = """
INSERT INTO raw_health_record (
    metric, end_user_id, data_id, app, start_at, end_at, payload,
    last_modified_at, source_created_at, source_updated_at, ingested_at
) VALUES (
    %(metric)s, %(end_user_id)s, %(data_id)s, %(app)s, %(start_at)s, %(end_at)s, %(payload)s,
    %(last_modified_at)s, %(source_created_at)s, %(source_updated_at)s, now()
)
ON CONFLICT (end_user_id, metric, data_id) DO UPDATE SET
    app = EXCLUDED.app,
    start_at = EXCLUDED.start_at,
    end_at = EXCLUDED.end_at,
    payload = EXCLUDED.payload,
    last_modified_at = EXCLUDED.last_modified_at,
    -- CANDiY는 다시 전송된 기록을 새 행으로 저장한다. 처음 도착한 시각을 유지한다 (PIPE-08 첫 연동 성공 시각)
    source_created_at = LEAST(raw_health_record.source_created_at, EXCLUDED.source_created_at),
    source_updated_at = EXCLUDED.source_updated_at,
    ingested_at = now()
WHERE raw_health_record.last_modified_at < EXCLUDED.last_modified_at
   OR (
       raw_health_record.app = %(refresh_source)s AND EXCLUDED.app = %(refresh_source)s
       AND raw_health_record.last_modified_at = EXCLUDED.last_modified_at
       AND raw_health_record.payload IS DISTINCT FROM EXCLUDED.payload
   )
RETURNING (xmax = 0) AS inserted
"""

# 걸음 누적값 버전. 같은 버전이 다시 오면 그대로 둔다 (calculation-rules.md 7.3)
STEP_SNAPSHOT_SQL = """
INSERT INTO step_snapshot (end_user_id, data_id, modified_at, step_count)
VALUES (%s, %s, %s, %s)
ON CONFLICT (end_user_id, data_id, modified_at) DO NOTHING
"""

MARK_ATTEMPT_SQL = """
INSERT INTO sync_state (end_user_id, metric, last_attempt_at)
VALUES (%s, %s, now())
ON CONFLICT (end_user_id, metric) DO UPDATE SET last_attempt_at = now()
RETURNING initial_sync_done
"""

MARK_SUCCESS_SQL = """
UPDATE sync_state SET last_status = 'success', last_error = NULL
WHERE end_user_id = %(end_user_id)s AND metric = %(metric)s
"""

MARK_FAIL_SQL = """
UPDATE sync_state SET last_status = 'fail', last_error = %(error)s
WHERE end_user_id = %(end_user_id)s AND metric = ANY(%(metrics)s)
"""

# 원본이 없는 지표도 준비 완료로 만든다 (기록이 없는 것도 확인된 결과다). 그때 last_data_at·first_data_at은 NULL
MARK_READY_SQL = """
UPDATE sync_state s SET
    initial_sync_done = true,
    last_success_at = now(),
    last_data_at = r.last_data_at,
    first_data_at = r.first_data_at
FROM (
    SELECT m.metric,
           max(least(coalesce(h.end_at, h.start_at), h.last_modified_at)) AS last_data_at,
           min(h.start_at) AS first_data_at
    FROM unnest(%(metrics)s::text[]) AS m(metric)
    LEFT JOIN raw_health_record h ON h.end_user_id = %(end_user_id)s AND h.metric = m.metric
    GROUP BY m.metric
) r
WHERE s.end_user_id = %(end_user_id)s AND s.metric = r.metric
"""


def _fetch_records(
    client: CandiyClient,
    target: Target,
    metric: str,
    start: date,
    end: date,
    result: SyncResult,
    include_sample_app: bool,
) -> tuple[list[RawRecord], list[RawRecord]]:
    """가장 새 버전 목록과, 받은 모든 버전 목록 (걸음 누적값 버전 저장용)."""
    # end 파라미터를 못 쓰는 지표는 start 이후 전체가 오므로 나누지 않고 한 번만 부른다
    ranges = [(start, end)] if metric in NO_END_PARAM else chunks(start, end)

    # 실제 회원의 예제 앱 패키지 기록은 우리 수집 앱이 쓴 실측값이다 (metrics.EXCLUDED_APPS)
    include_app = include_sample_app or target.is_member

    latest: dict[str, RawRecord] = {}
    versions: list[RawRecord] = []
    for chunk_start, chunk_end in ranges:
        for raw in client.fetch(metric, target.candiy_id, chunk_start, chunk_end):
            result.fetched += 1
            try:
                record = parse_record(raw, include_sample_app=include_app)
            except SkipRecord as e:
                result.skipped[e.reason] += 1
                continue
            if metric in NO_END_PARAM and not in_range(record, start, end):
                result.skipped["out_of_range"] += 1
                continue
            versions.append(record)
            # 같은 data_id가 여러 번 온다. CANDiY가 다시 전송된 기록을 새 행으로 저장하고, 조회 구간이 하루씩
            # 겹쳐(windows.chunks) 겹친 날의 기록은 두 구간에서 모두 오기 때문이다. 가장 새 버전을 남기되 도착
            # 시각은 가장 이른 것을 쓴다.
            prev = latest.get(record.data_id)
            if prev is None:
                latest[record.data_id] = record
                continue
            newest = record if prev.last_modified_at < record.last_modified_at else prev
            earliest = min(prev.source_created_at, record.source_created_at)
            latest[record.data_id] = replace(newest, source_created_at=earliest)
    return list(latest.values()), versions


def sync_metric(
    conn: psycopg.Connection,
    client: CandiyClient,
    target: Target,
    metric: str,
    today: date,
    *,
    full: bool = False,
    include_sample_app: bool = False,
    recent_days: int = RECENT_DAYS,
    initial_days: int = INITIAL_DAYS,
    refresh_source: str | None = None,
) -> SyncResult:
    # 합성 생성기는 기준일에 따라 같은 수정 시각의 값이 바뀐다. 지정한 출처만 같은 시각의 갱신을 허용한다.
    result = SyncResult(metric=metric)
    key = (target.member_id, metric)

    with conn.transaction():
        initial_done = conn.execute(MARK_ATTEMPT_SQL, key).fetchone()[0]

    start, end = query_range(
        today, initial=not initial_done, full=full, recent_days=recent_days, initial_days=initial_days
    )
    try:
        records, versions = _fetch_records(client, target, metric, start, end, result, include_sample_app)
        with conn.transaction():
            if metric == "steps":
                # 하루 누적값이 갱신된 버전을 모두 남긴다. 시간대별 걸음은 버전 사이 차이로 계산한다
                for v in versions:
                    count = v.payload.get("count")
                    if isinstance(count, int | float):
                        conn.execute(STEP_SNAPSHOT_SQL, (target.member_id, v.data_id, v.last_modified_at, round(count)))
            for record in records:
                row = conn.execute(
                    UPSERT_SQL,
                    {
                        "metric": metric,
                        "end_user_id": target.member_id,
                        "data_id": record.data_id,
                        "app": record.app,
                        "start_at": record.start_at,
                        "end_at": record.end_at,
                        "payload": Jsonb(record.payload),
                        "last_modified_at": record.last_modified_at,
                        "source_created_at": record.source_created_at,
                        "source_updated_at": record.source_updated_at,
                        "refresh_source": refresh_source,
                    },
                ).fetchone()
                if row is None:
                    result.unchanged += 1
                elif row[0]:
                    result.inserted += 1
                else:
                    result.updated += 1
            conn.execute(MARK_SUCCESS_SQL, {"end_user_id": target.member_id, "metric": metric})
    except CandiyError as e:
        result.status, result.error = "fail", e.kind
    except psycopg.Error as e:
        result.status, result.error = "fail", f"db:{type(e).__name__}"

    if result.status == "fail":
        mark_failed(conn, target.member_id, [metric], result.error)
    return result


def mark_ready(conn: psycopg.Connection, member_id, metrics: list[str]) -> None:
    """적재에 성공한 지표를 조회 준비 완료로 만든다. 그 회원 집계와 같은 트랜잭션 안에서 부른다."""
    if metrics:
        conn.execute(MARK_READY_SQL, {"end_user_id": member_id, "metrics": metrics})


def mark_failed(conn: psycopg.Connection, member_id, metrics: list[str], error: str) -> None:
    """지표들을 실패로 남긴다. 준비 상태와 기준 시각은 그대로 두어 기존 집계를 계속 쓸 수 있게 한다."""
    if metrics:
        with conn.transaction():
            conn.execute(MARK_FAIL_SQL, {"end_user_id": member_id, "metrics": metrics, "error": error})
