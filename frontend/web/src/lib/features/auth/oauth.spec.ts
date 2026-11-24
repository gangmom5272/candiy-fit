import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import {
	createAuthorizeUrl,
	createCodeChallenge,
	createCodeVerifier,
	getRedirectUri,
	readCallback
} from './oauth';

const ORIGIN = 'https://app.test';
const NOW = Date.parse('2026-10-24T00:00:00Z');

class MemoryStorage implements Storage {
	#items = new Map<string, string>();
	get length() {
		return this.#items.size;
	}
	clear() {
		this.#items.clear();
	}
	getItem(key: string) {
		return this.#items.get(key) ?? null;
	}
	key(index: number) {
		return [...this.#items.keys()][index] ?? null;
	}
	removeItem(key: string) {
		this.#items.delete(key);
	}
	setItem(key: string, value: string) {
		this.#items.set(key, value);
	}
}

async function start(provider: 'google' | 'kakao' = 'google', storage = new MemoryStorage()) {
	const url = new URL(await createAuthorizeUrl(provider, 'synthetic-client', ORIGIN, storage, NOW));
	return { url, storage, state: url.searchParams.get('state') ?? '' };
}

function callback(provider: string, params: Record<string, string>) {
	return new URL(`${ORIGIN}/auth/callback/${provider}?${new URLSearchParams(params)}`);
}

describe('PKCE', () => {
	it('verifier는 명세의 길이·문자 조건을 만족한다', () => {
		const verifier = createCodeVerifier();
		expect(verifier).toMatch(/^[A-Za-z0-9\-._~]{43,128}$/);
		expect(createCodeVerifier()).not.toBe(verifier);
	});

	it('challenge는 verifier의 SHA-256 base64url이다', async () => {
		const verifier = createCodeVerifier();
		expect(await createCodeChallenge(verifier)).toBe(
			createHash('sha256').update(verifier).digest('base64url')
		);
	});
});

describe('createAuthorizeUrl', () => {
	it('Google 인가 URL에 openid scope와 S256 challenge를 넣는다', async () => {
		const { url } = await start('google');

		expect(url.origin + url.pathname).toBe('https://accounts.google.com/o/oauth2/v2/auth');
		expect(Object.fromEntries(url.searchParams)).toMatchObject({
			client_id: 'synthetic-client',
			redirect_uri: 'https://app.test/auth/callback/google',
			response_type: 'code',
			scope: 'openid',
			code_challenge_method: 'S256'
		});
		expect(url.searchParams.get('code_challenge')).toMatch(/^[A-Za-z0-9_-]{43}$/);
	});

	it('Kakao 인가 URL에는 scope를 넣지 않는다', async () => {
		const { url } = await start('kakao');

		expect(url.origin + url.pathname).toBe('https://kauth.kakao.com/oauth/authorize');
		expect(url.searchParams.get('redirect_uri')).toBe(getRedirectUri('kakao', ORIGIN));
		expect(url.searchParams.has('scope')).toBe(false);
	});
});

describe('readCallback', () => {
	it('state가 맞으면 로그인 요청 값을 만들고 시작 기록을 지운다', async () => {
		const { url, storage, state } = await start('google');
		const result = readCallback(
			'google',
			callback('google', { code: 'synthetic-code', state }),
			storage,
			NOW
		);

		expect(result).toEqual({
			ok: true,
			input: {
				socialType: 'GOOGLE',
				authorizationCode: 'synthetic-code',
				codeVerifier: expect.any(String),
				redirectUri: url.searchParams.get('redirect_uri')
			}
		});
		if (result.ok) {
			expect(await createCodeChallenge(result.input.codeVerifier)).toBe(
				url.searchParams.get('code_challenge')
			);
		}
		expect(storage.length).toBe(0);
	});

	it('같은 콜백을 다시 열면 인가 코드를 다시 쓰지 않는다', async () => {
		const { storage, state } = await start();
		const url = callback('google', { code: 'synthetic-code', state });

		expect(readCallback('google', url, storage, NOW).ok).toBe(true);
		expect(readCallback('google', url, storage, NOW)).toEqual({
			ok: false,
			reason: 'state-mismatch'
		});
	});

	it.each([
		['state가 다름', 'google', { code: 'c', state: 'other' }, 0],
		['다른 제공자 콜백', 'kakao', { code: 'c' }, 0],
		['정확히 10분 경과', 'google', { code: 'c' }, 10 * 60 * 1000],
		['10분 초과', 'google', { code: 'c' }, 10 * 60 * 1000 + 1]
	])('%s → state-mismatch', async (_, provider, params, elapsed) => {
		const { storage, state } = await start('google');
		const result = readCallback(
			provider === 'google' ? 'google' : 'kakao',
			callback(provider, { state, ...params }),
			storage,
			NOW + elapsed
		);

		expect(result).toEqual({ ok: false, reason: 'state-mismatch' });
	});

	it('시작 기록이 없거나 깨졌으면 state-mismatch', () => {
		const storage = new MemoryStorage();
		const url = callback('google', { code: 'c', state: 's' });

		expect(readCallback('google', url, storage, NOW)).toEqual({
			ok: false,
			reason: 'state-mismatch'
		});
		storage.setItem('candiy-fit:oauth-pending', '{broken');
		expect(readCallback('google', url, storage, NOW)).toEqual({
			ok: false,
			reason: 'state-mismatch'
		});
	});

	it('제공자 오류는 취소와 그 외로 나눈다', async () => {
		const { storage, state } = await start();

		expect(
			readCallback('google', callback('google', { error: 'access_denied', state }), storage, NOW)
		).toEqual({ ok: false, reason: 'cancelled' });
		expect(storage.length).toBe(0);
		expect(
			readCallback('google', callback('google', { error: 'server_error' }), storage, NOW)
		).toEqual({ ok: false, reason: 'provider-error' });
	});

	it('code가 없으면 missing-code', async () => {
		const { storage, state } = await start();

		expect(readCallback('google', callback('google', { state }), storage, NOW)).toEqual({
			ok: false,
			reason: 'missing-code'
		});
	});
});
