"""CANDiY REST API 클라이언트 (PIPE-01 토큰, PIPE-02 조회).

토큰은 메모리에만 둔다. 응답 본문·토큰·비밀값은 예외 메시지에 넣지 않는다.
"""

import math
import threading
import time
from collections.abc import Callable
from datetime import date

import httpx

from .metrics import NO_END_PARAM

TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class CandiyError(Exception):
    """한 회원·지표 조회 실패. kind는 sync_state.last_error에 그대로 남긴다."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class CandiyAuthError(Exception):
    """토큰 발급 실패. 같은 실행에서는 추가 발급을 중단한다."""


class CandiyClient:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._base_url = base_url
        self._auth = (client_id, client_secret)
        self._http = http or httpx.Client(timeout=30)
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._clock = clock
        self._sleep = sleep
        self._token: str | None = None
        self._token_expires_at = 0.0
        # 한 실행의 인증 실패는 공유하되 예외 객체·traceback은 보관하지 않는다.
        self._auth_failure: str | None = None
        # 지표를 동시에 조회할 때(sync.py) 토큰을 한 번만 받도록 막는다. httpx.Client는 스레드에서 같이 써도 된다
        self._token_lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    # ----- PIPE-01 토큰 -----

    def _issue_token(self) -> None:
        try:
            res = self._http.post(
                f"{self._base_url}/oauth/token",
                data={"grant_type": "client_credentials"},
                auth=self._auth,
            )
        except httpx.HTTPError as e:
            raise CandiyAuthError(f"토큰 발급 실패: {type(e).__name__}") from None
        if res.status_code != 200:
            raise CandiyAuthError(f"토큰 발급 실패: HTTP {res.status_code}")
        try:
            body = res.json()
            if not isinstance(body, dict):
                raise ValueError
            access_token = body.get("access_token")
            expires_in = body.get("expires_in", 7200)
            if (
                not isinstance(access_token, str)
                or not access_token
                or any(not 33 <= ord(char) <= 126 for char in access_token)
                or isinstance(expires_in, bool)
            ):
                raise ValueError
            lifetime = float(expires_in)
            if not math.isfinite(lifetime) or lifetime <= 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise CandiyAuthError("토큰 발급 실패: invalid_response") from None
        self._token = access_token
        self._token_expires_at = self._clock() + lifetime

    def _valid_token(self, *, rejected: str | None = None) -> str:
        with self._token_lock:
            if self._auth_failure is not None:
                raise CandiyAuthError(self._auth_failure)
            if (
                self._token is None
                or self._token == rejected
                or self._clock() >= self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS
            ):
                try:
                    self._issue_token()
                except CandiyAuthError as exc:
                    self._auth_failure = str(exc)
                    raise
            return self._token

    # ----- PIPE-02 조회 -----

    def fetch(self, metric: str, candiy_id: str, start: date, end: date) -> list[dict]:
        """한 회원·지표·기간(양 끝 포함, KST 날짜)의 레코드 목록."""
        params = {"end_user_id": candiy_id, "start": start.isoformat()}
        if metric not in NO_END_PARAM:
            params["end"] = end.isoformat()

        body = self._get_with_retry(f"{self._base_url}/api/{metric}", params)
        if not isinstance(body, dict) or body.get("status") != "success" or not isinstance(body.get("data"), list):
            raise CandiyError("invalid_response")
        return body["data"]

    def _get_with_retry(self, url: str, params: dict) -> object:
        reissued = False
        attempt = 0
        while True:
            token = self._valid_token()
            try:
                res = self._http.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as e:
                kind = f"network:{type(e).__name__}"
            else:
                if res.status_code == 200:
                    try:
                        return res.json()
                    except ValueError:
                        raise CandiyError("invalid_response") from None
                if res.status_code == 401 and not reissued:
                    # 토큰이 서버 쪽에서 무효가 된 경우. 한 번만 새로 받아 다시 보낸다.
                    reissued = True
                    self._valid_token(rejected=token)
                    continue
                if res.status_code not in RETRY_STATUSES:
                    raise CandiyError(f"http_{res.status_code}")
                kind = f"http_{res.status_code}"

            if attempt >= self._max_retries:
                raise CandiyError(kind)
            self._sleep(self._backoff * (2**attempt))
            attempt += 1
