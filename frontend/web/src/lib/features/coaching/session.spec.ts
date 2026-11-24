import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '#lib/api/errors.js';
import { sleepAndStepsEvidence } from './fixtures';
import {
	CoachingRequest,
	CoachingSession,
	createClientRequestId,
	getCoachingErrorMessage,
	getHealthUnavailableMessage,
	isValidQuestion
} from './session.svelte';
import { CoachingSocketError } from './socket';
import type { CoachingRequestEvent, QuestionMessage, RequestSnapshot } from './types';

const CLIENT_ID = '11111111-1111-4111-8111-111111111111';
const REQUEST_ID = '33333333-3333-4333-8333-333333333333';
const CONVERSATION_ID = '22222222-2222-4222-8222-222222222222';
const [sleep, , steps] = sleepAndStepsEvidence;

const base = { clientRequestId: CLIENT_ID, requestId: REQUEST_ID, conversationId: CONVERSATION_ID };
const accepted = (eventSeq = 1): CoachingRequestEvent => ({
	...base,
	eventSeq,
	type: 'accepted',
	data: {
		userMessageId: 'u1',
		assistantMessageId: 'a1',
		userSequenceNo: 1,
		assistantSequenceNo: 2,
		conversation: {
			id: CONVERSATION_ID,
			title: '질문',
			createdAt: '2026-10-19T01:00:00Z',
			updatedAt: '2026-10-19T01:00:00Z'
		}
	}
});
const delta = (eventSeq: number, text: string): CoachingRequestEvent => ({
	...base,
	eventSeq,
	type: 'delta',
	data: { text }
});
const errorEvent = (
	eventSeq: number,
	data: Partial<Extract<CoachingRequestEvent, { type: 'error' }>['data']>
): CoachingRequestEvent => ({
	...base,
	eventSeq,
	type: 'error',
	data: {
		code: 'AI_UNAVAILABLE',
		message: '답변을 생성하지 못했습니다.',
		retryable: true,
		accepted: true,
		reason: null,
		metrics: null,
		...data
	}
});
const snapshot = (overrides: Partial<RequestSnapshot> = {}): RequestSnapshot => ({
	requestId: REQUEST_ID,
	clientRequestId: CLIENT_ID,
	conversationId: CONVERSATION_ID,
	status: 'completed',
	userMessageId: 'u1',
	assistantMessage: {
		id: 'a1',
		role: 'assistant',
		sequenceNo: 2,
		content: '최종 답변',
		createdAt: '2026-10-19T01:00:00Z'
	},
	evidence: [sleep],
	errorCode: null,
	createdAt: '2026-10-19T01:00:00Z',
	finishedAt: '2026-10-19T01:00:02Z',
	errorContext: null,
	...overrides
});

function setup(
	options: {
		send?: (q: QuestionMessage) => Promise<void>;
		maxPolls?: number;
		pollTimeoutMs?: number;
		recoveryDeadlineMs?: number;
	} = {}
) {
	const sent: QuestionMessage[] = [];
	const socket = {
		send: vi.fn(options.send ?? (async (question: QuestionMessage) => void sent.push(question))),
		close: vi.fn()
	};
	const getRequestStatus =
		vi.fn<(id: string, options: { signal: AbortSignal }) => Promise<RequestSnapshot>>();
	const session = new CoachingSession({
		socket,
		getRequestStatus,
		createId: () => CLIENT_ID,
		pollIntervalMs: 2000,
		maxPolls: options.maxPolls ?? 5,
		pollTimeoutMs: options.pollTimeoutMs,
		recoveryDeadlineMs: options.recoveryDeadlineMs
	});
	return { session, socket, sent, getRequestStatus };
}

afterEach(() => vi.useRealTimers());

describe('질문 검증과 ID', () => {
	it('앞뒤 공백을 뺀 1~2,000 코드 포인트만 허용한다', () => {
		expect(isValidQuestion('   ')).toBe(false);
		expect(isValidQuestion('가'.repeat(2000))).toBe(true);
		expect(isValidQuestion('😀'.repeat(2001))).toBe(false);
	});

	it('clientRequestId는 UUIDv4 형식이다', () => {
		expect(createClientRequestId()).toMatch(
			/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
		);
	});
});

