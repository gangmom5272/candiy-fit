import { expect, test, type Locator } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { activityMetricFixture, mockActivityApi } from '../../../lib/testing/activity-api-fixture';
import { sleepMetricFixture, mockSleepApi } from '../../../lib/testing/sleep-api-fixture';
import { heartMetricFixture, mockHeartApi } from '../../../lib/testing/heart-api-fixture';

async function revealChartItem(target: Locator) {
	await target.evaluate((element) =>
		element.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'instant' })
	);
}

for (const width of [320, 360, 375, 390, 412, 768, 1023, 1024, 1366]) {
	test(width + 'px: axis labels and long content stay inside summary cards', async ({ page }) => {
		await page.setViewportSize({ width, height: 844 });
		await mockSignedIn(page);
		await page.clock.setFixedTime(new Date('2026-10-26T03:00:00Z'));
		let state: 'normal' | 'large' | 'long' | 'uuid' | 'error' = 'normal';
		const longId = 'synthetic-' + 'a'.repeat(80);
		const uuid = '12345678-1234-1234-1234-123456789012';
		await page.route('**/api/v1/health/metrics/*?*', (route) => {
			if (state === 'error' || state === 'uuid')
				return route.fulfill({
					status: 503,
					headers: { 'x-request-id': state === 'uuid' ? uuid : longId },
					json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: 'synthetic' } }
				});
			const url = new URL(route.request().url());
			const metric = url.pathname.split('/').at(-1);
			const start = url.searchParams.get('start') ?? '';
			const end = url.searchParams.get('end') ?? '';
			const data =
				metric === 'heart_rates'
					? heartMetricFixture(start, end)
					: metric === 'sleeps'
						? sleepMetricFixture('sleeps', start, end)
						: activityMetricFixture(metric ?? '', start, end);
			if (state === 'large' && metric === 'steps') {
				data.days.forEach((day) => {
					if (!day.missing && day.value !== null) day.value = 100000;
				});
				data.summary.avg = 100000;
			}
			if (state === 'long' && metric === 'heart_rates') data.summary.avg = 72.1234567890123;
			return route.fulfill({ json: { data } });
		});
		for (const next of ['normal', 'large', 'long', 'uuid', 'error'] as const) {
			state = next;
			await page.goto('/health');
			const cards = page.locator('.health-summary-card');
			await expect(cards).toHaveCount(4);
			await expect(cards.locator('[role="status"]')).toHaveCount(0);
			if (state === 'error' || state === 'uuid') {
				await expect(cards.getByRole('alert')).toHaveCount(4);
				await expect(cards.first().getByRole('alert')).toContainText(
					state === 'uuid' ? uuid : longId
				);
			} else {
				await expect(cards.getByRole('figure')).toHaveCount(4);
				if (state === 'long')
					await expect(cards.nth(3).locator('.text-metric')).toHaveText(
						'평균 72.1234567890123 bpm'
					);
				await expect(cards.first().locator('.text-metric')).toHaveCSS('font-size', '28px');
				await expect(cards.first().locator('.text-metric')).toHaveCSS('font-weight', '700');
				await expect(cards.first().locator('.text-metric')).toHaveCSS('line-height', '35px');
				const chart = cards.first().getByRole('figure');
				const labels = chart.locator(':scope > div[aria-hidden="true"] > span');
				await expect(labels.first()).toHaveText(state === 'large' ? '100,000' : '10,000');
				await page.evaluate(() => document.fonts.ready);
				const bounds = await labels.evaluateAll((nodes) =>
					nodes.map((node) => {
						const label = node.getBoundingClientRect();
						const parent = node.closest('figure');
						if (!parent) throw new Error('Missing chart');
						const figure = parent.getBoundingClientRect();
						return { left: label.left, right: label.right, start: figure.left, end: figure.right };
					})
				);
				for (const bound of bounds) {
					expect(bound.left).toBeGreaterThanOrEqual(bound.start);
					expect(bound.right).toBeLessThanOrEqual(bound.end);
				}
				const alignments = await chart.evaluate((node) =>
					[...node.querySelectorAll('[data-chart-point]')].map((button) => {
						const index = Number((button as HTMLElement).dataset.chartPoint);
						const bar = node
							.querySelector('[data-summary-bar="' + index + '"]')
							?.getBoundingClientRect();
						if (!bar) throw new Error('Missing bar');
						const target = button.getBoundingClientRect();
						const date = node.querySelectorAll('[data-summary-date]')[index];
						return {
							bar: bar.left + bar.width / 2,
							point: target.left + target.width / 2,
							date: date.getClientRects().length
								? date.getBoundingClientRect().left + date.getBoundingClientRect().width / 2
								: null
						};
					})
				);
				for (const point of alignments) {
					expect(Math.abs(point.bar - point.point)).toBeLessThan(1);
					if (point.date !== null) expect(Math.abs(point.bar - point.date)).toBeLessThan(1);
				}
				for (const [summaryIndex, summary] of [cards.first(), cards.nth(3)].entries()) {
					const isLine = summaryIndex === 1;
					const figure = summary.getByRole('figure');
					const dates = figure.locator('[data-summary-date]:visible');
					await expect(dates).toHaveCount(7);
					await expect(dates.first()).toHaveCSS('font-size', '12px');
					const geometry = await figure.evaluate((node) => {
						const bounds = node.getBoundingClientRect();
						const dateElements = [...node.querySelectorAll('[data-summary-date]')].filter(
							(date) => date.getClientRects().length
						);
						const dates = dateElements.map((date) => date.getBoundingClientRect());
						const pane = node.querySelector<HTMLElement>('[data-health-chart-scroll]')!;
						const overflow = pane.scrollWidth - pane.clientWidth;
						if (overflow > 0) {
							pane.scrollLeft = 0;
							dateElements
								.at(-1)!
								.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'instant' });
						}
						const panned = pane.scrollLeft > 0;
						// 브라우저 내부에서 실제 pan과 동기 측정을 묶어 날짜마다의 수치를 보존한다.
						const revealedDates = dateElements.map((date) => {
							date.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'instant' });
							const box = date.getBoundingClientRect();
							return { left: box.left, right: box.right };
						});
						const circles = [...node.querySelectorAll('[data-summary-point]')].map((point) => {
							point.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'instant' });
							const box = point.getBoundingClientRect();
							const matrix = node.querySelector('svg')?.getScreenCTM();
							if (!matrix) throw new Error('Missing chart matrix');
							const vertices = [...node.querySelectorAll('polyline')].flatMap((line) =>
								[...line.points].map((vertex) =>
									new DOMPoint(vertex.x, vertex.y).matrixTransform(matrix)
								)
							);
							const centerX = box.left + box.width / 2;
							const expected = point.hasAttribute('data-missing')
								? new DOMPoint(
										(Number.parseFloat((point as HTMLElement).style.left) / 100) * 580,
										96
									).matrixTransform(matrix)
								: vertices.find((vertex) => Math.abs(vertex.x - centerX) < 1);
							if (!expected) throw new Error('Missing point vertex');
							return {
								width: box.width,
								height: box.height,
								left: box.left,
								right: box.right,
								offsetX: centerX - expected.x,
								offsetY: box.top + box.height / 2 - expected.y
							};
						});
						return {
							left: bounds.left,
							right: bounds.right,
							dates: dates.map((date) => ({ left: date.left, right: date.right })),
							revealedDates,
							overflow,
							panned,
							circles
						};
					});
					// 한 pan 시점의 content 좌표로 날짜 간 비중첩을 유지한다.
					for (const [index, date] of geometry.dates.entries()) {
						if (index) expect(date.left).toBeGreaterThanOrEqual(geometry.dates[index - 1].right);
					}
					if (geometry.overflow > 0) expect(geometry.panned).toBe(true);
					// 24px 날짜 목표 때문에 필요한 내부 pan을 실제로 사용한 뒤 같은 경계를 검사한다.
					for (const date of geometry.revealedDates) {
						expect(date.left).toBeGreaterThanOrEqual(geometry.left);
						expect(geometry.right - date.right).toBeGreaterThanOrEqual(8);
					}
					for (const circle of geometry.circles) {
						expect(circle.width).toBeCloseTo(9, 2);
						expect(circle.height).toBeCloseTo(9, 2);
						expect(Math.abs(circle.offsetX)).toBeLessThan(1);
						expect(Math.abs(circle.offsetY)).toBeLessThan(1);
						expect(circle.left).toBeGreaterThanOrEqual(geometry.left);
						expect(geometry.right - circle.right).toBeGreaterThanOrEqual(8);
					}
					expect(geometry.circles).toHaveLength(isLine ? 7 : 0);
					for (const point of [
						figure.locator('[data-chart-point]').first(),
						figure.locator('[data-chart-point]').last()
					]) {
						await page.keyboard.press('Tab');
						await point.focus();
						await expect(page.getByRole('tooltip')).toBeVisible();
						const index = await point.getAttribute('data-chart-point');
						const selection = figure.locator(
							isLine
								? `[data-point-selection="${index}"]`
								: `[data-summary-tooltip-anchor="${index}"]`
						);
						await revealChartItem(selection);
						const box = await selection.boundingBox();
						if (!box) throw new Error('Missing chart target');
						if (isLine) {
							expect(box.width).toBe(32);
							expect(box.height).toBe(32);
							expect(box.width).toBeCloseTo(box.height, 2);
							expect(
								await selection.evaluate((element) =>
									Number.parseFloat(getComputedStyle(element).borderRadius)
								)
							).toBeGreaterThanOrEqual(box.width / 2);
						}
						expect(box.x).toBeGreaterThanOrEqual(geometry.left);
						expect(box.x + box.width + 4).toBeLessThanOrEqual(geometry.right);
						const label = await point.boundingBox();
						const plot = await figure.locator('svg').boundingBox();
						if (!label || !plot) throw new Error('Missing date label');
						expect(label.height).toBeGreaterThanOrEqual(24);
						expect(label.y - plot.y).toBeCloseTo(104, 1);
						expect(label.x).toBeGreaterThanOrEqual(geometry.left);
						expect(label.x + label.width).toBeLessThanOrEqual(geometry.right);
						await page.keyboard.press('Escape');
					}
				}
				const legend = cards.first().getByText('평균선', { exact: true });
				await expect(legend).toHaveCSS('color', 'rgb(109, 114, 128)');
				const point = chart.locator('[data-chart-point]').first();
				await point.focus();
				await expect(page.getByRole('tooltip')).toBeVisible();
				await page.keyboard.press('Escape');
				await expect(page.getByRole('tooltip')).toHaveCount(0);
			}
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
			expect(
				await page.evaluate(() => document.body.scrollWidth <= document.documentElement.clientWidth)
			).toBe(true);
			expect(
				await cards.evaluateAll((nodes) =>
					nodes.every((node) => node.scrollWidth <= node.clientWidth + 1)
				)
			).toBe(true);
		}
	});
}

test('320px 월 경계에서도 7일의 일자와 전체 기간을 유지한다', async ({ page }) => {
	await page.setViewportSize({ width: 320, height: 844 });
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await mockHeartApi(page);
	await page.clock.setFixedTime(new Date('2026-11-02T03:00:00Z'));
	await page.goto('/health');
	const chart = page.locator('.health-summary-card').first().getByRole('figure');
	const dates = chart.locator('[data-summary-date]');
	await expect(dates).toHaveCount(7);
	expect(
		await dates.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-summary-date')))
	).toEqual(['10.27', '10.28', '10.29', '10.30', '10.31', '11.01', '11.02']);
	await expect(dates.locator('span:visible')).toHaveText([
		'27',
		'28',
		'29',
		'30',
		'31',
		'01',
		'02'
	]);
	await expect(page.locator('.date-picker-trigger')).toContainText('10.27');
	await expect(page.locator('.date-picker-trigger')).toContainText('11.02');
	await expect(chart.locator('figcaption')).toContainText('11.01');
});
