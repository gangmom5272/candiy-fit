from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Lock

import httpx
import pytest
import respx

from candiy_pipeline.candiy_client import CandiyAuthError, CandiyClient, CandiyError

BASE = "https://candiy.test"
TOKEN_URL = f"{BASE}/oauth/token"
STEPS_URL = f"{BASE}/api/steps"
D1, D2 = date(2026, 10, 16), date(2026, 10, 18)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def token(value="t1", expires_in=7200):
    return httpx.Response(200, json={"access_token": value, "token_type": "Bearer", "expires_in": expires_in})


def ok(data=None):
    return httpx.Response(200, json={"status": "success", "data": data or []})


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(clock):
    c = CandiyClient(BASE, "id", "secret", clock=clock, sleep=lambda _: None)
    yield c
    c.close()


@respx.mock
def test_reuses_token_until_near_expiry(client, clock):
    token_route = respx.post(TOKEN_URL).mock(side_effect=[token("t1"), token("t2")])
    respx.get(STEPS_URL).mock(return_value=ok())

    client.fetch("steps", "mock-1013", D1, D2)
    clock.now = 7200 - 301
    client.fetch("steps", "mock-1013", D1, D2)
    assert token_route.call_count == 1

    clock.now = 7200 - 299  # 만료 5분 전 안쪽
    client.fetch("steps", "mock-1013", D1, D2)
    assert token_route.call_count == 2
    assert respx.calls.last.request.headers["Authorization"] == "Bearer t2"


@respx.mock
def test_401_reissues_token_once(client):
    token_route = respx.post(TOKEN_URL).mock(side_effect=[token("t1"), token("t2")])
    respx.get(STEPS_URL).mock(side_effect=[httpx.Response(401, json={}), ok([{"data_id": "x"}])])

    assert client.fetch("steps", "mock-1013", D1, D2) == [{"data_id": "x"}]
    assert token_route.call_count == 2


@respx.mock
def test_retries_5xx_then_fails_with_kind(client):
    respx.post(TOKEN_URL).mock(return_value=token())
    route = respx.get(STEPS_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(CandiyError) as e:
        client.fetch("steps", "mock-1013", D1, D2)
    assert e.value.kind == "http_503"
    assert route.call_count == 4  # 처음 1번 + 재시도 3번


@respx.mock
def test_404_is_not_retried(client):
    respx.post(TOKEN_URL).mock(return_value=token())
    route = respx.get(STEPS_URL).mock(return_value=httpx.Response(404, json={"status": "fail"}))

    with pytest.raises(CandiyError) as e:
        client.fetch("steps", "unknown", D1, D2)
    assert e.value.kind == "http_404"
    assert route.call_count == 1


@respx.mock
def test_fail_status_is_invalid_response(client):
    respx.post(TOKEN_URL).mock(return_value=token())
    respx.get(STEPS_URL).mock(return_value=httpx.Response(200, json={"status": "fail", "data": {"error": "x"}}))

    with pytest.raises(CandiyError) as e:
        client.fetch("steps", "mock-1013", D1, D2)
    assert e.value.kind == "invalid_response"


@respx.mock
def test_token_failure_stops_without_secret(client):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_client"}))

    with pytest.raises(CandiyAuthError) as e:
        client.fetch("steps", "mock-1013", D1, D2)
    assert "secret" not in str(e.value)


@respx.mock
def test_oxygen_has_no_end_param(client):
    respx.post(TOKEN_URL).mock(return_value=token())
    route = respx.get(f"{BASE}/api/oxygen_saturations").mock(return_value=ok())

    client.fetch("oxygen_saturations", "mock-1013", D1, D2)
    params = route.calls.last.request.url.params
    assert params["start"] == "2026-10-16" and "end" not in params


@pytest.mark.parametrize("phase", ["initial", "expired", "rejected"])
@pytest.mark.parametrize("failure", ["http", "timeout"])
def test_parallel_fetches_share_token_failure(phase, failure, clock):
    token_calls = 0
    lock = Lock()

    def respond(request):
        nonlocal token_calls
        if request.url.path == "/oauth/token":
            with lock:
                token_calls += 1
                current = token_calls
            if phase != "initial" and current == 1:
                return token()
            if failure == "timeout":
                raise httpx.ReadTimeout("synthetic-secret", request=request)
            return httpx.Response(401, json={"error": "synthetic-secret"})
        return httpx.Response(401) if phase == "rejected" else ok()

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = CandiyClient(BASE, "id", "secret", http=http, clock=clock)
        if phase != "initial":
            client._valid_token()
        if phase == "expired":
            clock.now = 7200

        def fetch(_):
            with pytest.raises(CandiyAuthError) as exc:
                client.fetch("steps", "synthetic-owner", D1, D2)
            assert "secret" not in str(exc.value)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(fetch, range(73)))
        assert token_calls == (1 if phase == "initial" else 2)

        # 새 실행에서 만든 클라이언트는 토큰 발급을 다시 시도한다.
        fresh = CandiyClient(BASE, "id", "secret", http=http, clock=clock)
        with pytest.raises(CandiyAuthError):
            fresh.fetch("steps", "synthetic-owner", D1, D2)
        assert token_calls == (2 if phase == "initial" else 3)


