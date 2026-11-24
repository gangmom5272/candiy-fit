"""한 번의 실행: 모든 조회 대상 × 지표를 조회·적재하고, 바뀐 날짜를 집계한다.

사용법 (data/pipeline 에서):
    python -m candiy_pipeline                  # 평소 (첫 연동은 35일, 나머지는 최근 3일)
    python -m candiy_pipeline --full           # 최근 35일 전체 재조회
    python -m candiy_pipeline --metrics steps sleeps
    python -m candiy_pipeline --aggregate-only --reaggregate   # 조회 없이 모든 날짜 다시 집계 (규칙을 바꿨을 때)

출력에는 개수와 오류 종류만 나온다. 측정값은 나오지 않는다.
"""

import argparse
import logging
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

import psycopg

from .aggregator import aggregate_and_mark_ready, dates_to_aggregate
from .candiy_client import CandiyAuthError, CandiyClient
from .config import load_settings
from .loader import mark_failed, sync_metric
from .metrics import METRICS
from .records import KST
from .sync import member_lock
from .targets import load_targets

# 동시에 한 실행만 돈다 (PIPE-07). 다른 기능과 겹치지 않는 임의의 고정 값.
ADVISORY_LOCK_KEY = 726_001

log = logging.getLogger("candiy_pipeline")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="candiy_pipeline")
    parser.add_argument("--full", action="store_true", help="최근 35일 전체 재조회")
    parser.add_argument("--metrics", nargs="+", choices=METRICS, default=list(METRICS))
    parser.add_argument("--today", type=date.fromisoformat, help="기준 날짜 (기본: 오늘, KST)")
    parser.add_argument("--aggregate-only", action="store_true", help="CANDiY 조회 없이 집계만")
    parser.add_argument(
        "--reaggregate", action="store_true", help="바뀐 날짜가 아니라 원본이 있는 모든 날짜를 다시 집계"
    )
    return parser.parse_args(argv)


@dataclass
class RunResult:
    """한 번의 실행 결과. 스케줄러가 실패 알림(alert.py)에 쓴다. 개수와 오류 종류만 담는다 (회원 ID·측정값 없음).

    code: 0 정상 / 1 실행을 멈춘 오류 (CANDiY 인증 실패 등) / 2 일부 지표 적재 실패 또는 회원 집계 실패
    앱 미연동 회원(조회한 지표가 모두 http_404)은 실패로 세지 않고 totals["unlinked_members"]로 따로 센다
    (S15P31A403-232)
    """

    code: int
    totals: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)  # 오류 종류 → 건수 (예: http_503, aggregate:RuntimeError)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx는 요청 URL마다 INFO 로그를 남긴다. 실행 요약만 남기도록 줄인다.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return run(argv).code


