"""답변 형식 세기(eval/answer_format.py) 테스트. 화면 약속은 docs/agent-interface.md 5장 "답변 형식"이다."""

import pytest

from eval.answer_format import AGREED, LABELS, ORDER, OUTSIDE, TABLE_SHAPE, answer_forms, day_by_day_result

TABLE = "| 날짜 | 걸음 |\n|---|---|\n| 10월 24일 (토) | 11,650걸음 |\n| 10월 25일 (일) | 5,394걸음 |"


def test_answer_in_paragraphs_has_no_forms():
    """문단만 쓴 답 → 형식 없음."""
    assert answer_forms("10월 21일 수면 시간은 5시간 40분이에요.\n산소포화도는 95.1%예요.") == []


def test_bold_is_counted_outside_agreement():
    """굵게는 약속 밖이고 보고서 칸 순서가 약속 → 약속 밖 → 표 모양 → 순서다."""
    # 굵게는 쓰지 않는다. 화면이 첫 문단 첫 줄을 큰 요약 글씨로 그려 강조는 그쪽에 맡긴다
    assert list(AGREED) == ["table", "list", "subheading"]
    assert list(OUTSIDE) == ["bold", "heading", "link", "code_block", "html"]
    assert list(ORDER) == ["subheading_first"]
    assert list(LABELS) == [*AGREED, *OUTSIDE, *TABLE_SHAPE, *ORDER]
    answer = f"최근 이틀이에요.\n\n{TABLE}\n\n정리하면\n- 토요일은 **많이** 걸었어요\n1. 일요일은 쉬었어요"
    assert answer_forms(answer) == ["table", "list", "bold"]
    assert [name for name in LABELS if name in {"table", "list", "bold"}] == ["table", "list", "bold"]


@pytest.mark.parametrize(
    ("answer", "forms"),
    [
        ("## 요약\n걸음이 늘었어요.", ["heading"]),
        # 문장 바로 아래 --- 는 화면이 제목으로 그린다. 빈 줄 뒤나 목록 뒤의 --- 는 구분 줄이다
        ("요약\n---\n걸음이 늘었어요.", ["heading"]),
        ("요약\n\n---\n걸음이 늘었어요.", []),
        ("- 걸음\n---", ["list"]),
        ("[출처](https://example.invalid/a) 를 확인하세요.", ["link"]),
        ("https://example.invalid/a 에 있어요.", ["link"]),
        ("```\n걸음 11,650\n```", ["code_block"]),
        ("토요일<br>일요일", ["html"]),
        ("<b>11,650걸음</b>", ["html"]),
        # 사고 채널 표시와 부등호는 HTML 이 아니다
        ("기다려 주세요.\n<|channel>thought\n<channel|>오늘 4,951걸음이에요.", []),
        ("6시간<7시간 이에요.", []),
        # 기호 뒤에 띄어쓰기가 없으면 목록이 아니다
        ("-15분 줄었어요.", []),
    ],
)
def test_forms_outside_agreement(answer, forms):
    """약속 밖 형식(큰 제목, 링크, 코드 블록, HTML)과 형식이 아닌 글 → 기대한 형식만 센다."""
    assert answer_forms(answer) == forms


@pytest.mark.parametrize(
    ("answer", "forms"),
    [
        (f"최근 이틀이에요.\n{TABLE}\n\n끝.", ["table", "no_blank_before"]),
        (f"최근 이틀이에요.\n\n{TABLE}\n일요일이 적어요.", ["table", "no_blank_after"]),
        # 답변 처음과 끝의 표는 앞뒤 빈 줄이 없어도 된다
        (TABLE, ["table"]),
        # 구분선이 없거나 칸 수가 머리 행과 다르면 화면은 표로 그리지 않는다
        ("| 날짜 | 걸음 |\n| 10월 24일 | 11,650걸음 |", ["no_delimiter"]),
        ("| 날짜 | 걸음 |\n|---|\n| 10월 24일 | 11,650걸음 |", ["no_delimiter"]),
        (f"{TABLE}\n\n| 10월 26일 (월) | 7,000걸음 |", ["table", "no_delimiter"]),
    ],
)
def test_table_shape(answer, forms):
    """표 앞뒤 빈 줄과 구분선 → 어긴 모양을 센다."""
    assert answer_forms(answer) == forms


def test_code_block_lines_are_not_tables_or_lists():
    """코드 블록 안의 표·목록·굵게 → code_block 만 센다."""
    assert answer_forms(f"```\n{TABLE}\n- **목록**\n```") == ["code_block"]


