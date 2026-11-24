import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import {
	activityMetricFixture,
	mockActivityApi
} from '../../../../lib/testing/activity-api-fixture';

test.describe('일별 걸음 수 툴팁 입력', () => {
	test.use({ hasTouch: true });

	test('390×180px 스크롤 툴팁은 휠·키보드로 읽고 Escape 후 원래 날짜로 복귀한다', async ({
		page
	}) => {
		await page.setViewportSize({ width: 390, height: 180 });
		await openActivity(page);
		const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true });
		const point = chart.getByRole('link', { name: /하루 상세 보기$/ }).last();
		await point.evaluate((node) => {
			const rect = node.getBoundingClientRect();
			scrollTo(0, scrollY + rect.top + rect.height / 2 - 83);
			node.focus({ preventScroll: true });
		});
		const tooltip = page.getByRole('tooltip');
		await expect(tooltip).toHaveAttribute('data-scrollable', 'true');
		const before = await tooltip.evaluate((node) => ({
			box: node.getBoundingClientRect().toJSON(),
			client: node.clientHeight,
			scroll: node.scrollHeight,
			pageY: scrollY,
			pointer: getComputedStyle(node).pointerEvents
		}));
		expect(before.scroll).toBeGreaterThan(before.client);
		expect(before.pointer).toBe('auto');
		await page.mouse.move(
			before.box.x + before.box.width / 2,
			before.box.y + before.box.height / 2
		);
		await page.mouse.wheel(0, 120);
		await expect.poll(() => tooltip.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
		// 짧은 스크롤 영역에서도 안내 한 줄 전체를 실제 휠로 읽을 수 있다.
		await page.mouse.wheel(0, -10);
		await expect
			.poll(() =>
				tooltip.evaluate((node) => {
					const panel = node.getBoundingClientRect();
					const text = node.querySelector('p')!.getBoundingClientRect();
					return text.top >= panel.top + 1 && text.bottom <= panel.bottom - 1;
				})
			)
			.toBe(true);
		await expect(tooltip).toBeVisible();
		await expect(point).toBeFocused();
		expect(await page.evaluate(() => scrollY)).toBe(before.pageY);
		expect(
			await tooltip.evaluate((node) => {
				const box = node.getBoundingClientRect();
				const header = document.querySelector('header')!.getBoundingClientRect();
				const nav = document.querySelector('nav[aria-label="주 메뉴"]')!.getBoundingClientRect();
				return box.top >= header.bottom && box.bottom <= nav.top;
			})
		).toBe(true);
		await page.keyboard.press('Tab');
		await expect(tooltip).toBeFocused();
		// scrollTop이 0에 도달해도 Home의 브라우저 스크롤 동작이 끝나기 전일 수 있다.
		const homeScrollEnd = tooltip.evaluate((panel) => {
			if (panel.scrollTop === 0) return;
			return new Promise<void>((resolve) => {
				function handleScrollEnd(event: Event) {
					if (event.target !== panel || panel.scrollTop !== 0) return;
					panel.removeEventListener('scrollend', handleScrollEnd);
					resolve();
				}
				panel.addEventListener('scrollend', handleScrollEnd);
			});
		});
		await page.keyboard.press('Home');
		await expect.poll(() => tooltip.evaluate((node) => node.scrollTop)).toBe(0);
		await homeScrollEnd;
		await expect(tooltip).toBeFocused();
		await page.keyboard.press('End');
		await expect.poll(() => tooltip.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
		await page.keyboard.press('Escape');
		await expect(point).toBeFocused();
		await expect(tooltip).toHaveCount(0);
		await page.evaluate(
			() =>
				new Promise<void>((resolve) =>
					requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
				)
		);
		await expect(tooltip).toHaveCount(0);
		expect(await page.evaluate(() => scrollY)).toBe(before.pageY);
		const otherPoint = chart.getByRole('link', { name: /하루 상세 보기$/ }).first();
		const otherBounds = await otherPoint.boundingBox();
		if (!otherBounds) throw new Error('다른 날짜의 실제 위치 없음');
		await page.mouse.move(
			otherBounds.x + otherBounds.width / 2,
			otherBounds.y + otherBounds.height / 2
		);
		await expect(otherPoint).toHaveAttribute('aria-describedby', /.+/);
		await page.keyboard.press('Escape');
		await expect(tooltip).toHaveCount(0);
		await page.keyboard.press('Shift+Tab');
		await expect(tooltip).toBeVisible();
		await page.keyboard.press('Tab');
		await expect(point).toBeFocused();
		await expect(tooltip).toBeVisible();
	});

	for (const width of [390, 1366]) {
		test(`${width}px 일반 툴팁은 클릭을 통과시키고 키보드·마우스·터치 이동을 유지한다`, async ({
			page
		}) => {
			await page.setViewportSize({ width, height: 900 });
			await openActivity(page);
			const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true });
			const point = chart.getByRole('link', { name: /하루 상세 보기$/ }).last();
			await point.evaluate((node) => {
				const rect = node.getBoundingClientRect();
				scrollTo(0, scrollY + rect.top - innerHeight / 2);
				node.focus({ preventScroll: true });
			});
			const tooltip = page.getByRole('tooltip');
			await expect(tooltip).toHaveAttribute('data-scrollable', 'false');
			expect(await tooltip.evaluate((node) => getComputedStyle(node).pointerEvents)).toBe('none');
			await page.keyboard.press('Shift+Tab');
			await expect(tooltip).toBeVisible();
			await page.keyboard.press('Tab');
			await expect(point).toBeFocused();
			const user = page.getByRole('button', { name: '사용자 메뉴', exact: true });
			await user.click();
			await expect(user).toBeFocused();
			await expect(tooltip).toHaveCount(0);
			await page.keyboard.press('Escape');
			const bar = chart.locator('svg a[data-chart-point]').first();
			await bar.hover();
			await expect(bar).toHaveAttribute('aria-describedby', /.+/);
			await point.focus();
			// 설명 패널을 통과해 실제 아래쪽 날짜 링크를 클릭한다.
			const hit = await tooltip.evaluate((panel) => {
				const box = panel.getBoundingClientRect();
				for (let y = box.top + 2; y < box.bottom - 2; y += 2) {
					for (let x = box.left + 2; x < box.right - 2; x += 2) {
						const link = document.elementFromPoint(x, y)?.closest('a[data-chart-point]');
						const href = link?.getAttribute('href');
						if (href) return { x, y, href };
					}
				}
				return null;
			});
			expect(hit).not.toBeNull();
			if (!hit) throw new Error('툴팁 아래의 실제 날짜 링크 없음');
			await page.mouse.click(hit.x, hit.y);
			await expect(page).toHaveURL(hit.href);
			await openActivity(page);
			await point.tap();
			await expect(page).toHaveURL(`/health/day/${referenceDate}`);
		});
	}
});

