import { api, type ApiClient } from '#lib/api/client.js';
import { ApiError, isRecord, ResponseFormatError } from '#lib/api/errors.js';
import { readEnum, readRecord } from '#lib/api/parse.js';

export const syncStatuses = ['SYNCED', 'SKIPPED_RECENT', 'BUSY', 'SYNTHETIC', 'DISABLED'] as const;
export type HealthSyncStatus = (typeof syncStatuses)[number];
export type HealthSyncOutcome =
	HealthSyncStatus | 'TIMEOUT' | 'NOT_LINKED' | 'UNAVAILABLE' | 'NETWORK' | 'INVALID' | 'AUTH';

export async function syncHealth(
	signal: AbortSignal,
	client: ApiClient = api
): Promise<HealthSyncStatus> {
	return readEnum(
		readRecord(await client.request('/health/sync', { method: 'POST', signal }), 'sync'),
		'status',
		syncStatuses
	);
}

class SyncWaitExpired extends Error {}

export function syncOutcome(error: unknown): HealthSyncOutcome {
	if (error instanceof SyncWaitExpired) return 'TIMEOUT';
	if (error instanceof ApiError) {
		if (error.status === 401) return 'AUTH';
		if (error.status === 409 && error.code === 'CANDIY_USER_NOT_LINKED') return 'NOT_LINKED';
		if (
			error.status === 503 &&
			error.code === 'HEALTH_SYNC_UNAVAILABLE' &&
			isRecord(error.context) &&
			error.context.reason === 'TIMEOUT'
		)
			return 'TIMEOUT';
		return 'UNAVAILABLE';
	}
	return error instanceof ResponseFormatError ? 'INVALID' : 'NETWORK';
}

/** 현재 실행만 같은 계정끼리 공유한다. 완료 결과를 캐시하거나 계정 사이에 공유하지 않는다. */
export function createHealthSyncRequester(client: ApiClient = api) {
	const pending = new Map<
		string,
		{ controller: AbortController; promise: Promise<HealthSyncStatus>; readers: number }
	>();
	return function request(userId: string, signal: AbortSignal): Promise<HealthSyncStatus> {
		if (signal.aborted) return Promise.reject(signal.reason);
		let entry = pending.get(userId);
		if (!entry) {
			const controller = new AbortController();
			let timer: ReturnType<typeof setTimeout>;
			let abort = () => {};
			const cancelled = new Promise<never>((_, reject) => {
				abort = () => reject(controller.signal.reason);
				controller.signal.addEventListener('abort', abort, { once: true });
			});
			const timeout = new Promise<never>((_, reject) => {
				timer = setTimeout(() => {
					reject(new SyncWaitExpired());
					controller.abort();
				}, 12000); // 백엔드의 10초 응답 한도 + 전송 여유. 서버 작업 중단을 뜻하지 않는다.
			});
			const promise = Promise.race([
				syncHealth(controller.signal, client),
				timeout,
				cancelled
			]).finally(() => {
				clearTimeout(timer);
				controller.signal.removeEventListener('abort', abort);
				if (pending.get(userId)?.promise === promise) pending.delete(userId);
			});
			entry = { controller, promise, readers: 0 };
			pending.set(userId, entry);
		}
		const current = entry;
		current.readers++;
		return new Promise<HealthSyncStatus>((resolve, reject) => {
			let finished = false;
			const finish = () => {
				if (finished) return;
				finished = true;
				signal.removeEventListener('abort', abort);
				if (--current.readers === 0) {
					if (pending.get(userId) === current) pending.delete(userId);
					current.controller.abort();
				}
			};
			const abort = () => {
				finish();
				reject(signal.reason);
			};
			signal.addEventListener('abort', abort, { once: true });
			current.promise.then(
				(value) => {
					finish();
					resolve(value);
				},
				(error) => {
					finish();
					reject(error);
				}
			);
			if (signal.aborted) abort();
		});
	};
}

export const requestHealthSync = createHealthSyncRequester();

/** 원래 화면 signal은 유지해 시간 초과를 기존 GET 오류 UI로 처리한다. */
export function readHealthData<T>(
	request: (signal: AbortSignal) => Promise<T>,
	signal: AbortSignal
): Promise<T> {
	if (signal.aborted) return Promise.reject(signal.reason);
	const controller = new AbortController();
	let timer: ReturnType<typeof setTimeout>;
	let abort = () => {};
	const boundary = new Promise<never>((_, reject) => {
		abort = () => {
			reject(signal.reason);
			controller.abort();
		};
		signal.addEventListener('abort', abort, { once: true });
		timer = setTimeout(() => {
			reject(new Error('건강 기록 조회 시간이 초과됐어요. 다시 시도해 주세요.'));
			controller.abort();
		}, 15000);
	});
	return Promise.race([
		Promise.resolve().then(() => {
			if (controller.signal.aborted) throw controller.signal.reason;
			return request(controller.signal);
		}),
		boundary
	]).finally(() => {
		clearTimeout(timer);
		signal.removeEventListener('abort', abort);
	});
}

/** 복합 loader의 부분 성공을 보존하도록 각 HTTP GET에만 제한 시간을 적용한다. */
export function createHealthReadClient(client: ApiClient = api): ApiClient {
	return {
		...client,
		request(path, options = {}) {
			if (options.method && options.method !== 'GET') return client.request(path, options);
			return readHealthData(
				(signal) => client.request(path, { ...options, signal }),
				options.signal ?? new AbortController().signal
			);
		}
	};
}

export const healthReadClient = createHealthReadClient();