def run(argv: list[str] | None = None) -> RunResult:
    args = parse_args(argv)
    today = args.today or datetime.now(KST).date()
    settings = load_settings()

    client = CandiyClient(settings.candiy_base_url, settings.candiy_client_id, settings.candiy_client_secret)
    totals: Counter = Counter()
    errors: Counter = Counter()
    try:
        with psycopg.connect(settings.db_dsn, autocommit=True) as conn:
            if not conn.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,)).fetchone()[0]:
                log.warning("이전 실행이 아직 진행 중이라 이번 실행은 건너뜁니다")
                return RunResult(0)

            targets = load_targets(conn, settings.test_users, include_members=settings.include_members)
            if not settings.include_members:
                log.info("PIPELINE_INCLUDE_MEMBERS=false: 회원(users)은 조회하지 않습니다")
            if settings.include_sample_app:
                log.warning("PIPELINE_INCLUDE_SAMPLE_APP=true: 샘플 앱 기록도 적재합니다 (개발용)")
            log.info(
                "실행 시작: 기준일 %s, 회원 %d명, 지표 %d개, full=%s", today, len(targets), len(args.metrics), args.full
            )

            for target in targets:
                # 같은 회원을 즉시 동기화(sync.py)가 처리 중이면 이번에는 건너뛴다. 다음 실행에서 다시 본다
                with member_lock(conn, target.member_id) as got:
                    if not got:
                        totals["busy_members"] += 1
                        log.info("%s 즉시 동기화가 처리 중이라 건너뜁니다", target.member_id)
                        continue
                    loaded = []
                    member_errors: list[str] = []
                    for metric in [] if args.aggregate_only else args.metrics:
                        r = sync_metric(
                            conn,
                            client,
                            target,
                            metric,
                            today,
                            full=args.full,
                            include_sample_app=settings.include_sample_app,
                        )
                        if r.status == "success":
                            totals["success"] += 1
                            loaded.append(metric)
                        else:
                            member_errors.append(r.error)
                        totals["fetched"] += r.fetched
                        totals["inserted"] += r.inserted
                        totals["updated"] += r.updated
                        totals["unchanged"] += r.unchanged
                        totals["skipped"] += sum(r.skipped.values())
                        log.info(
                            "%s %s %s: 받음 %d, 추가 %d, 갱신 %d, 그대로 %d, 제외 %s%s",
                            target.member_id,
                            metric,
                            r.status,
                            r.fetched,
                            r.inserted,
                            r.updated,
                            r.unchanged,
                            dict(r.skipped) or 0,
                            f", 오류 {r.error}" if r.error else "",
                        )

                    if member_errors and len(member_errors) == len(args.metrics) and set(member_errors) == {"http_404"}:
                        # CANDiY에 이 회원 기록이 하나도 없다 = 앱을 아직 연동하지 않은 회원.
                        # 장애가 아니라서 실패·알림에서 뺀다.
                        # sync_state에는 fail/http_404가 그대로 남아 조회 쪽이 "앱 연동을 확인해 주세요"(409)로 안내한다
                        totals["unlinked_members"] += 1
                    else:
                        totals["fail"] += len(member_errors)
                        errors.update(member_errors)
                    dates = dates_to_aggregate(conn, target.member_id, all_dates=args.reaggregate)
                    try:
                        counts = aggregate_and_mark_ready(conn, target.member_id, dates, loaded)
                    except Exception as e:
                        # 한 회원 집계가 실패해도 다음 회원은 처리한다. 다음 실행에서 같은 날짜를 다시 집계한다.
                        # DB 오류 문구에는 실패한 행의 값이 들어갈 수 있어 오류 종류만 남긴다
                        error = f"aggregate:{type(e).__name__}"
                        mark_failed(conn, target.member_id, loaded, error)
                        totals["aggregate_failed"] += 1
                        errors[error] += 1
                        log.error("%s 집계 실패: %s", target.member_id, error)
                        continue
                    totals["aggregated_dates"] += len(dates)
                    log.info("%s 집계: 날짜 %d개, 만든 행 %s", target.member_id, len(dates), dict(counts) or 0)
    except CandiyAuthError as e:
        log.error("%s. 실행을 멈춥니다", e)
        errors["candiy_auth"] += 1
        return RunResult(1, totals, errors)
    finally:
        client.close()

    log.info(
        "실행 끝: 성공 %d, 실패 %d, 앱 미연동 회원 %d / 받음 %d, 추가 %d, 갱신 %d, 그대로 %d, 제외 %d / "
        "집계 날짜 %d, 집계 실패 회원 %d",
        totals["success"],
        totals["fail"],
        totals["unlinked_members"],
        totals["fetched"],
        totals["inserted"],
        totals["updated"],
        totals["unchanged"],
        totals["skipped"],
        totals["aggregated_dates"],
        totals["aggregate_failed"],
    )
    return RunResult(0 if totals["fail"] == 0 and totals["aggregate_failed"] == 0 else 2, totals, errors)


if __name__ == "__main__":
    sys.exit(main())
