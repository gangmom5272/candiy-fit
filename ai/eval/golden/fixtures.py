"""골든셋 고정 데이터(fixtures/)와 정답 원형(truth/)을 읽는다.

고정 데이터의 칸은 adapters/memory.py 의 dataclass 와 같다. sync 의 키는 DB 지표 이름이라 SYNC_METRICS 로 도구 지표
이름으로 바꾼다 (eval/datasets/README.md "sync 이름 변환"). data_sha256 은 데이터 쪽 내보내기와 같은 방식(그 칸을 뺀
내용을 키 정렬·공백 없는 JSON 으로)으로 다시 계산해 확인한다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import MISSING, dataclass, fields
from pathlib import Path
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from coach_ai.adapters.memory import (
    ExerciseSession,
    HeartDay,
    InMemoryHealthData,
    ScoreDay,
    SleepSession,
    StepDay,
    SyncState,
)
from coach_ai.tools.periods import DEFAULT_TZ
from coach_ai.tools.schemas import PUBLIC_METRICS, Metric
from eval.golden.items import GOLDEN_DIR

FIXTURES_DIR = GOLDEN_DIR / "fixtures"
TRUTH_DIR = GOLDEN_DIR / "truth"
# README 의 기준 시각. 고정 데이터의 now 와 같아야 하고, run_coach 의 now 와 시스템 프롬프트 날짜도 이 값이다
NOW = dt.datetime(2026, 10, 22, 21, 0, tzinfo=ZoneInfo(DEFAULT_TZ))
SYNC_METRICS: dict[str, Metric] = {
    "steps": "steps",
    "heart_rates": "heart_rate",
    "oxygen_saturations": "oxygen_saturation",
    "sleeps": "sleep",
    "exercise_sessions": "exercise",
}
_TOP_KEYS = {"persona", "user_id", "now", "linked_from", "steps", "heart", "sleep", "exercise", "sync", "data_sha256"}
# 생활 습관 점수 행(lifestyle_score). 이 칸이 없는 파일은 점수 행이 없는 것으로 읽는다.
# 칸 이름은 어댑터의 ScoreDay 와 같다
_OPTIONAL_TOP_KEYS = {"scores"}
_SYNC_KEYS = {"last_data_at", "last_status", "last_success_at"}
_DATE_FIELDS = {"date"}
_TIME_FIELDS = {"start_at", "end_at", "computed_at"}
# KST ISO 8601 초 단위 문자열 목록이나 null
_TIME_LIST_FIELDS = {"spo2_low_times"}
# 과거 정답 원형에 없던 추가 필드만 비교 대상에서 제외한다.
# 정답에 명시된 새 필드와 기존 필드는 그대로 엄격 비교한다.
_ADDED_DETAIL_FIELDS = {
    "daily_ranges": {"sample_count"},
    "nights": {
        "session_minutes",
        "unspecified_minutes",
        "unknown_minutes",
        "avg_heart_rate",
        "min_heart_rate",
        "max_heart_rate",
        "spo2_avg",
        "spo2_min",
        "spo2_max",
        "spo2_count",
        "spo2_low_count",
        # 90% 미만 기록 시각. 칸을 더하기 전 정답 원형에는 없다
        "spo2_low_times",
    },
}
# 과거 정답 원형의 days 에 없던 착용 칸. 정답에 칸이 있으면 그대로 비교한다
_ADDED_DAY_FIELDS = {"wear_hours", "is_well_worn"}
_Row = TypeVar("_Row")


class FixtureError(ValueError):
    """고정 데이터·정답 원형 파일을 실행에 쓸 수 없다."""


@dataclass(frozen=True, slots=True)
class Fixture:
    """한 페르소나의 고정 데이터."""

    persona: str
    user_id: str
    now: dt.datetime
    linked_from: dt.date | None
    steps: tuple[StepDay, ...]
    heart: tuple[HeartDay, ...]
    sleep: tuple[SleepSession, ...]
    exercise: tuple[ExerciseSession, ...]
    sync: dict[Metric, SyncState]
    data_sha256: str
    # 파일에 scores 칸이 없으면 비어 있고, 점수 조회는 저장된 점수가 없는 날로 처리된다
    scores: tuple[ScoreDay, ...] = ()

    def port(self) -> InMemoryHealthData:
        """실행마다 새 어댑터를 만든다. 어댑터는 조회 기록(calls)을 쌓아 실행끼리 섞이지 않게 한다."""
        uid = self.user_id
        return InMemoryHealthData(
            steps={uid: list(self.steps)},
            heart={uid: list(self.heart)},
            sleep={uid: list(self.sleep)},
            exercise={uid: list(self.exercise)},
            scores={uid: list(self.scores)},
            sync={uid: dict(self.sync)},
            linked_from={} if self.linked_from is None else {uid: self.linked_from},
            now=self.now,
        )


def data_sha256(data: dict[str, Any]) -> str:
    """data_sha256 칸을 뺀 내용의 sha256. 데이터 쪽 내보내기(candiy_pipeline.golden.sha256)와 같은 방식이다."""
    body = {k: v for k, v in data.items() if k != "data_sha256"}
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def files_sha256(paths: Iterable[Path]) -> str:
    """여러 파일을 이름 순으로 묶은 sha256. 파일 이름도 넣어 파일이 바뀌거나 빠지면 값이 달라진다."""
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.name):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _date(where: str, value: Any) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise FixtureError(f"{where}: 날짜 {value!r} 를 읽을 수 없다") from None


def _moment(where: str, value: Any) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise FixtureError(f"{where}: 시각 {value!r} 를 읽을 수 없다") from None
    if parsed.tzinfo is None:
        raise FixtureError(f"{where}: 시각 {value!r} 에 시간대가 없다")
    return parsed


def _optional_moment(where: str, value: Any) -> dt.datetime | None:
    return None if value is None else _moment(where, value)


def _moments(where: str, value: Any) -> tuple[dt.datetime, ...] | None:
    """시각 목록을 순서대로 읽는다. 같은 시각이 여러 번 있으면 그대로 둔다."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise FixtureError(f"{where}: 시각 목록 {value!r} 를 읽을 수 없다")
    return tuple(_moment(where, item) for item in value)


