import { expect, test } from '@playwright/test';
import { e2eSession, mockSignedIn } from '../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../lib/testing/home-api-fixture';

test.beforeEach(({ page }) => mockHomeApi(page));

const SESSION_API = '**/api/v1/auth/session';
const LOGOUT_API = '**/api/v1/auth/logout';

test('세션이 만료됐으면(AUTH_EXPIRED) 다시 보내지 않고 로그인으로 돌아간다', async ({ page }) => {
	let calls = 0;
	await page.route(SESSION_API, (route) => {
		calls += 1;
		return route.fulfill({
			status: 401,
			json: { error: { code: 'AUTH_EXPIRED', message: '로그인이 필요합니다.' } }
		});
	});
	await page.goto('/home');
	await expect(page).toHaveURL('/login');
	await expect(page.getByRole('heading', { name: '캔디핏 로그인' })).toBeVisible();
	expect(calls).toBe(1);
});

test('세션 조회 응답을 받지 못해도 로그아웃하지 않고 다시 시도할 수 있다', async ({ page }) => {
	let calls = 0;
	await page.route(SESSION_API, (route) => {
		calls += 1;
		return calls === 1 ? route.abort() : route.fulfill({ json: { data: e2eSession } });
	});
	await page.goto('/home');
	await expect(
		page.getByRole('heading', { name: '로그인 상태를 확인하지 못했어요' })
	).toBeVisible();
	await expect(page).toHaveURL('/home');

	await page.getByRole('button', { name: '다시 시도' }).click();
	await expect(page.getByRole('heading', { level: 1, name: '오늘의 점수' })).toBeVisible();
	expect(calls).toBe(2);
});

test('로그인 기록이 없으면 앱 화면 대신 로그인으로 보낸다', async ({ page }) => {
	await page.route(SESSION_API, (route) =>
		route.fulfill({
			status: 401,
			json: { error: { code: 'AUTH_REQUIRED', message: '로그인이 필요해요.' } }
		})
	);

	await page.goto('/home');

	await expect(page).toHaveURL('/login');
	await expect(page.getByRole('banner')).toHaveCount(0);
});

test('새로고침하면 세션 쿠키로 로그인을 복원한다', async ({ page }) => {
	let method = 'not-called';
	let requestBody: string | null = 'not-called';
	await page.route(SESSION_API, (route) => {
		method = route.request().method();
		requestBody = route.request().postData();
		return route.fulfill({ json: { data: e2eSession } });
	});

	await page.goto('/health');

	await expect(page.getByRole('heading', { level: 1, name: '건강 데이터' })).toBeVisible();
	expect(method).toBe('GET');
	expect(requestBody).toBeNull();
});

test('연속으로 새로고침해도 세션 조회만 하고 로그인 상태를 유지한다', async ({ page }) => {
	let sessionCalls = 0;
	let refreshCalls = 0;
	await page.route(SESSION_API, (route) => {
		sessionCalls += 1;
		return route.fulfill({ json: { data: e2eSession } });
	});
	page.on('request', (request) => {
		if (request.url().endsWith('/api/v1/auth/refresh')) refreshCalls += 1;
	});
	await page.goto('/home');
	// 응답을 기다리지 않고 이어서 새로고침한다(S15P31A403-325 재현 조건)
	for (let i = 0; i < 5; i++) await page.reload({ waitUntil: 'commit' });

	await expect(page.getByRole('heading', { level: 1, name: '오늘의 점수' })).toBeVisible();
	await expect(page).toHaveURL('/home');
	expect(sessionCalls).toBeGreaterThanOrEqual(1);
	expect(refreshCalls).toBe(0);
});

