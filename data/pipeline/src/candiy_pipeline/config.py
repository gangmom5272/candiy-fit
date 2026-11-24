"""설정. 저장소 루트 .env를 읽고, 같은 이름의 환경 변수가 있으면 그것을 쓴다."""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


@dataclass(frozen=True)
class Settings:
    candiy_base_url: str
    candiy_client_id: str
    candiy_client_secret: str
    db_dsn: str
    # 개발용 테스트 사용자 (CANDiY 조회 ID). 운영에서는 비워 둔다.
    test_users: tuple[str, ...]
    # 개발용. 켜면 CANDiY 샘플 앱 기록도 적재한다 (사각 가짜 사용자는 기록이 모두 샘플 앱이다). 운영에서는 끈다.
    include_sample_app: bool = False
    # false면 users 회원을 조회하지 않는다. 싸피 개발 서버는 합성 데이터만 쓰므로 false (실데이터 반출 방지)
    include_members: bool = True
    # 주기 실행 (scheduler.py): 전체 회원 최근 3일 실행 간격(분), 35일 전체 다시 조회 시각(KST 시)
    interval_minutes: int = 15
    full_hour: int = 4
    # 실패 알림 (alert.py): Mattermost 수신 웹훅 URL(비밀값, 비우면 알림 끔), 메시지에 붙일 서버 이름
    alert_webhook_url: str = ""
    alert_name: str = "파이프라인"
    app_env: str = ""
    # 개발 서버 15분 갱신 때 합성 데이터를 다시 넣을 로그인 회원 "회원 UUID:페르소나" 목록 (S15P31A403-217)
    synthetic_members: tuple[str, ...] = ()

    def __repr__(self) -> str:
        # 비밀값이 로그·오류 메시지에 찍히지 않게 한다
        return (
            f"Settings(candiy_base_url={self.candiy_base_url!r}, test_users={self.test_users!r}, "
            f"include_sample_app={self.include_sample_app!r}, include_members={self.include_members!r}, "
            f"alert={'on' if self.alert_webhook_url else 'off'})"
        )


def load_settings(env_path: Path | None = None, *, require_candiy: bool = True) -> Settings:
    """require_candiy=False: CANDiY를 부르지 않는 실행(합성 데이터 적재)은 DB 설정만 있으면 된다."""
    env = _read_env_file(env_path or ROOT / ".env")
    env.update(
        {k: v for k, v in os.environ.items() if k == "APP_ENV" or k.startswith(("CANDIY_", "POSTGRES_", "PIPELINE_"))}
    )

    missing = [k for k in ("CANDIY_BASE_URL", "CANDIY_CLIENT_ID", "CANDIY_CLIENT_SECRET") if not env.get(k)]
    if missing and require_candiy:
        raise RuntimeError(f".env에 값이 없습니다: {', '.join(missing)}")

    db_dsn = (
        # 연결이 안 되면 멈춰 있지 말고 10초 뒤 오류로 끝낸다 (S15P31A403-120)
        f"host={env.get('POSTGRES_HOST', '127.0.0.1')} port={env.get('POSTGRES_PORT', '5432')} connect_timeout=10 "
        f"dbname={env.get('POSTGRES_DB', '')} user={env.get('POSTGRES_USER', '')} "
        f"password={env.get('POSTGRES_PASSWORD', '')}"
    )
    test_users = tuple(u.strip() for u in env.get("PIPELINE_TEST_USERS", "").split(",") if u.strip())

    return Settings(
        candiy_base_url=env.get("CANDIY_BASE_URL", "").rstrip("/"),
        candiy_client_id=env.get("CANDIY_CLIENT_ID", ""),
        candiy_client_secret=env.get("CANDIY_CLIENT_SECRET", ""),
        db_dsn=db_dsn,
        test_users=test_users,
        include_sample_app=env.get("PIPELINE_INCLUDE_SAMPLE_APP", "").lower() == "true",
        include_members=env.get("PIPELINE_INCLUDE_MEMBERS", "true").lower() != "false",
        interval_minutes=int(env.get("PIPELINE_INTERVAL_MINUTES") or 15),
        full_hour=int(env.get("PIPELINE_FULL_HOUR") or 4),
        alert_webhook_url=env.get("PIPELINE_ALERT_WEBHOOK_URL", ""),
        alert_name=env.get("PIPELINE_ALERT_NAME") or "파이프라인",
        app_env=env.get("APP_ENV", ""),
        synthetic_members=tuple(m.strip() for m in env.get("PIPELINE_SYNTHETIC_MEMBERS", "").split(",") if m.strip()),
    )
