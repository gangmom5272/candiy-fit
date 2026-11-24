import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import { mockActivityApi } from '../../../../lib/testing/activity-api-fixture';

async function measureContrast(page: Page) {
	return page
		.getByRole('region', { name: '걸음 수 기록', exact: true })
		.locator('dt, p')
		.evaluateAll((elements) => {
			const luminance = (color: string) => {
				const channels = color
					.match(/[\d.]+/g)
					?.slice(0, 3)
					.map(Number);
				if (!channels || channels.length !== 3)
					throw new Error(`색상을 읽을 수 없습니다: ${color}`);
				return channels
					.map((value) => value / 255)
					.map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4))
					.reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
			};
			return elements
				.filter(
					(element) =>
						element.textContent?.trim() === '선택 기간 일평균' || element.tagName === 'DT'
				)
				.map((element) => {
					let ancestor: Element | null = element;
					let background = '';
					while (ancestor) {
						background = getComputedStyle(ancestor).backgroundColor;
						if (background !== 'rgba(0, 0, 0, 0)') break;
						ancestor = ancestor.parentElement;
					}
					const foreground = getComputedStyle(element).color;
					const [light, dark] = [luminance(foreground), luminance(background)].sort(
						(a, b) => b - a
					);
					return {
						text: element.textContent,
						foreground,
						background,
						contrast: (light + 0.05) / (dark + 0.05)
					};
				});
		});
}

for (const width of [390, 768, 1366]) {
	test(`${width}px 활동 걸음 수 요약의 작은 글자는 기본·hover·focus에서 대비 4.5:1 이상이다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height: 900 });
		await page.clock.setFixedTime(new Date('2026-10-26T03:00:00Z'));
		await mockSignedIn(page);
		await mockActivityApi(page);
		await page.goto('/health/activity', { waitUntil: 'networkidle' });
		const label = page.getByText('선택 기간 일평균', { exact: true });
		await expect(label).toBeVisible();
		for (const state of ['default', 'hover', 'focus']) {
			if (state === 'hover') await label.hover();
			if (state === 'focus')
				await page
					.getByRole('figure', { name: '일별 걸음 수 차트', exact: true })
					.getByRole('link')
					.first()
					.focus();
			const measured = await measureContrast(page);
			expect(measured).toHaveLength(5);
			for (const item of measured) expect(item.contrast).toBeGreaterThanOrEqual(4.5);
			await testInfo.attach(state, {
				body: JSON.stringify(measured),
				contentType: 'application/json'
			});
		}
	});
}
