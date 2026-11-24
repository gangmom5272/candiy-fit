import { ApiError, isRecord } from '#lib/api/errors.js';

/** 준비 미완료는 명시적인 건강 조회 사유로만 판별한다. 일반 장애·결측·미연동과 구분한다. */
export function isHealthDataNotReady(error: unknown): boolean {
	return (
		error instanceof ApiError &&
		error.status === 503 &&
		error.code === 'HEALTH_DATA_UNAVAILABLE' &&
		isRecord(error.context) &&
		error.context.reason === 'DATA_NOT_READY'
	);
}
