import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { activityMetricFixture, mockActivityApi } from '../../../lib/testing/activity-api-fixture';
import { mockSleepApi } from '../../../lib/testing/sleep-api-fixture';
import { mockHeartApi } from '../../../lib/testing/heart-api-fixture';
import { mockDayDetailApi } from '../../../lib/testing/day-detail-api-fixture';

const metrics = ['steps', 'exercises', 'sleeps', 'heart_rates'] as const;
type Metric = (typeof metrics)[number];
const completedPeriod = '2026.10.19 ~ 10.25';
function metricPaths(referenceDate: string): Record<Metric, string> {
	return {
		steps: `/health/activity?date=${referenceDate}`,
		exercises: `/health/activity?date=${referenceDate}#exercise`,
		sleeps: `/health/sleep?date=${referenceDate}`,
		heart_rates: `/health/heart?date=${referenceDate}`
	};
}

async function installHealthApi(page: Page, today = '2026-11-01') {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await mockHeartApi(page);
	await mockDayDetailApi(page);
	// 각 fixture의 기본 날짜를 모두 설정한 다음 이 시나리오의 KST 기준일을 고정한다.
	await page.clock.setFixedTime(new Date(`${today}T03:00:00Z`));
}

function card(page: Page, metric: Metric) {
	return page.locator(`#health-summary-${metric}`);
}

function chart(page: Page, metric: Metric) {
	return card(page, metric).getByRole('figure');
}

function dateTarget(page: Page, metric: Metric, date: string) {
	return chart(page, metric).locator(`a[data-date-target="${date}"]`);
}

async function mainLoaded(page: Page) {
	await expect(page).toHaveURL('/health');
	await expect(page.locator('.health-summary-card[aria-busy="false"]')).toHaveCount(4);
	await expect(page.locator('.health-summary-card figure')).toHaveCount(4);
	await expect(page.locator('[data-health-chart-scroll]')).toHaveCount(4);
	await page.evaluate(() => document.fonts.ready);
}

async function completedMain(page: Page) {
	await page.goto('/health');
	await mainLoaded(page);
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		completedPeriod
	);
	await mainLoaded(page);
}

function trackQueries(page: Page) {
	const queries: URL[] = [];
	page.on('request', (request) => {
		const url = new URL(request.url());
		if (
			request.method() === 'GET' &&
			(url.pathname.startsWith('/api/v1/health/metrics/') ||
				url.pathname === '/api/v1/health/timeline')
		)
			queries.push(url);
	});
	return queries;
}

function dailyResponses(page: Page, date: string) {
	return Promise.all(
		[...metrics.map((metric) => `/api/v1/health/metrics/${metric}`), '/api/v1/health/timeline'].map(
			(path) =>
				page.waitForResponse((response) => {
					const url = new URL(response.url());
					return (
						response.request().method() === 'GET' &&
						response.status() === 200 &&
						url.pathname === path &&
						url.searchParams.get('start') === date &&
						url.searchParams.get('end') === date
					);
				})
		)
	).then((responses) => Promise.all(responses.map((response) => response.finished())));
}

async function dayLoaded(page: Page, date: string) {
	await expect(page).toHaveURL(`/health/day/${date}`);
	const [, month, day] = date.split('-').map(Number);
	await expect(
		page.getByRole('heading', { name: `${month}월 ${day}일 하루 상세`, exact: true })
	).toBeVisible();
	await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveCount(4);
}

