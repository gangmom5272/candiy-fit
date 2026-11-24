"""답변 마크다운 읽기(eval/answer_markdown.py) 테스트. 표 판단은 화면(marked 18.1.1)과 같은 결과인지 확인한다."""

import re

import pytest

from eval.answer_markdown import plain_answer, split_spans

TABLE = "\n".join(
    [
        "최근 이틀 활동량이에요.",
        "",
        "| 날짜 | 걸음 | 운동 |",
        "| --- | ---: | --- |",
        "| 10월 24일 (토) | **11,650걸음** | 43분 |",
        "| 10월 25일 (일) | 5,394걸음 | 0분 |",
        "",
        "일요일만 걸음이 크게 줄었어요.",
    ]
)


def labels(text: str, values: str) -> list[str]:
    """values 에서 공백으로 바뀐 구간의 원래 글."""
    spans = [(m.start(), m.end()) for m in re.finditer(r" {2,}", values)]
    return [text[a:b].strip() for a, b in spans if text[a:b].strip()]


@pytest.mark.parametrize(
    "answer",
    [
        "10월 21일 수면 시간은 5시간 40분이에요.\n산소포화도는 95.1%예요.",
        # 줄 가운데의 | 와 사고 채널 표시는 표가 아니다
        "걸음은 3 | 4 정도예요.",
        "기다려 주세요.\n\n<|channel>thought\n<channel|>오늘 4,951걸음이에요.",
        # 기호 바로 뒤에 띄어쓰기가 없으면 목록이 아니다
        "-15분 줄었어요.\n+713걸음이에요.\n6.5시간 주무셨어요.",
    ],
)
def test_answer_without_markdown_stays_the_same(answer):
    plain = plain_answer(answer)
    assert (plain.text, plain.values, plain.rows) == (answer, answer, ())


def test_bold_markers_are_removed_even_inside_numbers():
    plain = plain_answer("수면은 **6**시간 **30**분, 걸음은 __11,650걸음__이에요.")
    assert plain.text == plain.values == "수면은 6시간 30분, 걸음은 11,650걸음이에요."


def test_list_markers_are_removed_at_line_start():
    answer = "\n".join(
        ["정리하면", "- 수면 6시간 30분", "* 걸음 7,000걸음", "+ 심박 72.3bpm", "  - 하위 항목", "1. 산소포화도 96.1%"]
        + ["2) 기록 5일"]
    )
    plain = plain_answer(answer)
    assert plain.text.splitlines() == [
        "정리하면",
        "수면 6시간 30분",
        "걸음 7,000걸음",
        "심박 72.3bpm",
        "하위 항목",
        "산소포화도 96.1%",
        "기록 5일",
    ]
    assert plain.rows == ()


def test_table_rows_become_lines_without_delimiter_and_pipes():
    plain = plain_answer(TABLE)
    lines = plain.text.splitlines()
    assert lines == [
        "최근 이틀 활동량이에요.",
        "",
        "날짜 · 걸음 · 운동",
        "10월 24일 (토) · 11,650걸음 · 43분",
        "10월 25일 (일) · 5,394걸음 · 0분",
        "",
        "일요일만 걸음이 크게 줄었어요.",
    ]
    assert "|" not in plain.text and "---" not in plain.text
    # 머리 행과 각 행이 한 구간이다
    assert [plain.text[a:b] for a, b in plain.rows] == lines[2:5]
    # 머리 행과 첫 열은 수치로 읽지 않는다. values 는 그 구간을 공백으로 바꾼 글이라 위치가 text 와 같다
    assert len(plain.values) == len(plain.text)
    assert labels(plain.text, plain.values) == ["날짜 · 걸음 · 운동", "10월 24일 (토)", "10월 25일 (일)"]
    assert plain.values.splitlines()[3].strip() == "· 11,650걸음 · 43분"


def test_table_without_outer_pipes_is_a_table():
    plain = plain_answer("날짜 | 걸음\n---|---\n10월 24일 | 11,650걸음")
    assert plain.text == "날짜 · 걸음\n10월 24일 · 11,650걸음"
    assert len(plain.rows) == 2


@pytest.mark.parametrize(
    "answer",
    [
        # 구분선의 칸이 머리 행보다 적거나 구분선이 없으면 화면은 표로 그리지 않는다
        "| 날짜 | 걸음 |\n|---|\n| 10월 24일 | 11,650걸음 |",
        "| 날짜 | 걸음 |\n| 10월 24일 | 11,650걸음 |",
        # 코드 블록 안은 그대로 둔다
        "```\n| 날짜 | 걸음 |\n|---|---|\n| 10월 24일 | 11,650걸음 |\n- **목록**\n```",
    ],
)
def test_lines_that_screen_does_not_draw_as_table_stay(answer):
    plain = plain_answer(answer)
    assert (plain.text, plain.rows) == (answer, ())


def test_table_ends_at_line_without_pipe():
    # 화면은 빈 줄 없이 이어진 문장을 표의 행으로 넣지만, 채점은 그 문장을 표 밖 문장으로 읽는다
    plain = plain_answer("| 날짜 | 걸음 |\n|---|---|\n| 10월 24일 | 11,650걸음 |\n일요일은 줄었어요.")
    assert plain.text == "날짜 · 걸음\n10월 24일 · 11,650걸음\n일요일은 줄었어요."
    assert [plain.text[a:b] for a, b in plain.rows] == ["날짜 · 걸음", "10월 24일 · 11,650걸음"]


def test_split_spans_does_not_split_inside_table_rows():
    plain = plain_answer("| 날짜 | 수면 |\n|---|---|\n| 10월 20일 (화) | 6시간 2분, 깊은 잠 1시간 |\n첫 문장, 둘째.")
    breaks = re.compile(r"(?<!\d)[.!?](?!\d)|\n|,(?!\d)")
    parts = [plain.text[a:b] for a, b in split_spans(plain.text, breaks, plain.rows)]
    assert parts == ["날짜 · 수면", "10월 20일 (화) · 6시간 2분, 깊은 잠 1시간", "첫 문장", " 둘째", ""]
    # keep 이면 나눈 자리의 글자를 앞 구간에 넣는다
    kept = [plain.text[a:b] for a, b in split_spans(plain.text, breaks, plain.rows, keep=True)]
    assert kept[-3:] == ["첫 문장,", " 둘째.", ""]
