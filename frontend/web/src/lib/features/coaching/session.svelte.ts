import { ApiError } from '#lib/api/errors.js';
import { CoachingSocketError } from './socket';
import type {
	CoachingErrorData,
	CoachingRequestEvent,
	CoachingStage,
	Evidence,
	HealthErrorContext,
	HealthMetric,
	QuestionMessage,
	RequestSnapshot
} from './types';

/** 명세 7.3: 앞뒤 공백 제거 후 1~2,000 코드 포인트 */
export const MAX_QUESTION_LENGTH = 2000;
const DEFAULT_POLL_INTERVAL_MS = 2000;
/** 생성 기한(접수 후 120초)에 여유를 둔 상태 조회 횟수 */
const DEFAULT_MAX_POLLS = 90;
/** 상태 조회 한 번을 기다리는 시간. 응답이 오지 않는 조회를 끊고 다음 간격에 다시 확인한다 */
const DEFAULT_POLL_TIMEOUT_MS = 10_000;
/**
 * 상태 조회로 결과를 따라가는 전체 기한. 정상일 때의 조회 횟수 상한(90회 × 2초 = 3분)과 같게 둔다.
 * 조회가 느리거나 끊기기를 반복해도 이 시간이 지나면 실패로 바꿔 입력 잠금을 푼다
 */
const DEFAULT_RECOVERY_DEADLINE_MS = DEFAULT_MAX_POLLS * DEFAULT_POLL_INTERVAL_MS;

/** 명세 1.3 코칭 오류 공개 메시지. 상태 조회로 복구한 실패는 메시지가 없어 이 표로 채운다. */
const ERROR_MESSAGES: Record<string, string> = {
	AUTH_REQUIRED: '로그인이 필요합니다.',
	AUTH_EXPIRED: '로그인이 필요합니다.',
	// 세션 확인 중 일시 장애. 로그인 상태는 그대로이고 같은 질문을 다시 보낼 수 있다
	AUTH_UNAVAILABLE: '인증 처리를 일시적으로 이용할 수 없습니다. 잠시 후 다시 시도해 주세요.',
	CANDIY_USER_NOT_LINKED:
		'앱 연동을 확인해 주세요. 이미 연동했다면 첫 데이터가 준비될 때까지 기다린 뒤 다시 시도해 주세요.',
	HEALTH_DATA_FORBIDDEN: '건강데이터 조회 권한을 확인해 주세요.',
	// 사유(errorContext)를 모를 때만 쓴다. 사유가 있으면 getHealthUnavailableMessage로 만든다
	HEALTH_DATA_UNAVAILABLE: '건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.',
	AI_UNAVAILABLE: '답변을 생성하지 못했습니다.',
	GENERATION_TIMEOUT: '답변을 생성하지 못했습니다.',
	GENERATION_INTERRUPTED: '답변을 생성하지 못했습니다.',
	PERSISTENCE_FAILED: '요청을 처리하지 못했습니다.',
	INTERNAL_ERROR: '요청을 처리하지 못했습니다.',
	REQUEST_NOT_FOUND: '요청을 찾을 수 없습니다.',
	INVALID_REQUEST: '요청 내용을 확인해 주세요.',
	INVALID_MESSAGE: '요청 내용을 확인해 주세요.',
	INVALID_QUESTION: '질문 내용을 확인해 주세요.',
	CONVERSATION_NOT_FOUND: '대화를 찾을 수 없습니다.',
	REQUEST_ID_CONFLICT: '이미 사용한 전송 ID입니다.',
	CONVERSATION_BUSY: '답변이 생성 중입니다.',
	CONNECTION_FAILED: '코칭 서버와 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.',
	// 웹 전용 코드. 상태 조회를 다 써도 생성 중에 머물러 있을 때(서버에서 요청이 멈춘 경우)
	RESULT_TIMEOUT: '답변을 가져오지 못했습니다. 다시 시도해 주세요.'
};

