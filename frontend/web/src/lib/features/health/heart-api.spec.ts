import { expect, it, vi } from 'vitest';
import { createApiClient } from '#lib/api/client.js';
import { ResponseFormatError } from '#lib/api/errors.js';
import { heartMetricFixture } from '#lib/testing/heart-api-data.js';
import { activityToday, activityWeek } from './activity-api';
import { getHeartMetric } from './heart-api';
import { heartMissingMessage } from './heart';

it('일별 sampleCount가 추가되어도 기존 심박 파싱 결과를 유지한다', async () => {
	const original = heartMetricFixture('2026-10-19', '2026-10-25');
	const response = structuredClone(original);
	response.detail.dailyRanges = response.detail.dailyRanges.map((day) => ({
		...day,
		sampleCount: day.min === null ? null : 42
	}));
	const client = createApiClient({ fetch: async () => Response.json({ data: response }) });
	expect(await getHeartMetric('2026-10-19', '2026-10-25', undefined, client)).toEqual(original);
});

it('요청 URL·기간·취소 signal을 전달하고 서버 가중 평균과 일별 범위를 그대로 보존한다', async () => {
	const response = heartMetricFixture('2026-10-19', '2026-10-25');
	const controller = new AbortController();
	const fetch = vi.fn(async () => Response.json({ data: response }));
	const data = await getHeartMetric(
		'2026-10-19',
		'2026-10-25',
		controller.signal,
		createApiClient({ fetch })
	);
	expect(fetch).toHaveBeenCalledWith(
		'/api/v1/health/metrics/heart_rates?start=2026-10-19&end=2026-10-25',
		expect.objectContaining({ method: 'GET', signal: controller.signal })
	);
	expect(data).toEqual(response);
	const values = data.days.flatMap((day) => (day.value === null ? [] : [day.value]));
	expect(data.summary.avg).toBe(75.68);
	expect(data.summary.avg).not.toBeCloseTo(
		values.reduce((sum, value) => sum + value, 0) / values.length,
		2
	);
	expect(data.detail.dailyRanges[0]).toEqual({ date: '2026-10-19', min: 54, max: 132 });
	expect(data.detail.measuredMin).toEqual({ date: '2026-10-22', value: 53 });
});

it('진행 중인 오늘 일별 값과 범위는 유지하고 서버의 빈 기간 평균을 보정하지 않는다', async () => {
	const response = heartMetricFixture('2026-10-26', '2026-10-26');
	const client = createApiClient({ fetch: async () => Response.json({ data: response }) });
	const data = await getHeartMetric('2026-10-26', '2026-10-26', undefined, client);
	expect(data.days).toEqual([
		{ date: '2026-10-26', value: 74.2, missing: false, reason: null, isInProgress: true }
	]);
	expect(data.summary.avg).toBeNull();
	expect(data.inProgress).toEqual(response.inProgress);
	expect(data.summary.daysUsedForSummary).toBe(0);
	expect(data.detail.dailyRanges).toEqual([{ date: '2026-10-26', min: 52, max: 118 }]);
});

it('nullable 계약의 방어적 입력에서 실제 0과 범위 null을 그대로 보존한다', async () => {
	const response = heartMetricFixture('2026-10-19', '2026-10-25');
	// REST 타입은 nullable이지만 현재 Backend heart_record는 유효 평균에 min/max를 요구한다.
	// 실제 서버 정상 응답 검증과 별개로, parser가 nullable 값을 임의 보정하지 않는지 확인한다.
	response.days[0].value = 0;
	response.detail.dailyRanges[0] = { date: response.days[0].date, min: null, max: null };
	response.detail.dailyRanges[1].min = null;
	response.summary = {
		...response.summary,
		avg: 66.92,
		dailyMin: { date: response.days[0].date, value: 0 },
		dailyMax: { date: '2026-10-22', value: 78 }
	};
	const client = createApiClient({ fetch: async () => Response.json({ data: response }) });
	const data = await getHeartMetric('2026-10-19', '2026-10-25', undefined, client);
	expect(data).toEqual(response);
	expect(data.days[0]).toMatchObject({ value: 0, missing: false });
});