type Scenario =
	| 'normal'
	| 'large'
	| 'empty'
	| 'one'
	| 'missing'
	| 'equal'
	| 'growth'
	| 'zero'
	| 'axis'
	| 'billion'
	| 'negative'
	| 'axis-boundary';
const referenceDate = '2026-11-09';
const referenceTime = '2026-11-09T03:00:00Z';
const dataStatus = {
	lastDataAt: '2026-11-09T02:50:00Z',
	syncedUntil: '2026-11-09T02:55:00Z',
	lastSyncStatus: 'success',
	lastSuccessAt: '2026-11-09T02:58:00Z',
	checkedAt: referenceTime
};

function responsiveMetricFixture(
	metric: 'steps' | 'exercises',
	start: string,
	end: string,
	scenario: Scenario
) {
	const data = activityMetricFixture(metric, start, end);
	let values: (number | null)[] =
		metric === 'steps' ? [4000, 8000, 10000, 7500, 3000, 9000, 8000] : [60, 0, 30, 40, 25, 55, 20];
	if (scenario === 'large') values = Array(7).fill(metric === 'steps' ? 100000 : 60);
	if (scenario === 'billion' && metric === 'steps') values = Array(7).fill(1_000_000_000);
	if (scenario === 'negative' && metric === 'steps')
		values = [-1000, 1, null, null, null, null, null];
	if (scenario === 'axis-boundary' && metric === 'steps') values = Array(7).fill(99999);
	if (scenario === 'axis' && metric === 'steps')
		values = [8200, 12344, 13000, 18234, 23150, 0, null];
	if (scenario === 'empty') values = Array(7).fill(null);
	if (scenario === 'zero') values = Array(7).fill(0);
	if (scenario === 'one')
		values = [null, null, null, metric === 'steps' ? 100000 : 60, null, null, null];
	if (scenario === 'missing')
		values = [null, 0, null, null, metric === 'steps' ? 100000 : 60, 0, null];
	if (scenario === 'equal') values = Array(7).fill(metric === 'steps' ? 5000 : 30);
	if (scenario === 'growth' && metric === 'steps' && end !== referenceDate) {
		values = values.map((value) => (value === null ? null : value / 2));
	}
	const days = data.days.map((day, index) => {
		const value = values[index % 7];
		const zeroSteps = metric === 'steps' && value === 0;
		const reason =
			day.date < data.summary.linkedFrom
				? 'BEFORE_LINK'
				: value !== null && !zeroSteps
					? null
					: day.date >= referenceDate
						? 'UNSYNCED'
						: zeroSteps
							? 'ZERO_STEPS'
							: 'NO_RECORD';
		return {
			...day,
			value: reason === null || zeroSteps ? value : null,
			missing: reason !== null,
			reason,
			isInProgress: day.date === referenceDate
		};
	});
	// 완료일에는 기록 없는 날도 포함한다. 통계에는 완료된 유효 기록만 사용한다.
	const completed = days.filter((day) => !day.isInProgress && day.reason !== 'BEFORE_LINK');
	const valid = completed.filter((day) => !day.missing);
	const total = valid.length ? valid.reduce((sum, day) => sum + (day.value ?? 0), 0) : null;
	const validValues = valid.map((day) => day.value ?? 0);
	const dated = (value: number) => {
		const day = valid.find((day) => day.value === value);
		return day ? { date: day.date, value } : null;
	};
	const progress = days.find((day) => day.isInProgress);
	const seed = activityMetricFixture('exercises', '2026-10-19', '2026-10-25').detail.sessions[0];
	const sessions =
		metric === 'exercises'
			? days.flatMap((day) =>
					day.value === null || day.value === 0
						? []
						: [
								{
									...seed,
									recordId: `00000000-0000-4000-8000-${day.date.replaceAll('-', '').padStart(12, '0')}`,
									date: day.date,
									durationMinutes: day.value,
									countsAsExercise: true,
									caloriesKcal: 123.45000000000002,
									distanceM: null,
									avgHeartRate: 72.1234567890123,
									startAt: `${day.date}T00:00:00Z`,
									endAt: new Date(
										Date.parse(`${day.date}T00:00:00Z`) + day.value * 60000
									).toISOString()
								}
							]
				)
			: [];
	return {
		...data,
		days,
		summary: {
			...data.summary,
			avg: total === null ? null : Math.round((total / valid.length) * 100) / 100,
			total,
			completedDays: completed.length,
			daysWithData: valid.length,
			daysUsedForSummary: valid.length,
			dailyMin: valid.length ? dated(Math.min(...validValues)) : null,
			dailyMax: valid.length ? dated(Math.max(...validValues)) : null,
			missingDays: days
				.filter((day) => ['NO_RECORD', 'NULL_VALUE', 'ZERO_STEPS'].includes(day.reason ?? ''))
				.map((day) => day.date),
			unsyncedDays: days.filter((day) => day.reason === 'UNSYNCED').map((day) => day.date)
		},
		inProgress: progress
			? {
					date: progress.date,
					value: progress.value,
					missing: progress.missing,
					asOf: dataStatus.lastDataAt
				}
			: null,
		dataStatus: { ...dataStatus },
		detail: { ...data.detail, sessions }
	};
}

