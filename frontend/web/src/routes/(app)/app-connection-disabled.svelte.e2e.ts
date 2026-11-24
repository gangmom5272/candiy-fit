import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../lib/testing/home-api-fixture';

// 기본 E2E 빌드는 배포 기본값과 같은 PUBLIC_APP_CONNECTION_GUIDE=false다.
for (const path of ['/home', '/health']) {
	test(`${path}: 기본 비활성 빌드는 연결 조회 없이 기존 POST·GET과 복귀 동작을 유지한다`, async ({
		page
	}) => {
		await page.clock.install({ time: new Date('2026-10-26T03:00:00Z') });
		await mockSignedIn(page);
		await mockHomeApi(page);
		let connectionReads = 0;
		let posts = 0;
		const healthReads: string[] = [];
		const order: string[] = [];
		page.on('request', (request) => {
			const { pathname } = new URL(request.url());
			if (pathname === '/api/v1/health/sync') {
				posts++;
				order.push('post');
			} else if (
				request.method() === 'GET' &&
				pathname.startsWith('/api/v1/health/') &&
				pathname !== '/api/v1/health/data-status'
			) {
				healthReads.push(pathname);
				order.push('get');
			}
		});
		await page.route('**/api/v1/health/data-status', (route) => {
			connectionReads++;
			return route.fulfill({
				json: { data: { appConnection: { status: 'NOT_CONNECTED', connectedAt: null } } }
			});
		});
		await page.goto(path);
		if (path === '/home') {
			await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
			await expect(
				page.getByRole('region', { name: '최근 건강 기록' }).locator('.metric-value')
			).toHaveText(['3,200보', '18분', '7시간 0분', '64.5 bpm']);
			await expect(
				page.getByRole('region', { name: '오늘의 한마디', exact: true }).getByRole('heading')
			).toBeVisible();
		} else {
			await expect(page.locator('.health-summary-card .health-summary-link[href]')).toHaveCount(4);
		}
		expect(order.slice(0, 2)).toEqual(['post', 'get']);
		expect(posts).toBe(1);
		expect(healthReads).toHaveLength(path === '/home' ? 5 : 4);
		expect(connectionReads).toBe(0);
		await expect(page.locator('.guide-main')).toHaveCount(0);
		await expect(page.getByText('앱 연동 상태를 확인하고 있어요.', { exact: true })).toHaveCount(0);
		await page.locator('main[data-health-ui]').evaluate((main) => {
			main.setAttribute('data-disabled-guide-probe', 'retained');
		});

		await page.clock.pauseAt(new Date('2026-10-26T03:01:00Z'));
		await page.evaluate(() => {
			window.dispatchEvent(new Event('focus'));
			document.dispatchEvent(new Event('visibilitychange'));
		});
		// 활성 빌드의 50ms 복귀 debounce를 지나도 비활성 빌드는 조회하지 않아야 한다.
		await page.clock.runFor(200);
		await expect(page.locator('main[data-health-ui]')).toHaveAttribute(
			'data-disabled-guide-probe',
			'retained'
		);
		expect(connectionReads).toBe(0);
		expect(posts).toBe(1);
		expect(healthReads).toHaveLength(path === '/home' ? 5 : 4);
		await expect(page.locator('.guide-main')).toHaveCount(0);
	});
}
