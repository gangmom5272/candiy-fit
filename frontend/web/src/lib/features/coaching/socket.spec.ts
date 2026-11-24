import { afterEach, describe, expect, it, vi } from 'vitest';
import { CoachingSocket, CoachingSocketError, defaultSocketUrl, type SocketLike } from './socket';

class FakeSocket implements SocketLike {
	readyState = 0;
	sent: unknown[] = [];
	closedWith: number | undefined;
	onopen: ((event: Event) => void) | null = null;
	onmessage: ((event: MessageEvent) => void) | null = null;
	onclose: ((event: CloseEvent) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;

	send(data: string) {
		this.sent.push(JSON.parse(data));
	}
	close(code?: number) {
		this.closedWith = code;
		this.readyState = 3;
	}
	open() {
		this.readyState = 1;
		this.onopen?.(new Event('open'));
	}
	receive(message: unknown) {
		this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(message) }));
	}
	serverClose() {
		this.readyState = 3;
		this.onclose?.(new CloseEvent('close', { code: 1008 }));
	}
}

const ready = { type: 'ready', data: { protocolVersion: '1.0' } };
const connectionError = (code: string) => ({
	type: 'connection_error',
	data: { code, message: '로그인이 필요합니다.' }
});
/** 질문 시점 인증 실패의 요청 단위 error(명세 v47 7.2·7.5). 접수 전이라 requestId·conversationId가 null, eventSeq가 0이다 */
const requestError = (code: string, retryable: boolean) => ({
	type: 'error',
	clientRequestId: '11111111-1111-4111-8111-111111111111',
	requestId: null,
	conversationId: null,
	eventSeq: 0,
	data: { code, message: '안내 문구', retryable, accepted: false, reason: null, metrics: null }
});
const question = {
	type: 'question' as const,
	clientRequestId: '11111111-1111-4111-8111-111111111111',
	conversationId: null,
	content: '지난주 걸음 기록이 며칠 있어?'
};

function setup() {
	const sockets: FakeSocket[] = [];
	const onEvent = vi.fn();
	const onClose = vi.fn();
	const onAuthRequired = vi.fn();
	const onQuestionRejected = vi.fn();
	const socket = new CoachingSocket({
		url: 'wss://app.test/api/v1/coaching/ws',
		onEvent,
		onClose,
		onAuthRequired,
		onQuestionRejected,
		createSocket: () => {
			const fake = new FakeSocket();
			sockets.push(fake);
			return fake;
		},
		readyTimeoutMs: 50
	});
	return { socket, sockets, onEvent, onClose, onAuthRequired, onQuestionRejected };
}

afterEach(() => vi.useRealTimers());

describe('CoachingSocket 연결', () => {
	it('auth 프레임을 보내지 않고 ready를 받은 뒤 질문을 보낸다', async () => {
		const { socket, sockets } = setup();
		const sending = socket.send(question);
		sockets[0].open();
		expect(sockets[0].sent).toEqual([]);

		sockets[0].receive(ready);
		await sending;
		expect(sockets[0].sent).toEqual([question]);
		expect(socket.isReady).toBe(true);
	});

	it('동시에 여러 번 보내도 연결은 하나만 만든다', async () => {
		const { socket, sockets } = setup();
		const both = Promise.all([
			socket.send(question),
			socket.send({ ...question, content: '두 번째' })
		]);
		sockets[0].open();
		sockets[0].receive(ready);
		await both;
		expect(sockets).toHaveLength(1);
		expect(sockets[0].sent).toHaveLength(2);
	});

	it.each(['AUTH_REQUIRED', 'AUTH_EXPIRED'])(
		'%s로 거절되면 다시 연결하지 않고 로그인 정리를 알린다',
		async (code) => {
			const { socket, sockets, onAuthRequired } = setup();
			const connecting = socket.ensureReady();
			sockets[0].open();
			sockets[0].receive(connectionError(code));

			await expect(connecting).rejects.toMatchObject({ code });
			expect(onAuthRequired).toHaveBeenCalledOnce();
			expect(sockets).toHaveLength(1);
		}
	);

	it('AUTH_UNAVAILABLE로 거절되면 로그인 정리를 알리지 않고, 다음 전송 때 새로 연결한다', async () => {
		const { socket, sockets, onAuthRequired } = setup();
		const connecting = socket.ensureReady();
		sockets[0].open();
		sockets[0].receive(connectionError('AUTH_UNAVAILABLE'));

		await expect(connecting).rejects.toMatchObject({ code: 'AUTH_UNAVAILABLE' });
		expect(onAuthRequired).not.toHaveBeenCalled();
		expect(sockets[0].closedWith).toBe(1000);

		const sending = socket.send(question);
		sockets[1].open();
		sockets[1].receive(ready);
		await sending;
		expect(sockets[1].sent).toEqual([question]);
	});

	it('ready가 오지 않으면 READY_TIMEOUT으로 거절하고 연결을 닫는다', async () => {
		const { socket, sockets } = setup();
		const connecting = socket.ensureReady();
		sockets[0].open();
		await expect(connecting).rejects.toBeInstanceOf(CoachingSocketError);
		await expect(connecting).rejects.toMatchObject({ code: 'READY_TIMEOUT' });
		expect(sockets[0].closedWith).toBe(1000);
	});
});

