import { mockSleepApi, openCompletedSleepWeek } from '../../../../lib/testing/sleep-api-fixture';
import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockSleepApi(page);
});

test('수면 기록, 결측, 기간 이동, 상세와 코칭 링크', async ({ page }) => {
	await openCompletedSleepWeek(page);
	await expect(page.getByRole('button', { name: '이전 주' })).toBeEnabled();
	const flow = page.getByRole('region', { name: '주간 수면 흐름' });
	await expect(flow.getByText('6시간 39분', { exact: true })).toBeVisible();
	await expect(flow.getByText('기록 없음', { exact: true })).toHaveCount(1);
	await expect(flow.getByText('+17분 증가')).toBeVisible();
	await expect(flow.getByRole('link', { name: /^2026-.* 수면 상세$/ })).toHaveText([
		'10/19',
		'10/20',
		'10/21',
		'10/22',
		'10/23',
		'10/24',
		'10/25'
	]);
	await expect(flow.getByRole('link', { name: '2026-10-22 수면 그래프 상세' })).toHaveCount(0);
	const oxygen = page.getByRole('region', { name: '수면 중 산소포화도' });
	await expect(oxygen.getByText('96.25%', { exact: true })).toBeVisible();
	await expect(oxygen.getByText('93.4%', { exact: true })).toBeVisible();
	await expect(oxygen.locator('[data-oxygen-point]')).toHaveCount(4);
	await expect(oxygen.locator('polyline')).toHaveCount(3);
	await expect(page.getByRole('link', { name: 'AI 코치로 이동' })).toHaveAttribute(
		'href',
		'/coaching'
	);
	await page.getByRole('button', { name: '다음 주' }).click();
	await expect(page.getByRole('button', { name: '다음 주' })).toBeDisabled();
	const displayedPeriod = page
		.getByRole('group', { name: '주간 기간 선택' })
		.getByRole('button', { name: /^날짜 선택,/ });
	await expect(displayedPeriod).toBeVisible();
	await expect(displayedPeriod).toHaveText('2026.10.26 ~ 11.01');
	await expect(flow.getByRole('link', { name: /^2026-.* 수면 상세$/ })).toHaveText([
		'10/26',
		'10/27',
		'10/28',
		'10/29',
		'10/30',
		'10/31',
		'11/01'
	]);
	await expect(flow.getByText('동기화 대기', { exact: true })).toHaveCount(7);
	await expect(flow.getByText('비교할 기록 없음')).toBeVisible();
	await expect(oxygen.locator('[data-oxygen-point]')).toHaveCount(1);
	await page.getByRole('button', { name: '이전 주' }).click();
	const date = page.getByRole('link', { name: '2026-10-20 수면 상세' });
	await expect(date).toHaveAttribute('href', '/health/sleep/2026-10-20');
	await date.click();
	await expect(page).toHaveURL('/health/sleep/2026-10-20');
	await expect(
		page.getByRole('heading', { name: '10월 20일 수면 상세', exact: true })
	).toBeVisible();
});

test('bar 클릭과 키보드로 날짜와 동일한 상세 화면으로 이동한다', async ({ page }) => {
	await openCompletedSleepWeek(page);
	const bar = page.getByRole('link', { name: '2026-10-20 수면 그래프 상세', exact: true });
	await expect(bar).toHaveAttribute('href', '/health/sleep/2026-10-20');
	await bar.click();
	await expect(page).toHaveURL('/health/sleep/2026-10-20');
	await page.goBack();
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	const date = page.getByRole('link', { name: '2026-10-20 수면 상세', exact: true });
	await date.focus();
	await page.keyboard.press('Tab');
	await expect(bar).toBeFocused();
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL('/health/sleep/2026-10-20');
	await page.goBack();
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await page.getByRole('link', { name: '2026-10-22 수면 상세', exact: true }).focus();
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL('/health/sleep/2026-10-22');
});