/**
 * - `sending`: 보냈지만 접수 여부를 아직 모름
 * - `accepted` / `generating`: 접수됨, 답변 생성 중
 * - `completed`: 최종 답변·근거 확정
 * - `failed`: 접수 후 실패. 부분 답변은 유지
 * - `rejected`: 접수되지 않음. 저장된 것이 없어 고쳐서 새로 물어볼 수 있음
 */
export type RequestPhase =
	'sending' | 'accepted' | 'generating' | 'completed' | 'failed' | 'rejected';

export type RequestError = Pick<CoachingErrorData, 'code' | 'message' | 'reason' | 'metrics'> & {
	retryable: boolean;
};

/**
 * 명세 1.3에서 retryable이 X인 코드. 같은 질문을 다시 보내도 같은 결과라 다시 묻기를 제공하지 않는다.
 * 상태 조회로 복구한 실패는 이벤트와 달리 retryable 값이 없어 이 표로 판단한다.
 */
const NON_RETRYABLE_CODES = new Set([
	'INVALID_REQUEST',
	'INVALID_MESSAGE',
	'INVALID_QUESTION',
	'CONVERSATION_NOT_FOUND',
	'REQUEST_NOT_FOUND',
	'REQUEST_ID_CONFLICT',
	'HEALTH_DATA_FORBIDDEN'
]);

export function isRetryableCode(code: string): boolean {
	return !NON_RETRYABLE_CODES.has(code);
}

/** 저장된 실패 메시지(명세 6.3의 errorCode)를 화면 문구로 바꾼다 */
/**
 * 건강 오류 문구의 지표 이름. 백엔드(backend/app/health/http.py)와 같아야 한다.
 * 화면 표기(format.ts METRIC_LABELS의 steps '활동')와 다르다.
 */
const HEALTH_ERROR_METRIC_NAMES: Record<HealthMetric, string> = {
	steps: '걸음',
	heart_rates: '심박',
	oxygen_saturations: '산소포화도',
	sleeps: '수면',
	exercises: '운동',
	weights: '체중',
	body_fats: '체지방률',
	basal_metabolic_rates: '기초대사량',
	heights: '키'
};

/**
 * 명세 2.4 HEALTH_DATA_UNAVAILABLE 공개 메시지. 실시간 error는 서버 문구를 그대로 쓰고,
 * 상태 조회·지난 메시지로 복구한 실패는 errorContext로 같은 문구를 만든다.
 * DATA_NOT_READY는 지표가 하나면 그 이름, 여러 개·없음이면 "건강데이터"를 쓴다.
 */
export function getHealthUnavailableMessage(context: HealthErrorContext): string {
	if (context.reason === 'LOOKUP_FAILED') return ERROR_MESSAGES.HEALTH_DATA_UNAVAILABLE;
	const metrics = context.metrics.filter((metric, index, list) => list.indexOf(metric) === index);
	const label =
		metrics.length === 1 ? `${HEALTH_ERROR_METRIC_NAMES[metrics[0]]} 데이터` : '건강데이터';
	return `${label}를 준비 중입니다. 잠시 후 다시 시도해 주세요.`;
}

/** 저장된 실패(명세 6.3·6.4의 errorCode·errorContext)를 화면 문구로 바꾼다 */
export function getCoachingErrorMessage(
	code: string,
	context: HealthErrorContext | null = null
): string {
	if (code === 'HEALTH_DATA_UNAVAILABLE' && context) return getHealthUnavailableMessage(context);
	return ERROR_MESSAGES[code] ?? '답변을 생성하지 못했습니다.';
}

/**
 * 상태 조회(명세 6.4)에서 다시 물어도 결과가 바뀌지 않는 오류.
 * 404 REQUEST_NOT_FOUND·422 INVALID_REQUEST·인증 실패 등 4xx이며, 408·429는 일시적이라 제외한다.
 * 5xx·명세 형식이 아닌 프록시 오류·네트워크 오류는 일시적일 수 있어 다음 간격에 다시 확인한다.
 */
function isFinalPollError(error: unknown): error is ApiError {
	return (
		error instanceof ApiError &&
		error.status >= 400 &&
		error.status < 500 &&
		error.status !== 408 &&
		error.status !== 429
	);
}