test('세션 조회가 일시 장애(503)면 로그아웃하지 않고 다시 시도할 수 있다', async ({ page }) => {
	let calls = 0;
	await page.route(SESSION_API, (route) => {
		calls += 1;
		return calls === 1
			? route.fulfill({
					status: 503,
					json: {
						error: { code: 'AUTH_UNAVAILABLE', message: '잠시 후 다시', retryable: true }
					}
				})
			: route.fulfill({ json: { data: e2eSession } });
	});

	await page.goto('/home');
	await expect(
		page.getByRole('heading', { name: '로그인 상태를 확인하지 못했어요' })
	).toBeVisible();
	await expect(page).toHaveURL('/home');

	await page.getByRole('button', { name: '다시 시도' }).click();
	await expect(page.getByRole('heading', { level: 1, name: '오늘의 점수' })).toBeVisible();
	expect(calls).toBe(2);
});

test('Origin 거절(403)이 계속되면 로그인 화면으로 갈 수 있다', async ({ page }) => {
	await page.route(SESSION_API, (route) =>
		route.fulfill({
			status: 403,
			json: { error: { code: 'ORIGIN_NOT_ALLOWED', message: '요청 출처를 확인해 주세요.' } }
		})
	);

	await page.goto('/home');
	await expect(
		page.getByRole('heading', { name: '로그인 상태를 확인하지 못했어요' })
	).toBeVisible();

	await page.getByRole('link', { name: '로그인 화면으로' }).click();
	await expect(page).toHaveURL('/login');
});

test('헤더의 로그아웃은 서버 로그아웃 후 로그인 화면으로 보낸다', async ({ page }) => {
	await mockSignedIn(page);
	let logoutBody: string | null = 'not-called';
	await page.route(LOGOUT_API, (route) => {
		logoutBody = route.request().postData();
		return route.fulfill({ status: 204 });
	});
	await page.goto('/home');

	await page.getByRole('button', { name: '사용자 메뉴' }).click();
	await page.getByRole('menuitem', { name: '로그아웃' }).click();

	await expect(page).toHaveURL('/login');
	expect(logoutBody).toBeNull();
});

test('로그아웃한 뒤에는 보호 화면을 다시 열거나 뒤로 가도 로그인으로 보낸다', async ({ page }) => {
	await mockSignedIn(page);
	// 서버는 로그아웃한 세션 쿠키를 폐기한다. 그 뒤의 세션 조회는 401 AUTH_REQUIRED다(명세 3.7)
	let isLoggedOut = false;
	await page.route(LOGOUT_API, (route) => {
		isLoggedOut = true;
		return route.fulfill({ status: 204 });
	});
	await page.route(SESSION_API, (route) =>
		isLoggedOut
			? route.fulfill({
					status: 401,
					json: { error: { code: 'AUTH_REQUIRED', message: '로그인이 필요해요.' } }
				})
			: route.fallback()
	);
	await page.goto('/home');
	await page.goto('/health');
	await page.getByRole('button', { name: '사용자 메뉴' }).click();
	await page.getByRole('menuitem', { name: '로그아웃' }).click();
	await expect(page).toHaveURL('/login');

	// 뒤로 가기: 로그아웃한 화면은 기록에서 로그인으로 바뀌어 있고, 그 앞의 보호 화면으로 가도 막는다
	await page.goBack();
	await expect(page).toHaveURL('/login');
	await expect(page.getByRole('banner')).toHaveCount(0);

	// 주소로 직접 열기: 세션 조회가 거절돼 로그인으로 간다
	for (const path of ['/home', '/health', '/coaching', '/my']) {
		await page.goto(path);
		await expect(page).toHaveURL('/login');
		await expect(page.getByRole('banner')).toHaveCount(0);
	}
});

test('로그아웃 실패는 로그인 상태를 유지하고 토스트로 잠시 안내한다', async ({ page }) => {
	await mockSignedIn(page);
	let calls = 0;
	await page.route(LOGOUT_API, (route) => {
		calls += 1;
		return route.fulfill({
			status: 403,
			json: { error: { code: 'ORIGIN_NOT_ALLOWED', message: '요청 출처를 확인해 주세요.' } }
		});
	});
	await page.goto('/home');

	await page.getByRole('button', { name: '사용자 메뉴' }).click();
	await page.getByRole('menuitem', { name: '로그아웃' }).click();

	const toast = page.getByRole('alert');
	await expect(toast).toHaveText('로그아웃하지 못했어요. 잠시 후 다시 시도해 주세요.');
	// 페이지 영역을 밀어내지 않는 고정 위치 안내다
	await expect(toast.locator('..')).toHaveCSS('position', 'fixed');
	await expect(page).toHaveURL('/home');
	// 3초 뒤 사라진다
	await expect(toast).toBeHidden({ timeout: 5000 });
	expect(calls).toBe(1);
});

