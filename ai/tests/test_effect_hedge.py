"""효과 단정 끝맺음을 완곡한 말로 바꾸기 (agent/rules/effect_hedge.py)."""

import pytest

from coach_ai.agent.rules.effect_hedge import HEDGES, EffectHedge, hedge_text


def test_hedges_are_the_expressions_found_in_records():
    # 저장된 답과 회귀 기록(docs/evals/2026-11-13-fatigue-answers.md 1절)에 나온 효과 단정만 둔다.
    # 늘리려면 기록에서 찾은 근거를 적는다
    assert HEDGES == {
        "도움이 됩니다": "도움이 될 수 있어요",
        "도움을 줍니다": "도움을 줄 수 있어요",
        "효과적입니다": "효과적일 수 있어요",
        "수월해집니다": "수월해질 수 있어요",
        "효과를 기대할 수 있습니다": "효과가 있을 수 있어요",
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("저녁에 가볍게 걸으면 잠드는 데 도움이 됩니다.", "저녁에 가볍게 걸으면 잠드는 데 도움이 될 수 있어요."),
        ("규칙적인 기상은 리듬을 잡는 데 도움을 줍니다.", "규칙적인 기상은 리듬을 잡는 데 도움을 줄 수 있어요."),
        ("아침 햇빛은 리듬을 맞추는 데 효과적입니다.", "아침 햇빛은 리듬을 맞추는 데 효과적일 수 있어요."),
        ("방을 어둡게 하면 잠드는 것이 수월해집니다.", "방을 어둡게 하면 잠드는 것이 수월해질 수 있어요."),
        ("낮에 움직이면 깊이 자는 효과를 기대할 수 있습니다.", "낮에 움직이면 깊이 자는 효과가 있을 수 있어요."),
    ],
)
def test_listed_expressions_are_hedged(text, expected):
    assert hedge_text(text) == (expected, 1)


@pytest.mark.parametrize("end", ["!", "?", "\n", ""])
def test_sentence_end_forms(end):
    assert hedge_text(f"걸으면 도움이 됩니다{end}") == (f"걸으면 도움이 될 수 있어요{end}", 1)


@pytest.mark.parametrize(
    "text",
    [
        "늦잠은 리듬을 잡는 데 도움이 되지 않습니다.",
        "낮잠을 길게 자면 도움이 안 됩니다.",
        "한 번에 몰아 자는 것은 효과적이지 않습니다.",
        "늦은 운동은 잠에 도움을 주지 않습니다.",
        "카페인을 마시면 잠드는 것이 수월해지지 않습니다.",
    ],
)
def test_negative_sentences_are_kept(text):
    assert hedge_text(text) == (text, 0)


@pytest.mark.parametrize(
    "text",
    [
        '"걸으면 도움이 됩니다"라는 말은 사람마다 달라요.',
        "그 글에는 “꾸준히 걸으면 도움이 됩니다.”라고 적혀 있어요.",
        "‘효과적입니다’처럼 단정하지 않아요.",
    ],
)
def test_quoted_text_is_kept(text):
    assert hedge_text(text) == (text, 0)


@pytest.mark.parametrize(
    "text",
    [
        "화면을 줄이면 잠이 빨라질 수 있고, 아침에도 도움이 됩니다.",
        "사람에 따라 다를 수도 있지만 걷기는 도움이 됩니다.",
        "늦잠이 원인인 것 같고 일찍 일어나면 도움이 됩니다.",
        "잠드는 데 도움이 될 수 있어요.",
    ],
)
def test_already_hedged_sentences_are_kept(text):
    assert hedge_text(text) == (text, 0)


def test_consult_sentences_are_kept():
    text = "의료진과 상담하시면 원인을 아는 데 도움이 됩니다."
    assert hedge_text(text) == (text, 0)


@pytest.mark.parametrize("text", ["걸으면 도움이 됩니다만 사람마다 달라요.", "효과적입니다라고 말하기는 어려워요."])
def test_expressions_not_at_sentence_end_are_kept(text):
    assert hedge_text(text) == (text, 0)


def test_negation_in_condition_is_not_a_negative_sentence():
    # 끝맺는 말이 긍정이면 앞 조건에 "않으면"이 있어도 바꾼다
    text = "자기 전에 화면을 보지 않으면 잠드는 데 도움이 됩니다."
    assert hedge_text(text) == ("자기 전에 화면을 보지 않으면 잠드는 데 도움이 될 수 있어요.", 1)


def test_each_changed_sentence_is_counted():
    text = "걸으면 도움이 됩니다. 햇빛도 효과적입니다.\n- 방을 어둡게 하면 도움을 줍니다"
    assert hedge_text(text) == (
        "걸으면 도움이 될 수 있어요. 햇빛도 효과적일 수 있어요.\n- 방을 어둡게 하면 도움을 줄 수 있어요",
        3,
    )


def test_decimal_point_does_not_end_the_sentence():
    text = "7.5시간 잘 수 있게 일찍 누우면 도움이 됩니다."
    assert hedge_text(text) == (text, 0)


def test_chunks_without_targets_are_sent_as_they_come():
    hedge = EffectHedge()
    assert hedge.feed("합성 걸음 기록을 ") == "합성 걸음 기록을 "
    assert hedge.feed("확인했어요.") == "확인했어요."
    assert hedge.flush() == ""
    assert hedge.changed == 0


def test_possible_start_of_target_is_held_until_resolved():
    hedge = EffectHedge()
    assert hedge.feed("잠드는 데 도움") == "잠드는 데 "
    assert hedge.feed("이 됩니다") == ""
    # "도"·"수"도 바꿀 말의 앞부분이라 다음 조각까지 잡아 둔다
    assert hedge.feed(". 내일도") == "도움이 될 수 있어요. 내일"
    assert hedge.feed(" 걸어요. 수") == "도 걸어요. "
    assert hedge.feed("면 시간을") == "수면 시간을"
    assert hedge.flush() == ""
    assert hedge.changed == 1


def test_flush_releases_target_at_end_of_answer():
    hedge = EffectHedge()
    assert hedge.feed("걸으면 도움이 됩니다") == "걸으면 "
    assert hedge.flush() == "도움이 될 수 있어요"
    assert hedge.changed == 1


@pytest.mark.parametrize(
    "text",
    [
        "저녁에 걸으면 도움이 됩니다. 7.5시간 잘 수 있게 하면 도움이 됩니다.",
        '"도움이 됩니다"라고 단정하지 않아요. 햇빛은 효과적입니다!',
        "낮에 움직이면 깊이 자는 효과를 기대할 수 있습니다\n- 방을 어둡게 하면 수월해집니다",
    ],
)
def test_any_chunk_split_gives_the_same_text(text):
    expected = hedge_text(text)
    for cut in range(len(text) + 1):
        hedge = EffectHedge()
        out = hedge.feed(text[:cut]) + hedge.feed(text[cut:]) + hedge.flush()
        assert (out, hedge.changed) == expected, cut