def test_parallel_401s_reuse_one_successful_reissue():
    rejected = Barrier(8, timeout=5)
    token_calls = 0

    def respond(request):
        nonlocal token_calls
        if request.url.path == "/oauth/token":
            token_calls += 1
            return token(f"t{token_calls}")
        if request.headers["Authorization"] == "Bearer t1":
            rejected.wait()
            return httpx.Response(401)
        assert request.headers["Authorization"] == "Bearer t2"
        return ok()

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = CandiyClient(BASE, "id", "secret", http=http)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: client.fetch("steps", "synthetic-owner", D1, D2), range(8)))
        assert results == [[]] * 8
        assert token_calls == 2


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize(
    "payload",
    [
        b"synthetic-secret-not-json",
        b"null",
        b"[]",
        b'"synthetic-secret"',
        b"{}",
        b'{"access_token":null}',
        b'{"access_token":123}',
        b'{"access_token":{}}',
        b'{"access_token":""}',
        b'{"access_token":" "}',
        b'{"access_token":"synthetic\\r\\nsecret"}',
        b'{"access_token":"synthetic\\u00e9"}',
        b'{"access_token":"synthetic-secret","expires_in":null}',
        b'{"access_token":"synthetic-secret","expires_in":true}',
        b'{"access_token":"synthetic-secret","expires_in":"invalid"}',
        b'{"access_token":"synthetic-secret","expires_in":[]}',
        b'{"access_token":"synthetic-secret","expires_in":0}',
        b'{"access_token":"synthetic-secret","expires_in":-1}',
        b'{"access_token":"synthetic-secret","expires_in":"NaN"}',
        b'{"access_token":"synthetic-secret","expires_in":"Infinity"}',
        b'{"access_token":"synthetic-secret","expires_in":1e999}',
    ],
)
def test_invalid_token_payload_is_safe_shared_auth_failure(payload, refresh, clock):
    calls = []

    def respond(request):
        calls.append(request.url.path)
        assert request.url.path == "/oauth/token"
        if refresh and len(calls) == 1:
            return token("previous-token")
        return httpx.Response(200, content=payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = CandiyClient(BASE, "id", "secret", http=http, clock=clock)
        if refresh:
            client._valid_token()
            clock.now = 7200
        old_token, old_expiry = client._token, client._token_expires_at
        for _ in range(2):
            with pytest.raises(CandiyAuthError, match="invalid_response") as exc:
                client.fetch("steps", "synthetic-owner", D1, D2)
            assert "secret" not in str(exc.value)
        assert calls == ["/oauth/token"] * (2 if refresh else 1)
        assert (client._token, client._token_expires_at) == (old_token, old_expiry)


@pytest.mark.parametrize("expiry", [None, 7200, "7200", 3600.5])
def test_valid_token_payload_preserves_default_and_numeric_expiry(expiry, clock):
    payload = {"access_token": "synthetic-token"}
    if expiry is not None:
        payload["expires_in"] = expiry

    def respond(request):
        return httpx.Response(200, json=payload) if request.url.path == "/oauth/token" else ok()

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = CandiyClient(BASE, "id", "secret", http=http, clock=clock)
        assert client.fetch("steps", "synthetic-owner", D1, D2) == []
        assert client._token_expires_at == float(expiry if expiry is not None else 7200)
