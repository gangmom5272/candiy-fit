import { expect, test, type Locator, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { activityMetricFixture, mockActivityApi } from '../../../lib/testing/activity-api-fixture';
import { sleepMetricFixture } from '../../../lib/testing/sleep-api-fixture';
import { heartMetricFixture } from '../../../lib/testing/heart-api-fixture';
import { formatSleepMinutes } from '../../../lib/features/health/sleep';
import {
	homeLayoutViewports,
	measureHomeCardLayout,
	expectNoHomePageOverflow
} from '../../../lib/testing/home-layout';
import {
	healthSummaryChart,
	hoverSummaryPoint as hoverPoint,
	tooltipPointerSelection,
	tooltipBridge,
	pointerEdgePositions
} from '../../../lib/testing/tooltip-pointer';

const artifacts = resolve('../../test-results/health-home-220');
const phase = 'final';
const metrics = [
	['steps', '걸음 수', '보'],
	['exercises', '운동 시간', '분'],
	['sleeps', '수면', '분'],
	['heart_rates', '일별 평균 심박수', 'bpm']
] as const;

function fixture(metric: string, start: string, end: string) {
	return metric === 'heart_rates'
		? heartMetricFixture(start, end)
		: metric === 'sleeps'
			? sleepMetricFixture('sleeps', start, end)
			: activityMetricFixture(metric, start, end);
}

function pointerSelection(chart: Locator, point: Locator, metric: (typeof metrics)[number][0]) {
	return metric === 'steps' || metric === 'sleeps'
		? tooltipPointerSelection(chart, point, metric)
		: chart.locator('[data-point-selection]');
}

async function expectTooltipInViewport(page: Page) {
	const box = await page.getByRole('tooltip').boundingBox();
	const viewport = page.viewportSize();
	if (!box || !viewport) throw new Error('툴팁 위치 없음');
	expect(box.x).toBeGreaterThanOrEqual(0);
	expect(box.y).toBeGreaterThanOrEqual(0);
	expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
	expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
}
test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	// 상세 이동 시 timeline 요청도 합성 응답을 사용하고 실행 중인 백엔드에 의존하지 않는다.
	await mockActivityApi(page);
	await page.clock.setFixedTime(new Date('2026-10-25T03:00:00Z'));
	await page.route('**/api/v1/health/metrics/*?*', (route) => {
		const url = new URL(route.request().url());
		const metric = url.pathname.split('/').at(-1);
		const start = url.searchParams.get('start') ?? '';
		const end = url.searchParams.get('end') ?? '';
		const data = fixture(metric ?? '', start, end);
		return route.fulfill({ json: { data } });
	});
});

