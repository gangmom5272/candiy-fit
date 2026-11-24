import { expect, test, type Locator } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockActivityApi } from '../../../lib/testing/activity-api-fixture';
import { mockSleepApi, sleepMetricFixture } from '../../../lib/testing/sleep-api-fixture';
import { mockHeartApi, heartMetricFixture } from '../../../lib/testing/heart-api-fixture';
import {
	healthSummaryChart,
	hoverSummaryPoint as hoverPoint,
	tooltipPointerSelection,
	tooltipBridge
} from '../../../lib/testing/tooltip-pointer';

const artifacts = resolve('../../test-results/health-home-220/final-review');
type Metric = 'steps' | 'exercises' | 'sleeps' | 'heart_rates';

function pointerSelection(chart: Locator, point: Locator, metric: Metric) {
	return metric === 'steps' || metric === 'sleeps'
		? tooltipPointerSelection(chart, point, metric)
		: chart.locator('[data-point-selection]');
}

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await mockHeartApi(page);
	await page.clock.setFixedTime(new Date('2026-10-25T03:00:00Z'));
	mkdirSync(artifacts, { recursive: true });
});

for (const width of [1366, 360]) {
	for (const metric of ['steps', 'exercises', 'sleeps', 'heart_rates'] as const) {
		test(`${metric} ${width}px B안: 연결 통로·툴팁·focus 유지 및 Escape 재진입`, async ({
			page
		}) => {
			await page.setViewportSize({ width, height: 800 });
			await page.goto('/health', { waitUntil: 'networkidle' });
			const chart = healthSummaryChart(page, metric);
			const point = chart.getByRole('link', { name: /^2026-10-19 / });
			const tooltip = chart.getByRole('tooltip');
			const selection = pointerSelection(chart, point, metric);
			await hoverPoint(page, chart, point, metric);
			await expect(tooltip).toBeVisible();
			const bounds = await selection.boundingBox();
			const panel = await tooltip.boundingBox();
			if (!bounds || !panel) throw new Error('선택 또는 툴팁 위치 없음');
			const bridge = tooltipBridge(bounds, panel);
			for (let step = 0; step <= 6; step++) {
				await page.mouse.move(bridge.x, bridge.fromY + ((bridge.toY - bridge.fromY) * step) / 6);
				await expect(tooltip).toBeVisible();
			}
			await tooltip.hover();
			await expect(tooltip).toContainText('2026.10.19');
			await page.screenshot({ path: resolve(artifacts, `b-hoverable-${metric}-${width}.png`) });
			// 통로의 바로 옆은 hover 영역이 아니다. 넓은 사각형으로 확장하지 않는다.
			await page.mouse.move(bridge.outsideX, bridge.gapY);
			await expect(tooltip).toHaveCount(0);
			await hoverPoint(page, chart, point, metric);
			await expect(tooltip).toBeVisible();
			await page.keyboard.press('Escape');
			await expect(tooltip).toHaveCount(0);
			const target = await chart
				.locator(`[data-summary-tooltip-anchor="${await point.getAttribute('data-chart-point')}"]`)
				.boundingBox();
			if (!target) throw new Error('데이터 지점 위치 없음');
			await page.mouse.move(target.x + target.width / 2 + 0.5, target.y + target.height / 2);
			await expect(tooltip).toHaveCount(0);
			await page.mouse.move(0, 0);
			await hoverPoint(page, chart, point, metric);
			await expect(tooltip).toBeVisible();
			await page.keyboard.press('Tab');
			await point.focus();
			await page.mouse.move(0, 0);
			await expect(point).toBeFocused();
			await expect(tooltip).toContainText('2026.10.19');
			await hoverPoint(page, chart, chart.getByRole('link', { name: /^2026-10-25 / }), metric);
			await expect(tooltip).toContainText('2026.10.25');
			await page.mouse.move(0, 0);
			await expect(tooltip).toContainText('2026.10.19');
			await page.keyboard.press('Escape');
			await page.mouse.move(1, 1);
			await expect(tooltip).toHaveCount(0);
			await expect(point).toBeFocused();
			await page.keyboard.press('ArrowRight');
			await expect(tooltip).toBeVisible();
			await expect(chart.locator('[data-chart-point]').nth(1)).toBeFocused();
		});
	}
}