describe('CoachingRequest 이벤트 반영', () => {
	it('접수 → 단계 → 조각 → 잠정 근거 → 완료 순서로 반영한다', () => {
		const request = new CoachingRequest(CLIENT_ID, null, '질문');
		request.applyEvent(accepted());
		expect(request).toMatchObject({
			phase: 'accepted',
			requestId: REQUEST_ID,
			conversationId: CONVERSATION_ID
		});

		request.applyEvent({
			...base,
			eventSeq: 2,
			type: 'status',
			data: { stage: 'fetching_health_data' }
		});
		expect(request).toMatchObject({ phase: 'generating', stage: 'fetching_health_data' });

		request.applyEvent({ ...base, eventSeq: 3, type: 'evidence', data: { items: [sleep] } });
		request.applyEvent({ ...base, eventSeq: 4, type: 'evidence', data: { items: [sleep, steps] } });
		expect(request.evidence).toHaveLength(2);

		request.applyEvent(delta(5, '수면은 '));
		request.applyEvent(delta(6, '늘었어요.'));
		expect(request.text).toBe('수면은 늘었어요.');

		request.applyEvent({
			...base,
			eventSeq: 7,
			type: 'completed',
			data: {
				assistantMessage: snapshot().assistantMessage,
				evidence: [sleep],
				finishedAt: '2026-10-19T01:00:02Z'
			}
		});
		expect(request).toMatchObject({ phase: 'completed', stage: null, text: '최종 답변' });
		expect(request.evidence).toEqual([sleep]);
		expect(request.isInProgress).toBe(false);
	});

	it('이미 받은 eventSeq는 다시 반영하지 않는다', () => {
		const request = new CoachingRequest(CLIENT_ID, null, '질문');
		request.applyEvent(accepted());
		request.applyEvent(delta(2, '가'));
		request.applyEvent(delta(2, '가'));
		request.applyEvent(delta(1, '나'));
		expect(request.text).toBe('가');
	});

	it('접수 후 실패는 부분 답변을 남기고 잠정 근거를 비운다', () => {
		const request = new CoachingRequest(CLIENT_ID, null, '질문');
		request.applyEvent(accepted());
		request.applyEvent({ ...base, eventSeq: 2, type: 'evidence', data: { items: [sleep] } });
		request.applyEvent(delta(3, '부분 답변'));
		request.applyEvent(
			errorEvent(4, {
				code: 'HEALTH_DATA_UNAVAILABLE',
				reason: 'DATA_NOT_READY',
				metrics: ['sleeps']
			})
		);

		expect(request).toMatchObject({ phase: 'failed', text: '부분 답변' });
		expect(request.evidence).toEqual([]);
		expect(request.error).toMatchObject({
			code: 'HEALTH_DATA_UNAVAILABLE',
			reason: 'DATA_NOT_READY'
		});
	});

	it('접수 전 실패는 rejected로 구분한다', () => {
		const request = new CoachingRequest(CLIENT_ID, null, '질문');
		request.applyEvent({
			...errorEvent(0, { code: 'INVALID_QUESTION', accepted: false, retryable: false }),
			requestId: null,
			conversationId: null
		});
		expect(request).toMatchObject({ phase: 'rejected', requestId: null });
		expect(request.error?.retryable).toBe(false);
	});

	it('진행 중 snapshot은 이미 보인 답변을 지우지 않는다', () => {
		const request = new CoachingRequest(CLIENT_ID, null, '질문');
		request.applyEvent(accepted());
		request.applyEvent(delta(2, '보이는 답변'));
		request.applySnapshot(
			snapshot({
				status: 'generating',
				finishedAt: null,
				evidence: [],
				assistantMessage: { ...snapshot().assistantMessage, content: '' }
			})
		);
		expect(request).toMatchObject({ phase: 'generating', text: '보이는 답변' });
	});

	it.each([
		['HEALTH_DATA_FORBIDDEN', false],
		['INVALID_QUESTION', false],
		['AI_UNAVAILABLE', true]
	])('실패 snapshot(%s)은 명세의 재시도 가능 여부(%s)를 따른다', (errorCode, retryable) => {
		const request = new CoachingRequest(CLIENT_ID, null, '질문');
		request.applySnapshot(snapshot({ status: 'failed', errorCode, evidence: [] }));
		expect(request.error).toMatchObject({ code: errorCode, retryable });
	});

	it('실패 snapshot은 저장된 부분 답변과 errorContext로 실패 사유를 복원한다', () => {
		const request = new CoachingRequest(CLIENT_ID, null, '질문');
		request.applySnapshot(
			snapshot({
				status: 'failed',
				errorCode: 'HEALTH_DATA_UNAVAILABLE',
				errorContext: { reason: 'DATA_NOT_READY', metrics: ['sleeps'] },
				evidence: [],
				assistantMessage: { ...snapshot().assistantMessage, content: '저장된 부분' }
			})
		);
		expect(request).toMatchObject({ phase: 'failed', text: '저장된 부분' });
		expect(request.error).toMatchObject({
			code: 'HEALTH_DATA_UNAVAILABLE',
			message: '수면 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.',
			metrics: ['sleeps']
		});
	});
});