async function openActivity(page: Page, scenario: Scenario = 'normal') {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await page.clock.setFixedTime(new Date(referenceTime));
	await page.route('**/api/v1/health/metrics/*?*', (route) => {
		const url = new URL(route.request().url());
		const metric = url.pathname.split('/').at(-1);
		if (metric !== 'steps' && metric !== 'exercises') return route.fallback();
		const data = responsiveMetricFixture(
			metric,
			url.searchParams.get('start') ?? '',
			url.searchParams.get('end') ?? '',
			scenario
		);
		return route.fulfill({ json: { data } });
	});
	await page.goto('/health/activity');
	await expect(page.locator('.activity-exercise')).toBeVisible();
	await page.evaluate(() => document.fonts.ready);
}

test('대표 fixture의 완료일·유효일·요약·동률 날짜가 계약상 예상값과 일치한다', () => {
	for (const [metric, scenario, count, total, avg, min, max] of [
		[
			'steps',
			'normal',
			6,
			41500,
			6916.67,
			{ date: '2026-11-07', value: 3000 },
			{ date: '2026-11-05', value: 10000 }
		],
		[
			'steps',
			'large',
			6,
			600000,
			100000,
			{ date: '2026-11-03', value: 100000 },
			{ date: '2026-11-03', value: 100000 }
		],
		[
			'steps',
			'equal',
			6,
			30000,
			5000,
			{ date: '2026-11-03', value: 5000 },
			{ date: '2026-11-03', value: 5000 }
		],
		['steps', 'empty', 0, null, null, null, null],
		[
			'steps',
			'one',
			1,
			100000,
			100000,
			{ date: '2026-11-06', value: 100000 },
			{ date: '2026-11-06', value: 100000 }
		],
		[
			'steps',
			'missing',
			1,
			100000,
			100000,
			{ date: '2026-11-07', value: 100000 },
			{ date: '2026-11-07', value: 100000 }
		],
		['steps', 'zero', 0, null, null, null, null],
		[
			'exercises',
			'normal',
			6,
			210,
			35,
			{ date: '2026-11-04', value: 0 },
			{ date: '2026-11-03', value: 60 }
		],
		[
			'exercises',
			'missing',
			3,
			60,
			20,
			{ date: '2026-11-04', value: 0 },
			{ date: '2026-11-07', value: 60 }
		],
		[
			'exercises',
			'zero',
			6,
			0,
			0,
			{ date: '2026-11-03', value: 0 },
			{ date: '2026-11-03', value: 0 }
		]
	] as const) {
		const result = responsiveMetricFixture(metric, '2026-11-03', referenceDate, scenario);
		expect(result.days).toHaveLength(7);
		expect(result.summary).toMatchObject({
			completedDays: 6,
			daysWithData: count,
			daysUsedForSummary: count,
			total,
			avg,
			dailyMin: min,
			dailyMax: max
		});
		expect(
			result.days.filter((day) => !day.missing).length +
				result.summary.missingDays.length +
				result.summary.unsyncedDays.length +
				result.days.filter((day) => day.reason === 'BEFORE_LINK').length
		).toBe(7);
	}
});

