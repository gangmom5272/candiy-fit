"""즉시 동기화의 공개 결과. 원본·개수·내부 오류는 응답에 포함하지 않는다."""

from app.core.schemas import ApiResponse
from app.health.sync_service import SyncStatus


class HealthSyncResponse(ApiResponse):
    status: SyncStatus
