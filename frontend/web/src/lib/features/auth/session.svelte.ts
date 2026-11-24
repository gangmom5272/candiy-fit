import { api, type ApiClient, type AuthHandler } from '#lib/api/client.js';
import { ApiError, isApiError } from '#lib/api/errors.js';
import * as authApi from './api';
import {
	getLoginVersion,
	getLogoutVersion,
	listenForSessionChange,
	publishLogin,
	publishLogout
} from './logout-sync';
import type {
	AuthSessionData,
	AuthStatus,
	LogoutFailure,
	RestoreOutcome,
	SignupCredential,
	SignupProfile,
	SocialLoginResult,
	User,
	WebSocialLoginInput
} from './types';

export type SignupOutcome = 'signed-up' | 'login-required';

/**
 * 명세 3.7 세션 조회 실패 분류. 서버가 자격을 거절했다고 확인한 401 두 코드만 로그인 상태를 지운다.
 * 조회는 쿠키를 바꾸지 않으므로 503·응답 유실·명세 형식이 아닌 응답(proxy 오류 등)은 자격이 그대로다.
 */
function toRestoreFailure(error: unknown): Exclude<RestoreOutcome, 'restored'> {
	if (isApiError(error, 'AUTH_REQUIRED')) return 'no-session';
	if (isApiError(error, 'AUTH_EXPIRED')) return 'expired';
	return 'unavailable';
}

/** 명세 3.6에서 세션을 폐기하기 전에 거절하는 오류 */
const LOGOUT_FAILURE_CODES = ['ORIGIN_NOT_ALLOWED', 'INVALID_REQUEST'];

/**
 * 로그아웃의 503·500은 폐기 커밋 중에도 날 수 있고 `retryable`도 오지 않아 완료 여부를 알 수 없다.
 * 응답 유실과 명세 형식이 아닌 응답도 같다.
 */
export function toLogoutFailure(error: unknown): LogoutFailure {
	if (error instanceof ApiError && error.code && LOGOUT_FAILURE_CODES.includes(error.code)) {
		return 'failed';
	}
	return 'uncertain';
}

/**
 * 웹 로그인 상태(명세 1.4). 서비스 자격은 HttpOnly 세션 쿠키라 JS는 회원 정보와 상태만 갖고,
 * 가입 자격은 메모리에만 둔다. client에는 AuthHandler로 연결되어 세션 만료(`AUTH_EXPIRED`)를 받는다.
 */
export class AuthSession implements AuthHandler {
	status = $state<AuthStatus>('unknown');
	user = $state<User | null>(null);

	#client: ApiClient;
	#signupCredential: SignupCredential | null = null;
	#restoring: Promise<RestoreOutcome> | null = null;
	#revision = 0;
	#sessionVersion = $state(0);
	#logoutVersion = getLogoutVersion();
	#loginVersion = getLoginVersion();
	#syncingLogin: Promise<void> | null = null;

	constructor(client: ApiClient) {
		this.#client = client;
	}

	/** 같은 회원으로 다시 로그인해도 이전 세션의 화면 상태와 진행 요청을 공유하지 않는다. */
	get sessionVersion(): number {
		return this.#sessionVersion;
	}

	/** 일반 API가 401 `AUTH_EXPIRED`를 받았을 때. 웹 세션은 갱신이 없으므로 로그인 상태를 정리해 재로그인하게 한다. */
	handleSessionExpired(): void {
		this.#clearSession();
	}