test('동기화 경계·진행 중 0·과거 기간·연동 전 날짜를 구분하고 fixture를 공유하지 않는다', () => {
	const steps = responsiveMetricFixture('steps', '2026-11-03', referenceDate, 'zero');
	expect(steps.summary.missingDays).toEqual([
		'2026-11-03',
		'2026-11-04',
		'2026-11-05',
		'2026-11-06',
		'2026-11-07',
		'2026-11-08'
	]);
	expect(steps.summary.unsyncedDays).toEqual(['2026-11-09']);
	expect(steps.inProgress).toEqual({
		date: '2026-11-09',
		value: 0,
		missing: true,
		asOf: '2026-11-09T02:50:00Z'
	});
	expect(steps.days.at(-1)?.reason).toBe('UNSYNCED');
	expect(steps.dataStatus).toEqual({
		lastDataAt: '2026-11-09T02:50:00Z',
		syncedUntil: '2026-11-09T02:55:00Z',
		lastSyncStatus: 'success',
		lastSuccessAt: '2026-11-09T02:58:00Z',
		checkedAt: '2026-11-09T03:00:00Z'
	});
	const exercise = responsiveMetricFixture('exercises', referenceDate, referenceDate, 'zero');
	expect(exercise.summary).toMatchObject({
		completedDays: 0,
		daysWithData: 0,
		avg: null,
		total: null
	});
	expect(exercise.inProgress).toEqual({
		date: '2026-11-09',
		value: 0,
		missing: false,
		asOf: '2026-11-09T02:50:00Z'
	});
	const past = responsiveMetricFixture('steps', '2026-10-27', '2026-11-02', 'equal');
	expect(past.inProgress).toBeNull();
	expect(past.summary).toMatchObject({
		completedDays: 7,
		daysWithData: 7,
		total: 35000,
		avg: 5000
	});
	const beforeLink = responsiveMetricFixture('steps', '2026-09-28', '2026-10-04', 'equal');
	expect(beforeLink.summary).toMatchObject({
		completedDays: 4,
		daysWithData: 4,
		total: 20000,
		dailyMin: { date: '2026-10-01', value: 5000 },
		dailyMax: { date: '2026-10-01', value: 5000 }
	});
	const snapshot = structuredClone(steps);
	exercise.days[0].value = 99;
	exercise.dataStatus.lastDataAt = '2000-01-01T00:00:00Z';
	expect(steps).toEqual(snapshot);
});

