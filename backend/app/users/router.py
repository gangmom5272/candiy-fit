"""본인 정보 API의 입출력과 공개 오류 변환."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.contract import AuthenticatedUser
from app.auth.dependencies import get_current_user
from app.core.db import get_session
from app.core.errors import ApiError, TemporaryStorageError
from app.core.routing import ApiRoute
from app.core.schemas import DataResponse
from app.core.validation import invalid_request
from app.users import service
from app.users.schemas import UpdateProfileRequest, UserResponse, user_response
from app.users.validation import ProfileValidationError, validate_profile

router = APIRouter(prefix="/api/v1/users", tags=["users"], route_class=ApiRoute)


@router.get("/me", response_model=DataResponse[UserResponse])
async def get_me(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> DataResponse[UserResponse]:
    try:
        member = await service.get_current_profile(db_session, user.user_id)
    except service.UserNotFoundError:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.") from None
    except TemporaryStorageError:
        raise ApiError(
            503, "USER_UNAVAILABLE", "회원 정보를 일시적으로 이용할 수 없습니다."
        ) from None
    return DataResponse(data=user_response(member))


@router.patch("/me", response_model=DataResponse[UserResponse])
async def patch_me(
    body: UpdateProfileRequest,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> DataResponse[UserResponse]:
    now = datetime.now(UTC)
    try:
        changes = validate_profile(body, await request.body(), now=now)
    except ProfileValidationError as error:
        raise invalid_request(error.details) from None
    try:
        member = await service.update_current_profile(
            db_session, user.user_id, changes, now=now
        )
    except service.UserNotFoundError:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.") from None
    except TemporaryStorageError:
        raise ApiError(
            503, "USER_UNAVAILABLE", "회원 정보를 일시적으로 이용할 수 없습니다."
        ) from None
    return DataResponse(data=user_response(member))