it.each([
	['BEFORE_LINK', '연동 전 날짜예요.'],
	['UNSYNCED', '아직 동기화되지 않았어요. 앱을 열어 동기화해 주세요.'],
	['NO_RECORD', '심박 기록이 없어요.'],
	['NULL_VALUE', '기록에 심박수 값이 없어요.']
] as const)('%s는 서버 사유를 보존하고 사용자 안내로 표시한다', async (reason, message) => {
	const response = heartMetricFixture('2026-10-12', '2026-10-18');
	response.days = response.days.map((day) => ({ ...day, reason }));
	response.summary.completedDays = reason === 'BEFORE_LINK' ? 0 : 7;
	response.summary.linkedFrom = reason === 'BEFORE_LINK' ? '2026-10-19' : '2026-10-01';
	response.summary.missingDays =
		reason === 'NO_RECORD' || reason === 'NULL_VALUE' ? response.days.map((day) => day.date) : [];
	response.summary.unsyncedDays = reason === 'UNSYNCED' ? response.days.map((day) => day.date) : [];
	if (reason === 'UNSYNCED') {
		response.dataStatus.lastDataAt = '2026-10-11T15:00:00Z';
		response.dataStatus.syncedUntil = response.dataStatus.lastDataAt;
	}
	const client = createApiClient({ fetch: async () => Response.json({ data: response }) });
	const data = await getHeartMetric('2026-10-12', '2026-10-18', undefined, client);
	expect(data).toEqual(response);
	expect(data.days.every((day) => day.value === null && day.missing)).toBe(true);
	expect(heartMissingMessage(data.days[0].reason)).toBe(message);
});

it('401 AUTH_EXPIRED는 다시 보내지 않고 로그인 상태 정리를 알린 뒤 오류를 던진다', async () => {
	const fetch = vi.fn<typeof globalThis.fetch>(async () =>
		Response.json({ error: { code: 'AUTH_EXPIRED', message: '만료' } }, { status: 401 })
	);
	const client = createApiClient({ fetch });
	const handleSessionExpired = vi.fn();
	client.setAuthHandler({ handleSessionExpired });
	await expect(getHeartMetric('2026-10-26', '2026-10-26', undefined, client)).rejects.toMatchObject(
		{ code: 'AUTH_EXPIRED' }
	);
	expect(handleSessionExpired).toHaveBeenCalledTimes(1);
	expect(fetch).toHaveBeenCalledTimes(1);
});

it.each([
	[401, 'AUTH_REQUIRED'],
	[403, 'HEALTH_DATA_FORBIDDEN'],
	[503, 'HEALTH_DATA_UNAVAILABLE']
] as const)('%i API 오류는 빈 데이터나 mock으로 바꾸지 않는다', async (status, code) => {
	const client = createApiClient({
		fetch: async () =>
			Response.json(
				{
					error: {
						code,
						message: '조회 실패',
						...(status === 503
							? { context: { reason: 'LOOKUP_FAILED', metrics: ['heart_rates'] } }
							: {})
					}
				},
				{ status }
			)
	});
	await expect(getHeartMetric('2026-10-26', '2026-10-26', undefined, client)).rejects.toMatchObject(
		{ status, code, message: '조회 실패' }
	);
});

it('네트워크 오류는 정상 데이터로 대체하지 않는다', async () => {
	const client = createApiClient({
		fetch: async () => {
			throw new TypeError('Network error');
		}
	});
	await expect(getHeartMetric('2026-10-26', '2026-10-26', undefined, client)).rejects.toThrow(
		'Network error'
	);
});

it('응답 타입 오류를 숫자나 빈 값으로 강제 변환하지 않는다', async () => {
	const response = heartMetricFixture('2026-10-26', '2026-10-26');
	const client = createApiClient({
		fetch: async () =>
			Response.json({ data: { ...response, summary: { ...response.summary, avg: '74.2' } } })
	});
	await expect(
		getHeartMetric('2026-10-26', '2026-10-26', undefined, client)
	).rejects.toBeInstanceOf(ResponseFormatError);
});

it('UTC 자정 대신 KST 날짜를 사용하고 현재 주 조회를 오늘로 제한한다', () => {
	const today = activityToday(new Date('2026-10-25T15:30:00Z'));
	expect(today).toBe('2026-10-26');
	expect(activityWeek(today, 0)).toMatchObject({ start: today, end: today });
	expect(activityWeek('2026-10-28', 0)).toMatchObject({ start: today, end: '2026-10-28' });
	expect(activityWeek(today, -1)).toMatchObject({ start: '2026-10-19', end: '2026-10-25' });
});
