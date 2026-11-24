import { expect, type Page } from '@playwright/test';
import { HEART_MOCK_TODAY } from '../features/health/heart-mock';
import { heartMetricFixture } from './heart-api-data';

export { heartMetricFixture } from './heart-api-data';

export async function mockHeartApi(page: Page) {
	await page.clock.setFixedTime(new Date(`${HEART_MOCK_TODAY}T03:00:00Z`));
	await page.route('**/api/v1/health/metrics/heart_rates?*', (route) => {
		const url = new URL(route.request().url());
		return route.fulfill({
			json: {
				data: heartMetricFixture(
					url.searchParams.get('start') ?? '',
					url.searchParams.get('end') ?? ''
				)
			}
		});
	});
}

export async function openCompletedHeartWeek(page: Page) {
	await page.clock.setFixedTime(new Date('2026-11-01T03:00:00Z'));
	await page.goto('/health/heart');
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(
		page.getByRole('group', { name: '주간 기간 선택' }).getByRole('button', { name: /^날짜 선택,/ })
	).toHaveText('2026.10.19 ~ 10.25');
	const dates = page
		.getByRole('region', { name: '일별 심박수' })
		.locator('[data-chart-navigation]');
	await expect(dates.first()).toHaveAttribute('href', '/health/day/2026-10-19');
	await expect(dates.last()).toHaveAttribute('href', '/health/day/2026-10-25');
}
