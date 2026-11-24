import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, ResponseFormatError } from '#lib/api/errors.js';
import { AppConnectionState } from './app-connection.svelte';
import type { AppConnection } from './app-connection-api';

const connected: AppConnection = { status: 'CONNECTED', connectedAt: '2026-11-11T07:00:00Z' };
const unknown: AppConnection = { status: 'UNKNOWN', connectedAt: null };
const notConnected: AppConnection = { status: 'NOT_CONNECTED', connectedAt: null };
const states: AppConnectionState[] = [];

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (error: unknown) => void;
	const promise = new Promise<T>((yes, no) => {
		resolve = yes;
		reject = no;
	});
	return { promise, resolve, reject };
}

function state(
	enabled: boolean,
	load: (signal: AbortSignal) => Promise<AppConnection>,
	onUnauthorized = vi.fn()
) {
	const result = new AppConnectionState(enabled, load, onUnauthorized);
	states.push(result);
	return result;
}

function apiError(status: number, code: string) {
	return new ApiError({
		status,
		code,
		message: '합성 응답 오류',
		details: [],
		retryable: null,
		context: undefined,
		requestId: null
	});
}

afterEach(() => {
	for (const value of states.splice(0)) value.dispose();
	vi.useRealTimers();
});

it('비활성 상태는 조회하지 않고 기존 흐름을 허용한다', async () => {
	const load = vi.fn(async () => connected);
	const value = state(false, load);
	await value.refresh();
	expect(await value.recheck(new AbortController().signal)).toBe(true);
	expect(load).not.toHaveBeenCalled();
	expect(value.blocked).toBe(false);
	expect(value.value).toBeNull();
});

it.each([connected, unknown, notConnected])(
	'최초 $status 응답을 보존하고 NOT_CONNECTED만 차단한다',
	async (connection) => {
		const value = state(true, async () => connection);
		expect(value.blocked).toBe(true);
		await value.refresh();
		expect(value.value).toEqual(connection);
		expect(value.blocked).toBe(connection.status === 'NOT_CONNECTED');
		expect(value.checking).toBe(false);
	}
);

it('진행 중 조회는 하나로 합치고 완료 후 수동 재조회는 새 요청을 보낸다', async () => {
	const held = deferred<AppConnection>();
	const load = vi.fn().mockReturnValueOnce(held.promise).mockResolvedValueOnce(unknown);
	const value = state(true, load);
	const first = value.refresh();
	expect(value.refresh()).toBe(first);
	await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1));
	expect(value.checking).toBe(true);
	held.resolve(connected);
	await first;
	await value.refresh();
	expect(load).toHaveBeenCalledTimes(2);
	expect(value.value).toEqual(unknown);
});

it('재조회 중에는 마지막 정상 값을 유지하고 오류를 UNKNOWN으로 바꾸지 않는다', async () => {
	const held = deferred<AppConnection>();
	const load = vi.fn().mockResolvedValueOnce(connected).mockReturnValueOnce(held.promise);
	const value = state(true, load);
	await value.refresh();
	const pending = value.refresh();
	expect(value.value).toEqual(connected);
	expect(value.blocked).toBe(false);
	const failure = new ResponseFormatError('appConnection');
	held.reject(failure);
	await pending;
	expect(value.value).toEqual(connected);
	expect(value.error).toBe(failure);
	expect(value.checking).toBe(false);
});

it.each([
	{ status: 401, code: 'AUTH_REQUIRED', unauthorized: true },
	{ status: 401, code: 'AUTH_EXPIRED', unauthorized: true },
	{ status: 401, code: 'AUTH_UNAVAILABLE', unauthorized: false },
	{ status: 503, code: 'HEALTH_DATA_UNAVAILABLE', unauthorized: false },
	{ status: 403, code: 'AUTH_REQUIRED', unauthorized: false },
	{ status: 409, code: 'CANDIY_USER_NOT_LINKED', unauthorized: false },
	{ status: 422, code: 'INVALID_REQUEST', unauthorized: false },
	{ status: 500, code: 'INTERNAL_ERROR', unauthorized: false }
])('$status $code만 계약에 맞게 인증 실패로 분류한다', async ({ status, code, unauthorized }) => {
	vi.useFakeTimers();
	const failure = apiError(status, code);
	const onUnauthorized = vi.fn();
	const load = vi.fn(async () => {
		throw failure;
	});
	const value = state(true, load, onUnauthorized);
	const pending = value.refresh();
	await vi.advanceTimersByTimeAsync(500);
	await pending;
	expect(value.error).toBe(failure);
	expect(value.value).toBeNull();
	expect(load).toHaveBeenCalledTimes(status === 503 ? 2 : 1);
	expect(onUnauthorized).toHaveBeenCalledTimes(unauthorized ? 1 : 0);
});

