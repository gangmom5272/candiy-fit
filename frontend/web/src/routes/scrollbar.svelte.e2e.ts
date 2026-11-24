import { expect, test } from '@playwright/test';

// 스크롤바는 앱 전체가 같은 코랄 스크롤바다. 홈·건강 화면이 아닌 로그인 화면에서도 같은지 본다
test('홈·건강 밖 화면도 같은 코랄 스크롤바를 쓴다', async ({ page }) => {
	await page.goto('/login', { waitUntil: 'networkidle' });

	const styling = await page.evaluate(() => ({
		webkit: CSS.supports('selector(::-webkit-scrollbar)'),
		color: getComputedStyle(document.documentElement).scrollbarColor,
		thumb: getComputedStyle(document.documentElement, '::-webkit-scrollbar-thumb').backgroundColor
	}));
	if (styling.webkit) expect(styling.thumb).toBe('rgb(245, 199, 188)');
	else expect(styling.color).toBe('rgb(245, 199, 188) rgb(243, 246, 251)');
});