async function expectChartBounds(page: Page) {
	await expect(page.locator('.daily-steps-chart > div:first-child')).toHaveCSS('font-size', '12px');
	const result = await page.locator('.daily-steps-chart').evaluate((chart) => {
		const bounds = chart.getBoundingClientRect();
		const boxes = [...chart.querySelectorAll('[data-step-value]')].map((node) =>
			node.getBoundingClientRect()
		);
		const axes = [...chart.querySelectorAll(':scope > div:first-child span')].map((node) => {
			const range = document.createRange();
			range.selectNodeContents(node);
			return range.getBoundingClientRect();
		});
		const overlaps = (a: DOMRect, b: DOMRect) =>
			a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
		return {
			inside: boxes.every(
				(box) => box.left >= bounds.left && box.right <= bounds.right + 0.5 && box.top >= bounds.top
			),
			valuesOverlap: boxes.some((box, index) =>
				boxes.slice(index + 1).some((other) => overlaps(box, other))
			),
			axisOverlap: boxes.some((box) => axes.some((axis) => overlaps(box, axis)))
		};
	});
	expect(result).toEqual({ inside: true, valuesOverlap: false, axisOverlap: false });
	for (const figure of await page.locator('.daily-steps-chart, .activity-trend').all()) {
		const dates = figure.locator('a:not(svg a)');
		await expect(dates).toHaveCount(7);
		const measurements = await dates.evaluateAll((nodes) =>
			nodes.map((node) => {
				const range = document.createRange();
				const mobile = node.querySelector('.sm\\:hidden');
				const visible =
					mobile && getComputedStyle(mobile).display !== 'none'
						? mobile
						: (node.querySelector('.sm\\:inline') ?? node);
				range.selectNodeContents(visible);
				const box = range.getBoundingClientRect();
				const card = node.closest('section')!.getBoundingClientRect();
				return {
					left: box.left,
					right: box.right,
					font: getComputedStyle(node).fontSize,
					inside: box.left >= card.left && box.right <= card.right
				};
			})
		);
		for (let index = 0; index < measurements.length; index++) {
			expect(measurements[index].font).toBe('12px');
			expect(measurements[index].inside).toBe(true);
			if (index > 0)
				expect(measurements[index].left).toBeGreaterThanOrEqual(measurements[index - 1].right);
		}
	}
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

for (const width of [320, 360, 390, 414, 639, 640, 767, 768, 1023, 1024, 1366, 1535, 1536, 1920]) {
	test(`${width}px: 7px 원형과 선·날짜·조작 영역 정렬, 12px 날짜를 유지한다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 844 });
		await openActivity(page);
		const points = await page.locator('[data-trend-point]').evaluateAll((nodes) =>
			nodes.map((node) => {
				const box = node.getBoundingClientRect();
				const figure = node.closest('figure')!;
				const date = node.getAttribute('data-date');
				const link = figure.querySelector<HTMLAnchorElement>(`a[href$="/${date}"]`)!;
				const index = link.dataset.chartNavigation;
				const button = figure
					.querySelector(`[data-chart-point="${index}"]`)!
					.getBoundingClientRect();
				const label = link.getBoundingClientRect();
				const plot = figure.querySelector('svg')!.getBoundingClientRect();
				const coordinate = figure
					.querySelector('polyline')!
					.getAttribute('points')!
					.split(' ')
					[Number(index)].split(',')
					.map(Number);
				return {
					width: box.width,
					height: box.height,
					buttonWidth: button.width,
					buttonHeight: button.height,
					buttonX: Math.abs(box.x + box.width / 2 - button.x - button.width / 2),
					buttonY: Math.abs(box.y + box.height / 2 - button.y - button.height / 2),
					dateX: Math.abs(box.x + box.width / 2 - label.x - label.width / 2),
					lineX: Math.abs(box.x + box.width / 2 - plot.x - (coordinate[0] / 580) * plot.width),
					lineY: Math.abs(box.y + box.height / 2 - plot.y - coordinate[1])
				};
			})
		);
		expect(points.length).toBeGreaterThan(0);
		for (const point of points) {
			expect(point.width).toBe(7);
			expect(point.height).toBe(7);
			expect(point.buttonWidth).toBe(32);
			expect(point.buttonHeight).toBe(32);
			for (const difference of [
				point.buttonX,
				point.buttonY,
				point.dateX,
				point.lineX,
				point.lineY
			])
				expect(difference).toBeLessThan(0.1);
		}
		await expectChartBounds(page);
	});
}

for (const width of [320, 390, 1024]) {
	for (const scenario of ['large', 'empty', 'one', 'missing', 'equal', 'zero'] as const) {
		test(`${width}px ${scenario}: 원문 수치와 7일을 유지하며 내부 겹침을 방지한다`, async ({
			page
		}) => {
			await page.setViewportSize({ width, height: 844 });
			await openActivity(page, scenario);
			await expectChartBounds(page);
			if (width === 320 && (scenario === 'missing' || scenario === 'empty')) {
				await test.info().attach(`${scenario}-320`, {
					body: await page.screenshot({ fullPage: true }),
					contentType: 'image/png'
				});
			}
			if (scenario === 'large') {
				await expect(page.locator('[data-step-value]')).toHaveCount(7);
				for (const label of await page.locator('[data-step-value]').all())
					await expect(label).toHaveText('100,000');
				const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true });
				await expect(chart.locator(':scope > div').first().locator('span')).toHaveText([
					'120,000',
					'80,000',
					'40,000',
					'0'
				]);
				const averageY = Number(await chart.locator('[stroke-dasharray]').getAttribute('y1'));
				expect(averageY).toBeCloseTo(156 - (100_000 / 120_000) * 156);
				for (const bar of await chart.locator('rect').all())
					expect(Number(await bar.getAttribute('y'))).toBeCloseTo(averageY);
			}
		});
	}
}

for (const width of [390, 1366]) {
	test(`${width}px 음수 원본은 유지하고 차트 밖 좌표나 페이지 높이 증가를 만들지 않는다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		const response = page.waitForResponse((response) => {
			const url = new URL(response.url());
			return (
				url.pathname.endsWith('/health/metrics/steps') &&
				url.searchParams.get('end') === referenceDate
			);
		});
		await openActivity(page, 'negative');
		const body: { data: { days: { value: number | null }[]; summary: { avg: number | null } } } =
			await (await response).json();
		expect(body.data.days.slice(0, 2).map((day) => day.value)).toEqual([-1000, 1]);
		expect(body.data.summary.avg).toBe(-499.5);
		const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true });
		await expect(chart.locator('[data-step-value]')).toHaveText(['-1,000', '1']);
		await expect(chart.locator(':scope > div').first().locator('span')).toHaveText([
			'12,000',
			'8,000',
			'4,000',
			'0'
		]);
		await expect(chart.locator('rect')).toHaveCount(1);
		expect(Number(await chart.locator('rect').getAttribute('height'))).toBeCloseTo(0.013);
		expect(Number(await chart.locator('rect').getAttribute('y'))).toBeCloseTo(155.987);
		await expect(chart.locator('[stroke-dasharray]')).toHaveCount(0);
		await expect
			.poll(() =>
				chart.evaluate((figure) => {
					const bounds = figure.getBoundingClientRect();
					const values = [...figure.querySelectorAll('[data-step-value]')].map((node) =>
						node.getBoundingClientRect()
					);
					const bars = [...figure.querySelectorAll('rect')].map((node) => ({
						height: Number(node.getAttribute('height')),
						y: Number(node.getAttribute('y'))
					}));
					return {
						valuesInside: values.every(
							(value) =>
								value.top >= bounds.top &&
								value.bottom <= bounds.bottom &&
								value.left >= bounds.left &&
								value.right <= bounds.right
						),
						barsInside: bars.every(
							(bar) =>
								Number.isFinite(bar.height) &&
								Number.isFinite(bar.y) &&
								bar.height >= 0 &&
								bar.y >= 0 &&
								bar.height + bar.y <= 156
						),
						noPageOverflow:
							document.documentElement.scrollHeight <=
							Math.ceil(
								Math.max(innerHeight, document.body.getBoundingClientRect().bottom + scrollY)
							)
					};
				})
			)
			.toEqual({ valuesInside: true, barsInside: true, noPageOverflow: true });
		const date = chart.getByRole('link', { name: /^-1,000보 · / });
		await expect(date).toHaveAttribute('href', '/health/day/2026-11-03');
		await date.focus();
		await expect(date).toBeFocused();
		await expect(date).toHaveAttribute('aria-describedby', /.+/);
		await expect(page.getByRole('tooltip')).toHaveText('클릭하여 하루 상세 보기');
		await page.keyboard.press('Escape');
		await expect(date).toBeFocused();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await date.click();
		await expect(page).toHaveURL('/health/day/2026-11-03');
	});

	test(`${width}px 200% 확대: 걸음 눈금이 잘리거나 겹치지 않고 원본 값과 평균선을 유지한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		await openActivity(page, 'axis');
		await page.evaluate(() => {
			document.documentElement.style.zoom = '2';
		});
		await expect(page.locator('html')).toHaveCSS('zoom', '2');
		const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true });
		await chart.evaluate((figure) => figure.scrollIntoView({ block: 'center' }));
		await expect(chart.locator('[data-step-value]')).toHaveText([
			'8,200',
			'12,344',
			'13,000',
			'18,234',
			'23,150'
		]);
		await expect
			.poll(() =>
				chart.evaluate((figure) => {
					const bounds = figure.getBoundingClientRect();
					const axes = [...figure.querySelectorAll(':scope > div:first-child span')].map((node) => {
						const range = document.createRange();
						range.selectNodeContents(node);
						return range.getBoundingClientRect();
					});
					const values = [...figure.querySelectorAll('[data-step-value]')].map((node) =>
						node.getBoundingClientRect()
					);
					const overlaps = (a: DOMRect, b: DOMRect) =>
						a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
					return {
						axisCount: axes.length,
						inside: axes.every(
							(axis) =>
								axis.left >= 0 &&
								axis.right <= innerWidth &&
								axis.left >= bounds.left &&
								axis.right <= bounds.right &&
								axis.top >= bounds.top &&
								axis.bottom <= bounds.bottom
						),
						axisOverlap: axes.some((axis, index) =>
							axes.slice(index + 1).some((other) => overlaps(axis, other))
						),
						valueOverlap: values.some((value) => axes.some((axis) => overlaps(value, axis)))
					};
				})
			)
			.toEqual({ axisCount: 4, inside: true, axisOverlap: false, valueOverlap: false });
		const ticks = await chart.locator(':scope > div').first().locator('span').allTextContents();
		const maximum = Number(ticks[0].replaceAll(',', ''));
		const numericTicks = ticks.map((tick) => Number(tick.replaceAll(',', '')));
		expect(maximum).toBeGreaterThanOrEqual(23150);
		expect(numericTicks.at(-1)).toBe(0);
		expect(numericTicks[0] - numericTicks[1]).toBe(numericTicks[1] - numericTicks[2]);
		expect(numericTicks[1] - numericTicks[2]).toBe(numericTicks[2] - numericTicks[3]);
		const bars = chart.locator('rect');
		await expect(bars).toHaveCount(5);
		const firstHeight = Number(await bars.nth(0).getAttribute('height'));
		const fourthHeight = Number(await bars.nth(3).getAttribute('height'));
		expect(firstHeight / fourthHeight).toBeCloseTo(8200 / 18234);
		expect(firstHeight).toBeCloseTo((8200 / maximum) * 156);
		expect(Number(await chart.locator('[stroke-dasharray]').getAttribute('y1'))).toBeCloseTo(
			156 - (14985.6 / maximum) * 156
		);
		const date = chart.getByRole('link', { name: /^18,234보 · / });
		await date.focus();
		await expect(date).toBeFocused();
		await expect(date).toHaveAttribute('href', '/health/day/2026-11-06');
		await expect(date).toHaveAttribute('aria-describedby', /.+/);
		await expect(page.getByRole('tooltip')).toHaveText('클릭하여 하루 상세 보기');
		await page.keyboard.press('Escape');
		await expect(date).toBeFocused();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await chart.evaluate((figure) => figure.scrollIntoView({ block: 'center' }));
		await test.info().attach(`steps-axis-${width}-zoom-200`, {
			body: await chart.screenshot({ path: `test-results/steps-axis-${width}-zoom-200.png` }),
			contentType: 'image/png'
		});
	});

	test(`${width}px 매우 큰 걸음: 10억 보 원본과 눈금·평균선이 잘림 없이 표시된다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		await openActivity(page, 'billion');
		await expectChartBounds(page);
		const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true });
		await expect(chart.locator('[data-step-value]')).toHaveText(Array(7).fill('1,000,000,000'));
		await expect(chart.locator(':scope > div').first().locator('span')).toHaveText([
			'1,200,000,000',
			'800,000,000',
			'400,000,000',
			'0'
		]);
		const bars = chart.locator('rect');
		await expect(bars).toHaveCount(7);
		for (const bar of await bars.all())
			expect(Number(await bar.getAttribute('height'))).toBeCloseTo(130);
		expect(Number(await chart.locator('[stroke-dasharray]').getAttribute('y1'))).toBeCloseTo(26);
		await chart.evaluate((figure) => figure.scrollIntoView({ block: 'center' }));
		await test.info().attach(`steps-axis-${width}-billion`, {
			body: await chart.screenshot({ path: `test-results/steps-axis-${width}-billion.png` }),
			contentType: 'image/png'
		});
	});
}

