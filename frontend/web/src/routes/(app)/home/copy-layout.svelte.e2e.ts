import { expect, test } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../../lib/testing/home-api-fixture';
import {
	homeLayoutViewports,
	measureHomeCardLayout,
	expectNoHomePageOverflow
} from '../../../lib/testing/home-layout';

const artifacts = resolve('../../test-results/home-230/after');

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
});

for (const [width, height] of homeLayoutViewports) {
	test(`두 홈 문구와 카드 배치 ${width}x${height}`, async ({ page }) => {
		await page.setViewportSize({ width, height });
		mkdirSync(artifacts, { recursive: true });
		for (const route of ['home', 'health']) {
			await page.goto(`/${route}`, { waitUntil: 'networkidle' });
			if (route === 'home') {
				await expect(page.locator('.metric-card')).toHaveCount(4);
				await expect(page.getByText('기록 확인', { exact: true })).toHaveCount(0);
				await expect(page.getByLabel('기록이 반영된 시각', { exact: true })).toHaveCount(0);
				await expect(page.getByRole('button', { name: /기록이 반영된 시각/ })).toHaveCount(0);
			} else {
				await expect(page.getByText('오늘 기록은 기간 평균에서 제외해요.')).toHaveCount(0);
				await expect(
					page.getByRole('heading', {
						name: '내 건강에 맞는 생활 습관, AI 코치와 함께 찾아볼까요?'
					})
				).toBeVisible();
				await expect(page.getByRole('link', { name: 'AI 코치로 이동' })).toHaveAttribute(
					'href',
					'/coaching'
				);
			}
			const layout = await measureHomeCardLayout(page, '.metric-card, .health-summary-card');
			expectNoHomePageOverflow(layout);
			expect(layout.cards.every((card) => !card.overflow)).toBe(true);
			if (route === 'home') {
				for (const card of layout.cards) {
					expect(card.gap).toBe(16);
					expect(card.centerOffset).toBeLessThanOrEqual(1);
				}
			}
			writeFileSync(resolve(artifacts, `${route}-${width}.json`), JSON.stringify(layout, null, 2));
			await page.mouse.move(0, 0);
			await page.keyboard.press('Escape');
			await page.evaluate(() => scrollTo(0, 0));
			await page.screenshot({ path: resolve(artifacts, `${route}-${width}.png`), fullPage: true });
		}
	});
}
