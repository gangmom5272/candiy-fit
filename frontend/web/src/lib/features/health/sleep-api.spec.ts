import { expect, it, vi } from 'vitest';
import { createApiClient } from '#lib/api/client.js';
import { sleepMetricFixture } from '#lib/testing/sleep-api-data.js';
import { activityWeek, activityRecentWeek } from './activity-api';
import {
	getSleepMetric,
	getSleepTimeline,
	loadSleepWeek,
	loadSleepDetail,
	sleepDateChoices
} from './sleep-api';
import type { SleepSegment } from './sleep-detail';
import {
	metricDayViewFixture,
	sleepNightViewFixture
} from '#lib/testing/health-api-fixture-fields.js';

const today = '2026-10-26';

it('수면 상세 필드가 추가되어도 기존 수면 화면의 파싱 결과를 유지한다', async () => {
	const response = sleepMetricFixture('sleeps', '2026-10-24', today);
	const before = structuredClone(response.detail.nights).map(sleepNightViewFixture);
	for (const night of response.detail.nights) {
		Object.assign(night, {
			sessionMinutes: 480,
			unspecifiedMinutes: 0,
			unknownMinutes: null,
			avgHeartRate: 58.3,
			minHeartRate: 45,
			maxHeartRate: 81,
			spo2Avg: 95.7,
			spo2Min: 90.5,
			spo2Max: 99,
			spo2Count: 25,
			spo2LowCount: 0
		});
	}
	const client = createApiClient({ fetch: vi.fn(async () => Response.json({ data: response })) });
	const data = await getSleepMetric('sleeps', '2026-10-24', today, undefined, client);
	expect(data.detail.nights).toEqual(before);
});

it('수면·산소포화도는 최근 7일, 이전 비교는 겹치지 않는 직전 7일을 요청한다', async () => {
	const urls: string[] = [];
	const client = createApiClient({
		fetch: vi.fn(async (input) => {
			const url = new URL(String(input), 'http://example.test');
			urls.push(url.pathname + url.search);
			return Response.json({
				data: sleepMetricFixture(
					url.pathname.includes('oxygen') ? 'oxygen_saturations' : 'sleeps',
					url.searchParams.get('start') ?? '',
					url.searchParams.get('end') ?? ''
				)
			});
		})
	});
	const result = await loadSleepWeek(
		activityRecentWeek('2026-11-02', 0),
		new AbortController().signal,
		client
	);
	expect(urls).toEqual(
		expect.arrayContaining([
			'/api/v1/health/metrics/sleeps?start=2026-10-27&end=2026-11-02',
			'/api/v1/health/metrics/oxygen_saturations?start=2026-10-27&end=2026-11-02',
			'/api/v1/health/metrics/sleeps?start=2026-10-20&end=2026-10-26'
		])
	);
	expect(result.sleep.status).toBe('fulfilled');
	expect(result.previous.status).toBe('fulfilled');
});

it('서버 수면 summary, 오늘 완료 상태와 sleepMinutes null을 원본 그대로 유지한다', async () => {
	const response = sleepMetricFixture('sleeps', '2026-10-24', today);
	for (const [index, value] of [412, 413].entries()) {
		response.days[index] = { ...response.days[index], value, missing: false, reason: null };
		const night = response.detail.nights.find((n) => n.date === response.days[index].date);
		if (!night) throw new Error('수면 fixture 없음');
		night.sleepMinutes = value;
	}
	response.summary.avg = 412.5;
	response.summary.daysWithData = response.summary.daysUsedForSummary = 2;
	response.summary.dailyMin = { date: '2026-10-24', value: 412 };
	response.summary.dailyMax = { date: '2026-10-25', value: 413 };
	const client = createApiClient({ fetch: vi.fn(async () => Response.json({ data: response })) });
	const data = await getSleepMetric('sleeps', '2026-10-24', today, undefined, client);
	expect(data.summary.avg).toBe(412.5);
	expect(data.inProgress).toBeNull();
	expect(data.days[2]).toEqual(metricDayViewFixture(response.days[2]));
	expect(data.detail.nights.find((n) => n.date === today)?.sleepMinutes).toBeNull();
	expect(data.detail.nights[0].startAt).toBe(response.detail.nights[0].startAt);
});

