import { afterEach, expect, it, vi } from 'vitest';
import { createApiClient, type ApiClient } from '#lib/api/client.js';
import { ResponseFormatError } from '#lib/api/errors.js';
import { sleepMetricFixture } from '#lib/testing/sleep-api-data.js';
import { activityWeek } from './activity-api';
import { loadSleepWeek } from './sleep-api';
import {
	syncHealth,
	syncStatuses,
	syncOutcome,
	createHealthSyncRequester,
	readHealthData,
	createHealthReadClient
} from './sync-api';

afterEach(() => vi.useRealTimers());

it.each(['oxygen-delay', 'oxygen-503', 'only-oxygen', 'all-fail'] as const)(
	'개별 GET 제한은 복합 수면 조회의 부분 결과를 보존한다: %s',
	async (state) => {
		vi.useFakeTimers();
		const signals: AbortSignal[] = [];
		const client = createHealthReadClient(
			createApiClient({
				fetch: async (input, options) => {
					if (options?.signal) signals.push(options.signal);
					const url = new URL(String(input), 'http://localhost');
					const oxygen = url.pathname.endsWith('/oxygen_saturations');
					if (oxygen && state === 'oxygen-delay') return new Promise<Response>(() => {});
					if (
						state === 'all-fail' ||
						(oxygen && state === 'oxygen-503') ||
						(!oxygen && state === 'only-oxygen')
					)
						return Response.json(
							{
								error: {
									code: 'HEALTH_DATA_UNAVAILABLE',
									message: 'synthetic lookup failure',
									context: { reason: 'LOOKUP_FAILED' }
								}
							},
							{ status: 503 }
						);
					return Response.json({
						data: sleepMetricFixture(
							oxygen ? 'oxygen_saturations' : 'sleeps',
							url.searchParams.get('start') ?? '',
							url.searchParams.get('end') ?? ''
						)
					});
				}
			})
		);
		const result = loadSleepWeek(
			activityWeek('2026-10-26', 0),
			new AbortController().signal,
			client
		);
		await vi.advanceTimersByTimeAsync(15000);
		const data = await result;
		expect(data.sleep.status).toBe(
			state === 'only-oxygen' || state === 'all-fail' ? 'rejected' : 'fulfilled'
		);
		expect(data.previous.status).toBe(data.sleep.status);
		expect(data.oxygen.status).toBe(state === 'only-oxygen' ? 'fulfilled' : 'rejected');
		if (state === 'oxygen-delay') {
			expect(data.oxygen).toMatchObject({
				status: 'rejected',
				reason: expect.objectContaining({
					message: '건강 기록 조회 시간이 초과됐어요. 다시 시도해 주세요.'
				})
			});
			expect(signals.filter((signal) => signal.aborted)).toHaveLength(1);
		}
		expect(vi.getTimerCount()).toBe(0);
	}
);