for (const width of [1920, 1366, 768, 360]) {
	test(`${width}px에서 카드 배치와 콘텐츠가 화면 안에 들어온다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 1000 });
		await openCompletedSleepWeek(page);
		await page.getByRole('heading', { name: '주간 수면 흐름' }).waitFor();
		await page.evaluate(() => document.fonts.ready);
		const date = page.getByRole('link', { name: '2026-10-20 수면 상세', exact: true });
		expect(
			await date.evaluate((element) => {
				const column = element.parentElement;
				if (!column) return false;
				const link = element.getBoundingClientRect();
				const cell = column.getBoundingClientRect();
				return link.width < cell.width && link.height < cell.height;
			})
		).toBe(true);
		for (const current of [false, true]) {
			if (current) await page.getByRole('button', { name: '다음 주' }).click();
			expect(
				await page.evaluate(
					() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
				)
			).toBe(true);
			const summary = await page.getByRole('region', { name: '수면 기록 요약' }).boundingBox();
			const oxygen = await page.getByRole('region', { name: '수면 중 산소포화도' }).boundingBox();
			if (!summary || !oxygen) throw new Error('카드 위치 없음');
			if (width >= 1024) expect(oxygen.x).toBeGreaterThanOrEqual(summary.x + summary.width);
			else expect(oxygen.y).toBeGreaterThanOrEqual(summary.y + summary.height);
			const flow = page.getByRole('region', { name: '주간 수면 흐름' });
			expect(
				await flow.evaluate((element) => {
					const outer = element.getBoundingClientRect();
					return Array.from(element.querySelectorAll('a, span, p, h2'))
						.filter(
							(child) => !child.classList.contains('sr-only') && child.getClientRects().length > 0
						)
						.every((child) => {
							const rect = child.getBoundingClientRect();
							return (
								rect.left >= outer.left &&
								rect.right <= outer.right &&
								child.scrollWidth <= child.clientWidth + 1
							);
						});
				})
			).toBe(true);
			const cta = page.getByRole('link', { name: 'AI 코치로 이동' });
			await cta.scrollIntoViewIfNeeded();
			await expect(cta).toBeInViewport({ ratio: 1 });
			const oxygenCard = page.getByRole('region', { name: '수면 중 산소포화도' });
			const points = oxygenCard.getByRole('button', { name: /산소포화도 상세$/ });
			for (const point of [points.first(), points.last()]) {
				await point.click();
				const tooltip = page.getByRole('tooltip');
				await expect(tooltip).toBeVisible();
				const bounds = await tooltip.boundingBox();
				const cardBounds = await oxygenCard.boundingBox();
				const pointBounds = await point.boundingBox();
				if (!bounds || !cardBounds || !pointBounds) throw new Error('툴팁 위치 없음');
				expect(bounds.x).toBeGreaterThanOrEqual(cardBounds.x);
				expect(bounds.x + bounds.width).toBeLessThanOrEqual(cardBounds.x + cardBounds.width);
				expect(bounds.y + bounds.height).toBeLessThanOrEqual(pointBounds.y);
				expect(
					await page.evaluate(
						() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
					)
				).toBe(true);
			}
			await page.screenshot({
				path: `test-results/sleep-${width}-${current ? 'current' : 'completed'}.png`,
				fullPage: true
			});
		}
	});
}

test.describe('산소포화도 모바일 터치', () => {
	test.use({ hasTouch: true, isMobile: true, viewport: { width: 360, height: 800 } });
	test('tap으로 날짜를 바꾸고 Escape와 외부 선택으로 닫는다', async ({ page }) => {
		await openCompletedSleepWeek(page);
		const first = page.getByRole('button', { name: '10월 20일 산소포화도 상세' });
		await first.tap();
		await expect(page.getByRole('tooltip')).toContainText('10월 20일');
		await page.getByRole('button', { name: '10월 25일 산소포화도 상세' }).tap();
		await expect(page.getByRole('tooltip')).toContainText('10월 25일');
		await page.keyboard.press('Escape');
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await first.tap();
		await page.getByRole('heading', { name: '수면 중 산소포화도', exact: true }).tap();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
	});
});
