/** 동일한 합성 회원의 수집 기준. 실제 API 연결 시에는 응답 값을 사용한다. */
export const healthMockLinkedFrom = '2026-10-01';
export const healthMockSyncedUntil = '2026-10-26T01:00:00Z';
export const healthMockSyncedDate = new Date(Date.parse(healthMockSyncedUntil) + 9 * 3600000)
	.toISOString()
	.slice(0, 10);
