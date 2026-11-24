"""CANDiY 응답 레코드 → raw_health_record 행 (PIPE-03).

저장하지 않는 필드: _id, user_id, count, value, sync_run_id (health-tables.md 3.1)
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .metrics import EXCLUDED_APPS

KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class RawRecord:
    data_id: str
    app: str
    start_at: datetime
    end_at: datetime | None
    payload: dict
    last_modified_at: datetime
    source_created_at: datetime
    source_updated_at: datetime


class SkipRecord(Exception):
    """적재하지 않는 레코드. 이유만 담고 레코드 내용은 담지 않는다."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _time(record: dict, key: str, *, required: bool = True) -> datetime | None:
    value = record.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise SkipRecord(f"missing_{key}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SkipRecord(f"invalid_{key}") from None
    if parsed.tzinfo is None:
        # 레코드 시각은 한국 시간이다 (candiy-api.md 2장)
        parsed = parsed.replace(tzinfo=KST)
    return parsed


def parse_record(record: dict, *, include_sample_app: bool = False) -> RawRecord:
    app = record.get("app")
    if not isinstance(app, str) or not app:
        raise SkipRecord("missing_app")
    if app in EXCLUDED_APPS and not include_sample_app:
        raise SkipRecord("excluded_app")

    data_id = record.get("data_id")
    if not isinstance(data_id, str) or not data_id:
        raise SkipRecord("missing_data_id")

    # data는 JSON 문자열이라 한 번 더 파싱한다
    data = record.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            raise SkipRecord("invalid_data") from None
    if not isinstance(data, dict):
        raise SkipRecord("invalid_data")

    return RawRecord(
        data_id=data_id,
        app=app,
        start_at=_time(record, "start"),
        end_at=_time(record, "end", required=False),
        payload=data,
        last_modified_at=_time(record, "last_modified_time"),
        source_created_at=_time(record, "created_at"),
        source_updated_at=_time(record, "updated_at"),
    )


def in_range(record: RawRecord, start: date, end: date) -> bool:
    """end 파라미터를 못 쓰는 지표(산소포화도)를 기간으로 거른다. 기준은 측정 시작의 KST 날짜."""
    return start <= record.start_at.astimezone(KST).date() <= end
