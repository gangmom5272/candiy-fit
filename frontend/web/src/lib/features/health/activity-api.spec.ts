import { expect, it, vi } from 'vitest';
import { createApiClient } from '#lib/api/client.js';
import { activityMetricFixture } from '#lib/testing/activity-api-data.js';
import {
	activityPeriod,
	activityToday,
	activityWeek,
	activityRecentWeek,
	getActivityHours,
	getActivityMetric,
	loadActivity
} from './activity-api';
import { getExerciseSummary, getWeekdayAverages } from './activity';

const today = '2026-10-26';
const week = activityWeek(today, -1);

it('운동 API의 0·null·정상값을 추이와 집계에서 보존한다', async () => {
	const response = activityMetricFixture('exercises', week.start, week.end);
	const template = response.detail.sessions[0];
	// 심박 0은 정상 서버 측정값이 아닌 경계 입력이다. 파서·집계의 원본 보존을 의도적으로 검사한다.
	response.detail.sessions = [0, null, 72].map((value, index) => ({
		...template,
		date: response.days[index].date,
		countsAsExercise: true,
		caloriesKcal: value,
		avgHeartRate: value,
		maxHeartRate: value
	}));
	const original = structuredClone(response);
	const client = createApiClient({ fetch: vi.fn(async () => Response.json({ data: response })) });
	const result = await getActivityMetric('exercises', week.start, week.end, undefined, client);
	const summary = getExerciseSummary(activityPeriod(result), result.detail.sessions);
	expect(result.detail.sessions.map((session) => session.caloriesKcal)).toEqual([0, null, 72]);
	expect(result.detail.sessions.map((session) => session.avgHeartRate)).toEqual([0, null, 72]);
	expect(summary.metrics[1]).toMatchObject({
		trend: [0, null, 72, null, null, null, null],
		value: '평균 36 kcal',
		description: '최고 72 kcal',
		detail: '총 72 kcal'
	});
	expect(summary.metrics[2]).toMatchObject({
		trend: [0, null, 72, null, null, null, null],
		value: '평균 36 bpm',
		description: '최고 72 bpm'
	});
	expect(response).toEqual(original);
});

it.each([
	['2026-11-02', '2026-10-27', '2026-11-02'],
	['2027-01-02', '2026-12-27', '2027-01-02'],
	['2028-03-01', '2028-02-24', '2028-03-01']
])('활동 기준일 %s를 포함한 연속 7일을 월·연도·윤일 경계에서도 조회한다', (date, start, end) => {
	expect(activityRecentWeek(date, 0)).toMatchObject({ start, end });
	expect((Date.parse(end) - Date.parse(start)) / 86400000).toBe(6);
	expect(activityRecentWeek(date, -1).end).toBe(
		new Date(Date.parse(start) - 86400000).toISOString().slice(0, 10)
	);
});

it('API 진행 상태를 보존하고 최근 28일의 완료된 유효 기록만 요일 평균에 반영한다', async () => {
	const response = activityMetricFixture('steps', '2026-09-29', today);
	const data = {
		...response,
		days: [
			{ date: '2026-10-05', value: 6000, missing: false, reason: null, isInProgress: false },
			{ date: '2026-10-12', value: 8000, missing: false, reason: null, isInProgress: false },
			{ date: '2026-10-19', value: 0, missing: true, reason: 'ZERO_STEPS', isInProgress: false },
			{ date: today, value: 1000, missing: false, reason: null, isInProgress: true },
			{ date: '2026-10-06', value: 0, missing: false, reason: null, isInProgress: false },
			{ date: '2026-10-13', value: null, missing: true, reason: 'NULL_VALUE', isInProgress: false }
		]
	};
	const client = createApiClient({ fetch: vi.fn(async () => Response.json({ data })) });
	const result = await getActivityMetric('steps', '2026-09-29', today, undefined, client);
	expect(result.days.find((day) => day.date === today)).toMatchObject({
		value: 1000,
		isInProgress: true,
		missing: false
	});
	expect(getWeekdayAverages(result.days, today)).toEqual([7000, 0, null, null, null, null, null]);
	expect(activityPeriod(result).days.find((day) => day.date === today)?.steps).toBe(1000);
});