export function normalizeQuestion(content: string): string {
	return content.trim();
}

export function isValidQuestion(content: string): boolean {
	const length = Array.from(normalizeQuestion(content)).length;
	return length > 0 && length <= MAX_QUESTION_LENGTH;
}

/** 브라우저가 secure context가 아니면 randomUUID가 없으므로 getRandomValues로 UUIDv4를 만든다 */
export function createClientRequestId(): string {
	if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
	const bytes = crypto.getRandomValues(new Uint8Array(16));
	bytes[6] = (bytes[6] & 0x0f) | 0x40;
	bytes[8] = (bytes[8] & 0x3f) | 0x80;
	const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
	return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** 질문 한 건의 화면 상태. 명세 7.4 이벤트와 6.4 상태 조회 결과를 같은 규칙으로 반영한다. */
export class CoachingRequest {
	readonly clientRequestId: string;
	/** 재전송할 때도 최초 값을 그대로 쓴다(명세 6.1) */
	readonly initialConversationId: string | null;
	readonly content: string;

	phase = $state<RequestPhase>('sending');
	stage = $state<CoachingStage | null>(null);
	requestId = $state<string | null>(null);
	conversationId = $state<string | null>(null);
	/** 접수 이벤트로 받은 대화 제목. 새 대화 화면 제목에 쓴다 */
	conversationTitle = $state<string | null>(null);
	/** 화면에 보여 줄 답변. delta를 이어 붙이고 완료 시 최종 본문으로 바꾼다 */
	text = $state('');
	/** 생성 중에는 잠정 근거, 완료 후에는 최종 근거. 실패하면 비운다 */
	evidence = $state<Evidence[]>([]);
	error = $state<RequestError | null>(null);

	#lastEventSeq = 0;

	constructor(clientRequestId: string, conversationId: string | null, content: string) {
		this.clientRequestId = clientRequestId;
		this.initialConversationId = conversationId;
		this.conversationId = conversationId;
		this.content = content;
	}

	get isInProgress(): boolean {
		return this.phase === 'sending' || this.phase === 'accepted' || this.phase === 'generating';
	}

	/** 같은 요청의 이벤트만 넣는다. eventSeq가 이미 받은 값 이하면 무시한다. */
	applyEvent(event: CoachingRequestEvent): void {
		if (event.type === 'request_snapshot') {
			this.applySnapshot(event.data);
			return;
		}
		if (event.eventSeq > 0) {
			if (event.eventSeq <= this.#lastEventSeq) return;
			this.#lastEventSeq = event.eventSeq;
		}
		if (event.requestId) this.requestId = event.requestId;
		if (event.conversationId) this.conversationId = event.conversationId;

		switch (event.type) {
			case 'accepted':
				this.phase = 'accepted';
				this.conversationTitle = event.data.conversation.title;
				break;
			case 'status':
				this.phase = 'generating';
				this.stage = event.data.stage;
				break;
			case 'delta':
				this.phase = 'generating';
				this.text += event.data.text;
				break;
			case 'evidence':
				this.evidence = event.data.items;
				break;
			case 'completed':
				this.phase = 'completed';
				this.stage = null;
				this.text = event.data.assistantMessage.content;
				this.evidence = event.data.evidence;
				break;
			case 'error':
				this.#fail(event.data.accepted ? 'failed' : 'rejected', event.data);
				break;
		}
	}

	/** 명세 6.4 상태 조회 또는 request_snapshot. 과거 delta를 재생하지 않고 저장된 결과로 맞춘다. */
	applySnapshot(snapshot: RequestSnapshot): void {
		this.requestId = snapshot.requestId;
		this.conversationId = snapshot.conversationId;

		if (snapshot.status === 'completed') {
			this.phase = 'completed';
			this.stage = null;
			this.text = snapshot.assistantMessage.content;
			this.evidence = snapshot.evidence;
		} else if (snapshot.status === 'failed') {
			const code = snapshot.errorCode ?? 'INTERNAL_ERROR';
			// 서버에 저장된 부분 답변을 우선하고, 없으면 이미 보여 준 답변을 지우지 않는다
			if (snapshot.assistantMessage.content) this.text = snapshot.assistantMessage.content;
			this.#fail('failed', {
				code,
				message: getCoachingErrorMessage(code, snapshot.errorContext),
				retryable: isRetryableCode(code),
				reason: snapshot.errorContext?.reason ?? null,
				metrics: snapshot.errorContext?.metrics ?? null
			});
		} else if (this.phase === 'sending') {
			// 진행 중: 생성 중 본문은 빈 문자열이므로 화면의 답변은 그대로 둔다
			this.phase = snapshot.status;
		}
	}

	/**
	 * 상태 조회를 끝까지 했는데도 생성 중일 때. 안내 없이 "생각 중"에 멈춰 있지 않게 실패로 바꾼다.
	 * 서버 쪽 일시적 문제일 수 있어 다시 묻기를 열어 둔다
	 */
	markTimedOut(): void {
		this.#fail('failed', {
			code: 'RESULT_TIMEOUT',
			message: ERROR_MESSAGES.RESULT_TIMEOUT,
			retryable: true,
			reason: null,
			metrics: null
		});
	}

	/** 연결 실패 등으로 질문이 서버에 가지 않았을 때 */
	markNotSent(code: string): void {
		this.#fail('rejected', {
			code,
			message: ERROR_MESSAGES[code] ?? ERROR_MESSAGES.CONNECTION_FAILED,
			retryable: true,
			reason: null,
			metrics: null
		});
	}

	/** 상태 조회가 4xx로 끝나 결과를 더 확인할 수 없을 때. 다시 물어도 같으므로 재시도 대상이 아니다. */
	markUnconfirmed(code: string): void {
		this.#fail('failed', {
			code,
			message: ERROR_MESSAGES[code] ?? ERROR_MESSAGES.INTERNAL_ERROR,
			retryable: false,
			reason: null,
			metrics: null
		});
	}

	toQuestionMessage(): QuestionMessage {
		return {
			type: 'question',
			clientRequestId: this.clientRequestId,
			conversationId: this.initialConversationId,
			content: this.content
		};
	}

	#fail(phase: 'failed' | 'rejected', error: RequestError) {
		this.phase = phase;
		this.stage = null;
		// 실패한 요청의 잠정 근거를 최종 근거처럼 남기지 않는다(명세 7.4)
		this.evidence = [];
		this.error = {
			code: error.code,
			message: error.message,
			retryable: error.retryable,
			reason: error.reason,
			metrics: error.metrics
		};
	}
}

