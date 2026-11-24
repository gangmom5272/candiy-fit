"""파이프라인 주기 실행 (PIPE-07, S15P31A403-59).

계속 떠 있으면서 정해진 시각마다 한 번의 실행(__main__)을 한다. Docker Compose의 pipeline 서비스가 이걸 띄운다.

| 실행 | 언제 | 하는 일 |
| 평소 | 시작 직후, 그 뒤 PIPELINE_INTERVAL_MINUTES(기본 15분)마다 | 전체 회원 최근 3일 |
| 전체 다시 조회 | 매일 PIPELINE_FULL_HOUR시(KST, 기본 4시) | 전체 회원 최근 35일 (--full) |

- 주기 실행은 모든 수집의 바탕이다. 회원 단위 즉시 동기화(sync.py)가 연결되면 새 데이터를 더 빨리 반영하지만, 신호가
  실패하면 이 주기만큼 늦어진다. 그래서 폰 자체 동기화 주기(약 15분)와 같은 15분을 기본으로 둔다.
  그보다 짧게 해도 폰이 보내지 않아 빨라지지 않는다. 회원이 많아져 CANDiY 호출이 부담되면 늘린다.
- 전체 다시 조회는 평소 실행도 겸하므로 평소 실행 시계를 다시 맞춘다.
- 한 번의 실행이 실패해도(예외 포함) 스케줄러는 멈추지 않고 다음 시각에 다시 한다. CANDiY 오류의 재시도와
  회원·지표 단위 실패 격리, 동시에 한 실행만 도는 잠금은 __main__과 candiy_client에 있다.
- 실행이 끝날 때마다 결과를 alert.Alerter에 넘겨, 실패가 시작·계속·회복될 때 Mattermost로 알린다 (S15P31A403-82).

사용법 (data/pipeline 에서):
    uv run python -m candiy_pipeline.scheduler
"""

import logging
import sys
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from . import __main__ as pipeline
from .alert import Alerter, mattermost_sender
from .config import load_settings
from .records import KST

log = logging.getLogger("candiy_pipeline.scheduler")


def next_full_at(after: datetime, hour: int) -> datetime:
    """after 이후 처음 오는 hour시 정각 (KST)."""
    candidate = after.astimezone(KST).replace(hour=hour, minute=0, second=0, microsecond=0)
    return candidate if candidate > after else candidate + timedelta(days=1)


def next_run(
    now: datetime, last_regular: datetime | None, next_full: datetime, interval: timedelta
) -> tuple[datetime, bool]:
    """다음 실행 시각과 전체 다시 조회 여부. 처음에는 바로 평소 실행을 한다."""
    regular = now if last_regular is None else last_regular + interval
    if next_full <= regular:
        return next_full, True
    return regular, False


def _where(e: BaseException) -> str:
    """예외가 난 곳 중 이 패키지 안의 마지막 위치 (파일:줄). 라이브러리 안쪽 위치는 고칠 곳이 아니라서 건너뛴다."""
    ours = [f for f in traceback.extract_tb(e.__traceback__) if "candiy_pipeline" in Path(f.filename).parts]
    return f"{Path(ours[-1].filename).name}:{ours[-1].lineno}" if ours else "?"


def run_once(full: bool) -> pipeline.RunResult:
    """한 번의 실행. 예외가 나도 스케줄러가 멈추지 않게 실패 결과로 바꾼다."""
    try:
        return pipeline.run(["--full"] if full else [])
    except Exception as e:
        # 오류 문구는 남기지 않는다. DB 오류의 문구·DETAIL에는 실패한 행의 값(건강데이터)이 들어갈 수 있다
        # (infra/data-export-checklist.md). 고칠 곳을 찾을 수 있게 오류 종류와 우리 코드 안의 발생 위치만 남긴다
        log.error("실행 중 예상하지 못한 오류: %s (%s)", type(e).__name__, _where(e))
        result = pipeline.RunResult(1)
        result.errors[f"unexpected:{type(e).__name__}"] += 1
        return result


def run_forever(
    interval: timedelta,
    full_hour: int,
    *,
    run: Callable[[bool], pipeline.RunResult] = run_once,
    now: Callable[[], datetime] = lambda: datetime.now(KST),
    sleep: Callable[[float], None] = time.sleep,
    max_runs: int | None = None,
    alerter: Alerter | None = None,
) -> None:
    """max_runs는 테스트용. None이면 끝나지 않는다."""
    last_regular: datetime | None = None
    next_full = next_full_at(now(), full_hour)
    runs = 0
    while max_runs is None or runs < max_runs:
        when, full = next_run(now(), last_regular, next_full, interval)
        wait = (when - now()).total_seconds()
        if wait > 0:
            log.info("다음 실행: %s (%s)", when.isoformat(timespec="minutes"), "전체 35일" if full else "최근 3일")
            sleep(wait)
        started = now()
        result = run(full)
        log.info("%s 실행 끝: 종료 코드 %d", "전체" if full else "평소", result.code)
        if alerter is not None:
            alerter.observe(result, full)
        last_regular = started
        if full:
            next_full = next_full_at(started, full_hour)
        runs += 1


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    log.info(
        "스케줄러 시작: 평소 %d분마다, 전체 매일 %d시 (KST), 회원 조회 %s, 실패 알림 %s",
        settings.interval_minutes,
        settings.full_hour,
        "켬" if settings.include_members else "끔",
        "켬" if settings.alert_webhook_url else "끔",
    )
    sender = mattermost_sender(settings.alert_webhook_url) if settings.alert_webhook_url else None
    alerter = Alerter(sender, settings.alert_name)
    run_forever(timedelta(minutes=settings.interval_minutes), settings.full_hour, alerter=alerter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
