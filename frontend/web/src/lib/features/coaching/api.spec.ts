import { describe, expect, it, vi } from 'vitest';
import { createApiClient } from '#lib/api/client.js';
import { ResponseFormatError } from '#lib/api/errors.js';
import { getConversationMessages, getRequestStatus, listConversations } from './api';
import { sleepAndStepsEvidence } from './fixtures';

const conversation = {
	id: '22222222-2222-4222-8222-222222222222',
	title: '지난주 걸음 기록이 며칠 있어?',
	summary: null,
	createdAt: '2026-10-19T01:00:00.000000Z',
	updatedAt: '2026-10-19T01:00:02.000000Z'
};

// 합성 근거를 서버 응답처럼 JSON으로 한 번 직렬화해 파서를 통과시킨다
const evidenceJson = JSON.parse(JSON.stringify(sleepAndStepsEvidence));

function setup(body: unknown, status = 200) {
	const urls: string[] = [];
	const signals: (AbortSignal | null | undefined)[] = [];
	const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
		urls.push(String(input));
		signals.push(init?.signal);
		return new Response(JSON.stringify(body), {
			status,
			headers: { 'Content-Type': 'application/json' }
		});
	});
	return { client: createApiClient({ fetch }), urls, signals };
}

describe('listConversations', () => {
	it('대화 목록과 다음 cursor를 읽고 쿼리를 붙인다', async () => {
		const { client, urls } = setup({
			data: {
				items: [{ ...conversation, lastRequestStatus: 'completed' }],
				nextCursor: 'cursor-2'
			}
		});

		const page = await listConversations({ limit: 20, cursor: 'cursor-1' }, client);
		expect(urls[0]).toBe('/api/v1/coaching/conversations?limit=20&cursor=cursor-1');
		expect(page.items[0]).toMatchObject({ title: '지난주 걸음 기록이 며칠 있어?', summary: null });
		expect(page.nextCursor).toBe('cursor-2');
	});

	it('nextCursor가 null이면 마지막 페이지로 받는다', async () => {
		const { client, urls } = setup({
			data: { items: [{ ...conversation, lastRequestStatus: 'failed' }], nextCursor: null }
		});

		const page = await listConversations({}, client);
		expect(urls[0]).toBe('/api/v1/coaching/conversations');
		expect(page.items[0].lastRequestStatus).toBe('failed');
		expect(page.nextCursor).toBeNull();
	});

	it('lastRequestStatus가 null이면 ResponseFormatError를 던진다 (요청 없는 대화는 없다)', async () => {
		const { client } = setup({
			data: { items: [{ ...conversation, lastRequestStatus: null }], nextCursor: null }
		});

		await expect(listConversations({}, client)).rejects.toBeInstanceOf(ResponseFormatError);
	});

	it('명세에 없는 상태 값이면 ResponseFormatError를 던진다', async () => {
		const { client } = setup({
			data: { items: [{ ...conversation, lastRequestStatus: 'unknown' }], nextCursor: null }
		});

		await expect(listConversations({}, client)).rejects.toBeInstanceOf(ResponseFormatError);
	});
});

describe('getConversationMessages', () => {
	it('메시지·근거·errorContext를 읽는다', async () => {
		const { client, urls } = setup({
			data: {
				conversation,
				items: [
					{
						id: '55555555-5555-4555-8555-555555555555',
						requestId: '33333333-3333-4333-8333-333333333333',
						role: 'assistant',
						sequenceNo: 2,
						content: '',
						requestStatus: 'failed',
						errorCode: 'HEALTH_DATA_UNAVAILABLE',
						createdAt: '2026-10-19T01:00:00.000000Z',
						evidence: evidenceJson,
						errorContext: { reason: 'DATA_NOT_READY', metrics: ['sleeps'] }
					}
				],
				nextBeforeSequenceNo: null
			}
		});

		const page = await getConversationMessages(conversation.id, { beforeSequenceNo: 10 }, client);
		expect(urls[0]).toBe(
			`/api/v1/coaching/conversations/${conversation.id}/messages?beforeSequenceNo=10`
		);
		expect(page.items[0].evidence).toEqual(sleepAndStepsEvidence);
		expect(page.items[0].errorContext).toEqual({ reason: 'DATA_NOT_READY', metrics: ['sleeps'] });
		expect(page.nextBeforeSequenceNo).toBeNull();
	});

	it('404 CONVERSATION_NOT_FOUND를 ApiError로 전달한다', async () => {
		const { client } = setup(
			{ error: { code: 'CONVERSATION_NOT_FOUND', message: '대화를 찾을 수 없습니다.' } },
			404
		);

		await expect(getConversationMessages(conversation.id, {}, client)).rejects.toMatchObject({
			code: 'CONVERSATION_NOT_FOUND'
		});
	});
});

describe('getRequestStatus', () => {
	it('실패한 요청도 200 응답의 status로 읽는다', async () => {
		const { client, urls } = setup({
			data: {
				requestId: '99999999-9999-4999-8999-999999999999',
				clientRequestId: '88888888-8888-4888-8888-888888888888',
				conversationId: conversation.id,
				status: 'failed',
				userMessageId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
				assistantMessage: {
					id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
					role: 'assistant',
					sequenceNo: 4,
					content: '',
					createdAt: '2026-10-19T01:00:03Z'
				},
				evidence: [],
				errorCode: 'HEALTH_DATA_UNAVAILABLE',
				errorContext: { reason: 'DATA_NOT_READY', metrics: ['sleeps'] },
				createdAt: '2026-10-19T01:00:03Z',
				finishedAt: '2026-10-19T01:00:05Z'
			}
		});

		const snapshot = await getRequestStatus('99999999-9999-4999-8999-999999999999', {}, client);
		expect(urls[0]).toBe('/api/v1/coaching/requests/99999999-9999-4999-8999-999999999999');
		expect(snapshot).toMatchObject({ status: 'failed', errorCode: 'HEALTH_DATA_UNAVAILABLE' });
	});
});

describe('조회 취소', () => {
	it('대화 메시지 조회는 signal을 요청에 넘기고 쿼리에는 넣지 않는다', async () => {
		const { client, urls, signals } = setup({
			data: { conversation, items: [], nextBeforeSequenceNo: null }
		});
		const abort = new AbortController();

		await getConversationMessages(
			conversation.id,
			{ beforeSequenceNo: 9, signal: abort.signal },
			client
		);

		expect(urls[0]).toBe(
			`/api/v1/coaching/conversations/${conversation.id}/messages?beforeSequenceNo=9`
		);
		expect(signals[0]).toBe(abort.signal);
	});

	it('요청 상태 조회는 signal을 요청에 넘긴다', async () => {
		const { client, signals } = setup({ data: null });
		const abort = new AbortController();

		// 응답 형식 검증에서 실패하더라도 요청에는 signal이 실려 있어야 한다
		await getRequestStatus(
			'99999999-9999-4999-8999-999999999999',
			{ signal: abort.signal },
			client
		).catch(() => {});

		expect(signals[0]).toBe(abort.signal);
	});
});
