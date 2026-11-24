import { api, type ApiClient } from '#lib/api/client.js';
import { readEnum, readNumber, readRecord, readString } from '#lib/api/parse.js';
import { withAuthLock } from './lock';
import {
	GENDERS,
	SOCIAL_TYPES,
	type AuthSessionData,
	type SignupProfile,
	type SocialLoginResult,
	type User,
	type WebSocialLoginInput
} from './types';

export function parseUser(value: unknown): User {
	const record = readRecord(value, 'user');
	return {
		id: readString(record, 'id'),
		endUserId: readString(record, 'endUserId'),
		displayName: readString(record, 'displayName'),
		birthYear: readNumber(record, 'birthYear'),
		gender: readEnum(record, 'gender', GENDERS),
		heightCm: readNumber(record, 'heightCm'),
		weightKg: readNumber(record, 'weightKg'),
		socialType: readEnum(record, 'socialType', SOCIAL_TYPES),
		createdAt: readString(record, 'createdAt')
	};
}

function parseSessionData(record: Record<string, unknown>): AuthSessionData {
	return {
		user: parseUser(record.user),
		expiresAt: readString(record, 'expiresAt')
	};
}

function parseSocialLoginResult(value: unknown): SocialLoginResult {
	const record = readRecord(value, 'data');
	const status = readEnum(record, 'status', ['LOGGED_IN', 'SIGNUP_REQUIRED'] as const);

	if (status === 'LOGGED_IN') return { status, ...parseSessionData(record) };
	return {
		status,
		signupToken: readString(record, 'signupToken'),
		signupTokenExpiresAt: readString(record, 'signupTokenExpiresAt'),
		socialType: readEnum(record, 'socialType', SOCIAL_TYPES)
	};
}

/** 명세 3.3 WEB 소셜 로그인 */
export function loginWithAuthorizationCode(
	input: WebSocialLoginInput,
	client: ApiClient = api,
	beforeRequest: () => void = () => {}
): Promise<SocialLoginResult> {
	return withAuthLock(async () => {
		beforeRequest();
		return parseSocialLoginResult(
			await client.request('/auth/social/login', {
				method: 'POST',
				body: { ...input, platformType: 'WEB' },
				auth: false
			})
		);
	});
}

/** 명세 3.4 회원가입. 201·200 모두 로그인 상태가 된다. */
export function signup(
	signupToken: string,
	profile: SignupProfile,
	client: ApiClient = api,
	beforeRequest: () => void = () => {}
): Promise<AuthSessionData> {
	return withAuthLock(async () => {
		beforeRequest();
		return parseSessionData(
			readRecord(
				await client.request('/auth/signup', {
					method: 'POST',
					body: { signupToken, ...profile },
					auth: false
				}),
				'data'
			)
		);
	});
}

/**
 * 명세 3.7 웹 세션 조회. body 없이 세션 쿠키로 보낸다.
 * 조회는 쿠키를 바꾸지 않아 여러 탭에서 동시에 보내도 되므로 탭 간 잠금을 쓰지 않는다(명세 1.5).
 */
export async function getSession(client: ApiClient = api): Promise<AuthSessionData> {
	return parseSessionData(
		readRecord(await client.request('/auth/session', { auth: false }), 'data')
	);
}

/** 명세 3.6 로그아웃. WEB은 body 없이 cookie로 보낸다. */
export function logout(client: ApiClient = api, onSuccess: () => void = () => {}): Promise<void> {
	return withAuthLock(async () => {
		await client.request('/auth/logout', { method: 'POST', auth: false });
		// 다음 로그인·가입이 잠금을 얻기 전에 공유 로그아웃 상태를 기록한다.
		onSuccess();
	});
}
