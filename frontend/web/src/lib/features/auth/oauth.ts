import { isRecord } from '#lib/api/errors.js';
import type { SocialType, WebSocialLoginInput } from './types';

/** callback route `/auth/callback/[provider]`의 provider 값 */
export const OAUTH_PROVIDERS = ['google', 'kakao'] as const;
export type OAuthProvider = (typeof OAUTH_PROVIDERS)[number];

const AUTHORIZE_URLS: Record<OAuthProvider, string> = {
	google: 'https://accounts.google.com/o/oauth2/v2/auth',
	kakao: 'https://kauth.kakao.com/oauth/authorize'
};

/** 서버 Google ID 토큰 검증에 openid가 필요하다. Kakao는 추가 scope 없이 회원번호만 쓴다. */
const SCOPES: Partial<Record<OAuthProvider, string>> = { google: 'openid' };

const PENDING_KEY = 'candiy-fit:oauth-pending';
/** 제공자 로그인 화면에 머무를 수 있는 시간. 지나면 콜백을 받아도 다시 시작한다. */
const PENDING_TTL_MS = 10 * 60 * 1000;

/** 로그인 시작 때 만든 임시 값. 콜백에서 한 번 꺼내면 지운다. */
type PendingLogin = {
	provider: OAuthProvider;
	state: string;
	codeVerifier: string;
	createdAt: number;
};

export type CallbackFailure = 'cancelled' | 'provider-error' | 'state-mismatch' | 'missing-code';

export type CallbackResult =
	{ ok: true; input: WebSocialLoginInput } | { ok: false; reason: CallbackFailure };

export function isOAuthProvider(value: string): value is OAuthProvider {
	return (OAUTH_PROVIDERS as readonly string[]).includes(value);
}

export function toSocialType(provider: OAuthProvider): SocialType {
	return provider === 'google' ? 'GOOGLE' : 'KAKAO';
}

/** 제공자 콘솔과 서버 allowlist에 등록한 값과 정확히 같아야 한다. */
export function getRedirectUri(provider: OAuthProvider, origin: string): string {
	return `${origin}/auth/callback/${provider}`;
}

function toBase64Url(bytes: Uint8Array): string {
	let binary = '';
	for (const byte of bytes) binary += String.fromCharCode(byte);
	return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function randomBase64Url(byteLength: number): string {
	return toBase64Url(crypto.getRandomValues(new Uint8Array(byteLength)));
}

/** 32바이트 → 43자. 명세의 43~128자, 영문·숫자·`-._~` 조건을 만족한다. */
export function createCodeVerifier(): string {
	return randomBase64Url(32);
}

export async function createCodeChallenge(codeVerifier: string): Promise<string> {
	const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(codeVerifier));
	return toBase64Url(new Uint8Array(digest));
}

/**
 * state·verifier를 만들어 sessionStorage에 두고 제공자 인가 URL을 돌려준다.
 * 같은 탭에서 다시 시작하면 이전 값은 덮어쓴다.
 */
export async function createAuthorizeUrl(
	provider: OAuthProvider,
	clientId: string,
	origin: string,
	storage: Storage = sessionStorage,
	now: number = Date.now()
): Promise<string> {
	const pending: PendingLogin = {
		provider,
		state: randomBase64Url(16),
		codeVerifier: createCodeVerifier(),
		createdAt: now
	};
	storage.setItem(PENDING_KEY, JSON.stringify(pending));

	const url = new URL(AUTHORIZE_URLS[provider]);
	url.search = new URLSearchParams({
		client_id: clientId,
		redirect_uri: getRedirectUri(provider, origin),
		response_type: 'code',
		state: pending.state,
		code_challenge: await createCodeChallenge(pending.codeVerifier),
		code_challenge_method: 'S256',
		...(SCOPES[provider] ? { scope: SCOPES[provider] } : {})
	}).toString();
	return url.toString();
}

function takePending(storage: Storage): PendingLogin | null {
	let value: unknown;
	try {
		const raw = storage.getItem(PENDING_KEY);
		storage.removeItem(PENDING_KEY);
		if (raw === null) return null;
		value = JSON.parse(raw);
	} catch {
		// 저장소 차단이나 JSON 오류는 시작 기록이 없는 것과 같다.
		return null;
	}
	if (
		!isRecord(value) ||
		typeof value.provider !== 'string' ||
		!isOAuthProvider(value.provider) ||
		typeof value.state !== 'string' ||
		typeof value.codeVerifier !== 'string' ||
		typeof value.createdAt !== 'number'
	) {
		return null;
	}
	return {
		provider: value.provider,
		state: value.state,
		codeVerifier: value.codeVerifier,
		createdAt: value.createdAt
	};
}

/**
 * 콜백 URL을 확인해 로그인 요청 값을 만든다. 시작 기록은 결과와 상관없이 지우므로
 * 같은 콜백을 다시 열면 `state-mismatch`가 되어 인가 코드를 두 번 보내지 않는다.
 */
export function readCallback(
	provider: OAuthProvider,
	url: URL,
	storage: Storage = sessionStorage,
	now: number = Date.now()
): CallbackResult {
	const pending = takePending(storage);
	const params = url.searchParams;

	const error = params.get('error');
	if (error !== null) {
		return { ok: false, reason: error === 'access_denied' ? 'cancelled' : 'provider-error' };
	}

	if (
		!pending ||
		pending.provider !== provider ||
		pending.state !== params.get('state') ||
		now - pending.createdAt >= PENDING_TTL_MS
	) {
		return { ok: false, reason: 'state-mismatch' };
	}

	const code = params.get('code');
	if (!code) return { ok: false, reason: 'missing-code' };

	return {
		ok: true,
		input: {
			socialType: toSocialType(provider),
			authorizationCode: code,
			codeVerifier: pending.codeVerifier,
			redirectUri: getRedirectUri(provider, url.origin)
		}
	};
}
