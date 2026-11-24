import { expect, test } from '@playwright/test';
import {
	LOGIN_API,
	e2eSession,
	enterOnboarding,
	mockSignedIn,
	openCallback,
	startLogin
} from '../../../lib/testing/e2e-auth';

test('로그인 화면에 제공자 버튼을 보여 준다', async ({ page }) => {
	await page.goto('/login', { waitUntil: 'networkidle' });

	await expect(page.getByRole('heading', { level: 1, name: '캔디핏 로그인' })).toBeVisible();
	await expect(page.getByRole('button')).toHaveText(['카카오로 시작하기', 'Google로 시작하기']);
	await expect(page.getByRole('button', { name: 'Google로 시작하기' })).toBeEnabled();
});

// 회원가입·마이페이지와 같은 캐릭터를 쓰고, 낮은 노트북 화면에서도 숨기지 않고 줄여서 세로 스크롤 없이 보인다 (S15P31A403-271)
for (const viewport of [
	{ width: 1440, height: 900 },
	{ width: 1366, height: 768 },
	{ width: 1280, height: 640 }
]) {
	test(`로그인 소개 패널에 마이페이지와 같은 캐릭터가 보인다 (${viewport.width}x${viewport.height})`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await page.goto('/login', { waitUntil: 'networkidle' });

		const intro = page.getByRole('region', { name: '캔디핏 소개' });
		const character = intro.locator('img[src*="character-profile"]');
		await expect(character).toBeVisible();
		const [panel, image] = await Promise.all([intro.boundingBox(), character.boundingBox()]);
		expect(image!.height).toBeGreaterThan(40);
		expect(image!.y + image!.height).toBeLessThanOrEqual(panel!.y + panel!.height);
		expect(
			await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)
		).toBe(true);
	});
}

// 카드가 화면 높이를 꽉 채우고(위아래 여백만), 어느 화면에서도 세로 스크롤이 생기지 않는다
for (const viewport of [
	{ width: 1920, height: 1080 },
	{ width: 1024, height: 768 },
	{ width: 768, height: 1024 },
	{ width: 375, height: 667 },
	{ width: 360, height: 640 }
]) {
	test(`로그인 카드가 화면을 꽉 채우고 스크롤이 없다 (${viewport.width}x${viewport.height})`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await page.goto('/login', { waitUntil: 'networkidle' });

		const card = page.locator('main > div').first();
		const box = (await card.boundingBox())!;
		expect(box.y).toBeLessThanOrEqual(32);
		expect(viewport.height - (box.y + box.height)).toBeLessThanOrEqual(32);
		expect(
			await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)
		).toBe(true);
	});
}

test('이미 로그인한 상태로 주소창에서 로그인 화면을 열면 홈으로 간다', async ({ page }) => {
	await mockSignedIn(page);
	await page.goto('/login');

	await expect(page).toHaveURL('/home');
	// 로그인 화면 기록을 홈으로 바꿔 넣었으므로 뒤로 가기로 로그인 화면에 돌아오지 않는다
	await page.goBack();
	expect(page.url()).not.toContain('/login');
});

test('세션 쿠키가 없으면 로그인 화면에 그대로 머문다', async ({ page }) => {
	let sessionCalls = 0;
	await page.route('**/api/v1/auth/session', (route) => {
		sessionCalls++;
		return route.fulfill({
			status: 401,
			json: { error: { code: 'AUTH_REQUIRED', message: '로그인이 필요해요.' } }
		});
	});
	await page.goto('/login', { waitUntil: 'networkidle' });

	await expect(page).toHaveURL('/login');
	await expect(page.getByRole('heading', { level: 1, name: '캔디핏 로그인' })).toBeVisible();
	expect(sessionCalls).toBe(1);
});

test('Google 인가 URL은 PKCE·state·openid scope를 담는다', async ({ page, baseURL }) => {
	const url = await startLogin(page, 'google');

	expect(url.pathname).toBe('/o/oauth2/v2/auth');
	expect(Object.fromEntries(url.searchParams)).toMatchObject({
		client_id: 'e2e-google-client',
		redirect_uri: `${baseURL}/auth/callback/google`,
		response_type: 'code',
		scope: 'openid',
		code_challenge_method: 'S256'
	});
	expect(url.searchParams.get('state')).toBeTruthy();
});

test('Kakao 인가 URL에는 scope가 없다', async ({ page, baseURL }) => {
	const url = await startLogin(page, 'kakao');

	expect(url.pathname).toBe('/oauth/authorize');
	expect(url.searchParams.get('client_id')).toBe('e2e-kakao-key');
	expect(url.searchParams.get('redirect_uri')).toBe(`${baseURL}/auth/callback/kakao`);
	expect(url.searchParams.has('scope')).toBe(false);
});

