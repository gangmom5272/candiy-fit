import { mockDayDetailApi } from '../../../../lib/testing/day-detail-api-fixture';
import {
	mockActivityApi,
	openCompletedActivityWeek
} from '../../../../lib/testing/activity-api-fixture';
import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';

async function captureBelowHeader(locator: import('@playwright/test').Locator) {
	await locator.evaluate((node) => {
		const header = document.querySelector('header')?.getBoundingClientRect().height ?? 0;
		window.scrollTo(0, window.scrollY + node.getBoundingClientRect().top - header - 12);
	});
	return locator.screenshot();
}

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockDayDetailApi(page);
});

test('첫 진입은 KST 기준일을 포함한 최근 7일을 조회한다', async ({ page }) => {
	await page.clock.setFixedTime(new Date('2026-10-27T16:00:00Z'));
	const requests: URL[] = [];
	page.on('request', (request) => {
		const url = new URL(request.url());
		if (url.pathname.startsWith('/api/v1/health/')) requests.push(url);
	});
	await page.goto('/health/activity');
	await expect(page.getByRole('heading', { name: '일별 걸음 수', exact: true })).toBeVisible();
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		'2026.10.22 ~ 10.28'
	);
	await expect(page.getByRole('button', { name: '다음 주', exact: true })).toBeDisabled();
	const chart = page.getByRole('figure', { name: '일별 걸음 수 차트' });
	await expect(chart.locator('a:not(svg a)')).toHaveCount(7);
	await expect(chart.locator('a').last()).toHaveAttribute('href', '/health/day/2026-10-28');
	for (const path of ['/metrics/steps', '/metrics/exercises', '/timeline']) {
		expect(
			requests.some(
				(url) =>
					url.pathname.endsWith(path) &&
					url.searchParams.get('start') === '2026-10-22' &&
					url.searchParams.get('end') === '2026-10-28'
			)
		).toBe(true);
	}
});

test('활동 화면에 기준 기간과 인정 세션으로 계산한 요약을 표시한다', async ({ page }) => {
	await openCompletedActivityWeek(page);
	await expect(page.getByRole('heading', { name: '건강 데이터', level: 1 })).toBeVisible();
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		'2026.10.19 ~ 10.25'
	);
	await expect(
		page
			.getByRole('navigation', { name: '건강 데이터', exact: true })
			.getByRole('link', { name: '활동', exact: true })
	).toHaveAttribute('aria-current', 'page');
	await expect(
		page
			.getByRole('navigation', { name: '주 메뉴' })
			.getByRole('link', { name: '건강 데이터', exact: true })
	).toHaveAttribute('aria-current', 'true');
	for (const title of [
		'일별 걸음 수',
		'요일별 평균 걸음 수',
		'시간대별 평균 걸음 수',
		'운동',
		'운동 시간',
		'운동 종류',
		'소모 칼로리',
		'운동 중 심박수'
	])
		await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
	for (const text of [
		'평균 26분',
		'총 2시간 59분',
		'총 4종',
		'평균 326 kcal',
		'총 1,630 kcal',
		'최고 420 kcal',
		'평균 128 bpm',
		'최고 154 bpm'
	])
		await expect(page.getByText(text, { exact: true })).toBeVisible();
	await expect(page.locator('.daily-steps-chart rect')).toHaveCount(6);
	await expect(page.locator('.activity-main').getByRole('figure').locator('img')).toHaveCount(0);
	await expect(page.locator('.activity-main figure')).toHaveCount(6);
	await expect(page.getByRole('link', { name: /^기록 없음.*10\/21.*수/ })).toHaveAttribute(
		'href',
		'/health/day/2026-10-21'
	);
	await expect(page.getByRole('link', { name: /^7,760보.*10\/25.*일/ })).toHaveAttribute(
		'href',
		'/health/day/2026-10-25'
	);
	await expect(
		page
			.getByRole('region', { name: '요일별 평균 걸음 수', exact: true })
			.getByRole('button', { name: /^수요일/ })
	).toHaveCount(0);
});

test('과거 주를 탐색하고 현재 주에도 미래 날짜를 생성하지 않는다', async ({ page }) => {
	await openCompletedActivityWeek(page);
	const previous = page.getByRole('button', { name: '이전 주', exact: true });
	const next = page.getByRole('button', { name: '다음 주', exact: true });
	await expect(previous).toBeEnabled();
	await previous.click();
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		'2026.10.12 ~ 10.18'
	);
	await next.click();
	await next.click();
	await expect(next).toBeDisabled();
	await expect(page.locator('.daily-steps-chart a:not(svg a)')).toHaveCount(7);
	await expect(page.locator('.daily-steps-chart a:not(svg a)').first()).toHaveAttribute(
		'href',
		'/health/day/2026-10-26'
	);
	await expect(page.locator('.daily-steps-chart a:not(svg a)').last()).toHaveAttribute(
		'href',
		'/health/day/2026-11-01'
	);
	await previous.click();
	await expect(page.getByText('평균 26분', { exact: true })).toBeVisible();
});