it('명세와 다른 합성 산소포화도 평균도 파서가 임의 재계산하지 않는다', async () => {
	const response = sleepMetricFixture('oxygen_saturations', '2026-10-25', today);
	response.days[0] = { ...response.days[0], value: 96.5, missing: false, reason: null };
	response.days[1] = { ...response.days[1], value: 97.1, missing: false, reason: null };
	// v47 정상 계약은 단순 일평균 96.8이다. 이 테스트만 고의로 96.7을 보내 파서의 원본 보존을 검사한다.
	response.summary.avg = 96.7;
	response.summary.dailyMin = { date: '2026-10-25', value: 96.5 };
	response.summary.dailyMax = { date: today, value: 97.1 };
	response.detail.measuredMin = { date: '2026-10-25', value: 70 };
	response.detail.measuredMax = { date: today, value: 99 };
	response.detail.dailyRanges = [
		{ date: '2026-10-25', min: 70, max: 98, sampleCount: null },
		{ date: today, min: 94, max: 99, sampleCount: null }
	];
	const client = createApiClient({ fetch: vi.fn(async () => Response.json({ data: response })) });
	const data = await getSleepMetric('oxygen_saturations', '2026-10-25', today, undefined, client);
	expect(data.summary.avg).toBe(96.7);
	expect(data.detail.measuredMin?.value).toBe(70);
	expect(data.detail.dailyRanges[1]).toEqual({ date: today, min: 94, max: 99 });
	expect(data.days[0].isInProgress).toBe(false);
	expect(data.inProgress).toBeNull();
});

it('timeline의 6개 stage와 mainSleep을 재분류하거나 재생성하지 않는다', async () => {
	const mainSleep = { startAt: '2026-10-25T14:30:00Z', endAt: '2026-10-26T00:00:00Z' };
	const segments: SleepSegment[] = [
		{ startAt: '2026-10-25T14:30:00Z', endAt: '2026-10-25T15:00:00Z', stage: 'deep' },
		{ startAt: '2026-10-25T15:00:00Z', endAt: '2026-10-25T16:00:00Z', stage: 'rem' },
		{ startAt: '2026-10-25T16:00:00Z', endAt: '2026-10-25T17:00:00Z', stage: 'light' },
		{ startAt: '2026-10-25T17:00:00Z', endAt: '2026-10-25T17:10:00Z', stage: 'awake' },
		{ startAt: '2026-10-25T17:10:00Z', endAt: '2026-10-25T18:00:00Z', stage: 'unknown' },
		{ startAt: '2026-10-25T18:00:00Z', endAt: '2026-10-26T00:00:00Z', stage: 'unspecified' }
	];
	const response = { days: [{ date: today, mainSleep, sleepSegments: segments }] };
	const client = createApiClient({ fetch: vi.fn(async () => Response.json({ data: response })) });
	const [data] = await getSleepTimeline(today, undefined, client);
	expect(data.sleepSegments).toEqual(segments);
	expect(data.mainSleep).toEqual(mainSleep);
});

