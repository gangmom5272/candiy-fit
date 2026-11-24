import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../lib/testing/e2e-auth';
import { mockActivityApi } from '../lib/testing/activity-api-fixture';
import { mockDayDetailApi } from '../lib/testing/day-detail-api-fixture';
import { mockCoachingServer } from '../lib/testing/e2e-coaching';
import { mockHeartApi } from '../lib/testing/heart-api-fixture';
import { mockSleepApi } from '../lib/testing/sleep-api-fixture';

/**
 * 역할별 글자 기준(S15P31A403-309). 페이지 제목 30px, 섹션 제목 20px(lg 24px), 큰 차트 카드 제목 18px, 핵심 수치 28px.
 * 팀 합의대로 360·390·768·1366px 에서 크기와 가로 넘침을 함께 확인한다. 값은 합성 fixture 다.
 */
test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await mockHeartApi(page);
	await mockDayDetailApi(page);
	await mockCoachingServer(page, () => {});
	const list = { data: { items: [], nextCursor: null } };
	await page.route('**/api/v1/coaching/conversations?**', (route) => route.fulfill({ json: list }));
	await page.route('**/api/v1/coaching/conversations', (route) => route.fulfill({ json: list }));
});

type Check = { selector: string; size: [number, number]; weight?: string };
const PAGES: { path: string; checks: Check[] }[] = [
	{
		path: '/health/activity',
		checks: [
			{ selector: 'h1', size: [30, 30], weight: '700' },
			{ selector: '#daily-steps-title', size: [20, 24], weight: '700' },
			{ selector: '#weekday-average-title', size: [18, 18], weight: '700' }
		]
	},
	{
		path: '/health/heart',
		checks: [
			{ selector: 'h1', size: [30, 30] },
			{ selector: '#heart-chart-title', size: [20, 24] },
			{ selector: '[data-period-average]', size: [28, 28], weight: '700' }
		]
	},
	{
		path: '/health/sleep',
		checks: [
			{ selector: 'h1', size: [30, 30] },
			{ selector: '#sleep-flow-title', size: [20, 24] },
			{ selector: '#sleep-oxygen-title', size: [18, 18] }
		]
	},
	{
		path: '/health/day/2026-10-19',
		checks: [
			{ selector: 'h1', size: [30, 30] },
			{ selector: '#day-timeline-title', size: [20, 24] }
		]
	},
	// 좁은 화면은 화면에 보이지 않는 제목(sr-only)을 따로 두므로 보이는 제목만 잰다
	{ path: '/coaching', checks: [{ selector: 'h1:not(.sr-only)', size: [30, 30] }] }
];

async function fontOf(page: Page, selector: string) {
	return page
		.locator(selector)
		.first()
		.evaluate((el) => {
			const style = getComputedStyle(el);
			return { size: parseFloat(style.fontSize), weight: style.fontWeight };
		});
}

for (const width of [360, 390, 768, 1366]) {
	test(`${width}px 페이지·섹션·카드 제목과 수치가 역할 기준 크기이고 화면을 넘지 않는다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		for (const { path, checks } of PAGES) {
			await page.goto(path);
			await expect(page.locator('[data-health-loading]')).toHaveCount(0);
			for (const { selector } of checks)
				await expect(page.locator(selector).first()).toBeAttached();
			await page.evaluate(() => document.fonts.ready);
			for (const { selector, size, weight } of checks) {
				const font = await fontOf(page, selector);
				expect(font.size, `${path} ${selector}`).toBe(width >= 1024 ? size[1] : size[0]);
				if (weight) expect(font.weight, `${path} ${selector}`).toBe(weight);
			}
			expect(
				await page.evaluate(() => document.documentElement.scrollWidth),
				`${path} 가로 넘침`
			).toBeLessThanOrEqual(width);
		}
	});
}
