"""종류별(kind) 근거 snapshot의 합성 fixture와 근거 항목 도우미."""

from copy import deepcopy
from typing import Any

from app.coaching.schemas import _KIND_METRIC
from tests.coaching.fakes import synthetic_evidence

# 모든 값은 허구다.
SYNTHETIC_HOURLY_STEPS: dict[str, Any] = {
    "kind": "hourly_steps",
    "period": {"start": "2026-11-04", "end": "2026-11-05"},
    "start_hour": 6,
    "end_hour": 9,
    "days": [
        {
            "date": "2026-11-04",
            "is_in_progress": False,
            "values": [100, 200, 300],
            "total": 600,
            "reason": None,
        },
        {
            "date": "2026-11-05",
            "is_in_progress": True,
            "values": None,
            "total": None,
            "reason": "NO_RECORD",
        },
    ],
    # 하루라도 결측이면 기간 합계는 null (건강 조회 규칙)
    "total": None,
    "data_status": {
        "last_data_at": "2026-11-05T01:00:00Z",
        "synced_until": "2026-11-05T01:00:00Z",
        "last_sync_status": "success",
        "last_success_at": "2026-11-05T01:05:00Z",
        "checked_at": "2026-11-05T01:10:00Z",
    },
}

_NIGHT_NULLS: dict[str, Any] = {
    "avg_heart_rate": None,
    "min_heart_rate": None,
    "max_heart_rate": None,
    "spo2_avg": None,
    "spo2_min": None,
    "spo2_max": None,
    "spo2_count": None,
    "spo2_low_count": None,
    "spo2_low_times": None,
}

SYNTHETIC_SLEEP_SESSIONS: dict[str, Any] = {
    "kind": "sleep_sessions",
    "period": {"start": "2026-11-05", "end": "2026-11-05"},
    "sessions": [
        {
            "session_id": "s-001",
            "is_main": True,
            "computed_at": "2026-11-05T01:00:00Z",
            "date": "2026-11-05",
            "start_at": "2026-11-04T14:10:00Z",
            "end_at": "2026-11-04T21:40:00Z",
            "sleep_minutes": 420,
            "deep_minutes": 80,
            "rem_minutes": 90,
            "light_minutes": 250,
            "awake_minutes": 30,
            "session_minutes": 450,
            "unspecified_minutes": 0,
            "unknown_minutes": 0,
            **_NIGHT_NULLS,
            "avg_heart_rate": 58,
            "min_heart_rate": 51,
            "max_heart_rate": 72,
            "spo2_avg": 96.1,
            "spo2_min": 89,
            "spo2_max": 98,
            "spo2_count": 40,
            "spo2_low_count": 2,
            "spo2_low_times": ["2026-11-04T17:02:00Z", "2026-11-04T19:31:00Z"],
        },
        {
            "session_id": "s-002",
            "is_main": False,
            "computed_at": "2026-11-05T01:00:00Z",
            "date": "2026-11-05",
            "start_at": "2026-11-05T05:00:00Z",
            "end_at": "2026-11-05T05:40:00Z",
            "sleep_minutes": 35,
            "deep_minutes": None,
            "rem_minutes": None,
            "light_minutes": 35,
            "awake_minutes": 5,
            "session_minutes": 40,
            "unspecified_minutes": 0,
            "unknown_minutes": 0,
            **_NIGHT_NULLS,
        },
    ],
    "data_status": {
        "last_sync_status": "success",
        "last_data_at": "2026-11-05T01:00:00Z",
        "last_success_at": "2026-11-05T01:05:00Z",
        "checked_at": "2026-11-05T01:10:00Z",
    },
}

_SNAPSHOTS = {
    "hourly_steps": SYNTHETIC_HOURLY_STEPS,
    "sleep_sessions": SYNTHETIC_SLEEP_SESSIONS,
}


def kind_snapshot(kind: str) -> dict[str, Any]:
    """kind의 합성 snapshot 사본을 돌려준다."""
    return deepcopy(_SNAPSHOTS[kind])


def kind_item(
    kind: str, position: int = 1, *, metric: str | None = None
) -> dict[str, Any]:
    """공통 원형 근거 항목을 바탕으로 metric과 snapshot만 kind 근거로 바꿔 돌려준다.

    기간은 snapshot과 같게 맞춘다.
    """
    item = synthetic_evidence(position).event_item()
    item["metric"] = metric or _KIND_METRIC[kind]
    item["snapshot"] = kind_snapshot(kind)
    item["period"]["start"] = item["snapshot"]["period"]["start"]
    item["period"]["end"] = item["snapshot"]["period"]["end"]
    return item
