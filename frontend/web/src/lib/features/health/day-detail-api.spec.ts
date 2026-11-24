import { expect, it, vi } from 'vitest';
import { createApiClient } from '#lib/api/client.js';
import { ResponseFormatError } from '#lib/api/errors.js';
import { dayMetricFixture, dayTimelineFixture } from '#lib/testing/day-detail-api-fixture.js';
import { getDayTimeline, loadDayDetail } from './day-detail-api';
import { dayTimelineStart, dayHeartSegments } from './day-detail';
import { metricDayViewFixture } from '#lib/testing/health-api-fixture-fields.js';

const date = '2026-10-19';

it('시간대 심박 sampleCount가 추가되어도 기존 타임라인 파싱을 유지한다', async () => {
	const original = dayTimelineFixture(date);
	const response = structuredClone(original);
	for (const day of response.days) {
		day.hourlyHeartRates =
			day.hourlyHeartRates?.map((hour) => (hour === null ? null : { ...hour, sampleCount: 42 })) ??
			null;
	}
	const client = createApiClient({ fetch: async () => Response.json({ data: response }) });
	expect(await getDayTimeline(date, undefined, client)).toEqual(original.days[0]);
});

function responseFor(input: RequestInfo | URL) {
	const url = new URL(String(input), 'http://example.test');
	const selected = url.searchParams.get('start') ?? '';
	if (url.pathname.endsWith('/timeline')) return dayTimelineFixture(selected);
	const metric = (['steps', 'exercises', 'sleeps', 'heart_rates'] as const).find((metric) =>
		url.pathname.endsWith(`/${metric}`)
	);
	if (!metric) throw new Error('Unknown fixture metric');
	return dayMetricFixture(metric, selected);
}

it('선택 날짜의 지표 4개와 timeline을 각각 한 번 조회하며 원본과 취소 signal을 보존한다', async () => {
	const controller = new AbortController();
	const fetch = vi.fn<typeof globalThis.fetch>(async (input) =>
		Response.json({ data: responseFor(input) })
	);
	const result = await loadDayDetail(date, controller.signal, createApiClient({ fetch }));
	expect(fetch).toHaveBeenCalledTimes(5);
	expect(fetch.mock.calls.map(([input]) => String(input)).sort()).toEqual(
		[
			`/api/v1/health/metrics/steps?start=${date}&end=${date}`,
			`/api/v1/health/metrics/exercises?start=${date}&end=${date}`,
			`/api/v1/health/metrics/sleeps?start=${date}&end=${date}`,
			`/api/v1/health/metrics/heart_rates?start=${date}&end=${date}`,
			`/api/v1/health/timeline?start=${date}&end=${date}`
		].sort()
	);
	for (const [, options] of fetch.mock.calls) expect(options?.signal).toBe(controller.signal);
	for (const [key, metric] of [
		['steps', 'steps'],
		['exercise', 'exercises'],
		['sleep', 'sleeps'],
		['heart', 'heart_rates']
	] as const) {
		expect(result[key]).toEqual({
			status: 'fulfilled',
			value: metricDayViewFixture(dayMetricFixture(metric, date).days[0])
		});
	}
	expect(result.timeline).toEqual({ status: 'fulfilled', value: dayTimelineFixture(date).days[0] });
});

it('오늘의 summary가 null이어도 진행 중 일별 심박을 사용하고 수면 null을 시간차로 대체하지 않는다', async () => {
	const today = '2026-10-26';
	const client = createApiClient({
		fetch: async (input) => Response.json({ data: responseFor(input) })
	});
	const result = await loadDayDetail(today, new AbortController().signal, client);
	expect(result.heart).toMatchObject({
		status: 'fulfilled',
		value: { value: 74.2, isInProgress: true }
	});
	expect(dayMetricFixture('heart_rates', today).summary.avg).toBeNull();
	expect(result.sleep).toMatchObject({
		status: 'fulfilled',
		value: { value: null, missing: true, reason: 'UNSYNCED', isInProgress: false }
	});
});