for (const [width, height] of [
	[360, 800],
	[390, 844],
	[767, 1024],
	[768, 1024],
	[1024, 768],
	[1366, 768]
]) {
	test(`${width}px에서 로그아웃 토스트는 메뉴를 가리지 않고 3초 동안 표시된다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height });
		await mockSignedIn(page);
		await page.route(LOGOUT_API, (route) =>
			route.fulfill({
				status: 403,
				json: { error: { code: 'ORIGIN_NOT_ALLOWED', message: '요청 출처를 확인해 주세요.' } }
			})
		);
		await page.goto('/home');
		await page.evaluate(() => document.fonts.ready);
		await page.clock.pauseAt(new Date('2026-10-26T03:01:00Z'));
		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		await page.getByRole('menuitem', { name: '로그아웃' }).click();
		const toast = page.getByRole('alert');
		const navigation = page.getByRole('navigation', { name: '주 메뉴' });
		await expect(toast).toHaveText('로그아웃하지 못했어요. 잠시 후 다시 시도해 주세요.');
		await expect(toast.locator('..')).toHaveCSS('bottom', width < 768 ? '112px' : '32px');
		const toastBounds = await toast.boundingBox();
		const navBounds = await navigation.boundingBox();
		if (!toastBounds || !navBounds) throw new Error('토스트와 메뉴 위치를 확인할 수 없습니다.');
		expect(toastBounds.x).toBeGreaterThanOrEqual(0);
		expect(toastBounds.x + toastBounds.width).toBeLessThanOrEqual(width);
		expect(toastBounds.y).toBeGreaterThanOrEqual(0);
		if (width < 768) {
			expect(navBounds.y - (toastBounds.y + toastBounds.height)).toBeCloseTo(32, 0);
		} else {
			expect(height - (toastBounds.y + toastBounds.height)).toBeCloseTo(32, 0);
		}
		await testInfo.attach('toast-position', {
			body: JSON.stringify({ width, height, toast: toastBounds, navigation: navBounds }),
			contentType: 'application/json'
		});
		await page.clock.runFor(2999);
		await expect(toast).toBeVisible();
		await page.clock.runFor(1);
		await expect(toast).toBeHidden();
		// 화면 이동에 사용하는 animation frame도 다시 흐르게 한 뒤 클릭을 확인한다.
		await page.clock.resume();
		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		await page.getByRole('menuitem', { name: '로그아웃' }).click();
		// 토스트가 떠 있는 동안에도 세 메뉴의 실제 링크 이동이 가능하다.
		for (const [name, path] of [
			['건강 데이터', '/health'],
			['AI 코치', '/coaching'],
			['홈', '/home']
		]) {
			await expect(toast).toBeVisible();
			await navigation.getByRole('link', { name, exact: true }).click();
			await expect(page).toHaveURL(path);
		}
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
			width
		);
	});
}

for (const failure of ['network', 'unavailable'] as const) {
	test(`로그아웃 ${failure} 오류는 완료 여부 미확인으로 안내하고 다시 보내지 않는다`, async ({
		page
	}) => {
		await mockSignedIn(page);
		let calls = 0;
		await page.route(LOGOUT_API, (route) => {
			calls += 1;
			if (failure === 'network') return route.abort();
			return route.fulfill({
				status: 503,
				json: { error: { code: 'AUTH_UNAVAILABLE', message: '잠시 후 다시 시도해 주세요.' } }
			});
		});
		await page.goto('/home');

		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		await page.getByRole('menuitem', { name: '로그아웃' }).click();

		const toast = page.getByRole('alert');
		await expect(toast).toHaveText(
			'로그아웃 완료 여부를 확인하지 못했어요. 잠시 후 다시 확인해 주세요.'
		);
		await expect(page).toHaveURL('/home');
		await expect(page.getByRole('banner')).toBeVisible();
		await expect(toast).toBeHidden({ timeout: 5000 });
		expect(calls).toBe(1);

		// 자동 재전송은 없고, 사용자가 다시 누르면 한 번 더 보낸다
		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		await page.getByRole('menuitem', { name: '로그아웃' }).click();
		await expect(toast).toBeVisible();
		expect(calls).toBe(2);
	});
}

test('로그아웃은 같은 브라우저의 다른 탭에 전파되고 별도 세션은 유지된다', async ({
	page,
	context,
	browser,
	baseURL
}) => {
	await context.route(SESSION_API, (route) => route.fulfill({ json: { data: e2eSession } }));
	let logoutCalls = 0;
	await context.route(LOGOUT_API, (route) => {
		logoutCalls++;
		return route.fulfill({ status: 204 });
	});
	const other = await context.newPage();
	await mockHomeApi(other);
	const separate = await browser.newContext({ baseURL });
	try {
		const separatePage = await separate.newPage();
		await mockHomeApi(separatePage);
		await mockSignedIn(separatePage);
		await Promise.all([page.goto('/home'), other.goto('/home'), separatePage.goto('/home')]);
		for (const tab of [page, other, separatePage])
			await expect(tab.getByRole('banner')).toBeVisible();
		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		await page.getByRole('menuitem', { name: '로그아웃' }).click();
		await expect(page).toHaveURL('/login');
		await expect(other).toHaveURL('/login');
		await expect(other.getByRole('banner')).toHaveCount(0);
		await expect(separatePage).toHaveURL('/home');
		await expect(separatePage.getByRole('banner')).toBeVisible();
		expect(logoutCalls).toBe(1);
	} finally {
		await separate.close();
	}
});

test('실패한 로그아웃은 다른 탭을 로그아웃시키지 않는다', async ({ page, context }) => {
	await context.route(SESSION_API, (route) => route.fulfill({ json: { data: e2eSession } }));
	await context.route(LOGOUT_API, (route) =>
		route.fulfill({
			status: 503,
			json: { error: { code: 'AUTH_UNAVAILABLE', message: '잠시 후 다시' } }
		})
	);
	const other = await context.newPage();
	await mockHomeApi(other);
	await Promise.all([page.goto('/home'), other.goto('/home')]);
	await expect(other.getByRole('banner')).toBeVisible();
	await page.getByRole('button', { name: '사용자 메뉴' }).click();
	await page.getByRole('menuitem', { name: '로그아웃' }).click();
	await expect(page.getByRole('alert')).toBeVisible();
	await expect(other).toHaveURL('/home');
	await expect(other.getByRole('banner')).toBeVisible();
});

test('로그아웃 중 다른 탭이 세션을 복원해도 로그아웃이 끝나면 그 탭도 로그인으로 보낸다', async ({
	page,
	context
}) => {
	await context.route(SESSION_API, (route) => route.fulfill({ json: { data: e2eSession } }));
	let release!: () => void;
	let started!: () => void;
	const blocked = new Promise<void>((resolve) => {
		release = resolve;
	});
	const requestStarted = new Promise<void>((resolve) => {
		started = resolve;
	});
	await context.route(LOGOUT_API, async (route) => {
		started();
		await blocked;
		await route.fulfill({ status: 204 });
	});
	try {
		await page.goto('/home');
		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		await page.getByRole('menuitem', { name: '로그아웃' }).click();
		await requestStarted;
		// 세션 조회는 쿠키를 바꾸지 않아 잠금 대상이 아니다(명세 1.5). 로그아웃이 끝나기 전에는 복원된다
		const other = await context.newPage();
		await mockHomeApi(other);
		await other.goto('/home');
		await expect(other.getByRole('banner')).toBeVisible();
		release();
		await expect(page).toHaveURL('/login');
		await expect(other).toHaveURL('/login');
	} finally {
		release();
	}
});