export type CoachingSessionDeps = {
	socket: {
		send(question: QuestionMessage): Promise<void>;
		close(): void;
	};
	getRequestStatus: (
		requestId: string,
		options: { signal: AbortSignal }
	) => Promise<RequestSnapshot>;
	createId?: () => string;
	pollIntervalMs?: number;
	maxPolls?: number;
	/** 상태 조회 한 번의 시간 제한 */
	pollTimeoutMs?: number;
	/** 상태 조회를 시작한 때부터의 전체 기한 */
	recoveryDeadlineMs?: number;
};

/**
 * 코칭 질문 전송과 복구. 소켓 이벤트를 요청별로 나눠 반영하고,
 * 연결이 끊기면 명세 7.6대로 접수된 요청은 상태 조회(2초 간격), 접수 여부를 모르는 요청은 같은 ID로 재전송한다.
 */
export class CoachingSession {
	requests = $state<CoachingRequest[]>([]);
	/** 특정 질문에 붙일 수 없는 오류(clientRequestId·requestId가 null인 error, 명세 7.3) */
	commonError = $state<RequestError | null>(null);

	#deps: CoachingSessionDeps;
	#timers: ReturnType<typeof setTimeout>[] = [];
	/** 진행 중인 상태 조회. 화면을 떠나면 모두 취소한다 */
	#polling: AbortController[] = [];
	#isDisposed = false;

	constructor(deps: CoachingSessionDeps) {
		this.#deps = deps;
	}

