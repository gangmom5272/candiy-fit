"""건강데이터 조회·연동 상태 API(담당 개발팀). 서버 상태 확인 /healthz와는 다르다."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.contract import AuthenticatedUser
from app.core.db import get_session
from app.core.errors import ApiError, TemporaryStorageError
from app.core.routing import ApiRoute
from app.core.schemas import DataResponse
from app.health import daily_message_service, service
from app.health.dependencies import get_app_connection_user, get_health_user
from app.health.diagnostics import query_failure_diagnostics
from app.health.errors import (
    HealthDataError,
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
)
from app.health.http import health_api_error
from app.health.rest_schemas import (
    AppConnectionResponse,
    DailyMessageResponse,
    HealthDataStatusResponse,
    HealthTimelineResponse,
    LifestyleScoreRequest,
    LifestyleScoreResponse,
    MetricPeriodRequest,
    MetricResponse,
    TimelinePeriodRequest,
    daily_message_response,
    data_status_response,
    lifestyle_score_response,
    metric_response,
    timeline_response,
)
from app.health.schemas import KST, HealthMetric
from app.health.sync_repository import MemberRemovedError
from app.health.sync_schemas import HealthSyncResponse
from app.health.sync_service import HealthSyncError
from app.users import service as user_service

router = APIRouter(prefix="/api/v1/health", tags=["health"], route_class=ApiRoute)


@router.put("/app-connection", response_model=DataResponse[AppConnectionResponse])
async def complete_app_connection(
    user: Annotated[AuthenticatedUser, Depends(get_app_connection_user)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> DataResponse[AppConnectionResponse]:
    try:
        result = await user_service.complete_app_connection(db_session, user.user_id)
    except user_service.UserNotFoundError:
        raise ApiError(401, "AUTH_REQUIRED", "앱 로그인이 필요합니다.") from None
    except TemporaryStorageError:
        raise ApiError(
            503,
            "APP_CONNECTION_UNAVAILABLE",
            "앱 연동 완료를 저장하지 못했어요. 잠시 후 다시 시도해 주세요.",
            retryable=True,
        ) from None
    return DataResponse(data=AppConnectionResponse.model_validate(result.model_dump()))


@router.post("/sync", response_model=DataResponse[HealthSyncResponse])
async def sync_current_member(
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_health_user)],
) -> DataResponse[HealthSyncResponse]:
    try:
        status = await request.app.state.member_sync.sync(
            user.user_id, request.state.request_id
        )
    except MemberRemovedError:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.") from None
    except HealthDataNotLinkedError as error:
        raise health_api_error(error) from None
    except HealthSyncError as error:
        raise ApiError(
            503,
            "HEALTH_SYNC_UNAVAILABLE",
            "건강 기록을 갱신하지 못했어요. 잠시 후 다시 확인해 주세요.",
            context={"reason": error.reason, "metrics": list(error.metrics)},
        ) from None
    return DataResponse(data=HealthSyncResponse(status=status))


class DailyMessageRoute(ApiRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def dated(request: Request):
            # 입력 읽기·인증 대기 전에 고정해 자정을 넘겨도 같은 날짜를 사용한다.
            request.state.daily_message_today = datetime.now(KST).date()
            return await handler(request)

        return dated


async def get_daily_message(
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_health_user)],
) -> DataResponse[DailyMessageResponse]:
    try:
        result = await daily_message_service.get_daily_message(
            user.user_id,
            today=request.state.daily_message_today,
            request_id=request.state.request_id,
            engine=request.app.state.engine,
        )
    except HealthDataError as error:
        raise health_api_error(error) from None
    if result.user_id != user.user_id:
        raise health_api_error(HealthDataForbiddenError())
    try:
        response = DataResponse(data=daily_message_response(result))
        response.model_dump_json()
    except (ValidationError, PydanticSerializationError):
        raise health_api_error(
            HealthDataLookupError(daily_message_service.MESSAGE_METRICS)
        ) from None
    return response


router.add_api_route(
    "/daily-message",
    get_daily_message,
    methods=["GET"],
    response_model=DataResponse[DailyMessageResponse],
    route_class_override=DailyMessageRoute,
)


@router.get("/timeline", response_model=DataResponse[HealthTimelineResponse])
async def get_timeline(
    query: Annotated[TimelinePeriodRequest, Query()],
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_health_user)],
) -> DataResponse[HealthTimelineResponse]:
    today = datetime.now(KST).date()
    query.validate_period(today=today)
    try:
        with query_failure_diagnostics(
            request.state.request_id, route="timeline", stage="lookup"
        ):
            result = await service.get_timeline(
                user.user_id,
                query.start,
                query.end,
                today=today,
                session_factory=request.app.state.sessionmaker,
            )
    except HealthDataError as error:
        raise health_api_error(error) from None
    if result.user_id != user.user_id:
        raise health_api_error(HealthDataForbiddenError(service.TIMELINE_METRICS))
    try:
        with query_failure_diagnostics(
            request.state.request_id, route="timeline", stage="response"
        ):
            response = DataResponse(data=timeline_response(result))
            response.model_dump_json()
    except (ValidationError, PydanticSerializationError):
        raise health_api_error(
            HealthDataLookupError(service.TIMELINE_METRICS)
        ) from None
    return response


@router.get("/lifestyle-score", response_model=DataResponse[LifestyleScoreResponse])
async def get_lifestyle_score(
    query: Annotated[LifestyleScoreRequest, Query()],
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_health_user)],
) -> DataResponse[LifestyleScoreResponse]:
    today = datetime.now(KST).date()
    score_date = query.resolve_date(today=today)
    try:
        result = await service.get_lifestyle_score(
            user.user_id,
            score_date,
            session_factory=request.app.state.sessionmaker,
        )
    except HealthDataError as error:
        raise health_api_error(error) from None
    if result.end_user_id != user.user_id:
        raise health_api_error(HealthDataForbiddenError())
    try:
        response = DataResponse(data=lifestyle_score_response(result, today=today))
        response.model_dump_json()
    except (ValidationError, PydanticSerializationError):
        raise health_api_error(HealthDataLookupError()) from None
    return response


@router.get("/data-status", response_model=DataResponse[HealthDataStatusResponse])
async def get_data_status(
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_health_user)],
) -> DataResponse[HealthDataStatusResponse]:
    try:
        result = await service.get_data_status(
            user.user_id, session_factory=request.app.state.sessionmaker
        )
    except user_service.UserNotFoundError:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.") from None
    except HealthDataError as error:
        raise health_api_error(error) from None
    if result.user_id != user.user_id:
        raise health_api_error(HealthDataForbiddenError(service.STATUS_METRICS))
    try:
        response = DataResponse(data=data_status_response(result))
        response.model_dump_json()
    except (ValidationError, PydanticSerializationError):
        raise health_api_error(HealthDataLookupError(service.STATUS_METRICS)) from None
    return response


@router.get("/metrics/{metric}", response_model=DataResponse[MetricResponse])
async def get_metric(
    metric: HealthMetric,
    period: Annotated[MetricPeriodRequest, Query()],
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_health_user)],
) -> DataResponse[MetricResponse]:
    try:
        with query_failure_diagnostics(
            request.state.request_id, route="metric", stage="lookup"
        ):
            result = await service.get_metric_result(
                user.user_id,
                metric,
                period.start,
                period.end,
                session_factory=request.app.state.sessionmaker,
            )
    except HealthDataError as error:
        raise health_api_error(error) from None
    if result.user_id != user.user_id:
        raise health_api_error(HealthDataForbiddenError((metric,)))
    try:
        with query_failure_diagnostics(
            request.state.request_id, route="metric", stage="response"
        ):
            response = DataResponse(data=metric_response(result.to_public()))
            # FastAPI 응답 전송 전에 알려진 직렬화 실패를 공개 조회 오류로 변환한다.
            response.model_dump_json()
    except (ValidationError, PydanticSerializationError):
        raise health_api_error(HealthDataLookupError((metric,))) from None
    return response
