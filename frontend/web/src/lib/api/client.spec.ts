import { describe, expect, it, vi } from 'vitest';
import { createApiClient } from './client';
import { ApiError } from './errors';

type Call = { url: string; init: RequestInit };

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json', ...headers }
	});
}

function fakeFetch(...responses: Response[]) {
	const calls: Call[] = [];
	const fetchFn = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
		calls.push({ url: String(input), init });
		const response = responses.shift();
		if (!response) throw new Error('준비한 응답이 없습니다.');
		return response;
	});
	return { calls, fetch: fetchFn };
}

function authHeader(call: Call): string | null {
	return new Headers(call.init.headers).get('Authorization');
}

const expired = () =>
	json(401, { error: { code: 'AUTH_EXPIRED', message: '로그인이 필요합니다.' } });

describe('createApiClient', () => {
	it('이전 세션의 늦은 AUTH_EXPIRED는 같은 ID의 새 로그인 세션도 지우지 않는다', async () => {
		let release!: (value: Response) => void;
		const response = new Promise<Response>((resolve) => (release = resolve));
		const client = createApiClient({ fetch: vi.fn(() => response) });
		const handler = { sessionVersion: 1, handleSessionExpired: vi.fn() };
		client.setAuthHandler(handler);
		const request = client.request('/health/metrics/steps');
		handler.sessionVersion++;
		release(expired());
		await expect(request).rejects.toMatchObject({ code: 'AUTH_EXPIRED' });
		expect(handler.handleSessionExpired).not.toHaveBeenCalled();
	});

	it('취소를 무시한 HTTP 응답은 현재 세션의 만료 알림을 발생시키지 않는다', async () => {
		let release!: (value: Response) => void;
		const response = new Promise<Response>((resolve) => (release = resolve));
		const client = createApiClient({ fetch: vi.fn(() => response) });
		const handler = { handleSessionExpired: vi.fn() };
		client.setAuthHandler(handler);
		const controller = new AbortController();
		const request = client.request('/health/metrics/steps', { signal: controller.signal });
		controller.abort();
		release(expired());
		await expect(request).rejects.toMatchObject({ code: 'AUTH_EXPIRED' });
		expect(handler.handleSessionExpired).not.toHaveBeenCalled();
	});

	it('상태 재확인은 캐시 없이 세션 쿠키만 사용하고 body와 query를 추가하지 않는다', async () => {
		const { calls, fetch } = fakeFetch(json(200, { data: { ok: true } }));
		const client = createApiClient({ fetch });
		await client.request('/health/data-status', { cache: 'no-store', auth: false });
		expect(calls).toHaveLength(1);
		expect(calls[0].url).toBe('/api/v1/health/data-status');
		expect(calls[0].init).toMatchObject({
			method: 'GET',
			cache: 'no-store',
			credentials: 'include'
		});
		expect(calls[0].init.body).toBeUndefined();
		expect(authHeader(calls[0])).toBeNull();
	});

	it('성공 응답의 data를 돌려주고 세션 쿠키를 함께 보내며 Authorization header는 붙이지 않는다', async () => {
		const { calls, fetch } = fakeFetch(json(200, { data: { ok: true } }));
		const client = createApiClient({ baseUrl: 'https://api.test', fetch });
		client.setAuthHandler({ handleSessionExpired: () => {} });

		await expect(client.request('/users/me')).resolves.toEqual({ ok: true });
		expect(calls[0].url).toBe('https://api.test/api/v1/users/me');
		expect(authHeader(calls[0])).toBeNull();
		expect(calls[0].init.credentials).toBe('include');
	});

	it('204는 body 없이 undefined를 돌려준다', async () => {
		const { fetch } = fakeFetch(new Response(null, { status: 204 }));
		const client = createApiClient({ fetch });

		await expect(client.request('/auth/logout', { method: 'POST' })).resolves.toBeUndefined();
	});

	it('오류 응답을 ApiError로 바꾸고 422 details와 request id를 담는다', async () => {
		const { fetch } = fakeFetch(
			json(
				422,
				{
					error: {
						code: 'INVALID_REQUEST',
						message: '요청 내용을 확인해 주세요.',
						details: [{ field: 'heightCm', reason: 'decimal_places' }]
					}
				},
				{ 'X-Request-Id': 'req-1' }
			)
		);
		const client = createApiClient({ fetch });

		const error = await client
			.request('/auth/signup', { method: 'POST', body: {} })
			.catch((e) => e);
		expect(error).toBeInstanceOf(ApiError);
		expect(error).toMatchObject({
			status: 422,
			code: 'INVALID_REQUEST',
			details: [{ field: 'heightCm', reason: 'decimal_places' }],
			retryable: null,
			requestId: 'req-1'
		});
	});

	it('AUTH_EXPIRED면 다시 보내지 않고 세션 만료를 알린 뒤 던진다', async () => {
		const { calls, fetch } = fakeFetch(expired());
		const client = createApiClient({ fetch });
		const handleSessionExpired = vi.fn();
		client.setAuthHandler({ handleSessionExpired });

		await expect(client.request('/health/status')).rejects.toMatchObject({ code: 'AUTH_EXPIRED' });
		expect(handleSessionExpired).toHaveBeenCalledOnce();
		expect(calls).toHaveLength(1);
	});

	it.each([
		[401, 'AUTH_REQUIRED'],
		[503, 'AUTH_UNAVAILABLE'],
		[500, 'INTERNAL_ERROR']
	])('%i %s는 세션 만료로 처리하지 않고 그대로 던진다', async (status, code) => {
		const { fetch } = fakeFetch(json(status, { error: { code, message: '오류' } }));
		const client = createApiClient({ fetch });
		const handleSessionExpired = vi.fn();
		client.setAuthHandler({ handleSessionExpired });

		await expect(client.request('/health/status')).rejects.toMatchObject({ status, code });
		expect(handleSessionExpired).not.toHaveBeenCalled();
	});

	it('인증 API(auth: false)는 401 AUTH_EXPIRED에도 세션 만료를 알리지 않고 body 없이 쿠키로 보낸다', async () => {
		const { calls, fetch } = fakeFetch(expired());
		const client = createApiClient({ fetch });
		const handleSessionExpired = vi.fn();
		client.setAuthHandler({ handleSessionExpired });

		await expect(client.request('/auth/session', { auth: false })).rejects.toMatchObject({
			code: 'AUTH_EXPIRED'
		});
		expect(authHeader(calls[0])).toBeNull();
		expect(calls[0].init.credentials).toBe('include');
		expect(calls[0].init.body).toBeUndefined();
		expect(handleSessionExpired).not.toHaveBeenCalled();
	});
});
