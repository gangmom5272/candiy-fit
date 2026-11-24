"""수면 시간·피로 재질문의 조회와 설명. 다른 지표·기간·조건 비교는 보존한다."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from pydantic import ValidationError

from coach_ai.agent.rules.reused_numbers import health_values
from coach_ai.agent.rules.sleep_period import has_period_word
from coach_ai.tools.periods import PeriodError, resolve_period
from coach_ai.tools.schemas import TOOL_INPUTS


def sleep_plan_followup(question: str, previous: str) -> bool:
    """기간 없는 개인 수면 계획 후속. 일반 조언·다른 주제·기록 기간 요청은 제외한다."""
    return bool(
        re.search(r"수면|취침|잤|잠든|잔 시간", previous)
        and re.search(r"그럼|그러면|오늘부터|이제", question)
        and re.search(r"뭘|뭐|어떻게|개선|하면", question)
        and not has_period_word(re.sub(r"오늘부터|오늘|이제", "", question))
        and not re.search(
            r"일반|조회\s?(?:없이|하지)|운동|걸음|심박|산소|점수|지난|최근|주말|평일|\d+\s?(?:일|주|달)", question
        )
        and not sleep_closing(question)
    )


def sleep_closing(question: str) -> bool:
    """새 요청이 없는 감사·실천 다짐에는 재조회를 요구하지 않는다."""
    return bool(
        re.search(
            r"(?:알겠(?:어|어요)|고마워(?:요)?|감사(?:해|해요|합니다)|(?:해|자|실천해)\s?볼게(?:요)?)[.!~\s]*$",
            question,
        )
        and not re.search(
            r"[?？]|뭐|뭘|몇|궁금|어떻게|왜|언제|얼마|알려\s?(?:줘|주세요|줄래)|조회|분석|비교|[해봐]\s?줘", question
        )
    )


def unqueried_sleep_clock(answer: str, question: str) -> bool:
    """이전 답에 없던 취침·기상 시각도 조회한 평균처럼 말하면 재조회한다."""
    supplied = health_values(question, general=False)
    for sentence in re.split(r"[.!?\n]", answer):
        if re.search(r"평균|기록|측정", sentence) and re.search(r"취침|기상|잠든|깬", sentence):
            if any(kind == "clock" for kind, _ in health_values(sentence) - supplied):
                return True
    return False


def fatigue_claim(question: str) -> bool:
    return bool(
        re.search(r"피곤|피로", question)
        and re.search(r"수면|잠|잤|자고|자도|잔", question)
        and "시간" in question
        and not re.search(r"조회하지|조회 없이|일반적|운동|걸음|주말|평일|조건|다른 사람", question)
    )


def signals_question(question: str) -> bool:
    return bool(
        re.search(r"산소|spo2", question, re.I)
        and re.search(r"들쭉|변동|오르내|이상", question)
        and not re.search(r"평균|최저|최고|운동|걸음|주말|평일|조건", question)
    )


def sleep_results(messages: Sequence[AnyMessage], analysis: str) -> list[dict]:
    results = []
    for m in messages:
        if isinstance(m, ToolMessage) and m.name == "get_sleep_records" and isinstance(m.content, str):
            try:
                body = json.loads(m.content)
            except json.JSONDecodeError:
                continue  # 게이트웨이의 거부·한도 안내는 JSON이 아니다.
            result = body.get("result") if isinstance(body, dict) else None
            if isinstance(result, dict) and result.get("analysis") == analysis:
                results.append(result)
    return results


def period_key(expression: str, today: date | None):
    if today is None:
        return expression
    try:
        span = resolve_period(expression, today)
        return span.start.isoformat(), span.end.isoformat()
    except PeriodError:
        return None


def normalize_calls(
    message: AIMessage, question: str, *, messages: Sequence[AnyMessage] = (), today: date | None = None
) -> AIMessage:
    """모델이 실제로 요청한 수면 조회만 보정한다. 무조회·거절 답에서 임의 조회하지 않는다."""
    calls = [dict(c, args=dict(c["args"])) for c in message.tool_calls]
    valid = set()
    for c in calls:
        schema = TOOL_INPUTS.get(c["name"])
        if schema is not None:
            try:
                schema.model_validate(c["args"])
                valid.add(c["id"])
            except ValidationError:
                pass  # 잘못된 호출은 원형 그대로 게이트웨이에서 거부한다.
    if fatigue_claim(question):
        for c in calls:
            if c["id"] not in valid:
                continue
            if c["name"] == "get_sleep_records" and c["args"].get("analysis") is None:
                c["args"]["analysis"] = "pattern"
            elif c["name"] == "get_metric_summary" and c["args"].get("metric") == "sleep":
                c["name"] = "get_sleep_records"
                c["args"] = {"analysis": "pattern", "period": c["args"]["period"]}
        if not any(c["name"] == "get_sleep_records" and c["args"].get("analysis") == "pattern" for c in calls):
            for c in calls:
                if c["id"] in valid and c["name"] == "compare_periods" and c["args"].get("metric") == "sleep":
                    calls.append(
                        {
                            "name": "get_sleep_records",
                            "args": {"analysis": "pattern", "period": c["args"]["current"]},
                            "id": f"{c['id']}-pattern",
                            "type": "tool_call",
                        }
                    )
                    break
    if signals_question(question):
        periods = {
            period_key(c["args"].get("period") or "recent_days:14", today)
            for c in calls
            if c["id"] in valid and c["name"] == "get_sleep_records" and c["args"].get("analysis") == "signals"
        }
        for result in sleep_results(messages, "signals"):
            period = result["period"]
            periods.add((period["start"], period["end"]) if today else period["period"])
        periods.discard(None)
        calls = [
            c
            for c in calls
            if not (
                c["name"] == "get_metric_summary"
                and c["args"].get("metric") == "oxygen_saturation"
                and c["id"] in valid
                and period_key(c["args"]["period"], today) in periods
            )
        ]
    unique = []
    seen = set()
    for call in calls:
        if call["name"] == "get_sleep_records":
            try:
                args = TOOL_INPUTS[call["name"]].model_validate(call["args"]).model_dump(mode="json")
            except ValidationError:
                unique.append(call)
                continue
            period = args.pop("period", None)
            default = "recent_days:28" if args.get("analysis") == "factors" else "recent_days:14"
            key = (json.dumps(args, sort_keys=True), period_key(period or default, today))
            if key[1] is not None:
                if key in seen:
                    continue
                seen.add(key)
        unique.append(call)
    return message.model_copy(update={"tool_calls": unique}) if unique != message.tool_calls else message


def pattern_result(messages: Sequence[AnyMessage], question: str) -> dict | None:
    if not fatigue_claim(question):
        return None
    # 이번 요청의 ToolMessage만 들어 있다. 과거 답변의 수치는 사용하지 않는다.
    results = sleep_results(messages, "pattern")
    # 현재 기간과 과거 기간을 함께 조회한 경우 현재 수면 설명을 선택한다.
    return next((r for r in results if r.get("sleep_time_note")), results[0] if results else None)


NO_SLEEP_RECORD = (
    "이번 답변에 필요한 수면 기록을 확인하지 못했습니다. "
    "기록을 다시 조회한 뒤 확인해 주세요. 피로의 원인은 추측할 수 없습니다."
)


def grounded_answer(result: dict | None) -> str:
    """불일치·피로 설명은 모델의 자유 문장이 아닌 이번 도구 사실만으로 구성한다."""
    if result is None:
        return NO_SLEEP_RECORD
    note = result.get("sleep_time_note")
    last_night = result.get("last_night_vs_week", "")
    # 도구가 이미 표시한 기간·수치를 그대로 사용한다. 과거 분석에는 어젯밤 문장이 없다.
    if last_night:
        record = last_night.split(". ", 1)[0].rstrip(".")
        record = re.sub(r"이다$", "입니다", record)
        record = re.sub(r"없다$", "없습니다", record) + "."
    else:
        record = f"조회한 기간의 기록은 “{result['text']}”로 정리됩니다."
    paragraphs = [record]
    if note:
        paragraphs.append(note.split(" 이는 집계 기준", 1)[0].replace("있다.", "있습니다."))
    paragraphs.append(
        "말씀하신 시간과 기록의 차이 및 피로의 원인은 이 기록만으로 확인할 수 없습니다. "
        "피로가 계속되거나 걱정되시면 의료진과 상담해 주세요."
    )
    return "\n\n".join(paragraphs)


def signals_answer(messages: Sequence[AnyMessage]) -> str:
    records = [f"조회한 기록은 “{r['text']}”로 정리됩니다." for r in sleep_results(messages, "signals")]
    return "\n\n".join(
        [
            *records,
            "산소포화도 변동의 원인은 이 기록만으로 확인할 수 없습니다. 걱정되는 증상이 있으면 의료진과 상담해 주세요.",
        ]
    )