for (const width of [1920, 1366, 768, 360]) {
	test(`${width}px에서 요일 7개와 시간대 24개·요약·hover 정보를 잘림 없이 표시한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 1080 });
		await openCompletedActivityWeek(page);
		const weekday = page.getByRole('figure', { name: '요일별 평균 걸음 수 차트', exact: true });
		await expect(weekday.locator('p')).toHaveCount(7);
		await expect(weekday.locator('p')).toHaveText([
			/^월/,
			/^화/,
			/^수/,
			/^목/,
			/^금/,
			/^토/,
			/^일/
		]);
		await expect(
			page.getByText('최근 4주 · 같은 요일 기록 평균 (기록이 있는 날 기준)', { exact: true })
		).toHaveClass('sr-only');

		await expect(page.locator('.step-summary-card [aria-live]')).toHaveCount(0);
		const weekdayCard = page.getByRole('region', { name: '요일별 평균 걸음 수', exact: true });
		const hourlyCard = page.getByRole('region', { name: '시간대별 평균 걸음 수', exact: true });
		const heights = await page
			.locator('.step-summary-card')
			.evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().height));
		const weekdayWidth = await weekday.evaluate((figure) => ({
			figure: figure.getBoundingClientRect().width,
			plot: figure.firstElementChild?.getBoundingClientRect().width,
			labels: figure.children[1]?.getBoundingClientRect().width,
			bars: [...figure.querySelectorAll('[data-average-bar]')].map(
				(bar) => bar.getBoundingClientRect().width
			)
		}));
		expect(weekdayWidth.plot).toBe(weekdayWidth.figure);
		expect(weekdayWidth.labels).toBe(weekdayWidth.figure);
		if (width >= 1024) expect(weekdayWidth.bars.every((bar) => Math.abs(bar - 34) < 1)).toBe(true);
		async function checkTooltip(
			chart: import('@playwright/test').Locator,
			bar: import('@playwright/test').Locator
		) {
			const label = await bar.getAttribute('aria-label');
			if (!label) throw new Error('막대 설명이 없습니다.');
			const [name, value] = label.split(' · 평균 ');
			expect(value).toMatch(/^\d[\d,]*보$/);
			const tooltip = chart.getByRole('tooltip');
			await expect(tooltip.getByText(name + ' · 평균', { exact: true })).toBeVisible();
			await expect(tooltip.locator('strong')).toHaveText(value);
			const bounds = await tooltip.boundingBox();
			const column = await bar.boundingBox();
			const card = await chart.evaluate((node) => {
				const r = node.closest('section')?.getBoundingClientRect();
				if (!r) throw new Error('카드 없음');
				return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
			});
			if (!bounds) throw new Error('툴팁 없음');
			if (!column) throw new Error('막대 영역 없음');
			expect(bounds.height).toBeLessThanOrEqual(80);
			expect(bounds.x).toBeGreaterThanOrEqual(card.left);
			expect(bounds.x + bounds.width).toBeLessThanOrEqual(card.right);
			expect(bounds.y).toBeGreaterThanOrEqual(Math.max(8, card.top + 8));
			expect(bounds.y + bounds.height).toBeLessThanOrEqual(card.bottom - 8);
			const anchorTop = column.y;
			if (anchorTop - bounds.height - 10 >= Math.max(8, card.top + 8)) {
				expect(anchorTop - (bounds.y + bounds.height)).toBeGreaterThanOrEqual(8);
				expect(anchorTop - (bounds.y + bounds.height)).toBeLessThanOrEqual(12);
			}
		}
		async function checkHoverPath(
			chart: import('@playwright/test').Locator,
			bar: import('@playwright/test').Locator
		) {
			await bar.hover();
			await checkTooltip(chart, bar);
			const tooltip = chart.getByRole('tooltip');
			const bounds = await tooltip.boundingBox();
			if (!bounds) throw new Error('툴팁 이동 경계 없음');
			await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2, {
				steps: 8
			});
			await expect(tooltip).toBeVisible();
			await checkTooltip(chart, bar);
			await page.mouse.move(bounds.x + bounds.width / 2 + 1, bounds.y + bounds.height / 2);
			await expect(tooltip).toBeVisible();
			await checkTooltip(chart, bar);
			await page.mouse.move(0, 0);
			await expect(tooltip).toHaveCount(0);
		}
		const monday = weekday.getByRole('button', { name: /^월요일 · 평균 / });
		await expect(weekdayCard.getByRole('definition')).toHaveText([
			'토요일 · 5,200보',
			'목요일 · 9,240보'
		]);
		await expect(weekdayCard.locator('dt')).toHaveText(['최소', '최대']);
		await monday.hover();
		const tuesday = weekday.getByRole('button', { name: /^화요일 · 평균 / });
		await tuesday.hover();
		await checkTooltip(weekday, tuesday);
		await checkHoverPath(weekday, monday);
		await checkHoverPath(weekday, weekday.getByRole('button', { name: /^일요일 · 평균 / }));
		await monday.hover();
		await checkTooltip(weekday, monday);
		const tip = weekday.getByRole('tooltip');
		const tipBounds = await tip.boundingBox();
		const pointBounds = await monday.boundingBox();
		if (!tipBounds || !pointBounds) throw new Error('호버 경계 없음');
		await page.mouse.move(
			pointBounds.x + pointBounds.width / 2,
			tipBounds.y + tipBounds.height / 2,
			{ steps: 8 }
		);
		await expect(tip).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(tip).toHaveCount(0);
		await page.mouse.move(
			pointBounds.x + pointBounds.width / 2 + 1,
			pointBounds.y + pointBounds.height / 2
		);
		await expect(tip).toHaveCount(0);
		const friday = weekday.getByRole('button', { name: /^금요일 · 평균 / });
		await friday.click();
		await checkTooltip(weekday, friday);
		await friday.focus();
		await page.keyboard.press('Home');
		await expect(monday).toBeFocused();
		await checkTooltip(weekday, monday);
		await page.mouse.move(0, 0);
		await checkTooltip(weekday, monday);
		await page.keyboard.press('Escape');
		await expect(tip).toHaveCount(0);
		const chart = page.getByRole('figure', { name: '시간대별 평균 걸음 수 분포', exact: true });
		await expect(chart.getByRole('button')).toHaveCount(24);
		await chart.getByRole('button', { name: /^00시 · 평균 / }).hover();
		const firstHour = chart.getByRole('button', { name: /^01시 · 평균 / });
		await firstHour.hover();
		await checkTooltip(chart, firstHour);
		await checkHoverPath(chart, chart.getByRole('button', { name: /^00시 · 평균 / }));
		await checkHoverPath(chart, chart.getByRole('button', { name: /^23시 · 평균 / }));
		const bar = chart.getByRole('button', { name: /^18시 · 평균 / });
		await bar.hover();
		await checkTooltip(chart, bar);
		const anotherBar = chart.getByRole('button', { name: /^01시 · 평균 / });
		await anotherBar.click();
		await checkTooltip(chart, anotherBar);
		const keyboardBar = chart.getByRole('button', { name: /^02시 · 평균 / });
		await keyboardBar.focus();
		await checkTooltip(chart, keyboardBar);
		await page.keyboard.press('Enter');
		await checkTooltip(chart, keyboardBar);
		await page.keyboard.press('ArrowRight');
		await expect(chart.getByRole('button', { name: /^03시 · 평균 / })).toBeFocused();
		await page.keyboard.press('End');
		await checkTooltip(chart, chart.getByRole('button', { name: /^23시 · 평균 / }));
		await page.keyboard.press('Home');
		await checkTooltip(chart, chart.getByRole('button', { name: /^00시 · 평균 / }));
		const summary = page.locator('dl[aria-label="시간대별 걸음 수 요약"]');
		await expect(summary.locator('dt')).toHaveText(['최소 ', '최대 ']);
		for (const card of [weekdayCard, hourlyCard])
			expect(await card.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
		expect(
			await page
				.locator('.step-summary-card')
				.evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().height))
		).toEqual(heights);
		if (width >= 1024) {
			expect(heights[0]).toBe(heights[1]);
			for (const card of [weekdayCard, hourlyCard]) {
				await expect(card).toHaveCSS('padding-top', '24px');
				await expect(card).toHaveCSS('padding-bottom', '24px');
				await expect(card).toHaveCSS('padding-left', '28px');
				await expect(card).toHaveCSS('padding-right', '28px');
				expect(
					await card.evaluate((node) => {
						const body = node.getBoundingClientRect();
						const chart = node.querySelector('figure')!.getBoundingClientRect();
						return chart.bottom <= body.bottom - parseFloat(getComputedStyle(node).paddingBottom);
					})
				).toBe(true);
			}
			const chips = await summary.locator(':scope > div').evaluateAll((nodes) =>
				nodes.map((node) => {
					const { top, left, right } = node.getBoundingClientRect();
					return { top, left, right };
				})
			);
			expect(chips).toHaveLength(2);
			for (let i = 1; i < chips.length; i++) {
				expect(Math.abs(chips[i].top - chips[0].top)).toBeLessThanOrEqual(2);
				expect(chips[i].left).toBeGreaterThanOrEqual(chips[i - 1].right);
			}
			const baselines = await page
				.locator('.step-summary-card figure > div:first-child')
				.evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().bottom));
			expect(Math.abs(baselines[0] - baselines[1])).toBeLessThanOrEqual(2);
		}
		await test.info().attach('activity-tooltip-' + width, {
			body: await page.screenshot({ fullPage: true }),
			contentType: 'image/png'
		});
		await test.info().attach('activity-summary-' + width, {
			body: await captureBelowHeader(page.locator('.step-summary-card').first().locator('..')),
			contentType: 'image/png'
		});
		for (const [label, unit] of [
			['운동 시간', '시간'],
			['소모 칼로리', 'kcal'],
			['운동 중 심박수', 'bpm']
		]) {
			const card = page.getByRole('region', { name: label, exact: true });
			await expect(card.locator('[stroke-dasharray]')).toHaveCount(0);
			const geometry = await card.evaluate((node) => {
				const card = node.getBoundingClientRect();
				const points = [...node.querySelectorAll('.activity-trend button')].map((point) =>
					point.getBoundingClientRect()
				);
				return [...node.querySelectorAll('.activity-trend a')].map((date) => {
					const r = date.getBoundingClientRect();
					return {
						width: r.width,
						height: r.height,
						inside: r.bottom <= card.bottom && r.left >= card.left && r.right <= card.right,
						overlap: points.some(
							(point) =>
								r.left < point.right &&
								r.right > point.left &&
								r.top < point.bottom &&
								r.bottom > point.top
						)
					};
				});
			});
			expect(geometry).toHaveLength(7);
			for (const date of geometry) {
				expect(date.width).toBeGreaterThanOrEqual(24);
				expect(date.height).toBeGreaterThanOrEqual(24);
				expect(date.inside).toBe(true);
				expect(date.overlap).toBe(false);
			}
			const point = card.getByRole('button', { name: new RegExp(`^2026-10-19 ${label} `) });
			await point.hover();
			const tooltip = card.getByRole('tooltip');
			await expect(tooltip).toContainText('2026.10.19');
			await expect(tooltip).toContainText(unit);
			await test.info().attach(`exercise-${label}-${width}`, {
				body: await captureBelowHeader(card),
				contentType: 'image/png'
			});
			await point.hover();
			const bounds = await tooltip.boundingBox();
			const cardBounds = await card.boundingBox();
			if (!bounds || !cardBounds) throw new Error('운동 툴팁 위치 없음');
			expect(bounds.x).toBeGreaterThanOrEqual(cardBounds.x);
			expect(bounds.x + bounds.width).toBeLessThanOrEqual(cardBounds.x + cardBounds.width);
			await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2, {
				steps: 8
			});
			await expect(tooltip).toBeVisible();
			await point.focus();
			await page.mouse.move(0, 0);
			await expect(tooltip).toBeVisible();
			await page.keyboard.press('Escape');
			await expect(tooltip).toHaveCount(0);
		}
	});
}

test('터치로 요일·시간대 막대의 정확한 평균을 확인한다', async ({ browser, baseURL }) => {
	const context = await browser.newContext({
		baseURL,
		viewport: { width: 360, height: 800 },
		isMobile: true,
		hasTouch: true
	});
	try {
		const page = await context.newPage();
		await mockSignedIn(page);
		await mockActivityApi(page);
		await mockDayDetailApi(page);
		await openCompletedActivityWeek(page);
		const weekday = page.getByRole('region', { name: '요일별 평균 걸음 수', exact: true });
		const weekdayBar = weekday.getByRole('button', { name: /^월요일 · 평균 / });
		await weekdayBar.tap();
		await expect(weekday.getByRole('tooltip')).toContainText('월요일 · 평균');
		await expect(weekday.getByRole('tooltip').locator('strong')).toHaveText(
			(await weekdayBar.getAttribute('aria-label'))?.split(' · 평균 ')[1] ?? ''
		);
		const chart = page.getByRole('figure', { name: '시간대별 평균 걸음 수 분포', exact: true });
		const bar = chart.getByRole('button', { name: /^18시 · 평균 / });
		await bar.tap();
		await expect(chart.getByRole('tooltip')).toContainText('18시 · 평균');
		await expect(chart.getByRole('tooltip').locator('strong')).toHaveText(
			(await bar.getAttribute('aria-label'))?.split(' · 평균 ')[1] ?? ''
		);
		await bar.tap();
		await expect(chart.getByRole('tooltip')).toHaveCount(0);
		const exercise = page.getByRole('region', { name: '운동 시간', exact: true });
		const point = exercise.getByRole('button', { name: /^2026-10-19 운동 시간 / });
		await point.tap();
		await expect(exercise.getByRole('tooltip')).toContainText('2026.10.19');
		await expect(exercise.getByRole('tooltip')).toContainText('시간');
		await point.tap();
		await expect(exercise.getByRole('tooltip')).toHaveCount(0);
		await exercise.getByRole('link', { name: '2026-10-21 하루 상세 보기', exact: true }).tap();
		await expect(page).toHaveURL('/health/day/2026-10-21');
	} finally {
		await context.close();
	}
});

for (const width of [1920, 1366, 768, 360]) {
	test(`${width}px에서 시간대 기록이 없으면 빈 상태만 표시하고 요일 패턴은 유지한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 1080 });
		await openCompletedActivityWeek(page);
		await page.getByRole('button', { name: '다음 주', exact: true }).click();
		const hourly = page.getByRole('region', { name: '시간대별 평균 걸음 수', exact: true });
		const empty = hourly.getByText('선택한 기간의 기록이 없어요.', { exact: true });
		await expect(empty).toBeVisible();
		await expect(hourly.getByRole('button')).toHaveCount(0);
		await expect(hourly.locator('figure, dl, [aria-live]')).toHaveCount(0);
		await expect(
			page.getByRole('region', { name: '요일별 평균 걸음 수', exact: true }).getByRole('button')
		).toHaveCount(6);
		expect(
			await empty.evaluate((node) => {
				const card = node.closest('.step-summary-card')?.getBoundingClientRect();
				const text = node.getBoundingClientRect();
				return (
					!!card &&
					text.top >= card.top &&
					text.bottom <= card.bottom &&
					node.scrollWidth <= node.clientWidth
				);
			})
		).toBe(true);
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
			)
		).toBe(true);
	});
}