for (const width of [360, 768, 1366]) {
	test(`${width}px에서 요약 축 확장 후에도 레이아웃·눈금 좌표·원본 툴팁을 유지한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		let expanded = false;
		const cases = [
			{
				metric: 'steps',
				base: 10000,
				value: 10001,
				labels: ['12,000', '6,000', '0'],
				max: 12000,
				min: 0
			},
			{
				metric: 'exercises',
				base: 120,
				value: 120.1,
				labels: ['2h 30m', '1h 15m', '0'],
				max: 150,
				min: 0
			},
			{
				metric: 'sleeps',
				base: 480,
				value: 480.1,
				labels: ['9h', '4h 30m', '0'],
				max: 540,
				min: 0
			},
			{
				metric: 'heart_rates',
				base: 90,
				value: 90.1,
				labels: ['100', '80', '60'],
				max: 100,
				min: 60
			}
		];
		await page.route('**/api/v1/health/metrics/*?*', (route) => {
			const url = new URL(route.request().url());
			const scenario = cases.find((item) => item.metric === url.pathname.split('/').at(-1));
			if (!scenario) throw new Error('요약 축 검증 지표 없음');
			const data = fixture(
				scenario.metric,
				url.searchParams.get('start') ?? '',
				url.searchParams.get('end') ?? ''
			);
			const value = expanded ? scenario.value : scenario.base;
			data.days = data.days.map((day) => ({ ...day, value, missing: false, reason: null }));
			data.summary.avg = value;
			return route.fulfill({ json: { data } });
		});
		await page.goto('/health');
		const cards = page.locator('.health-summary-card');
		await expect(cards.locator('.health-summary-link[href]')).toHaveCount(4);
		await page.evaluate(() => document.fonts.ready);
		const before = await cards.evaluateAll((nodes) =>
			nodes.map((node) => ({
				width: node.getBoundingClientRect().width,
				height: node.getBoundingClientRect().height
			}))
		);
		expanded = true;
		await page.reload();
		await expect(cards.locator('.health-summary-link[href]')).toHaveCount(4);
		await page.evaluate(() => document.fonts.ready);
		expect(
			await cards.evaluateAll((nodes) =>
				nodes.map((node) => ({
					width: node.getBoundingClientRect().width,
					height: node.getBoundingClientRect().height
				}))
			)
		).toEqual(before);
		for (const [index, scenario] of cases.entries()) {
			const chart = cards.nth(index).getByRole('figure');
			const labels = chart.locator('div[aria-hidden="true"] > span:not([data-summary-date])');
			await expect(labels).toHaveText(scenario.labels);
			const geometry = await chart.evaluate((node) => {
				const point = node.querySelector('svg rect, [data-summary-point]');
				const card = node.closest('.health-summary-card')?.getBoundingClientRect();
				if (!card) throw new Error('요약 카드 위치 없음');
				const lines = [...node.querySelectorAll('svg line[stroke="var(--color-border)"]')];
				const labels = [
					...node.querySelectorAll('div[aria-hidden="true"] > span:not([data-summary-date])')
				];
				return {
					grid: lines.map((line) => Number(line.getAttribute('y1'))),
					labelOffsets: labels.map(
						(label, index) =>
							label.getBoundingClientRect().top +
							label.getBoundingClientRect().height / 2 -
							lines[index].getBoundingClientRect().top
					),
					labelsFit: labels.every((label) => {
						const bounds = label.getBoundingClientRect();
						return bounds.left >= card.left && bounds.right <= card.right;
					}),
					pointY:
						point?.tagName === 'rect'
							? Number(point.getAttribute('y'))
							: Number(
									node
										.querySelector('polyline')
										?.getAttribute('points')
										?.split(' ')[0]
										.split(',')[1]
								),
					height: node.querySelector('svg')?.getBoundingClientRect().height
				};
			});
			expect(geometry.grid).toEqual([24, 60, 96]);
			expect(geometry.labelsFit).toBe(true);
			for (const offset of geometry.labelOffsets) expect(offset).toBeCloseTo(0, 1);
			expect(geometry.height).toBe(132);
			expect(geometry.pointY).toBeCloseTo(
				96 - ((scenario.value - scenario.min) / (scenario.max - scenario.min)) * 72,
				6
			);
			await chart.locator('[data-chart-point]').first().focus();
			await expect(page.getByRole('tooltip')).toContainText(
				scenario.metric === 'sleeps'
					? formatSleepMinutes(scenario.value)
					: scenario.value.toLocaleString('ko-KR')
			);
			await page.keyboard.press('Escape');
		}
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
	});
}

for (const [width, height] of homeLayoutViewports) {
	test(`layout ${width}x${height}`, async ({ page }) => {
		await page.setViewportSize({ width, height });
		await page.goto('/health', { waitUntil: 'networkidle' });
		await expect(page.locator('.health-summary-card svg')).toHaveCount(4);
		const measurements = await measureHomeCardLayout(page, '.health-summary-card');
		mkdirSync(artifacts, { recursive: true });
		writeFileSync(
			resolve(artifacts, `${phase}-${width}x${height}.json`),
			JSON.stringify(measurements, null, 2)
		);
		await page.screenshot({
			path: resolve(artifacts, `${phase}-${width}x${height}.png`),
			fullPage: true
		});
		expectNoHomePageOverflow(measurements);
		// 카드 하단 여백 축소 후에도 그래프 높이는 기존 크기를 유지한다.
		if (width >= 1024) {
			for (const card of measurements.cards) {
				expect(card.height).toBe(288);
				expect(card.chart?.height).toBe(132);
			}
		}
	});
}

for (const [index, [metric, label, unit]] of metrics.entries()) {
	test(`${metric} 원본 값·0·결측, hover·키보드·가장자리`, async ({ page }) => {
		await page.setViewportSize({ width: 1366, height: 768 });
		const data = fixture(metric, '2026-10-19', '2026-10-25');
		data.days[1] = { ...data.days[1], value: 0, missing: false, reason: null };
		data.days[2] = { ...data.days[2], value: null, missing: true, reason: 'UNSYNCED' };
		data.days[3] = { ...data.days[3], value: 999, missing: true, reason: 'NO_RECORD' };
		await page.route(`**/api/v1/health/metrics/${metric}?*`, (route) =>
			route.fulfill({ json: { data } })
		);
		await page.goto('/health', { waitUntil: 'networkidle' });
		const chart = page.locator('.health-summary-card').nth(index).getByRole('figure');
		const points = chart.locator('[data-chart-point]');
		await expect(points).toHaveCount(
			data.days.filter((day) => !day.missing && day.value !== null).length
		);
		await expect(chart.locator('[data-summary-date]')).toHaveCount(7);
		await hoverPoint(page, chart, points.first(), metric);
		const tooltip = page.getByRole('tooltip');
		await expect(tooltip).toContainText('2026.10.19');
		await expect(tooltip).not.toContainText('KST');
		await expect(tooltip).toContainText(label);
		await expect(tooltip).toContainText(
			metric === 'sleeps'
				? formatSleepMinutes(data.days[0].value)
				: `${data.days[0].value?.toLocaleString('ko-KR', { maximumFractionDigits: 20 })} ${unit}`
		);
		await expectTooltipInViewport(page);
		await expect(
			metric === 'steps' || metric === 'sleeps'
				? chart.locator('[data-bar-selection]')
				: chart.locator('[data-point-selection]')
		).toHaveCSS('border-color', 'rgb(248, 90, 90)');
		await page.screenshot({ path: resolve(artifacts, `tooltip-${metric}.png`) });
		const selection = await (
			metric === 'steps' || metric === 'sleeps'
				? chart.locator('[data-bar-selection]')
				: chart.locator('[data-point-selection]')
		).boundingBox();
		if (!selection) throw new Error('선택 테두리 위치 없음');
		await page.mouse.move(selection.x + selection.width + 1, selection.y + selection.height / 2);
		await expect(tooltip).toHaveCount(0);
		await points.nth(1).focus();
		await expect(tooltip).toContainText(metric === 'sleeps' ? '0시간 0분' : `0 ${unit}`);
		await page.keyboard.press('ArrowRight');
		await expect(chart.locator('[data-date-target="2026-10-21"]')).toBeFocused();
		await expect(tooltip).toHaveCount(0);
		await page.keyboard.press('ArrowRight');
		await expect(chart.locator('[data-date-target="2026-10-22"]')).toBeFocused();
		await expect(tooltip).toHaveCount(0);
		await page.keyboard.press('ArrowRight');
		await expect(points.nth(2)).toBeFocused();
		await expect(tooltip).toContainText('2026.10.23');
		await expect(tooltip).not.toContainText('999');
		await chart.locator('[data-missing-point]').first().hover();
		await expect(points.nth(2)).toBeFocused();
		await expect(tooltip).toHaveText('클릭하여 하루 상세 보기');
		await expect(tooltip.locator('strong')).toHaveCount(0);
		await expect(chart.locator('[data-bar-selection], [data-point-selection]')).toHaveCount(0);
		await expect(chart.locator('figcaption')).toContainText('10.21: 동기화 대기');
		await page.keyboard.press('End');
		await expect(points.last()).toBeFocused();
		await expect(tooltip).toContainText('2026.10.25');
		await expectTooltipInViewport(page);
		await page.keyboard.press('Escape');
		await expect(tooltip).toHaveCount(0);
		await page.keyboard.press('Home');
		await expect(points.first()).toBeFocused();
		await page.keyboard.press('Tab');
		if (index < metrics.length - 1)
			await expect(
				page
					.locator('.health-summary-card')
					.nth(index + 1)
					.locator('.health-summary-link')
			).toBeFocused();
		else
			await expect(page.getByRole('link', { name: 'AI 코치로 이동', exact: true })).toBeFocused();
		await expect(page).toHaveURL('/health');
		await page.getByRole('heading', { level: 1 }).click();
		await expect(tooltip).toHaveCount(0);
		await hoverPoint(page, chart, points.first(), metric);
		await expect(tooltip).toBeVisible();
		await page.locator('.health-summary-card').nth(index).getByRole('heading').click();
		await expect(page).toHaveURL(
			[
				'/health/activity?date=2026-10-25',
				'/health/activity?date=2026-10-25#exercise',
				'/health/sleep?date=2026-10-25',
				'/health/heart?date=2026-10-25'
			][index]
		);
	});

	test(`${metric} 전체 결측과 존재하지 않는 날짜에 수치를 만들지 않는다`, async ({ page }) => {
		const data = fixture(metric, '2026-10-19', '2026-10-25');
		data.days = data.days
			.slice(0, 2)
			.map((day) => ({ ...day, value: null, missing: true, reason: 'NO_RECORD' }));
		data.summary.avg = null;
		await page.route(`**/api/v1/health/metrics/${metric}?*`, (route) =>
			route.fulfill({ json: { data } })
		);
		await page.goto('/health', { waitUntil: 'networkidle' });
		const chart = page.locator('.health-summary-card').nth(index).getByRole('figure');
		await expect(chart.locator('[data-chart-point]')).toHaveCount(0);
		await expect(chart.locator('[data-summary-date]')).toHaveCount(2);
		await expect(chart.locator('rect, polyline, [stroke-dasharray]')).toHaveCount(0);
		await chart.locator('[data-missing-point]').first().hover();
		await expect(chart.getByRole('tooltip')).toHaveText('클릭하여 하루 상세 보기');
		await expect(chart.getByRole('tooltip').locator('strong')).toHaveCount(0);
		await expect(chart.locator('[data-bar-selection], [data-point-selection]')).toHaveCount(0);
		await expect(chart.locator('figcaption')).toContainText('10.19: 기록 없음');
	});
}

test.describe('touch', () => {
	test.use({ hasTouch: true });
	const cases = metrics.flatMap(([metric, label], index) =>
		[360, 768].map((width) => ({ metric, label, index, width }))
	);
	for (const { metric, label, index, width } of cases) {
		test(`${metric} ${width}px tap과 바깥 tap, 세로 스크롤`, async ({ page }) => {
			await page.setViewportSize({ width, height: 800 });
			await page.goto('/health', { waitUntil: 'networkidle' });
			const points = page.locator('.health-summary-card').nth(index).locator('[data-chart-point]');
			await points.first().tap();
			await expect(page).toHaveURL('/health/day/2026-10-19');
			await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
			await expect(page).toHaveURL('/health');
			await page.keyboard.press('Tab');
			await points.first().focus();
			await expect(page.getByRole('tooltip')).toContainText(label);
			await expectTooltipInViewport(page);
			await points.last().tap();
			await expect(page).toHaveURL('/health/day/2026-10-25');
			await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
			await expect(page).toHaveURL('/health');
			await page.keyboard.press('Tab');
			await points.last().focus();
			await expect(page.getByRole('tooltip')).toContainText('2026.10.25');
			await expectTooltipInViewport(page);
			await page
				.getByRole('tooltip')
				.screenshot({ path: resolve(artifacts, `touch-${metric}-${width}.png`) });
			await page.getByRole('heading', { level: 1 }).tap();
			await expect(page.getByRole('tooltip')).toHaveCount(0);
			await expect(page).toHaveURL('/health');
			await points.last().scrollIntoViewIfNeeded();
			const beforeScroll = await page.evaluate(() => scrollY);
			const point = await points.last().boundingBox();
			if (!point) throw new Error('터치할 지점 없음');
			const cdp = await page.context().newCDPSession(page);
			const x = point.x + point.width / 2;
			const y = point.y + point.height / 2;
			const direction = beforeScroll > 0 ? 1 : -1;
			await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
			for (let step = 1; step <= 5; step++) {
				await cdp.send('Input.dispatchTouchEvent', {
					type: 'touchMove',
					touchPoints: [{ x, y: y + direction * step * 20 }]
				});
				await page.waitForTimeout(20);
			}
			await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
			await expect.poll(() => page.evaluate(() => scrollY)).not.toBe(beforeScroll);
			await cdp.detach();
		});
	}
});

test('확대에 해당하는 683px 뷰포트에서도 가로 잘림 없이 툴팁을 읽는다', async ({ page }) => {
	await page.setViewportSize({ width: 683, height: 384 });
	await page.goto('/health', { waitUntil: 'networkidle' });
	// 짧은 뷰포트 회귀 조건이며 실제 Chrome native 200% 확대 QA를 대체하지 않는다.
	const expectVisibleBounds = async (target: Locator) => {
		await expect
			.poll(() =>
				target.evaluate((element) => {
					const box = element.getBoundingClientRect();
					const banner = document.querySelector('header')!.getBoundingClientRect();
					const navigation = document
						.querySelector('nav[aria-label="주 메뉴"]')!
						.getBoundingClientRect();
					return banner.bottom <= box.top && box.bottom <= navigation.top;
				})
			)
			.toBe(true);
	};
	await page.keyboard.press('Tab');
	const point = page.locator('[data-chart-point]').last();
	await point.focus();
	await expectTooltipInViewport(page);
	await expectVisibleBounds(point);
	expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(683);
	const heart = healthSummaryChart(page, 'heart_rates');
	await heart.locator('[data-date-target="2026-10-20"]').focus();
	await page.keyboard.press('ArrowRight');
	const missing = heart.locator('[data-date-target="2026-10-21"]');
	await expect(missing).toBeFocused();
	await expect(heart.getByRole('tooltip')).toHaveCount(0);
	await expectVisibleBounds(missing);
});

for (const width of [1920, 1366, 768, 360]) {
	for (const [metric, cardIndex, values] of [
		['steps', 0, [10000, 500, 0, null, 3000, 7000, 1000]],
		['sleeps', 2, [480, 10, 0, null, 300, 400, 100]]
	] as const) {
		test(`${metric} ${width}px 막대 테두리 간격과 기존 상세 이동`, async ({ page }) => {
			await page.setViewportSize({ width, height: width >= 1366 ? 1080 : 800 });
			const data = fixture(metric, '2026-10-19', '2026-10-25');
			data.days = data.days.map((day, index) => ({
				...day,
				value: values[index],
				missing: index === 3,
				reason: index === 3 ? 'NO_RECORD' : null
			}));
			await page.route(`**/api/v1/health/metrics/${metric}?*`, (route) =>
				route.fulfill({ json: { data } })
			);
			await page.goto('/health', { waitUntil: 'networkidle' });
			await page.evaluate(() => document.fonts.ready);
			const card = page.locator('.health-summary-card').nth(cardIndex);
			const chart = card.getByRole('figure');
			const points = chart.locator('[data-chart-point]');
			const captures = resolve(artifacts, 'bar-spacing');
			mkdirSync(captures, { recursive: true });
			for (const [index, state] of ['large', 'small', 'zero'].entries()) {
				await hoverPoint(page, chart, points.nth(index), metric);
				await expect(page.getByRole('tooltip')).toBeVisible();
				const bounds = await chart.evaluate((element, index) => {
					const bar = element.querySelectorAll('svg rect')[index]?.getBoundingClientRect();
					const frame = element.querySelector('[data-bar-selection]');
					const target = element
						.querySelector(`[data-summary-tooltip-anchor="${index}"]`)
						?.getBoundingClientRect();
					const date = element
						.querySelectorAll('[data-chart-point]')
						[index]?.getBoundingClientRect();
					const metric = element.querySelector('[data-metric-target]')?.getBoundingClientRect();
					if (!bar || !frame || !target || !date || !metric) throw new Error('막대 선택 요소 없음');
					const outline = frame.getBoundingClientRect();
					const style = getComputedStyle(frame);
					return {
						gaps: [
							bar.left - outline.left - 2,
							outline.right - bar.right - 2,
							bar.top - outline.top - 2,
							outline.bottom - bar.bottom - 2
						],
						barHeight: bar.height,
						targetHeight: target.height,
						targetWidth: target.width,
						dateTargetWidth: date.width,
						dateTargetHeight: date.height,
						metricTargetHeight: metric.height,
						frameWidth: outline.width,
						frameHeight: outline.height,
						plotHeight: element.querySelector('svg')!.getBoundingClientRect().height,
						dateColumns: [...element.querySelectorAll('[data-date-target]')].map((node) => {
							const box = node.getBoundingClientRect();
							return { left: box.left, right: box.right };
						}),
						border: style.borderWidth,
						radius: style.borderRadius,
						color: style.borderColor,
						background: style.backgroundColor,
						pointerEvents: style.pointerEvents
					};
				}, index);
				for (const gap of bounds.gaps) expect(Math.abs(gap - 2.5)).toBeLessThan(0.05);
				expect(bounds.targetWidth).toBe(32);
				expect(bounds.targetHeight).toBeCloseTo(Math.max(32, bounds.barHeight + 16), 1);
				expect(bounds.metricTargetHeight).toBeCloseTo(bounds.plotHeight, 1);
				expect(bounds.dateTargetHeight).toBeGreaterThanOrEqual(24);
				for (let day = 1; day < bounds.dateColumns.length; day++)
					expect(bounds.dateColumns[day - 1].right).toBeLessThanOrEqual(
						bounds.dateColumns[day].left + 0.1
					);
				expect(bounds.border).toBe('2px');
				expect(bounds.radius).toBe('8px');
				expect(bounds.color).toBe('rgb(248, 90, 90)');
				expect(bounds.background).toBe('rgba(0, 0, 0, 0)');
				expect(bounds.pointerEvents).toBe('none');
				writeFileSync(
					resolve(captures, `after-${metric}-${width}-${state}.json`),
					JSON.stringify(bounds, null, 2)
				);
				await points.nth(index).focus();
				await expect(page).toHaveURL('/health');
				await expect(page.getByRole('tooltip')).toContainText(
					metric === 'sleeps'
						? formatSleepMinutes(values[index])
						: `${values[index]?.toLocaleString('ko-KR')} 보`
				);
				await card.screenshot({ path: resolve(captures, `after-${metric}-${width}-${state}.png`) });
				// 터치 영역은 유지하지만 마우스는 보이는 테두리 밖에서 선택을 해제한다.
				await page.keyboard.press('Escape');
				await card.getByRole('heading').hover();
				await points
					.nth(index)
					.hover({ position: { x: bounds.dateTargetWidth / 2, y: bounds.dateTargetHeight - 3 } });
				await expect(chart.getByRole('tooltip')).toHaveText('클릭하여 하루 상세 보기');
				await expect(chart.locator('[data-bar-selection]')).toHaveCount(0);
				await points.nth(index).click();
				await expect(page).toHaveURL(`/health/day/${data.days[index].date}`);
				await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
				await expect(page).toHaveURL('/health');
			}
			await chart.locator('[data-missing-point]').hover();
			await expect(chart.locator('[data-bar-selection]')).toHaveCount(0);
			await expect(chart.locator('[data-chart-point][data-date-target="2026-10-22"]')).toHaveCount(
				0
			);
			await expect(chart.locator('a[data-date-target="2026-10-22"]')).toHaveAttribute(
				'href',
				'/health/day/2026-10-22'
			);
			await expect(chart.locator('[data-summary-date="10.22"]')).toHaveCount(1);
			await expect(chart.getByRole('tooltip')).toHaveText('클릭하여 하루 상세 보기');
			await page.keyboard.press('Tab');
			await points.first().focus();
			await expect(points.first()).toHaveCSS('outline-width', '2px');
			await page.keyboard.press('Escape');
			await expect(page.getByRole('tooltip')).toHaveCount(0);
			await page.keyboard.press('ArrowRight');
			await expect(points.nth(1)).toBeFocused();
			// 툴팁이 열린 상태에서도 기존 제목 링크를 직접 클릭할 수 있다.
			await card.getByRole('heading').click();
			await expect(page).toHaveURL(
				metric === 'steps' ? '/health/activity?date=2026-10-25' : '/health/sleep?date=2026-10-25'
			);
		});
	}
}

for (const width of [1366, 360]) {
	for (const [cardIndex, [metric, , unit]] of metrics.entries()) {
		test(`${metric} ${width}px null 선택 제외와 결측 사유 유지`, async ({ page }) => {
			await page.setViewportSize({ width, height: 768 });
			const data = fixture(metric, '2026-10-19', '2026-10-25');
			const reasons = ['BEFORE_LINK', 'UNSYNCED', 'NULL_VALUE', 'NO_RECORD'] as const;
			const labels = ['연동 전', '동기화 대기', '값 없음', '기록 없음'];
			data.days = data.days.map((day, index) =>
				index === 0
					? day
					: index === 1
						? { ...day, value: 0, missing: false, reason: null }
						: {
								...day,
								value: null,
								missing: true,
								reason: index === 6 && metric === 'steps' ? 'ZERO_STEPS' : reasons[(index - 2) % 4]
							}
			);
			await page.route(`**/api/v1/health/metrics/${metric}?*`, (route) =>
				route.fulfill({ json: { data } })
			);
			await page.goto('/health', { waitUntil: 'networkidle' });
			const card = page.locator('.health-summary-card').nth(cardIndex);
			const chart = card.getByRole('figure');
			const points = chart.locator('[data-chart-point]');
			await expect(points).toHaveCount(2);
			await expect(chart.locator('[data-summary-date]')).toHaveCount(7);
			const captures = resolve(artifacts, 'null-selection');
			mkdirSync(captures, { recursive: true });
			const capture = async (state: string) => {
				// sticky 헤더가 캡처할 카드를 가리지 않도록 스크롤 위치만 맞춘다.
				await card.evaluate((element) => {
					const headerHeight =
						Number.parseFloat(
							getComputedStyle(document.documentElement).getPropertyValue('--app-header-height')
						) || 140;
					window.scrollBy(0, element.getBoundingClientRect().top - headerHeight - 12);
				});
				await card.screenshot({ path: resolve(captures, `${metric}-${width}-${state}.png`) });
				// 스크롤로 포인터 아래에 다른 차트가 온 경우도 다음 검증 전에 닫는다.
				await page.keyboard.press('Escape');
			};
			await hoverPoint(page, chart, points.first(), metric);
			await expect(page.getByRole('tooltip')).toBeVisible();
			await capture('normal');
			await points.nth(1).focus();
			await expect(page.getByRole('tooltip')).toContainText(
				metric === 'sleeps' ? '0시간 0분' : `0 ${unit}`
			);
			await capture('zero');
			const reasonCases = metric === 'steps' ? [...reasons, 'ZERO_STEPS'] : reasons;
			for (const [index, reason] of reasonCases.entries()) {
				const label = labels[index] ?? '기록 없음';
				await hoverPoint(page, chart, points.first(), metric);
				await expect(page.getByRole('tooltip')).toBeVisible();
				// null 자체는 선택하지 않는다. 키보드 focus 유지와 별개인 순수 hover를 검증한다.
				await points.evaluateAll((elements) =>
					elements.forEach((element) => element instanceof HTMLElement && element.blur())
				);
				const missing = chart.locator('[data-missing-point]').nth(index);
				await missing.hover();
				await expect(chart.getByRole('tooltip')).toHaveText('클릭하여 하루 상세 보기');
				await expect(chart.getByRole('tooltip').locator('strong')).toHaveCount(0);
				await expect(chart.locator('[data-bar-selection], [data-point-selection]')).toHaveCount(0);
				await expect(missing).toHaveCSS('border-width', '0px');
				await expect(chart.locator('[data-date-target][tabindex="0"]')).toHaveCount(1);
				await expect(missing).toHaveAttribute('href', `/health/day/${data.days[index + 2].date}`);
				await missing.click();
				await expect(page).toHaveURL(`/health/day/${data.days[index + 2].date}`);
				await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
				await expect(page).toHaveURL('/health');
				await expect(page.getByRole('tooltip')).toHaveCount(0);
				await expect(
					card.locator('.health-summary-link').locator('p').last().locator('span')
				).toHaveCount(0);
				await expect(card.locator('.health-summary-link').locator('p').last()).not.toContainText(
					'동기화 대기'
				);
				await expect(chart.locator('figcaption')).toContainText(
					`${data.days[index + 2].date.slice(5).replace('-', '.')}: ${label}`
				);
				await capture(reason);
			}
			await points.first().focus();
			await page.keyboard.press('ArrowRight');
			await expect(points.nth(1)).toBeFocused();
			await expect(page.getByRole('tooltip')).toContainText(
				metric === 'sleeps' ? '0시간 0분' : `0 ${unit}`
			);
			await page.keyboard.press('End');
			await expect(chart.locator('[data-date-target="2026-10-25"]')).toBeFocused();
			await expect(page.getByRole('tooltip')).toHaveCount(0);
			await page.keyboard.press('Home');
			await expect(points.first()).toBeFocused();
			await page.keyboard.press('ArrowRight');
			await expect(points.nth(1)).toBeFocused();
			await expect(page.getByRole('tooltip')).toContainText(
				metric === 'sleeps' ? '0시간 0분' : `0 ${unit}`
			);
			await page.keyboard.press('Escape');
			await expect(page.getByRole('tooltip')).toHaveCount(0);
		});
	}
}

for (const width of [1366, 360]) {
	for (const [metric] of metrics) {
		test(`${metric} ${width}px 마우스는 테두리·툴팁·좁은 연결 구간에서 유지한다`, async ({
			page
		}) => {
			await page.setViewportSize({ width, height: 800 });
			const data = fixture(metric, '2026-10-19', '2026-10-25');
			data.days[1] = { ...data.days[1], value: 0, missing: false, reason: null };
			await page.route(`**/api/v1/health/metrics/${metric}?*`, (route) =>
				route.fulfill({ json: { data } })
			);
			await page.goto('/health', { waitUntil: 'networkidle' });
			const chart = healthSummaryChart(page, metric);
			const points = chart.locator('[data-chart-point]');
			// 실제 막대만 노출하면 테두리의 내부 샘플까지 viewport 밖으로 나갈 수 있다.
			await chart.evaluate((element) =>
				element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' })
			);
			const dateAt = (position: [number, number]) =>
				chart.evaluate((element, [x, y]) => {
					const date = document
						.elementFromPoint(x, y)
						?.closest<HTMLAnchorElement>('a[data-date-target]');
					return date && element.contains(date) ? date.dataset.dateTarget : null;
				}, position);
			const expectDateGuide = async (date: string) => {
				const tooltip = chart.getByRole('tooltip');
				await expect(tooltip).toHaveText('클릭하여 하루 상세 보기');
				await expect(tooltip.locator('strong')).toHaveCount(0);
				await expect(chart.locator('[data-bar-selection], [data-point-selection]')).toHaveCount(0);
				const id = await tooltip.getAttribute('id');
				if (!id) throw new Error('날짜 안내 접근성 연결 없음');
				await expect(chart.locator(`[data-date-target="${date}"]`)).toHaveAttribute(
					'aria-describedby',
					id
				);
			};
			for (const index of [0, 1]) {
				for (const edge of ['left', 'right', 'top', 'bottom'] as const) {
					await hoverPoint(page, chart, points.nth(index), metric);
					await expect
						.poll(() =>
							chart.evaluate(
								(element) => element.closest('.health-summary-card')?.getAnimations().length ?? 0
							)
						)
						.toBe(0);
					await expect(chart.getByRole('tooltip')).toBeVisible();
					const box = await pointerSelection(chart, points.nth(index), metric).boundingBox();
					if (!box) throw new Error('선택 영역 없음');
					const { inside, outside } = pointerEdgePositions(box, edge);
					for (const [x, y] of [inside, outside]) {
						expect(x).toBeGreaterThanOrEqual(0);
						expect(x).toBeLessThan(width);
						expect(y).toBeGreaterThanOrEqual(0);
						expect(y).toBeLessThan(800);
					}
					const insideDate = await dateAt(inside);
					await page.mouse.move(inside[0], inside[1]);
					// 0값 선 그래프의 하단 경계는 실제 날짜 링크와 겹칠 수 있다.
					if (insideDate) {
						await expectDateGuide(insideDate);
						const outsideDate = await dateAt(outside);
						await page.mouse.move(outside[0], outside[1]);
						if (outsideDate) await expectDateGuide(outsideDate);
						else await expect(chart.getByRole('tooltip')).toHaveCount(0);
						await page.mouse.move(0, 0);
						await expect(chart.getByRole('tooltip')).toHaveCount(0);
						continue;
					}
					await expect(chart.getByRole('tooltip')).toBeVisible();
					await expect(chart.getByRole('tooltip')).not.toContainText('클릭하여 하루 상세 보기');
					const panel = await chart.getByRole('tooltip').boundingBox();
					if (!panel) throw new Error('툴팁 위치 없음');
					const outsideDate = await dateAt(outside);
					await page.mouse.move(outside[0], outside[1]);
					if (outsideDate) {
						await expectDateGuide(outsideDate);
						await page.mouse.move(0, 0);
						await expect(chart.getByRole('tooltip')).toHaveCount(0);
						continue;
					}
					const bridge = tooltipBridge(box, panel);
					const crossesBridge =
						(edge === 'top' && bridge.above) || (edge === 'bottom' && bridge.below);
					if (crossesBridge) {
						await expect(chart.getByRole('tooltip')).toBeVisible();
						await page.mouse.move(bridge.outsideX, outside[1]);
					}
					await expect(chart.getByRole('tooltip')).toHaveCount(0);
					await expect(chart.locator('[data-bar-selection]')).toHaveCount(0);
				}
			}
			await points.first().focus();
			await expect(chart.getByRole('tooltip')).toBeVisible();
			await page.keyboard.press('ArrowRight');
			await expect(points.nth(1)).toBeFocused();
			await expect(chart.getByRole('tooltip')).toBeVisible();
			await page.keyboard.press('Escape');
			await expect(chart.getByRole('tooltip')).toHaveCount(0);
		});
	}
}
