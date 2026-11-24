import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn, SESSION_API } from '../lib/testing/e2e-auth';
import { mockHomeApi } from '../lib/testing/home-api-fixture';

const missingPath = '/missing-logout-345';
const logoutApi = '**/api/v1/auth/logout';

async function openLogout(page: Page) {
	await page.getByRole('button', { name: '사용자 메뉴' }).click();
	const logout = page.getByRole('menuitem', { name: '로그아웃', exact: true });
	await expect(logout).toBeEnabled();
	return logout;
}

for (const width of [390, 768, 1366]) {
	test(`${width}px 404의 로그아웃은 세션을 정리하고 로그인으로 이동한다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 844 });
		await mockSignedIn(page);
		await mockHomeApi(page);
		let loggedOut = false;
		await page.route(logoutApi, (route) => {
			expect(route.request().method()).toBe('POST');
			expect(route.request().postData()).toBeNull();
			loggedOut = true;
			return route.fulfill({ status: 204 });
		});
		await page.route(SESSION_API, (route) =>
			loggedOut
				? route.fulfill({ status: 401, json: { error: { code: 'AUTH_REQUIRED' } } })
				: route.fallback()
		);
		if (width === 1366) {
			await page.goto('/health', { waitUntil: 'networkidle' });
			await page.evaluate((href) => {
				const link = document.createElement('a');
				link.href = href;
				document.body.append(link);
				link.click();
			}, missingPath);
		} else await page.goto(missingPath);
		await expect(page.locator('#error-title')).toHaveText('페이지를 찾을 수 없어요');
		await (await openLogout(page)).click();
		await expect(page).toHaveURL('/login');
		expect(loggedOut).toBe(true);
		for (const path of ['/home', '/health']) {
			await page.goto(path);
			await expect(page).toHaveURL('/login');
		}
	});
}

for (const status of [401, 503]) {
	test(`세션 조회 ${status}: 공개 404를 유지하고 로그아웃 요청을 보내지 않는다`, async ({
		page
	}) => {
		let calls = 0;
		await page.route(SESSION_API, (route) =>
			route.fulfill({
				status,
				json: { error: { code: status === 401 ? 'AUTH_REQUIRED' : 'AUTH_UNAVAILABLE' } }
			})
		);
		await page.route(logoutApi, (route) => {
			calls++;
			return route.fulfill({ status: 204 });
		});
		await page.goto(missingPath, { waitUntil: 'networkidle' });
		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		const logout = page.getByRole('menuitem', { name: '로그아웃' });
		await expect(logout).toHaveAttribute('aria-disabled', 'true');
		await logout.focus();
		await page.keyboard.press('Enter');
		expect(calls).toBe(0);
		await expect(page).toHaveURL(missingPath);
		await expect(page.locator('#error-title')).toHaveText('페이지를 찾을 수 없어요');
	});
}

for (const failure of ['rejected', 'unavailable', 'network'] as const) {
	test(`404 로그아웃 ${failure}: 기존 실패 안내·3초 표시·로그인 상태를 유지한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width: 390, height: 844 });
		await mockSignedIn(page);
		await mockHomeApi(page);
		let calls = 0;
		await page.route(logoutApi, (route) => {
			calls++;
			if (failure === 'network') return route.abort();
			return route.fulfill({
				status: failure === 'rejected' ? 403 : 503,
				json: {
					error: { code: failure === 'rejected' ? 'ORIGIN_NOT_ALLOWED' : 'AUTH_UNAVAILABLE' }
				}
			});
		});
		await page.goto(missingPath, { waitUntil: 'networkidle' });
		await page.clock.pauseAt(new Date());
		await (await openLogout(page)).click();
		const toast = page.getByRole('alert');
		await expect(toast).toHaveText(
			failure === 'rejected'
				? '로그아웃하지 못했어요. 잠시 후 다시 시도해 주세요.'
				: '로그아웃 완료 여부를 확인하지 못했어요. 잠시 후 다시 확인해 주세요.'
		);
		const bounds = await toast.boundingBox();
		const nav = await page.getByRole('navigation').boundingBox();
		if (!bounds || !nav) throw new Error('실패 안내와 하단 메뉴가 없습니다.');
		expect(bounds.y + bounds.height).toBeLessThan(nav.y);
		await expect(page).toHaveURL(missingPath);
		await page.clock.runFor(2999);
		await expect(toast).toBeVisible();
		await page.clock.runFor(1);
		await expect(toast).toBeHidden();
		expect(calls).toBe(1);
		await page.clock.resume();
		await page.getByRole('link', { name: '홈으로 가기', exact: true }).click();
		await expect(page).toHaveURL('/home');
	});
}

test('404에서 로그아웃 처리 중 메뉴를 다시 눌러도 요청은 한 번만 보낸다', async ({ page }) => {
	await mockSignedIn(page);
	let calls = 0;
	let release: () => void = () => {};
	const pending = new Promise<void>((resolve) => (release = resolve));
	await page.route(logoutApi, async (route) => {
		calls++;
		await pending;
		await route.fulfill({ status: 204 });
	});
	await page.goto(missingPath, { waitUntil: 'networkidle' });
	await (await openLogout(page)).click();
	await expect.poll(() => calls).toBe(1);
	await (await openLogout(page)).click();
	expect(calls).toBe(1);
	release();
	await expect(page).toHaveURL('/login');
});