test('날짜의 클릭·Enter로 하루 상세에 이동하고 원래 활동 기간으로 돌아온다', async ({ page }) => {
	await openCompletedActivityWeek(page);
	const firstDay = page.getByRole('link', { name: /^8,120보.*10\/19/ });
	await firstDay.hover();
	const daily = page.getByRole('figure', { name: '일별 걸음 수 차트' });
	await expect(daily.getByRole('tooltip')).toHaveText('클릭하여 하루 상세 보기');
	await firstDay.click();
	await expect(page).toHaveURL('/health/day/2026-10-19');
	await expect(
		page.getByRole('heading', { name: '10월 19일 하루 상세', exact: true })
	).toBeVisible();
	const back = page.getByRole('link', { name: '건강 데이터로', exact: true });
	await expect(back).toHaveAttribute('href', '/health/activity');
	await back.click();
	await expect(page).toHaveURL('/health/activity');
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		'2026.10.19 ~ 10.25'
	);
	await page.getByRole('link', { name: /^기록 없음.*10\/21/ }).focus();
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL('/health/day/2026-10-21');
});

test('활동 CTA로 기존 코칭 화면에 이동한다', async ({ page }) => {
	await openCompletedActivityWeek(page);
	const cta = page.getByRole('link', { name: '내 활동에 대해 물어보기', exact: true });
	await expect(cta).toHaveAttribute('href', '/coaching');
	await cta.focus();
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL('/coaching');
});

