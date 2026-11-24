import type { Page } from '@playwright/test';

export const LOGIN_API = '**/api/v1/auth/social/login';
export const SESSION_API = '**/api/v1/auth/session';

/** e2e 전용 합성 회원. 실제 계정·토큰이 아니다. */
export const e2eUser = {
	id: '6ef7d160-428a-4bd3-86ca-08f084390dea',
	endUserId: '6ef7d160-428a-4bd3-86ca-08f084390dea',
	displayName: '테스트',
	birthYear: 2000,
	gender: 'FEMALE',
	heightCm: 160,
	weightKg: 50,
	socialType: 'GOOGLE',
	createdAt: '2026-10-19T00:00:00Z'
};

/** 로그인·가입·세션 조회 성공 응답의 공통 부분(명세 3.3·3.4·3.7). 웹 자격은 쿠키라 body에 토큰이 없다. */
export const e2eSession = {
	user: e2eUser,
	expiresAt: '2099-01-01T00:00:00Z'
};

const PROVIDERS = {
	google: { button: 'Google로 시작하기', host: 'accounts.google.com' },
	kakao: { button: '카카오로 시작하기', host: 'kauth.kakao.com' }
} as const;

/** (app) 화면은 새로고침 때 세션 조회(명세 3.7)로 로그인을 복원하므로, 로그인된 상태로 열려면 응답을 대신 준다. */
export async function mockHealthSync(page: Page) {
	await page.route('**/api/v1/health/sync', (route) =>
		route.fulfill({ json: { data: { status: 'SYNCED' } } })
	);
}
export async function mockSignedIn(page: Page) {
	await mockHealthSync(page);
	await page.route(SESSION_API, (route) => route.fulfill({ json: { data: e2eSession } }));
}

/** 로그인 버튼을 눌러 제공자 인가 URL을 받는다. 제공자 화면은 띄우지 않는다. */
export async function startLogin(page: Page, provider: keyof typeof PROVIDERS): Promise<URL> {
	const { button, host } = PROVIDERS[provider];
	await page.route(`https://${host}/**`, (route) =>
		route.fulfill({ contentType: 'text/html', body: '<title>provider</title>' })
	);
	await page.goto('/login', { waitUntil: 'networkidle' });
	const request = page.waitForRequest(`https://${host}/**`);
	await page.getByRole('button', { name: button }).click();
	return new URL((await request).url());
}

export async function openCallback(page: Page, provider: string, params: Record<string, string>) {
	await page.goto(`/auth/callback/${provider}?${new URLSearchParams(params)}`);
}

/** 제공자 로그인을 마친 것처럼 콜백을 열어 가입 필요 상태로 /onboarding에 들어간다. */
export async function enterOnboarding(page: Page) {
	const authorizeUrl = await startLogin(page, 'kakao');
	await page.route(LOGIN_API, (route) =>
		route.fulfill({
			json: {
				data: {
					status: 'SIGNUP_REQUIRED',
					signupToken: 'synthetic-signup',
					signupTokenExpiresAt: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
					socialType: 'KAKAO'
				}
			}
		})
	);
	await openCallback(page, 'kakao', {
		code: 'synthetic-code',
		state: authorizeUrl.searchParams.get('state') ?? ''
	});
}
