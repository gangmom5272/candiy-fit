import json

import httpx
import pytest
import respx

from bench import bench_latency
from coach_ai.llm.metrics import LLMCallRecord, MetricsRecorder
from tests.llm_fakes import CHAT_URL, SSE_HEADERS, chunk, completion, sse

COMMIT = "abc1234"


# 커밋 조회는 git 을 실행해, git 이 없거나 저장소 소유자가 달라 git 이 거부하면 None 을 반환한다.
# 테스트 결과가 실행 환경의 git 에 따라 달라지지 않게 고정값을 쓴다
@pytest.fixture(autouse=True)
def fixed_commit(monkeypatch):
    monkeypatch.setattr(bench_latency, "git_commit", lambda: COMMIT)


class FakeServer:
    """입력 글자 수를 토큰 수로 쓰고, 받은 요청 본문을 모아 둔다."""

    def __init__(self, *, report_cached: bool = False, headers_for=None) -> None:
        self.report_cached = report_cached
        self.headers_for = headers_for or (lambda i: {})
        self.seen_prefixes: set[str] = set()
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        headers = self.headers_for(len(self.requests))
        self.requests.append(body)
        system = next((m["content"] for m in body["messages"] if m["role"] == "system"), "")
        prompt_tokens = sum(len(m["content"]) for m in body["messages"])
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": 3, "total_tokens": prompt_tokens + 3}
        if self.report_cached:
            cached = len(system) if system in self.seen_prefixes else 0
            usage["prompt_tokens_details"] = {"cached_tokens": cached}
        self.seen_prefixes.add(system)
        if not body.get("stream"):
            return httpx.Response(200, json=completion("좋아요", usage), headers=headers)
        content = sse(
            chunk({"role": "assistant", "content": ""}),
            chunk({"content": "좋아요"}, finish_reason="stop"),
            chunk(None, usage=usage),
        )
        return httpx.Response(200, content=content, headers={**SSE_HEADERS, **headers})


def systems(requests: list[dict]) -> list[str]:
    return [r["messages"][0]["content"] for r in requests]


def questions(requests: list[dict]) -> list[str]:
    return [r["messages"][-1]["content"] for r in requests]


def test_questions_are_distinct_and_enough():
    assert len(set(bench_latency.QUESTIONS)) == len(bench_latency.QUESTIONS)
    assert len(bench_latency.QUESTIONS) >= bench_latency.WARMUP + 30


def test_service_prefix_carries_tool_descriptions():
    for name in ("get_metric_summary", "get_sleep_records", "compare_periods"):
        assert name in bench_latency.SERVICE_PREFIX


def test_service_messages_share_prefix_and_differ_in_question():
    qs = bench_latency.QUESTIONS[:5]
    msgs = [bench_latency.service_messages(q) for q in qs]
    assert {m[0]["content"] for m in msgs} == {bench_latency.SERVICE_PREFIX}
    assert [m[-1]["content"] for m in msgs] == qs


def test_cold_messages_differ_in_first_line():
    q = bench_latency.QUESTIONS[0]
    msgs = [bench_latency.cold_messages(q, seq) for seq in range(1, 6)]
    first_lines = [m[0]["content"].split("\n", 1)[0] for m in msgs]
    assert len(set(first_lines)) == 5
    assert all(line.startswith("[cold ") for line in first_lines)
    assert {m[0]["content"].split("\n", 1)[1] for m in msgs} == {bench_latency.SERVICE_PREFIX}


def test_cold_tag_is_unique_even_for_same_seq():
    assert bench_latency.cold_tag(1) != bench_latency.cold_tag(1)


@pytest.mark.parametrize("argv", [["--n", "0"], ["--n", "46"], ["--cache-n", "4"]])
def test_out_of_range_counts_are_rejected(argv):
    with pytest.raises(SystemExit):
        bench_latency.parse_args(argv)