test('490px CSS 200%에서 99,999보의 날짜 글자와 링크가 겹치지 않고 상세로 이동한다', async ({
	page
}) => {
	await page.setViewportSize({ width: 490, height: 900 });
	await openActivity(page, 'axis-boundary');
	await page.evaluate(() => {
		document.documentElement.style.zoom = '2';
	});
	await expect(page.locator('html')).toHaveCSS('zoom', '2');
	const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true });
	await chart.evaluate((figure) => figure.scrollIntoView({ block: 'center' }));
	await expect(chart.locator('[data-step-value]')).toHaveText(Array(7).fill('99,999'));
	await expect(chart.locator(':scope > div').first().locator('span')).toHaveText([
		'120,000',
		'80,000',
		'40,000',
		'0'
	]);
	await expect
		.poll(() =>
			chart.evaluate((figure) => {
				const plot = figure.children[1].getBoundingClientRect();
				const links = [...figure.querySelectorAll('a:not(svg a)')];
				const boxes = links.map((link) => link.getBoundingClientRect());
				const textBoxes = links.map((link) => {
					const range = document.createRange();
					range.selectNodeContents(link.querySelector('[aria-hidden="true"]') ?? link);
					return range.getBoundingClientRect();
				});
				const overlaps = (a: DOMRect, b: DOMRect) =>
					a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
				return {
					count: links.length,
					inside: boxes.every(
						(box) =>
							box.left >= plot.left &&
							box.right <= plot.right &&
							box.left >= 0 &&
							box.right <= innerWidth
					),
					textInsideLink: textBoxes.every(
						(text, index) =>
							text.left >= boxes[index].left &&
							text.right <= boxes[index].right &&
							text.top >= boxes[index].top &&
							text.bottom <= boxes[index].bottom
					),
					textOverlap: textBoxes.some((text, index) =>
						textBoxes.slice(index + 1).some((other) => overlaps(text, other))
					),
					linkOverlap: boxes.some((box, index) =>
						boxes.slice(index + 1).some((other) => overlaps(box, other))
					),
					noHorizontalOverflow: document.documentElement.scrollWidth <= innerWidth
				};
			})
		)
		.toEqual({
			count: 7,
			inside: true,
			textInsideLink: true,
			textOverlap: false,
			linkOverlap: false,
			noHorizontalOverflow: true
		});
	const dates = chart.getByRole('link', { name: /하루 상세 보기$/ });
	for (let index = 0; index < 7; index++) {
		await expect(dates.nth(index)).toHaveAttribute(
			'href',
			`/health/day/2026-11-${String(index + 3).padStart(2, '0')}`
		);
	}
	const date = dates.nth(3);
	await date.focus();
	await expect(date).toBeFocused();
	await expect(page.getByRole('tooltip')).toHaveText('클릭하여 하루 상세 보기');
	await page.keyboard.press('Escape');
	await expect(date).toBeFocused();
	await expect(page.getByRole('tooltip')).toHaveCount(0);
	await chart.evaluate((figure) => figure.scrollIntoView({ block: 'center' }));
	await test.info().attach('steps-axis-490-zoom-200-boundary', {
		body: await chart.screenshot({ path: 'test-results/steps-axis-490-zoom-200-boundary.png' }),
		contentType: 'image/png'
	});
	await date.click();
	await expect(page).toHaveURL('/health/day/2026-11-06');
});