describe('CoachingSession', () => {
	it('질문을 정규화해 보내고 이벤트를 해당 요청에 반영한다', async () => {
		const { session, sent } = setup();
		const request = await session.ask('  지난주 걸음은?  ', null);
		expect(sent).toEqual([
			{
				type: 'question',
				clientRequestId: CLIENT_ID,
				conversationId: null,
				content: '지난주 걸음은?'
			}
		]);

		session.handleEvent(accepted());
		session.handleEvent({ ...accepted(), clientRequestId: 'someone-else', eventSeq: 9 });
		expect(request.phase).toBe('accepted');
		expect(session.isBusy(CONVERSATION_ID)).toBe(true);
	});

	it('접수 이벤트의 대화 제목을 보관한다', async () => {
		const { session } = setup();
		const request = await session.ask('질문', null);
		session.handleEvent(accepted());
		expect(request).toMatchObject({ conversationId: CONVERSATION_ID, conversationTitle: '질문' });
	});

	it('질문에 붙일 수 없는 오류(ID가 null)는 공통 오류로 알리고 전송 중 질문을 미접수로 정리한다', async () => {
		const { session } = setup();
		const sending = await session.ask('보내는 중', null);
		const done = await session.ask('끝난 질문', null);
		done.phase = 'completed';

		session.handleEvent({
			...errorEvent(0, { code: 'INVALID_MESSAGE', message: '요청 형식을 확인해 주세요.' }),
			clientRequestId: null,
			requestId: null,
			conversationId: null
		});

		expect(session.commonError).toMatchObject({
			code: 'INVALID_MESSAGE',
			message: '요청 형식을 확인해 주세요.'
		});
		expect(sending).toMatchObject({ phase: 'rejected' });
		expect(done.phase).toBe('completed');

		await session.ask('새 질문', null);
		expect(session.commonError).toBeNull();
	});

	it('잘못된 길이의 질문은 보내지 않는다', async () => {
		const { session, socket } = setup();
		await expect(session.ask('   ', null)).rejects.toBeInstanceOf(RangeError);
		expect(socket.send).not.toHaveBeenCalled();
	});

	it('연결하지 못하면 접수되지 않은 요청으로 표시한다', async () => {
		const { session } = setup({
			send: async () => {
				throw new CoachingSocketError('CONNECTION_FAILED', '연결 실패');
			}
		});
		const request = await session.ask('질문', null);
		expect(request).toMatchObject({ phase: 'rejected' });
		expect(request.error?.code).toBe('CONNECTION_FAILED');
	});

	it('접수 후 끊기면 2초 간격으로 상태를 조회하고 완료되면 멈춘다', async () => {
		vi.useFakeTimers();
		const { session, getRequestStatus } = setup();
		const request = await session.ask('질문', null);
		session.handleEvent(accepted());
		session.handleEvent(delta(2, '부분'));

		getRequestStatus
			.mockResolvedValueOnce(snapshot({ status: 'generating', finishedAt: null }))
			.mockResolvedValueOnce(snapshot());
		session.handleDisconnect();

		await vi.advanceTimersByTimeAsync(1999);
		expect(getRequestStatus).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(1);
		expect(request).toMatchObject({ phase: 'generating', text: '부분' });
		await vi.advanceTimersByTimeAsync(2000);
		expect(request).toMatchObject({ phase: 'completed', text: '최종 답변' });
		await vi.advanceTimersByTimeAsync(10000);
		expect(getRequestStatus).toHaveBeenCalledTimes(2);
	});

	it('질문 시점에 서버가 거절하면 접수를 기다리던 질문을 미접수로 안내하고, 끊겨도 자동으로 다시 보내지 않는다', async () => {
		const { session, sent } = setup();
		const request = await session.ask('질문', null);
		session.handleQuestionRejected('AUTH_UNAVAILABLE');

		expect(request.phase).toBe('rejected');
		expect(request.error).toMatchObject({
			code: 'AUTH_UNAVAILABLE',
			message: '인증 처리를 일시적으로 이용할 수 없습니다. 잠시 후 다시 시도해 주세요.',
			retryable: true
		});

		// 다시 보내는 것은 사용자가 정한다. 연결이 닫혀도 같은 질문을 다시 보내지 않는다
		session.handleDisconnect();
		await Promise.resolve();
		expect(sent).toHaveLength(1);
	});

	it.each([
		['AUTH_REQUIRED', true, '로그인이 필요합니다.'],
		['AUTH_UNAVAILABLE', true, '인증 처리를 일시적으로 이용할 수 없습니다.']
	])(
		'요청별 error의 %s는 그 질문만 미접수로 정리하고, 끊겨도 자동으로 다시 보내지 않는다',
		async (code, retryable, message) => {
			const { session, sent } = setup();
			const request = await session.ask('질문', null);
			// 서버는 질문을 접수하지 않고 clientRequestId와 accepted: false를 담아 알린다. 소켓은 닫지 않는다.
			// 두 코드 모두 retryable: true다(명세 v47 7.2)
			session.handleEvent({
				...errorEvent(0, { code, message, retryable, accepted: false }),
				requestId: null,
				conversationId: null
			});

			expect(request.phase).toBe('rejected');
			expect(request.error).toMatchObject({ code, message, retryable });
			expect(session.commonError).toBeNull();

			// 로그인 화면으로 가며 연결이 닫히거나 서버가 끊어도 거절된 질문은 다시 보내지 않는다
			session.handleDisconnect();
			await Promise.resolve();
			expect(sent).toHaveLength(1);
		}
	);

	it('질문 시점 거절은 이미 접수돼 답변 중인 질문을 건드리지 않는다', async () => {
		const { session } = setup();
		const request = await session.ask('질문', null);
		session.handleEvent(accepted());
		session.handleEvent(delta(2, '부분'));

		session.handleQuestionRejected('AUTH_UNAVAILABLE');
		expect(request.isInProgress).toBe(true);
		expect(request.text).toBe('부분');
		expect(request.error).toBeNull();
	});

	it('접수 여부를 모른 채 끊기면 같은 ID·최초 대화 ID·같은 내용으로 다시 보낸다', async () => {
		const { session, sent } = setup();
		await session.ask('질문', null);
		session.handleDisconnect();
		await vi.waitFor(() => expect(sent).toHaveLength(2));
		expect(sent[1]).toEqual(sent[0]);

		// 이미 접수됐던 요청이면 snapshot이 오고, 진행 중이면 상태 조회를 이어 간다
		session.handleEvent({
			...base,
			eventSeq: 0,
			type: 'request_snapshot',
			data: snapshot()
		});
		expect(session.requests[0].phase).toBe('completed');
	});

	it('저장 실패(PERSISTENCE_FAILED)는 상태 조회로 실제 저장 결과를 확인한다', async () => {
		vi.useFakeTimers();
		const { session, getRequestStatus } = setup();
		const request = await session.ask('질문', null);
		session.handleEvent(accepted());
		getRequestStatus.mockResolvedValueOnce(snapshot());
		session.handleEvent(errorEvent(2, { code: 'PERSISTENCE_FAILED' }));
		expect(request.phase).toBe('failed');

		await vi.advanceTimersByTimeAsync(2000);
		expect(request.phase).toBe('completed');
	});

	it('저장 실패가 서버에 실패로 확정되면 더 조회하지 않는다 (278)', async () => {
		vi.useFakeTimers();
		const { session, getRequestStatus } = setup();
		const request = await session.ask('질문', null);
		session.handleEvent(accepted());
		getRequestStatus.mockResolvedValue(
			snapshot({ status: 'failed', errorCode: 'PERSISTENCE_FAILED' })
		);
		session.handleEvent(errorEvent(2, { code: 'PERSISTENCE_FAILED' }));

		await vi.advanceTimersByTimeAsync(20_000);
		expect(getRequestStatus).toHaveBeenCalledTimes(1);
		expect(request.phase).toBe('failed');
	});

	it('저장 실패 뒤 아직 진행 중이면 확정될 때까지 이어서 조회한다', async () => {
		vi.useFakeTimers();
		const { session, getRequestStatus } = setup();
		const request = await session.ask('질문', null);
		session.handleEvent(accepted());
		getRequestStatus
			.mockResolvedValueOnce(snapshot({ status: 'generating' }))
			.mockResolvedValueOnce(snapshot());
		session.handleEvent(errorEvent(2, { code: 'PERSISTENCE_FAILED' }));

		await vi.advanceTimersByTimeAsync(20_000);
		expect(getRequestStatus).toHaveBeenCalledTimes(2);
		expect(request.phase).toBe('completed');
	});

	const apiError = (status: number, code: string | null) =>
		new ApiError({
			status,
			code,
			message: '오류',
			details: [],
			retryable: null,
			context: null,
			requestId: null
		});

	it.each([
		[404, 'REQUEST_NOT_FOUND', '요청을 찾을 수 없습니다.'],
		[422, 'INVALID_REQUEST', '요청 내용을 확인해 주세요.']
	])('상태 조회가 %i(%s)면 다시 묻지 않고 실패로 표시한다', async (status, code, message) => {
		vi.useFakeTimers();
		const { session, getRequestStatus } = setup();
		const request = await session.ask('질문', null);
		session.handleEvent(accepted());
		session.handleEvent(delta(2, '부분'));
		getRequestStatus.mockRejectedValue(apiError(status, code));
		session.handleDisconnect();

		await vi.advanceTimersByTimeAsync(10000);
		expect(getRequestStatus).toHaveBeenCalledOnce();
		expect(request).toMatchObject({ phase: 'failed', text: '부분' });
		expect(request.error).toMatchObject({ code, message, retryable: false });
	});

	it.each([
		['500', apiError(500, 'INTERNAL_ERROR')],
		['502 프록시 오류', apiError(502, null)],
		['네트워크 오류', new TypeError('Failed to fetch')]
	])('상태 조회가 %s면 다음 간격에 다시 확인한다', async (_, error) => {
		vi.useFakeTimers();
		const { session, getRequestStatus } = setup();
		const request = await session.ask('질문', null);
		session.handleEvent(accepted());
		getRequestStatus.mockRejectedValueOnce(error).mockResolvedValueOnce(snapshot());
		session.handleDisconnect();

		await vi.advanceTimersByTimeAsync(2000);
		expect(request.phase).toBe('accepted');
		await vi.advanceTimersByTimeAsync(2000);
		expect(request.phase).toBe('completed');
		expect(getRequestStatus).toHaveBeenCalledTimes(2);
	});

	it('상태 조회를 다 써도 생성 중이면 안내와 함께 실패로 바꾸고 다시 묻기를 열어 둔다', async () => {
		vi.useFakeTimers();
		const { session, getRequestStatus } = setup();
		const request = await session.ask('질문', null);
		session.handleEvent(accepted());
		session.handleEvent(delta(2, '부분'));
		getRequestStatus.mockResolvedValue(snapshot({ status: 'generating', finishedAt: null }));
		session.handleDisconnect();

		// setup의 maxPolls 5번(2초 간격)까지는 생성 중으로 둔다
		await vi.advanceTimersByTimeAsync(9999);
		expect(request.phase).toBe('generating');
		await vi.advanceTimersByTimeAsync(1);
		expect(getRequestStatus).toHaveBeenCalledTimes(5);
		expect(request).toMatchObject({ phase: 'failed', text: '부분' });
		expect(request.error).toMatchObject({
			code: 'RESULT_TIMEOUT',
			message: '답변을 가져오지 못했습니다. 다시 시도해 주세요.',
			retryable: true
		});
		expect(session.isBusy(CONVERSATION_ID)).toBe(false);

		await vi.advanceTimersByTimeAsync(10000);
		expect(getRequestStatus).toHaveBeenCalledTimes(5);
	});

	it('저장된 진행 중 질문을 이어받아 상태 조회로 끝까지 따라가고 그동안 대화를 잠근다', async () => {
		vi.useFakeTimers();
		const { session, getRequestStatus, socket } = setup();
		getRequestStatus
			.mockResolvedValueOnce(snapshot({ status: 'generating', finishedAt: null }))
			.mockResolvedValueOnce(snapshot());

		const request = session.resume({
			requestId: REQUEST_ID,
			conversationId: CONVERSATION_ID,
			content: '저장된 질문',
			status: 'generating'
		});
		expect(request).toMatchObject({ phase: 'generating', content: '저장된 질문' });
		expect(session.isBusy(CONVERSATION_ID)).toBe(true);

		await vi.advanceTimersByTimeAsync(4000);
		expect(request).toMatchObject({ phase: 'completed', text: '최종 답변' });
		expect(session.isBusy(CONVERSATION_ID)).toBe(false);
		expect(getRequestStatus).toHaveBeenCalledWith(REQUEST_ID, { signal: expect.any(AbortSignal) });
		expect(socket.send).not.toHaveBeenCalled();
	});

	it('상태 조회가 응답하지 않으면 시간 제한으로 끊고 다음 조회로 넘어가며, 끝내 실패로 바꿔 입력 잠금을 푼다', async () => {
		vi.useFakeTimers();
		const { session, getRequestStatus } = setup({ maxPolls: 3, pollTimeoutMs: 1000 });
		const signals: AbortSignal[] = [];
		// 응답도 오류도 오지 않는 조회(네트워크 장애)
		getRequestStatus.mockImplementation((_id, { signal }) => {
			signals.push(signal);
			return new Promise<RequestSnapshot>(() => {});
		});

		const request = session.resume({
			requestId: REQUEST_ID,
			conversationId: CONVERSATION_ID,
			content: '저장된 질문',
			status: 'generating'
		});

		// 첫 조회: 2초 뒤 시작, 1초 뒤 시간 제한
		await vi.advanceTimersByTimeAsync(3000);
		expect(getRequestStatus).toHaveBeenCalledTimes(1);
		expect(signals[0].aborted).toBe(true);
		expect(session.isBusy(CONVERSATION_ID)).toBe(true);

		// 조회 3번(간격 2초 + 제한 1초)이 모두 끊기면 실패로 바뀐다
		await vi.advanceTimersByTimeAsync(6000);
		expect(getRequestStatus).toHaveBeenCalledTimes(3);
		expect(signals.every((signal) => signal.aborted)).toBe(true);
		expect(request.phase).toBe('failed');
		expect(request.error).toMatchObject({ code: 'RESULT_TIMEOUT', retryable: true });
		expect(session.isBusy(CONVERSATION_ID)).toBe(false);
	});

	it('한 번 응답하지 않은 조회 뒤에도 다음 조회가 성공하면 답변을 반영한다', async () => {
		vi.useFakeTimers();
		const { session, getRequestStatus } = setup({ pollTimeoutMs: 1000 });
		getRequestStatus
			.mockImplementationOnce(() => new Promise<RequestSnapshot>(() => {}))
			.mockResolvedValueOnce(snapshot());

		const request = session.resume({
			requestId: REQUEST_ID,
			conversationId: CONVERSATION_ID,
			content: '저장된 질문',
			status: 'generating'
		});

		await vi.advanceTimersByTimeAsync(5000);
		expect(request).toMatchObject({ phase: 'completed', text: '최종 답변' });
		expect(session.isBusy(CONVERSATION_ID)).toBe(false);
	});

	it('조회 횟수가 남아 있어도 전체 복구 기한이 지나면 실패로 바꿔 입력 잠금을 푼다', async () => {
		vi.useFakeTimers();
		const { session, getRequestStatus } = setup({ maxPolls: 100, recoveryDeadlineMs: 5000 });
		getRequestStatus.mockResolvedValue(snapshot({ status: 'generating', finishedAt: null }));

		const request = session.resume({
			requestId: REQUEST_ID,
			conversationId: CONVERSATION_ID,
			content: '저장된 질문',
			status: 'generating'
		});

		await vi.advanceTimersByTimeAsync(4000);
		expect(request.isInProgress).toBe(true);
		await vi.advanceTimersByTimeAsync(2000);
		expect(request.error).toMatchObject({ code: 'RESULT_TIMEOUT' });
		expect(session.isBusy(CONVERSATION_ID)).toBe(false);
		const calls = getRequestStatus.mock.calls.length;
		await vi.advanceTimersByTimeAsync(10000);
		expect(getRequestStatus).toHaveBeenCalledTimes(calls);
	});

	it('화면을 떠나면 진행 중인 상태 조회를 취소하고 늦은 응답을 반영하지 않는다', async () => {
		vi.useFakeTimers();
		const { session, getRequestStatus } = setup();
		let signal: AbortSignal | undefined;
		let respond: (value: RequestSnapshot) => void = () => {};
		getRequestStatus.mockImplementation((_id, options) => {
			signal = options.signal;
			return new Promise<RequestSnapshot>((resolve) => (respond = resolve));
		});

		const request = session.resume({
			requestId: REQUEST_ID,
			conversationId: CONVERSATION_ID,
			content: '저장된 질문',
			status: 'generating'
		});
		await vi.advanceTimersByTimeAsync(2000);
		expect(getRequestStatus).toHaveBeenCalledOnce();

		session.dispose();
		expect(signal?.aborted).toBe(true);

		respond(snapshot());
		await vi.advanceTimersByTimeAsync(20000);
		expect(request.phase).toBe('generating');
		expect(getRequestStatus).toHaveBeenCalledOnce();
	});

	it('이미 알고 있는 요청은 다시 이어받지 않는다', async () => {
		const { session } = setup();
		const asked = await session.ask('질문', null);
		session.handleEvent(accepted());

		const resumed = session.resume({
			requestId: REQUEST_ID,
			conversationId: CONVERSATION_ID,
			content: '질문',
			status: 'accepted'
		});
		expect(resumed).toBe(asked);
		expect(session.requests).toHaveLength(1);
	});

	it('정리하면 상태 조회를 멈추고 소켓을 닫는다', async () => {
		vi.useFakeTimers();
		const { session, socket, getRequestStatus } = setup();
		await session.ask('질문', null);
		session.handleEvent(accepted());
		session.handleDisconnect();
		session.dispose();

		await vi.advanceTimersByTimeAsync(10000);
		expect(getRequestStatus).not.toHaveBeenCalled();
		expect(socket.close).toHaveBeenCalledOnce();
	});
});