it('15초 응답 제한은 로더를 취소하고 재시도 가능한 오류로 끝난다', async () => {
	vi.useFakeTimers();
	let signal: AbortSignal | undefined;
	const onUnauthorized = vi.fn();
	const value = state(
		true,
		(next) => {
			signal = next;
			return new Promise<AppConnection>(() => {});
		},
		onUnauthorized
	);
	const pending = value.refresh();
	await vi.advanceTimersByTimeAsync(15000);
	await pending;
	expect(signal?.aborted).toBe(true);
	expect(value.error?.message).toContain('초과');
	expect(value.checking).toBe(false);
	expect(value.blocked).toBe(true);
	expect(onUnauthorized).not.toHaveBeenCalled();
});

it.each(['success', '401'] as const)(
	'폐기 후 늦은 %s는 값·오류·인증에 영향을 주지 않는다',
	async (late) => {
		const held = deferred<AppConnection>();
		let signal: AbortSignal | undefined;
		const onUnauthorized = vi.fn();
		const value = state(
			true,
			(next) => {
				signal = next;
				return held.promise;
			},
			onUnauthorized
		);
		const pending = value.refresh();
		await vi.waitFor(() => expect(signal).toBeDefined());
		value.dispose();
		expect(signal?.aborted).toBe(true);
		if (late === 'success') held.resolve(connected);
		else held.reject(apiError(401, 'AUTH_EXPIRED'));
		await pending;
		expect(value.value).toBeNull();
		expect(value.error).toBeNull();
		expect(value.checking).toBe(false);
		expect(onUnauthorized).not.toHaveBeenCalled();
		await value.refresh();
		expect(value.value).toBeNull();
	}
);

it.each([connected, unknown, notConnected])(
	'수동 동기화 전 재확인은 $status 판정에 맞게 허용한다',
	async (connection) => {
		const value = state(true, async () => connection);
		expect(await value.recheck(new AbortController().signal)).toBe(
			connection.status !== 'NOT_CONNECTED'
		);
	}
);

it('기존 CONNECTED가 있어도 재확인 오류가 나면 새 POST를 허용하지 않는다', async () => {
	vi.useFakeTimers();
	const failure = new TypeError('offline');
	const load = vi.fn().mockResolvedValueOnce(connected).mockRejectedValue(failure);
	const value = state(true, load);
	await value.refresh();
	const pending = value.recheck(new AbortController().signal);
	await vi.advanceTimersByTimeAsync(500);
	expect(await pending).toBe(false);
	expect(value.value).toEqual(connected);
	expect(value.error).toBe(failure);
	expect(load).toHaveBeenCalledTimes(3);
});

it('취소된 소비자는 재확인 결과로 진행하지 않는다', async () => {
	const held = deferred<AppConnection>();
	const load = vi.fn(() => held.promise);
	const value = state(true, load);
	const controller = new AbortController();
	const pending = value.recheck(controller.signal);
	controller.abort();
	held.resolve(connected);
	expect(await pending).toBe(false);
	expect(await value.recheck(controller.signal)).toBe(false);
	expect(load).toHaveBeenCalledTimes(1);
});