test('긴 요청 ID·오류 원문은 카드 안에서 줄바꿈되고 재시도할 수 있다', async ({ page }) => {
	await page.setViewportSize({ width: 320, height: 700 });
	await mockSignedIn(page);
	const requestId = 'R'.repeat(180);
	const message = '조회실패'.repeat(60);
	await page.route('**/api/v1/health/metrics/*?*', (route) =>
		route.fulfill({
			status: 503,
			headers: { 'X-Request-Id': requestId },
			json: { error: { code: 'LOOKUP_FAILED', message, retryable: true } }
		})
	);
	await page.goto('/health/activity');
	const alert = page.getByRole('alert');
	await expect(alert).toContainText(requestId);
	await expect(alert).toContainText(message);
	expect(await alert.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
	await expect(alert.getByRole('button', { name: '다시 시도', exact: true })).toBeEnabled();
	await page.unroute('**/api/v1/health/metrics/*?*');
	await mockActivityApi(page);
	await alert.getByRole('button', { name: '다시 시도', exact: true }).click();
	await expect(page.locator('.activity-exercise')).toBeVisible();
});

test('작은 강조 텍스트는 실제 카드 배경에서 4.5:1 이상의 대비를 제공한다', async ({ page }) => {
	await openActivity(page, 'growth');
	const colors = await page.locator('.exercise-card > div:first-child > p').evaluateAll((nodes) =>
		nodes.map((node) => ({
			foreground: getComputedStyle(node).color,
			background: getComputedStyle(node.closest('section')!).backgroundColor
		}))
	);
	const luminance = (color: string) => {
		const channels = color
			.match(/\d+/g)!
			.slice(0, 3)
			.map(Number)
			.map((value) => value / 255)
			.map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
		return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
	};
	expect(colors).toHaveLength(4);
	for (const color of colors)
		expect(
			(luminance(color.background) + 0.05) / (luminance(color.foreground) + 0.05)
		).toBeGreaterThanOrEqual(4.5);
	const change = await page
		.getByRole('region', { name: '걸음 수 기록' })
		.getByText(/^\+/)
		.evaluate((node) => ({
			foreground: getComputedStyle(node).color,
			background: getComputedStyle(node.parentElement!).backgroundColor
		}));
	expect(
		(luminance(change.background) + 0.05) / (luminance(change.foreground) + 0.05)
	).toBeGreaterThanOrEqual(4.5);
});

test('증가·감소·0·null의 기존 문구를 유지하고 실제 배경 대비를 확보한다', async ({ page }) => {
	await page.setViewportSize({ width: 320, height: 844 });
	for (const [scenario, expected] of [
		['growth', /^\+95\.6%$/],
		['normal', /^-2\.2%$/],
		['equal', /^0\.0%$/],
		['empty', /^—$/]
	] as const) {
		await openActivity(page, scenario);
		const label = page.getByRole('region', { name: '걸음 수 기록' }).getByText(expected);
		await expect(label).toBeVisible();
		await expect(label).toHaveCSS('color', 'rgb(44, 46, 53)');
		await test.info().attach(`change-${scenario}-320`, {
			body: await label.locator('..').screenshot(),
			contentType: 'image/png'
		});
		const contrast = await label.evaluate((node) => {
			const luminance = (color: string) => {
				const channels = (color.match(/\d+/g) ?? [])
					.slice(0, 3)
					.map(Number)
					.map((value) => value / 255)
					.map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
				return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
			};
			return (
				(luminance(getComputedStyle(node.parentElement!).backgroundColor) + 0.05) /
				(luminance(getComputedStyle(node).color) + 0.05)
			);
		});
		expect(contrast).toBeGreaterThanOrEqual(4.5);
	}
});
