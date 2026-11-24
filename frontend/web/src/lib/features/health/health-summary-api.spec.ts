import { expect, it, vi } from 'vitest';
import { createApiClient } from '#lib/api/client.js';
import { activityMetricFixture } from '#lib/testing/activity-api-data.js';
import { sleepMetricFixture } from '#lib/testing/sleep-api-data.js';
import { heartMetricFixture } from '#lib/testing/heart-api-data.js';
import { getHealthSummary, healthSummaryMetrics } from './health-summary-api';
import { activityWeek } from './activity-api';
import { metricDayViewFixture } from '#lib/testing/health-api-fixture-fields.js';

it.each(healthSummaryMetrics)(
	'%s 응답이 월요일부터 와도 날짜·값·결측을 함께 오름차순 배치한다',
	async (metric) => {
		const start = '2026-10-27',
			end = '2026-11-02';
		const response =
			metric === 'sleeps'
				? sleepMetricFixture('sleeps', start, end)
				: metric === 'heart_rates'
					? heartMetricFixture(start, end)
					: activityMetricFixture(metric, start, end);
		const expectedDays = structuredClone(response.days).map(metricDayViewFixture);
		response.days = [...response.days.slice(-1), ...response.days.slice(0, -1)];
		const originalOrder = response.days.map((day) => day.date);
		const result = await getHealthSummary(
			metric,
			start,
			end,
			undefined,
			createApiClient({ fetch: async () => Response.json({ data: response }) })
		);
		expect(result.days).toEqual(expectedDays);
		expect(result.dates).toEqual(['10.27', '10.28', '10.29', '10.30', '10.31', '11.01', '11.02']);
		expect(result.values).toEqual(expectedDays.map((day) => (day.missing ? null : day.value)));
		expect(result.average).toBe(response.summary.avg);
		expect(response.days.map((day) => day.date)).toEqual(originalOrder);
	}
);

it.each(healthSummaryMetrics)(
	'%s는 동일 기간의 API·서버 평균·날짜 상태·통계 일수를 사용한다',
	async (metric) => {
		const start = '2026-10-19';
		const end = '2026-10-25';
		const response =
			metric === 'sleeps'
				? sleepMetricFixture('sleeps', start, end)
				: metric === 'heart_rates'
					? heartMetricFixture(start, end)
					: activityMetricFixture(metric, start, end);
		const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ data: response }));
		const controller = new AbortController();
		const result = await getHealthSummary(
			metric,
			start,
			end,
			controller.signal,
			createApiClient({ fetch })
		);
		expect(fetch).toHaveBeenCalledExactlyOnceWith(
			`/api/v1/health/metrics/${metric}?start=${start}&end=${end}`,
			expect.objectContaining({ method: 'GET', signal: controller.signal })
		);
		expect(result.average).toBe(response.summary.avg);
		expect(result.values).toEqual(response.days.map((day) => (day.missing ? null : day.value)));
		expect(result.days).toEqual(response.days.map(metricDayViewFixture));
		expect(result.recordedDays).toBe(response.summary.daysWithData);
		expect(result.completedDays).toBe(response.summary.completedDays);
		expect(result.dates).toEqual(['10.19', '10.20', '10.21', '10.22', '10.23', '10.24', '10.25']);
		if (metric === 'steps') expect(result.description).toBe('총 46,560보');
		if (metric === 'sleeps') expect(result.value).toBe('평균 6시간 29분');
		if (metric === 'heart_rates') {
			expect(result.label).toBe('일별 심박수');
			expect(result.average).toBe(75.68);
			const values = result.values.filter((value) => value !== null);
			expect(result.average).not.toBeCloseTo(
				values.reduce((sum, value) => sum + value, 0) / values.length,
				2
			);
		}
	}
);

it('진행 중 오늘 심박은 표시하고 null 기간 평균과 완료 기록 0일을 유지한다', async () => {
	const today = '2026-10-26';
	const response = heartMetricFixture(today, today);
	const result = await getHealthSummary(
		'heart_rates',
		today,
		today,
		undefined,
		createApiClient({ fetch: async () => Response.json({ data: response }) })
	);
	expect(result.average).toBeNull();
	expect(result.value).toBe('집계된 평균 없음');
	expect(result.values).toEqual([74.2]);
	expect(result.days?.[0].isInProgress).toBe(true);
	expect(result.recordedDays).toBe(0);
	expect(result.completedDays).toBe(0);
});

it('서버의 ZERO_STEPS 결측 0과 유효한 운동 0분을 구분한다', async () => {
	const steps = activityMetricFixture('steps', '2026-10-21', '2026-10-21');
	steps.days[0] = { ...steps.days[0], value: 0, missing: true, reason: 'ZERO_STEPS' };
	steps.summary.missingDays = ['2026-10-21'];
	Object.assign(steps.summary, {
		avg: null,
		total: null,
		daysWithData: 0,
		daysUsedForSummary: 0,
		dailyMin: null,
		dailyMax: null
	});
	const exercise = activityMetricFixture('exercises', '2026-10-21', '2026-10-21');
	const client = createApiClient({
		fetch: async (input) =>
			Response.json({ data: String(input).includes('/steps?') ? steps : exercise })
	});
	const stepCard = await getHealthSummary('steps', '2026-10-21', '2026-10-21', undefined, client);
	const exerciseCard = await getHealthSummary(
		'exercises',
		'2026-10-21',
		'2026-10-21',
		undefined,
		client
	);
	expect(stepCard.days?.[0]).toMatchObject({ value: 0, reason: 'ZERO_STEPS', missing: true });
	expect(stepCard.values).toEqual([null]);
	expect(exerciseCard.values).toEqual([0]);
	expect(exerciseCard.value).toBe('평균 0분');
	expect(exerciseCard.average).toBe(0);
});

it('연동 전을 누락된 7일로 세지 않고 서버의 완료 일수를 그대로 사용한다', async () => {
	const response = activityMetricFixture('steps', '2026-09-28', '2026-10-04');
	const result = await getHealthSummary(
		'steps',
		'2026-09-28',
		'2026-10-04',
		undefined,
		createApiClient({ fetch: async () => Response.json({ data: response }) })
	);
	expect(result.completedDays).toBe(4);
	expect(result.recordedDays).toBe(0);
	expect(result.days?.slice(0, 3).every((day) => day.reason === 'BEFORE_LINK')).toBe(true);
});

it('현재 주는 KST 오늘까지만, 이전 주는 월~일 전체를 조회한다', () => {
	expect(activityWeek('2026-10-28', 0)).toMatchObject({ start: '2026-10-26', end: '2026-10-28' });
	expect(activityWeek('2026-10-28', -1)).toMatchObject({ start: '2026-10-19', end: '2026-10-25' });
});

it('API 오류와 네트워크 오류를 정상 빈 카드나 Home mock으로 대체하지 않는다', async () => {
	const client = createApiClient({
		fetch: async () =>
			Response.json(
				{
					error: {
						code: 'HEALTH_DATA_UNAVAILABLE',
						message: '수면 준비 중',
						context: { reason: 'DATA_NOT_READY', metrics: ['sleeps'] }
					}
				},
				{ status: 503 }
			)
	});
	await expect(
		getHealthSummary('sleeps', '2026-10-26', '2026-10-26', undefined, client)
	).rejects.toMatchObject({ status: 503 });
	await expect(
		getHealthSummary(
			'steps',
			'2026-10-26',
			'2026-10-26',
			undefined,
			createApiClient({
				fetch: async () => {
					throw new TypeError('Network error');
				}
			})
		)
	).rejects.toThrow('Network error');
});
