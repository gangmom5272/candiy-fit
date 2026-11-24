import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApiClient } from '#lib/api/client.js';
import { ApiError } from '#lib/api/errors.js';
import { withAuthLock } from './lock';
import { AuthSession, toLogoutFailure } from './session.svelte';
import { getLogoutVersion, publishLogout } from './logout-sync';
import type { SignupProfile } from './types';

const user = {
	id: '6ef7d160-428a-4bd3-86ca-08f084390dea',
	endUserId: '6ef7d160-428a-4bd3-86ca-08f084390dea',
	displayName: '테스트',
	birthYear: 2000,
	gender: 'FEMALE',
	heightCm: 160,
	weightKg: 50,
	socialType: 'KAKAO',
	createdAt: '2026-10-19T00:00:00Z'
};

const profile: SignupProfile = {
	displayName: '테스트',
	birthYear: 2000,
	gender: 'FEMALE',
	heightCm: 160,
	weightKg: 50
};

const loginInput = {
	socialType: 'KAKAO',
	authorizationCode: 'synthetic-code',
	codeVerifier: 'synthetic-verifier',
	redirectUri: 'https://app.test/auth/callback/kakao'
} as const;

function json(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

const sessionBody = () => ({
	data: { user, expiresAt: '2026-10-26T01:00:00Z' }
});
const errorBody = (code: string, extra: Record<string, unknown> = {}) => ({
	error: { code, message: '오류', ...extra }
});

function setup(...responses: Array<Response | Error>) {
	const paths: string[] = [];
	const fetch = vi.fn(async (input: RequestInfo | URL) => {
		paths.push(new URL(String(input), 'https://app.test').pathname);
		const next = responses.shift();
		if (!next) throw new Error('준비한 응답이 없습니다.');
		if (next instanceof Error) throw next;
		return next;
	});
	const client = createApiClient({ fetch });
	const session = new AuthSession(client);
	client.setAuthHandler(session);
	return { client, session, paths };
}

describe('AuthSession.sessionVersion', () => {
	it('인증된 동일 ID로 다시 로그인해도 새 세션 세대를 만든다', async () => {
		const { session, paths } = setup(
			json(200, { data: { status: 'LOGGED_IN', ...sessionBody().data } }),
			json(200, { data: { status: 'LOGGED_IN', ...sessionBody().data } })
		);
		await session.loginWithAuthorizationCode(loginInput);
		const previousVersion = session.sessionVersion;
		expect(session.status).toBe('authenticated');
		expect(session.user?.id).toBe(user.id);

		await expect(session.loginWithAuthorizationCode(loginInput)).resolves.toBe('LOGGED_IN');
		expect(session.status).toBe('authenticated');
		expect(session.user?.id).toBe(user.id);
		expect(session.sessionVersion).toBeGreaterThan(previousVersion);
		expect(paths).toEqual(['/api/v1/auth/social/login', '/api/v1/auth/social/login']);
	});

	it('같은 세션 복원과 프로필 객체 갱신은 세션 세대를 유지한다', async () => {
		const { session, paths } = setup(json(200, sessionBody()), json(200, sessionBody()));
		await session.restore();
		const previousVersion = session.sessionVersion;

		await expect(session.restore()).resolves.toBe('restored');
		expect(session.sessionVersion).toBe(previousVersion);
		if (session.user) session.updateUser({ ...session.user, displayName: '프로필 갱신' });
		expect(session.user?.displayName).toBe('프로필 갱신');
		expect(session.status).toBe('authenticated');
		expect(session.sessionVersion).toBe(previousVersion);
		expect(paths).toEqual(['/api/v1/auth/session', '/api/v1/auth/session']);
	});

	it('로컬 상태 정리 후 동일 ID 로그인은 이전 세션과 분리한다', async () => {
		const { session, paths } = setup(
			json(200, { data: { status: 'LOGGED_IN', ...sessionBody().data } }),
			json(200, { data: { status: 'LOGGED_IN', ...sessionBody().data } })
		);
		await session.loginWithAuthorizationCode(loginInput);
		const previousVersion = session.sessionVersion;

		session.clearLocalSession();
		const clearedVersion = session.sessionVersion;
		expect(clearedVersion).toBeGreaterThan(previousVersion);
		expect(session.status).toBe('unauthenticated');
		expect(session.user).toBeNull();

		await expect(session.loginWithAuthorizationCode(loginInput)).resolves.toBe('LOGGED_IN');
		expect(session.status).toBe('authenticated');
		expect(session.user?.id).toBe(user.id);
		expect(session.sessionVersion).toBeGreaterThan(clearedVersion);
		expect(paths).toEqual(['/api/v1/auth/social/login', '/api/v1/auth/social/login']);
	});
});

describe('AuthSession.restore', () => {
	it('상태를 정리한 뒤 도착한 조회 성공은 로그인 상태를 되살리지 않는다', async () => {
		let release!: (response: Response) => void;
		let started!: () => void;
		const requestStarted = new Promise<void>((resolve) => {
			started = resolve;
		});
		const client = createApiClient({
			fetch: async () => {
				started();
				return new Promise<Response>((resolve) => {
					release = resolve;
				});
			}
		});
		const session = new AuthSession(client);
		const restoring = session.restore();
		await requestStarted;
		session.clearLocalSession();
		release(json(200, sessionBody()));
		await expect(restoring).resolves.toBe('no-session');
		expect(session.status).toBe('unauthenticated');
	});

	it('세션 조회에 성공하면 로그인 상태가 된다', async () => {
		const { session, paths } = setup(json(200, sessionBody()));

		await expect(session.restore()).resolves.toBe('restored');
		expect(session.status).toBe('authenticated');
		expect(session.user?.displayName).toBe('테스트');
		expect(paths).toEqual(['/api/v1/auth/session']);
	});

	it.each([
		['AUTH_REQUIRED', json(401, errorBody('AUTH_REQUIRED')), 'no-session'],
		['AUTH_EXPIRED', json(401, errorBody('AUTH_EXPIRED')), 'expired']
	])('%s이면 로그인 상태를 정리한다', async (_, failure, outcome) => {
		const { session } = setup(failure);

		await expect(session.restore()).resolves.toBe(outcome);
		expect(session.status).toBe('unauthenticated');
		expect(session.user).toBeNull();
	});

	it.each([
		['503 AUTH_UNAVAILABLE', json(503, errorBody('AUTH_UNAVAILABLE'))],
		['403 ORIGIN_NOT_ALLOWED', json(403, errorBody('ORIGIN_NOT_ALLOWED'))],
		['422 INVALID_REQUEST', json(422, errorBody('INVALID_REQUEST'))],
		['500 INTERNAL_ERROR', json(500, errorBody('INTERNAL_ERROR'))],
		['응답 유실', new TypeError('Failed to fetch')],
		['명세 형식이 아닌 502', new Response('Bad Gateway', { status: 502 })],
		['형식이 다른 200', json(200, { data: { user } })]
	])('%s이면 로그인 상태를 지우지 않고 같은 쿠키로 다시 조회할 수 있다', async (_, failure) => {
		const { session, paths } = setup(failure, json(200, sessionBody()));

		await expect(session.restore()).resolves.toBe('unavailable');
		expect(session.status).toBe('unknown');

		await expect(session.restore()).resolves.toBe('restored');
		expect(session.status).toBe('authenticated');
		expect(paths).toEqual(['/api/v1/auth/session', '/api/v1/auth/session']);
	});

	it('같은 탭의 동시 복원은 요청 하나로 합친다', async () => {
		const { session, paths } = setup(json(200, sessionBody()));

		const outcomes = await Promise.all([session.restore(), session.restore()]);
		expect(outcomes).toEqual(['restored', 'restored']);
		expect(paths).toEqual(['/api/v1/auth/session']);
	});

	it('일반 API가 AUTH_EXPIRED면 다시 보내지 않고 로그인 상태를 정리한다', async () => {
		const { client, session, paths } = setup(
			json(200, sessionBody()),
			json(401, errorBody('AUTH_EXPIRED'))
		);
		await session.restore();

		await expect(client.request('/users/me')).rejects.toMatchObject({ code: 'AUTH_EXPIRED' });
		expect(session.status).toBe('unauthenticated');
		expect(paths).toEqual(['/api/v1/auth/session', '/api/v1/users/me']);
	});

	it('일반 API의 503은 로그인 상태를 지우지 않는다', async () => {
		const { client, session } = setup(
			json(200, sessionBody()),
			json(503, errorBody('AUTH_UNAVAILABLE'))
		);
		await session.restore();

		await expect(client.request('/users/me')).rejects.toMatchObject({ status: 503 });
		expect(session.status).toBe('authenticated');
	});
});

describe('AuthSession 로그인·가입', () => {
	const signupRequired = () =>
		json(200, {
			data: {
				status: 'SIGNUP_REQUIRED',
				signupToken: 'signup-1',
				signupTokenExpiresAt: '2026-10-19T01:10:00Z',
				socialType: 'KAKAO'
			}
		});
	const beforeExpiry = Date.parse('2026-10-19T01:05:00Z');

	it('기존 회원이면 바로 로그인 상태가 된다', async () => {
		const { session } = setup(json(200, { data: { status: 'LOGGED_IN', ...sessionBody().data } }));

		await expect(session.loginWithAuthorizationCode(loginInput)).resolves.toBe('LOGGED_IN');
		expect(session.status).toBe('authenticated');
	});

	it('서버가 자격을 거절하면 서버 호출 없이 로그인 상태만 지운다', async () => {
		const { session, paths } = setup(
			json(200, { data: { status: 'LOGGED_IN', ...sessionBody().data } })
		);
		await session.loginWithAuthorizationCode(loginInput);

		session.clearLocalSession();
		expect(session).toMatchObject({ status: 'unauthenticated', user: null });
		expect(paths).toHaveLength(1);
	});

	it('가입이 필요하면 가입 자격을 메모리에 두고 만료되면 버린다', async () => {
		const { session } = setup(signupRequired());

		await expect(session.loginWithAuthorizationCode(loginInput)).resolves.toBe('SIGNUP_REQUIRED');
		expect(session.getSignupCredential(beforeExpiry)?.signupToken).toBe('signup-1');
		expect(session.getSignupCredential(Date.parse('2026-10-19T01:10:00Z'))).toBeNull();
		expect(session.getSignupCredential(beforeExpiry)).toBeNull();
	});

	it('가입 자격이 없으면 요청 없이 login-required를 돌려준다', async () => {
		const { session, paths } = setup();

		await expect(session.signup(profile)).resolves.toBe('login-required');
		expect(paths).toEqual([]);
	});

	it('422는 가입 자격을 유지하고 ApiError를 던진다', async () => {
		vi.useFakeTimers({ now: beforeExpiry });
		try {
			const { session } = setup(
				signupRequired(),
				json(
					422,
					errorBody('INVALID_REQUEST', { details: [{ field: 'displayName', reason: 'blank' }] })
				)
			);
			await session.loginWithAuthorizationCode(loginInput);

			await expect(session.signup(profile)).rejects.toBeInstanceOf(ApiError);
			expect(session.getSignupCredential()).not.toBeNull();
		} finally {
			vi.useRealTimers();
		}
	});

	it.each([
		['SIGNUP_TOKEN_INVALID', json(401, errorBody('SIGNUP_TOKEN_INVALID'))],
		['응답 유실', new TypeError('Failed to fetch')]
	])('%s이면 가입 자격을 버리고 login-required를 돌려준다', async (_, failure) => {
		vi.useFakeTimers({ now: beforeExpiry });
		try {
			const { session } = setup(signupRequired(), failure);
			await session.loginWithAuthorizationCode(loginInput);

			await expect(session.signup(profile)).resolves.toBe('login-required');
			expect(session.getSignupCredential()).toBeNull();
		} finally {
			vi.useRealTimers();
		}
	});

	it('가입에 성공하면 로그인 상태가 되고 가입 자격을 버린다', async () => {
		vi.useFakeTimers({ now: beforeExpiry });
		try {
			const { session } = setup(
				signupRequired(),
				json(201, { data: { status: 'LOGGED_IN', ...sessionBody().data } })
			);
			await session.loginWithAuthorizationCode(loginInput);

			await expect(session.signup(profile)).resolves.toBe('signed-up');
			expect(session.status).toBe('authenticated');
			expect(session.getSignupCredential()).toBeNull();
		} finally {
			vi.useRealTimers();
		}
	});

	it('로그아웃 204를 받은 뒤 로그인 상태를 정리한다', async () => {
		const { session } = setup(json(200, sessionBody()), new Response(null, { status: 204 }));
		await session.restore();

		await session.logout();
		expect(session.status).toBe('unauthenticated');
	});
});

describe('withAuthLock', () => {
	it('Web Locks가 없으면 탭 안에서 순서대로 실행한다', async () => {
		const order: string[] = [];
		const slow = withAuthLock(async () => {
			await new Promise((resolve) => setTimeout(resolve, 10));
			order.push('first');
		}, undefined);
		const fast = withAuthLock(async () => {
			order.push('second');
		}, undefined);

		await Promise.all([slow, fast]);
		expect(order).toEqual(['first', 'second']);
	});

	it('앞 작업이 실패해도 다음 작업을 실행한다', async () => {
		const failed = withAuthLock(async () => {
			throw new Error('실패');
		}, undefined);
		const next = withAuthLock(async () => 'next', undefined);

		await expect(failed).rejects.toThrow('실패');
		await expect(next).resolves.toBe('next');
	});
});

describe('로그아웃 오류 분류', () => {
	async function failLogout(response: Response | Error) {
		const { session, paths } = setup(json(200, sessionBody()), response);
		await session.restore();
		const error = await session.logout().then(
			() => null,
			(reason: unknown) => reason
		);
		expect(session.status).toBe('authenticated');
		expect(paths.filter((path) => path === '/api/v1/auth/logout')).toHaveLength(1);
		return toLogoutFailure(error);
	}

	it.each([
		[403, 'ORIGIN_NOT_ALLOWED'],
		[422, 'INVALID_REQUEST']
	])('폐기 전에 거절한 %i %s는 실패로 본다', async (status, code) => {
		expect(await failLogout(json(status, errorBody(code)))).toBe('failed');
	});

	it.each([
		['retryable 없음', {}],
		['retryable: true', { retryable: true }],
		['retryable: false', { retryable: false }]
	])('503 AUTH_UNAVAILABLE(%s)은 완료 여부를 모름으로 본다', async (_, extra) => {
		expect(await failLogout(json(503, errorBody('AUTH_UNAVAILABLE', extra)))).toBe('uncertain');
	});

	it.each([
		['500 INTERNAL_ERROR', json(500, errorBody('INTERNAL_ERROR'))],
		['명세 형식이 아닌 502', new Response('<html>Bad Gateway</html>', { status: 502 })],
		['응답 유실', new TypeError('Failed to fetch')]
	])('%s는 완료 여부를 모름으로 본다', async (_, response) => {
		expect(await failLogout(response)).toBe('uncertain');
	});
});

describe('탭 간 로그아웃', () => {
	afterEach(() => vi.unstubAllGlobals());

	function sharedStorage() {
		const entries = new Map<string, string>();
		vi.stubGlobal('window', {
			localStorage: {
				getItem: (key: string) => entries.get(key) ?? null,
				setItem: (key: string, value: string) => entries.set(key, value)
			}
		});
	}

	it('204일 때만 다른 탭의 로그인 상태와 회원 정보를 정리한다', async () => {
		sharedStorage();
		const first = setup(json(200, sessionBody()), new Response(null, { status: 204 }));
		const second = setup(json(200, sessionBody()));
		await first.session.restore();
		await second.session.restore();
		await first.session.logout();
		second.session.syncLogout();
		expect(second.session.status).toBe('unauthenticated');
		expect(second.session.user).toBeNull();
		expect(second.paths).toEqual(['/api/v1/auth/session']);
		expect(getLogoutVersion()).not.toBe('');
	});

	it('로그아웃 실패는 다른 탭으로 전파하지 않는다', async () => {
		sharedStorage();
		const first = setup(json(200, sessionBody()), json(503, errorBody('AUTH_UNAVAILABLE')));
		const second = setup(json(200, sessionBody()));
		await first.session.restore();
		await second.session.restore();
		await expect(first.session.logout()).rejects.toBeInstanceOf(ApiError);
		second.session.syncLogout();
		expect(first.session.status).toBe('authenticated');
		expect(second.session.status).toBe('authenticated');
		expect(getLogoutVersion()).toBe('');
	});

	it.each(['restore', 'login'] as const)(
		'다른 탭 로그아웃 이후 늦은 %s 성공을 버린다',
		async (kind) => {
			sharedStorage();
			let release!: (response: Response) => void;
			let started!: () => void;
			const requestStarted = new Promise<void>((resolve) => {
				started = resolve;
			});
			const client = createApiClient({
				fetch: async () => {
					started();
					return new Promise<Response>((resolve) => {
						release = resolve;
					});
				}
			});
			const session = new AuthSession(client);
			const pending =
				kind === 'restore' ? session.restore() : session.loginWithAuthorizationCode(loginInput);
			await requestStarted;
			publishLogout();
			// storage 이벤트를 받기 전에도 공유 버전을 확인해야 한다.
			release(json(200, { data: { status: 'LOGGED_IN', ...sessionBody().data } }));
			if (kind === 'restore') await expect(pending).resolves.toBe('no-session');
			else await expect(pending).rejects.toThrow('로그인 상태가 변경');
			expect(session.status).toBe('unauthenticated');
		}
	);

	it('로그아웃 뒤 재로그인하면 늦은 이벤트로 새 상태를 지우지 않는다', async () => {
		sharedStorage();
		const { session } = setup(json(200, { data: { status: 'LOGGED_IN', ...sessionBody().data } }));
		publishLogout();
		await session.loginWithAuthorizationCode(loginInput);
		session.syncLogout();
		expect(session.status).toBe('authenticated');
	});

	// 세션 조회는 쿠키를 바꾸지 않아 잠금 대상이 아니다(명세 1.5). 로그인·가입만 잠금 뒤에서 취소된다
	it.each(['login', 'signup'] as const)(
		'로그아웃 잠금 뒤에 대기하던 %s 요청은 서버에 보내지 않는다',
		async (kind) => {
			sharedStorage();
			const second = setup(
				json(200, {
					data: {
						status: 'SIGNUP_REQUIRED',
						signupToken: 'synthetic',
						signupTokenExpiresAt: '2099-01-01T00:00:00Z',
						socialType: 'KAKAO'
					}
				}),
				json(200, { data: { status: 'LOGGED_IN', ...sessionBody().data } })
			);
			await second.session.loginWithAuthorizationCode(loginInput);
			second.paths.length = 0;
			let release!: (response: Response) => void;
			let started!: () => void;
			const requestStarted = new Promise<void>((resolve) => {
				started = resolve;
			});
			const first = new AuthSession(
				createApiClient({
					fetch: async () => {
						started();
						return new Promise<Response>((resolve) => {
							release = resolve;
						});
					}
				})
			);
			const loggingOut = first.logout();
			await requestStarted;
			const pending =
				kind === 'login'
					? second.session.loginWithAuthorizationCode(loginInput)
					: second.session.signup(profile);
			const result = pending.catch(() => 'cancelled');
			release(new Response(null, { status: 204 }));
			await loggingOut;
			await result;
			expect(second.paths).toEqual([]);
			expect(second.session.status).toBe('unauthenticated');
		}
	);

	it('다른 탭 로그아웃은 가입 자격과 늦은 가입 성공도 무효화한다', async () => {
		sharedStorage();
		let release!: (response: Response) => void;
		let started!: () => void;
		const requestStarted = new Promise<void>((resolve) => {
			started = resolve;
		});
		const client = createApiClient({
			fetch: async (input) => {
				if (String(input).endsWith('/social/login'))
					return json(200, {
						data: {
							status: 'SIGNUP_REQUIRED',
							signupToken: 'synthetic',
							signupTokenExpiresAt: '2099-01-01T00:00:00Z',
							socialType: 'KAKAO'
						}
					});
				started();
				return new Promise<Response>((resolve) => {
					release = resolve;
				});
			}
		});
		const session = new AuthSession(client);
		await session.loginWithAuthorizationCode(loginInput);
		const pending = session.signup(profile);
		await requestStarted;
		publishLogout();
		expect(session.getSignupCredential()).toBeNull();
		release(json(201, { data: { status: 'LOGGED_IN', ...sessionBody().data } }));
		await expect(pending).resolves.toBe('login-required');
		expect(session.status).toBe('unauthenticated');
	});

	it('이전 복원의 종료 처리가 새 복원의 중복 방지를 해제하지 않는다', async () => {
		sharedStorage();
		const releases: Array<(response: Response) => void> = [];
		const client = createApiClient({
			fetch: async () => new Promise<Response>((resolve) => releases.push(resolve))
		});
		const session = new AuthSession(client);
		const old = session.restore();
		await vi.waitFor(() => expect(releases).toHaveLength(1));
		session.clearLocalSession();
		const next = session.restore();
		releases[0](json(200, sessionBody()));
		await expect(old).resolves.toBe('no-session');
		await vi.waitFor(() => expect(releases).toHaveLength(2));
		expect(session.restore()).toBe(next);
		releases[1](json(200, sessionBody()));
		await expect(next).resolves.toBe('restored');
		expect(session.status).toBe('authenticated');
	});

	it('저장소 접근이 막혀도 현재 탭 로그아웃은 완료한다', async () => {
		vi.stubGlobal('window', {
			get localStorage() {
				throw new Error('blocked');
			}
		});
		const { session } = setup(json(200, sessionBody()), new Response(null, { status: 204 }));
		await session.restore();
		await session.logout();
		expect(session.status).toBe('unauthenticated');
	});
});

describe('탭 간 로그인(S15P31A403-426)', () => {
	afterEach(() => vi.unstubAllGlobals());

	const other = {
		...user,
		id: '0b0f7a4e-5c1d-4f0e-9a52-3f6f1c2d7e91',
		endUserId: '0b0f7a4e-5c1d-4f0e-9a52-3f6f1c2d7e91',
		displayName: '다른 계정'
	};
	const sessionOf = (who: typeof user) => ({
		data: { user: who, expiresAt: '2026-10-26T01:00:00Z' }
	});
	const loggedIn = (who: typeof user) => ({
		data: { status: 'LOGGED_IN', ...sessionOf(who).data }
	});

	function sharedStorage() {
		const entries = new Map<string, string>();
		vi.stubGlobal('window', {
			localStorage: {
				getItem: (key: string) => entries.get(key) ?? null,
				setItem: (key: string, value: string) => entries.set(key, value)
			}
		});
	}

	it('다른 탭이 다른 계정으로 로그인하면 세션을 다시 조회해 회원을 바꾸고 새 세대를 만든다', async () => {
		sharedStorage();
		const first = setup(json(200, loggedIn(other)));
		const second = setup(json(200, sessionOf(user)), json(200, sessionOf(other)));
		await second.session.restore();
		const previousVersion = second.session.sessionVersion;

		await first.session.loginWithAuthorizationCode(loginInput);
		await second.session.syncLogin();

		expect(second.session.user?.id).toBe(other.id);
		expect(second.session.status).toBe('authenticated');
		expect(second.session.sessionVersion).toBeGreaterThan(previousVersion);
		expect(second.paths).toEqual(['/api/v1/auth/session', '/api/v1/auth/session']);
		// 한 번 확인한 뒤에는 다시 묻지 않는다
		await second.session.syncLogin();
		expect(second.paths).toHaveLength(2);
	});

	it('로그인한 탭 자신은 세션을 다시 조회하지 않는다', async () => {
		sharedStorage();
		const first = setup(json(200, loggedIn(user)));
		await first.session.loginWithAuthorizationCode(loginInput);
		await first.session.syncLogin();
		expect(first.paths).toEqual(['/api/v1/auth/social/login']);
	});

	it('다른 탭이 같은 계정으로 로그인하면 회원과 세대를 그대로 둔다', async () => {
		sharedStorage();
		const first = setup(json(200, loggedIn(user)));
		const second = setup(json(200, sessionOf(user)), json(200, sessionOf(user)));
		await second.session.restore();
		const previousVersion = second.session.sessionVersion;

		await first.session.loginWithAuthorizationCode(loginInput);
		await second.session.syncLogin();

		expect(second.session.user?.id).toBe(user.id);
		expect(second.session.sessionVersion).toBe(previousVersion);
	});

	it('다시 조회하지 못하면 다음 기회에 한 번 더 확인한다', async () => {
		sharedStorage();
		const first = setup(json(200, loggedIn(other)));
		const second = setup(
			json(200, sessionOf(user)),
			json(503, errorBody('AUTH_UNAVAILABLE')),
			json(200, sessionOf(other))
		);
		await second.session.restore();
		await first.session.loginWithAuthorizationCode(loginInput);

		await second.session.syncLogin();
		expect(second.session.user?.id).toBe(user.id);
		await second.session.syncLogin();
		expect(second.session.user?.id).toBe(other.id);
	});

	it('내 정보 응답의 회원이 다르면 새 세대를 만든다', async () => {
		const { session } = setup(json(200, sessionOf(user)));
		await session.restore();
		const previousVersion = session.sessionVersion;

		session.updateUser({ ...user, weightKg: 51 } as never);
		expect(session.sessionVersion).toBe(previousVersion);
		session.updateUser(other as never);
		expect(session.user?.id).toBe(other.id);
		expect(session.sessionVersion).toBeGreaterThan(previousVersion);
	});
});