it.each([
	{ name: '503', failure: apiError(503, 'HEALTH_DATA_UNAVAILABLE') },
	{ name: 'network', failure: new TypeError('Failed to fetch') }
])('$name 뒤 성공은 500ms 후 한 번만 재시도하고 전체 조회를 공유한다', async ({ failure }) => {
	vi.useFakeTimers();
	const held = deferred<AppConnection>();
	const load = vi.fn().mockRejectedValueOnce(failure).mockReturnValueOnce(held.promise);
	const value = state(true, load);
	const pending = value.refresh();
	const requestVersion = value.requestVersion;
	await vi.advanceTimersByTimeAsync(0);
	expect(load).toHaveBeenCalledTimes(1);
	expect(value.checking).toBe(true);
	expect(value.retrying).toBe(true);
	expect(value.error).toBeNull();
	expect(value.refresh()).toBe(pending);
	await vi.advanceTimersByTimeAsync(499);
	expect(load).toHaveBeenCalledTimes(1);
	expect(value.refresh()).toBe(pending);
	await vi.advanceTimersByTimeAsync(1);
	expect(load).toHaveBeenCalledTimes(2);
	expect(load.mock.calls[1][0]).toBe(load.mock.calls[0][0]);
	expect(value.requestVersion).toBe(requestVersion);
	expect(value.refresh()).toBe(pending);
	held.resolve(connected);
	await pending;
	expect(value.value).toEqual(connected);
	expect(value.error).toBeNull();
	expect(value.checking).toBe(false);
	expect(value.retrying).toBe(false);
	expect(vi.getTimerCount()).toBe(0);
});

it.each([
	{ name: '503', failure: apiError(503, 'HEALTH_DATA_UNAVAILABLE') },
	{ name: 'network', failure: new TypeError('offline') }
])(
	'$name 지속 실패도 총 두 요청에서 멈추고 수동 확인은 새 예산으로 시작한다',
	async ({ failure }) => {
		vi.useFakeTimers();
		const load = vi.fn().mockRejectedValue(failure);
		const value = state(true, load);
		const pending = value.refresh();
		await vi.advanceTimersByTimeAsync(500);
		await pending;
		expect(load).toHaveBeenCalledTimes(2);
		expect(value.error).toBe(failure);
		expect(value.value).toBeNull();
		expect(value.retrying).toBe(false);
		expect(value.checking).toBe(false);
		await vi.advanceTimersByTimeAsync(60000);
		expect(load).toHaveBeenCalledTimes(2);
		const next = value.refresh();
		await vi.advanceTimersByTimeAsync(500);
		await next;
		expect(load).toHaveBeenCalledTimes(4);
	}
);

it.each([
	new ResponseFormatError('appConnection'),
	new DOMException('취소됨', 'AbortError'),
	new Error('로더 내부 오류')
])('$name는 네트워크 오류로 넓혀 해석하거나 재시도하지 않는다', async (failure) => {
	vi.useFakeTimers();
	const load = vi.fn().mockRejectedValue(failure);
	const value = state(true, load);
	await value.refresh();
	await vi.advanceTimersByTimeAsync(15000);
	expect(load).toHaveBeenCalledTimes(1);
	expect(value.error).toBe(failure);
	expect(value.retrying).toBe(false);
	expect(value.checking).toBe(false);
});

it.each([connected, unknown, notConnected])(
	'$status 캐시는 자동 재시도 중과 최종 실패 후에도 그대로 유지한다',
	async (previous) => {
		vi.useFakeTimers();
		const failure = apiError(503, 'HEALTH_DATA_UNAVAILABLE');
		const load = vi.fn().mockResolvedValueOnce(previous).mockRejectedValue(failure);
		const value = state(true, load);
		await value.refresh();
		const pending = value.refresh();
		await vi.advanceTimersByTimeAsync(0);
		expect(value.retrying).toBe(true);
		expect(value.checking).toBe(true);
		expect(value.value).toEqual(previous);
		expect(value.error).toBeNull();
		await vi.advanceTimersByTimeAsync(500);
		await pending;
		expect(load).toHaveBeenCalledTimes(3);
		expect(value.value).toEqual(previous);
		expect(value.error).toBe(failure);
		expect(value.blocked).toBe(previous.status === 'NOT_CONNECTED');
	}
);

