import { ApiError } from '#lib/api/errors.js';
import { getAppConnection, type AppConnection } from './app-connection-api';
import { readHealthData } from './sync-api';

const retryDelayMs = 500;

function waitForRetry(signal: AbortSignal): Promise<void> {
	return new Promise((resolve, reject) => {
		if (signal.aborted) {
			reject(signal.reason);
			return;
		}
		const abort = () => {
			clearTimeout(timer);
			signal.removeEventListener('abort', abort);
			reject(signal.reason);
		};
		const timer = setTimeout(() => {
			signal.removeEventListener('abort', abort);
			resolve();
		}, retryDelayMs);
		signal.addEventListener('abort', abort, { once: true });
	});
}

/** 한 로그인 계정의 메모리 상태. 건강 기록의 준비 여부와는 독립적이다. */
export class AppConnectionState {
	value = $state<AppConnection | null>(null);
	error = $state<Error | null>(null);
	checking = $state(false);
	retrying = $state(false);
	#unknownNoticeDismissed = $state(false);
	#controller: AbortController | undefined;
	#pending: Promise<void> | undefined;
	#sequence = 0;
	#disposed = false;

	constructor(
		readonly enabled: boolean,
		private readonly load: (signal: AbortSignal) => Promise<AppConnection> = getAppConnection,
		private readonly onUnauthorized: () => void = () => {}
	) {}

	get blocked(): boolean {
		return this.enabled && (!this.value || this.value.status === 'NOT_CONNECTED');
	}

	get showUnknownNotice(): boolean {
		return this.value?.status === 'UNKNOWN' && !this.#unknownNoticeDismissed;
	}

	dismissUnknownNotice(): void {
		if (this.value?.status === 'UNKNOWN') this.#unknownNoticeDismissed = true;
	}

	async #loadWithRetry(signal: AbortSignal): Promise<AppConnection> {
		try {
			return await this.load(signal);
		} catch (error) {
			if (
				signal.aborted ||
				!((error instanceof ApiError && error.status === 503) || error instanceof TypeError)
			)
				throw error;
			this.retrying = true;
			await waitForRetry(signal);
			signal.throwIfAborted();
			return this.load(signal);
		}
	}

	/** 예약된 탭 복귀 확인보다 새 요청이 시작됐는지 비교한다. */
	get requestVersion(): number {
		return this.#sequence;
	}

	/** 진행 중 요청만 합친다. 재확인 중에는 마지막 정상 응답과 화면을 유지한다. */
	refresh(): Promise<void> {
		if (!this.enabled || this.#disposed) return Promise.resolve();
		if (this.#pending) return this.#pending;
		const sequence = ++this.#sequence;
		const controller = new AbortController();
		this.#controller = controller;
		this.checking = true;
		this.retrying = false;
		this.error = null;
		const isCurrent = () =>
			!this.#disposed && !controller.signal.aborted && sequence === this.#sequence;
		// 첫 시도와 1회 재시도·대기를 모두 기존 15초 조회 예산 안에 둔다.
		const pending = readHealthData((signal) => this.#loadWithRetry(signal), controller.signal)
			.then((value) => {
				if (isCurrent()) this.value = value;
			})
			.catch((error: unknown) => {
				if (!isCurrent()) return;
				this.error =
					error instanceof Error ? error : new Error('앱 연동 상태를 확인하지 못했어요.');
				if (
					error instanceof ApiError &&
					error.status === 401 &&
					(error.code === 'AUTH_REQUIRED' || error.code === 'AUTH_EXPIRED')
				)
					this.onUnauthorized();
			})
			.finally(() => {
				if (isCurrent()) {
					this.checking = false;
					this.retrying = false;
					this.#pending = undefined;
				}
			});
		this.#pending = pending;
		return pending;
	}

	/** 기존 수동 동기화도 최신 연결 판별을 통과한 뒤에만 POST한다. */
	async recheck(signal: AbortSignal): Promise<boolean> {
		if (signal.aborted || this.#disposed) return false;
		await this.refresh();
		return !signal.aborted && !this.#disposed && !this.blocked && this.error === null;
	}

	dispose(): void {
		this.#disposed = true;
		this.#sequence++;
		this.#controller?.abort();
		this.#pending = undefined;
		this.value = null;
		this.error = null;
		this.checking = false;
		this.retrying = false;
		this.#unknownNoticeDismissed = false;
	}
}