	/** 같은 대화에 진행 중인 요청이 있으면 서버가 CONVERSATION_BUSY로 거절하므로 미리 막는다 */
	isBusy(conversationId: string | null): boolean {
		return this.requests.some(
			(request) => request.isInProgress && request.conversationId === conversationId
		);
	}

	async ask(content: string, conversationId: string | null): Promise<CoachingRequest> {
		const normalized = normalizeQuestion(content);
		if (!isValidQuestion(normalized)) throw new RangeError('질문은 1~2,000자로 입력해 주세요.');
		this.commonError = null;

		const request = new CoachingRequest(
			(this.#deps.createId ?? createClientRequestId)(),
			conversationId,
			normalized
		);
		this.requests.push(request);
		await this.#send(request);
		return request;
	}

	/** CoachingSocket의 onEvent로 연결한다 */
	handleEvent(event: CoachingRequestEvent): void {
		const request = this.requests.find(
			(item) =>
				(event.clientRequestId !== null && item.clientRequestId === event.clientRequestId) ||
				(event.requestId !== null && item.requestId === event.requestId)
		);
		if (!request) {
			if (event.type === 'error' && event.clientRequestId === null && event.requestId === null) {
				this.#handleCommonError(event);
			}
			return;
		}
		request.applyEvent(event);

		// 저장 실패는 결과를 상태 조회로 확인한다(명세 7.4)
		if (event.type === 'error' && event.data.code === 'PERSISTENCE_FAILED') this.#poll(request);
		// 동일 전송 재시도의 snapshot이 아직 진행 중이면 상태 조회를 이어 간다(명세 7.6)
		if (event.type === 'request_snapshot' && request.isInProgress) this.#poll(request);
	}

	/**
	 * 지난 메시지(6.3)에 진행 중(accepted·generating)으로 저장된 질문을 이어서 확인한다.
	 * 새로고침·화면 이동으로 실시간 연결을 잃은 질문이라 상태 조회(6.4)로 끝까지 따라간다(명세 7.6).
	 * clientRequestId는 6.3에 없어 화면 구분용 값을 쓴다. requestId가 있어 다시 보내지는 않는다.
	 */
	resume(saved: {
		requestId: string;
		conversationId: string;
		content: string;
		status: 'accepted' | 'generating';
	}): CoachingRequest {
		const existing = this.requests.find((request) => request.requestId === saved.requestId);
		if (existing) return existing;

		const request = new CoachingRequest(
			`resumed-${saved.requestId}`,
			saved.conversationId,
			saved.content
		);
		request.requestId = saved.requestId;
		request.phase = saved.status;
		this.requests.push(request);
		this.#poll(request);
		return request;
	}

	/**
	 * CoachingSocket의 onQuestionRejected로 연결한다. ready 이후에 connection_error를 받은 경우의 호환 처리다
	 * (명세 v47에서 질문 시점 인증 실패는 요청 단위 error로 오고 handleEvent가 처리한다).
	 * 접수를 기다리던 질문을 미접수로 정리해 안내하고, 연결이 닫힐 때 자동으로 다시 보내지 않게 한다.
	 * 이미 접수돼 답변 중인 질문은 그대로 둔다.
	 */
	handleQuestionRejected(code: string): void {
		for (const request of this.requests.filter((item) => item.phase === 'sending')) {
			request.markNotSent(code);
		}
	}

	/** CoachingSocket의 onClose로 연결한다. 끊김은 취소가 아니다. */
	handleDisconnect(): void {
		for (const request of this.requests.filter((item) => item.isInProgress)) {
			if (request.requestId) this.#poll(request);
			else void this.#send(request);
		}
	}

	/**
	 * 웹은 질문마다 올바른 UUID를 보내므로 null 오류는 전송 중인 질문 중 하나가 원인이다.
	 * 어느 질문인지 알 수 없어 공통 오류로 알리고, 접수되지 않은 채 남지 않도록 전송 중 질문을 미접수로 정리한다.
	 */
	#handleCommonError(event: Extract<CoachingRequestEvent, { type: 'error' }>) {
		this.commonError = {
			code: event.data.code,
			message: event.data.message,
			retryable: event.data.retryable,
			reason: event.data.reason,
			metrics: event.data.metrics
		};
		for (const request of this.requests.filter((item) => item.phase === 'sending')) {
			request.applyEvent({ ...event, data: { ...event.data, accepted: false } });
		}
	}

