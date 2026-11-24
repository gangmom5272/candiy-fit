import { expect, type Page } from '@playwright/test';
import { SLEEP_MOCK_TODAY } from '../features/health/sleep-mock';
import { sleepDetailTimelineMock } from '../features/health/sleep-detail-mock';
import { sleepMetricFixture } from './sleep-api-data';

export { sleepMetricFixture } from './sleep-api-data';

export async function mockSleepApi(page: Page) {
	await page.clock.setFixedTime(new Date(`${SLEEP_MOCK_TODAY}T03:00:00Z`));
	await page.route('**/api/v1/health/metrics/*?*', (route) => {
		const url = new URL(route.request().url());
		const metric = url.pathname.split('/').at(-1);
		if (metric !== 'sleeps' && metric !== 'oxygen_saturations') return route.fallback();
		return route.fulfill({
			json: {
				data: sleepMetricFixture(
					metric,
					url.searchParams.get('start') ?? '',
					url.searchParams.get('end') ?? ''
				)
			}
		});
	});
	await page.route('**/api/v1/health/timeline?*', (route) => {
		const url = new URL(route.request().url());
		const start = url.searchParams.get('start') ?? '';
		const end = url.searchParams.get('end') ?? '';
		// Activity 시간대 fixture와 공존한다. 수면 상세는 단일 날짜를 요청한다.
		if (start !== end) return route.fallback();
		const found = sleepDetailTimelineMock.days.find((day) => day.date === start);
		return route.fulfill({
			json: {
				data: {
					period: { start, end },
					days: [
						found ?? {
							date: start,
							isInProgress: false,
							mainSleep: null,
							sleepSegments: [],
							hourlySteps: null,
							hourlyHeartRates: null,
							exerciseSegments: []
						}
					]
				}
			}
		});
	});
}

export async function openCompletedSleepWeek(page: Page) {
	await page.clock.setFixedTime(new Date('2026-11-01T03:00:00Z'));
	await page.goto('/health/sleep');
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		'2026.10.19 ~ 10.25'
	);
}
