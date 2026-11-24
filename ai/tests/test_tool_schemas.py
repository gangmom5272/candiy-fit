import inspect
import json
import re
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from coach_ai.agent.executor import tool_specs
from coach_ai.tools.results import PeriodAverage, PeriodComparison
from coach_ai.tools.schemas import (
    METRICS,
    OVERVIEW_METRICS,
    PERIODS,
    PUBLIC_METRICS,
    TOOL_INPUTS,
    ComparePeriods,
    GetDailyMessage,
    GetDailyTrend,
    GetExerciseSessions,
    GetHealthOverview,
    GetLifestyleScore,
    GetMetricSummary,
    GetSleepRecords,
    PublicMetric,
    SleepAnalysis,
    public_metrics,
)

VALID_ARGS = {
    "get_metric_summary": {"metric": "steps", "period": "last_7_days"},
    "get_daily_trend": {"metric": "heart_rate", "period": "this_week"},
    "get_sleep_records": {"period": "last_14_days"},
    "compare_periods": {"metric": "sleep", "current": "this_week", "baseline": "last_week"},
    "get_health_overview": {"period": "yesterday"},
    "get_exercise_sessions": {"period": "last_month"},
    "get_lifestyle_score": {"period": "days_ago:0"},
    "get_daily_message": {},
}

SUBJECT_WORDS = ("user", "owner", "account", "member", "subject", "patient", "person", "uid", "email")

SLEEP_RECORDS_USE = "밤별 기록(취침·기상 시각, 총 수면, 단계별 시간)은 이 도구로 조회한다."
# 수면 기록을 묻는 질문에 나오는 말. 기간 칸 설명에 수면 문장이 없어 도구 설명에 둔다
SLEEP_RECORDS_CLUES = ("수면 기록을 보여 달라는 질문", "잠든·깬 시각", "수면 단계(깊은 잠·렘수면·얕은 잠)")
TREND_SLEEP_USE = (
    "수면은 날짜별 수면 시간의 흐름이나 비교만 이 도구로 조회하고, 밤별 기록은 get_sleep_records 로 조회한다."
)
# 수면 기록 도구 설명은 세 줄이고, 분석을 넣는 조건과 분석마다의 기준은 analysis 칸 설명에만 둔다. 도구 설명에
# 조건이 있거나 칸 설명에 지표 이름이 있으면 분석이 아닌 수면·산소포화도 질문의 도구 선택이 흔들린다 (측정 기록 15.4)
SLEEP_RECORDS_DESCRIPTION = (
    "밤별 기록(취침·기상 시각, 총 수면, 단계별 시간)은 이 도구로 조회한다.\n"
    "수면 기록을 보여 달라는 질문, 잠든·깬 시각, 수면 단계(깊은 잠·렘수면·얕은 잠)를 묻는 질문이 해당한다.\n"
    "낮에 잔 주 수면도 포함하나 별도 낮잠은 제외한다. 기간 요약도 반환한다."
)
SLEEP_ANALYSIS_DESC = (
    "수면을 평가하거나 이유, 수면 습관, 개선 방법, 수면 중 이상 신호, 조건에 따른 차이를 물을 때 넣는다. "
    "평가·이유·수면 습관·개선 방법은 pattern, 이상 신호는 그 이유를 물어도 signals, "
    "운동·걸음·주말 같은 조건에 따른 차이는 factors. "
    "얼마나 잤는지 같은 사실은 넣지 않는다. 질문에 기간이 없으면 period 를 넣지 않는다"
)
# 기간 칸 설명에 보여 주는 새 기간 표현
EXPRESSION_FORMS = (
    "days_ago:N",
    "recent_days:N",
    "weeks_ago:N",
    "months_ago:N",
    "recent_months:N",
    "date:MM-DD",
    "dates:MM-DD..MM-DD",
    "weekday:요일",
)
PERIOD_FIELDS = {"period", "current", "baseline"}
# 하루만 조회하는 점수 도구의 기간 칸에 보여 주는 표현
DAY_FORMS = ("days_ago:N", "date:MM-DD", "weekday:요일")
# 기간 칸 7곳의 설명과 점수 도구 기간 칸의 설명 (표현 규칙은 시스템 프롬프트 기간 절에 한 번만 둔다)
PERIOD_DESC = (
    "기간(한국 시간). days_ago:N, recent_days:N, weeks_ago:N, months_ago:N, recent_months:N, "
    "date:MM-DD, dates:MM-DD..MM-DD, weekday:요일"
)
SCORE_PERIOD_DESC = "점수 날짜 하루(한국 시간). days_ago:N, date:MM-DD, weekday:요일"
DAY_TOOLS = {"get_lifestyle_score"}
DATASETS_DIR = Path(__file__).resolve().parents[1] / "eval" / "datasets"