@respx.mock
async def test_run_bench_end_to_end(settings):
    server = FakeServer()
    route = respx.post(CHAT_URL).mock(side_effect=server)
    args = bench_latency.parse_args(["--n", "2", "--len-n", "1"])
    recorder = MetricsRecorder()

    report = await bench_latency.run_bench(settings, args, recorder)
    res = report["results"]
    warmup = bench_latency.WARMUP

    # 기준선 2조건 × (워밍업 3 + 2) + 보정 2 + 길이 3 × (워밍업 1 + 1) + 캐시 (채우기 1 + 5 × 2)
    assert route.call_count == 2 * (warmup + 2) + 2 + 3 * 2 + (1 + 5 * 2)

    service_reqs = server.requests[: warmup + 2]
    cold_reqs = server.requests[warmup + 2 : 2 * (warmup + 2)]
    assert set(systems(service_reqs)) == {bench_latency.SERVICE_PREFIX}
    assert len(set(questions(service_reqs))) == len(service_reqs)
    cold_first = [s.split("\n", 1)[0] for s in systems(cold_reqs)]
    assert len(set(cold_first)) == len(cold_reqs)
    assert len(set(questions(cold_reqs))) == len(cold_reqs)

    lat = res["latency"]
    assert lat["service"]["n"] == 2 and lat["cold"]["n"] == 2
    assert lat["service"]["ttft_p50"] is not None
    assert lat["cold"]["input_tokens"] > lat["service"]["input_tokens"]

    length_reqs = server.requests[2 * (warmup + 2) + 2 : 2 * (warmup + 2) + 2 + 6]
    assert len({s.split("\n", 1)[0] for s in systems(length_reqs)}) == 6
    assert [r["target"] for r in res["lengths"]["rows"]] == [200, 2000, 8000]
    for row in res["lengths"]["rows"]:
        assert abs(row["input_tokens"] - row["target"]) < row["target"] * 0.1

    cache_reqs = server.requests[-11:]
    shared = [r for r in cache_reqs if r["messages"][0]["content"].startswith("[shared ")]
    cold = [r for r in cache_reqs if r["messages"][0]["content"].startswith("[cold ")]
    assert len(shared) == 6 and len(set(systems(shared))) == 1
    assert len(cold) == 5 and len({s.split("\n", 1)[0] for s in systems(cold)}) == 5
    assert res["cache"]["cold"]["n"] == 5 and res["cache"]["shared"]["n"] == 5
    assert res["cache"]["cached_tokens"] == "서버 미제공"
    assert res["cache"]["verdict"].startswith("같은 접두 반복 시 첫 글자 시간 중앙값 ")

    md = bench_latency.to_markdown(report)
    assert report["commit"] == COMMIT and f"커밋 `{COMMIT}`" in md
    assert "| 서비스형 |" in md and "| cold |" in md
    assert "## 입력 길이별 첫 글자 시간 (cold)" in md
    assert "같은 접두 반복의 cached_tokens 중앙값: 서버 미제공" in md
    assert f"동시성 설정 {settings.llm_max_concurrency}" in md
    assert "워밍업 3회 제외" in md
    # 이 모의 서버는 cached_tokens 를 보고하지 않는다
    assert lat["service"]["cached_reported"] == 0
    assert "| 0/2 |" in md


@respx.mock
async def test_cache_reports_cached_tokens_when_server_provides(settings):
    respx.post(CHAT_URL).mock(side_effect=FakeServer(report_cached=True))
    args = bench_latency.parse_args(["--only", "cache"])
    report = await bench_latency.run_bench(settings, args, MetricsRecorder())
    cache = report["results"]["cache"]
    assert cache["cached_tokens"] != "서버 미제공"
    assert float(cache["cached_tokens"]) > 0
    assert cache["cold"]["cached_median"] == 0


def test_save_writes_json_and_markdown(tmp_path):
    report = {
        "measured_at": "2026-10-16T10:00:00",
        "commit": "abc",
        "model": "gemma4-31b",
        "base_url_host": "llm.test",
        "max_concurrency": 1,
        "n": 1,
        "warmup": 3,
        "len_n": 1,
        "cache_n": 5,
        "results": {},
    }
    json_path, md_path = bench_latency.save(report, tmp_path, "20261016-1000")
    assert json.loads(json_path.read_text(encoding="utf-8"))["model"] == "gemma4-31b"
    md = md_path.read_text(encoding="utf-8")
    assert md.startswith("# LLM 래퍼 응답 시간")
    assert "동시성 설정 1" in md


def rec(ttft: float, *, fp: str | None = None, instance: dict | None = None) -> LLMCallRecord:
    return LLMCallRecord(
        request_id=None,
        node=None,
        model=None,
        background=False,
        stream=True,
        started_at="",
        ttft_s=ttft,
        total_s=ttft + 1,
        system_fingerprint=fp,
        instance=instance,
    )


