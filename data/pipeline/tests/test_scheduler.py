"""주기 실행 (PIPE-07, S15P31A403-59). 시계와 실행을 가짜로 바꿔 순서만 본다."""

import uuid
from datetime import datetime, timedelta

import psycopg
import pytest

from candiy_pipeline import scheduler
from candiy_pipeline.__main__ import RunResult
from candiy_pipeline.alert import Alerter
from candiy_pipeline.config import load_settings
from candiy_pipeline.records import KST
from candiy_pipeline.scheduler import next_full_at, next_run, run_forever, run_once
from candiy_pipeline.sync import sync_member
from candiy_pipeline.targets import load_targets, target_for_member, target_for_test_user

HOUR = timedelta(hours=1)


def kst(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=KST)


def test_next_full_at():
    assert next_full_at(kst(22, 1), 4) == kst(22, 4)
    assert next_full_at(kst(22, 4), 4) == kst(23, 4)  # 정각이면 다음 날
    assert next_full_at(kst(22, 15), 4) == kst(23, 4)


def test_next_run_starts_immediately_then_hourly():
    full = kst(23, 4)
    assert next_run(kst(22, 10), None, full, HOUR) == (kst(22, 10), False)
    assert next_run(kst(22, 10, 5), kst(22, 10), full, HOUR) == (kst(22, 11), False)


def test_next_run_prefers_full_when_due_first():
    assert next_run(kst(23, 3, 30), kst(23, 3, 10), kst(23, 4), HOUR) == (kst(23, 4), True)


class FakeClock:
    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


def test_run_forever_schedule_and_failures_do_not_stop():
    clock = FakeClock(kst(22, 1, 30))
    runs = []

    def run(full):
        runs.append((clock.now(), full))
        clock.t += timedelta(minutes=5)  # 한 번 실행에 5분
        return RunResult(1 if len(runs) == 2 else 0)  # 두 번째 실행이 실패해도 다음 실행으로 넘어간다

    run_forever(HOUR, 4, run=run, now=clock.now, sleep=clock.sleep, max_runs=6)
    assert runs == [
        (kst(22, 1, 30), False),  # 시작 직후
        (kst(22, 2, 30), False),
        (kst(22, 3, 30), False),
        (kst(22, 4), True),  # 4시 전체 다시 조회
        (kst(22, 5), False),  # 전체 실행이 평소 시계를 다시 맞춘다
        (kst(22, 6), False),
    ]


def test_run_forever_alerts_on_failure_and_recovery():
    clock = FakeClock(kst(22, 10))
    codes = iter([0, 2, 2, 0])
    sent = []

    def run(full):
        clock.t += timedelta(minutes=5)
        return RunResult(next(codes))

    alerter = Alerter(sent.append, "개발 서버", now=clock.now)
    run_forever(HOUR, 4, run=run, now=clock.now, sleep=clock.sleep, max_runs=4, alerter=alerter)
    assert [m.splitlines()[0] for m in sent] == ["[개발 서버] 파이프라인 실패", sent[1].splitlines()[0]]
    assert sent[1].startswith("[개발 서버] 파이프라인 회복: 2번 연속 실패 후 정상")


def test_unexpected_error_logs_type_and_place_not_message(monkeypatch, caplog):
    # DB 오류 문구에는 실패한 행의 값이 들어갈 수 있다. 로그에는 오류 종류와 우리 코드 위치만 남는다
    def broken(argv):
        raise psycopg.errors.CheckViolation(
            "new row violates check constraint DETAIL: Failing row contains (72.4, 8123)"
        )

    monkeypatch.setattr(scheduler.pipeline, "run", broken)
    with caplog.at_level("ERROR", logger="candiy_pipeline.scheduler"):
        result = run_once(full=False)
    assert result.code == 1 and result.errors == {"unexpected:CheckViolation": 1}
    # 테스트 파일은 패키지 밖이라 패키지 안 마지막 위치(run_once)가 찍힌다
    assert "CheckViolation (scheduler.py:" in caplog.text
    assert "72.4" not in caplog.text and "Failing row" not in caplog.text and not caplog.records[0].exc_info


def test_settings_from_env_file(tmp_path, monkeypatch):
    for key in (
        "PIPELINE_INCLUDE_MEMBERS",
        "PIPELINE_INTERVAL_MINUTES",
        "PIPELINE_FULL_HOUR",
        "PIPELINE_ALERT_WEBHOOK_URL",
        "PIPELINE_ALERT_NAME",
    ):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "PIPELINE_INCLUDE_MEMBERS=false\nPIPELINE_INTERVAL_MINUTES=15\nPIPELINE_FULL_HOUR=3\n", encoding="utf-8"
    )
    s = load_settings(env, require_candiy=False)
    assert (s.include_members, s.interval_minutes, s.full_hour) == (False, 15, 3)

    env.write_text("", encoding="utf-8")
    s = load_settings(env, require_candiy=False)
    assert (s.include_members, s.interval_minutes, s.full_hour) == (True, 15, 4)  # 기본값
    assert (s.alert_webhook_url, s.alert_name) == ("", "파이프라인")  # 알림 끔

    env.write_text(
        "PIPELINE_ALERT_WEBHOOK_URL=https://mm.example/hooks/secret-id\nPIPELINE_ALERT_NAME=개발 서버\n",
        encoding="utf-8",
    )
    s = load_settings(env, require_candiy=False)
    assert (s.alert_webhook_url, s.alert_name) == ("https://mm.example/hooks/secret-id", "개발 서버")
    assert "secret-id" not in repr(s) and "alert=on" in repr(s)  # 웹훅 URL은 비밀값이라 로그에 찍히지 않는다


# ----- 회원 조회 끄기 (싸피 개발 서버) -----


@pytest.mark.db
def test_members_disabled_skips_users():
    member = uuid.uuid4()
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as conn:
        try:
            conn.execute(
                "INSERT INTO users (id, social_type, social_user_id, display_name, birth_year, gender, height_cm, "
                "weight_kg) VALUES (%s, 'KAKAO', %s, '테스트', 2000, 'MALE', 170, 60)",
                (member, f"real-{member}"),
            )
            on = {t.member_id for t in load_targets(conn, ("mock-1013",))}
            off = {t.member_id for t in load_targets(conn, ("mock-1013",), include_members=False)}
            mock = target_for_test_user("mock-1013").member_id
            assert member in on and mock in on
            assert member not in off and mock in off  # 테스트 사용자만 남는다

            r = sync_member(conn, object(), target_for_member(member), include_members=False)
            assert r.status == "members_disabled"  # CANDiY를 부르지 않는다 (client를 쓰면 실패한다)
        finally:
            conn.execute("DELETE FROM users WHERE id = %s", (member,))