def test_registry_has_eight_tools():
    # 한마디 도구는 맨 끝에 더하고 기존 도구의 순서는 그대로 둔다
    assert list(TOOL_INPUTS) == list(VALID_ARGS)
    assert TOOL_INPUTS["get_metric_summary"] is GetMetricSummary
    assert TOOL_INPUTS["get_daily_trend"] is GetDailyTrend
    assert TOOL_INPUTS["get_sleep_records"] is GetSleepRecords
    assert TOOL_INPUTS["compare_periods"] is ComparePeriods
    assert TOOL_INPUTS["get_health_overview"] is GetHealthOverview
    assert TOOL_INPUTS["get_exercise_sessions"] is GetExerciseSessions
    assert TOOL_INPUTS["get_lifestyle_score"] is GetLifestyleScore
    assert TOOL_INPUTS["get_daily_message"] is GetDailyMessage


@pytest.mark.parametrize("name", list(VALID_ARGS))
def test_valid_args_from_dict_and_json(name):
    model = TOOL_INPUTS[name]
    args = VALID_ARGS[name]
    # 기본값이 있는 선택 인자(수면 기록의 include_non_main)는 적지 않으면 기본값이라 비교에서 뺀다
    assert model.model_validate(args).model_dump(exclude_defaults=True) == args
    # LLM 도구 호출 인자는 JSON 문자열로 온다
    assert model.model_validate_json(json.dumps(args)).model_dump(exclude_defaults=True) == args


@pytest.mark.parametrize("name", list(VALID_ARGS))
@pytest.mark.parametrize("extra", ["user_id", "end_user_id", "owner", "account_id", "start_date"])
def test_rejects_unknown_fields(name, extra):
    with pytest.raises(ValidationError):
        TOOL_INPUTS[name].model_validate({**VALID_ARGS[name], extra: "someone-else"})


@pytest.mark.parametrize(
    "args",
    [
        {"metric": "weight", "period": "last_7_days"},
        {"metric": "steps", "period": "last_90_days"},
        {"metric": "Steps", "period": "last_7_days"},
        {"metric": "steps", "period": "2026-10-01~2026-10-07"},
        {"metric": "steps"},
        {"metric": 1, "period": "today"},
    ],
)
def test_rejects_bad_values(args):
    with pytest.raises(ValidationError):
        GetMetricSummary.model_validate(args)


def test_compare_periods_rejects_same_period():
    with pytest.raises(ValidationError):
        ComparePeriods.model_validate({"metric": "steps", "current": "last_week", "baseline": "last_week"})


def test_compare_periods_description_says_result_has_both_periods():
    # 결과의 current·baseline 에 두 기간의 평균과 기록 일수가 있다.
    # 설명에 없으면 같은 기간의 기간 요약을 함께 조회할 수 있다
    description = ComparePeriods.model_json_schema()["description"]
    assert description == (
        "한 지표의 두 기간 평균을 비교한다(차이, 변화율).\n결과에는 두 기간의 평균과 기록 일수도 함께 들어 있다."
    )
    assert {"current", "baseline"} <= set(PeriodComparison.model_fields)
    assert {"avg", "days_with_data"} <= set(PeriodAverage.model_fields)


def test_tools_with_period_accept_week_before_last():
    args = {"metric": "oxygen_saturation", "current": "last_week", "baseline": "week_before_last"}
    assert ComparePeriods.model_validate(args).baseline == "week_before_last"
    for name in ("get_metric_summary", "get_daily_trend", "get_sleep_records", "get_health_overview"):
        args = {**VALID_ARGS[name], "period": "week_before_last"}
        # LLM 도구 호출 인자는 JSON 문자열로 온다
        assert TOOL_INPUTS[name].model_validate_json(json.dumps(args)).period == "week_before_last", name