	/** 새로고침 후 첫 호출. 세션 쿠키로 로그인 상태를 복원한다. 같은 탭의 동시 호출은 요청 하나로 합친다. */
	restore(): Promise<RestoreOutcome> {
		this.syncLogout();
		if (!this.#restoring) {
			const restoring = this.#runRestore().finally(() => {
				if (this.#restoring === restoring) this.#restoring = null;
			});
			this.#restoring = restoring;
		}
		return this.#restoring;
	}

	async loginWithAuthorizationCode(
		input: WebSocialLoginInput
	): Promise<SocialLoginResult['status']> {
		this.syncLogout();
		const revision = ++this.#revision;
		const result = await authApi.loginWithAuthorizationCode(input, this.#client, () =>
			this.#assertCurrent(revision)
		);
		this.#assertCurrent(revision);

		if (result.status === 'LOGGED_IN') {
			this.#applySession(result, true);
			this.#announceLogin();
		} else {
			this.#signupCredential = {
				signupToken: result.signupToken,
				signupTokenExpiresAt: result.signupTokenExpiresAt,
				socialType: result.socialType
			};
		}
		return result.status;
	}

	/** 만료되지 않은 가입 자격이 메모리에 있을 때만 돌려준다. */
	getSignupCredential(now: number = Date.now()): SignupCredential | null {
		this.syncLogout();
		const credential = this.#signupCredential;
		if (credential && Date.parse(credential.signupTokenExpiresAt) <= now) {
			this.#signupCredential = null;
			return null;
		}
		return credential;
	}

	/**
	 * 명세 3.4 회원가입. 422는 자격이 남아 있으므로 ApiError를 그대로 던져 화면에서 details를 표시한다.
	 * 자격이 없거나 무효이거나 응답을 잃으면 `login-required`로 소셜 로그인부터 다시 한다.
	 */
	async signup(profile: SignupProfile): Promise<SignupOutcome> {
		this.syncLogout();
		const credential = this.getSignupCredential();
		if (!credential) return 'login-required';
		const revision = ++this.#revision;

		try {
			const result = await authApi.signup(credential.signupToken, profile, this.#client, () =>
				this.#assertCurrent(revision)
			);
			if (!this.#isCurrent(revision)) return 'login-required';
			this.#applySession(result, true);
			this.#announceLogin();
			this.#signupCredential = null;
			return 'signed-up';
		} catch (error) {
			if (!this.#isCurrent(revision)) return 'login-required';
			if (error instanceof ApiError && error.code !== 'SIGNUP_TOKEN_INVALID') throw error;
			this.#signupCredential = null;
			return 'login-required';
		}
	}

	/**
	 * 내 정보 조회·수정(명세 4.1·4.2) 응답으로 로그인 상태의 사용자 정보를 맞춘다.
	 * 응답의 회원이 메모리의 회원과 다르면 다른 탭에서 계정이 바뀐 것이다(S15P31A403-426). 새 세션으로 보고 화면을 다시 만들게 한다
	 */
	updateUser(user: User) {
		if (this.status !== 'authenticated') return;
		if (this.user && this.user.id !== user.id) this.#sessionVersion++;
		this.user = user;
	}

	/**
	 * 서버가 세션 자격을 무효로 거절했을 때(AUTH_REQUIRED, 명세 1.3 "로그인 안내").
	 * 서버 로그아웃은 하지 않고 메모리의 로그인 상태만 지워 AuthGate가 /login으로 보내게 한다.
	 */
	clearLocalSession(): void {
		this.#clearSession();
	}

	/** 204를 받은 뒤에만 메모리 상태를 지운다. 그 외는 그대로 던지며 `toLogoutFailure`로 분류한다. */
	async logout(): Promise<void> {
		await authApi.logout(this.#client, () => {
			publishLogout();
			this.#clearSession();
		});
	}

	/** 이벤트가 늦게 와도 과거 이벤트 값 대신 최신 공유 상태를 확인한다. */
	syncLogout(): void {
		if (this.#logoutVersion !== getLogoutVersion()) this.#clearSession();
	}

	/**
	 * 다른 탭이 로그인·가입해 세션 쿠키가 바뀌었으면 서버에 세션을 다시 묻는다(S15P31A403-426).
	 * 회원이 달라졌으면 세션 세대가 올라가 화면이 새 회원으로 다시 만들어진다. 같은 회원이면 화면은 그대로다.
	 * 조회에 실패하면 확인한 번호를 올리지 않아, 다음 이벤트나 탭이 다시 보일 때 한 번 더 확인한다
	 */
	syncLogin(): Promise<void> {
		const version = getLoginVersion();
		if (version === this.#loginVersion) return Promise.resolve();
		if (!this.#syncingLogin) {
			// 쿠키가 바뀌기 전에 시작한 조회는 옛 회원을 돌려줄 수 있어, 끝나기를 기다린 뒤 새로 묻는다
			const before = this.#restoring;
			const syncing = (before ? before.then(() => this.restore()) : this.restore())
				.then((outcome) => {
					if (outcome !== 'unavailable') this.#loginVersion = version;
				})
				.finally(() => {
					if (this.#syncingLogin === syncing) this.#syncingLogin = null;
				});
			this.#syncingLogin = syncing;
		}
		return this.#syncingLogin;
	}

	/** 이 탭의 로그인·가입을 다른 탭에 알린다. 이 탭은 방금 받은 회원이 최신이라 다시 묻지 않는다 */
	#announceLogin(): void {
		publishLogin();
		this.#loginVersion = getLoginVersion();
	}

	#isCurrent(revision: number): boolean {
		this.syncLogout();
		return revision === this.#revision;
	}

	/** 잠금 대기 중 로그아웃됐다면 서버가 새 쿠키를 발급하기 전에 취소한다. */
	#assertCurrent(revision: number): void {
		if (!this.#isCurrent(revision))
			throw new Error('로그인 상태가 변경되었습니다. 다시 로그인해 주세요.');
	}

	async #runRestore(): Promise<RestoreOutcome> {
		const revision = this.#revision;
		try {
			const result = await authApi.getSession(this.#client);
			// 조회 중 로그아웃·로그인으로 상태가 바뀌었으면 늦게 온 응답으로 덮어쓰지 않는다
			if (!this.#isCurrent(revision)) return 'no-session';
			this.#applySession(result);
			return 'restored';
		} catch (error) {
			if (!this.#isCurrent(revision)) return 'no-session';
			const outcome = toRestoreFailure(error);
			if (outcome !== 'unavailable') this.#clearSession();
			return outcome;
		}
	}

	#applySession(data: AuthSessionData, newSession = false) {
		if (newSession || this.status !== 'authenticated' || this.user?.id !== data.user.id)
			this.#sessionVersion++;
		this.user = data.user;
		this.status = 'authenticated';
	}

	#clearSession() {
		this.#sessionVersion++;
		this.#revision += 1;
		this.#logoutVersion = getLogoutVersion();
		this.#restoring = null;
		this.#signupCredential = null;
		this.user = null;
		this.status = 'unauthenticated';
	}
}

export const authSession = new AuthSession(api);
api.setAuthHandler(authSession);

if (typeof window !== 'undefined') {
	const stopListening = listenForSessionChange(() => {
		authSession.syncLogout();
		void authSession.syncLogin();
	});
	import.meta.hot?.dispose(stopListening);
}