for (const [index, metric] of [
	[0, 'steps'],
	[2, 'sleeps']
] as const) {
	test(`${metric} Escape로 툴팁을 닫아도 키보드 포커스 표시는 남는다`, async ({ page }) => {
		await page.setViewportSize({ width: 1366, height: 768 });
		await page.goto('/health', { waitUntil: 'networkidle' });
		const chart = page.locator('.health-summary-card').nth(index).getByRole('figure');
		const point = chart.locator('[data-chart-point]').first();
		await page.keyboard.press('Tab');
		await point.focus();
		await expect(chart.getByRole('tooltip')).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(chart.getByRole('tooltip')).toHaveCount(0);
		await expect(point).toBeFocused();
		await page.screenshot({ path: resolve(artifacts, `escape-focus-${metric}.png`) });
		await expect(point).toHaveCSS('outline-style', 'solid');
		await expect(point).toHaveCSS('outline-width', '2px');
	});
}

test('수면 0·1·59·60·61·376분의 툴팁·접근성 표시는 실제 분과 대응한다', async ({ page }) => {
	const cases = [
		[0, '0시간 0분'],
		[1, '0시간 1분'],
		[59, '0시간 59분'],
		[60, '1시간 0분'],
		[61, '1시간 1분'],
		[376, '6시간 16분']
	] as const;
	const data = sleepMetricFixture('sleeps', '2026-10-19', '2026-10-25');
	data.days = data.days.map((day, index) => ({
		...day,
		value: cases[index]?.[0] ?? null,
		missing: index === 6,
		reason: index === 6 ? 'NULL_VALUE' : null
	}));
	await page.route('**/api/v1/health/metrics/sleeps?*', (route) =>
		route.fulfill({ json: { data } })
	);
	await page.goto('/health', { waitUntil: 'networkidle' });
	const chart = page.getByRole('figure', { name: '수면 주간 기록' });
	await expect(chart.locator('[data-chart-point]')).toHaveCount(6);
	await page.keyboard.press('Tab');
	for (const [index, [, expected]] of cases.entries()) {
		const point = chart.locator('[data-chart-point]').nth(index);
		await point.focus();
		await expect(chart.getByRole('tooltip')).toContainText(expected);
		await expect(point).toHaveAccessibleName(new RegExp(expected));
		await expect(chart.locator('figcaption')).toContainText(
			`${data.days[index].date.slice(5).replace('-', '.')}: ${expected}`
		);
		await page.screenshot({ path: resolve(artifacts, `sleep-${cases[index][0]}min.png`) });
	}
});

test('심박 단일 정상값은 연속 결측과 기간 평균·측정 최솟값을 혼동하지 않는다', async ({ page }) => {
	const data = heartMetricFixture('2026-10-19', '2026-10-25');
	data.days = data.days.map((day, index) => ({
		...day,
		value: index === 2 ? 74.25 : null,
		missing: index !== 2,
		reason: index === 2 ? null : 'NO_RECORD'
	}));
	data.summary.avg = 90;
	data.detail.measuredMin = { date: '2026-10-21', value: 42 };
	await page.route('**/api/v1/health/metrics/heart_rates?*', (route) =>
		route.fulfill({ json: { data } })
	);
	await page.goto('/health', { waitUntil: 'networkidle' });
	const chart = page.getByRole('figure', { name: '일별 심박수 주간 기록' });
	await expect(chart.locator('[data-chart-point]')).toHaveCount(1);
	await expect(chart.locator('[data-summary-date]')).toHaveCount(7);
	await expect(chart.locator('polyline')).toHaveCount(1);
	const points = await chart.locator('polyline').getAttribute('points');
	expect(points?.trim().split(/\s+/)).toHaveLength(1);
	await chart.locator('[data-chart-point]').focus();
	await expect(chart.getByRole('tooltip')).toContainText('74.25 bpm');
	await expect(chart.getByRole('tooltip')).not.toContainText('90 bpm');
	await expect(chart.getByRole('tooltip')).not.toContainText('42 bpm');
});