def test_enums_match_spec():
    assert METRICS == ("steps", "heart_rate", "oxygen_saturation", "sleep", "exercise")
    assert set(PERIODS) == {
        "today",
        "yesterday",
        "last_7_days",
        "last_14_days",
        "last_30_days",
        "this_week",
        "last_week",
        "week_before_last",
        "this_month",
        "last_month",
    }


def test_every_tool_metric_has_public_code():
    assert set(PUBLIC_METRICS) == set(METRICS)
    assert set(get_args(PublicMetric)) == {"steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises"}
    assert set(PUBLIC_METRICS.values()) <= set(get_args(PublicMetric))


def test_public_codes_follow_api_spec():
    # API 명세 2.2 의 공개 지표 코드. 복수형 규칙으로 추측하지 않고 지표마다 적는다
    assert PUBLIC_METRICS == {
        "steps": "steps",
        "heart_rate": "heart_rates",
        "oxygen_saturation": "oxygen_saturations",
        "sleep": "sleeps",
        "exercise": "exercises",
    }


def test_public_metrics_keeps_order_and_drops_duplicates():
    assert public_metrics(["sleep", "exercise", "heart_rate", "sleep", "oxygen_saturation", "exercise"]) == [
        "sleeps",
        "exercises",
        "heart_rates",
        "oxygen_saturations",
    ]
    assert public_metrics([]) == []


def test_metric_tools_accept_exercise():
    for model in (GetMetricSummary, GetDailyTrend):
        assert model.model_validate({"metric": "exercise", "period": "this_week"}).metric == "exercise"
    args = {"metric": "exercise", "current": "this_week", "baseline": "last_week"}
    assert ComparePeriods.model_validate(args).metric == "exercise"
    for spec in tool_specs():
        properties = spec["function"]["parameters"]["properties"]
        if "metric" in properties:
            assert properties["metric"]["enum"][-1] == "exercise", spec["function"]["name"]


def test_overview_follows_home_cards_without_oxygen():
    # 홈 화면 카드 순서다. 산소포화도는 get_metric_summary 로 따로 조회한다
    assert OVERVIEW_METRICS == ("steps", "exercise", "sleep", "heart_rate")
    schema = GetHealthOverview.model_json_schema()
    assert set(schema["properties"]) == {"period"}
    assert schema["description"] == "걸음 수·운동 시간·수면·심박수 4개 지표의 기간 요약을 한 번에 조회한다."
    with pytest.raises(ValidationError):
        GetHealthOverview.model_validate({"period": "this_week", "metric": "exercise"})


def _property_names(schema: object) -> set[str]:
    names: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                names.update(value)
            names |= _property_names(value)
    elif isinstance(schema, list):
        for item in schema:
            names |= _property_names(item)
    return names


@pytest.mark.parametrize("name", list(TOOL_INPUTS))
def test_no_subject_fields_in_schema(name):
    schema = TOOL_INPUTS[name].model_json_schema()
    for prop in _property_names(schema):
        assert not any(word in prop.lower() for word in SUBJECT_WORDS), prop
    assert schema.get("additionalProperties") is False


def test_summary_description_explains_lowest_and_highest_day():
    # 요약의 최저·최고는 그날 하루 값이라 하루 안의 최저·최고처럼 읽히지 않게 설명한다
    description = GetMetricSummary.model_json_schema()["description"]
    assert "(lowest_day·highest_day)의 값은 그날 하루 값이고, 하루 안에서 측정한 최저·최고가 아니다" in description


def test_summary_and_trend_descriptions_separate_uses():
    """요약·추이 도구 설명 → 쓰임이 나뉘고, 주와 요일로 말한 날의 하루 값은 추이 쪽에 있다."""
    summary = GetMetricSummary.model_json_schema()["description"]
    trend = GetDailyTrend.model_json_schema()["description"]
    one_day = "하루만 조회하는 기간(오늘, 어제, 날짜·요일 하루)의 값"
    assert summary.index(one_day) < summary.index("평균·합계")
    assert "주와 요일 하루" not in summary
    assert trend.startswith("한 지표의 여러 날 값을 날짜별로 조회한다.")
    for text in ("평균·합계", "가장 낮은·높은 날", "심박의 기간 측정 최저·최고", one_day):
        assert text in summary
    for text in ("날짜별 값의 흐름이나 목록", "주와 요일로 말한 날(지난주 X요일)의 하루 값", "날짜끼리 비교"):
        assert text in trend
    assert "하루만 조회하는 기간의 값은 get_metric_summary 로 조회한다" in trend
    # 같은 지표·기간을 두 도구로 함께 조회하면 결과가 겹쳐 입력만 늘어난다
    together = "같은 지표·같은 기간을 get_metric_summary 와 get_daily_trend 로 함께 조회하지 않는다"
    assert together in trend and together not in summary


