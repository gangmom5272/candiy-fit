"""수면 코칭 계산 참고 구현 (calculation-rules.md 11장, S15P31A403-305).

골든셋 정답과 AI 도구 검증에 쓴다. AI 도구 처리 함수(get_sleep_records analysis)와 **따로** 짠 계산이라,
두 값이 다르면 어느 한쪽 버그다. 버그가 아닌데 값이 갈리지 않게 둘 다 11.9 "계산 세부"를 따른다.

입력은 DB가 아니라 커밋된 골든셋 고정 데이터(ai/eval/datasets/golden/fixtures/<페르소나>.json)다.
DB 없이 누구나 같은 정답을 다시 만들 수 있다. 칸 이름은 AI 도구 정의 초안(개발팀, 11-06) 3장을 따른다.

사용법 (data/pipeline 에서):
    uv run python -m candiy_pipeline.sleep_reference --persona synth-11
    uv run python -m candiy_pipeline.sleep_reference --persona synth-11 --analysis factors --condition weekend

- 판정은 반올림 전 값으로 한다. 결과 JSON의 수는 표시 자릿수(분·bpm·걸음·비율은 정수, 시각은 HH:MM, 0.5 올림)로
  적는다. 도구가 결과 문장에 쓰는 자릿수와 같아야 채점에서 맞는다 (11.9)
- 산소포화도 90% 미만 시각(spo2_low_times)은 고정 데이터에 칸이 생기면 쓴다. 없으면 횟수만 낸다
"""

import argparse
import json
import statistics
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .config import ROOT
from .records import KST

FIXTURES = ROOT / "ai" / "eval" / "datasets" / "golden" / "fixtures"

MIN_NIGHTS = 4  # 11.2·11.4 (9장 "유효한 주 = 4일")
MIN_WEEKDAY, MIN_WEEKEND = 3, 2  # 11.2 평일·주말 차이
BASELINE_DAYS = 28  # 11.3·11.5 본인 기준
DEEP_BASELINE_MIN = 7  # 11.3 깊은 잠 적음
LONG_SLEEP = 540  # 11.3 긴 수면 (9시간)
LAST_NIGHT_WEEK_DAYS = 7  # 11.6 어젯밤 대 그 전 7일
DAY = 1440


@dataclass(frozen=True)
class Night:
    date: date  # 깬 날짜 (KST)
    start: datetime
    end: datetime
    sleep: int | None
    awake: int | None
    deep: int | None
    hr: float | None
    spo2_count: int | None
    low: int | None
    low_times: tuple[datetime, ...] | None

    @property
    def session(self) -> int:
        # sleep_session.session_minutes와 같은 계산 (rules.minutes_between)
        return round((self.end - self.start).total_seconds() / 60)

    @property
    def efficiency(self) -> float | None:
        return 100 * self.sleep / self.session if self.sleep is not None and self.session else None

    @property
    def deep_ratio(self) -> float | None:
        return 100 * self.deep / self.sleep if self.deep is not None and self.sleep else None


@dataclass(frozen=True)
class Data:
    today: date
    linked_from: date | None
    synced_day: date | None  # 동기화 기준일 (1.3 synced_until: 모든 지표 last_data_at 중 가장 늦은 시각의 KST 날짜)
    nights: dict[date, Night]  # 주 수면만 (11장)
    steps: dict[date, int | None]  # 활동 행이 있는 날 (값이 비면 None)
    heart: dict[date, tuple[float | None, int]]  # 하루 평균 심박, 측정 시간대 수
    exercise_ends: dict[date, list[datetime]]  # 인정 운동만, 시작 날짜별 끝난 시각


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def load(path: Path) -> Data:
    raw = json.loads(path.read_text(encoding="utf-8"))
    nights = {}
    for r in raw["sleep"]:
        if not r["is_main"]:
            continue
        times = r.get("spo2_low_times")
        nights[date.fromisoformat(r["date"])] = Night(
            date=date.fromisoformat(r["date"]),
            start=_dt(r["start_at"]),
            end=_dt(r["end_at"]),
            sleep=r["sleep_minutes"],
            awake=r["awake_minutes"],
            deep=r["deep_minutes"],
            hr=r.get("avg_heart_rate"),
            spo2_count=r.get("spo2_count"),
            low=r.get("spo2_low_count"),
            low_times=None if times is None else tuple(_dt(t) for t in times),
        )
    exercise: dict[date, list[datetime]] = {}
    for r in raw["exercise"]:
        if r["counts_as_exercise"]:
            exercise.setdefault(date.fromisoformat(r["date"]), []).append(_dt(r["end_at"]))
    synced = [_dt(v["last_data_at"]) for v in raw.get("sync", {}).values() if v.get("last_data_at")]
    return Data(
        today=_dt(raw["now"]).astimezone(KST).date(),
        linked_from=date.fromisoformat(raw["linked_from"]) if raw["linked_from"] else None,
        synced_day=max(synced).astimezone(KST).date() if synced else None,
        nights=nights,
        steps={date.fromisoformat(r["date"]): r["steps"] for r in raw["steps"]},
        heart={date.fromisoformat(r["date"]): (r["hr_avg"], r["hours"]) for r in raw["heart"]},
        exercise_ends=exercise,
    )


