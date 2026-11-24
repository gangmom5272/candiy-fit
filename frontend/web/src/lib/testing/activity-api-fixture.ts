import { expect, type Page } from '@playwright/test';
import { hourlyStepDays } from '../features/health/activity';
import { activityMetricFixture } from './activity-api-data';

export { activityMetricFixture } from './activity-api-data';

/** 테스트 서버 응답 전용. 운영 Activity에서는 이 파일을 참조하지 않는다. */
export async function openCompletedActivityWeek(page: Page) {
	// 최근 7일 탐색에서 기존 완료 기록(10/19~10/25)을 선택한다.
	await page.clock.setFixedTime(new Date('2026-11-01T03:00:00Z'));
	await page.goto('/health/activity');
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		'2026.10.19 ~ 10.25'
	);
}

export async function mockActivityApi(page: Page) {
	await page.clock.setFixedTime(new Date('2026-10-26T03:00:00Z'));
	await page.route('**/api/v1/health/metrics/*?*', async (route) => {
		const url = new URL(route.request().url());
		const metric = url.pathname.split('/').at(-1) ?? '';
		if (metric !== 'steps' && metric !== 'exercises') return route.fallback();
		await route.fulfill({
			json: {
				data: activityMetricFixture(
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
		return route.fulfill({
			json: {
				data: {
					period: { start, end },
					days: activityMetricFixture('steps', start, end).days.map((d) => ({
						date: d.date,
						isInProgress: d.isInProgress,
						hourlySteps: hourlyStepDays.find((day) => day.date === d.date)?.hourlySteps ?? null,
						hourlyHeartRates: null,
						mainSleep: null,
						sleepSegments: [],
						exerciseSegments: []
					}))
				}
			}
		});
	});
}