describe('건강 오류 문구 (명세 2.4, 백엔드와 같은 규칙)', () => {
	it.each([
		[
			{ reason: 'DATA_NOT_READY', metrics: ['sleeps'] },
			'수면 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.'
		],
		[
			{ reason: 'DATA_NOT_READY', metrics: ['steps', 'steps'] },
			'걸음 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.'
		],
		[
			{ reason: 'DATA_NOT_READY', metrics: ['sleeps', 'steps'] },
			'건강데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.'
		],
		[
			{ reason: 'DATA_NOT_READY', metrics: [] },
			'건강데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.'
		],
		[
			{ reason: 'LOOKUP_FAILED', metrics: ['sleeps'] },
			'건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.'
		]
	] as const)('%o → %s', (context, message) => {
		expect(getHealthUnavailableMessage({ ...context, metrics: [...context.metrics] })).toBe(
			message
		);
	});

	it('사유가 없으면 조회 실패 문구, 다른 코드는 공개 메시지를 쓴다', () => {
		expect(getCoachingErrorMessage('HEALTH_DATA_UNAVAILABLE')).toBe(
			'건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.'
		);
		expect(
			getCoachingErrorMessage('AI_UNAVAILABLE', { reason: 'LOOKUP_FAILED', metrics: [] })
		).toBe('답변을 생성하지 못했습니다.');
	});
});