# ----- 11.9 계산 세부 -----


def show(x: float | None, places: str = "1") -> int | float | None:
    """결과(정답)에 적는 값. 분·bpm·걸음·비율은 정수, 0.5는 올린다 (11.9).

    Python round는 0.5를 짝수 쪽으로 보내 쓰지 않는다 (round(482.5) = 482, 여기서는 483).
    판정은 이 값이 아니라 반올림 전 값으로 한다.
    """
    if x is None:
        return None
    q = Decimal(str(x)).quantize(Decimal(places), rounding=ROUND_HALF_UP)
    return int(q) if places == "1" else float(q)


def clock(minutes: float) -> str:
    m = int(Decimal(str(minutes)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) % DAY
    return f"{m // 60:02d}:{m % 60:02d}"


def minute_of_day(t: datetime) -> float:
    k = t.astimezone(KST)
    return round(k.hour * 60 + k.minute + k.second / 60, 4)


def unwrap(values: list[float]) -> list[float]:
    """자정을 넘는 시각을 이어서 본다: 24시간 원에서 가장 넓게 빈 구간을 자른다 (11.2, ClockRange와 같다).

    자르는 기준과 값의 자릿수를 맞춘다 (다르면 경계 값이 하루 뒤로 넘어간다, 11.9).
    """
    values = [round(v, 4) for v in values]
    s = sorted(set(values))
    gaps = [(s[i] - s[i - 1]) if i else (s[0] + DAY - s[-1]) for i in range(len(s))]
    cut = s[gaps.index(max(gaps))]
    return [v if v >= cut else v + DAY for v in values]


def mean(values: list[float | None]) -> tuple[float | None, int]:
    present = [float(v) for v in values if v is not None]
    return (statistics.mean(present), len(present)) if present else (None, 0)


def average(values: list[float | None]) -> tuple[float | None, int]:
    """11.2 기간 값·11.6 7일 평균. 값이 있는 밤이 4밤 미만이면 평균은 None, 밤 수는 그대로 (11.9)."""
    avg, n = mean(values)
    return (avg if n >= MIN_NIGHTS else None), n


def clock_stats(times: list[datetime]) -> dict:
    """시각 평균·흔들림(표본 표준편차, 분)·밤 수. 4밤 미만이면 평균·흔들림 null."""
    if len(times) < MIN_NIGHTS:
        return {"avg": None, "sd_min": None, "nights": len(times)}
    u = unwrap([minute_of_day(t) for t in times])
    return {
        "avg": clock(statistics.mean(u)),
        "sd_min": show(statistics.stdev(u)),
        "nights": len(u),
        "_sd": statistics.stdev(u),
    }


def gap(nights: list[Night], at: Callable[[Night], float]) -> dict:
    """주말 평균 − 평일 평균 (분, 늦으면 +). 기간 전체를 한 번에 자정 넘김 처리한다 (11.9)."""
    weekday = [n for n in nights if n.date.weekday() < 5]
    weekend = [n for n in nights if n.date.weekday() >= 5]
    if len(weekday) < MIN_WEEKDAY or len(weekend) < MIN_WEEKEND:
        return {"min": None, "weekday_nights": len(weekday), "weekend_nights": len(weekend), "_raw": None}
    u = unwrap([at(n) for n in weekday + weekend])
    raw = statistics.mean(u[len(weekday) :]) - statistics.mean(u[: len(weekday)])
    return {"min": show(raw), "weekday_nights": len(weekday), "weekend_nights": len(weekend), "_raw": raw}


def window(data: Data, end: date, days: int) -> list[Night]:
    """end까지 days일 동안 깬 주 수면 (깬 날짜 기준)."""
    return [data.nights[d] for d in (end - timedelta(days=i) for i in range(days)) if d in data.nights]


def reliable(data: Data, end: date, days: int, nights: int) -> bool:
    """4.1: 기록 3일 이상이고 완성된 날의 절반 이상. 수면은 오늘 깬 것도 완성이다 (1.2). 연동 전 날은 빼고 센다."""
    start = end - timedelta(days=days - 1)
    if data.linked_from and data.linked_from > start:
        start = data.linked_from
    completed = max(0, (end - start).days + 1)
    return nights >= 3 and nights * 2 >= completed


# ----- pattern (11.2 + 11.3) -----


def pattern(data: Data, end: date | None = None, days: int = 14) -> dict:
    end = end or data.today
    nights = window(data, end, days)
    base_end = end - timedelta(days=days)
    base = window(data, base_end, BASELINE_DAYS)
    out = {
        "analysis": "pattern",
        "period": {"start": (end - timedelta(days=days - 1)).isoformat(), "end": end.isoformat()},
        "baseline_period": {
            "start": (base_end - timedelta(days=BASELINE_DAYS - 1)).isoformat(),
            "end": base_end.isoformat(),
        },
        "nights": len(nights),
        "enough_data": len(nights) >= MIN_NIGHTS,
    }
    bed = clock_stats([n.start for n in nights])
    wake = clock_stats([n.end for n in nights])
    mid = gap(nights, lambda n: minute_of_day(n.start) + n.session / 2)
    wake_gap = gap(nights, lambda n: minute_of_day(n.end))
    # 기간 값은 값이 있는 밤이 4밤 이상일 때만 (11.9). 본인 기준 비교 평균은 4.1처럼 밤 수가 적어도 낸다
    recent_sleep, sleep_n = mean([n.sleep for n in nights])
    sleep_avg = recent_sleep if sleep_n >= MIN_NIGHTS else None
    eff, eff_n = average([n.efficiency for n in nights])
    awake, awake_n = average([n.awake for n in nights])
    deep, deep_n = average([n.deep_ratio for n in nights])
    base_sleep, base_sleep_n = mean([n.sleep for n in base])
    base_deep, base_deep_n = mean([n.deep_ratio for n in base])
    enough = out["enough_data"]
    out |= {
        "bedtime": {k: v for k, v in bed.items() if not k.startswith("_")},
        "waketime": {k: v for k, v in wake.items() if not k.startswith("_")},
        "midpoint_gap_min": {k: v for k, v in mid.items() if not k.startswith("_")},
        "waketime_gap_min": {k: v for k, v in wake_gap.items() if not k.startswith("_")},
        "sleep_avg_min": {"value": show(sleep_avg), "nights": sleep_n},
        "efficiency_avg_pct": {"value": show(eff), "nights": eff_n},
        "awake_avg_min": {"value": show(awake), "nights": awake_n},
        "deep_ratio_avg_pct": {"value": show(deep), "nights": deep_n},
    }
    # 해당이 없어도 판정에 쓴 값은 남긴다 (11.3: 판정에 쓴 값과 밤 수를 항상 함께 준다)
    recent_ok = reliable(data, end, days, sleep_n)
    base_ok = reliable(data, base_end, BASELINE_DAYS, base_sleep_n)
    deep_change = 100 * (deep - base_deep) / base_deep if deep is not None and base_deep else None
    out |= {
        "sleep_vs_baseline": {
            "recent_avg": show(recent_sleep),
            "baseline_avg": show(base_sleep),
            "diff": show(recent_sleep - base_sleep) if recent_sleep is not None and base_sleep is not None else None,
            "recent_nights": sleep_n,
            "baseline_nights": base_sleep_n,
            "recent_reliable": recent_ok,
            "baseline_reliable": base_ok,
        },
        "deep_ratio_vs_baseline": {
            "recent_avg_pct": show(deep),
            "baseline_avg_pct": show(base_deep),
            "change_pct": show(deep_change),
            "recent_nights": deep_n,
            "baseline_nights": base_deep_n,
        },
    }
    problems, not_judged = [], []
    if not enough:
        return out | {"problems": [], "not_judged": [{"type": "all", "reason": "nights_below_4"}]}
    if sleep_avg is None:
        not_judged.append({"type": "short", "reason": "nights_below_4", "nights": sleep_n})
    elif sleep_avg < 420:
        problems.append({"type": "short", "value": show(sleep_avg), "threshold": 420, "nights": sleep_n})
    elif sleep_avg >= LONG_SLEEP:
        # 긴 수면: 평균 9시간 이상 (11.3, score-rules.md 3장 LE8 9시간 이상 감점과 같은 근거)
        problems.append({"type": "long", "value": show(sleep_avg), "threshold": LONG_SLEEP, "nights": sleep_n})
    if not (recent_ok and base_ok) or recent_sleep is None or base_sleep is None:
        not_judged.append(
            {"type": "declining", "reason": "not_reliable", "nights": sleep_n, "baseline_nights": base_sleep_n}
        )
    elif base_sleep - recent_sleep >= 60:
        problems.append(
            {
                "type": "declining",
                "value": show(recent_sleep - base_sleep),
                "baseline": show(base_sleep),
                "threshold": -60,
                "nights": sleep_n,
                "baseline_nights": base_sleep_n,
            }
        )
    sd = bed.get("_sd")
    if (sd is not None and sd >= 60) or (mid["_raw"] is not None and abs(mid["_raw"]) >= 60):
        problems.append(
            {
                "type": "irregular",
                "bedtime_sd_min": bed["sd_min"],
                "midpoint_gap_min": mid["min"],
                "threshold": 60,
                "nights": len(nights),
            }
        )
    if awake is None and eff is None:
        not_judged.append({"type": "fragmented", "reason": "nights_below_4", "nights": awake_n})
    elif (awake is not None and awake > 30) or (eff is not None and eff < 85):
        problems.append(
            {"type": "fragmented", "awake_avg_min": show(awake), "efficiency_avg_pct": show(eff), "nights": awake_n}
        )
    if base_deep_n < DEEP_BASELINE_MIN:
        not_judged.append({"type": "low_deep", "reason": "baseline_below_7", "baseline_nights": base_deep_n})
    elif deep_change is not None and deep_change <= -20:
        # %포인트가 아니라 상대 변화: (최근 − 기준) ÷ 기준 × 100 이 −20 이하 (11.9)
        problems.append(
            {
                "type": "low_deep",
                "value": show(deep),
                "baseline": show(base_deep),
                "change_pct": show(deep_change),
                "nights": deep_n,
                "baseline_nights": base_deep_n,
            }
        )
    return out | {"problems": problems, "not_judged": not_judged}


# ----- factors (11.4) -----

CONDITIONS = (
    "exercise",
    "late_exercise",
    "high_steps",
    "short_prev_night",
    "late_bedtime_streak",
    "high_day_hr",
    "weekend",
)
OUTCOMES: dict[str, Callable[[Night], float | None]] = {
    "sleep_minutes": lambda n: n.sleep,
    "deep_minutes": lambda n: n.deep,
    "efficiency_pct": lambda n: n.efficiency,
    "awake_minutes": lambda n: n.awake,
    "sleep_hr_avg": lambda n: n.hr,
}


def factors(data: Data, end: date | None = None, days: int = 28, condition: str | None = None) -> dict:
    end = end or data.today
    nights = window(data, end, days)  # 분석 기간 (깬 날짜)
    # 조건 날짜 D = 분석 기간 각 밤의 깬 날 전날 (11.9). 4.2처럼 연동 전, 아직 안 들어옴(동기화 기준일과
    # 같거나 이후, 2.2), 활동 행 없는 날은 비교에서 빼고 "뺀 날 수"에 센다 (11.9)
    all_days = [end - timedelta(days=i) for i in range(1, days + 1)]
    ds = [
        d
        for d in all_days
        if data.linked_from is not None
        and d >= data.linked_from
        and data.synced_day is not None
        and d < data.synced_day
        and d in data.steps
    ]
    sleep_avg, _ = mean([n.sleep for n in nights])
    bed_times = [minute_of_day(n.start) for n in nights]
    step_values = [data.steps[d] for d in ds if data.steps[d]]
    step_median = statistics.median(step_values) if step_values else None
    hr_values = [
        data.heart[d][0] for d in ds if d in data.heart and data.heart[d][1] >= 10 and data.heart[d][0] is not None
    ]
    hr_median = statistics.median(hr_values) if hr_values else None

    def late_bed(n: Night) -> bool:
        u = unwrap([*bed_times, minute_of_day(n.start)])
        return u[-1] - statistics.mean(u[:-1]) >= 60

    def test(name: str, d: date) -> bool | None:
        night = data.nights.get(d + timedelta(days=1))
        ends = data.exercise_ends.get(d, [])
        if name == "exercise":
            return bool(ends)
        if name == "late_exercise":
            if not ends or night is None:
                return None
            return any(night.start - e <= timedelta(hours=3) for e in ends)
        if name == "high_steps":
            return None if not data.steps.get(d) or step_median is None else data.steps[d] >= step_median
        if name == "short_prev_night":
            prev = data.nights.get(d)
            return None if prev is None or prev.sleep is None or sleep_avg is None else prev.sleep <= sleep_avg - 60
        if name == "late_bedtime_streak":
            a, b = data.nights.get(d), data.nights.get(d - timedelta(days=1))
            if a is None or b is None or not bed_times:
                return None
            la, lb = late_bed(a), late_bed(b)
            return True if la and lb else (False if not la and not lb else None)
        if name == "high_day_hr":
            h = data.heart.get(d)
            if h is None or h[1] < 10 or h[0] is None or hr_median is None:
                return None
            return h[0] >= hr_median + 5
        if name == "weekend":
            return (d + timedelta(days=1)).weekday() >= 5
        raise ValueError(name)

    out = {
        "analysis": "factors",
        "period": {"start": (end - timedelta(days=days - 1)).isoformat(), "end": end.isoformat()},
        "nights": len(nights),
        "enough_data": len(nights) >= MIN_NIGHTS,
        "conditions": [],
    }
    for name in [condition] if condition else CONDITIONS:
        groups: dict[bool, list[Night]] = {True: [], False: []}
        excluded = len(all_days) - len(ds)
        for d in ds:
            c = test(name, d)
            n = data.nights.get(d + timedelta(days=1))
            if c is None or n is None:
                excluded += 1
                continue
            groups[c].append(n)
        results = {}
        for key, value in OUTCOMES.items():
            t, tn = mean([value(n) for n in groups[True]])
            f, fn = mean([value(n) for n in groups[False]])
            results[key] = {
                "true_avg": show(t),
                "true_nights": tn,
                "false_avg": show(f),
                "false_nights": fn,
                "diff": show(t - f) if t is not None and f is not None else None,
                "enough_data": tn >= MIN_NIGHTS and fn >= MIN_NIGHTS,
            }
        out["conditions"].append({"condition": name, "excluded_days": excluded, **results})
    if condition is None:
        out["good_bad_nights"] = good_bad(data, nights)
    return out


def good_bad(data: Data, nights: list[Night]) -> dict:
    """잘 잔 밤(긴 순)·못 잔 밤(짧은 순) 앞에서 k개, 같으면 먼저 깬 밤 먼저 (11.9)."""
    ns = [n for n in nights if n.sleep is not None]
    k = len(ns) // 3
    groups = {
        "good": sorted(ns, key=lambda n: (-n.sleep, n.date))[:k],
        "bad": sorted(ns, key=lambda n: (n.sleep, n.date))[:k],
    }
    out = {}
    for name, grp in groups.items():
        prev_days = [n.date - timedelta(days=1) for n in grp]
        ends = [data.exercise_ends.get(d, []) for d in prev_days]
        late = [any(n.start - e <= timedelta(hours=3) for e in es) for n, es in zip(grp, ends, strict=True)]
        bed = unwrap([minute_of_day(n.start) for n in grp]) if grp else []
        out[name] = {
            "nights": len(grp),
            "sleep_range": [min(n.sleep for n in grp), max(n.sleep for n in grp)] if grp else None,
            "exercise_day_pct": show(100 * sum(bool(e) for e in ends) / len(grp)) if grp else None,
            "late_exercise_pct": show(100 * sum(late) / len(grp)) if grp else None,
            # 0보는 걸음 결측 (2.1)
            "steps_avg": show(mean([data.steps.get(d) or None for d in prev_days])[0]),
            "prev_sleep_avg": show(mean([data.nights[d].sleep for d in prev_days if d in data.nights])[0]),
            "bedtime_avg": clock(statistics.mean(bed)) if bed else None,
            "weekend_pct": show(100 * sum(n.date.weekday() >= 5 for n in grp) / len(grp)) if grp else None,
            "enough_data": len(grp) >= MIN_NIGHTS,
        }
    return out


# ----- 어젯밤 대 그 전 7일 (11.6) -----


def last_night_vs_week(data: Data, end: date | None = None) -> dict:
    """기준일에 깬 주 수면(어젯밤)과 그 전 7일 평균. 평균에 어젯밤은 넣지 않는다 (11.9)."""
    end = end or data.today
    last = data.nights.get(end)
    week = window(data, end - timedelta(days=1), LAST_NIGHT_WEEK_DAYS)
    out: dict = {"analysis": "last_night_vs_week", "date": end.isoformat()}
    for key, value in (("sleep_minutes", lambda n: n.sleep), ("sleep_hr_avg", lambda n: n.hr)):
        avg, n = average([value(x) for x in week])
        now = None if last is None else value(last)
        out[key] = {
            "last_night": show(now),
            "week_avg": show(avg),
            "diff": show(now - avg) if now is not None and avg is not None else None,
            "week_nights": n,
        }
    return out


# ----- signals (11.5) -----


def signals(data: Data, end: date | None = None, days: int = 14) -> dict:
    end = end or data.today
    nights = window(data, end, days)
    low_nights = sorted((n for n in nights if n.low), key=lambda n: n.date)
    times = sorted(t for n in low_nights for t in (n.low_times or ()))
    has_times = all(n.low_times is not None for n in low_nights)
    recent, recent_n = mean([n.hr for n in window(data, end, 7)])
    base, base_n = mean([n.hr for n in window(data, end - timedelta(days=7), BASELINE_DAYS)])
    streak, best, best_dates, run_dates = 0, 0, [], []
    for d in sorted(end - timedelta(days=i) for i in range(days)):
        n = data.nights.get(d)
        if n is not None and n.sleep is not None and n.sleep < 300:
            streak += 1
            run_dates.append(d)
        else:
            streak, run_dates = 0, []
        if streak > best:
            best, best_dates = streak, list(run_dates)
    return {
        "analysis": "signals",
        "period": {"start": (end - timedelta(days=days - 1)).isoformat(), "end": end.isoformat()},
        "nights": len(nights),
        "enough_data": len(nights) >= MIN_NIGHTS,
        "spo2_low_repeat": {
            "hit": len(low_nights) >= 3,
            "low_nights": len(low_nights),
            "low_records": sum(n.low for n in low_nights),
            "low_dates": [n.date.isoformat() for n in low_nights],
            "times": [t.astimezone(KST).isoformat(timespec="seconds") for t in times[:5]] if has_times else None,
            "more": max(0, sum(n.low for n in low_nights) - 5) if has_times else None,
            "recorded_nights": sum(1 for n in nights if n.spo2_count),
        },
        "sleep_hr_high": {
            "hit": recent is not None and base is not None and recent - base > 5,
            "recent_avg": show(recent),
            "recent_nights": recent_n,
            "baseline_avg": show(base),
            "baseline_nights": base_n,
            "diff": show(recent - base) if recent is not None and base is not None else None,
        },
        "short_sleep_streak": {
            "hit": best >= 3,
            "longest": best,
            "dates": [d.isoformat() for d in best_dates],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="candiy_pipeline.sleep_reference")
    parser.add_argument("--persona", required=True, help="고정 데이터 이름 (예: synth-11)")
    parser.add_argument(
        "--analysis",
        choices=["pattern", "factors", "signals", "last_night_vs_week"],
        nargs="+",
        default=["pattern", "factors", "signals"],  # last_night_vs_week는 --analysis로 고른다
    )
    parser.add_argument("--condition", choices=CONDITIONS, help="factors에서 한 조건만")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    args = parser.parse_args(argv)
    data = load(args.fixtures / f"{args.persona}.json")
    out = {}
    for a in args.analysis:
        out[a] = factors(data, condition=args.condition) if a == "factors" else globals()[a](data)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
