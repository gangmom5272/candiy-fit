import { api, type ApiClient } from '#lib/api/client.js';
import { parseConversationPage, parseMessagePage, parseRequestSnapshot } from './parse';
import type { ConversationPage, MessagePage, RequestSnapshot } from './types';

const BASE = '/coaching';

function withQuery(path: string, query: Record<string, string | number | undefined>): string {
	const params = new URLSearchParams();
	for (const [key, value] of Object.entries(query)) {
		if (value !== undefined) params.set(key, String(value));
	}
	const search = params.toString();
	return search ? `${path}?${search}` : path;
}

/** 명세 6.2 대화 목록. 다음 페이지는 직전 응답의 nextCursor를 그대로 넘긴다. */
export async function listConversations(
	options: { limit?: number; cursor?: string } = {},
	client: ApiClient = api
): Promise<ConversationPage> {
	return parseConversationPage(await client.request(withQuery(`${BASE}/conversations`, options)));
}

/**
 * 명세 6.3 대화 메시지. 이전 페이지는 직전 응답의 nextBeforeSequenceNo를 넘긴다.
 * signal을 넘기면 다른 대화로 옮기거나 화면을 떠날 때 조회를 취소할 수 있다.
 */
export async function getConversationMessages(
	conversationId: string,
	options: { limit?: number; beforeSequenceNo?: number; signal?: AbortSignal } = {},
	client: ApiClient = api
): Promise<MessagePage> {
	const { signal, ...query } = options;
	const path = `${BASE}/conversations/${encodeURIComponent(conversationId)}/messages`;
	return parseMessagePage(await client.request(withQuery(path, query), { signal }));
}

/**
 * 명세 6.4 요청 상태. 실패한 요청도 HTTP 200이며 status가 failed다.
 * signal을 넘기면 응답이 오지 않는 조회를 끊을 수 있다.
 */
export async function getRequestStatus(
	requestId: string,
	options: { signal?: AbortSignal } = {},
	client: ApiClient = api
): Promise<RequestSnapshot> {
	return parseRequestSnapshot(
		await client.request(`${BASE}/requests/${encodeURIComponent(requestId)}`, {
			signal: options.signal
		})
	);
}
