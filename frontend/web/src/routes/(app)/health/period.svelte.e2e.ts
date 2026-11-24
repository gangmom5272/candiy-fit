import { mockHeartApi } from '../../../lib/testing/heart-api-fixture';
import { mockSleepApi } from '../../../lib/testing/sleep-api-fixture';
import { mockActivityApi } from '../../../lib/testing/activity-api-fixture';
import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await mockHeartApi(page);
});
for (const name of ['activity', 'sleep', 'heart']) {
	for (const sample of [
		{
			date: '2026-11-02',
			start: '2026-10-27',
			end: '2026-11-02',
			previousStart: '2026-10-20',
			previousEnd: '2026-10-26',
			label: '2026.10.27 ~ 11.02'
		},
		{
			date: '2027-01-02',
			start: '2026-12-27',
			end: '2027-01-02',
			previousStart: '2026-12-20',
			previousEnd: '2026-12-26',
			label: '2026.12.27 ~ 2027.01.02'
		}
	]) {
		test(`${name}: ${sample.date}의 표시·API·차트는 같은 연속 7일을 사용한다`, async ({ page }) => {
			await page.clock.setFixedTime(new Date(`${sample.date}T03:00:00Z`));
			const metric = name === 'sleep' ? 'sleeps' : name === 'heart' ? 'heart_rates' : 'steps';
			const request = page.waitForRequest(
				`**/api/v1/health/metrics/${metric}?start=${sample.start}&end=${sample.end}`
			);
			const previous =
				name === 'sleep'
					? page.waitForRequest(
							`**/api/v1/health/metrics/sleeps?start=${sample.previousStart}&end=${sample.previousEnd}`
						)
					: null;
			await page.goto(`/health/${name}`);
			await request;
			if (previous) await previous;
			await expect(
				page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')
			).toHaveText(sample.label);
			const links =
				name === 'sleep'
					? page
							.getByRole('region', { name: '주간 수면 흐름' })
							.getByRole('link', { name: / 수면 상세$/ })
					: name === 'heart'
						? page.getByRole('region', { name: '일별 심박수' }).getByRole('link')
						: page.locator('.daily-steps-chart a:not(svg a)');
			await expect(links).toHaveCount(7);
			await expect(links.first()).toHaveAttribute('href', new RegExp(`${sample.start}$`));
			await expect(links.last()).toHaveAttribute('href', new RegExp(`${sample.end}$`));
			if (name === 'heart') {
				const positions = await links.evaluateAll((nodes) =>
					nodes.map((node) => node.getBoundingClientRect().left)
				);
				for (let index = 1; index < positions.length; index++)
					expect(positions[index]).toBeGreaterThan(positions[index - 1]);
			}
		});
	}
	test(`${name} 기간 탐색은 기준일 포함 연속 7일을 공통으로 사용한다`, async ({ page }) => {
		await page.goto(`/health/${name}`);
		const nav = page.getByRole('group', { name: '주간 기간 선택' });
		await expect(nav.getByRole('status')).toHaveText('2026.10.20 ~ 10.26');
		await nav.getByRole('button', { name: '이전 주' }).click();
		await expect(nav.getByRole('status')).toHaveText('2026.10.13 ~ 10.19');
		await nav.getByRole('button', { name: '이전 주' }).click();
		await expect(nav.getByRole('status')).toHaveText('2026.10.06 ~ 10.12');
		await nav.getByRole('button', { name: '이전 주' }).click();
		await expect(nav.getByRole('status')).toHaveText('2026.09.29 ~ 10.05');
		for (let i = 0; i < 3; i++) await nav.getByRole('button', { name: '다음 주' }).click();
		await expect(nav.getByRole('status')).toHaveText('2026.10.20 ~ 10.26');
		await expect(nav.getByRole('button', { name: '다음 주' })).toBeDisabled();
	});
}