def _rows(where: str, raw: Any, cls: type[_Row]) -> tuple[_Row, ...]:
    """행 목록을 dataclass 로 바꾼다. 날짜·시각 문자열은 date·datetime 으로 바꾼다.

    어댑터는 문자열 날짜를 검사하지 않아 그대로 넣으면 모든 날이 기록 없음으로 계산된다.
    """
    if not isinstance(raw, list):
        raise FixtureError(f"{where}: 행 목록이 아니다")
    names = {f.name for f in fields(cls)}
    required = {f.name for f in fields(cls) if f.default is MISSING and f.default_factory is MISSING}
    rows = []
    for index, row in enumerate(raw):
        at = f"{where}[{index}]"
        if not isinstance(row, dict):
            raise FixtureError(f"{at}: 객체가 아니다")
        if extra := sorted(set(row) - names):
            raise FixtureError(f"{at}: 어댑터에 없는 칸 {extra}")
        values = dict(row)
        if cls is HeartDay:
            # 기존 고정 파일과 해시는 보존한다. 과거에 내보내지 않은 측정 수는 알 수 없다.
            values.setdefault("sample_count", None)
        if missing := sorted(required - set(values)):
            raise FixtureError(f"{at}: 빠진 칸 {missing}")
        for name in _DATE_FIELDS & set(values):
            values[name] = _date(at, values[name])
        for name in _TIME_FIELDS & set(values):
            values[name] = _moment(at, values[name])
        for name in _TIME_LIST_FIELDS & set(values):
            values[name] = _moments(at, values[name])
        try:
            rows.append(cls(**values))
        except (TypeError, ValueError) as e:
            raise FixtureError(f"{at}: {e}") from None
    return tuple(rows)