async function clickPixels(page: Page, target: Locator) {
	await target.scrollIntoViewIfNeeded();
	const bounds = await target.boundingBox();
	if (!bounds) throw new Error('클릭할 날짜 표시 없음');
	expect(bounds.width).toBeGreaterThan(0);
	expect(bounds.height).toBeGreaterThan(0);
	await page.mouse.click(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
}

async function metricClick(
	page: Page,
	metric: Metric,
	click: () => Promise<void>,
	referenceDate = '2026-10-25'
) {
	const expectedPath = metricPaths(referenceDate)[metric];
	const target = chart(page, metric).locator('a[data-metric-target]');
	await expect(target).toHaveAttribute('href', expectedPath);
	await expect(target).toHaveAttribute('tabindex', '-1');
	const navigations: string[] = [];
	const record = (frame: import('@playwright/test').Frame) => {
		if (frame === page.mainFrame()) {
			const url = new URL(frame.url());
			navigations.push(url.pathname + url.search + url.hash);
		}
	};
	page.on('framenavigated', record);
	try {
		await click();
		await expect(page).toHaveURL(expectedPath);
		await expect(
			page
				.getByRole('navigation', { name: '건강 데이터', exact: true })
				.locator('[aria-current="page"]')
		).toHaveText(metric === 'sleeps' ? '수면' : metric === 'heart_rates' ? '심박' : '활동');
		// 복귀 상태를 남기는 원본 /health의 shallow replace 1회와 대상 이동 1회를 순서대로 검증한다.
		expect(navigations).toEqual(['/health', expectedPath]);
	} finally {
		page.off('framenavigated', record);
	}
}

async function position(page: Page, metric: Metric) {
	return card(page, metric).evaluate((element) => ({
		scrollY,
		// 기존 hover의 top:-2px 장식·전환을 제외하고 문서 레이아웃 위치를 비교한다.
		top:
			element.getBoundingClientRect().top - (Number.parseFloat(getComputedStyle(element).top) || 0),
		chartScrollLeft: [...document.querySelectorAll<HTMLElement>('[data-health-chart-scroll]')].map(
			(chart) => chart.scrollLeft
		)
	}));
}

async function clickPosition(page: Page, metric: Metric, target: Locator) {
	let capture!: (value: Awaited<ReturnType<typeof position>>) => void;
	const captured = new Promise<Awaited<ReturnType<typeof position>>>(
		(resolve) => (capture = resolve)
	);
	await page.exposeFunction('__captureHealthClick356', capture);
	await card(page, metric).evaluate((element) => {
		element.addEventListener(
			'click',
			() => {
				const send = (
					window as unknown as {
						__captureHealthClick356: (value: {
							scrollY: number;
							top: number;
							chartScrollLeft: number[];
						}) => Promise<void>;
					}
				).__captureHealthClick356;
				void send({
					scrollY,
					top:
						element.getBoundingClientRect().top -
						(Number.parseFloat(getComputedStyle(element).top) || 0),
					chartScrollLeft: [
						...document.querySelectorAll<HTMLElement>('[data-health-chart-scroll]')
					].map((chart) => chart.scrollLeft)
				});
			},
			{ capture: true, once: true }
		);
	});
	// Locator.click의 자동 스크롤까지 끝난 실제 선택 시점, 제품 handler 이전에 측정한다.
	await target.click();
	return captured;
}

async function restored(
	page: Page,
	metric: Metric,
	before: Awaited<ReturnType<typeof position>>,
	period = completedPeriod
) {
	await mainLoaded(page);
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		period
	);
	await expect
		.poll(async () => {
			const after = await position(page, metric);
			return (
				Math.abs(after.scrollY - before.scrollY) <= 1 &&
				Math.abs(after.top - before.top) <= 1 &&
				after.chartScrollLeft.length === before.chartScrollLeft.length &&
				after.chartScrollLeft.every(
					(value, index) => Math.abs(value - before.chartScrollLeft[index]) <= 1
				)
			);
		})
		.toBe(true);
}

test.beforeEach(async ({ page }) => {
	await installHealthApi(page);
});

