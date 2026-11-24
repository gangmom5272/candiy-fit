from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coach_ai.agent.context import CoachProfile

# 판마다 바뀐 점은 docs/agent-interface.md 4장 버전 표와 docs/evals 측정 기록에 있다. 지난 판 파일은 그 측정을
# 같은 조건으로 다시 잴 때 쓰므로 고치지 않는다
DEFAULT_PROMPT_VERSION = "v1.20"
# 템플릿의 날짜 자리. 앞부분이 요청마다 같아야 서버가 프리픽스 캐시를 재사용하므로 템플릿 끝에 둔다
TODAY_SLOT = "{today}"
_WEEKDAYS = "월화수목금토일"
# 예산 때문에 이전 대화 쌍이나 요약을 뺀 호출에만 시스템 프롬프트 끝(이전 대화 요약 뒤)에 덧붙인다
OMITTED_HISTORY_NOTICE = (
    "앞선 대화 일부는 길이 제한으로 빠졌다. "
    "빠진 대화에 있었을 목표, 정정, 기간을 특정할 수 없으면 추측하지 말고 되묻는다."
)
# 이전 대화에 오늘과 다른 날 질문한 쌍이 있을 때만 요약 뒤에 붙인다. 쌍의 질문·답변에는 날짜가 없어 그 대화의
# "오늘"이 이번 질문의 오늘과 섞일 수 있다
OTHER_DAY_HISTORY_NOTICE = "이전 대화에는 다른 날 나눈 내용이 있고, 그 대화의 오늘·어제는 그날 기준이다."


@cache
def load_system_prompt(version: str = DEFAULT_PROMPT_VERSION) -> str:
    """시스템 프롬프트 템플릿. 날짜 자리는 render_system_prompt 로 채운다."""
    return (files(__package__) / f"system_{version}.md").read_text(encoding="utf-8").strip()


def today_text(today: date) -> str:
    """예: 2026년 10월 27일 화요일. 요일은 실행 환경의 로캘과 관계없이 한국어로 쓴다."""
    return f"{today.year}년 {today.month}월 {today.day}일 {_WEEKDAYS[today.weekday()]}요일"


def render_system_prompt(template: str, today: date, profile: CoachProfile | None = None) -> str:
    """템플릿의 날짜 자리에 today 를 넣는다. today 는 도구 기간 계산과 같은 사용자 시간대의 날짜다."""
    system = template.replace(TODAY_SLOT, today_text(today))
    if profile is None:
        return system
    # 프로필이 없는 평가·CLI 입력과 측정된 기존 템플릿은 그대로 유지한다.
    return (
        f"{system}\n\n## 현재 등록 프로필\n\n"
        f"키 {profile.height_cm:f}cm, 몸무게 {profile.weight_kg:f}kg, BMI {profile.bmi:.1f}.\n"
        "이번 요청의 본인 등록값·서버 계산 BMI다(실측·이력 아님). "
        "이 세 값은 도구 없이 답하며 이전 답변·요약보다 현재 프로필을 우선한다. "
        "키·몸무게는 '등록값', BMI는 '서버 계산값'이라고 답에 반드시 명시한다. 수치·단위를 그대로 쓴다. "
        "타인 값 대입, BMI 재계산·등급 판정, "
        "체지방률·과거 체중 추정은 금한다. 다른 건강 기록은 기존 도구 규칙을 따른다."
    )


def other_day_notice(days: Iterable[date | None], today: date) -> str | None:
    """쌍을 질문한 날짜(days)에 today 와 다른 날짜가 있으면 OTHER_DAY_HISTORY_NOTICE 와 오늘 날짜 문장을 반환한다.

    날짜를 모르는 쌍(None)은 세지 않는다.
    """
    if all(day is None or day == today for day in days):
        return None
    return f"{OTHER_DAY_HISTORY_NOTICE} 오늘은 {today_text(today)}이다."


def system_with_history(
    system_prompt: str, summary: str | None, *, omitted: bool = False, day_notice: str | None = None
) -> str:
    """시스템 프롬프트 끝에 이전 대화 요약, day_notice(other_day_notice), omitted 이면 OMITTED_HISTORY_NOTICE 를
    이 순서로 붙인다.

    요약은 시스템 메시지 끝에 붙인다 (시스템 메시지를 하나만 받는 템플릿 대비, 프리픽스 캐시 재사용).
    """
    system = system_prompt if not summary else f"{system_prompt}\n\n## 이전 대화 요약\n\n{summary.strip()}"
    if day_notice:
        system = f"{system}\n\n{day_notice}"
    return f"{system}\n\n{OMITTED_HISTORY_NOTICE}" if omitted else system