def test_exercise_descriptions_separate_summary_and_daily_values():
    metric = GetMetricSummary.model_json_schema()["properties"]["metric"]["description"]
    assert metric.endswith(", exercise=하루 운동 시간")
    summary = GetMetricSummary.model_json_schema()["description"]
    assert "합계(걸음 수·운동 시간)" in summary
    assert "운동은 하루·기간의 시간·횟수·칼로리·거리를 반환한다." in summary
    trend = GetDailyTrend.model_json_schema()["description"]
    assert "운동은 날짜마다 시간·횟수·칼로리·거리를 반환하며, 운동 0인 날을 셀 때도 쓴다." in trend
    assert "운동 종류는 나누지 않는다." in trend
    assert "하루만 조회하는 기간의 값은 get_metric_summary" in trend


def test_exercise_sessions_description_separates_sessions_from_exercise_time():
    schema = GetExerciseSessions.model_json_schema()
    # 운동 종류를 지정하는 인자가 없다. 종류별 값은 결과에 모두 들어 있다
    assert set(schema["properties"]) == {"period"}
    boundary = (
        "운동 전체의 시간·횟수·칼로리·거리 합계·평균은 하루·기간 모두 get_metric_summary(metric=exercise)로 조회한다."
    )
    for text in (
        "운동 종류",
        "종류별 횟수·거리·칼로리",
        "종류를 말한 운동을 한 날을 찾을 때도 쓴다.",
        "운동 시각·운동 중 심박·인정 여부",
        "인정 여부",
        boundary,
    ):
        assert text in schema["description"]
    # 여러 종류에 걸치는 운동 이름은 먼저 조회하고, 결과에 해당하는 종류가 여럿일 때만 되묻는다
    ask = "운동 이름이 모호하면 먼저 조회해 해당 종류가 여럿일 때만 되묻고"
    one = "하나면 그 종류로 답한다."
    for text in (ask, one, "헬스는 근력 운동과 웨이트 트레이닝에 걸친다"):
        assert text in schema["description"]


def test_score_description_lists_how_score_is_calculated():
    # 계산 방식만 묻는 질문에도 점수 도구를 쓰게 기존 목록 문장에 넣는다. 결과의 수면 영역 rule 이 점수 기준이다
    description = GetLifestyleScore.model_json_schema()["description"]
    assert "점수가 그렇게 나온 이유, 점수를 어떻게 계산하는지,\n점수를 올리는 방법을 물을 때와" in description
    assert description.count("계산") == 1


def test_sleep_records_and_trend_descriptions_separate_sleep_uses():
    assert SLEEP_RECORDS_USE in GetSleepRecords.model_json_schema()["description"]
    description = GetSleepRecords.model_json_schema()["description"]
    assert "낮에 잔 주 수면도 포함하나 별도 낮잠은 제외한다." in description
    for clue in SLEEP_RECORDS_CLUES:
        assert clue in GetSleepRecords.model_json_schema()["description"], clue
    assert TREND_SLEEP_USE in GetDailyTrend.model_json_schema()["description"]


def test_sleep_records_include_non_main_is_optional_boolean():
    [spec] = [s for s in tool_specs() if s["function"]["name"] == "get_sleep_records"]
    parameters = spec["function"]["parameters"]
    # 분석(analysis)은 질문에 기간이 없으면 도구가 기간을 정해 period 도 선택 칸이다
    assert "required" not in parameters
    prop = parameters["properties"]["include_non_main"]
    assert (prop["type"], prop["default"]) == ("boolean", False)
    # 낮잠을 물을 때 쓰는 인자라는 것은 도구 설명 본문이 아니라 필드 설명 한 문장에 둔다
    assert prop["description"] == "낮잠을 물을 때 참"


