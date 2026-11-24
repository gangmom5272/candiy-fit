"""AI 내부 이벤트를 요청별 공개 이벤트로 변환해 해당 연결에 보낸다."""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from app.coaching.connection import EventChannel, log_failure
from app.coaching.repository import EvidenceInfo, MessageInfo
from app.coaching.schemas import (
    PublicStage,
    RequestFailure,
    RequestIds,
    accepted_error_event,
    completed_event,
    delta_event,
    evidence_event,
    partition_evidence,
    status_event,
)

INTERNAL_STAGES: Mapping[str, PublicStage | None] = {
    "thinking": "generating_answer",
    "tool_call": "fetching_health_data",
    "tool_done": None,
    "answering": "generating_answer",
}
logger = logging.getLogger(__name__)


class PublicEventSink:
    """run_coach 내부 이벤트를 공개 status·delta·누적 근거로 바꿔 해당 연결에 보낸다.

    emit은 예외를 던지지 않는다. 송신이 막혀도 답변 생성은 계속된다.
    """

    def __init__(
        self,
        channel: EventChannel,
        ids: RequestIds,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """이벤트를 보낼 연결과 요청 식별자를 받고 eventSeq를 2부터 시작한다."""
        self._channel = channel
        self._ids = ids
        self._monotonic = monotonic
        self._next_event_seq = 2
        self._deltas: list[str] = []
        self._evidence_items: tuple[EvidenceInfo, ...] = ()
        self._last_stage: PublicStage | None = None
        self._finished = False
        self.coach_error: tuple[str, tuple[str, ...]] | None = None
        self.first_status_at: float | None = None
        self.first_delta_at: float | None = None

    @property
    def answer(self) -> str:
        """송신 성공 여부와 관계없이 받은 답변 조각을 이어 붙여 반환한다."""
        return "".join(self._deltas)

    @property
    def next_event_seq(self) -> int:
        """다음에 송신할 공개 이벤트 번호를 반환한다."""
        return self._next_event_seq

    def _send(self, event: dict[str, Any]) -> None:
        """지정된 연결의 큐에 이벤트를 넣고 성공한 송신의 번호를 소비한다."""
        self._channel.send(event)
        self._next_event_seq += 1

    def send_stage(self, stage: PublicStage) -> None:
        """종료 전 마지막 송신 stage와 다를 때만 status를 보내고 최초 시각을 남긴다."""
        if self._finished or stage == self._last_stage:
            return
        self._send(status_event(self._ids, self._next_event_seq, stage))
        self._last_stage = stage
        if self.first_status_at is None:
            self.first_status_at = self._monotonic()

    async def emit(self, event: dict[str, Any]) -> None:
        """내부 이벤트와 누적 근거를 송신하고 잘못된 잠정 근거는 안전하게 기록한다."""
        if self._finished:
            return
        try:
            if not isinstance(event, dict):
                return
            event_type = event.get("type")
            if event_type == "status":
                internal_stage = event.get("stage")
                if isinstance(internal_stage, str):
                    stage = INTERNAL_STAGES.get(internal_stage)
                    if stage is not None:
                        self.send_stage(stage)
            elif event_type == "token":
                delta = event.get("delta")
                if isinstance(delta, str) and delta:
                    self._deltas.append(delta)
                    self._send(delta_event(self._ids, self._next_event_seq, delta))
                    if self.first_delta_at is None:
                        self.first_delta_at = self._monotonic()
            elif event_type == "evidence":
                items = event.get("items")
                if not isinstance(items, list) or not items:
                    return
                partition = partition_evidence(items)
                if partition.skipped > 0:
                    logger.warning(
                        "coaching evidence skipped=%d metrics=%s",
                        partition.skipped,
                        ",".join(partition.skipped_metrics),
                    )
                if not partition.valid:
                    return
                self._evidence_items += partition.valid
                self._send(
                    evidence_event(
                        self._ids, self._next_event_seq, self._evidence_items
                    )
                )
            elif event_type == "error":
                metrics = event.get("metrics")
                if not isinstance(metrics, Sequence) or isinstance(
                    metrics, (str, bytes)
                ):
                    metrics = ()
                self.coach_error = (
                    str(event.get("code")),
                    tuple(metric for metric in metrics if isinstance(metric, str)),
                )
        except Exception as error:
            # sink 실패가 run_coach의 internal 실패가 되지 않도록 이 경계에서 포착한다.
            log_failure(logger, "event sink", error)

    def send_completed(
        self,
        assistant_message: MessageInfo,
        answer: str,
        finished_at: datetime,
        *,
        evidence: Sequence[EvidenceInfo],
    ) -> None:
        """첫 종료 호출이면 최종 저장 결과를 completed로 보내고 이후 이벤트를 막는다."""
        if self._finished:
            return
        event = completed_event(
            self._ids,
            self._next_event_seq,
            assistant_message=assistant_message,
            answer=answer,
            evidence=evidence,
            finished_at=finished_at,
        )
        self._finished = True
        self._send(event)

    def send_error(self, failure: RequestFailure) -> None:
        """첫 종료 호출이면 접수 후 error를 보내고 이후 이벤트를 막는다."""
        if self._finished:
            return
        event = accepted_error_event(self._ids, self._next_event_seq, failure)
        self._finished = True
        self._send(event)