it('복합 조회 이탈은 개별 GET signal과 대기 타이머를 모두 정리한다', async () => {
	vi.useFakeTimers();
	const signals: AbortSignal[] = [];
	const client = createHealthReadClient(
		createApiClient({
			fetch: async (_input, options) => {
				if (options?.signal) signals.push(options.signal);
				return new Promise<Response>(() => {});
			}
		})
	);
	const controller = new AbortController();
	const result = loadSleepWeek(activityWeek('2026-10-26', 0), controller.signal, client);
	await vi.advanceTimersByTimeAsync(0);
	controller.abort();
	const data = await result;
	for (const value of Object.values(data))
		expect(value).toMatchObject({
			status: 'rejected',
			reason: expect.objectContaining({ name: 'AbortError' })
		});
	expect(signals).toHaveLength(3);
	expect(signals.every((signal) => signal.aborted)).toBe(true);
	expect(vi.getTimerCount()).toBe(0);
});
it.each(syncStatuses)(
	'%s: 세션 쿠키로 body·query·회원 ID 없이 POST하고 응답을 검증한다',
	async (status) => {
		const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ data: { status } }));
		const client = createApiClient({ fetch });
		client.setAuthHandler({ handleSessionExpired: () => {} });
		const signal = new AbortController().signal;
		expect(await syncHealth(signal, client)).toBe(status);
		expect(fetch).toHaveBeenCalledExactlyOnceWith(
			'/api/v1/health/sync',
			expect.objectContaining({ method: 'POST', signal, body: undefined, credentials: 'include' })
		);
		expect(new Headers(fetch.mock.calls[0][1]?.headers).get('Authorization')).toBeNull();
	}
);
it.each([null, {}, { status: 'DONE' }, { status: null }, []])(
	'잘못된 정상 응답 %j를 거절한다',
	async (data) => {
		const client = createApiClient({ fetch: async () => Response.json({ data }) });
		await expect(syncHealth(new AbortController().signal, client)).rejects.toBeInstanceOf(
			ResponseFormatError
		);
	}
);
it.each([
	[409, 'CANDIY_USER_NOT_LINKED', null, 'NOT_LINKED'],
	[503, 'HEALTH_SYNC_UNAVAILABLE', { reason: 'TIMEOUT', metrics: [] }, 'TIMEOUT'],
	[503, 'HEALTH_SYNC_UNAVAILABLE', { reason: 'CANDIY_AUTH_FAILED', metrics: [] }, 'UNAVAILABLE'],
	[503, 'HEALTH_SYNC_UNAVAILABLE', { reason: 'METRIC_FAILED', metrics: ['steps'] }, 'UNAVAILABLE'],
	[401, 'AUTH_REQUIRED', null, 'AUTH']
])('공개 오류 %s %s를 %s로 분류한다', async (status, code, context, outcome) => {
	const client = createApiClient({
		fetch: async () =>
			Response.json({ error: { code, context, message: 'synthetic' } }, { status: Number(status) })
	});
	try {
		await syncHealth(new AbortController().signal, client);
		throw new Error('성공하면 안 됨');
	} catch (error) {
		expect(syncOutcome(error)).toBe(outcome);
	}
});
it('네트워크·응답 형식 오류를 서버 오류와 구분한다', () => {
	expect(syncOutcome(new TypeError('synthetic'))).toBe('NETWORK');
	expect(syncOutcome(new ResponseFormatError('sync'))).toBe('INVALID');
});
it('AUTH_EXPIRED는 POST를 다시 보내지 않고 로그인 상태 정리를 알린 뒤 오류를 던진다', async () => {
	const fetch = vi
		.fn<typeof globalThis.fetch>()
		.mockResolvedValueOnce(Response.json({ error: { code: 'AUTH_EXPIRED' } }, { status: 401 }));
	const handleSessionExpired = vi.fn();
	const client = createApiClient({ fetch });
	client.setAuthHandler({ handleSessionExpired });
	await expect(syncHealth(new AbortController().signal, client)).rejects.toMatchObject({
		code: 'AUTH_EXPIRED'
	});
	expect(handleSessionExpired).toHaveBeenCalledTimes(1);
	expect(fetch).toHaveBeenCalledTimes(1);
});
it('같은 계정의 현재 요청은 공유하고 다른 계정과 완료 결과는 공유하지 않는다', async () => {
	const resolvers: ((value: unknown) => void)[] = [];
	const request = vi.fn<ApiClient['request']>(
		() => new Promise((resolve) => resolvers.push(resolve))
	);
	const run = createHealthSyncRequester({ request, setAuthHandler: () => {} });
	const a = run('synthetic-a', new AbortController().signal),
		b = run('synthetic-a', new AbortController().signal),
		c = run('synthetic-b', new AbortController().signal);
	expect(request).toHaveBeenCalledTimes(2);
	resolvers.forEach((resolve) => resolve({ status: 'SYNCED' }));
	expect(await Promise.all([a, b, c])).toEqual(['SYNCED', 'SYNCED', 'SYNCED']);
	const next = run('synthetic-a', new AbortController().signal);
	resolvers[2]({ status: 'SKIPPED_RECENT' });
	expect(await next).toBe('SKIPPED_RECENT');
	expect(request).toHaveBeenCalledTimes(3);
});
it('구독자 한 명의 취소는 다른 구독자를 취소하지 않고 마지막 이탈 시 타이머를 정리한다', async () => {
	vi.useFakeTimers();
	let signal: AbortSignal | undefined;
	const run = createHealthSyncRequester({
		request: (_path, options) => {
			signal = options?.signal;
			return new Promise(() => {});
		},
		setAuthHandler: () => {}
	});
	const first = new AbortController(),
		second = new AbortController();
	const a = run('synthetic', first.signal),
		b = run('synthetic', second.signal);
	const checkA = expect(a).rejects.toMatchObject({ name: 'AbortError' });
	first.abort();
	await checkA;
	expect(signal?.aborted).toBe(false);
	const checkB = expect(b).rejects.toMatchObject({ name: 'AbortError' });
	second.abort();
	await checkB;
	await vi.runAllTimersAsync();
	expect(signal?.aborted).toBe(true);
	expect(vi.getTimerCount()).toBe(0);
});
it('응답이 없어도 12초 안에 대기를 끝내고 요청을 취소한다', async () => {
	vi.useFakeTimers();
	const run = createHealthSyncRequester({
		request: () => new Promise(() => {}),
		setAuthHandler: () => {}
	});
	const result = run('synthetic', new AbortController().signal).catch(syncOutcome);
	await vi.advanceTimersByTimeAsync(12000);
	expect(await result).toBe('TIMEOUT');
	expect(vi.getTimerCount()).toBe(0);
});

it('마지막 이탈 직후 같은 계정의 새 진입은 취소된 요청을 재사용하지 않는다', async () => {
	const request = vi
		.fn<ApiClient['request']>()
		.mockImplementationOnce(() => new Promise(() => {}))
		.mockResolvedValueOnce({ status: 'SYNCED' });
	const run = createHealthSyncRequester({ request, setAuthHandler: () => {} });
	const controller = new AbortController();
	const first = run('synthetic', controller.signal);
	const cancelled = expect(first).rejects.toMatchObject({ name: 'AbortError' });
	controller.abort();
	const second = run('synthetic', new AbortController().signal);
	expect(await second).toBe('SYNCED');
	await cancelled;
	expect(request).toHaveBeenCalledTimes(2);
});

it('GET 무응답은 15초에 종료하고 원래 화면 signal은 살려 기존 오류 처리를 허용한다', async () => {
	vi.useFakeTimers();
	const controller = new AbortController();
	let child: AbortSignal | undefined;
	const result = readHealthData((signal) => {
		child = signal;
		return new Promise(() => {});
	}, controller.signal);
	const failed = expect(result).rejects.toThrow('건강 기록 조회 시간이 초과');
	await vi.advanceTimersByTimeAsync(15000);
	await failed;
	expect(child?.aborted).toBe(true);
	expect(controller.signal.aborted).toBe(false);
	expect(vi.getTimerCount()).toBe(0);
});

it('GET 완료·화면 이탈은 대기 타이머와 구독을 정리한다', async () => {
	vi.useFakeTimers();
	const controller = new AbortController();
	expect(await readHealthData(async () => 42, controller.signal)).toBe(42);
	expect(vi.getTimerCount()).toBe(0);
	const result = readHealthData(() => new Promise(() => {}), controller.signal);
	const failed = expect(result).rejects.toMatchObject({ name: 'AbortError' });
	controller.abort();
	await failed;
	expect(vi.getTimerCount()).toBe(0);
});
