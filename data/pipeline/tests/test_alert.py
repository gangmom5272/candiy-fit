"""파이프라인 실패 알림 (S15P31A403-82). 실제로 보내지 않고 보낸 글만 모은다."""

import re
from collections import Counter
from datetime import datetime, timedelta

import httpx

from candiy_pipeline import alert
from candiy_pipeline.__main__ import RunResult
from candiy_pipeline.alert import REPEAT_EVERY, Alerter, mattermost_sender
from candiy_pipeline.records import KST


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 23, 10, 0, tzinfo=KST)

    def now(self):
        return self.t

    def tick(self, minutes=15):
        self.t += timedelta(minutes=minutes)


def failed(code=2):
    return RunResult(
        code,
        Counter(success=120, fail=3, aggregate_failed=1),
        Counter({"http_503": 3, "aggregate:RuntimeError": 1}),
    )


def make():
    clock, sent = Clock(), []
    return Alerter(sent.append, "개발 서버", now=clock.now), clock, sent


def test_success_sends_nothing():
    a, clock, sent = make()
    for _ in range(3):
        a.observe(RunResult(0), full=False)
        clock.tick()
    assert sent == []


def test_failure_once_then_quiet_then_repeat_then_recover():
    a, clock, sent = make()
    a.observe(failed(), full=False)
    assert len(sent) == 1
    assert sent[0].splitlines() == [
        "[개발 서버] 파이프라인 실패",
        "- 실행: 평소 실행(최근 3일), 10-23 10:00 KST, 종료 코드 2",
        "- 지표 적재: 성공 120, 실패 3",
        "- 회원 집계 실패: 1명",
        "- 오류 종류: http_503 3건, aggregate:RuntimeError 1건",
    ]

    # 15분마다 실패가 계속돼도 6시간 동안은 다시 보내지 않는다
    for _ in range(int(REPEAT_EVERY / timedelta(minutes=15)) - 1):
        clock.tick()
        a.observe(failed(), full=False)
    assert len(sent) == 1
    clock.tick()
    a.observe(failed(), full=True)
    assert len(sent) == 2 and sent[1].startswith("[개발 서버] 파이프라인 실패 계속 (25번째)")
    assert "전체 다시 조회(35일)" in sent[1]

    clock.tick()
    a.observe(RunResult(0), full=False)
    assert sent[2] == "[개발 서버] 파이프라인 회복: 25번 연속 실패 후 정상 (375분 동안)"

    # 회복 뒤 새 실패는 다시 처음부터 알린다
    clock.tick()
    a.observe(failed(), full=False)
    assert len(sent) == 4 and sent[3].startswith("[개발 서버] 파이프라인 실패\n")


def test_stopped_run_is_marked():
    a, _, sent = make()
    a.observe(RunResult(1, errors=Counter({"candiy_auth": 1})), full=False)
    assert "종료 코드 1" in sent[0] and "candiy_auth 1건" in sent[0] and "서버 로그를 확인하세요" in sent[0]


def test_message_has_no_ids_or_values():
    # 메시지에는 개수·오류 종류만 들어간다. UUID(회원 ID)가 들어가면 안 된다
    a, _, sent = make()
    a.observe(failed(), full=False)
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", sent[0])


def test_send_failure_does_not_raise():
    def broken(text):
        raise httpx.ConnectError("https://mm.example/hooks/secret-id")

    a = Alerter(broken, now=Clock().now)
    a.observe(failed(), full=False)  # 예외가 밖으로 나오지 않는다
    assert a.failures == 1


def test_disabled_alerter_tracks_state_without_sending():
    a = Alerter(None, now=Clock().now)
    a.observe(failed(), full=False)
    a.observe(RunResult(0), full=False)
    assert a.failures == 0 and a.failing_since is None


def test_mattermost_sender_posts_text(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(alert.httpx, "post", fake_post)
    mattermost_sender("https://mm.example/hooks/x")("안녕")
    assert calls == [("https://mm.example/hooks/x", {"text": "안녕"}, alert.SEND_TIMEOUT)]
