"""API 경계에서만 사용하는 공개 오류. 메시지는 안전한 상수로 전달한다."""

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import JsonValue
from starlette.exceptions import HTTPException

from app.core.schemas import ErrorDetail


class TemporaryStorageError(Exception):
    """확인된 일시적 저장소 실패. 공개 코드·롤백·재시도는 호출자가 결정한다."""


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: list[ErrorDetail] | None = None,
        retryable: bool | None = None,
        context: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(code)
        if (status_code == 422) != (details is not None):
            raise ValueError("details are required only for 422")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.retryable = retryable
        self.context = context


def error_response(error: ApiError) -> JSONResponse:
    content = {"code": error.code, "message": error.message}
    if error.details is not None:
        content["details"] = [detail.model_dump() for detail in error.details]
    if error.retryable is not None:
        content["retryable"] = error.retryable
    if error.context is not None:
        content["context"] = error.context
    return JSONResponse(status_code=error.status_code, content={"error": content})


async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
    return error_response(error)


async def handle_http_error(request: Request, error: HTTPException) -> JSONResponse:
    # 프레임워크 detail에는 원본 입력이 있을 수 있어 반환하지 않는다.
    mapping = {
        401: ("AUTH_REQUIRED", "로그인이 필요합니다."),
        403: ("ORIGIN_NOT_ALLOWED", "요청 출처를 확인해 주세요."),
        404: ("NOT_FOUND", "경로를 찾을 수 없습니다."),
        405: ("METHOD_NOT_ALLOWED", "지원하지 않는 메서드입니다."),
    }
    status = error.status_code if error.status_code in mapping else 500
    code, message = mapping.get(
        status, ("INTERNAL_ERROR", "요청을 처리하지 못했습니다.")
    )
    response = error_response(ApiError(status, code, message))
    if status == 405 and error.headers and "Allow" in error.headers:
        response.headers["Allow"] = error.headers["Allow"]
    return response
