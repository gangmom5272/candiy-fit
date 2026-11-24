"""회원 한 명만 즉시 동기화 (S15P31A403-58).

새 데이터가 CANDiY에 도착했다는 신호가 오면(SDK 전송 완료 콜백, 또는 화면을 열 때) 그 회원만 바로
CANDiY 조회 → 원본 적재 → 집계 → 생활 습관 점수까지 다시 만든다. 전체 회원 실행(__main__)은 놓친 신호와
며칠 뒤 수정된 기록을 채우는 안전망으로 남는다.

- 조회 기간은 오늘·어제(2일). 처음 동기화하는 지표는 평소처럼 35일
- 지표 13개를 동시에 조회한다. DB 적재는 지금처럼 지표마다 한 트랜잭션
- 같은 회원을 2분 안에 이미 조회했으면 건너뛴다 (화면 새로고침 연타, 신호가 겹칠 때)
- 같은 회원을 전체 실행과 동시에 돌리지 않는다 (회원 단위 advisory lock)
- 합성 데이터 회원은 CANDiY에 기록이 없으므로 조회하지 않는다

사용법 (data/pipeline 에서):
    uv run python -m candiy_pipeline.sync --member <users.id>
    uv run python -m candiy_pipeline.sync --test-user mock-1013 --force

출력에는 개수·시간·오류 종류만 나온다. 측정값은 나오지 않는다.
"""

import argparse
import logging
import sys
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import psycopg

from .aggregator import aggregate_and_mark_ready, dates_to_aggregate
from .candiy_client import CandiyAuthError, CandiyClient
from .config import load_settings
from .loader import mark_failed, sync_metric
from .metrics import METRICS, NO_END_PARAM
from .records import KST
from .targets import SYNTHETIC_SOCIAL_PREFIX, Target, target_for_member, target_for_test_user
from .windows import chunks, query_range

log = logging.getLogger("candiy_pipeline.sync")

# 회원 단위 잠금의 첫 번째 키. 전체 실행 잠금(726_001)과 겹치지 않는 고정 값
MEMBER_LOCK_KEY = 726_002
SYNC_MEMBER_DAYS = 2  # 오늘·어제
RECENT_SYNC_SKIP = timedelta(minutes=2)
MAX_WORKERS = 8


@contextmanager
def member_lock(conn: psycopg.Connection, member_id: uuid.UUID) -> Iterator[bool]:
    """그 회원을 다른 실행이 처리 중이면 False. autocommit 연결에서 쓴다 (세션 단위 잠금)."""
    key = (MEMBER_LOCK_KEY, str(member_id))
    got = conn.execute("SELECT pg_try_advisory_lock(%s, hashtext(%s))", key).fetchone()[0]
    try:
        yield got
    finally:
        if got:
            conn.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", key)


class _PrefetchedClient:
    """지표별 조회를 미리 동시에 해 두고, sync_metric이 부르는 fetch에 그 결과를 돌려준다."""

    def __init__(self, client: CandiyClient, candiy_id: str, plan: list[tuple[str, date, date]]):
        self._client = client
        self._results: dict[tuple[str, str, date, date], object] = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {(m, candiy_id, s, e): pool.submit(client.fetch, m, candiy_id, s, e) for m, s, e in plan}
            for key, future in futures.items():
                try:
                    self._results[key] = future.result()
                except Exception as e:  # 오류도 그대로 돌려줘 sync_metric이 지금처럼 처리하게 한다
                    self._results[key] = e

    def fetch(self, metric: str, candiy_id: str, start: date, end: date) -> list[dict]:
        key = (metric, candiy_id, start, end)
        if key not in self._results:
            return self._client.fetch(metric, candiy_id, start, end)
        result = self._results[key]
        if isinstance(result, Exception):
            raise result
        return result


def _plan(conn: psycopg.Connection, target: Target, today: date) -> list[tuple[str, date, date]]:
    """sync_metric이 부를 조회 구간과 똑같이 만든다."""
    done = dict(
        conn.execute(
            "SELECT metric, initial_sync_done FROM sync_state WHERE end_user_id = %s", (target.member_id,)
        ).fetchall()
    )
    plan = []
    for metric in METRICS:
        start, end = query_range(today, initial=not done.get(metric, False), full=False, recent_days=SYNC_MEMBER_DAYS)
        ranges = [(start, end)] if metric in NO_END_PARAM else chunks(start, end)
        plan += [(metric, s, e) for s, e in ranges]
    return plan


