"""날짜 지정 질문 하나를 이력 조건별로 반복 실행해 답변을 판정한다.

ai/ 디렉터리에서 실행한다.
    uv run python -m eval.repeated_question                                  # 기본 문항(10월 21일), 조건 3개 × 10회
    uv run python -m eval.repeated_question --question control               # 통제 문항(10월 18일)
    uv run python -m eval.repeated_question --runs 1                         # 스모크
    uv run python -m eval.repeated_question --save-doc --label <label>       # docs/evals/ 에도 md·json 저장
    uv run python -m eval.repeated_question --rejudge eval/results/<측정>_records.jsonl --save-doc  # LLM 없이 재판정

- 운영과 같은 run_coach 경로를 거친다. 건강데이터는 합성 값을 넣은 메모리 어댑터에서 조회하고,
  기준 시각은 NOW 로 고정한다. 시스템 프롬프트의 날짜 자리에는 NOW 의 날짜(KST)가 들어간다.
- 이력은 대화 API 처럼 CoachContext.turns 로 넘긴다. 첫 LLM 요청 본문은 이력을 메시지로 그대로 넣던 때와 같다.
- 조건(new, sleep_history, wrong_history)은 이력만 다르다. 회차마다 세 조건을 한 번씩 실행하고 순서는 SEED 로 섞는다.
  한 실행의 답변은 다음 실행 이력에 넣지 않는다.
- 실행이 끝날 때마다 eval/results/ 의 jsonl 에 한 줄을 추가하고, 모두 끝나면 집계 json·md 를 저장한다 (git 제외).
- 판정 기준은 JUDGE_RULES 다.
- --rejudge 는 저장된 실행별 기록을 지금 판정 기준으로 다시 판정해 같은 형식의 기록을 새로 저장한다.
  측정 조건은 같은 측정의 집계 json 에서 읽고, 재판정 시각·커밋·바뀐 판정을 rejudges 에 추가한다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from bench.probe_llm import fmt, git_commit, pct
from coach_ai.adapters.memory import HeartDay, InMemoryHealthData, SleepSession, StepDay, SyncState
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.executor import tool_specs
from coach_ai.agent.graph import ChatTurn, CoachResult, run_coach
from coach_ai.agent.prompts import DEFAULT_PROMPT_VERSION, TODAY_SLOT, load_system_prompt
from coach_ai.agent.state import Evidence
from coach_ai.config import Settings, get_settings
from coach_ai.history.types import HistoryTurn
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import CALL_LIMIT_MESSAGE, DENIED_MESSAGE
from coach_ai.security.tracing import TracingEnabledError, ensure_tracing_disabled
from coach_ai.tools.periods import DEFAULT_TZ, resolve_period
from coach_ai.tools.results import format_minutes
from coach_ai.tools.schemas import METRICS, Metric
from eval.answer_markdown import plain_answer, split_spans
from eval.tool_selection import RequestLog, fmt_call, make_model, prompt_text

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
DOCS_DIR = EVAL_DIR.parent / "docs" / "evals"

_KST = ZoneInfo(DEFAULT_TZ)
# 10월 21일이 이틀 전이 되는 시각. 연동 쪽 확인 스크립트(backend/scripts/coach_health_check.py)를 이 시각에 실행한 것과
# 날짜 구성이 같다
NOW = datetime(2026, 10, 23, 16, 32, tzinfo=_KST)
TODAY = NOW.date()
SEED = 20261023
DEFAULT_RUNS = 10
USER_ID = "synthetic-repeated-question"
CONVERSATION_ID = "eval-repeated-question"

Condition = Literal["new", "sleep_history", "wrong_history"]
CONDITIONS: tuple[Condition, ...] = get_args(Condition)
Verdict = Literal["correct", "unavailable", "contradictory", "wrong_value", "other", "error"]
VERDICTS: tuple[Verdict, ...] = get_args(Verdict)


@dataclass(frozen=True, slots=True)
class Target:
    """묻는 날짜와 그날의 합성 값. 판정은 이 값을 정답으로 쓴다."""

    day: date
    sleep_minutes: int
    spo2: float

    @property
    def question(self) -> str:
        return f"{self.day.month}월 {self.day.day}일 수면 시간과 산소포화도를 알려줘"


TARGETS: dict[str, Target] = {
    # 최근 7일·14일·30일 산소포화도가 가장 낮은 날이라 기간 요약의 daily_min 이 이 날을 가리킨다
    "default": Target(date(2026, 10, 21), 340, 95.1),
    # 산소포화도가 이 날을 포함하는 어느 기간에서도 가장 낮거나 높지 않아 기간 요약 값으로는 이 날 값을 알 수 없다
    "control": Target(date(2026, 10, 18), 441, 95.5),
}

SLEEP_TALK: tuple[ChatTurn, ...] = (
    ChatTurn("user", "요즘 내 평균 수면시간을 알려줘"),
    ChatTurn(
        "assistant",
        "최근 7일 동안의 평균 수면 시간은 6시간 36분이에요. 7일 중 5일 기록, 동기화 전 1일 기준입니다. "
        "10월 20일은 기록이 없어요. 10월 23일은 아직 동기화되지 않았으니 앱에서 동기화를 진행해 주세요.",
    ),
    ChatTurn("user", "아니 나 요즘 잘 자고 있는거 맞나"),
    ChatTurn(
        "assistant",
        "최근 7일 동안의 평균 수면 시간은 6시간 36분이에요. 7일 중 5일 기록, 동기화 전 1일 기준입니다. "
        "수면의 질이나 충분함에 대한 판단은 개인마다 다를 수 있으니, 평소 기상 후의 컨디션을 함께 확인해 보시는 것이 "
        "좋습니다. 혹시 수면과 관련하여 걱정되는 증상이 있으시다면 의료진과 상담하시기를 권해 드립니다.",
    ),
)
WRONG_ANSWER: tuple[ChatTurn, ...] = (
    ChatTurn("user", "10월 21일 수면 시간과 산소포화도를 알려줘"),
    ChatTurn("assistant", "10월 21일 수면 시간은 5시간 40분입니다. 해당 날짜의 산소포화도 기록은 확인할 수 없습니다."),
)
# 통제 문항에도 같은 이력을 넣으므로 이전 오답은 두 문항 모두 10월 21일 문답이다
HISTORIES: dict[Condition, tuple[ChatTurn, ...]] = {
    "new": (),
    "sleep_history": SLEEP_TALK,
    "wrong_history": WRONG_ANSWER + SLEEP_TALK,
}
CONDITION_LABELS: dict[Condition, str] = {
    "new": "이력 없음",
    "sleep_history": "수면 대화 2회",
    "wrong_history": "이전 오답 + 수면 대화 2회",
}


def context_turns(history: Sequence[ChatTurn]) -> tuple[HistoryTurn, ...]:
    """질문·답변이 번갈아 오는 이력을 CoachContext.turns 의 쌍으로 묶는다.

    ID 와 순서 번호는 대화 API 처럼 질문 n·답변 n+1 로 붙인 합성 값이고 LLM 요청에는 들어가지 않는다.
    """
    turns = []
    for n, (question, answer) in enumerate(zip(history[::2], history[1::2], strict=True), 1):
        if (question.role, answer.role) != ("user", "assistant"):
            raise ValueError("이력은 질문과 답변이 번갈아 와야 한다")
        turns.append(
            HistoryTurn(
                request_id=f"{CONVERSATION_ID}-{n}",
                question_message_id=f"{CONVERSATION_ID}-{n}-question",
                answer_message_id=f"{CONVERSATION_ID}-{n}-answer",
                question_seq=2 * n - 1,
                answer_seq=2 * n,
                question=question.content,
                answer=answer.content,
            )
        )
    return tuple(turns)


def run_order(runs: int, seed: int = SEED) -> list[tuple[int, Condition]]:
    """(회차, 조건) 실행 순서. 회차마다 세 조건을 한 번씩 넣고 그 순서를 seed 로 섞는다."""
    rng = random.Random(seed)
    order: list[tuple[int, Condition]] = []
    for run in range(1, runs + 1):
        batch = list(CONDITIONS)
        rng.shuffle(batch)
        order += [(run, condition) for condition in batch]
    return order


# 오늘에서 며칠 전인지 → 값. 지난 7일은 연동 쪽 합성 값(backend/scripts/health_query_example.py)과 같다
_STEPS = {6: 7980, 5: 6450, 4: 0, 3: 8120, 1: 7310, 0: 4210}
# (하루 평균 심박, 유효 시간대 수, 측정 최저, 측정 최고)
_HEART = {3: (70, 14, 50, 110), 2: (76, 15, 52, 140), 1: (95, 1, 60, 130)}
# (실제 수면 분, 산소포화도 평균). 8일 전(10월 15일) 밤은 연동 쪽에 없는 값이다.
# 이 밤이 없으면 통제 날짜(10월 18일)가 지난주(last_week) 산소포화도 최저가 된다
_NIGHTS = {8: (402, 95.3), 6: (418, 96.3), 5: (441, 95.5), 4: (368, None), 2: (340, 95.1), 1: (412, 96.0)}
# 연동 쪽 합성 회원은 첫 기록 시각(sync_state.first_data_at)이 13일 전이라 연동일이 13일 전이다
_LINKED_DAYS_AGO = 13
# 연동 쪽 합성 데이터를 공통 조회 함수로 조회한 지난 7일 공개 평균
INTEGRATION_WEEK_AVG: dict[Metric, float] = {
    "steps": 7465.0,
    "sleep": 395.8,
    "heart_rate": 73.83,
    "oxygen_saturation": 95.73,
}
# 결과에 산소포화도 최저·최고 날을 기록하는 기간
_PROFILE_PERIODS = ("last_7_days", "last_14_days", "last_30_days", "this_week", "last_week", "this_month")


def synthetic_health_data(user_id: str = USER_ID) -> InMemoryHealthData:
    """NOW 기준 합성 건강데이터. 지난 7일 밖에는 10월 15일 밤 하나만 있다."""

    def day(ago: int) -> date:
        return TODAY - timedelta(days=ago)

    sessions = []
    for ago, (minutes, spo2) in _NIGHTS.items():
        d = day(ago)
        wake = datetime(d.year, d.month, d.day, 7, 25, tzinfo=_KST)
        spo2_range = (None, None) if spo2 is None else (90.0, 99.0)
        stages = (70, 85, minutes - 155, 18)
        sessions.append(SleepSession(d, wake - timedelta(hours=8), wake, True, minutes, *stages, spo2, *spo2_range))
    synced = SyncState(NOW, "success", NOW)
    return InMemoryHealthData(
        steps={user_id: [StepDay(day(ago), steps) for ago, steps in _STEPS.items()]},
        heart={
            user_id: [
                HeartDay(day(ago), float(avg), low, high, hours, sample_count=120)
                for ago, (avg, hours, low, high) in _HEART.items()
            ]
        },
        sleep={user_id: sessions},
        sync={user_id: dict.fromkeys(METRICS, synced)},
        linked_from={user_id: day(_LINKED_DAYS_AGO)},
        now=NOW,
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"직렬화할 수 없는 값: {type(value).__name__}")


def data_sha256(port: InMemoryHealthData) -> str:
    """합성 데이터(기록 행, 동기화 상태, 연동일, 기준 시각)의 sha256."""
    rows = {
        "steps": port.steps,
        "heart": port.heart,
        "sleep": port.sleep,
        "sync": port.sync,
        "linked_from": port.linked_from,
        "now": port.now,
    }
    return _sha256(json.dumps(rows, default=_plain, ensure_ascii=False, sort_keys=True))


def _point(snapshot_value: dict[str, Any] | None) -> dict[str, Any] | None:
    return None if snapshot_value is None else {"date": snapshot_value["date"], "value": snapshot_value["value"]}


async def data_profile(port: InMemoryHealthData) -> dict[str, Any]:
    """합성 데이터의 지난 7일 공개 평균, 문항 날짜 값, 기간별 산소포화도 최저·최고 날을 조회한다."""
    week = resolve_period("last_7_days", TODAY)
    averages: dict[str, float | None] = {}
    for metric in INTEGRATION_WEEK_AVG:
        averages[metric] = (await port.get_metric_result(USER_ID, metric, week.start, week.end)).summary.avg
    targets = {}
    for key, target in TARGETS.items():
        sleep = await port.get_metric_result(USER_ID, "sleep", target.day, target.day)
        oxygen = await port.get_metric_result(USER_ID, "oxygen_saturation", target.day, target.day)
        targets[key] = {
            "date": target.day.isoformat(),
            "sleep_minutes": sleep.snapshot["days"][0]["value"],
            "spo2": oxygen.snapshot["days"][0]["value"],
        }
    extremes = {}
    for period in _PROFILE_PERIODS:
        span = resolve_period(period, TODAY)
        summary = (await port.get_metric_result(USER_ID, "oxygen_saturation", span.start, span.end)).snapshot["summary"]
        extremes[period] = {
            "start": span.start.isoformat(),
            "end": span.end.isoformat(),
            "daily_min": _point(summary["daily_min"]),
            "daily_max": _point(summary["daily_max"]),
        }
    outside = [
        {"date": s.date.isoformat(), "sleep_minutes": s.sleep_minutes, "spo2": s.spo2_avg}
        for s in port.sleep.get(USER_ID, [])
        if s.is_main and s.date < week.start
    ]
    return {
        "linked_from": port.linked_from[USER_ID].isoformat(),
        "week": {
            "start": week.start.isoformat(),
            "end": week.end.isoformat(),
            "avg": averages,
            "integration_avg": INTEGRATION_WEEK_AVG,
            "matches_integration": averages == INTEGRATION_WEEK_AVG,
        },
        "nights_before_week": outside,
        "targets": targets,
        "oxygen_extremes": extremes,
    }


JUDGE_RULES = (
    "답변의 마크다운은 기호를 지운 글로 읽는다. 굵게 기호(**)와 줄 앞의 목록 기호(-, 1.)를 지우고, 표는 구분선과 "
    "| 기호를 빼고 한 행을 한 부분으로 읽는다. 표의 머리 행과 첫 열은 값으로 읽지 않는다.",
    "답변을 문장·쉼표 단위로 나누고(표의 행 안에서는 나누지 않는다), 묻는 날짜가 아닌 날짜만 말하는 부분과 기간 "
    '평균(최근 7일 평균 등)을 말하는 부분은 판정에서 뺀다. 묻는 날짜나 "해당 날짜"·"그날"을 말하는 부분은 넣는다.',
    '기록을 부정하는 표현("확인할 수 없", "기록이 없", "확인되지 않" 등)은 띄어쓰기 차이를 무시하고 찾는다.',
    "아래 순서로 먼저 맞는 판정을 쓴다.",
    "error: 실행이 오류로 끝난다.",
    "contradictory: 정답 산소포화도를 말하면서 기록을 부정하는 표현이 있거나, 그 값을 기간 없이 최저치·최솟값으로 "
    "설명하거나 일일 최저처럼 설명한다.",
    "wrong_value: 정답과 다른 산소포화도(%)나 수면 시간을 말한다.",
    "correct: 산소포화도와 수면 시간(분 또는 N시간 M분)이 모두 정답과 같다.",
    "unavailable: 산소포화도 값 없이 기록을 부정하는 표현이 있다.",
    "other: 어느 것에도 해당하지 않는다. 답변을 사람이 확인한다.",
)

# 소수점과 천 단위 쉼표에서는 나누지 않는다
_CLAUSE_BREAK = re.compile(r"(?<!\d)[.!?。](?!\d)|\n|,(?!\d)")
_DATE = re.compile(
    r"(\d{1,2})\s*월\s*(\d{1,2})\s*일|(\d{4})-(\d{1,2})-(\d{1,2})|(?<![\d.])(\d{1,2})/(\d{1,2})(?![\d/])"
)
_RELATIVE_DAYS = {"그저께": 2, "그제": 2, "어제": 1, "오늘": 0}
_TARGET_REF = re.compile(r"해당\s*(?:날|일자)|그\s?날|당일|이날")
_PERIOD = re.compile(
    r"(?:최근|지난)\s*(?:\d+\s*(?:일|주|개월)|일주일|한\s*(?:주|달))|이번\s*(?:주|달)|지난\s*(?:주|달)"
    r"|\d+\s*일\s*(?:간|동안)|일주일|한\s*달"
)
_AVERAGE = re.compile(r"평균")
_PERCENT = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*(?:%|퍼센트)")
# "7시 15분" 같은 시각은 clock 으로 먼저 소비해 수면 시간에서 뺀다
_DURATION = re.compile(
    r"(?<![\d.])(?P<h>\d+)\s*시간(?:\s*(?P<hm>\d+)\s*분)?|(?P<clock>(?<![\d.])\d+\s*시\s*\d+\s*분)|(?<![\d.])(?P<m>\d+)\s*분"
)
# 띄어쓰기를 뺀 문장에서 찾는다. "확인 할 수 없"과 "확인할 수 없"은 같은 표현이다
_DENIAL = re.compile(
    r"확인할수가?없|확인(?:이|하기가?)(?:어렵|불가)|확인이?되지않|알수가?없"
    r"|기록(?:이|은|도)(?:없|존재하지않)|기록되지않|측정되지않|(?:제공|조회)되지않|(?:조회할|찾을)수없"
    r"|(?:안내|말씀)해?드리기가?어렵|(?:정보|데이터|수치|값)(?:가|이|은|는)없"
)
# 기간을 밝히지 않은 최저·최고 표현은 그날 값을 그날 측정치 중 최저·최고처럼 설명한 것으로 간주한다
_EXTREME = re.compile(r"최저|최솟값|최소\s*(?:치|값|수치)|최고\s*(?:치|값|수치)|최댓값|최대\s*(?:치|값|수치)")
# 하루 안의 최저·최고라고 하면 기간을 밝혀도 그날 값을 다르게 설명한 것이다
_DAY_EXTREME = re.compile(r"(?:일일|하루|당일|그\s?날|해당\s*날짜의?)\s*(?:중\s*)?(?:의\s*)?최(?:저|소|고|대)")

Status = Literal["correct", "wrong", "absent"]


@dataclass(frozen=True, slots=True)
class Judgement:
    verdict: Verdict
    detail: dict[str, Any] = field(default_factory=dict)


def _mentioned_days(part: str, today: date) -> set[date]:
    days: set[date] = set()
    for m in _DATE.finditer(part):
        if m.group(1):
            ymd = (today.year, int(m.group(1)), int(m.group(2)))
        elif m.group(3):
            ymd = (int(m.group(3)), int(m.group(4)), int(m.group(5)))
        else:
            ymd = (today.year, int(m.group(6)), int(m.group(7)))
        try:
            days.add(date(*ymd))
        except ValueError:
            continue
    days |= {today - timedelta(days=ago) for word, ago in _RELATIVE_DAYS.items() if word in part}
    return days


def _about_target(part: str, target: date, today: date) -> bool:
    days = _mentioned_days(part, today)
    if target in days or _TARGET_REF.search(part):
        return True
    if days:
        return False
    return not (_PERIOD.search(part) and _AVERAGE.search(part))


def _durations(part: str) -> list[int]:
    minutes = []
    for m in _DURATION.finditer(part):
        if m.group("h"):
            minutes.append(int(m.group("h")) * 60 + int(m.group("hm") or 0))
        elif m.group("m"):
            minutes.append(int(m.group("m")))
    return minutes


def _denial(part: str) -> str | None:
    """기록을 부정하는 표현을 띄어쓰기 차이를 무시하고 찾는다. 찾으면 답변에 쓰인 그대로의 구간을 반환한다."""
    kept = [i for i, ch in enumerate(part) if not ch.isspace()]
    m = _DENIAL.search("".join(part[i] for i in kept))
    return None if m is None else part[kept[m.start()] : kept[m.end() - 1] + 1]


def _reinterpretation(part: str) -> re.Match[str] | None:
    return _DAY_EXTREME.search(part) or (None if _PERIOD.search(part) else _EXTREME.search(part))


def _first(matches: Iterable[re.Match[str] | None]) -> str | None:
    return next((m.group(0) for m in matches if m is not None), None)


def _status(values: Sequence[float], expected: float) -> Status:
    if not values:
        return "absent"
    return "correct" if any(abs(v - expected) < 0.05 for v in values) else "wrong"


def judge(answer: str, target: Target, *, error_code: str | None = None, today: date = TODAY) -> Judgement:
    """답변 하나를 JUDGE_RULES 대로 판정한다. detail 에 판정에 쓴 값과 표현을 담는다."""
    if error_code is not None:
        return Judgement("error", {"error_code": error_code})
    plain = plain_answer(answer)
    # (부분, 그 부분에서 값으로 읽는 글). 값은 표 머리 행과 첫 열을 지운 글에서 읽는다
    parts = [
        (plain.text[a:b], plain.values[a:b])
        for a, b in split_spans(plain.text, _CLAUSE_BREAK, plain.rows)
        if plain.text[a:b].strip()
    ]
    about = [(p, v) for p, v in parts if _about_target(p, target.day, today)]
    spo2_values = [float(x) for _, v in about for x in _PERCENT.findall(v)]
    sleep_values = [m for _, v in about for m in _durations(v)]
    denial = next((d for d in (_denial(p) for p, _ in about) if d is not None), None)
    reinterpret = _first(_reinterpretation(p) for p, _ in about)
    spo2 = _status(spo2_values, target.spo2)
    sleep = _status(sleep_values, target.sleep_minutes)
    verdict: Verdict
    if spo2 == "correct" and (denial or reinterpret):
        verdict = "contradictory"
    elif "wrong" in (spo2, sleep):
        verdict = "wrong_value"
    elif spo2 == sleep == "correct":
        verdict = "correct"
    elif spo2 == "absent" and denial:
        verdict = "unavailable"
    else:
        verdict = "other"
    return Judgement(
        verdict,
        {
            "spo2": spo2,
            "sleep": sleep,
            "spo2_values": spo2_values,
            "sleep_minutes": sleep_values,
            "denial": denial,
            "reinterpret": reinterpret,
            "excluded_parts": len(parts) - len(about),
        },
    )


def _reply_kind(content: Any) -> str:
    """도구 메시지 본문의 종류.

    ok, 처리기 오류 이름(not_supported 등), 게이트웨이 거부(denied), 해석할 수 없는 본문(unknown) 중 하나다.
    """
    if content in (DENIED_MESSAGE, CALL_LIMIT_MESSAGE):
        return "denied"
    try:
        body = json.loads(content)
    except (TypeError, ValueError):
        return "unknown"
    if not isinstance(body, dict):
        return "unknown"
    return "ok" if "result" in body else str(body.get("error", "unknown"))


def added_lookups(message: ToolMessage) -> list[dict[str, Any]]:
    """도구 메시지에 에이전트가 더해 이어 붙인 호출. 더한 순서대로 이름(name), 인자(args), 결과 종류(reply), 결과 본문
    (result)을 반환한다.

    도구 노드는 더한 호출을 그 메시지 artifact 의 added 에, 결과 본문을 메시지 본문의 added 목록에 같은 순서로 넣는다.
    더한 호출이 없는 메시지와 이 처리가 없는 앱 코드의 메시지는 빈 목록이다.
    """
    added = getattr(message.artifact, "added", None) or ()
    try:
        body = json.loads(message.content)
    except (TypeError, ValueError):
        body = None
    parts = body.get("added") if isinstance(body, dict) else None
    parts = parts if isinstance(parts, list) else []
    out = []
    for i, call in enumerate(added):
        part = parts[i] if i < len(parts) else None
        if isinstance(part, dict):
            kind = "ok" if "result" in part else str(part.get("error", "unknown"))
        else:
            kind = "unknown"
        args = None if call.args is None else dict(call.args)
        out.append({"name": call.name, "args": args, "reply": kind, "result": part})
    return out


def tool_calls_of(result: CoachResult, audit: MemoryAuditSink) -> list[dict[str, Any]]:
    """LLM 응답의 도구 호출을 왕복 순서로 정리하고 호출마다 받은 결과 종류(reply)를 붙인다.

    에이전트가 더해 함께 실행한 호출은 그 왕복의 호출 뒤에 added 가 참인 호출로 넣는다.
    오류로 끝난 실행은 결과에 메시지가 없어 게이트웨이 호출 기록에서 이름·인자만 가져온다 (round·reply 는 None).
    """
    if not result.messages:
        return [{"round": None, "name": r.tool_name, "args": r.args, "reply": None} for r in audit.records()]
    calls: list[dict[str, Any]] = []
    pending: list[tuple[str, dict[str, Any]]] = []
    rounds = 0
    for message in result.messages:
        if isinstance(message, AIMessage) and (message.tool_calls or message.invalid_tool_calls):
            rounds += 1
            pending = []
            # 도구 메시지의 tool_call_id 는 tools_node 가 정한 ID 다 (없으면 도구 이름, 해석 실패면 "invalid")
            for call in message.tool_calls:
                entry = {"round": rounds, "name": call["name"], "args": call["args"], "reply": None}
                pending.append((call["id"] or call["name"], entry))
            for bad in message.invalid_tool_calls:
                entry = {"round": rounds, "name": bad.get("name") or "", "args": bad.get("args"), "reply": None}
                pending.append((bad.get("id") or "invalid", entry))
            calls += [entry for _, entry in pending]
        elif isinstance(message, ToolMessage):
            index = next((i for i, (call_id, _) in enumerate(pending) if call_id == message.tool_call_id), None)
            if index is not None:
                pending.pop(index)[1]["reply"] = _reply_kind(message.content)
                calls += [
                    {"round": rounds, "name": a["name"], "args": a["args"], "reply": a["reply"], "added": True}
                    for a in added_lookups(message)
                ]
    return calls


def calls_signature(calls: Sequence[dict[str, Any]]) -> str:
    """한 왕복의 호출을 정렬해 + 로 묶고 왕복 순서를 → 로 잇는다."""
    if not calls:
        return "(도구 없음)"
    rounds: dict[int | None, list[str]] = {}
    for call in calls:
        rounds.setdefault(call["round"], []).append(fmt_call(call))
    return " → ".join(" + ".join(sorted(names)) for names in rounds.values())


def evidence_value(evidence: Evidence, day: date) -> dict[str, Any]:
    """근거 snapshot 의 묻는 날짜 하루 값. 기간 밖이면 day 가 None 이다.

    그 날이 snapshot 요약의 daily_min·daily_max 날이면 extreme 에 적는다.
    """
    snapshot = evidence.snapshot
    iso = day.isoformat()
    row = next((d for d in snapshot.get("days", []) if d.get("date") == iso), None)
    summary = snapshot.get("summary") or {}
    extreme = next((k for k in ("daily_min", "daily_max") if (summary.get(k) or {}).get("date") == iso), None)
    span = evidence.period
    return {
        "position": evidence.position,
        "tool": evidence.tool,
        "metric": evidence.metric,
        "period": f"{span.period} {span.start}~{span.end}",
        "day": None if row is None else {k: row.get(k) for k in ("value", "missing", "reason")},
        "extreme": extreme,
    }


async def run_once(
    model: BaseChatModel,
    recorder: MetricsRecorder,
    question: str,
    condition: Condition,
    run: int,
    order: int,
    prompt_version: str,
) -> dict[str, Any]:
    """조건 하나를 한 번 실행하고 기록 한 줄을 반환한다. 합성 데이터와 호출 기록기는 실행마다 새로 만든다."""
    target = TARGETS[question]
    request_id = f"rq-{question}-{run}-{condition}"
    ctx = CoachContext(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        request_id=request_id,
        turns=context_turns(HISTORIES[condition]),
    )
    audit = MemoryAuditSink()
    started = time.perf_counter()
    result = await run_coach(
        ctx,
        target.question,
        port=synthetic_health_data(),
        model=model,
        sink=ListSink(),
        now=NOW,
        message_id=f"{request_id}-answer",
        audit=audit,
        prompt_version=prompt_version,
    )
    elapsed = time.perf_counter() - started
    judgement = judge(result.answer, target, error_code=result.error_code)
    calls = tool_calls_of(result, audit)
    llm = [r for r in recorder.records("llm") if r.request_id == request_id]
    return {
        "question": question,
        "condition": condition,
        "run": run,
        "order": order,
        "request_id": request_id,
        "verdict": judgement.verdict,
        "judge": judgement.detail,
        "answer": result.answer,
        "finish_reason": result.finish_reason,
        "error_code": result.error_code,
        "exception": None if result.exception is None else type(result.exception).__name__,
        "blocked_by": result.blocked_by,
        "tool_rounds": result.tool_rounds,
        # 앞 답의 건강 수치를 도구 없이 다시 써 되돌린 횟수와 효과 단정 끝맺음을 바꾼 문장 수.
        # 두 칸이 없는 이전 앱 코드는 그 기능이 없어 0 으로 기록한다
        "answer_rechecks": getattr(result, "answer_rechecks", 0),
        # 앞 답에도 질문에도 없는 건강 수치를 도구 없이 말해 되돌린 횟수. 이 칸이 없는 옛 앱 코드는 0 으로 기록한다
        "unsupported_rechecks": getattr(result, "unsupported_rechecks", 0),
        # 운동이 잠에 준 효과를 물은 질문에 도구 없이 답해 되돌린 횟수. 이 칸이 없는 옛 앱 코드는 0 으로 기록한다
        "exercise_rechecks": getattr(result, "exercise_rechecks", 0),
        "hedged_sentences": getattr(result, "hedged_sentences", 0),
        # 답 글의 내부 표시를 정리한 응답 수. 이 칸이 없는 이전 앱 코드는 0 으로 기록한다
        "marker_cleanups": getattr(result, "marker_cleanups", 0),
        "tool_calls": calls,
        "calls_signature": calls_signature(calls),
        "evidence": [evidence_value(e, target.day) for e in result.evidence],
        "llm_calls": len(llm),
        "llm_input_tokens": [r.input_tokens for r in llm],
        "input_tokens": sum(r.input_tokens or 0 for r in llm),
        "output_tokens": sum(r.output_tokens or 0 for r in llm),
        "elapsed_s": round(elapsed, 3),
    }


def _progress_line(record: dict[str, Any], total: int) -> str:
    return (
        f"[{record['order']}/{total}] {record['run']}회 {record['condition']} · {record['verdict']} · "
        f"왕복 {record['tool_rounds']} · {record['elapsed_s']:.1f}초"
    )


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(EVAL_DIR.parent).as_posix()
    except ValueError:
        return path.name


def _history_rows(history: Sequence[ChatTurn]) -> list[dict[str, str]]:
    return [{"role": turn.role, "content": turn.content} for turn in history]


async def run_eval(
    settings: Settings,
    args: argparse.Namespace,
    records_path: Path,
    *,
    command: str,
    progress: Callable[[str], None] = print,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """조건마다 args.runs 회 실행하고 (보고서, 실행별 기록)을 반환한다. 기록은 실행마다 records_path 에 추가한다."""
    target = TARGETS[args.question]
    template = load_system_prompt(args.prompt_version)
    measured_at = datetime.now().isoformat(timespec="seconds")
    order = run_order(args.runs, SEED)
    reference = synthetic_health_data()
    profile = await data_profile(reference)
    recorder = MetricsRecorder(max_records=max(1000, len(order) * 5))
    log = RequestLog()
    model = make_model(settings, args.temperature, recorder, log)
    records: list[dict[str, Any]] = []
    started = time.perf_counter()
    records_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 이미 있는 파일에 이어 쓰면 다른 측정의 기록과 섞이므로 새 파일만 만든다
        with records_path.open("x", encoding="utf-8") as f:
            for index, (run, condition) in enumerate(order, 1):
                record = await run_once(model, recorder, args.question, condition, run, index, args.prompt_version)
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                records.append(record)
                progress(_progress_line(record, len(order)))
    finally:
        await model.http_async_client.aclose()
    params = log.params[0] if log.params else None
    report = {
        "measured_at": measured_at,
        "label": args.label,
        "commit": git_commit(),
        "command": command,
        "model": settings.llm_model,
        "base_url_host": httpx.URL(settings.llm_base_url).host,
        "prompt_version": args.prompt_version,
        "prompt_sha256": _sha256(template)[:12],
        # run_coach 가 날짜 자리에 넣는 NOW 의 날짜. 날짜 자리가 없는 템플릿(v1, v1.1)이면 None 이다
        "prompt_date": TODAY.isoformat() if TODAY_SLOT in template else None,
        "tools_sha256": _sha256(json.dumps(tool_specs(), ensure_ascii=False, sort_keys=True))[:12],
        # 실제 요청에 들어간 값. 요청에 없으면 서버 기본값을 쓴다
        "temperature": None if params is None else params.get("temperature"),
        "temperature_source": None if params is None else ("request" if "temperature" in params else "server_default"),
        "request_params": log.params,
        "now": NOW.isoformat(),
        "seed": SEED,
        "runs": args.runs,
        "order": [[run, condition] for run, condition in order],
        "question": {
            "key": args.question,
            "text": target.question,
            "date": target.day.isoformat(),
            "sleep_minutes": target.sleep_minutes,
            "sleep_display": format_minutes(target.sleep_minutes),
            "spo2": target.spo2,
        },
        "conditions": {
            condition: {
                "label": CONDITION_LABELS[condition],
                "history": _history_rows(HISTORIES[condition]),
                "history_sha256": _sha256(json.dumps(_history_rows(HISTORIES[condition]), ensure_ascii=False))[:12],
            }
            for condition in CONDITIONS
        },
        "data": {"sha256": data_sha256(reference)[:12], "user_id": USER_ID, **profile},
        "elapsed_s": round(time.perf_counter() - started, 1),
        "records_file": _display_path(records_path),
        "summary": summarize(records),
    }
    return report, records


def summarize(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def group(rs: Sequence[dict[str, Any]]) -> dict[str, Any]:
        verdicts = Counter(r["verdict"] for r in rs)
        rounds = Counter(r["tool_rounds"] for r in rs)
        elapsed = [r["elapsed_s"] for r in rs]
        return {
            "runs": len(rs),
            "correct": verdicts["correct"],
            "verdicts": {v: verdicts[v] for v in VERDICTS},
            # JSON 으로 저장하면 키가 문자열이 되므로 처음부터 문자열로 둔다
            "tool_rounds": {str(n): rounds[n] for n in sorted(rounds)},
            "calls": dict(Counter(r["calls_signature"] for r in rs).most_common()),
            "error_codes": dict(Counter(r["error_code"] for r in rs if r["error_code"]).most_common()),
            "elapsed_p50": pct(elapsed, 50),
            "elapsed_p95": pct(elapsed, 95),
        }

    return {
        "by_condition": {c: group([r for r in records if r["condition"] == c]) for c in CONDITIONS},
        "overall": group(records),
        "llm": {
            "calls": sum(r["llm_calls"] for r in records),
            "input_tokens": sum(r["input_tokens"] for r in records),
            "output_tokens": sum(r["output_tokens"] for r in records),
        },
        "answers": [
            {k: r[k] for k in ("order", "run", "condition", "verdict", "tool_rounds", "calls_signature", "answer")}
            for r in records
        ],
    }


def _md(text: Any, limit: int | None = None) -> str:
    """표·목록 한 줄에 넣을 수 있게 줄바꿈을 공백으로 바꾸고 | 를 이스케이프한다."""
    s = " ".join(str(text).split()).replace("|", "\\|")
    return s if limit is None or len(s) <= limit else s[: limit - 1] + "…"


def _num(value: Any) -> str:
    return "-" if value is None else f"{value:g}"


def _temperature_text(report: dict[str, Any]) -> str:
    if report["temperature_source"] is None:
        return "- (LLM 호출 없음)"
    if report["temperature_source"] == "server_default":
        return "서버 기본값 (요청에 넣지 않음)"
    return f"{report['temperature']} (요청에 넣음)"


def _result_row(name: str, label: str, g: dict[str, Any]) -> str:
    counts = " | ".join(str(g["verdicts"][v]) for v in VERDICTS)
    return (
        f"| {name} | {label} | {g['runs']} | {g['correct']} | {counts} | "
        f"{fmt(g['elapsed_p50'])} | {fmt(g['elapsed_p95'])} |"
    )


def _extreme_text(point: dict[str, Any] | None) -> str:
    return "-" if point is None else f"{point['date']} {_num(point['value'])}%"


def _rejudge_lines(rejudges: Sequence[dict[str, Any]]) -> list[str]:
    lines = []
    for r in rejudges:
        lines += [
            f"- 재판정 {r['rejudged_at']} · 커밋 `{r['commit']}` · 판정 기준을 고친 뒤 `{r['source_records']}` 를 "
            f"LLM 호출 없이 다시 판정 · 바뀐 판정 {r['changed']}건",
            f"  - 재판정 명령 `{r['command']}`",
            *(
                f"  - 실행 {c['order']} · {c['run']}회 `{c['condition']}` · {c['before']} → {c['after']}"
                for c in r["changes"]
            ),
        ]
    return lines


def to_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    q = report["question"]
    data = report["data"]
    week = data["week"]
    llm = s["llm"]
    title = "# 날짜 지정 질문 반복 검증" + (f" {report['label']}" if report["label"] else "")
    lines = [
        f"{title} ({report['measured_at']})",
        "",
        f"- 커밋 `{report['commit']}` · 모델 `{report['model']}` · 호스트 `{report['base_url_host']}`",
        f"- 실행 명령 `{report['command']}`",
        f'- 문항 `{q["key"]}` "{q["text"]}" · 정답 수면 {q["sleep_minutes"]}분({q["sleep_display"]}), '
        f"수면 중 산소포화도 {_num(q['spo2'])}%",
        f"- 기준 시각 {report['now']} · 조건 {len(report['conditions'])}개 × {report['runs']}회 · "
        f"회차마다 조건 순서를 seed {report['seed']}로 섞음 · 실행 결과는 다음 실행 이력에 넣지 않음",
        f"- 프롬프트 {prompt_text(report)} · "
        f"도구 스키마 sha256 {report['tools_sha256']} · 합성 데이터 sha256 {data['sha256']}",
        f"- temperature {_temperature_text(report)} · LLM 호출 {llm['calls']}회 · 입력 토큰 {llm['input_tokens']:,} · "
        f"출력 토큰 {llm['output_tokens']:,} · 전체 {report['elapsed_s']}초",
        f"- 실행별 기록 `{report['records_file']}` (git 제외)",
        *_rejudge_lines(report.get("rejudges", [])),
        "",
        "## 조건별 결과",
        "",
        "| 조건 | 이력 | 실행 | 정답 | " + " | ".join(VERDICTS) + " | 소요 p50 | 소요 p95 |",
        "| --- | --- | --- | --- | " + " | ".join("---" for _ in VERDICTS) + " | --- | --- |",
        *(
            _result_row(c, report["conditions"][c]["label"], g)
            for c, g in s["by_condition"].items()
            if c in report["conditions"]
        ),
        _result_row("전체", "-", s["overall"]),
    ]
    max_round = max((int(n) for n in s["overall"]["tool_rounds"]), default=0)
    columns = [str(n) for n in range(max_round + 1)]
    lines += [
        "",
        "## 도구 왕복 분포",
        "",
        "| 조건 | " + " | ".join(f"{n}회" for n in columns) + " |",
        "| --- | " + " | ".join("---" for _ in columns) + " |",
        *(
            f"| {name} | " + " | ".join(str(g["tool_rounds"].get(n, 0)) for n in columns) + " |"
            for name, g in [*s["by_condition"].items(), ("전체", s["overall"])]
        ),
        "",
        "## 도구 호출",
        "",
        "한 왕복의 호출은 + 로 묶고 왕복 순서는 → 로 잇는다.",
        "",
        "| 조건 | 호출 | 횟수 |",
        "| --- | --- | --- |",
        *(f"| {c} | {_md(sig)} | {n} |" for c, g in s["by_condition"].items() for sig, n in g["calls"].items()),
    ]
    targets = data["targets"]
    matched = "같음" if week["matches_integration"] else "다름"
    lines += [
        "",
        "## 합성 데이터",
        "",
        f"- 기준 시각 {report['now']} · 연동일 {data['linked_from']} · 동기화 기준 시각은 기준 시각과 같음",
        f"- 지난 7일({week['start']}~{week['end']}) 공개 평균: 걸음 {_num(week['avg']['steps'])} · "
        f"수면 {_num(week['avg']['sleep'])}분 · 심박 {_num(week['avg']['heart_rate'])}bpm · "
        f"산소포화도 {_num(week['avg']['oxygen_saturation'])}% (연동 쪽 합성 값과 {matched})",
        *(
            f"- 지난 7일 밖 주 수면: {n['date']} {n['sleep_minutes']}분, 산소포화도 {_num(n['spo2'])}% "
            "(연동 쪽에 없는 값. 통제 날짜가 지난주 산소포화도 최저가 되지 않게 넣음)"
            for n in data["nights_before_week"]
        ),
        *(
            f"- `{key}` {t['date']}: 수면 {_num(t['sleep_minutes'])}분 · 산소포화도 {_num(t['spo2'])}%"
            for key, t in targets.items()
        ),
        "",
        "| 기간 | 구간 | 산소포화도 최저 날 | 최고 날 |",
        "| --- | --- | --- | --- |",
        *(
            f"| {period} | {e['start']}~{e['end']} | "
            f"{_extreme_text(e['daily_min'])} | {_extreme_text(e['daily_max'])} |"
            for period, e in data["oxygen_extremes"].items()
        ),
        "",
        "## 이력 조건",
        "",
        "모든 조건은 같은 시스템 프롬프트와 질문을 쓰고 이력만 다르다. 통제 문항에도 같은 이력을 넣는다.",
        "",
    ]
    for condition, info in report["conditions"].items():
        sha = f" (sha256 {info['history_sha256']})" if info["history"] else ""
        lines.append(f"- `{condition}` {info['label']}{sha}")
        lines += [f"  - {turn['role']}: {_md(turn['content'])}" for turn in info["history"]]
    lines += ["", "## 판정 기준", "", *(f"- {rule}" for rule in JUDGE_RULES), "", "## 답변 (사람 확인용)", ""]
    lines += [
        f"- 실행 {a['order']} · {a['run']}회 `{a['condition']}` · {a['verdict']} · 왕복 {a['tool_rounds']} · "
        f"{_md(a['calls_signature'])} · {_md(a['answer'], 300) or '(본문 없음)'}"
        for a in s["answers"]
    ]
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], md_path: Path, json_path: Path) -> None:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")


def doc_paths(doc_dir: Path, day: date, label: str) -> tuple[Path, Path]:
    stem = f"{day.isoformat()}-repeated-question-{label}"
    return doc_dir / f"{stem}.md", doc_dir / f"{stem}.json"


_RECORDS_SUFFIX = "_records.jsonl"


def load_measurement(records_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """실행별 기록과 같은 측정의 집계 json 을 읽어 (집계, 실행별 기록)을 반환한다.

    측정 조건(커밋, 모델, 프롬프트, 합성 데이터 등)은 실행별 기록에 없어 집계 json 에서 가져온다.
    두 파일이 같은 측정이 아니거나, 문항 정답·기준 시각이 측정 때와 다르면 멈춘다.
    """
    if not records_path.name.endswith(_RECORDS_SUFFIX):
        raise SystemExit(f"실행별 기록 파일(*{_RECORDS_SUFFIX})이 아닙니다: {records_path}")
    report_path = records_path.with_name(records_path.name.removesuffix(_RECORDS_SUFFIX) + ".json")
    if not report_path.exists():
        raise SystemExit(f"같은 측정의 집계 파일이 없습니다: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    q = report["question"]
    if Path(report["records_file"]).name != records_path.name or any(r["question"] != q["key"] for r in records):
        raise SystemExit(f"집계 파일과 실행별 기록이 같은 측정이 아닙니다: {report_path}")
    if len(records) != len(report["order"]):
        raise SystemExit(f"실행별 기록 수({len(records)})가 측정 실행 수({len(report['order'])})와 다릅니다")
    target = TARGETS[q["key"]]
    measured = (q["date"], q["sleep_minutes"], q["spo2"], report["now"])
    if (target.day.isoformat(), target.sleep_minutes, target.spo2, NOW.isoformat()) != measured:
        raise SystemExit(f"문항 {q['key']} 의 정답이나 기준 시각이 측정 때와 다릅니다")
    return report, records


def rejudge_records(
    records: Sequence[dict[str, Any]], target: Target
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """실행별 기록을 지금 판정 기준으로 다시 판정한다. (다시 판정한 기록, 판정이 바뀐 실행)을 반환한다."""
    rejudged: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for record in records:
        judgement = judge(record["answer"], target, error_code=record["error_code"])
        rejudged.append({**record, "verdict": judgement.verdict, "judge": judgement.detail})
        if judgement.verdict != record["verdict"]:
            where = {k: record[k] for k in ("order", "run", "condition")}
            changes.append({**where, "before": record["verdict"], "after": judgement.verdict})
    return rejudged, changes


def _doc_rejudges(doc_json: Path, source: dict[str, Any], records_path: Path) -> list[dict[str, Any]]:
    """새 재판정을 붙일 docs 기록의 재판정 이력을 반환한다. docs 기록이 없으면 측정 기록의 이력을 반환한다.

    docs 기록이 다른 측정의 것이거나 records_file 이 재판정할 실행별 기록과 다르면 멈춘다.
    다른 기록의 재판정 결과로 docs 기록을 바꾸면 docs 의 재판정 이력이 사라진다.
    """
    if not doc_json.exists():
        return source.get("rejudges", [])
    doc = json.loads(doc_json.read_text(encoding="utf-8"))
    if (doc.get("measured_at"), doc.get("command")) != (source["measured_at"], source["command"]):
        raise SystemExit(f"docs 기록이 다른 측정의 것입니다: {doc_json}")
    if doc.get("records_file") != _display_path(records_path):
        raise SystemExit(
            f"docs 기록의 records_file 과 다른 실행별 기록입니다. {doc.get('records_file')} 로 재판정하세요: {doc_json}"
        )
    return doc.get("rejudges", [])


def run_rejudge(args: argparse.Namespace, started: datetime, command: str) -> None:
    """--rejudge 로 받은 실행별 기록을 다시 판정해 실행별 기록·집계 json·md 를 새로 저장한다.

    --save-doc 이면 docs 의 같은 측정 기록(측정 날짜와 label)을 재판정 결과로 바꾼다.
    docs 기록의 재판정 이력은 그대로 두고 새 재판정을 뒤에 붙인다.
    """
    source, records = load_measurement(args.rejudge)
    history = source.get("rejudges", [])
    docs: tuple[Path, ...] = ()
    if args.save_doc:
        if not source["label"]:
            raise SystemExit("측정 기록에 label 이 없어 docs 에 저장할 수 없습니다")
        docs = doc_paths(args.doc_dir, datetime.fromisoformat(source["measured_at"]).date(), source["label"])
        history = _doc_rejudges(docs[1], source, args.rejudge)
    key = source["question"]["key"]
    rejudged, changes = rejudge_records(records, TARGETS[key])
    name = f"{started:%Y%m%d-%H%M%S}_repeated_question_{key}_rejudged"
    records_path = args.out / f"{name}{_RECORDS_SUFFIX}"
    records_path.parent.mkdir(parents=True, exist_ok=True)
    with records_path.open("x", encoding="utf-8") as f:
        f.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in rejudged)
    entry = {
        "rejudged_at": started.isoformat(timespec="seconds"),
        "commit": git_commit(),
        "command": command,
        "source_records": _display_path(args.rejudge),
        "changed": len(changes),
        "changes": changes,
    }
    report = {
        **source,
        "records_file": _display_path(records_path),
        "summary": summarize(rejudged),
        "rejudges": [*history, entry],
    }
    print("\n" + to_markdown(report))
    md_path, json_path = args.out / f"{name}.md", args.out / f"{name}.json"
    write_report(report, md_path, json_path)
    print(f"실행별 기록: {records_path}\n집계: {json_path}\n요약: {md_path}")
    if docs:
        write_report(report, *docs)
        print(f"평가 기록: {docs[0]}\n평가 기록 json: {docs[1]}")


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("1 이상이어야 한다")
    return value


def _temperature(text: str) -> float:
    value = float(text)
    if not 0 <= value <= 2:
        raise argparse.ArgumentTypeError("0~2 사이여야 한다")
    return value


_LABEL = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _label(text: str) -> str:
    if not _LABEL.fullmatch(text):
        raise argparse.ArgumentTypeError("영문 소문자·숫자와 하이픈만 쓴다 (예: before, before-control)")
    return text


def _command(argv: Sequence[str]) -> str:
    args = [a if a and not any(ch.isspace() for ch in a) else f'"{a}"' for a in argv]
    return " ".join(["python -m eval.repeated_question", *args])


# 측정 조건 옵션과 기본값. --rejudge 는 측정 때 값을 집계 json 에서 읽으므로 이 옵션을 받지 않는다
_MEASURE_OPTIONS: dict[str, Any] = {
    "question": "default",
    "runs": DEFAULT_RUNS,
    "temperature": None,
    "prompt_version": DEFAULT_PROMPT_VERSION,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="날짜 지정 질문을 이력 조건별로 반복 실행해 답변을 판정한다 (실제 LLM 호출)"
    )
    p.add_argument(
        "--question",
        choices=list(TARGETS),
        help="default(기본): 10월 21일 (기간 중 산소포화도가 가장 낮은 날), control: 10월 18일 (통제)",
    )
    p.add_argument("--runs", type=_positive, help=f"조건마다 반복 횟수 (기본 {DEFAULT_RUNS})")
    p.add_argument("--temperature", type=_temperature, help="생략하면 요청에 넣지 않아 서버 기본값을 쓴다")
    p.add_argument(
        "--prompt-version",
        help=f"시스템 프롬프트 버전 (agent/prompts/system_<버전>.md, 기본 {_MEASURE_OPTIONS['prompt_version']})",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=RESULTS_DIR,
        help="실행별 jsonl·집계 json·md 저장 폴더 (기본 eval/results/, git 제외)",
    )
    p.add_argument(
        "--save-doc", action="store_true", help="docs/evals/<날짜>-repeated-question-<label>.md·json 으로도 저장"
    )
    p.add_argument("--label", type=_label, help="--save-doc 기록 이름 (예: before, before-control, after)")
    p.add_argument("--doc-dir", type=Path, default=DOCS_DIR, help="--save-doc 저장 폴더 (기본 docs/evals/)")
    p.add_argument(
        "--rejudge",
        type=Path,
        metavar="JSONL",
        help="저장된 실행별 기록(*_records.jsonl)을 LLM 호출 없이 지금 판정 기준으로 다시 판정한다. "
        "--save-doc 이면 docs 의 같은 측정 기록을 바꾼다",
    )
    args = p.parse_args(argv)
    if args.rejudge is not None:
        given = next((name for name in [*_MEASURE_OPTIONS, "label"] if getattr(args, name) is not None), None)
        if given is not None:
            p.error(f"--rejudge 는 측정 때 값을 쓰므로 --{given.replace('_', '-')} 를 함께 쓰지 않는다")
        return args
    for name, default in _MEASURE_OPTIONS.items():
        if getattr(args, name) is None:
            setattr(args, name, default)
    if args.save_doc and args.label is None:
        p.error("--save-doc 에는 --label 이 필요하다")
    return args


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    started = datetime.now()
    if args.rejudge is not None:
        run_rejudge(args, started, _command(argv))
        return
    docs = doc_paths(args.doc_dir, started.date(), args.label) if args.save_doc else ()
    if existing := [path for path in docs if path.exists()]:
        raise SystemExit(f"이미 있는 기록입니다: {existing[0]}. --label 을 바꾸세요")
    try:
        load_system_prompt(args.prompt_version)
    except FileNotFoundError:
        raise SystemExit(f"프롬프트 {args.prompt_version!r} 가 없습니다 (agent/prompts/system_<버전>.md)") from None
    try:
        ensure_tracing_disabled()
    except TracingEnabledError as e:
        raise SystemExit(f"LangSmith 추적이 켜져 있어 실행하지 않습니다: {', '.join(e.sources)}") from None
    try:
        settings = get_settings()
    except ValidationError:
        raise SystemExit("LLM_API_KEY가 없습니다. LLM_API_KEY를 넣은 뒤 다시 실행하세요.") from None
    name = f"{started:%Y%m%d-%H%M%S}_repeated_question_{args.question}"
    records_path = args.out / f"{name}_records.jsonl"
    report, _ = asyncio.run(run_eval(settings, args, records_path, command=_command(argv)))
    print("\n" + to_markdown(report))
    md_path, json_path = args.out / f"{name}.md", args.out / f"{name}.json"
    write_report(report, md_path, json_path)
    print(f"실행별 기록: {records_path}\n집계: {json_path}\n요약: {md_path}")
    if docs:
        write_report(report, *docs)
        print(f"평가 기록: {docs[0]}\n평가 기록 json: {docs[1]}")


if __name__ == "__main__":
    main()