for (const [metric, values] of [
	['steps', ['8,120 보', '7,680 보', null, '9,240 보', '7,760 보']],
	['exercises', ['60 분', '38 분', '0 분', '42 분', '15 분']],
	['sleeps', ['6시간 16분', '6시간 39분', '6시간 26분', null, '6시간 20분']],
	['heart_rates', ['76 bpm', '73 bpm', null, '78 bpm', '75.8 bpm']]
] as const) {
	test(`${metric}: 날짜에 멈춘 포인터는 키보드 값 툴팁을 덮지 않고 실제 마우스 이동은 날짜 안내를 연다`, async ({
		page
	}) => {
		await page.setViewportSize({ width: 1366, height: 768 });
		await completedMain(page);
		const selected = chart(page, metric);
		await expect(dateTarget(page, metric, '2026-10-19')).toHaveCount(1);
		await selected.evaluate((element) =>
			element.scrollIntoView({ block: 'center', behavior: 'instant' })
		);
		const tooltip = selected.getByRole('tooltip');
		const initialDate = dateTarget(page, metric, '2026-10-20');
		await initialDate.hover();
		await expect(tooltip).toHaveText('클릭하여 하루 상세 보기');

		async function expectKeyboardValue(date: string, value: string | null) {
			const target = dateTarget(page, metric, date);
			await expect(target).toBeFocused();
			expect(await target.evaluate((element) => element.matches(':focus-visible'))).toBe(true);
			// 패널의 RAF 재배치와 그로 인한 native pointer 경계 이벤트까지 반영한다.
			await page.evaluate(
				() =>
					new Promise<void>((resolve) =>
						requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
					)
			);
			if (value === null) {
				await expect(tooltip).toHaveCount(0);
				await expect(selected.locator('[data-date-target][aria-describedby]')).toHaveCount(0);
			} else {
				await expect(tooltip).toBeVisible();
				await expect(tooltip).toContainText(date.replaceAll('-', '.'));
				await expect(tooltip.locator('strong')).toHaveText(value);
				await expect(tooltip).not.toContainText('클릭하여 하루 상세 보기');
				const id = await tooltip.getAttribute('id');
				expect(id).toBeTruthy();
				await expect(target).toHaveAttribute('aria-describedby', id!);
				await expect(selected.locator('[data-date-target][aria-describedby]')).toHaveCount(1);
			}
		}

		const pointerMoves = await page.evaluateHandle(() => {
			const moves: { x: number; y: number }[] = [];
			const record = (event: PointerEvent) => {
				if (event.pointerType === 'mouse') moves.push({ x: event.clientX, y: event.clientY });
			};
			window.addEventListener('pointermove', record);
			return { moves, stop: () => window.removeEventListener('pointermove', record) };
		});
		try {
			const heading = card(page, metric).getByRole('heading', { level: 2 });
			await heading.evaluate((element) => {
				element.setAttribute('tabindex', '-1');
				element.focus({ preventScroll: true });
			});
			await page.keyboard.press('Tab');
			await heading.evaluate((element) => element.removeAttribute('tabindex'));
			await expectKeyboardValue('2026-10-19', values[0]);
			await page.keyboard.press('Home');
			await expectKeyboardValue('2026-10-19', values[0]);
			for (const [index, date] of ['2026-10-20', '2026-10-21', '2026-10-22'].entries()) {
				await page.keyboard.press('ArrowRight');
				await expectKeyboardValue(date, values[index + 1]);
			}
			await page.keyboard.press('End');
			await expectKeyboardValue('2026-10-25', values[4]);
			await page.keyboard.press('Home');
			await expectKeyboardValue('2026-10-19', values[0]);
			expect(await pointerMoves.evaluate((record) => record.moves)).toEqual([]);
		} finally {
			await pointerMoves.evaluate((record) => record.stop());
			await pointerMoves.dispose();
		}

		const movedDate = dateTarget(page, metric, '2026-10-22');
		await movedDate.hover();
		await expect(tooltip).toHaveText('클릭하여 하루 상세 보기');
		await expect(movedDate).toHaveAttribute(
			'aria-describedby',
			(await tooltip.getAttribute('id'))!
		);
		await page.keyboard.press('End');
		await expectKeyboardValue('2026-10-25', values[4]);
		// pointerenter가 다시 발생하지 않아도 같은 날짜 안의 실제 이동은 안내를 복구한다.
		const bounds = await movedDate.boundingBox();
		if (!bounds) throw new Error('다시 가리킬 날짜 표시 없음');
		await page.mouse.move(bounds.x + bounds.width / 2 + 1, bounds.y + bounds.height / 2);
		await expect(tooltip).toHaveText('클릭하여 하루 상세 보기');
		await expect(movedDate).toHaveAttribute(
			'aria-describedby',
			(await tooltip.getAttribute('id'))!
		);
		const guideBounds = await tooltip.boundingBox();
		if (!guideBounds) throw new Error('날짜 이동 안내 표시 없음');
		expect(guideBounds.y - (bounds.y + bounds.height)).toBeCloseTo(8, 1);
		await expect(dateTarget(page, metric, '2026-10-25')).toBeFocused();
		const before = await position(page, metric);
		const loaded = dailyResponses(page, '2026-10-25');
		await page.keyboard.press('Enter');
		await loaded;
		await dayLoaded(page, '2026-10-25');
		await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
		await restored(page, metric, before);
	});
}