def test_two_clusters_reports_ranges_only():
    low = [0.15, 0.16, 0.15, 0.17, 0.30]
    high = [0.92, 0.93, 0.95, 1.15]
    assert bench_latency.two_clusters(low + high) == [[0.15, 0.30], [0.92, 1.15]]


@pytest.mark.parametrize(
    "values",
    [
        [0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11],  # 고르게 퍼진 분포
        [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 5.0],  # 튀는 값 하나는 무리로 보지 않는다
        [0.1, 1.0],  # 표본이 너무 적다
    ],
)
def test_two_clusters_none_when_not_split(values):
    assert bench_latency.two_clusters(values) is None


def test_breakdown_by_label():
    records = [rec(0.1, instance={"x-backend": "b1"}) for _ in range(3)]
    records += [rec(0.9, instance={"x-backend": "b2"}) for _ in range(2)]
    bd = bench_latency.breakdown(records)
    assert [(g["label"], g["n"]) for g in bd["groups"]] == [("x-backend=b1", 3), ("x-backend=b2", 2)]
    assert bd["groups"][1]["ttft_p50"] == 0.9
    assert bd["clusters"] is None


def test_breakdown_without_label_describes_clusters_only():
    records = [rec(t) for t in (0.15, 0.16, 0.15, 0.17, 0.92, 0.93, 0.95, 0.94)]
    bd = bench_latency.breakdown(records)
    assert bd["groups"] == []
    assert bd["clusters"] == [[0.15, 0.17], [0.92, 0.95]]

    lines = "\n".join(bench_latency._breakdown_lines([("cold", {"breakdown": bd})]))
    assert "구분값을 얻지 못했다" in lines
    assert "0.150~0.170초, 0.920~0.950초" in lines
    assert "구분값별" not in lines


def test_breakdown_single_label_also_describes_clusters():
    records = [rec(t, fp="fp-same") for t in (0.15, 0.16, 0.15, 0.17, 0.92, 0.93, 0.95, 0.94)]
    bd = bench_latency.breakdown(records)
    assert [g["label"] for g in bd["groups"]] == ["fingerprint=fp-same"]
    assert bd["clusters"] is not None
    lines = "\n".join(bench_latency._breakdown_lines([("cold", {"breakdown": bd})]))
    assert "구분값이 하나뿐이라 나누지 못했다" in lines


@respx.mock
async def test_run_bench_splits_by_allowed_header(settings):
    settings.llm_instance_headers = "x-backend"
    server = FakeServer(headers_for=lambda i: {"x-backend": f"b{i % 2 + 1}", "x-request-id": f"req-{i}"})
    respx.post(CHAT_URL).mock(side_effect=server)
    args = bench_latency.parse_args(["--only", "latency", "--n", "4"])

    report = await bench_latency.run_bench(settings, args, MetricsRecorder())

    groups = report["results"]["latency"]["service"]["breakdown"]["groups"]
    assert {g["label"] for g in groups} == {"x-backend=b1", "x-backend=b2"}
    assert sum(g["n"] for g in groups) == 4
    md = bench_latency.to_markdown(report)
    assert "구분값별" in md and "| 서비스형 | x-backend=b1 |" in md
    assert "req-" not in md


@respx.mock
async def test_list_headers_prints_names_not_values(settings):
    def headers_for(i: int) -> dict:
        return {"x-backend": f"node-{i % 2}", "x-request-id": f"req-{i:04d}", "set-cookie": "sid=secret-cookie"}

    respx.post(CHAT_URL).mock(side_effect=FakeServer(headers_for=headers_for))
    args = bench_latency.parse_args(["--list-headers", "8"])

    found = await bench_latency.run_list_headers(settings, args, MetricsRecorder())
    text = bench_latency.headers_to_text(found)

    rows = {h["name"]: h for h in found["headers"]}
    assert (rows["x-backend"]["distinct"], rows["x-request-id"]["distinct"]) == (2, 8)
    assert "| x-backend | 8 | 2 |" in text and "인스턴스 구분 후보" in text
    assert "| set-cookie | 8 | 1 |" in text and "민감 헤더라 기록 불가" in text
    for value in ("node-0", "node-1", "req-0001", "secret-cookie"):
        assert value not in text
    x_request_line = next(line for line in text.splitlines() if line.startswith("| x-request-id"))
    assert "후보" not in x_request_line


