import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../../lib/testing/home-api-fixture';
import { sleepResponsiveFixture } from '../../../lib/testing/sleep-responsive-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
});

for (const width of [320, 390, 639, 640, 768, 1023, 1024, 1366]) {
	for (const [scenario, text] of [
		['increase', '+30분 증가'],
		['decrease', '-30분 감소'],
		['neutral', '변화 없음'],
		['oxygen-only', '비교할 기록 없음'],
		['previous-error', '이전 기간 조회 실패'],
		['long', '+1440분 증가']
	] as const) {
		test(`${width}px ${scenario}: 수면 비교 텍스트는 가로 헤더에서 세로 중앙이며 줄바꿈 시 자연스럽게 쌓인다`, async ({
			page
		}) => {
			await page.clock.setFixedTime(new Date('2026-11-09T03:00:00Z'));
			await page.route('**/api/v1/health/metrics/*?*', (route) => {
				const url = new URL(route.request().url());
				const metric = url.pathname.split('/').at(-1);
				if (metric !== 'sleeps' && metric !== 'oxygen_saturations') return route.fallback();
				const start = url.searchParams.get('start')!;
				const end = url.searchParams.get('end')!;
				if (scenario === 'previous-error' && end < '2026-11-03')
					return route.fulfill({
						status: 503,
						json: {
							error: { code: 'LOOKUP_FAILED', message: '합성 비교 조회 실패', retryable: true }
						}
					});
				const data = sleepResponsiveFixture(
					metric,
					start,
					end,
					scenario === 'long' ? 'normal' : scenario
				);
				if (scenario === 'long' && metric === 'sleeps') {
					// 완료된 7일 모두 같은 값이다. 일별 값·요약·실제 세션을 함께 맞춘다.
					const value = end === '2026-11-09' ? 1440 : 0;
					data.days = data.days.map((day) => ({ ...day, value }));
					data.summary = {
						...data.summary,
						avg: value,
						dailyMin: { date: start, value },
						dailyMax: { date: start, value }
					};
					data.detail.nights = data.detail.nights.map((night) => ({
						...night,
						startAt: new Date(Date.parse(night.endAt) - (value + 20) * 60000).toISOString(),
						sleepMinutes: value,
						deepMinutes: value / 5,
						remMinutes: value / 5,
						lightMinutes: (value * 3) / 5,
						awakeMinutes: 20,
						sessionMinutes: value + 20
					}));
				}
				return route.fulfill({ json: { data } });
			});
			await page.setViewportSize({ width, height: 900 });
			await page.goto('/health/sleep');
			const flow = page.getByRole('region', { name: '주간 수면 흐름' });
			await expect(flow.getByText(text, { exact: true })).toBeVisible();
			await page.evaluate(() => document.fonts.ready);
			const measure = () =>
				flow
					.locator(':scope > div')
					.first()
					.evaluate((header) => {
						const left = header.children[0].getBoundingClientRect();
						const comparison = header.children[1];
						const right = comparison.getBoundingClientRect();
						const span = comparison.querySelector('span')!;
						const style = getComputedStyle(span);
						const horizontal = right.left >= left.right;
						return {
							horizontal,
							centerDelta: Math.abs(right.y + right.height / 2 - left.y - left.height / 2),
							stacked: right.top >= left.bottom && Math.abs(right.left - left.left) < 1,
							overflow: document.documentElement.scrollWidth - innerWidth,
							labelWeight: getComputedStyle(comparison).fontWeight,
							font: style.fontSize,
							weight: style.fontWeight,
							color: style.color,
							background: style.backgroundColor,
							padding: style.padding,
							radius: style.borderRadius
						};
					});
			await expect
				.poll(async () => {
					const r = await measure();
					return r.horizontal ? r.centerDelta <= 1 : r.stacked;
				})
				.toBe(true);
			const result = await measure();
			expect(result.overflow).toBeLessThanOrEqual(1);
			expect(result.labelWeight).toBe('400');
			expect(result.font).toBe('14px');
			expect(result.weight).toBe('700');
			expect(result.color).toBe('rgb(44, 46, 53)');
			expect(result.background).toBe('rgba(0, 0, 0, 0)');
			expect(result.padding).toBe('0px');
			expect(result.radius).toBe('0px');
			// 넓은 화면에서 수평 배치가 실제로 검증되는지도 보장한다.
			if (width === 1366 && scenario === 'increase') expect(result.horizontal).toBe(true);
		});
	}
}

function contrast(foreground: string, background: string) {
	const luminance = (color: string) => {
		const channels = color
			.match(/[\d.]+/g)!
			.slice(0, 3)
			.map(Number)
			.map((value) => {
				value /= 255;
				return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
			});
		return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
	};
	const a = luminance(foreground),
		b = luminance(background);
	return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

test('수면 자정 날짜와 오늘의 점수 설명은 실제 카드 배경에서 4.5:1 대비를 유지한다', async ({
	page
}) => {
	for (const width of [320, 1366]) {
		await page.setViewportSize({ width, height: 900 });
		for (const target of ['midnight', 'score']) {
			await page.goto(target === 'midnight' ? '/health/sleep/2026-10-23' : '/home');
			if (target === 'score')
				await page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' }).click();
			const label = page
				.locator(target === 'midnight' ? '[aria-label$=" 자정 경계"] span' : '.score-details > p')
				.first();
			await expect(label).toBeVisible();
			await page.evaluate(() => document.fonts.ready);
			const colors = await label.evaluate((element) => {
				let parent: Element | null = element;
				while (parent && getComputedStyle(parent).backgroundColor === 'rgba(0, 0, 0, 0)')
					parent = parent.parentElement;
				const style = getComputedStyle(element);
				return {
					foreground: style.color,
					background: getComputedStyle(parent!).backgroundColor,
					size: style.fontSize,
					weight: style.fontWeight,
					opacity: style.opacity
				};
			});
			expect(colors.foreground).toBe('rgb(204, 56, 62)');
			expect(colors.background).toBe('rgb(254, 254, 254)');
			expect(colors.opacity).toBe('1');
			expect(contrast(colors.foreground, colors.background)).toBeGreaterThanOrEqual(4.5);
			// 자정 날짜는 좁은 화면에서도 12px이다(디자인 기준의 caption, 수면 상세 시간축과 같은 기준)
			expect(colors.size).toBe(target === 'score' ? '14px' : '12px');
			expect(colors.weight).toBe(target === 'score' ? '400' : '700');
		}
	}
});