for (const width of [360, 1920]) {
	test(`${width}px에서 빈 그래프 영역은 이동하지 않고 막대와 날짜 텍스트만 이동한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 1080 });
		await openCompletedActivityWeek(page);
		const chart = page.getByRole('figure', { name: '일별 걸음 수 차트' });
		const plot = await chart.locator('svg').boundingBox();
		const missingDate = chart.getByRole('link', { name: /^기록 없음.*10\/21/ });
		const date = await missingDate.boundingBox();
		if (!plot || !date) throw new Error('차트 위치 없음');
		await page.mouse.click(date.x + date.width / 2, plot.y + plot.height / 2);
		await expect(page).toHaveURL('/health/activity');
		expect(date.height).toBe(width < 640 ? 32 : 16);
		await page.mouse.click(plot.x + plot.width / 14, plot.y + 2);
		await expect(page).toHaveURL('/health/activity');
		await chart.locator('svg a rect').first().hover();
		await expect(chart.getByRole('tooltip')).toHaveText('클릭하여 하루 상세 보기');
		await test.info().attach(`daily-hint-${width}`, {
			body: await captureBelowHeader(page.locator('.daily-steps-card')),
			contentType: 'image/png'
		});
		await chart.locator('svg a rect').first().click();
		await expect(page).toHaveURL('/health/day/2026-10-19');
		await page.goBack();
		await expect(page).toHaveURL('/health/activity');
		await expect(
			page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')
		).toHaveText('2026.10.19 ~ 10.25');
		await missingDate.click();
		await expect(page).toHaveURL('/health/day/2026-10-21');
		await page.goBack();
		await expect(page).toHaveURL('/health/activity');
		await expect(
			page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')
		).toHaveText('2026.10.19 ~ 10.25');
		await page.getByRole('button', { name: '이전 주', exact: true }).click();
		await expect(
			page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')
		).toHaveText('2026.10.12 ~ 10.18');
		await chart.click({ position: { x: 100, y: 60 } });
		await expect(page).toHaveURL('/health/activity');
		await expect(chart.locator('svg a')).toHaveCount(0);
	});
}

test('미포함 걷기는 운동 종류와 분리된 목록을 클릭·Enter·Space로 여닫는다', async ({ page }) => {
	await openCompletedActivityWeek(page);
	const recorded = page.getByRole('list', { name: '기록된 운동 종류', exact: true });
	await expect(recorded.getByRole('listitem')).toHaveCount(4);
	await expect(recorded).not.toContainText('3.2km/h');
	const recordedBefore = await recorded.textContent();
	const button = page.getByRole('button', {
		name: '운동 시간에 포함되지 않은 걷기 4건',
		exact: true
	});
	await expect(button).toHaveCount(1);
	await expect(button).not.toHaveAttribute('aria-label');
	await expect(button).toHaveAttribute('aria-expanded', 'false');
	const id = await button.getAttribute('aria-controls');
	if (!id) throw new Error('설명 연결 없음');
	const description = page.locator(`[id="${id}"]`);
	await expect(description).toBeHidden();
	await button.click();
	await expect(button).toHaveAttribute('aria-expanded', 'true');
	await expect(description).toBeVisible();
	const excluded = page.getByRole('list', { name: '운동 시간에 포함되지 않은 걷기', exact: true });
	await expect(excluded.getByRole('listitem')).toHaveCount(4);
	await expect(excluded.locator('time')).toHaveText(['10/19', '10/21', '10/23', '10/25']);
	await expect(excluded.getByText('걷기 3.2km/h', { exact: true })).toBeVisible();
	await expect(excluded.getByText('30분', { exact: true })).toBeVisible();
	await expect(
		description.getByText(
			'평균 시속 4km 미만의 걷기는 운동 시간·횟수·활동 점수에 포함되지 않아요.',
			{
				exact: true
			}
		)
	).toHaveCount(1);
	await expect(recorded).toHaveText(recordedBefore ?? '');
	await expect(page.getByText('총 4종', { exact: true })).toBeVisible();
	await button.click();
	await expect(description).toBeHidden();
	await button.focus();
	await page.keyboard.press('Enter');
	await expect(button).toHaveAttribute('aria-expanded', 'true');
	await page.keyboard.press('Space');
	await expect(button).toHaveAttribute('aria-expanded', 'false');
	await expect(button).toBeFocused();
	await button.click();
	await page.getByRole('button', { name: '다음 주', exact: true }).click();
	await expect(button).toHaveCount(0);
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(button).toHaveAttribute('aria-expanded', 'false');
});

for (const width of [360, 768, 1366, 1920]) {
	test(`${width}px에서 미포함 걷기 목록과 설명은 잘림 없이 펼쳐진다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 1080 });
		await openCompletedActivityWeek(page);
		await expect(
			page.getByRole('button', { name: '운동 시간에 포함되지 않은 걷기 4건', exact: true })
		).toBeVisible();
		await page.evaluate(() => document.fonts.ready);
		const steps = await page.locator('.daily-steps-card').evaluate((element) => ({
			y: element.getBoundingClientRect().top + window.scrollY,
			height: element.clientHeight,
			width: element.clientWidth
		}));
		const button = page.getByRole('button', {
			name: '운동 시간에 포함되지 않은 걷기 4건',
			exact: true
		});
		const metricCards = page
			.locator('.exercise-card')
			.filter({ has: page.locator('.activity-trend') });
		const heightsBefore = await metricCards.evaluateAll((cards) =>
			cards.map((card) => card.getBoundingClientRect().height)
		);
		const typesCard = page.locator('section[aria-labelledby="exercise-types-title"]');
		const typesBefore = await typesCard.boundingBox();
		await button.click();
		const description = page.getByText(
			'평균 시속 4km 미만의 걷기는 운동 시간·횟수·활동 점수에 포함되지 않아요.',
			{
				exact: true
			}
		);
		await expect(description).toHaveCSS('position', 'absolute');
		const heightsAfter = await metricCards.evaluateAll((cards) =>
			cards.map((card) => card.getBoundingClientRect().height)
		);
		const typesAfter = await typesCard.boundingBox();
		if (!typesBefore || !typesAfter) throw new Error('운동 종류 카드 위치 없음');
		expect(typesAfter.height).toBeGreaterThan(typesBefore.height);
		// #269: PC의 같은 행은 높이를 맞추고, 아래 행의 지표는 펼침에 영향받지 않는다.
		if (width >= 1024) {
			expect(heightsAfter[0]).toBeCloseTo(typesAfter.height, 1);
			expect(heightsAfter[0]).toBeGreaterThan(heightsBefore[0]);
			expect(heightsAfter.slice(1)).toEqual(heightsBefore.slice(1));
		} else expect(heightsAfter).toEqual(heightsBefore);
		const bounds = await button.boundingBox();
		const content = await description.boundingBox();
		if (!bounds || !content) throw new Error('설명 위치 없음');
		expect(content.width).toBeLessThanOrEqual(1);
		expect(content.height).toBeLessThanOrEqual(1);
		const excluded = page.getByRole('list', {
			name: '운동 시간에 포함되지 않은 걷기',
			exact: true
		});
		expect(
			await excluded.evaluate((list) =>
				Array.from(list.querySelectorAll('li')).every((row) => {
					const columns = Array.from(row.children).map((column) => column.getBoundingClientRect());
					return columns.every(
						(column, index) => index === 0 || column.left >= columns[index - 1].right
					);
				})
			)
		).toBe(true);
		expect(
			await description.evaluate((element) => {
				const card = element.closest('.exercise-card')?.getBoundingClientRect();
				const rect = element.getBoundingClientRect();
				return (
					!!card && rect.left >= card.left && rect.right <= card.right && rect.bottom <= card.bottom
				);
			})
		).toBe(true);
		expect(
			await page.locator('.daily-steps-card').evaluate((element) => ({
				y: element.getBoundingClientRect().top + window.scrollY,
				height: element.clientHeight,
				width: element.clientWidth
			}))
		).toEqual(steps);
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
			)
		).toBe(true);
		await button.click();
		await expect(description).toBeHidden();
		expect(
			await metricCards.evaluateAll((cards) =>
				cards.map((card) => card.getBoundingClientRect().height)
			)
		).toEqual(heightsBefore);
	});
}