for (const [metric, date] of [
	['steps', '2026-10-20'],
	['exercises', '2026-10-21'],
	['sleeps', '2026-10-20'],
	['heart_rates', '2026-10-20']
] as const) {
	test(`${metric}: 날짜 라벨은 하루 상세·복귀로, 막대·점·회색·빈 영역은 기존 지표 상세로 이동한다`, async ({
		page
	}) => {
		const queries = trackQueries(page);
		await completedMain(page);
		const index = Number(date.slice(-2)) - 19;
		const target = dateTarget(page, metric, date);
		await expect(target).toHaveAttribute('href', `/health/day/${date}`);
		{
			const count = queries.length;
			const loaded = dailyResponses(page, date);
			const before = await clickPosition(page, metric, target);
			await loaded;
			await dayLoaded(page, date);
			expect(queries.slice(count)).toHaveLength(5);
			for (const query of queries.slice(count))
				expect(query.search).toBe(`?start=${date}&end=${date}`);
			if (metric === 'exercises')
				await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
					'기록 없음',
					'0분',
					'6시간 26분',
					'기록 없음'
				]);
			if (metric === 'steps') {
				const nextLoaded = dailyResponses(page, '2026-10-21');
				await page.getByRole('link', { name: '다음 날짜', exact: true }).click();
				await nextLoaded;
				await dayLoaded(page, '2026-10-21');
				const previousLoaded = dailyResponses(page, date);
				await page.getByRole('link', { name: '이전 날짜', exact: true }).click();
				await previousLoaded;
				await dayLoaded(page, date);
				await page.getByRole('button', { name: '날짜 선택, 2026.10.20', exact: true }).click();
				const selectedLoaded = dailyResponses(page, '2026-10-23');
				await page
					.getByRole('dialog', { name: '날짜 선택 캘린더' })
					.getByRole('link', { name: '2026-10-23', exact: true })
					.click();
				await selectedLoaded;
				await dayLoaded(page, '2026-10-23');
			}
			await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
			await restored(page, metric, before);
		}
		const selected = chart(page, metric);
		const visual = selected.locator(
			metric === 'steps' || metric === 'sleeps'
				? `[data-summary-bar="${index}"]`
				: `[data-summary-point="${index}"]`
		);
		await metricClick(page, metric, () => clickPixels(page, visual));
		await completedMain(page);
		// 완료 주의 운동은 실제 0분도 유효하다. 결측이 있는 현재 주에서 회색 표시를 따로 누른다.
		await page.getByRole('button', { name: '다음 주', exact: true }).click();
		await mainLoaded(page);
		const missing = selected.locator('[data-missing-point]').first();
		await missing.scrollIntoViewIfNeeded();
		const gray = await missing.evaluate((element) => {
			const label = element.getBoundingClientRect();
			const plot = element.closest('figure')!.querySelector('svg')!.getBoundingClientRect();
			return { x: label.left + label.width / 2, y: plot.top + (96 / 132) * plot.height };
		});
		await metricClick(page, metric, () => page.mouse.click(gray.x, gray.y), '2026-11-01');
		await completedMain(page);
		const plot = selected.locator('svg');
		await plot.scrollIntoViewIfNeeded();
		const empty = await plot.boundingBox();
		if (!empty) throw new Error('지표 차트 영역 없음');
		await metricClick(page, metric, () => page.mouse.click(empty.x + empty.width / 2, empty.y + 2));
		for (const area of ['axis-tick', 'axis-background', 'right-padding'] as const) {
			await completedMain(page);
			const axis = selected.locator(':scope > div[aria-hidden="true"]');
			if (area === 'axis-tick') {
				await metricClick(page, metric, () => clickPixels(page, axis.locator('span').nth(1)));
			} else {
				const areaTarget = area === 'axis-background' ? axis : selected;
				await areaTarget.scrollIntoViewIfNeeded();
				const box = await areaTarget.boundingBox();
				if (!box) throw new Error('지표 차트 프레임 영역 없음');
				await metricClick(page, metric, () =>
					page.mouse.click(
						area === 'axis-background' ? box.x + box.width / 2 : box.x + box.width - 4,
						box.y + box.height / 4
					)
				);
			}
		}
	});
}

