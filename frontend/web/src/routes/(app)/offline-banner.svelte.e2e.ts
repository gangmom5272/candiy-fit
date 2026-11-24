import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../lib/testing/home-api-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
});

for (const width of [1920, 1366, 768, 360]) {
	test(`${width}px에서 실제 오프라인 전환과 복구가 화면·데이터를 유지한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height: width === 1920 ? 1080 : 900 });
		let requests = 0;
		page.on('request', (request) => {
			if (new URL(request.url()).pathname.startsWith('/api/')) requests++;
		});
		await page.goto('/home');
		await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
		const cards = page.getByRole('region', { name: '최근 건강 기록' });
		await expect(cards.locator('.metric-value')).toHaveText([
			'3,200보',
			'18분',
			'7시간 0분',
			'64.5 bpm'
		]);
		const before = await cards.textContent();
		const main = await page.getByRole('main').elementHandle();
		if (!main) throw new Error('앱 화면이 표시되지 않았습니다.');
		const requestsBefore = requests;
		const banner = page.getByRole('status', { name: '네트워크 연결 상태' });
		await expect(banner).toHaveCount(0);

		await page.context().setOffline(true);
		await expect(banner).toBeVisible();
		await expect(banner).toHaveCount(1);
		expect(await page.evaluate(() => navigator.onLine)).toBe(false);
		await expect(page).toHaveURL('/home');
		await expect(cards).toHaveText(before ?? '');
		await expect(page.getByRole('banner')).toHaveCount(1);
		expect(await main.evaluate((element) => element.isConnected)).toBe(true);
		const headerBox = await page.getByRole('banner').boundingBox();
		const bannerBox = await banner.boundingBox();
		const mainBox = await page.getByRole('main').boundingBox();
		if (!headerBox || !bannerBox || !mainBox) throw new Error('화면 영역을 확인할 수 없습니다.');
		expect(bannerBox.y).toBeGreaterThanOrEqual(headerBox.y + headerBox.height);
		expect(mainBox.y).toBeGreaterThanOrEqual(bannerBox.y + bannerBox.height);
		expect(
			await banner
				.locator('p')
				.evaluateAll((elements) =>
					elements.every((element) => element.scrollWidth <= element.clientWidth + 1)
				)
		).toBe(true);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
		await page.screenshot({ path: testInfo.outputPath('offline.png'), fullPage: true });

		await page.context().setOffline(false);
		await expect(banner).toHaveCount(0);
		expect(await page.evaluate(() => navigator.onLine)).toBe(true);
		await expect(cards).toHaveText(before ?? '');
		expect(await main.evaluate((element) => element.isConnected)).toBe(true);
		expect(requests).toBe(requestsBefore);
	});
}

test('Health·AI Coach에서 배너를 공유하고 초안 유지 및 404 중첩 방지를 확인한다', async ({
	page
}) => {
	await page.route('**/api/v1/coaching/conversations*', (route) =>
		route.fulfill({ json: { data: { items: [], nextCursor: null } } })
	);
	await page.goto('/health');
	await expect(page.getByRole('heading', { name: '건강 데이터', exact: true })).toBeVisible();
	const banner = page.getByRole('status', { name: '네트워크 연결 상태' });
	await page.context().setOffline(true);
	await expect(banner).toHaveCount(1);
	await page.context().setOffline(false);
	await expect(banner).toHaveCount(0);

	await page
		.getByRole('navigation', { name: '주 메뉴' })
		.getByRole('link', { name: 'AI 코치', exact: true })
		.click();
	const input = page.getByRole('textbox', { name: '질문 입력' });
	await input.fill('수면 기록을 확인하고 싶어요.');
	await page.context().setOffline(true);
	await expect(banner).toHaveCount(1);
	await expect(input).toHaveValue('수면 기록을 확인하고 싶어요.');
	await page.context().setOffline(false);
	await expect(banner).toHaveCount(0);
	await expect(input).toHaveValue('수면 기록을 확인하고 싶어요.');

	const response = await page.goto('/this-page-does-not-exist');
	expect(response?.status()).toBe(404);
	await expect(page.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
	await page.context().setOffline(true);
	await expect(banner).toHaveCount(0);
	await expect(page.getByRole('banner')).toHaveCount(1);
	await page.context().setOffline(false);
});

test('브라우저가 온라인이면 일반 HTTP 오류를 오프라인으로 표시하지 않는다', async ({ page }) => {
	let status = 400;
	await page.route('**/api/v1/health/lifestyle-score?*', (route) =>
		route.fulfill({
			status,
			json: { error: { code: 'TEST_API_ERROR', message: `API 오류 ${status}` } }
		})
	);
	for (status of [400, 401, 403, 404, 500]) {
		const response = page.waitForResponse('**/api/v1/health/lifestyle-score?*');
		await page.goto('/home');
		expect((await response).status()).toBe(status);
		await expect(page.getByRole('alert')).toHaveText(
			'건강 기록을 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.'
		);
		expect(await page.evaluate(() => navigator.onLine)).toBe(true);
		await expect(page.getByRole('status', { name: '네트워크 연결 상태' })).toHaveCount(0);
		await expect(page).toHaveURL('/home');
	}
});
