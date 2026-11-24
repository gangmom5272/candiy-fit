import json

import httpx
import pytest
import respx

from bench import probe_llm

BASE = "https://llm.test/v1"
COMMIT = "abc1234"


# 커밋 조회는 git 을 실행해, git 이 없거나 저장소 소유자가 달라 git 이 거부하면 None 을 반환한다.
# 테스트 결과가 실행 환경의 git 에 따라 달라지지 않게 고정값을 쓴다
@pytest.fixture(autouse=True)
def fixed_commit(monkeypatch):
    monkeypatch.setattr(probe_llm, "git_commit", lambda: COMMIT)


def sse(*chunks: dict) -> bytes:
    lines = [f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def delta(**d) -> dict:
    return {"choices": [{"index": 0, "delta": d, "finish_reason": None}]}


TEXT_STREAM = sse(
    delta(role="assistant", content=""),  # role만 있는 첫 청크는 TTFT 측정에서 제외한다
    delta(content="잠은 "),
    delta(content="규칙적으로."),
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 5}},
)

TOOL_STREAM = sse(
    delta(
        role="assistant",
        tool_calls=[
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_metric_summary", "arguments": ""},
            }
        ],
    ),
    delta(tool_calls=[{"index": 0, "function": {"arguments": '{"metric": "sleep", '}}]),
    delta(tool_calls=[{"index": 0, "function": {"arguments": '"period": "last_7_days"}'}}]),
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
)


def tool_message(args: dict) -> dict:
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_metric_summary", "arguments": json.dumps(args)},
    }
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]}


@respx.mock
async def test_stream_text_ttft_and_usage(settings):
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, content=TEXT_STREAM))
    async with probe_llm.make_client(settings) as client:
        res = await probe_llm.chat_stream(client, {"model": "m", "messages": []})
    assert res.content == "잠은 규칙적으로."
    assert res.ttft is not None and res.first_byte is not None
    assert res.first_byte <= res.ttft
    assert res.usage == {"prompt_tokens": 20, "completion_tokens": 5}
    assert res.finish_reason == "stop"


@respx.mock
async def test_stream_assembles_tool_call(settings):
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, content=TOOL_STREAM))
    async with probe_llm.make_client(settings) as client:
        res = await probe_llm.chat_stream(client, {"model": "m", "messages": []})
    assert res.ttft is None  # 도구 호출만 있는 응답에는 '첫 글자'가 없다
    assert len(res.tool_calls) == 1
    assert probe_llm.check_tool_args(res.tool_calls[0]) == (True, True, "ok")


@respx.mock
async def test_authorization_header_uses_secret(settings):
    route = respx.get(f"{BASE}/models").mock(return_value=httpx.Response(200, json={"data": []}))
    async with probe_llm.make_client(settings) as client:
        await client.get("/models")
    assert route.calls.last.request.headers["authorization"] == "Bearer test-key"


def test_check_tool_args_rejects_bad_inputs():
    bad = [
        {"name": "export_all_users", "arguments": "{}"},
        {"name": "get_metric_summary", "arguments": "not json"},
        {
            "name": "get_metric_summary",
            "arguments": json.dumps({"metric": "sleep", "period": "last_7_days", "user_id": "b"}),
        },
        {"name": "get_metric_summary", "arguments": json.dumps({"metric": "steps' OR 1=1--", "period": "today"})},
    ]
    for call in bad:
        valid, _, _ = probe_llm.check_tool_args(call)
        assert valid is False


def test_pct_nearest_rank():
    vals = [float(v) for v in range(1, 11)]
    assert probe_llm.pct(vals, 50) == 5.0
    assert probe_llm.pct(vals, 95) == 10.0
    assert probe_llm.pct([], 50) is None


@respx.mock
async def test_run_probe_end_to_end(settings, tmp_path):
    def chat(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("stream"):
            return httpx.Response(200, content=TOOL_STREAM if body.get("tools") else TEXT_STREAM)
        if body.get("response_format"):
            content = json.dumps({"metric": "steps", "period": "yesterday"})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        if body.get("tools"):
            return httpx.Response(200, json=tool_message({"metric": "sleep", "period": "last_7_days"}))
        msg = {"role": "assistant", "content": "[코치] 물은 체온 조절을 돕습니다."}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {}})

    respx.get(f"{BASE}/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "gemma4-31b", "max_model_len": 32768}, {"id": "bge-m3"}]}
        )
    )
    respx.post(f"{BASE}/chat/completions").mock(side_effect=chat)
    respx.post(f"{BASE}/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024} for _ in range(8)]})
    )

    args = probe_llm.parse_args(["--n", "2", "--tool-n", "2", "--burst", "3", "--out", str(tmp_path)])
    report = await probe_llm.run_probe(settings, args)
    results = report["results"]

    assert set(results) == set(probe_llm.CHECKS)
    assert results["models"]["max_model_len"] == 32768
    assert results["basic"]["system_role_followed"] is True
    assert results["tools"]["modes"]["named"]["valid"] == 2
    assert all(r["status"] != "error" for r in results.values()), results

    json_path, md_path = probe_llm.save(report, tmp_path)
    assert "test-key" not in json_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["commit"] == COMMIT
    md = md_path.read_text(encoding="utf-8")
    assert "| tools |" in md and f"커밋 `{COMMIT}`" in md


@respx.mock
async def test_context_limit_reads_vllm_error(settings):
    def chat(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][0]["content"]
        if len(prompt) > 200_000:  # 두 번째 단계(약 14만 토큰)부터 거절 응답을 반환한다
            msg = "This model's maximum context length is 131072 tokens. However, you requested 140050 tokens."
            return httpx.Response(400, json={"object": "error", "message": msg})
        return httpx.Response(200, json={"choices": [{"message": {"content": "확인"}}]})

    respx.post(f"{BASE}/chat/completions").mock(side_effect=chat)
    async with probe_llm.make_client(settings) as client:
        result = await probe_llm.check_context_limit(client, settings, None)
    assert result["status"] == "ok"
    assert result["max_model_len"] == 131072


def test_save_is_opt_in():
    assert probe_llm.parse_args([]).save is False
    assert probe_llm.parse_args(["--save"]).save is True


@respx.mock
async def test_prefix_cache_reads_cached_tokens(settings):
    calls = {"n": 0}

    def chat(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        cached = 0 if calls["n"] == 1 else 3680
        usage = {"prompt_tokens": 3699, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": cached}}
        return httpx.Response(200, json={"choices": [{"message": {"content": "확인"}}], "usage": usage})

    respx.post(f"{BASE}/chat/completions").mock(side_effect=chat)
    async with probe_llm.make_client(settings) as client:
        result = await probe_llm.check_prefix_cache(client, settings, None)
    assert result["status"] == "ok"
    assert "3680/3699" in result["summary"]
    assert "켜져 있음" in result["summary"]
