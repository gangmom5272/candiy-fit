"""HTTP 응답 헤더와 안전한 최상위 예외 경계. WS/취소는 관여하지 않는다."""

import logging
from pathlib import Path
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import ApiError, error_response

logger = logging.getLogger(__name__)
_APP_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_FILES = {
    path: f"app/{path.relative_to(_APP_ROOT).as_posix()}"
    for path in _APP_ROOT.rglob("*.py")
}
_ERROR_KINDS = {
    kind: kind.__name__
    for kind in (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        RuntimeError,
        TimeoutError,
    )
}


def _error_source(error: Exception) -> str:
    source = "unknown"
    # 오류를 포착한 미들웨어 프레임과 예외의 cause·지역변수는 읽지 않는다.
    frame = error.__traceback__.tb_next
    while frame is not None:
        filename = _SOURCE_FILES.get(Path(frame.tb_frame.f_code.co_filename))
        if filename is not None:
            source = f"{filename}:{frame.tb_lineno}"
        frame = frame.tb_next
    return source


class HttpContractMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_response(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = request_id
                if scope["path"] == "/api/v1" or scope["path"].startswith("/api/v1/"):
                    headers["Cache-Control"] = "no-store"
                if message["status"] == 401:
                    headers["WWW-Authenticate"] = "Bearer"
            await send(message)

        await self.app(scope, receive, send_response)


class SafeErrorMiddleware:
    """CORS 안쪽에서 오류 응답을 만들어 CORS와 공통 헤더를 모두 거치게 한다."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False
        completed = False

        async def send_response(message: Message) -> None:
            nonlocal started, completed
            if message["type"] == "http.response.start":
                started = True
            elif message["type"] == "http.response.body":
                completed = not message.get("more_body", False)
            await send(message)

        try:
            await self.app(scope, receive, send_response)
        except Exception as error:
            # ServerErrorMiddleware의 재전파보다 안쪽에서 원본 예외 로그를 차단한다.
            logger.error(
                "request_failed request_id=%s category=unexpected error_kind=%s source=%s",
                scope["state"]["request_id"],
                _ERROR_KINDS.get(type(error), "unexpected"),
                _error_source(error),
            )
            if not started:
                response = error_response(
                    ApiError(500, "INTERNAL_ERROR", "요청을 처리하지 못했습니다.")
                )
                await response(scope, receive, send_response)
            elif not completed:
                await send({"type": "http.response.body", "body": b""})