def test_sleep_records_analysis_follows_calculation_rules():
    assert get_args(SleepAnalysis) == ("pattern", "signals", "factors")
    [spec] = [s for s in tool_specs() if s["function"]["name"] == "get_sleep_records"]
    properties = spec["function"]["parameters"]["properties"]
    # 조건별 비교(factors)는 11.4 의 조건 일곱 개를 모두 비교해 조건을 고르는 칸이 없다
    assert list(properties) == ["period", "include_non_main", "analysis"]
    assert (properties["analysis"]["type"], properties["analysis"]["enum"]) == ("string", list(get_args(SleepAnalysis)))
    # 도구 정의는 모든 호출의 고정 입력이라 선택 칸에 null 선택지와 기본값 null 을 적지 않는다
    for name in ("period", "analysis"):
        assert "anyOf" not in properties[name] and "default" not in properties[name], name
    assert properties["analysis"]["description"] == SLEEP_ANALYSIS_DESC
    assert spec["function"]["description"] == SLEEP_RECORDS_DESCRIPTION
    # 칸 설명에 다른 도구가 다루는 지표 이름을 적지 않는다
    assert not re.search("산소포화도|심박|걸음 수", SLEEP_ANALYSIS_DESC)


def test_sleep_records_description_has_no_line_break_inside_a_sentence():
    # 도구 설명은 줄마다 들여쓰기·빈 줄 없이 이어지고, 수면 기록 도구 설명의 줄은 모두 문장으로 끝난다
    descriptions = {s["function"]["name"]: s["function"]["description"] for s in tool_specs()}
    for name, description in descriptions.items():
        assert all(line and line == line.strip() for line in description.split("\n")), name
    sleep = descriptions["get_sleep_records"]
    assert sleep == inspect.cleandoc(GetSleepRecords.__doc__ or "")
    assert all(line.endswith(".") for line in sleep.split("\n"))


@pytest.mark.parametrize(
    "args",
    [
        {"analysis": "pattern"},
        {"analysis": "signals", "period": "recent_days:7"},
        {"analysis": "factors"},
        {"analysis": "pattern", "include_non_main": True},
        {"period": "days_ago:0", "include_non_main": True},
    ],
)
def test_sleep_records_accept_analysis_without_period(args):
    assert GetSleepRecords.model_validate(args).model_dump(exclude_defaults=True) == args


@pytest.mark.parametrize(
    "args",
    [
        # 분석이 없는 기본 조회는 기간이 있어야 한다
        {},
        {"include_non_main": True},
        {"period": None},
        {"analysis": "trend"},
        # 조건을 고르는 칸은 없다
        {"analysis": "factors", "condition": "late_exercise"},
        {"period": "recent_days:7", "condition": "weekend"},
    ],
)
def test_sleep_records_reject_missing_period_and_condition(args):
    with pytest.raises(ValidationError):
        GetSleepRecords.model_validate(args)


def test_period_fields_show_only_new_expressions_briefly():
    """기간 칸 → 7곳은 _PERIOD_DESC 그대로, 점수 도구는 하루 표현 문구이고 이름 기간은 보이지 않는다."""
    for spec in tool_specs():
        function = spec["function"]
        for name, prop in function["parameters"]["properties"].items():
            if name not in PERIOD_FIELDS:
                continue
            # 선택지를 나열하지 않고 형식만 보여 준다. 이름 기간은 해석에서 계속 받지만 보여 주지 않는다
            assert prop["type"] == "string" and "enum" not in prop, (function["name"], name)
            # 점수 도구는 하루만 조회해 하루 표현만 보여 준다
            if function["name"] in DAY_TOOLS:
                assert prop["description"] == SCORE_PERIOD_DESC, (function["name"], name)
            else:
                assert prop["description"].endswith(PERIOD_DESC), (function["name"], name)
            shown = DAY_FORMS if function["name"] in DAY_TOOLS else EXPRESSION_FORMS
            for form in EXPRESSION_FORMS:
                assert prop["description"].count(form) == (form in shown), (function["name"], name, form)
            # 기간 칸마다 같은 설명이 들어가 표현 규칙은 시스템 프롬프트에 한 번만 둔다. 형식 8가지만 적는 길이다
            assert len(prop["description"]) <= 140, (function["name"], name)
        text = json.dumps(spec, ensure_ascii=False)
        for period in PERIODS:
            assert not re.search(rf"\b{period}\b", text), (function["name"], period)