it('독립 요청의 503을 빈 기록으로 바꾸지 않고 정상 수면과 이전 주 비교를 유지한다', async () => {
	const urls: string[] = [];
	const controller = new AbortController();
	const client = createApiClient({
		fetch: vi.fn(async (input, init) => {
			const url = new URL(String(input), 'http://example.test');
			urls.push(url.pathname + url.search);
			expect(init?.signal).toBe(controller.signal);
			if (url.pathname.includes('oxygen_saturations'))
				return Response.json(
					{
						error: {
							code: 'HEALTH_DATA_UNAVAILABLE',
							message: '산소포화도 준비 중',
							context: { reason: 'DATA_NOT_READY' }
						}
					},
					{ status: 503 }
				);
			return Response.json({
				data: sleepMetricFixture(
					'sleeps',
					url.searchParams.get('start') ?? '',
					url.searchParams.get('end') ?? ''
				)
			});
		})
	});
	const result = await loadSleepWeek(activityWeek(today, 0), controller.signal, client);
	expect(result.sleep.status).toBe('fulfilled');
	expect(result.oxygen.status).toBe('rejected');
	expect(result.previous.status).toBe('fulfilled');
	expect(urls).toContain('/api/v1/health/metrics/sleeps?start=2026-10-19&end=2026-10-25');
	expect(urls).toContain('/api/v1/health/metrics/sleeps?start=2026-10-26&end=2026-10-26');
});

it('단일 날짜 상세 요청도 독립 실패를 보존하고 401은 인증 오류로 전파한다', async () => {
	const client = createApiClient({
		fetch: vi.fn(async () => {
			throw new TypeError('network');
		})
	});
	const result = await loadSleepDetail(today, new AbortController().signal, client);
	expect(Object.values(result).every((r) => r.status === 'rejected')).toBe(true);
	const unauthorized = createApiClient({
		fetch: vi.fn(async () =>
			Response.json({ error: { code: 'AUTH_REQUIRED', message: '로그인 필요' } }, { status: 401 })
		)
	});
	await expect(
		loadSleepDetail(today, new AbortController().signal, unauthorized)
	).rejects.toMatchObject({ status: 401 });
});

it('수면 API도 세션 만료(AUTH_EXPIRED)를 다시 보내지 않고 로그인 상태 정리를 알린다', async () => {
	const fetch = vi.fn(async () =>
		Response.json({ error: { code: 'AUTH_EXPIRED', message: '만료' } }, { status: 401 })
	);
	const client = createApiClient({ fetch });
	const handleSessionExpired = vi.fn();
	client.setAuthHandler({ handleSessionExpired });
	await expect(getSleepMetric('sleeps', today, today, undefined, client)).rejects.toMatchObject({
		code: 'AUTH_EXPIRED'
	});
	expect(handleSessionExpired).toHaveBeenCalledTimes(1);
	expect(fetch).toHaveBeenCalledTimes(1);
});

it('날짜 선택지는 서버 연동 시작일부터 오늘까지만 만들고 기록 상태는 생성하지 않는다', () => {
	expect(sleepDateChoices('2026-10-25', today)).toEqual(['2026-10-25', today]);
	expect(sleepDateChoices(null, today)).toEqual([]);
});

it.each(['sleeps', 'oxygen_saturations'] as const)(
	'%s fixture는 주 경계·부분 기간·연동 전의 응답 계약을 지킨다',
	(metric) => {
		const result = sleepMetricFixture(metric, '2026-10-18', today);
		expect(result.days).toHaveLength(9);
		expect(result.days[0].date).toBe('2026-10-18');
		expect(result.days.at(-1)?.date).toBe(today);
		const oneDay = sleepMetricFixture(metric, '2026-10-25', '2026-10-25');
		for (const extreme of [
			oneDay.summary.dailyMin,
			oneDay.summary.dailyMax,
			oneDay.detail.measuredMin,
			oneDay.detail.measuredMax
		]) {
			if (extreme !== null) expect(extreme.date).toBe('2026-10-25');
		}
		const beforeLink = sleepMetricFixture(metric, '2026-09-22', '2026-09-28');
		expect(beforeLink.days).toHaveLength(7);
		expect(
			beforeLink.days.every(
				(day) => day.reason === 'BEFORE_LINK' && day.missing && day.value === null
			)
		).toBe(true);
		expect(beforeLink.summary).toMatchObject({
			completedDays: 0,
			daysWithData: 0,
			avg: null,
			dailyMin: null,
			dailyMax: null
		});
		expect(beforeLink.detail.measuredMin).toBeNull();
		expect(beforeLink.detail.measuredMax).toBeNull();
	}
);
