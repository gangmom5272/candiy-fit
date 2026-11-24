import { parseServerMessage } from './parse';
import type { CONNECTION_ERROR_CODES, CoachingRequestEvent, QuestionMessage } from './types';

type ConnectionErrorCode = (typeof CONNECTION_ERROR_CODES)[number];

const SOCKET_PATH = '/api/v1/coaching/ws';
/** 서버는 핸드셰이크의 세션 쿠키를 확인한 뒤 바로 ready를 보낸다. 이보다 길게 기다리지 않는다 */
const DEFAULT_READY_TIMEOUT_MS = 10_000;

export type SocketErrorCode =
	'AUTH_REQUIRED' | 'AUTH_EXPIRED' | 'AUTH_UNAVAILABLE' | 'CONNECTION_FAILED' | 'READY_TIMEOUT';

export class CoachingSocketError extends Error {
	readonly code: SocketErrorCode;

	constructor(code: SocketErrorCode, message: string) {
		super(message);
		this.name = 'CoachingSocketError';
		this.code = code;
	}
}

/** 세션 자격이 무효라 다시 로그인해야 하는 코드. 일시 장애(AUTH_UNAVAILABLE)는 들어가지 않는다 */
function isSessionRejected(code: string): boolean {
	// 웹에는 AUTH_EXPIRED를 보내지 않지만(명세 7.2), 오더라도 갱신이 없으므로 재로그인으로 처리한다
	return code === 'AUTH_REQUIRED' || code === 'AUTH_EXPIRED';
}

export type SocketCloseInfo = {
	/** ready 이후에 끊겼는지. 질문을 보낸 뒤 끊긴 경우 요청 복구가 필요하다 */
	wasReady: boolean;
};

/** 테스트에서 가짜 WebSocket을 넣을 수 있도록 필요한 부분만 정의한다 */
export type SocketLike = {
	readyState: number;
	send(data: string): void;
	close(code?: number): void;
	onopen: ((event: Event) => void) | null;
	onmessage: ((event: MessageEvent) => void) | null;
	onclose: ((event: CloseEvent) => void) | null;
	onerror: ((event: Event) => void) | null;
};

export type CoachingSocketOptions = {
	url: string;
	onEvent: (event: CoachingRequestEvent) => void;
	onClose?: (info: SocketCloseInfo) => void;
	/**
	 * 서버가 세션 자격을 무효로 거절했을 때 부른다. 로그인 상태를 정리하는 데 쓴다.
	 * 연결 시점의 connection_error(이후 서버가 close 1008)와 질문 시점의 요청 단위 error(AUTH_REQUIRED) 둘 다 해당한다.
	 * 일시 장애(AUTH_UNAVAILABLE)에는 부르지 않는다. 로그인 상태를 지우지 않고 사용자가 다시 시도한다.
	 */
	onAuthRequired?: () => void;
	/**
	 * ready 이후에 connection_error를 받았을 때 부른다. 명세 v47에서 질문 시점 인증 실패는 요청 단위 error로
	 * 오므로 정상 계약에는 없는 경우이고, 서버가 예전 방식으로 알릴 때를 위한 호환 처리다.
	 * 서버가 그 질문을 접수하지 않은 것으로 보고, 보낸 채 접수를 기다리던 질문을 정리하는 데 쓴다.
	 */
	onQuestionRejected?: (code: ConnectionErrorCode) => void;
	createSocket?: (url: string) => SocketLike;
	readyTimeoutMs?: number;
};