it('원본 전날 수면 구간과 인정 운동 timeline, 0인 시간대 걸음 및 심박 결측을 유지한다', async () => {
	const selected = '2026-10-25';
	const response = dayTimelineFixture(selected);
	response.days[0].hourlySteps = Array(24).fill(0);
	response.days[0].hourlyHeartRates = Array.from({ length: 24 }, (_, hour) =>
		hour === 3 || hour === 5 ? { avg: 72, min: 60, max: 90 } : null
	);
	const client = createApiClient({ fetch: async () => Response.json({ data: response }) });
	const result = await getDayTimeline(selected, undefined, client);
	expect(result).toEqual(response.days[0]);
	expect(dayTimelineStart(result)).toBe(-148);
	expect(dayHeartSegments(result.hourlyHeartRates)).toHaveLength(2);
	expect(result.hourlySteps?.[0]).toBe(0);
	const missing = dayTimelineFixture('2026-10-21');
	expect(missing.days[0].exerciseSegments).toEqual([]);
	expect(
		dayMetricFixture('exercises', '2026-10-21').detail.sessions.some(
			(session) => !session.countsAsExercise
		)
	).toBe(true);
});

it.each([403, 503])(
	'%i 실패는 지표별 결과로 남기고 정상 지표와 timeline을 유지한다',
	async (status) => {
		const client = createApiClient({
			fetch: async (input) =>
				String(input).includes('/steps?')
					? Response.json(
							{
								error: {
									code: status === 403 ? 'HEALTH_DATA_FORBIDDEN' : 'HEALTH_DATA_UNAVAILABLE',
									message: '조회 실패',
									...(status === 503
										? { context: { reason: 'LOOKUP_FAILED', metrics: ['steps'] } }
										: {})
								}
							},
							{ status }
						)
					: Response.json({ data: responseFor(input) })
		});
		const result = await loadDayDetail(date, new AbortController().signal, client);
		expect(result.steps).toMatchObject({ status: 'rejected', reason: { status } });
		expect(result.sleep.status).toBe('fulfilled');
		expect(result.timeline.status).toBe('fulfilled');
	}
);

it('401은 기존 인증 처리에 전달하며 네트워크 실패를 정상 빈 데이터로 바꾸지 않는다', async () => {
	const client = createApiClient({
		fetch: async () =>
			Response.json({ error: { code: 'AUTH_REQUIRED', message: '로그인 필요' } }, { status: 401 })
	});
	await expect(loadDayDetail(date, new AbortController().signal, client)).rejects.toMatchObject({
		status: 401,
		code: 'AUTH_REQUIRED'
	});
	const failed = await loadDayDetail(
		date,
		new AbortController().signal,
		createApiClient({
			fetch: async () => {
				throw new TypeError('Network error');
			}
		})
	);
	expect(Object.values(failed).every((result) => result.status === 'rejected')).toBe(true);
});

it('timeline의 잘못된 시간대 배열과 응답에 없는 날짜를 빈 기록으로 위장하지 않는다', async () => {
	const response = dayTimelineFixture(date);
	response.days[0].hourlySteps = [1, 2];
	const client = createApiClient({ fetch: async () => Response.json({ data: response }) });
	await expect(getDayTimeline(date, undefined, client)).rejects.toBeInstanceOf(ResponseFormatError);
	const noDay = createApiClient({ fetch: async () => Response.json({ data: { days: [] } }) });
	await expect(getDayTimeline(date, undefined, noDay)).rejects.toBeInstanceOf(ResponseFormatError);
});

it('연동 전과 기록 없음의 reason 및 실제 인정 운동 0분을 구분한다', async () => {
	const client = createApiClient({
		fetch: async (input) => Response.json({ data: responseFor(input) })
	});
	const before = await loadDayDetail('2026-09-30', new AbortController().signal, client);
	const empty = await loadDayDetail('2026-10-07', new AbortController().signal, client);
	const zero = await loadDayDetail('2026-10-21', new AbortController().signal, client);
	expect(before.steps).toMatchObject({
		status: 'fulfilled',
		value: { value: null, missing: true, reason: 'BEFORE_LINK' }
	});
	expect(empty.steps).toMatchObject({
		status: 'fulfilled',
		value: { value: null, missing: true, reason: 'NO_RECORD' }
	});
	expect(zero.exercise).toMatchObject({
		status: 'fulfilled',
		value: { value: 0, missing: false, reason: null }
	});
});