PARAGRAPH = "10월 21일 수면은 5시간 40분이에요."


@pytest.mark.parametrize(
    ("answer", "forms"),
    [
        (f"{PARAGRAPH}\n\n### 오늘 할 일\n일찍 주무세요.", ["subheading"]),
        ("문단이에요.\n\n#### 메모\n내용이에요.", ["subheading"]),
        ("문단이에요.\n\n###### 메모\n내용이에요.", ["subheading"]),
        ("문단이에요.\n\n# 요약\n내용이에요.", ["heading"]),
        ("문단이에요.\n\n## 요약\n내용이에요.", ["heading"]),
        ("문단 제목\n===\n내용이에요.", ["heading"]),
        ("문단 제목\n---\n내용이에요.", ["heading"]),
        ("문단이에요.\n\n####### 일곱\n내용이에요.", []),
        ("문단이에요.\n\n###\n내용이에요.", ["subheading"]),
        ("### 기록\n내용이에요.", ["subheading", "subheading_first"]),
        ("\n\n\n### 기록\n내용이에요.", ["subheading", "subheading_first"]),
        ("## 기록\n내용이에요.", ["heading"]),
        ("```\n### x\n```", ["code_block"]),
        ("- ### x", ["list"]),
        ("문단이에요.\n\n- 항목 하나\n\n### 정리\n내용이에요.", ["list", "subheading"]),
    ],
)
def test_subheading_and_heading_levels(answer, forms):
    """### 이하는 소제목, # ## 밑줄형은 큰 제목, 첫 덩어리가 소제목이면 subheading_first → 기대한 이름만 센다."""
    assert answer_forms(answer) == forms


def test_subheading_inside_table_cell_is_only_a_table():
    """표 칸 안의 ### → table 만 센다."""
    assert answer_forms("문단이에요.\n\n| ### | 1 |\n|---|---|\n| ### | 2 |\n\n끝.") == ["table"]


def test_mixed_answer_keeps_labels_order():
    """소제목과 표·목록·굵게가 섞인 답 → LABELS 순서로 반환한다."""
    answer = f"문단이에요.\n\n### 기록\n{PARAGRAPH}\n\n{TABLE}\n\n- **조언** 하나"
    assert answer_forms(answer) == ["table", "list", "subheading", "bold"]
    first = f"### 기록\n{TABLE}\n\n- **조언** 하나"
    assert answer_forms(first) == ["table", "list", "subheading", "bold", "no_blank_before", "subheading_first"]


def daily(*displays):
    """display 값 목록으로 get_daily_trend 도구 호출 기록을 만든다. None 은 값 없는 날이다."""
    points = [
        {"date": f"2026-10-{20 + i}", **({"display": d} if d else {"missing": "미측정"})}
        for i, d in enumerate(displays)
    ]
    return {"name": "get_daily_trend", "reply": "ok", "result": {"result": {"points": points}}}


def sleep(count):
    """밤이 count 개인 get_sleep_records 도구 호출 기록을 만든다."""
    nights = [{"date": f"2026-10-{20 + i}"} for i in range(count)]
    return {"name": "get_sleep_records", "reply": "ok", "result": {"result": {"nights": nights}}}


@pytest.mark.parametrize(
    ("calls", "expected"),
    [
        ([daily("1,000걸음", "2,000걸음")], True),
        ([daily("1,000걸음", None, None, None, None, None, None)], False),
        ([sleep(2)], True),
        ([sleep(1)], False),
        ([{"name": "get_exercise_sessions", "reply": "ok", "result": {"result": {"sessions": [{}, {}, {}]}}}], False),
        ([{**daily("1", "2"), "reply": "not_supported"}], False),
        ([{**daily("1", "2"), "reply": "denied"}], False),
        ([{"name": "get_daily_trend", "reply": "ok", "result": "문자열"}], False),
        ([{"name": "get_daily_trend", "reply": "ok", "result": {"error": "x"}}], False),
        ([{"name": "get_daily_trend", "reply": "ok", "result": {"result": {}}}], False),
        ([{"name": "get_sleep_records", "reply": "ok", "result": {"result": {"nights": "둘"}}}], False),
        (None, False),
        ([], False),
        ([{"name": "get_period_summary", "reply": "ok", "result": {"result": {}}}, sleep(3)], True),
    ],
)
def test_day_by_day_result(calls, expected):
    """도구 호출 기록의 모양 → 이틀 이상 날짜별 값을 받았을 때만 True 이고 예외는 없다."""
    assert day_by_day_result(calls) is expected