/** 현재 페이지 origin 기준 WebSocket 주소. https면 wss를 쓴다 */
export function defaultSocketUrl(location: Pick<Location, 'protocol' | 'host'>): string {
	return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${SOCKET_PATH}`;
}

/**
 * 명세 7장 코칭 WebSocket 연결. 웹은 auth 프레임을 보내지 않는다. 브라우저가 핸드셰이크에 실은
 * 세션 쿠키를 서버가 확인하고 ready를 보내면 질문을 보낸다(명세 7.2). 웹 세션은 갱신이 없다.
 * 질문을 보내는 연결은 하나만 유지하고, 끊기면 다음 질문 때 다시 연결한다. 끊김을 요청 취소로 보지 않으며
 * 복구는 onClose를 받은 쪽(CoachingSession)이 REST로 처리한다.
 * 인증 실패는 연결 시점에는 connection_error(이후 서버가 close 1008·1011), 질문 시점에는 요청 단위 error로 온다
 * (명세 v47 7.2). 질문 시점 실패에 서버는 소켓을 닫지 않으므로 같은 연결을 계속 쓴다.
 * 호환 처리: ready 이후에 connection_error가 오면 그 연결은 닫지 않고 받기만 하도록 남겨 두고(#draining),
 * 다음 질문은 새 연결로 보낸다. 진행 중인 답변을 끊지 않기 위해서다.
 */
export class CoachingSocket {
	#options: CoachingSocketOptions;
	#socket: SocketLike | null = null;
	#ready: Promise<void> | null = null;
	#isReady = false;
	#isClosedByClient = false;
	/** 새 질문은 보내지 않고 진행 중인 답변만 받는 연결. 서버가 닫을 때까지 둔다 */
	#draining = new Set<SocketLike>();

	constructor(options: CoachingSocketOptions) {
		this.#options = options;
	}

	get isReady(): boolean {
		return this.#isReady;
	}

	/** ready까지 기다린다. 서버가 거절하면 CoachingSocketError로 끝나고, 다음 호출 때 새로 연결한다. */
	ensureReady(): Promise<void> {
		return this.#connect();
	}

	async send(question: QuestionMessage): Promise<void> {
		await this.ensureReady();
		if (!this.#socket) throw new CoachingSocketError('CONNECTION_FAILED', '연결이 없습니다.');
		this.#socket.send(JSON.stringify(question));
	}

	/** 화면을 떠날 때 연결과 이벤트 연결을 정리한다. onClose는 부르지 않는다. */
	close(): void {
		this.#isClosedByClient = true;
		this.#teardown();
		for (const socket of this.#draining) closeSocket(socket);
		this.#draining.clear();
	}

	#connect(): Promise<void> {
		if (this.#ready) return this.#ready;
		this.#isClosedByClient = false;

		const createSocket = this.#options.createSocket ?? ((url: string) => new WebSocket(url));
		const socket = createSocket(this.#options.url);
		this.#socket = socket;

		// 이 연결이 ready를 받았는지. 받기만 하는 연결로 남은 뒤에도 끊김 알림에 쓴다
		let wasReady = false;
		this.#ready = new Promise<void>((resolve, reject) => {
			const timer = setTimeout(() => {
				reject(new CoachingSocketError('READY_TIMEOUT', '코칭 서버 응답이 없습니다.'));
				this.#teardown();
			}, this.#options.readyTimeoutMs ?? DEFAULT_READY_TIMEOUT_MS);

			socket.onmessage = (event) => {
				let message;
				try {
					message = parseServerMessage(JSON.parse(String(event.data)));
				} catch {
					// 명세에 없는 메시지는 무시한다. 내용은 건강데이터일 수 있어 기록하지 않는다
					return;
				}

				if (message.type === 'ready') {
					clearTimeout(timer);
					wasReady = true;
					this.#isReady = true;
					resolve();
				} else if (message.type === 'connection_error') {
					clearTimeout(timer);
					reject(new CoachingSocketError(message.data.code, message.data.message));
					if (wasReady && this.#socket === socket) {
						// 호환 처리. v47 계약에서 ready 이후의 인증 실패는 요청 단위 error로 온다. 그래도 connection_error가
						// 오면 진행 중인 답변을 끊지 않도록 닫지 않고 받기만 하게 남기고, 다음 질문은 새 연결로 보낸다
						this.#draining.add(socket);
						this.#reset();
						this.#options.onQuestionRejected?.(message.data.code);
					}
					if (isSessionRejected(message.data.code)) this.#options.onAuthRequired?.();
				} else {
					this.#options.onEvent(message);
					// 서버는 질문마다 세션을 다시 확인하고, 그 사이 로그아웃·만료됐으면 소켓을 닫지 않고
					// 요청 단위 error(accepted: false)로 알린다(명세 v47 7.2·7.5). 연결은 두고 로그인 상태만 정리한다.
					// 화면이 로그인으로 가며 소켓이 닫혀도 이미 접수된 답변은 서버가 생성·저장을 계속한다
					if (message.type === 'error' && isSessionRejected(message.data.code)) {
						this.#options.onAuthRequired?.();
					}
				}
			};

			socket.onerror = () => {
				// 실패 내용은 onclose에서 함께 처리한다
			};

			socket.onclose = () => {
				clearTimeout(timer);
				reject(new CoachingSocketError('CONNECTION_FAILED', '코칭 서버와 연결하지 못했습니다.'));
				// 받기만 하던 연결이 닫힌 것이면 지금 질문을 보내는 연결은 건드리지 않는다
				this.#draining.delete(socket);
				if (this.#socket === socket) this.#reset();
				if (!this.#isClosedByClient && wasReady) this.#options.onClose?.({ wasReady });
			};
		});
		// 거절된 연결을 다음 시도에서 다시 쓰지 않는다
		this.#ready.catch(() => {
			if (!this.#isReady) this.#teardown();
		});
		return this.#ready;
	}

	#reset() {
		this.#socket = null;
		this.#ready = null;
		this.#isReady = false;
	}

	#teardown() {
		const socket = this.#socket;
		this.#reset();
		if (socket) closeSocket(socket);
	}
}

function closeSocket(socket: SocketLike) {
	socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
	if (socket.readyState === 0 || socket.readyState === 1) socket.close(1000);
}