test('기존 회원은 콜백에서 로그인 후 홈으로 간다', async ({ page, baseURL }) => {
	const authorizeUrl = await startLogin(page, 'google');
	let body: Record<string, unknown> = {};
	await page.route(LOGIN_API, async (route) => {
		body = route.request().postDataJSON();
		await route.fulfill({
			json: {
				data: { status: 'LOGGED_IN', ...e2eSession }
			}
		});
	});

	await openCallback(page, 'google', {
		code: 'synthetic-code',
		state: authorizeUrl.searchParams.get('state') ?? ''
	});

	await expect(page).toHaveURL('/home');
	expect(body).toMatchObject({
		socialType: 'GOOGLE',
		platformType: 'WEB',
		authorizationCode: 'synthetic-code',
		redirectUri: `${baseURL}/auth/callback/google`
	});
	expect(body.codeVerifier).toMatch(/^[A-Za-z0-9\-._~]{43,128}$/);
});

test('가입이 필요한 계정은 회원가입 화면으로 가고 주소에 code·state가 남지 않는다', async ({
	page
}) => {
	await enterOnboarding(page);

	await expect(page).toHaveURL('/onboarding');
	await expect(page.getByRole('heading', { level: 1, name: '기본 정보 입력' })).toBeVisible();
});

test('로그인 API 오류는 요청 ID와 함께 보여 주고 같은 코드를 다시 보내지 않는다', async ({
	page
}) => {
	const authorizeUrl = await startLogin(page, 'google');
	let calls = 0;
	await page.route(LOGIN_API, (route) => {
		calls += 1;
		return route.fulfill({
			status: 400,
			headers: { 'X-Request-Id': 'req-e2e-1' },
			json: {
				error: { code: 'SOCIAL_CREDENTIAL_INVALID', message: '다시 로그인해 주세요.' }
			}
		});
	});
	const params = { code: 'synthetic-code', state: authorizeUrl.searchParams.get('state') ?? '' };

	await openCallback(page, 'google', params);
	const alert = page.getByRole('alert');
	await expect(alert).toContainText('다시 로그인해 주세요.');
	await expect(alert).toContainText('요청 ID: req-e2e-1');

	await openCallback(page, 'google', params);
	await expect(page.getByRole('alert')).toContainText('로그인 요청을 확인할 수 없어요.');
	expect(calls).toBe(1);
});

test('제공자에서 취소하면 로그인 API를 부르지 않는다', async ({ page }) => {
	let calls = 0;
	await page.route(LOGIN_API, (route) => {
		calls += 1;
		return route.abort();
	});

	await openCallback(page, 'google', { error: 'access_denied', state: 'synthetic' });

	await expect(page.getByRole('alert')).toHaveText('로그인을 취소했어요.');
	expect(calls).toBe(0);
});

test('지원하지 않는 제공자 콜백은 404다', async ({ page }) => {
	await page.goto('/auth/callback/naver', { waitUntil: 'networkidle' });

	await expect(page.getByText('404')).toBeVisible();
});

for (const provider of ['google', 'kakao'] as const) {
	for (const failure of ['cancelled', 'state-mismatch', 'expired', 'missing-code'] as const) {
		test(`${provider} ${failure} 콜백은 코드를 교환하지 않고 URL을 정리한다`, async ({ page }) => {
			await page.clock.install();
			const authorize = await startLogin(page, provider);
			await page.waitForURL(`https://${authorize.host}/**`);
			let loginCalls = 0;
			await page.route(LOGIN_API, (route) => {
				loginCalls += 1;
				return route.abort();
			});
			if (failure === 'expired') await page.clock.fastForward(11 * 60 * 1000);
			const params: Record<string, string> = {
				state:
					failure === 'state-mismatch' ? 'wrong-state' : (authorize.searchParams.get('state') ?? '')
			};
			if (failure === 'cancelled') params.error = 'access_denied';
			else if (failure !== 'missing-code') params.code = 'synthetic-code';
			await openCallback(page, provider, params);
			const message =
				failure === 'cancelled'
					? '로그인을 취소했어요.'
					: failure === 'missing-code'
						? '로그인 중 문제가 생겼어요.'
						: '로그인 요청을 확인할 수 없어요.';
			await expect(page.getByRole('alert')).toContainText(message);
			await expect(page).toHaveURL(`/auth/callback/${provider}`);
			expect(loginCalls).toBe(0);
		});
	}
}