def _sync(where: str, raw: Any) -> dict[Metric, SyncState]:
    if not isinstance(raw, dict):
        raise FixtureError(f"{where}: sync 가 객체가 아니다")
    if unknown := sorted(set(raw) - set(SYNC_METRICS)):
        raise FixtureError(f"{where}: sync 에 변환표에 없는 지표 {unknown}")
    states: dict[Metric, SyncState] = {}
    for name, row in raw.items():
        at = f"{where}.sync.{name}"
        if not isinstance(row, dict) or set(row) != _SYNC_KEYS:
            raise FixtureError(f"{at}: 칸은 {sorted(_SYNC_KEYS)} 이어야 한다")
        if row["last_status"] not in (None, "success", "fail"):
            raise FixtureError(f"{at}: last_status {row['last_status']!r} 를 알 수 없다")
        states[SYNC_METRICS[name]] = SyncState(
            _optional_moment(at, row["last_data_at"]),
            row["last_status"],
            _optional_moment(at, row["last_success_at"]),
        )
    return states


def load_fixture(persona: str, directory: Path = FIXTURES_DIR, *, now: dt.datetime = NOW) -> Fixture:
    """페르소나 고정 데이터를 읽는다. 기준 시각이 now 와 다르거나 data_sha256 이 내용과 다르면 FixtureError."""
    path = directory / f"{persona}.json"
    if not path.exists():
        raise FixtureError(f"{persona}: 고정 데이터 파일이 없다 ({path.name})")
    data = json.loads(path.read_text(encoding="utf-8"))
    where = path.name
    if not isinstance(data, dict) or not _TOP_KEYS <= set(data) <= _TOP_KEYS | _OPTIONAL_TOP_KEYS:
        raise FixtureError(f"{where}: 최상위 칸은 {sorted(_TOP_KEYS)} 이어야 한다 (scores 는 있어도 된다)")
    if data["persona"] != persona:
        raise FixtureError(f"{where}: persona 가 {data['persona']!r} 다")
    if data_sha256(data) != data["data_sha256"]:
        raise FixtureError(f"{where}: data_sha256 이 내용과 다르다")
    fixed_now = _moment(where, data["now"])
    if fixed_now != now:
        raise FixtureError(f"{where}: 기준 시각 {data['now']} 가 README 의 {now.isoformat()} 와 다르다")
    linked_from = None if data["linked_from"] is None else _date(where, data["linked_from"])
    steps = _rows(f"{where}.steps", data["steps"], StepDay)
    heart = _rows(f"{where}.heart", data["heart"], HeartDay)
    sleep = _rows(f"{where}.sleep", data["sleep"], SleepSession)
    exercise = _rows(f"{where}.exercise", data["exercise"], ExerciseSession)
    # 연동 함수는 연동 시작일이 없으면 모든 날을 연동 전으로 본다. 어댑터는 이때 가장 이른 기록 날짜를 쓰므로,
    # 기록이 있는데 연동 시작일이 없는 파일은 연동 함수와 같은 원형을 만들 수 없다
    if linked_from is None and (steps or heart or sleep or exercise):
        raise FixtureError(f"{where}: 기록이 있는데 linked_from 이 null 이다")
    return Fixture(
        persona=persona,
        user_id=str(data["user_id"]),
        now=fixed_now,
        linked_from=linked_from,
        steps=steps,
        heart=heart,
        sleep=sleep,
        exercise=exercise,
        sync=_sync(where, data["sync"]),
        data_sha256=data["data_sha256"],
        scores=_rows(f"{where}.scores", data.get("scores", []), ScoreDay),
    )


def load_fixtures(
    personas: Iterable[str], directory: Path = FIXTURES_DIR, *, now: dt.datetime = NOW
) -> dict[str, Fixture]:
    return {persona: load_fixture(persona, directory, now=now) for persona in sorted(set(personas))}


def truth_key(metric: Metric, start: dt.date, end: dt.date) -> str:
    """정답 원형 파일의 결과 키. 데이터 쪽 정답 만들기(golden_build.key)와 같다."""
    return f"{PUBLIC_METRICS[metric]}/{start.isoformat()}/{end.isoformat()}"