for (const viewport of [
	{ width: 360, height: 800 },
	{ width: 768, height: 1024 },
	{ width: 1366, height: 768 },
	{ width: 1440, height: 900 },
	{ width: 1536, height: 864 },
	{ width: 1920, height: 1080 },
	{ width: 2560, height: 1440 }
]) {
	test(`${viewport.width}×${viewport.height} 활동 차트와 문구에 가로 잘림 없이 접근한다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await openCompletedActivityWeek(page);
		await page.evaluate(() => document.fonts.ready);
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
			)
		).toBe(true);
		await expect(page.getByRole('banner')).toHaveCount(1);
		await expect(page.getByRole('main')).toHaveCount(1);
		for (const selector of [
			'.daily-steps-card',
			'.step-summary-card',
			'.exercise-card',
			'.activity-cta'
		]) {
			for (const item of await page.locator(selector).all()) {
				expect(
					await item.evaluate((element) => {
						const bounds = element.getBoundingClientRect();
						return Array.from(element.querySelectorAll('h2, h3, p, svg')).every((child) => {
							if (!child.getClientRects().length) return true;
							const rect = child.getBoundingClientRect();
							const style = getComputedStyle(child);
							if (
								style.position === 'absolute' &&
								(style.clip !== 'auto' || style.clipPath !== 'none')
							)
								return rect.width <= 1 && rect.height <= 1 && style.overflow === 'hidden';
							return (
								rect.left >= bounds.left &&
								rect.right <= bounds.right &&
								child.scrollWidth <= child.clientWidth
							);
						});
					})
				).toBe(true);
			}
		}
		const cta = page.getByRole('link', { name: '내 활동에 대해 물어보기' });
		await cta.scrollIntoViewIfNeeded();
		await expect(cta).toBeInViewport({ ratio: 1 });
	});
}

for (const viewport of [
	{ width: 1920, height: 1080 },
	{ width: 1366, height: 768 },
	{ width: 768, height: 1024 },
	{ width: 360, height: 800 }
]) {
	test(`${viewport.width}px에서 section 순서·카드 배치·점과 날짜 정렬을 유지한다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await openCompletedActivityWeek(page);
		await page.evaluate(() => document.fonts.ready);
		const rectangles = await page
			.locator(
				'.activity-period, .daily-steps-card, .step-summary-card, .activity-exercise, .exercise-card, .activity-cta'
			)
			.evaluateAll((elements) =>
				elements.map((element) => {
					const { left, right, top, bottom, width } = element.getBoundingClientRect();
					return { left, right, top, bottom, width };
				})
			);
		expect(rectangles).toHaveLength(10);
		const [period, daily, weekday, hourly, exercise, time, types, calories, heart, cta] =
			rectangles;
		expect(daily.top).toBeGreaterThanOrEqual(period.bottom);
		expect(weekday.top).toBeGreaterThanOrEqual(daily.bottom);
		expect(hourly.top).toBeGreaterThanOrEqual(daily.bottom);
		expect(exercise.top).toBeGreaterThanOrEqual(Math.max(weekday.bottom, hourly.bottom));
		expect(time.top).toBeGreaterThan(exercise.top);
		expect(cta.top).toBeGreaterThanOrEqual(exercise.bottom);

		if (viewport.width >= 1024) {
			for (const [left, right] of [
				[weekday, hourly],
				[time, types],
				[calories, heart]
			]) {
				expect(Math.abs(left.top - right.top)).toBeLessThanOrEqual(2);
				expect(left.right).toBeLessThan(right.left);
				expect(Math.abs(left.width - right.width)).toBeLessThanOrEqual(2);
			}
			expect(calories.top).toBeGreaterThanOrEqual(Math.max(time.bottom, types.bottom));
			expect(Math.abs(time.left - calories.left)).toBeLessThanOrEqual(2);
			expect(Math.abs(types.left - heart.left)).toBeLessThanOrEqual(2);
		} else {
			for (const [previous, next] of [
				[weekday, hourly],
				[time, types],
				[types, calories],
				[calories, heart]
			]) {
				expect(next.top).toBeGreaterThanOrEqual(previous.bottom);
				expect(Math.abs(previous.left - next.left)).toBeLessThanOrEqual(2);
				expect(Math.abs(previous.width - next.width)).toBeLessThanOrEqual(2);
			}
		}

		const charts = await page.locator('.activity-trend').evaluateAll((figures) =>
			figures.map((figure) => {
				const svg = figure.querySelector('svg');
				if (!svg) throw new Error('운동 차트 없음');
				const plot = svg.getBoundingClientRect();
				const labels = Array.from(figure.querySelectorAll('a'));
				return Array.from(figure.querySelectorAll('[data-trend-point]')).map((point) => {
					const date = point.getAttribute('data-date');
					const label = labels.find((link) => link.pathname.endsWith(`/health/day/${date}`));
					if (!label) throw new Error(`${date} 날짜 label 없음`);
					const pointRect = point.getBoundingClientRect();
					const labelRect = label.getBoundingClientRect();
					return {
						difference: Math.abs(
							pointRect.left + pointRect.width / 2 - labelRect.left - labelRect.width / 2
						),
						isInsidePlot:
							pointRect.left >= plot.left &&
							pointRect.right <= plot.right &&
							pointRect.top >= plot.top &&
							pointRect.bottom <= plot.bottom
					};
				});
			})
		);
		expect(charts).toHaveLength(3);
		for (const chart of charts) {
			expect(chart.length).toBeGreaterThan(0);
			for (const point of chart) {
				expect(point.difference).toBeLessThanOrEqual(2);
				expect(point.isInsidePlot).toBe(true);
			}
		}
		if (viewport.width === 1920) {
			const main = await page.getByRole('main').boundingBox();
			if (!main) throw new Error('Activity main 위치 없음');
			expect(main.width).toBe(1344);
			await expect(page.getByRole('heading', { level: 1 })).toHaveCSS('font-size', '30px');
			await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(250, 250, 251)');
		}
	});
}