def test_period_fields_use_dots_only_for_date_range():
    """기간 칸 설명 → ".." 는 dates:MM-DD..MM-DD 에만 있고 요일 표기에는 없다 (시작..끝 범위 표현과 같은 기호)."""
    for spec in tool_specs():
        function = spec["function"]
        for name, prop in function["parameters"]["properties"].items():
            if name in PERIOD_FIELDS:
                description = prop["description"]
                assert description.replace("dates:MM-DD..MM-DD", "").count("..") == 0, (function["name"], name)
                assert "mon..sun" not in description, (function["name"], name)


def test_period_rules_are_not_repeated_in_tool_definitions():
    text = json.dumps(tool_specs(), ensure_ascii=False)
    # 수면 날짜 규칙, 날짜 지정 규칙, 한도 안내는 시스템 프롬프트와 도구 결과에 둔다
    for words in ("어젯밤", "깬 날", "특정 날짜는", "덮을 수 없는 기간", "3주 전", "12개월", "31일"):
        assert words not in text, words
    assert sum(text.count(form) for form in EXPRESSION_FORMS) == 7 * len(EXPRESSION_FORMS) + len(DAY_FORMS)


@pytest.mark.parametrize("name", list(TOOL_INPUTS))
def test_schema_has_description_for_llm(name):
    schema = TOOL_INPUTS[name].model_json_schema()
    assert schema.get("description")
    assert all(p.get("description") for p in schema["properties"].values())


def test_score_description_limits_use_to_score_and_resting_heart_rate():
    schema = GetLifestyleScore.model_json_schema()
    # 사용자를 가리키는 칸 없이 기간 하나만 받는다
    assert set(schema["properties"]) == {"period"}
    assert schema["properties"]["period"]["description"].startswith("점수 날짜 하루(한국 시간).")
    description = schema["description"]
    for text in (
        "하루의 생활 습관 점수(홈 화면의 오늘의 점수)를 조회한다.",
        "저장된 점수를 그대로 반환한다.",
        "등급(훌륭·안정·관심·관리)",
        "홈 화면의 안정 시 심박수(수면 중 평균 심박)",
        "점수나 등급을 묻지 않으면 쓰지 않는다.",
        "수면·심박 점수는 그날 깬 잠으로 매긴다.",
    ):
        assert text in description, text
    # 전체 요약 도구 설명은 바꾸지 않는다. 점수를 묻는지로 두 도구가 나뉜다
    assert "점수" not in GetHealthOverview.model_json_schema()["description"]


def test_daily_message_description_limits_use_to_daily_message_questions():
    [spec] = [s for s in tool_specs() if s["function"]["name"] == "get_daily_message"]
    # 날짜는 서버가 정한 질문 날짜 하나라 인자가 없고, 인자를 붙인 호출은 스키마 검사에서 거부된다
    assert spec["function"]["parameters"] == {"additionalProperties": False, "properties": {}, "type": "object"}
    description = spec["function"]["description"]
    for text in (
        "홈 화면의 오늘의 한마디(오늘 기록으로 고른 응원 문구)를 조회한다.",
        "오늘 것만 있다.",
        "오늘의 한마디 문구와 그 문구가 나온 이유를 물을 때만 쓴다.",
        "오늘의 한마디를 묻지 않으면 쓰지 않는다.",
        "결과에는 점수와 걸음 수 같은 기록 값이 없다.",
        "응원이나 조언을 해 달라는 요청에는 이 도구를 부르지 않고 직접 답한다.",
    ):
        assert text in description, text
    # 응원·조언 요청은 거절하지 않고 이 도구 없이 답한다. 쓰지 않는다는 문장은 응원 거절로 읽힐 수 있다
    assert "해 달라는 요청에는 쓰지 않는다" not in description
    # 다른 도구 설명에는 한마디를 넣지 않는다. 오늘의 한마디를 묻는지로 도구가 나뉜다
    others = [s["function"]["description"] for s in tool_specs() if s["function"]["name"] != "get_daily_message"]
    assert [text for text in others if "한마디" in text] == []


def test_tool_definitions_have_no_measured_questions():
    # 도구 설명은 규칙으로 쓰고 측정 문항의 질문을 넣지 않는다
    text = json.dumps(tool_specs(), ensure_ascii=False)
    questions = {
        json.loads(line)["question"]
        for path in DATASETS_DIR.glob("tool_selection_*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert len(questions) > 400
    assert [q for q in questions if q in text] == []
