"""본인 즉시 동기화. 동기 작업의 연결은 작업 스레드만 생성·정리한다."""

import asyncio
import logging
from collections.abc import Callable
from functools import partial
from time import monotonic
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import psycopg
from candiy_pipeline.candiy_client import CandiyClient
from candiy_pipeline.metrics import METRICS
from candiy_pipeline.sync import sync_member
from candiy_pipeline.targets import target_for_member

from app.core.config import Settings
from app.health import sync_repository
from app.health.diagnostics import emit_health_event
from app.health.errors import HealthDataNotLinkedError

SyncStatus = Literal["SYNCED", "SKIPPED_RECENT", "BUSY", "SYNTHETIC", "DISABLED"]
SyncFailure = Literal[
    "CONFIGURATION_ERROR",
    "CANDIY_AUTH_FAILED",
    "METRIC_FAILED",
    "AGGREGATE_FAILED",
    "STORAGE_UNAVAILABLE",
    "TIMEOUT",
    "CAPACITY_EXCEEDED",
    "SHUTTING_DOWN",
]
RESPONSE_WAIT_SECONDS = 10.0
MAX_ACTIVE_SYNCS = 2


class HealthSyncError(Exception):
    def __init__(self, reason: SyncFailure, metrics: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.metrics = tuple(dict.fromkeys(m for m in metrics if m in METRICS))


def _hide_member_urls(record: logging.LogRecord) -> bool:
    # HTTPX의 INFO 요청 URL에는 SDK 회원 ID가 쿼리로 들어간다.
    return not (
        isinstance(record.args, tuple)
        and any(
            isinstance(arg, httpx.URL) and "end_user_id" in arg.params
            for arg in record.args
        )
    )


logging.getLogger("httpx").addFilter(_hide_member_urls)


def run_member_sync(settings: Settings, user_id: UUID) -> SyncStatus:
    if not (settings.pipeline_enabled and settings.pipeline_include_members):
        return "DISABLED"
    try:
        with psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password.get_secret_value(),
            connect_timeout=3,
            options="-c statement_timeout=5000 -c lock_timeout=1000",
            autocommit=True,
        ) as conn:
            if sync_repository.is_synthetic(conn, user_id):
                return "SYNTHETIC"
            try:
                base = urlsplit(settings.candiy_base_url)
                valid_port = base.port is None or base.port > 0
            except ValueError as error:
                raise HealthSyncError("CONFIGURATION_ERROR") from error
            if (
                base.scheme not in ("http", "https")
                or not base.hostname
                or base.username
                or base.password
                or base.query
                or base.fragment
                or not valid_port
                or not settings.candiy_client_id.get_secret_value()
                or not settings.candiy_client_secret.get_secret_value()
            ):
                raise HealthSyncError("CONFIGURATION_ERROR")
            with httpx.Client(
                timeout=5, trust_env=False, follow_redirects=False
            ) as http:
                client = CandiyClient(
                    settings.candiy_base_url.rstrip("/"),
                    settings.candiy_client_id.get_secret_value(),
                    settings.candiy_client_secret.get_secret_value(),
                    http=http,
                    max_retries=0,
                )
                result = sync_member(
                    conn,
                    client,
                    target_for_member(user_id),
                    include_members=settings.pipeline_include_members,
                    include_sample_app=settings.pipeline_include_sample_app,
                )
    except (psycopg.OperationalError, psycopg.InterfaceError) as error:
        raise HealthSyncError("STORAGE_UNAVAILABLE") from error

    if result.status == "auth_failed":
        raise HealthSyncError("CANDIY_AUTH_FAILED")
    if result.status == "aggregate_failed":
        raise HealthSyncError("AGGREGATE_FAILED")
    if set(result.failed_metrics) == {f"{metric}:http_404" for metric in METRICS}:
        raise HealthDataNotLinkedError
    if result.failed_metrics:
        raise HealthSyncError(
            "METRIC_FAILED",
            tuple(item.split(":", 1)[0] for item in result.failed_metrics),
        )
    statuses: dict[str, SyncStatus] = {
        "synced": "SYNCED",
        "skipped_recent": "SKIPPED_RECENT",
        "busy": "BUSY",
        "synthetic": "SYNTHETIC",
        "members_disabled": "DISABLED",
    }
    return statuses[result.status]


class MemberSyncRunner:
    """API 소유의 실행 두 자리. 응답 취소 후에도 실제 작업 종료까지 자리를 유지한다."""

    def __init__(
        self,
        settings: Settings,
        *,
        worker: Callable[[Settings, UUID], SyncStatus] = run_member_sync,
    ) -> None:
        self.settings = settings
        self._worker = worker
        self._tasks: dict[UUID, asyncio.Task[SyncStatus]] = {}
        self._closing = False

    async def sync(self, user_id: UUID, request_id: str) -> SyncStatus:
        if self._closing:
            raise HealthSyncError("SHUTTING_DOWN")
        if not (
            self.settings.pipeline_enabled and self.settings.pipeline_include_members
        ):
            return "DISABLED"
        if user_id in self._tasks:
            return "BUSY"
        if len(self._tasks) >= MAX_ACTIVE_SYNCS:
            raise HealthSyncError("CAPACITY_EXCEEDED")
        task = asyncio.create_task(
            asyncio.to_thread(self._worker, self.settings, user_id)
        )
        self._tasks[user_id] = task
        task.add_done_callback(
            partial(self._finished, user_id, request_id, monotonic())
        )
        try:
            return await asyncio.wait_for(asyncio.shield(task), RESPONSE_WAIT_SECONDS)
        except TimeoutError as error:
            raise HealthSyncError("TIMEOUT") from error

    def _finished(
        self,
        user_id: UUID,
        request_id: str,
        started: float,
        task: asyncio.Task[SyncStatus],
    ) -> None:
        self._tasks.pop(user_id)
        try:
            status = task.result()
        except HealthSyncError as error:
            status = error.reason
        except HealthDataNotLinkedError as error:
            status = error.code
        except sync_repository.MemberRemovedError:
            status = "MEMBER_REMOVED"
        except asyncio.CancelledError:
            status = "CANCELLED"
        except Exception:
            # 예외 클래스 이름도 임의 문자열일 수 있으므로 고정 분류만 기록한다.
            status = "unexpected"
        emit_health_event(
            "member_sync",
            request_id=request_id,
            result=status,
            seconds=monotonic() - started,
        )

    async def aclose(self) -> None:
        self._closing = True
        pending = asyncio.gather(*self._tasks.values(), return_exceptions=True)
        cancelled = False
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                # 종료 요청 취소도 스레드의 연결 소유권을 넘기지 않는다.
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