	dispose(): void {
		this.#isDisposed = true;
		for (const timer of this.#timers) clearTimeout(timer);
		this.#timers = [];
		for (const abort of this.#polling) abort.abort();
		this.#polling = [];
		this.#deps.socket.close();
	}

	async #send(request: CoachingRequest) {
		try {
			await this.#deps.socket.send(request.toQuestionMessage());
		} catch (error) {
			const code = error instanceof CoachingSocketError ? error.code : 'CONNECTION_FAILED';
			request.markNotSent(code === 'READY_TIMEOUT' ? 'CONNECTION_FAILED' : code);
		}
	}

	#poll(request: CoachingRequest, attempt = 0, startedAt = Date.now()) {
		const requestId = request.requestId;
		if (this.#isDisposed || !requestId) return;
		const deadline = this.#deps.recoveryDeadlineMs ?? DEFAULT_RECOVERY_DEADLINE_MS;
		if (
			attempt >= (this.#deps.maxPolls ?? DEFAULT_MAX_POLLS) ||
			Date.now() - startedAt >= deadline
		) {
			if (request.isInProgress) request.markTimedOut();
			return;
		}

		const timer = setTimeout(async () => {
			this.#timers = this.#timers.filter((item) => item !== timer);
			// 서버가 완료·실패로 확정한 상태를 받았는지. 확정되면 저장 실패(PERSISTENCE_FAILED)여도 더 묻지 않는다.
			// 서버는 저장 실패를 보낸 뒤 보조 저장으로 failed·PERSISTENCE_FAILED를 남기므로, 코드만 보고 이어 가면
			// 같은 답을 최대 횟수(약 3분)까지 계속 조회한다
			let settled = false;
			try {
				const snapshot = await this.#getStatus(requestId);
				if (this.#isDisposed) return;
				request.applySnapshot(snapshot);
				settled = snapshot.status === 'completed' || snapshot.status === 'failed';
			} catch (error) {
				if (this.#isDisposed) return;
				if (isFinalPollError(error)) {
					request.markUnconfirmed(error.code ?? 'INTERNAL_ERROR');
					return;
				}
				// 일시적인 조회 실패(5xx·네트워크·시간 초과)는 다음 간격에 다시 확인한다
			}
			if (!settled && (request.isInProgress || request.error?.code === 'PERSISTENCE_FAILED')) {
				this.#poll(request, attempt + 1, startedAt);
			}
		}, this.#deps.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS);
		this.#timers.push(timer);
	}

	/**
	 * 상태 조회 한 번. 시간 제한 안에 응답이 없으면 요청을 취소하고 실패로 끝낸다.
	 * 응답하지 않는 조회가 다음 조회를 막아 입력 잠금이 풀리지 않는 일을 막는다.
	 * 취소 신호를 무시하는 구현이어도 기다리지 않도록 시간 제한과 경쟁시킨다.
	 */
	async #getStatus(requestId: string): Promise<RequestSnapshot> {
		const abort = new AbortController();
		this.#polling.push(abort);
		let timer: ReturnType<typeof setTimeout> | undefined;
		const timedOut = new Promise<never>((_, reject) => {
			timer = setTimeout(() => {
				abort.abort();
				reject(new Error('상태 조회 시간 초과'));
			}, this.#deps.pollTimeoutMs ?? DEFAULT_POLL_TIMEOUT_MS);
			// 화면을 떠나 취소됐을 때도 기다리지 않고 끝낸다
			abort.signal.addEventListener('abort', () => reject(new Error('상태 조회 취소')));
		});
		try {
			return await Promise.race([
				this.#deps.getRequestStatus(requestId, { signal: abort.signal }),
				timedOut
			]);
		} finally {
			clearTimeout(timer);
			this.#polling = this.#polling.filter((item) => item !== abort);
		}
	}
}
