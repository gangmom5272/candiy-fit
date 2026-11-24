"""파이프라인 실패 알림 (PIPE-07, S15P31A403-82).

스케줄러가 실행이 끝날 때마다 결과를 넘기면, 실패가 시작될 때·계속될 때·회복될 때 Mattermost 채널로 알린다.

| 상황 | 알림 |
| 정상 → 실패 | 실패 알림 |
| 실패가 계속됨 | REPEAT_EVERY(6시간)마다 한 번 다시 알림. 15분마다 같은 알림이 쌓이지 않게 한다 |
| 실패 → 정상 | 회복 알림 (몇 번, 얼마 동안 실패했는지) |

- 앱 미연동 회원(모든 지표 http_404)은 실패가 아니라 실행 결과에서 이미 빠져 있다 (__main__.RunResult, S15P31A403-232)
- 메시지에는 서버 이름, 실행 종류, 실패 개수, 오류 종류만 넣는다. 회원 ID·측정값·응답 본문은 넣지 않는다
  (건강데이터 반출 금지). 오류 종류는 loader·__main__이 이미 종류만 남긴 값이다 (http_503, aggregate:RuntimeError 등)
- 웹훅 URL(PIPELINE_ALERT_WEBHOOK_URL)은 비밀값이다. 비어 있으면 알림을 보내지 않는다 (로컬·테스트)
- 알림 전송이 실패해도 파이프라인 실행은 멈추지 않는다. 오류 종류만 로그에 남긴다 (오류 문구에 URL이 들어갈 수 있다)
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from .records import KST

log = logging.getLogger("candiy_pipeline.alert")

REPEAT_EVERY = timedelta(hours=6)
SEND_TIMEOUT = 5  # 초


def mattermost_sender(url: str) -> Callable[[str], None]:
    """Mattermost 수신 웹훅으로 글을 보내는 함수."""

    def send(text: str) -> None:
        httpx.post(url, json={"text": text}, timeout=SEND_TIMEOUT).raise_for_status()

    return send


def _errors_text(errors) -> str:
    return ", ".join(f"{kind} {n}건" for kind, n in errors.most_common(5)) or "없음"


@dataclass
class Alerter:
    """실행 결과를 받아 알릴지 정한다. send가 None이면 상태만 따라가고 보내지 않는다."""

    send: Callable[[str], None] | None
    name: str = "파이프라인"  # 메시지 머리에 붙일 서버 이름 (PIPELINE_ALERT_NAME, 예: 개발 서버)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(KST))
    failing_since: datetime | None = None
    failures: int = 0
    last_sent: datetime | None = None

    def observe(self, result, full: bool) -> None:
        """한 번의 실행 결과(__main__.RunResult). full이면 35일 전체 다시 조회 실행."""
        now = self.now()
        if result.code == 0:
            if self.failing_since is not None:
                minutes = int((now - self.failing_since).total_seconds() // 60)
                self._send(f"[{self.name}] 파이프라인 회복: {self.failures}번 연속 실패 후 정상 ({minutes}분 동안)")
            self.failing_since, self.failures, self.last_sent = None, 0, None
            return

        self.failures += 1
        if self.failing_since is None:
            self.failing_since = now
            self._send(self._failure_text("파이프라인 실패", result, full))
        elif now - self.last_sent >= REPEAT_EVERY:
            self._send(self._failure_text(f"파이프라인 실패 계속 ({self.failures}번째)", result, full))

    def _failure_text(self, title: str, result, full: bool) -> str:
        totals = result.totals
        kind = "전체 다시 조회(35일)" if full else "평소 실행(최근 3일)"
        lines = [
            f"[{self.name}] {title}",
            f"- 실행: {kind}, {self.now().strftime('%m-%d %H:%M')} KST, 종료 코드 {result.code}",
            f"- 지표 적재: 성공 {totals['success']}, 실패 {totals['fail']}",
            f"- 회원 집계 실패: {totals['aggregate_failed']}명",
            f"- 오류 종류: {_errors_text(result.errors)}",
        ]
        if result.code == 1:
            lines.append("- 실행이 중간에 멈췄습니다. 서버 로그를 확인하세요")
        return "\n".join(lines)

    def _send(self, text: str) -> None:
        self.last_sent = self.now()
        if self.send is None:
            return
        try:
            self.send(text)
        except Exception as e:
            log.warning("알림 전송 실패: %s", type(e).__name__)
