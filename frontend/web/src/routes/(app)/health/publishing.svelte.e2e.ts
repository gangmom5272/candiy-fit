import { mockDayDetailApi } from '../../../lib/testing/day-detail-api-fixture';
import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../../lib/testing/home-api-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
	await mockDayDetailApi(page);
});
for (const viewport of [
	{ width: 1920, height: 1080 },
	{ width: 1366, height: 768 },
	{ width: 768, height: 1024 },
	{ width: 360, height: 800 }
]) {
	test(`${viewport.width} 홈 설명 배치와 지표 카드·운동 anchor·수면 상세 연결`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize(viewport);
		await page.goto('/home');
		await page.evaluate(() => document.fonts.ready);
		const records = page.getByRole('region', { name: '최근 건강 기록' });
		await expect(records.getByRole('link')).toHaveCount(4);
		await expect(records.locator('img[src*="home-"]')).toHaveCount(0);
		const trigger = page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' });
		await trigger.click();
		const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
		const [t, p] = await Promise.all([trigger.boundingBox(), panel.boundingBox()]);
		if (!t || !p) throw Error('설명 패널 없음');
		if (viewport.width >= 1024) expect(p.x).toBeGreaterThanOrEqual(t.x + t.width);
		else expect(p.y).toBeGreaterThanOrEqual(t.y + t.height);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
		await page.screenshot({
			path: testInfo.outputPath(`home-${viewport.width}.png`),
			fullPage: true
		});
		await page.keyboard.press('Escape');
		await records.getByRole('link', { name: /운동 시간/ }).click();
		await expect(page).toHaveURL('/health/activity#exercise');
		await expect
			.poll(async () => {
				const target = await page.locator('#exercise').boundingBox();
				const header = await page.getByRole('banner').boundingBox();
				return target && header ? target.y >= header.y + header.height - 1 : false;
			})
			.toBe(true);
		await expect(
			page.getByRole('heading', { name: '운동', level: 2, exact: true })
		).toBeInViewport();
		await page.goto('/health/sleep/2026-10-25');
		await page.getByRole('link', { name: '하루 상세 보기', exact: true }).click();
		await expect(page).toHaveURL('/health/day/2026-10-25');
		await expect(page.locator('[data-range-start]')).toHaveAttribute('data-range-start', '-148');
		expect(
			await page.locator('[data-timeline-scroll]').evaluate((el) => el.scrollWidth > el.clientWidth)
		).toBe(true);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
		await page.screenshot({
			path: testInfo.outputPath(`day-${viewport.width}.png`),
			fullPage: true
		});
	});
}