test('360px 0값 차트 중앙은 지표 상세로, 0값 날짜 텍스트는 해당 하루로 이동한다', async ({
	page
}) => {
	await page.setViewportSize({ width: 360, height: 800 });
	await completedMain(page);
	const target = dateTarget(page, 'exercises', '2026-10-21');
	await expect(target).toHaveAccessibleName('2026-10-21 운동 시간 0 분 하루 상세 보기');
	await target.scrollIntoViewIfNeeded();
	const point = await target.evaluate((element) => {
		const label = element.getBoundingClientRect();
		const plot = element.closest('figure')!.querySelector('svg')!.getBoundingClientRect();
		return { x: label.left + label.width / 2, y: plot.top + plot.height / 2 };
	});
	await page.mouse.move(point.x, point.y);
	await page.mouse.down();
	let released = false;
	try {
		await expect(chart(page, 'exercises').getByRole('tooltip')).toHaveCount(0);
		expect(
			await page.evaluate(
				({ x, y }) =>
					document.elementFromPoint(x, y)?.closest('a[data-metric-target]')?.getAttribute('href'),
				point
			)
		).toBe('/health/activity?date=2026-10-25#exercise');
		await metricClick(page, 'exercises', () => page.mouse.up());
		released = true;
		await completedMain(page);
		const loaded = dailyResponses(page, '2026-10-21');
		await target.click();
		await loaded;
		await dayLoaded(page, '2026-10-21');
		await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
			'기록 없음',
			'0분',
			'6시간 26분',
			'기록 없음'
		]);
	} finally {
		if (!released) await page.mouse.up();
	}
});

for (const metric of ['steps', 'heart_rates'] as const) {
	test(`${metric}: 결측 날짜도 이동하며 null을 실제 0으로 그리지 않는다`, async ({ page }) => {
		await completedMain(page);
		const missing = dateTarget(page, metric, '2026-10-21');
		await expect(missing).toHaveAttribute('href', '/health/day/2026-10-21');
		await expect(missing).toHaveAccessibleName(/기록 없음/);
		await expect(missing).not.toHaveAttribute('data-chart-point');
		if (metric === 'steps')
			await expect(chart(page, metric).locator('[data-summary-bar="2"]')).toHaveCount(0);
		const loaded = dailyResponses(page, '2026-10-21');
		await missing.click();
		await loaded;
		await dayLoaded(page, '2026-10-21');
		await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
			'기록 없음',
			'0분',
			'6시간 26분',
			'기록 없음'
		]);
	});
}

