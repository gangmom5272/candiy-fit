import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../lib/testing/e2e-auth';
import { CONVERSATION_ID, mockCoachingServer } from '../lib/testing/e2e-coaching';

/** 디자인 기준에 맞춘 다듬기(S15P31A403-352) */
const PRIMARY_STRONG = 'rgb(204, 56, 62)';
const ON_PRIMARY = 'rgb(252, 249, 246)';

test('로그인 전 화면의 큰 버튼은 밝은 글자가 올라가므로 진한 레드를 쓴다', async ({ page }) => {
	// 로그인 확인이 일시 장애면 "다시 시도" 큰 버튼이 나온다
	await page.route('**/api/v1/auth/session', (route) =>
		route.fulfill({
			status: 503,
			json: { error: { code: 'AUTH_UNAVAILABLE', message: '잠시 후 다시 시도해 주세요.' } }
		})
	);
	await page.goto('/home');

	const retry = page.getByRole('button', { name: '다시 시도' });
	await expect(retry).toBeVisible();
	await expect(retry).toHaveCSS('background-color', PRIMARY_STRONG);
	await expect(retry).toHaveCSS('color', ON_PRIMARY);
});

for (const width of [320, 360, 1366]) {
	test(`${width}px AI 코치 안전 문구는 12px 이상이고 화면 밖으로 나가지 않는다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 800 });
		await mockSignedIn(page);
		await mockCoachingServer(page, () => {});
		await page.goto(`/coaching/${CONVERSATION_ID}`);

		const notice = page.getByText(/AI 코치는 .*참고용이며/).locator('visible=true');
		await expect(notice).toBeVisible();
		const style = await notice.evaluate((element) => {
			const computed = getComputedStyle(element);
			const box = element.getBoundingClientRect();
			return {
				size: parseFloat(computed.fontSize),
				lineHeight: computed.lineHeight,
				right: box.right,
				viewport: window.innerWidth
			};
		});
		expect(style.size).toBeGreaterThanOrEqual(12);
		expect(style.lineHeight).toBe('16px');
		expect(style.right).toBeLessThanOrEqual(style.viewport);
		expect(
			await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
		).toBe(true);
	});
}
