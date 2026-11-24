import json

import httpx
import respx

from bench import sample_responses
from tests.test_probe import BASE, TEXT_STREAM, tool_message


@respx.mock
async def test_collect_and_render(settings):
    def chat(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("stream"):
            return httpx.Response(200, content=TEXT_STREAM)
        if body.get("response_format"):
            return httpx.Response(
                200, json={"choices": [{"message": {"content": '{"metric":"steps","period":"yesterday"}'}}]}
            )
        roles = [m["role"] for m in body["messages"]]
        if "tool" in roles:  # 도구 결과를 포함한 2회차 호출
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "지난 7일 평균 수면은 6시간 4분이에요."}}]}
            )
        if body.get("tools"):
            return httpx.Response(200, json=tool_message({"metric": "sleep", "period": "last_7_days"}))
        return httpx.Response(200, json={"choices": [{"message": {"content": "규칙적으로 주무세요."}}]})

    respx.post(f"{BASE}/chat/completions").mock(side_effect=chat)
    respx.post(f"{BASE}/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024}]})
    )
    sections = await sample_responses.collect(settings)
    titles = [s["title"] for s in sections]
    assert len(sections) == 7, titles
    md = sample_responses.render(sections, settings)
    assert "지난 7일 평균 수면은 6시간 4분이에요." in md
    assert "총 1024개" in md
    assert "test-key" not in md


def test_finish_note_marks_truncation():
    cut = {"choices": [{"finish_reason": "length"}], "usage": {"completion_tokens": 1000}}
    done = {"choices": [{"finish_reason": "stop"}], "usage": {"completion_tokens": 420}}
    assert "잘린 답변" in sample_responses.finish_note(cut)
    assert "잘린 답변" not in sample_responses.finish_note(done)
    assert "420토큰" in sample_responses.finish_note(done)