test('응답에 실제로 존재하는 두 결측 날짜만 링크로 만들고 나머지 날짜·0막대를 생성하지 않는다', async ({
	page
}) => {
	await page.clock.setFixedTime(new Date('2026-10-25T03:00:00Z'));
	const data = activityMetricFixture('steps', '2026-10-19', '2026-10-25');
	data.days = data.days
		.slice(0, 2)
		.map((day) => ({ ...day, value: null, missing: true, reason: 'NO_RECORD' }));
	Object.assign(data.summary, {
		avg: null,
		total: null,
		dailyMin: null,
		dailyMax: null,
		daysWithData: 0,
		daysUsedForSummary: 0,
		missingDays: data.days.map((day) => day.date)
	});
	await page.route('**/api/v1/health/metrics/steps?*', (route) => {
		const url = new URL(route.request().url());
		if (
			url.searchParams.get('start') !== '2026-10-19' ||
			url.searchParams.get('end') !== '2026-10-25'
		)
			return route.fallback();
		return route.fulfill({ json: { data } });
	});
	await page.goto('/health');
	await mainLoaded(page);
	const selected = chart(page, 'steps');
	await expect(selected.locator('a[data-date-target]')).toHaveCount(2);
	await expect(selected.locator('[data-summary-date]')).toHaveCount(2);
	await expect(
		selected.locator('[data-chart-point], rect, polyline, [stroke-dasharray]')
	).toHaveCount(0);
	await expect(dateTarget(page, 'steps', '2026-10-21')).toHaveCount(0);
	const loaded = dailyResponses(page, '2026-10-20');
	await dateTarget(page, 'steps', '2026-10-20').click();
	await loaded;
	await dayLoaded(page, '2026-10-20');
});

test('기간을 바꾼 연말·연초 날짜 라벨은 해당 연도의 하루 상세와 API를 사용한다', async ({
	page
}) => {
	await page.clock.setFixedTime(new Date('2027-01-02T03:00:00Z'));
	await page.goto('/health');
	await mainLoaded(page);
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await mainLoaded(page);
	for (const [date, period] of [
		['2026-12-25', '2026.12.20 ~ 12.26'],
		['2027-01-01', '2026.12.27 ~ 2027.01.02']
	]) {
		await expect(
			page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')
		).toHaveText(period);
		const loaded = dailyResponses(page, date);
		await clickPixels(
			page,
			chart(page, 'steps').locator(`[data-summary-date="${date.slice(5).replace('-', '.')}"]`)
		);
		await loaded;
		await dayLoaded(page, date);
		await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
		await mainLoaded(page);
		await expect(
			page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')
		).toHaveText(period);
		if (date === '2026-12-25') {
			await page.getByRole('button', { name: '다음 주', exact: true }).click();
			await mainLoaded(page);
		}
	}
});

test('브라우저 뒤로가기도 원래 메인의 기간·스크롤·차트 위치를 복구한다', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await completedMain(page);
	await page.locator('[data-health-chart-scroll]').evaluateAll((elements) => {
		for (const [index, element] of elements.entries())
			element.scrollLeft = Math.min(element.scrollWidth - element.clientWidth, (index + 1) * 24);
	});
	const target = dateTarget(page, 'heart_rates', '2026-10-20');
	await target.scrollIntoViewIfNeeded();
	const loaded = dailyResponses(page, '2026-10-20');
	const before = await clickPosition(page, 'heart_rates', target);
	await loaded;
	await dayLoaded(page, '2026-10-20');
	await page.goBack();
	await restored(page, 'heart_rates', before);
});