it('재시도 대기와 두 요청을 합친 15초 한도는 두 번째 요청 때문에 늘어나지 않는다', async () => {
	vi.useFakeTimers();
	const firstRequest = deferred<AppConnection>();
	const secondRequest = deferred<AppConnection>();
	const load = vi
		.fn()
		.mockReturnValueOnce(firstRequest.promise)
		.mockReturnValueOnce(secondRequest.promise);
	const value = state(true, load);
	const pending = value.refresh();
	await vi.advanceTimersByTimeAsync(14000);
	firstRequest.reject(apiError(503, 'HEALTH_DATA_UNAVAILABLE'));
	await vi.advanceTimersByTimeAsync(0);
	await vi.advanceTimersByTimeAsync(500);
	expect(load).toHaveBeenCalledTimes(2);
	await vi.advanceTimersByTimeAsync(499);
	expect(value.checking).toBe(true);
	await vi.advanceTimersByTimeAsync(1);
	await pending;
	expect(value.error?.message).toContain('초과');
	expect(value.checking).toBe(false);
	expect(value.retrying).toBe(false);
	expect(load.mock.calls[0][0].aborted).toBe(true);
	expect(load.mock.calls[1][0].aborted).toBe(true);
	expect(vi.getTimerCount()).toBe(0);
});

it('500ms 재시도 사이에 폐기하면 예약·조회·인증 부작용을 모두 정리한다', async () => {
	vi.useFakeTimers();
	const onUnauthorized = vi.fn();
	const load = vi.fn().mockRejectedValue(apiError(503, 'HEALTH_DATA_UNAVAILABLE'));
	const value = state(true, load, onUnauthorized);
	const pending = value.refresh();
	await vi.advanceTimersByTimeAsync(0);
	expect(value.retrying).toBe(true);
	value.dispose();
	await pending;
	await vi.advanceTimersByTimeAsync(15000);
	expect(load).toHaveBeenCalledTimes(1);
	expect(load.mock.calls[0][0].aborted).toBe(true);
	expect(value.value).toBeNull();
	expect(value.error).toBeNull();
	expect(value.retrying).toBe(false);
	expect(value.checking).toBe(false);
	expect(onUnauthorized).not.toHaveBeenCalled();
	expect(vi.getTimerCount()).toBe(0);
});

it('두 번째 요청의 늦은 401도 폐기된 세션의 인증을 정리하지 않는다', async () => {
	vi.useFakeTimers();
	const held = deferred<AppConnection>();
	const load = vi
		.fn()
		.mockRejectedValueOnce(new TypeError('offline'))
		.mockReturnValueOnce(held.promise);
	const onUnauthorized = vi.fn();
	const value = state(true, load, onUnauthorized);
	const pending = value.refresh();
	await vi.advanceTimersByTimeAsync(500);
	expect(load).toHaveBeenCalledTimes(2);
	value.dispose();
	held.reject(apiError(401, 'AUTH_EXPIRED'));
	await pending;
	expect(onUnauthorized).not.toHaveBeenCalled();
	expect(value.value).toBeNull();
	expect(value.error).toBeNull();
});

it('UNKNOWN 안내 닫기는 같은 세션의 재조회·상태 전환에도 유지하고 새 세션과 공유하지 않는다', async () => {
	const load = vi
		.fn()
		.mockResolvedValueOnce(unknown)
		.mockResolvedValueOnce(unknown)
		.mockResolvedValueOnce(connected)
		.mockResolvedValueOnce(unknown);
	const value = state(true, load);
	await value.refresh();
	expect(value.showUnknownNotice).toBe(true);
	value.dismissUnknownNotice();
	expect(value.showUnknownNotice).toBe(false);
	await value.refresh();
	expect(value.value).toEqual(unknown);
	expect(value.showUnknownNotice).toBe(false);
	await value.refresh();
	await value.refresh();
	expect(value.value).toEqual(unknown);
	expect(value.showUnknownNotice).toBe(false);
	value.dispose();
	expect(value.showUnknownNotice).toBe(false);
	const nextSession = state(true, async () => unknown);
	await nextSession.refresh();
	expect(nextSession.showUnknownNotice).toBe(true);
});

it.each([connected, notConnected])(
	'$status에서는 UNKNOWN 안내를 미리 닫을 수 없다',
	async (connection) => {
		const load = vi.fn().mockResolvedValueOnce(connection).mockResolvedValueOnce(unknown);
		const value = state(true, load);
		value.dismissUnknownNotice();
		await value.refresh();
		value.dismissUnknownNotice();
		await value.refresh();
		expect(value.value).toEqual(unknown);
		expect(value.showUnknownNotice).toBe(true);
	}
);
