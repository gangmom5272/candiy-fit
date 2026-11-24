import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import { mockSleepApi, openCompletedSleepWeek } from '../../../../lib/testing/sleep-api-fixture';
import {
	mockActivityApi,
	openCompletedActivityWeek
} from '../../../../lib/testing/activity-api-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await page.route('**/api/v1/health/timeline?*', (route) => {
		if (new URL(route.request().url()).searchParams.get('start') !== '2026-10-20')
			return route.fallback();
		return route.fulfill({
			json: {
				data: {
					days: [
						{
							date: '2026-10-20',
							mainSleep: {
								startAt: '2026-10-19T23:29:00+09:00',
								endAt: '2026-10-20T01:00:00+09:00'
							},
							sleepSegments: [
								{
									stage: 'light',
									startAt: '2026-10-19T23:29:00+09:00',
									endAt: '2026-10-20T00:14:00+09:00'
								},
								{
									stage: 'deep',
									startAt: '2026-10-20T00:14:00+09:00',
									endAt: '2026-10-20T00:14:10+09:00'
								},
								{
									stage: 'rem',
									startAt: '2026-10-20T00:15:00+09:00',
									endAt: '2026-10-20T01:00:00+09:00'
								}
							]
						}
					]
				}
			}
		});
	});
});

test('PC에서 날짜·수면 막대·운동 날짜의 이동 안내와 단계 키보드 툴팁을 확인한다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	await openCompletedSleepWeek(page);
	const date = page.getByRole('link', { name: '2026-10-20 수면 상세', exact: true });
	await date.hover();
	await expect(page.getByRole('tooltip')).toHaveText('클릭하여 수면 상세 보기');
	await page.keyboard.press('Escape');
	await expect(page.getByRole('tooltip')).toHaveCount(0);
	const graph = page.getByRole('link', { name: '2026-10-20 수면 그래프 상세', exact: true });
	await graph.focus();
	await expect(page.getByRole('tooltip')).toHaveText('클릭하여 수면 상세 보기');
	await graph.press('Enter');
	await expect(page).toHaveURL('/health/sleep/2026-10-20');
	const stage = page.getByRole('region', { name: '수면 단계', exact: true });
	const first = stage.getByRole('button', { name: '얕은 수면 23:29~00:14 · 45분', exact: true });
	await first.focus();
	await expect(page.getByRole('tooltip')).toContainText('23:29~00:14 · 45분');
	await page.keyboard.press('Tab');
	await expect(page.getByRole('tooltip')).toContainText('1분 미만');
	await page.keyboard.press('Escape');
	await expect(page.getByRole('tooltip')).toHaveCount(0);
	await page.mouse.move(0, 0);
	await stage
		.getByRole('button', { name: '깊은 수면 00:14:00~00:14:10 · 1분 미만', exact: true })
		.hover();
	await expect(page.getByRole('tooltip')).toContainText('깊은 수면');
	await expect(page.getByRole('tooltip')).toHaveCount(1);
	await page.getByRole('link', { name: '하루 상세 보기', exact: true }).focus();
	await page.mouse.move(0, 0);
	await expect(page.getByRole('tooltip')).toHaveCount(0);
	await openCompletedActivityWeek(page);
	const exerciseDate = page
		.getByRole('region', { name: '운동 시간', exact: true })
		.getByRole('link')
		.first();
	await exerciseDate.hover();
	await expect(page.getByRole('tooltip')).toHaveText('클릭하여 하루 상세 보기');
	await exerciseDate.focus();
	await expect(page.getByRole('tooltip')).toHaveCount(1);
	await exerciseDate.press('Enter');
	await expect(page).toHaveURL('/health/day/2026-10-19');
});

test.describe('모바일 터치', () => {
	test.use({ hasTouch: true, isMobile: true, viewport: { width: 360, height: 800 } });
	test('단계 tap 전환·닫기와 기존 상세 이동을 유지한다', async ({ page }) => {
		await openCompletedSleepWeek(page);
		await page.getByRole('link', { name: '2026-10-20 수면 상세', exact: true }).tap();
		await expect(page).toHaveURL('/health/sleep/2026-10-20');
		const first = page.getByRole('button', { name: '얕은 수면 23:29~00:14 · 45분', exact: true });
		const short = page.getByRole('button', {
			name: '깊은 수면 00:14:00~00:14:10 · 1분 미만',
			exact: true
		});
		await first.tap();
		await expect(page.getByRole('tooltip')).toContainText('23:29~00:14 · 45분');
		await short.tap();
		await expect(page.getByRole('tooltip')).toContainText('깊은 수면');
		await expect(page.getByRole('tooltip')).toContainText('1분 미만');
		await expect(page.getByRole('tooltip')).toHaveCount(1);
		await short.tap();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await first.tap();
		await page.getByRole('heading', { name: '수면 단계', exact: true }).tap();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		expect(
			await short.evaluate((node) => node.getBoundingClientRect().width)
		).toBeGreaterThanOrEqual(24);
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
			)
		).toBe(true);
	});
});