it('KST 자정과 연도 경계, 현재 주의 표시·실제 요청 범위를 구분한다', () => {
	expect(activityToday(new Date('2026-10-25T15:00:00Z'))).toBe(today);
	expect(activityToday(new Date('2026-10-25T14:59:59Z'))).toBe('2026-10-25');
	expect(activityWeek(today, 0)).toEqual({ start: today, end: today, label: '2026.10.26 ~ 11.01' });
	expect(activityWeek('2027-01-01', 0)).toEqual({
		start: '2026-12-28',
		end: '2027-01-01',
		label: '2026.12.28 ~ 2027.01.03'
	});
});

it('서버 summary와 ZERO_STEPS 원본을 보존하고 운동 0분도 유효 값으로 사용한다', async () => {
	const response = activityMetricFixture('steps', week.start, week.end);
	response.summary.avg = 12345;
	const zeroResponse = {
		...response,
		days: response.days.map((day, index) =>
			index === 0 ? { ...day, value: 0, missing: true, reason: 'ZERO_STEPS' } : day
		)
	};
	const client = createApiClient({
		fetch: vi.fn(async () => Response.json({ data: zeroResponse }))
	});
	const data = await getActivityMetric('steps', week.start, week.end, undefined, client);
	expect(data.summary.avg).toBe(12345);
	expect(activityPeriod(data).days[0]).toMatchObject({
		steps: 0,
		missing: true,
		reason: 'ZERO_STEPS'
	});
	const exercise = getExerciseSummary(activityPeriod(data), [], {
		avg: 0,
		total: 0,
		values: [0, null, null, null, null, null, null]
	});
	expect(exercise.metrics[0]).toMatchObject({ value: '평균 0분', detail: '총 0분', average: 0 });
});

it('주간·이전 주·최근 28일과 시간대 요청을 정확히 보내고 보조 조회 실패는 알린다', async () => {
	const urls: string[] = [];
	const client = createApiClient({
		fetch: vi.fn(async (input) => {
			const url = new URL(String(input), 'https://example.test');
			urls.push(url.pathname + url.search);
			if (url.pathname.endsWith('/timeline')) return new Response('unavailable', { status: 503 });
			return Response.json({
				data: activityMetricFixture(
					url.pathname.split('/').at(-1) ?? '',
					url.searchParams.get('start') ?? '',
					url.searchParams.get('end') ?? ''
				)
			});
		})
	});
	const result = await loadActivity(week, today, new AbortController().signal, client);
	expect(urls).toEqual(
		expect.arrayContaining([
			'/api/v1/health/metrics/steps?start=2026-10-19&end=2026-10-25',
			'/api/v1/health/metrics/exercises?start=2026-10-19&end=2026-10-25',
			'/api/v1/health/metrics/steps?start=2026-10-12&end=2026-10-18',
			'/api/v1/health/metrics/steps?start=2026-09-29&end=2026-10-26',
			'/api/v1/health/timeline?start=2026-10-19&end=2026-10-25'
		])
	);
	expect(result.warnings).toEqual(['시간대별 걸음 수']);
	expect(result.hours).toBeNull();
	expect(result.steps.summary.avg).toBe(7760);
	expect(result.exercise.detail.sessions.some((s) => !s.countsAsExercise)).toBe(true);
});

it('세션 만료(AUTH_EXPIRED)는 다시 보내지 않고 로그인 상태 정리를 알린 뒤 오류를 던진다', async () => {
	const fetcher = vi.fn(async () =>
		Response.json({ error: { code: 'AUTH_EXPIRED', message: 'expired' } }, { status: 401 })
	);
	const client = createApiClient({ fetch: fetcher });
	const handleSessionExpired = vi.fn();
	client.setAuthHandler({ handleSessionExpired });
	await expect(getActivityMetric('steps', today, today, undefined, client)).rejects.toMatchObject({
		code: 'AUTH_EXPIRED'
	});
	expect(handleSessionExpired).toHaveBeenCalledTimes(1);
	expect(fetcher).toHaveBeenCalledTimes(1);
});

it('잘못된 시간대 응답과 필수 주간 조회 오류를 빈 데이터로 바꾸지 않는다', async () => {
	const invalid = createApiClient({
		fetch: vi.fn(async () => Response.json({ data: { days: [{ date: today, hourlySteps: [0] }] } }))
	});
	await expect(getActivityHours(today, today, undefined, invalid)).rejects.toThrow('hourlySteps');
	const failed = createApiClient({
		fetch: vi.fn(async () => {
			throw new TypeError('network');
		})
	});
	await expect(loadActivity(week, today, new AbortController().signal, failed)).rejects.toThrow(
		'network'
	);
});