MODEL_A = "69e434c6" + "a" * 56
MODEL_B = "5e590392" + "b" * 56


def test_label_uses_header_over_fingerprint_and_shortens_model_id():
    r = rec(0.1, fp="vllm-0.24.0-x", instance={"x-litellm-model-id": MODEL_A, "x-litellm-attempted-retries": "0"})
    assert bench_latency.instance_label(r) == "x-litellm-attempted-retries=0, x-litellm-model-id=69e434c6"
    assert bench_latency.instance_label(r, ["x-litellm-model-id"]) == "x-litellm-model-id=69e434c6"
    assert bench_latency.instance_label(rec(0.1, fp="vllm-0.24.0-x")) == "fingerprint=vllm-0.24.0-x"
    assert bench_latency.instance_label(rec(0.1)) is None


def test_fingerprint_does_not_split_same_backend():
    records = [rec(0.05, fp="fp", instance={"x-litellm-model-id": MODEL_A})]
    records += [rec(0.06, instance={"x-litellm-model-id": MODEL_A}) for _ in range(3)]
    bd = bench_latency.breakdown(records)
    assert [(g["label"], g["n"]) for g in bd["groups"]] == [("x-litellm-model-id=69e434c6", 4)]


@respx.mock
async def test_rebuild_from_saved_records_matches_live_run(settings, tmp_path):
    settings.llm_instance_headers = "x-litellm-model-id, x-litellm-attempted-retries"

    def headers_for(i: int) -> dict:
        return {"x-litellm-model-id": MODEL_A if i % 2 else MODEL_B, "x-litellm-attempted-retries": "0"}

    respx.post(CHAT_URL).mock(side_effect=FakeServer(headers_for=headers_for))
    args = bench_latency.parse_args(["--n", "4", "--len-n", "2"])
    calls = tmp_path / "20261016-1000_latency_calls.jsonl"
    live = await bench_latency.run_bench(settings, args, MetricsRecorder(calls))
    json_path, _ = bench_latency.save(live, tmp_path, "20261016-1000")

    assert MODEL_A in calls.read_text(encoding="utf-8")

    rebuilt = bench_latency.rebuild_report(json_path, keys=["x-litellm-model-id"])
    for cond in ("service", "cold"):
        for key in ("n", "ttft_p50", "ttft_p95", "total_p50", "total_p95", "cached_reported"):
            assert rebuilt["results"]["latency"][cond][key] == live["results"]["latency"][cond][key]
    assert [r["input_tokens"] for r in rebuilt["results"]["lengths"]["rows"]] == [
        r["input_tokens"] for r in live["results"]["lengths"]["rows"]
    ]
    assert rebuilt["results"]["cache"]["verdict"] == live["results"]["cache"]["verdict"]

    labels = {g["label"] for g in rebuilt["results"]["latency"]["service"]["breakdown"]["groups"]}
    assert labels == {"x-litellm-model-id=69e434c6", "x-litellm-model-id=5e590392"}
    md = bench_latency.to_markdown(rebuilt)
    assert "다시 만든 표다. 구분 헤더: x-litellm-model-id" in md
    assert MODEL_A not in md


def test_rebuild_cli_needs_no_api_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    meta = {
        "measured_at": "2026-10-16T10:00:00",
        "commit": "abc",
        "model": "gemma4-31b",
        "base_url_host": "llm.test",
        "max_concurrency": 3,
        "n": 1,
        "warmup": 0,
        "len_n": 1,
        "cache_n": 5,
        "results": {},
    }
    json_path = tmp_path / "x_latency.json"
    json_path.write_text(json.dumps(meta), encoding="utf-8")
    line = {
        "request_id": "bench-1",
        "node": "bench_latency_service",
        "model": "m",
        "background": False,
        "stream": True,
        "started_at": "",
        "ttft_s": 0.1,
        "total_s": 1.0,
        "instance": {"x-litellm-model-id": MODEL_A},
        "kind": "llm",
    }
    (tmp_path / "x_latency_calls.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")

    bench_latency.main(["--rebuild", str(json_path)])

    out = capsys.readouterr().out
    assert "| 서비스형 | x-litellm-model-id=69e434c6 | 1 |" in out
