"""기존 운동 요약·추이에만 붙이는 활동값. 공통 결과와 원형은 변경하지 않는다."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from coach_ai.ports import MetricResult, ResultExerciseActivityDay
from coach_ai.tools.outcomes import state_fact
from coach_ai.tools.results import ExerciseActivityPoint, ExerciseActivityStatistic, Value
from coach_ai.tools.rounding import round_half_up

FIELDS = ("exercise_minutes", "exercise_count", "exercise_calories_kcal", "exercise_distance_m")
BASIS = (
    "시간·횟수는 인정 운동만 포함한다. 칼로리·거리는 미인정 운동까지 포함한 저장 운동 기록의 값이며 "
    "부분 측정 합계일 수 있다. 하루 전체 소모 칼로리·이동 거리가 아니다. "
    "종류별 세션 조회의 거리는 인정 세션만 포함하므로 기준이 다르다."
)
UNAVAILABLE = "추가 운동 수치(횟수·칼로리·거리)를 제공받지 못했다. 0이나 운동 기록 없음이라는 뜻이 아니다."
_REASONS = {"BEFORE_LINK": "연동 전", "UNSYNCED": "동기화 전", "NO_RECORD": "기록 없음", "NULL_VALUE": "기록 없음"}


def _value(name: str, raw: Decimal | int | None) -> Value | None:
    if raw is None:
        return None
    if name == "exercise_distance_m":
        value = round_half_up(Decimal(str(raw)) / 1000, 1)
        return Value(value=float(raw), display=f"{value:.1f}km (미인정 포함)")
    value = round_half_up(raw)
    unit = {"exercise_minutes": "분", "exercise_count": "회", "exercise_calories_kcal": "kcal"}[name]
    basis = " (미인정 포함)" if name == "exercise_calories_kcal" else ""
    return Value(value=int(value), display=f"{value:,.0f}{unit}{basis}")


def _reasons(day: ResultExerciseActivityDay) -> dict[str, str]:
    return {
        name: reason
        for name, reason in zip(FIELDS, (day.reason, day.reason, day.calories_reason, day.distance_reason), strict=True)
        if reason is not None
    }


def _point(day: ResultExerciseActivityDay) -> dict[str, Any]:
    missing = {name: state_fact(_REASONS[reason]).fact for name, reason in _reasons(day).items()}
    return ExerciseActivityPoint(
        date=day.date,
        **{name: _value(name, getattr(day, name)) for name in FIELDS},
        is_in_progress=day.is_in_progress,
        missing=missing,
    ).model_dump(mode="json")


def extend(payload: dict[str, Any], row: MetricResult, *, daily: bool) -> dict[str, Any]:
    """기존 payload에 운동 전용 필드만 추가하고 호출 전체 상태는 유지한다."""
    activity = row.exercise_activity()
    if activity is None:
        return {**payload, "exercise_activity_note": UNAVAILABLE}
    note = BASIS + " 실제 0은 기록값이며 null은 값을 확인할 수 없는 항목이다."
    failed = [name for name, status in activity.source_statuses.items() if status.last_sync_status == "fail"]
    if row.data_status.last_sync_status == "fail" or failed:
        note += " 수집 실패 항목은 이전에 저장된 기록 기준이다."
    extension: dict[str, Any] = {"basis": note, "failed_sources": failed}
    if daily:
        extension["days"] = [_point(day) for day in activity.days]
    else:
        extension["summary"] = {
            name: ExerciseActivityStatistic(
                total=_value(name, getattr(activity.summary, name).total),
                avg=_value(name, getattr(activity.summary, name).avg),
                days_with_data=getattr(activity.summary, name).days_with_data,
            ).model_dump(mode="json")
            for name in FIELDS
        }
        for statistic in extension["summary"].values():
            if statistic["avg"] is not None and statistic["days_with_data"] > 0:
                statistic["avg"]["display"] += f" (기록 {statistic['days_with_data']}일)"
        extension["in_progress"] = [_point(day) for day in activity.days if day.is_in_progress]
        # 최대 366일 요약에서도 날짜 목록 대신 항목·사유별 일수만 전달한다. 오늘은 in_progress에 있다.
        missing = Counter(
            (name, reason) for day in activity.days if not day.is_in_progress for name, reason in _reasons(day).items()
        )
        extension["missing"] = {
            name: {
                reason: {"days": count, "fact": state_fact(_REASONS[reason]).fact}
                for (field, reason), count in missing.items()
                if field == name
            }
            for name in FIELDS
            if any(field == name for field, _ in missing)
        }
    if any(day.is_in_progress for day in activity.days):
        basis = row.data_status.synced_until
        extension["as_of"] = basis.astimezone(ZoneInfo("Asia/Seoul")).isoformat() if basis else None
        extension["progress_note"] = "오늘 값은 진행 중이며 기간 합계·평균에서 제외됐다."
    extension["source_statuses"] = {
        name: status.model_dump(mode="json") for name, status in activity.source_statuses.items()
    }
    # 공통 모델의 note 생성 규칙은 유지하고, 직렬화된 운동 결과에만 집계 범위를 명시한다.
    facts = []
    if failed:
        labels = {"exercise_sessions": "운동 시간·횟수", "calories_burned": "칼로리", "distances": "거리"}
        facts.append("·".join(labels[name] for name in failed) + " 수집 실패로 이전에 저장된 기록 기준이다.")
    elif row.data_status.last_sync_status == "fail":
        facts.append("수집 실패로 이전에 저장된 기록 기준이며 실패 원천은 특정되지 않았다.")
    if payload.get("record_note"):
        facts.append(payload["record_note"])
    facts.append("운동 시간·횟수는 인정 운동, 칼로리·거리는 미인정 운동 포함 기준이다.")
    if not daily:
        counts = []
        for name, label in zip(FIELDS, ("시간", "횟수", "칼로리", "거리"), strict=True):
            statistic = getattr(activity.summary, name)
            if statistic.avg is not None and statistic.days_with_data > 0:
                counts.append(f"{label} {statistic.days_with_data}일")
        if counts:
            facts.append("위 기록 범위는 운동 시간 기준이다. 항목별 평균의 기록 일수: " + ", ".join(counts) + ".")
    if "progress_note" in extension:
        facts.append(extension["progress_note"])
        if extension["as_of"] is not None:
            facts.append(f"운동 기준 시각: {extension['as_of']}.")
    return {**payload, "record_note": " ".join(facts), "exercise_activity": extension}