test('실제 내부 넘침이 있는 280px 화면에서도 0이 아닌 차트 가로 스크롤을 복구한다', async ({
	page
}) => {
	await page.setViewportSize({ width: 280, height: 800 });
	await completedMain(page);
	const pane = card(page, 'steps').locator('[data-health-chart-scroll]');
	const overflow = await pane.evaluate((element) => element.scrollWidth - element.clientWidth);
	expect(overflow).toBeGreaterThan(0);
	await pane.evaluate((element) => {
		element.scrollLeft = element.scrollWidth - element.clientWidth;
	});
	const target = dateTarget(page, 'steps', '2026-10-25');
	await target.scrollIntoViewIfNeeded();
	expect(await pane.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
	const loaded = dailyResponses(page, '2026-10-25');
	const before = await clickPosition(page, 'steps', target);
	expect(before.chartScrollLeft[0]).toBeGreaterThan(0);
	await loaded;
	await dayLoaded(page, '2026-10-25');
	await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
	await restored(page, 'steps', before);
	expect(await pane.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
});

for (const entry of ['direct-reload', 'new-tab'] as const) {
	test(`${entry}: 메인 복귀 문맥 없이 진입한 상세는 오늘 기준 건강 홈으로 안전하게 돌아온다`, async ({
		page,
		context
	}) => {
		const targetPage = entry === 'new-tab' ? await context.newPage() : page;
		if (targetPage !== page) await installHealthApi(targetPage);
		try {
			await targetPage.goto('/health/day/2026-10-20');
			await dayLoaded(targetPage, '2026-10-20');
			if (entry === 'direct-reload') await targetPage.reload();
			await expect(
				targetPage.getByRole('link', { name: '건강 데이터로', exact: true })
			).toHaveAttribute('href', '/health');
			await targetPage.getByRole('link', { name: '건강 데이터로', exact: true }).click();
			await mainLoaded(targetPage);
			await expect(
				targetPage.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')
			).toHaveText('2026.10.26 ~ 11.01');
		} finally {
			if (targetPage !== page) await targetPage.close();
		}
	});
}

test.describe('날짜 열 반응형 입력', () => {
	test.use({ hasTouch: true });
	for (const viewport of [
		{ width: 360, height: 800 },
		{ width: 390, height: 844 },
		{ width: 768, height: 1024 },
		{ width: 1366, height: 768 },
		{ width: 1920, height: 1080 }
	]) {
		test(`${viewport.width}×${viewport.height}: 날짜 열은 겹치지 않고 터치·키보드로 정확한 날짜를 연다`, async ({
			page
		}) => {
			await page.setViewportSize(viewport);
			await completedMain(page);
			for (const metric of metrics) {
				const selected = chart(page, metric);
				const targets = selected.locator('a[data-date-target]');
				await expect(targets).toHaveCount(7);
				await expect(selected.locator('a[data-date-target][tabindex="0"]')).toHaveCount(1);
				const geometry = await selected.evaluate((element) => {
					const plot = element.querySelector('svg')!.getBoundingClientRect();
					const figure = element.getBoundingClientRect();
					return {
						plot: { top: plot.top, bottom: plot.bottom },
						metric: (() => {
							const box = element.querySelector('[data-metric-target]')!.getBoundingClientRect();
							return { top: box.top, bottom: box.bottom };
						})(),
						figure: { left: figure.left, right: figure.right },
						targets: [...element.querySelectorAll('[data-date-target]')].map((target) => {
							const box = target.getBoundingClientRect();
							return { left: box.left, right: box.right, top: box.top, bottom: box.bottom };
						}),
						labels: [...element.querySelectorAll('[data-summary-date]')].map((label) => {
							const box = label.getBoundingClientRect();
							return { left: box.left, right: box.right };
						})
					};
				});
				for (const [index, box] of geometry.targets.entries()) {
					expect(box.right).toBeGreaterThan(box.left);
					expect(box.top - geometry.plot.top).toBeCloseTo(104, 1);
					expect(box.bottom - box.top).toBeGreaterThanOrEqual(24);
					expect(box.bottom).toBeLessThanOrEqual(geometry.plot.bottom + 1);
					expect(box.left).toBeGreaterThanOrEqual(geometry.figure.left - 1);
					expect(box.right).toBeLessThanOrEqual(geometry.figure.right + 1);
					expect(geometry.labels[index].left).toBeGreaterThanOrEqual(box.left - 0.5);
					expect(geometry.labels[index].right).toBeLessThanOrEqual(box.right + 0.5);
					if (index) expect(geometry.targets[index - 1].right).toBeLessThanOrEqual(box.left + 0.1);
				}
				expect(Math.abs(geometry.metric.top - geometry.plot.top)).toBeLessThanOrEqual(1);
				expect(Math.abs(geometry.metric.bottom - geometry.plot.bottom)).toBeLessThanOrEqual(1);
			}
			expect(
				await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
			).toBeLessThanOrEqual(1);
			const loaded = dailyResponses(page, '2026-10-20');
			const normal = dateTarget(page, 'steps', '2026-10-20');
			if (viewport.width < 640) await normal.tap();
			else await normal.click();
			await loaded;
			await dayLoaded(page, '2026-10-20');
			await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
			await mainLoaded(page);
			await dateTarget(page, 'steps', '2026-10-19').focus();
			await page.keyboard.press('ArrowRight');
			await expect(dateTarget(page, 'steps', '2026-10-20')).toBeFocused();
			await expect(chart(page, 'steps').getByRole('tooltip')).toBeVisible();
			await page.keyboard.press('ArrowRight');
			const missing = dateTarget(page, 'steps', '2026-10-21');
			await expect(missing).toBeFocused();
			await expect(chart(page, 'steps').getByRole('tooltip')).toHaveCount(0);
			const missingLoaded = dailyResponses(page, '2026-10-21');
			await page.keyboard.press('Enter');
			await missingLoaded;
			await dayLoaded(page, '2026-10-21');
		});
	}
});

for (const viewport of [
	{ width: 360, height: 800 },
	{ width: 1366, height: 768 }
]) {
	test(`${viewport.width}px 첫날·마지막날 라벨의 양 끝도 표시한 날짜로 이동한다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await completedMain(page);
		for (const date of ['2026-10-19', '2026-10-25']) {
			for (const edge of ['left', 'right'] as const) {
				const label = chart(page, 'steps').locator(
					`[data-summary-date="${date.slice(5).replace('-', '.')}"]`
				);
				await label.scrollIntoViewIfNeeded();
				const box = await label.boundingBox();
				if (!box) throw new Error('날짜 라벨 표시 없음');
				const loaded = dailyResponses(page, date);
				await page.mouse.click(
					edge === 'left' ? box.x + 0.5 : box.x + box.width - 0.5,
					box.y + box.height / 2
				);
				await loaded;
				await dayLoaded(page, date);
				await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
				await mainLoaded(page);
				await expect(
					page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')
				).toHaveText(completedPeriod);
			}
		}
	});
}

test('로딩·조회 오류에는 날짜 링크를 만들지 않고 정상 재시도 후 정확한 날짜를 연다', async ({
	page
}) => {
	await page.clock.setFixedTime(new Date('2026-10-25T03:00:00Z'));
	let release = () => {};
	const pending = new Promise<void>((resolve) => (release = resolve));
	let stage: 'pending' | 'error' | 'ready' = 'pending';
	await page.route('**/api/v1/health/metrics/steps?*', async (route) => {
		const url = new URL(route.request().url());
		if (url.searchParams.get('start') === url.searchParams.get('end')) return route.fallback();
		if (stage === 'pending') await pending;
		if (stage !== 'ready')
			return route.fulfill({
				status: 500,
				json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: 'synthetic' } }
			});
		return route.fulfill({
			json: {
				data: activityMetricFixture(
					'steps',
					url.searchParams.get('start')!,
					url.searchParams.get('end')!
				)
			}
		});
	});
	try {
		await page.goto('/health');
		await expect(page.locator('.health-summary-card[aria-busy="false"]')).toHaveCount(3);
		await expect(card(page, 'steps')).toHaveAttribute('aria-busy', 'true');
		await expect(card(page, 'steps').locator('[data-date-target]')).toHaveCount(0);
		stage = 'error';
		release();
		await expect(card(page, 'steps').getByRole('alert')).toBeVisible();
		await expect(card(page, 'steps').locator('[data-date-target]')).toHaveCount(0);
		stage = 'ready';
		await page.getByRole('button', { name: '걸음 수 다시 시도', exact: true }).click();
		await mainLoaded(page);
		await expect(chart(page, 'steps').locator('[data-date-target]')).toHaveCount(7);
		const loaded = dailyResponses(page, '2026-10-20');
		await dateTarget(page, 'steps', '2026-10-20').click();
		await loaded;
		await dayLoaded(page, '2026-10-20');
	} finally {
		release();
	}
});
