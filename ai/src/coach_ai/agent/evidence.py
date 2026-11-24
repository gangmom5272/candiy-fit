"""근거 후보 중 근거로 내보낼 것을 고른다. 지표 원형과 모양이 다른 조회 결과는 kind 근거로 내보낸다."""

from __future__ import annotations

import json
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, get_args

from coach_ai.config import get_settings
from coach_ai.tools.results import PeriodSpan
from coach_ai.tools.schemas import PublicMetric

if TYPE_CHECKING:
    from coach_ai.agent.executor import EvidenceDraft

logger = logging.getLogger(__name__)

# 웹이 처음부터 읽는 지표 근거. 근거 허용 목록(COACH_EVIDENCE_ENABLED)과 관계없이 내보낸다
BASE_EVIDENCE: frozenset[str] = frozenset(get_args(PublicMetric))
# kind 근거로 내보낼 snapshot(맨 앞의 kind 포함)을 공백 없는 JSON 으로 쓴 UTF-8 바이트 수의 상한
KIND_SNAPSHOT_MAX_BYTES = 256 * 1024
# snapshot 이 상한을 넘어 근거를 뺄 때 로그에 쓰는 코드
SNAPSHOT_TOO_LARGE = "snapshot_too_large"


def is_evidence_enabled(name: str, enabled: Collection[str] | None = None) -> bool:
    """name(공개 지표 코드나 kind)의 근거를 내보내는지. 기본 다섯 지표는 늘 참이다.

    enabled 가 없으면 설정의 근거 허용 목록을 읽는다. 기본 다섯 지표는 설정을 읽지 않는다.
    """
    if name in BASE_EVIDENCE:
        return True
    return name in (get_settings().evidence_names if enabled is None else enabled)


@dataclass(frozen=True, slots=True)
class KindEvidenceDraft:
    """지표 원형(API 명세 2.2)과 모양이 다른 조회 결과의 근거 후보. UUID 와 순서는 게이트웨이가 붙인다.

    snapshot 은 포트가 넘긴 값 그대로다 (백엔드 결과 모델을 model_dump(mode="json", exclude={"user_id"}) 한 값).
    근거로 내보낼 때만 맨 앞에 kind 를 넣는다. 대화 API 는 snapshot 의 kind 로 검증할 모양을 고른다.
    """

    tool: str
    # 근거 바깥의 공개 지표 코드. 대화 API 가 kind 마다 정한 지표와 같아야 한다
    metric: str
    kind: str
    period: PeriodSpan
    summary: str
    snapshot: Mapping[str, Any] = field(repr=False)
    # 근거로 내보낼 snapshot 의 바이트 수. KIND_SNAPSHOT_MAX_BYTES 와 같은 방식으로 센다
    size: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, Mapping):
            raise TypeError("kind 근거 snapshot 은 객체여야 한다")
        if "user_id" in self.snapshot:
            raise ValueError("kind 근거 snapshot 에 내부 user_id 가 들어 있다")
        if self.snapshot.get("kind", self.kind) != self.kind:
            raise ValueError("kind 근거 snapshot 의 kind 가 다르다")
        encoded = json.dumps(self.event_snapshot(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        object.__setattr__(self, "size", len(encoded))

    def event_snapshot(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.snapshot}


def with_detail(
    base: EvidenceDraft, kind: str, snapshot: Mapping[str, Any], *, summary: str, metric: str | None = None
) -> tuple[EvidenceDraft, KindEvidenceDraft]:
    """기본 결과와 상세 결과가 함께 나온 호출의 근거 두 개. 기본 근거(지표 원형)가 먼저다.

    상세 근거는 기본 근거와 도구·기간이 같다. 바깥 지표는 metric 이 없으면 기본 근거의 지표다.
    """
    outer = base.metric if metric is None else metric
    return base, KindEvidenceDraft(base.tool, outer, kind, base.period, summary, snapshot)


def emitted_evidence(
    drafts: Sequence[EvidenceDraft | KindEvidenceDraft], *, request_id: str, enabled: Collection[str] | None = None
) -> list[EvidenceDraft | KindEvidenceDraft]:
    """근거로 내보낼 후보를 순서대로 고른다.

    근거 허용 목록에 없는 새 지표·kind 근거는 뺀다. kind 근거는 kind 와 바깥 지표가 모두 허용돼야 한다.
    snapshot 이 상한을 넘는 kind 근거는 빼고 그 사실만 로그에 코드로 기록한다.
    """
    chosen: list[EvidenceDraft | KindEvidenceDraft] = []
    for draft in drafts:
        names = (draft.metric, draft.kind) if isinstance(draft, KindEvidenceDraft) else (draft.metric,)
        if not all(is_evidence_enabled(name, enabled) for name in names):
            continue
        if isinstance(draft, KindEvidenceDraft) and draft.size > KIND_SNAPSHOT_MAX_BYTES:
            logger.warning(
                "coaching evidence dropped code=%s request_id=%s tool=%s kind=%s",
                SNAPSHOT_TOO_LARGE,
                request_id,
                draft.tool,
                draft.kind,
            )
            continue
        chosen.append(draft)
    return chosen


def evidence_snapshot(draft: EvidenceDraft | KindEvidenceDraft) -> Mapping[str, Any]:
    """근거에 담을 snapshot. 지표 근거는 후보의 snapshot 그대로, kind 근거는 맨 앞에 kind 를 넣은 값이다."""
    return draft.event_snapshot() if isinstance(draft, KindEvidenceDraft) else draft.snapshot
