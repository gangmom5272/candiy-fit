"""합성 입력에 직접 넣은 활동 행을 기존 운동 원형의 날짜 판정에 맞춰 확장한다."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from coach_ai.ports import ResultContractError
from coach_ai.tools.rounding import round_half_up


@dataclass(frozen=True)
class ExerciseActivityDay:
    date: dt.date
    exercise_minutes: int
    exercise_count: int
    exercise_calories_kcal: Decimal | None
    exercise_distance_m: Decimal | None
    computed_at: dt.datetime | None


def activity_detail(
    snapshot: dict[str, Any], rows: list[ExerciseActivityDay], statuses: Mapping[str, Any]
) -> dict[str, Any]:
    by_date = {row.date.isoformat(): row for row in rows}
    synced = snapshot["data_status"]["synced_until"]
    synced_date = dt.datetime.fromisoformat(synced).astimezone(ZoneInfo("Asia/Seoul")).date() if synced else None
    fields = ("exercise_minutes", "exercise_count", "exercise_calories_kcal", "exercise_distance_m")
    days = []
    for base in snapshot["days"]:
        row = by_date.get(base["date"])
        if not base["missing"] and (row is None or row.exercise_minutes != base["value"]):
            raise ResultContractError("합성 운동 활동값과 기존 시간 기록이 다르다")
        values = {name: getattr(row, name) if row is not None and not base["missing"] else None for name in fields}
        reason = base["reason"]
        null_reason = (
            "UNSYNCED" if synced_date is None or dt.date.fromisoformat(base["date"]) >= synced_date else "NULL_VALUE"
        )
        days.append(
            {
                "date": base["date"],
                **values,
                "computed_at": row.computed_at.isoformat() if row and row.computed_at else None,
                "is_in_progress": base["is_in_progress"],
                "reason": reason,
                "calories_reason": reason or (null_reason if values["exercise_calories_kcal"] is None else None),
                "distance_reason": reason or (null_reason if values["exercise_distance_m"] is None else None),
            }
        )
    summary = {}
    for name in fields:
        values = [Decimal(str(day[name])) for day in days if not day["is_in_progress"] and day[name] is not None]
        total = sum(values) if values else None
        summary[name] = {
            "total": float(total) if total is not None else None,
            "avg": float(round_half_up(total / len(values), 2)) if values else None,
            "days_with_data": len(values),
        }
    # 시간은 기존 원형 통계가 기준이며 합성 어댑터의 공통 계산을 바꾸지 않는다.
    summary["exercise_minutes"] = {key: snapshot["summary"][key] for key in ("total", "avg", "days_with_data")}
    for day in days:
        for name in ("exercise_calories_kcal", "exercise_distance_m"):
            if day[name] is not None:
                day[name] = float(day[name])
    return {"days": days, "summary": summary, "source_statuses": dict(statuses)}
