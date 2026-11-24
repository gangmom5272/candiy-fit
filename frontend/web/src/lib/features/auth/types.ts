export const SOCIAL_TYPES = ['GOOGLE', 'KAKAO'] as const;
export const GENDERS = ['MALE', 'FEMALE'] as const;

export type SocialType = (typeof SOCIAL_TYPES)[number];
export type Gender = (typeof GENDERS)[number];

/** 명세 2.1 User */
export type User = {
	id: string;
	endUserId: string;
	displayName: string;
	birthYear: number;
	gender: Gender;
	heightCm: number;
	weightKg: number;
	socialType: SocialType;
	createdAt: string;
};

/**
 * 로그인·가입·세션 조회 성공 응답의 공통 부분(명세 3.3·3.4·3.7).
 * WEB 자격은 HttpOnly 세션 쿠키라 body에 없고, `expiresAt`은 7일 고정 만료 시각이다.
 */
export type AuthSessionData = {
	user: User;
	expiresAt: string;
};

export type SignupCredential = {
	signupToken: string;
	signupTokenExpiresAt: string;
	socialType: SocialType;
};

/** 명세 3.3 소셜 로그인 결과 */
export type SocialLoginResult =
	({ status: 'LOGGED_IN' } & AuthSessionData) | ({ status: 'SIGNUP_REQUIRED' } & SignupCredential);

/** 명세 3.3 WEB 요청. `platformType`은 API 함수가 채운다. */
export type WebSocialLoginInput = {
	socialType: SocialType;
	authorizationCode: string;
	codeVerifier: string;
	redirectUri: string;
};

/** 명세 3.4 회원가입 프로필 5개 필드 */
export type SignupProfile = {
	displayName: string;
	birthYear: number;
	gender: Gender;
	heightCm: number;
	weightKg: number;
};

/**
 * 세션 조회(명세 3.7)로 로그인 상태를 복원한 결과. 처리 기준은 docs/api-integration.md의 인증 오류 빠른 참조를 따른다.
 * 조회는 쿠키를 바꾸지 않으므로, 서버가 자격을 거절했다고 확인한 두 경우에만 로그인 상태를 지운다.
 * - `restored`: 로그인 상태 복원
 * - `no-session`: 401 `AUTH_REQUIRED`. 쿠키 없음(첫 방문)·무효·로그아웃한 세션
 * - `expired`: 401 `AUTH_EXPIRED`. 7일 고정 만료, 재로그인
 * - `unavailable`: 503·403·422·500, 응답 유실, 명세 형식이 아닌 응답. 로그인 상태를 지우지 않고 사용자가 다시 시도
 */
export type RestoreOutcome = 'restored' | 'no-session' | 'expired' | 'unavailable';

/**
 * 로그아웃이 204로 끝나지 않았을 때의 분류. 두 경우 모두 로그인 상태를 유지하고 자동 재전송하지 않는다.
 * - `failed`: 403·422, 서버가 폐기 전에 거절함
 * - `uncertain`: 응답 유실, 503·500, 명세 형식이 아닌 응답. 서버에서 폐기됐을 수 있음(같은 쿠키로 다시 로그아웃할 수 있다)
 */
export type LogoutFailure = 'failed' | 'uncertain';

export type AuthStatus = 'unknown' | 'authenticated' | 'unauthenticated';