def load_truth(persona: str, directory: Path = TRUTH_DIR, *, now: dt.datetime = NOW) -> dict[str, dict[str, Any]]:
    """페르소나의 정답 원형(키 → 원형)을 읽는다."""
    path = directory / f"{persona}.json"
    if not path.exists():
        raise FixtureError(f"{persona}: 정답 원형 파일이 없다 ({path.name})")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("persona") != persona or not isinstance(data.get("results"), dict):
        raise FixtureError(f"{path.name}: persona·results 칸을 읽을 수 없다")
    if _moment(path.name, data.get("now")) != now:
        raise FixtureError(f"{path.name}: 기준 시각 {data.get('now')} 가 README 의 {now.isoformat()} 와 다르다")
    return data["results"]


def _differences(mine: Any, theirs: Any, path: str = "") -> list[str]:
    """두 JSON 값이 다른 칸의 경로. 1 과 1.0 처럼 타입이 다른 수도 다른 값으로 센다."""
    if isinstance(mine, dict) and isinstance(theirs, dict):
        out = []
        for key in sorted(set(mine) | set(theirs)):
            sub = f"{path}.{key}" if path else key
            out += _differences(mine.get(key), theirs.get(key), sub) if key in mine and key in theirs else [sub]
        return out
    if isinstance(mine, list) and isinstance(theirs, list):
        if len(mine) != len(theirs):
            return [path]
        return [
            p for i, (a, b) in enumerate(zip(mine, theirs, strict=True)) for p in _differences(a, b, f"{path}[{i}]")
        ]
    return [] if type(mine) is type(theirs) and mine == theirs else [path]


async def compare_with_truth(fixture: Fixture, truth: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """고정 데이터를 어댑터로 조회한 원형과 정답 원형을 비교해 다른 원형과 칸을 반환한다.

    도구는 고정 데이터로 조회하고 채점은 정답 원형 값으로 하므로, 둘이 다르면 정답을 도구 결과로 알 수 없을 수 있다.
    """
    port = fixture.port()
    diffs = []
    for key, expected in truth.items():
        public, start, end = key.split("/")
        metric = next(m for m, p in PUBLIC_METRICS.items() if p == public)
        result = await port.get_metric_result(
            fixture.user_id, metric, dt.date.fromisoformat(start), dt.date.fromisoformat(end)
        )
        mine = {**result.snapshot, "user_id": result.user_id} if "user_id" in expected else result.snapshot
        if changed := _differences(_truth_contract(mine, expected), expected):
            diffs.append({"persona": fixture.persona, "key": key, "fields": changed})
    return diffs


def _truth_contract(mine: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """고정 정답의 필드 버전으로 비교하되, 임의의 새 키나 기존 필드 누락을 숨기지 않는다."""
    detail = dict(mine.get("detail", {}))
    expected_detail = expected.get("detail", {})
    for name, added in _ADDED_DETAIL_FIELDS.items():
        rows, expected_rows = detail.get(name), expected_detail.get(name)
        if not isinstance(rows, list) or not isinstance(expected_rows, list) or len(rows) != len(expected_rows):
            continue
        detail[name] = [
            {key: value for key, value in row.items() if key not in added or key in expected_row}
            for row, expected_row in zip(rows, expected_rows, strict=True)
        ]
    contract = {**mine, "detail": detail} if "detail" in mine else dict(mine)
    days, expected_days = mine.get("days"), expected.get("days")
    if isinstance(days, list) and isinstance(expected_days, list) and len(days) == len(expected_days):
        contract["days"] = [
            {key: value for key, value in day.items() if key not in _ADDED_DAY_FIELDS or key in expected_day}
            for day, expected_day in zip(days, expected_days, strict=True)
        ]
    return contract


def truth_paths(personas: Sequence[str], directory: Path = TRUTH_DIR) -> list[Path]:
    return [directory / f"{persona}.json" for persona in sorted(set(personas))]


def fixture_paths(personas: Sequence[str], directory: Path = FIXTURES_DIR) -> list[Path]:
    return [directory / f"{persona}.json" for persona in sorted(set(personas))]