test('KST 자정 양쪽 진입과 재진입은 날짜·주간 조회 범위·오늘 제외 안내를 유지한다', async ({
	page
}) => {
	const queries: string[] = [];
	const beforeMidnight = new Date('2026-10-25T14:59:59Z');
	const afterMidnight = new Date(beforeMidnight.getTime() + 1000);
	// 기대값은 운영 날짜 helper 대신 KST 달력 날짜와 7일 계약으로 구한다.
	const expectedPeriod = (instant: Date) => {
		const end = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(instant);
		const start = new Date(Date.parse(`${end}T00:00:00Z`) - 6 * 86400000)
			.toISOString()
			.slice(0, 10);
		const labelEnd = start.slice(0, 4) === end.slice(0, 4) ? end.slice(5) : end;
		return {
			start,
			end,
			label: `${start.replaceAll('-', '.')} ~ ${labelEnd.replaceAll('-', '.')}`,
			description: `· ${start.replaceAll('-', '.')} ~ ${labelEnd.replaceAll('-', '.')} · 기준일 ${end.replaceAll('-', '.')}`
		};
	};
	const before = expectedPeriod(beforeMidnight);
	const after = expectedPeriod(afterMidnight);
	page.on('request', (request) => {
		if (request.url().includes('/health/metrics/')) queries.push(request.url());
	});
	await page.clock.setFixedTime(beforeMidnight);
	await page.goto('/health', { waitUntil: 'networkidle' });
	const period = page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status');
	const legend = page.locator('main > p');
	await expect(period).toHaveText(before.label);
	await expect(legend).toBeVisible();
	await expect(legend).toContainText('— 기록 없음');
	await expect(legend.locator('span')).toHaveText(before.description);
	await page.clock.setFixedTime(afterMidnight);
	// 열린 페이지의 날짜 갱신 여부는 기존 동작을 기록한다. 자동 갱신 정책을 새로 도입하지 않는다.
	writeFileSync(
		resolve(artifacts, 'midnight-open-page.json'),
		JSON.stringify(
			{
				requestsBeforeReload: queries.length,
				period: await period.textContent()
			},
			null,
			2
		)
	);
	const heartResponse = page.waitForResponse((response) => {
		const url = new URL(response.url());
		return (
			url.pathname.endsWith('/health/metrics/heart_rates') &&
			url.searchParams.get('end') === after.end
		);
	});
	await page.reload({ waitUntil: 'networkidle' });
	await expect(period).toHaveText(after.label);
	await expect(legend).toBeVisible();
	await expect(legend).toContainText('— 기록 없음');
	await expect(legend.locator('span')).toHaveText(after.description);
	const heart = page.locator('.health-summary-card').last();
	await expect(heart).not.toContainText('오늘 기록은 기간 평균에서 제외해요.');
	const { data: heartData } = await (await heartResponse).json();
	expect(heartData.summary.avg).not.toBeNull();
	await expect(heart.getByText(`평균 ${heartData.summary.avg} bpm`, { exact: true })).toBeVisible();
	expect(queries).toHaveLength(8);
	for (const [requests, expected] of [
		[queries.slice(0, 4), before],
		[queries.slice(4), after]
	] as const)
		for (const url of requests) {
			expect(new URL(url).searchParams.get('start')).toBe(expected.start);
			expect(new URL(url).searchParams.get('end')).toBe(expected.end);
		}
});