@dataclass
class MemberSyncResult:
    status: str  # synced / skipped_recent / busy / synthetic / members_disabled / auth_failed / aggregate_failed
    fetched: int = 0
    failed_metrics: list[str] = field(default_factory=list)
    aggregated_dates: int = 0
    seconds: float = 0.0


def _is_synthetic(conn: psycopg.Connection, member_id: uuid.UUID) -> bool:
    if not conn.execute("SELECT to_regclass('public.users') IS NOT NULL").fetchone()[0]:
        return False
    row = conn.execute("SELECT social_user_id FROM users WHERE id = %s", (member_id,)).fetchone()
    return row is not None and row[0].startswith(SYNTHETIC_SOCIAL_PREFIX)


def sync_member(
    conn: psycopg.Connection,
    client: CandiyClient,
    target: Target,
    *,
    today: date | None = None,
    force: bool = False,
    include_sample_app: bool = False,
    include_members: bool = True,
) -> MemberSyncResult:
    """회원 한 명을 지금 바로 최신으로 만든다. conn은 autocommit 연결이어야 한다.

    include_members=False(싸피 개발 서버)면 실제 회원(users.id)은 조회하지 않는다. 테스트 사용자만 된다.
    """
    started = time.monotonic()
    today = today or datetime.now(KST).date()

    if not include_members and target.candiy_id == str(target.member_id):
        return MemberSyncResult("members_disabled")

    if _is_synthetic(conn, target.member_id):
        return MemberSyncResult("synthetic")
    if not force:
        last = conn.execute(
            "SELECT max(last_attempt_at) FROM sync_state WHERE end_user_id = %s", (target.member_id,)
        ).fetchone()[0]
        if last is not None and datetime.now(last.tzinfo) - last < RECENT_SYNC_SKIP:
            return MemberSyncResult("skipped_recent")

    with member_lock(conn, target.member_id) as got:
        if not got:
            return MemberSyncResult("busy")
        try:
            prefetched = _PrefetchedClient(client, target.candiy_id, _plan(conn, target, today))
            result = MemberSyncResult("synced")
            loaded = []
            for metric in METRICS:
                r = sync_metric(
                    conn,
                    prefetched,
                    target,
                    metric,
                    today,
                    include_sample_app=include_sample_app,
                    recent_days=SYNC_MEMBER_DAYS,
                )
                result.fetched += r.fetched
                if r.status == "success":
                    loaded.append(metric)
                else:
                    result.failed_metrics.append(f"{metric}:{r.error}")
        except CandiyAuthError:
            return MemberSyncResult("auth_failed", seconds=time.monotonic() - started)
        dates = dates_to_aggregate(conn, target.member_id)
        try:
            aggregate_and_mark_ready(conn, target.member_id, dates, loaded)
        except Exception as e:
            # 오류 문구에는 실패한 행의 값이 들어갈 수 있어 종류만 남긴다. 다음 실행에서 같은 날짜를 다시 집계한다
            error = f"aggregate:{type(e).__name__}"
            mark_failed(conn, target.member_id, loaded, error)
            return MemberSyncResult("aggregate_failed", result.fetched, [error], seconds=time.monotonic() - started)
        result.aggregated_dates = len(dates)
    result.seconds = round(time.monotonic() - started, 2)
    return result


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="candiy_pipeline.sync")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--member", type=uuid.UUID, help="users.id")
    who.add_argument("--test-user", help="테스트 사용자 CANDiY ID (예: mock-1013)")
    parser.add_argument("--force", action="store_true", help="2분 안에 조회했어도 다시 조회")
    args = parser.parse_args(argv)

    settings = load_settings()
    target = target_for_member(args.member) if args.member else target_for_test_user(args.test_user)
    client = CandiyClient(settings.candiy_base_url, settings.candiy_client_id, settings.candiy_client_secret)
    try:
        with psycopg.connect(settings.db_dsn, autocommit=True) as conn:
            r = sync_member(
                conn,
                client,
                target,
                force=args.force,
                include_sample_app=settings.include_sample_app,
                include_members=settings.include_members,
            )
    finally:
        client.close()
    log.info(
        "%s: %s, 받음 %d, 집계 날짜 %d, %.2f초%s",
        target.member_id,
        r.status,
        r.fetched,
        r.aggregated_dates,
        r.seconds,
        f", 실패 {r.failed_metrics}" if r.failed_metrics else "",
    )
    ok = ("synced", "skipped_recent", "busy", "synthetic", "members_disabled")
    return 0 if r.status in ok and not r.failed_metrics else 1


if __name__ == "__main__":
    sys.exit(main())