describe('CoachingSocket 이벤트와 종료', () => {
	async function connected() {
		const context = setup();
		const connecting = context.socket.ensureReady();
		context.sockets[0].open();
		context.sockets[0].receive(ready);
		await connecting;
		return context;
	}

	it('요청 이벤트를 검증해 onEvent로 넘기고 명세에 없는 메시지는 무시한다', async () => {
		const { sockets, onEvent } = await connected();
		sockets[0].receive({
			type: 'delta',
			clientRequestId: question.clientRequestId,
			requestId: '33333333-3333-4333-8333-333333333333',
			conversationId: '22222222-2222-4222-8222-222222222222',
			eventSeq: 7,
			data: { text: '조각' }
		});
		sockets[0].receive({ type: 'unknown', data: {} });
		sockets[0].onmessage?.(new MessageEvent('message', { data: 'not json' }));

		expect(onEvent).toHaveBeenCalledOnce();
		expect(onEvent.mock.calls[0][0]).toMatchObject({ type: 'delta', data: { text: '조각' } });
	});

	it('ready 이후 서버가 닫으면 onClose로 알리고 다음 전송 때 다시 연결한다', async () => {
		const { socket, sockets, onClose } = await connected();
		sockets[0].serverClose();
		expect(onClose).toHaveBeenCalledWith({ wasReady: true });
		expect(socket.isReady).toBe(false);

		const sending = socket.send(question);
		sockets[1].open();
		sockets[1].receive(ready);
		await sending;
		expect(sockets[1].sent).toEqual([question]);
	});

	it('ready 이후 AUTH_REQUIRED를 받으면 로그인 정리를 알리고, 닫히면 onClose로 알린다', async () => {
		const { sockets, onClose, onAuthRequired } = await connected();
		sockets[0].receive(connectionError('AUTH_REQUIRED'));
		expect(onAuthRequired).toHaveBeenCalledOnce();

		sockets[0].serverClose();
		expect(onClose).toHaveBeenCalledWith({ wasReady: true });
	});

	it('ready 이후 AUTH_UNAVAILABLE을 받아도 로그인 정리를 알리지 않는다', async () => {
		const { sockets, onAuthRequired } = await connected();
		sockets[0].receive(connectionError('AUTH_UNAVAILABLE'));
		expect(onAuthRequired).not.toHaveBeenCalled();
	});

	it('연결 시점의 거절은 질문 거절로 알리지 않는다', async () => {
		const { socket, sockets, onQuestionRejected } = setup();
		const connecting = socket.ensureReady();
		sockets[0].open();
		sockets[0].receive(connectionError('AUTH_UNAVAILABLE'));
		await expect(connecting).rejects.toMatchObject({ code: 'AUTH_UNAVAILABLE' });
		expect(onQuestionRejected).not.toHaveBeenCalled();
	});

	// 아래 세 개는 호환 처리다. v47 계약에서 ready 이후의 인증 실패는 요청 단위 error로 온다
	it('ready 이후 connection_error를 받으면 질문 거절을 알리고, 그 연결은 닫지 않은 채 진행 중인 답변을 계속 받는다', async () => {
		const { sockets, onEvent, onQuestionRejected } = await connected();
		sockets[0].receive(connectionError('AUTH_UNAVAILABLE'));

		expect(onQuestionRejected).toHaveBeenCalledExactlyOnceWith('AUTH_UNAVAILABLE');
		// 오류를 받았다는 이유만으로 진행 중인 답변이 오는 소켓을 닫지 않는다
		expect(sockets[0].closedWith).toBeUndefined();

		sockets[0].receive({
			type: 'delta',
			clientRequestId: question.clientRequestId,
			requestId: '33333333-3333-4333-8333-333333333333',
			conversationId: '22222222-2222-4222-8222-222222222222',
			eventSeq: 8,
			data: { text: '이어지는 조각' }
		});
		expect(onEvent).toHaveBeenCalledOnce();
	});

	it('질문이 거절된 연결로는 다시 보내지 않고 새 연결로 보내며, 예전 연결이 닫혀도 새 연결은 유지한다', async () => {
		const { socket, sockets, onClose } = await connected();
		sockets[0].receive(connectionError('AUTH_UNAVAILABLE'));
		expect(socket.isReady).toBe(false);

		const sending = socket.send(question);
		expect(sockets).toHaveLength(2);
		sockets[1].open();
		sockets[1].receive(ready);
		await sending;
		expect(sockets[0].sent).toEqual([]);
		expect(sockets[1].sent).toEqual([question]);

		// 서버가 진행 중인 답변을 다 보낸 뒤 예전 연결을 닫는다
		sockets[0].serverClose();
		expect(onClose).toHaveBeenCalledExactlyOnceWith({ wasReady: true });
		expect(socket.isReady).toBe(true);
		await socket.send(question);
		expect(sockets).toHaveLength(2);
	});

	it('close()는 받기만 하던 연결도 함께 닫는다', async () => {
		const { socket, sockets, onClose } = await connected();
		sockets[0].receive(connectionError('AUTH_UNAVAILABLE'));
		socket.close();
		expect(sockets[0].closedWith).toBe(1000);
		expect(onClose).not.toHaveBeenCalled();
	});

	it('질문 시점의 요청 단위 AUTH_REQUIRED는 이벤트를 넘긴 뒤 로그인 정리를 알리고 연결은 닫지 않는다', async () => {
		const { socket, sockets, onEvent, onClose, onAuthRequired } = await connected();
		sockets[0].receive(requestError('AUTH_REQUIRED', true));

		expect(onEvent).toHaveBeenCalledOnce();
		expect(onEvent.mock.calls[0][0]).toMatchObject({
			type: 'error',
			clientRequestId: question.clientRequestId,
			data: { code: 'AUTH_REQUIRED', accepted: false }
		});
		expect(onAuthRequired).toHaveBeenCalledOnce();
		// 다른 대화의 스트림이 이어지도록 소켓을 닫지 않는다
		expect(sockets[0].closedWith).toBeUndefined();
		expect(socket.isReady).toBe(true);
		expect(onClose).not.toHaveBeenCalled();
	});

	it('질문 시점의 요청 단위 AUTH_UNAVAILABLE은 로그인 정리를 알리지 않고 같은 연결로 다시 보낸다', async () => {
		const { socket, sockets, onEvent, onAuthRequired } = await connected();
		sockets[0].receive(requestError('AUTH_UNAVAILABLE', true));

		expect(onEvent).toHaveBeenCalledOnce();
		expect(onAuthRequired).not.toHaveBeenCalled();

		await socket.send(question);
		expect(sockets).toHaveLength(1);
		expect(sockets[0].sent).toEqual([question]);
	});

	it('인증과 무관한 요청 단위 error에는 로그인 정리를 알리지 않는다', async () => {
		const { sockets, onAuthRequired } = await connected();
		sockets[0].receive(requestError('AI_UNAVAILABLE', true));
		expect(onAuthRequired).not.toHaveBeenCalled();
	});

	it('close()로 닫으면 onClose를 부르지 않는다', async () => {
		const { socket, sockets, onClose } = await connected();
		socket.close();
		expect(sockets[0].closedWith).toBe(1000);
		expect(onClose).not.toHaveBeenCalled();
	});
});

describe('defaultSocketUrl', () => {
	it('https면 wss, http면 ws를 쓴다', () => {
		expect(defaultSocketUrl({ protocol: 'https:', host: 'fit.test' })).toBe(
			'wss://fit.test/api/v1/coaching/ws'
		);
		expect(defaultSocketUrl({ protocol: 'http:', host: 'localhost:5173' })).toBe(
			'ws://localhost:5173/api/v1/coaching/ws'
		);
	});
});
