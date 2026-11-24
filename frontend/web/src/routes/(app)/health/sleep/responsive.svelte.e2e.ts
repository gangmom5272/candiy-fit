import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import { sleepResponsiveFixture as response } from '../../../../lib/testing/sleep-responsive-fixture';

type Scenario = Parameters<typeof response>[3];
type State = { kind: Scenario; today: string };

async function setup(page: Page, state: State) {
	await mockSignedIn(page);
	await page.clock.setFixedTime(new Date(state.today + 'T03:00:00Z'));
	await page.route('**/api/v1/health/metrics/*?*', async (route) => {
		const url = new URL(route.request().url());
		const metric = url.pathname.split('/').at(-1);
		if (metric !== 'sleeps' && metric !== 'oxygen_saturations') return route.fallback();
		const start = url.searchParams.get('start')!,
			end = url.searchParams.get('end')!;
		if (state.kind === 'loading') await new Promise((resolve) => setTimeout(resolve, 500));
		if (
			state.kind === 'error' ||
			state.kind === 'error-long' ||
			(state.kind === 'oxygen-error' && metric === 'oxygen_saturations') ||
			(state.kind === 'previous-error' &&
				end < new Date(Date.parse(state.today) - 6 * 86400000).toISOString().slice(0, 10))
		) {
			return route.fulfill({
				status: 503,
				headers: {
					'X-Request-Id':
						state.kind === 'error-long' ? 'R'.repeat(180) : '00000000-0000-4000-8000-000000000001'
				},
				json: {
					error: {
						code: 'LOOKUP_FAILED',
						message: '수면 데이터를 불러오지 못했습니다 '.repeat(20),
						retryable: true
					}
				}
			});
		}
		return route.fulfill({ json: { data: response(metric, start, end, state.kind, state.today) } });
	});
}
async function ready(page: Page) {
	await page.goto('/health/sleep');
	await expect(page.locator('#sleep-flow-title')).toBeVisible();
	await page.evaluate(() => document.fonts.ready);
}
async function geometry(page: Page) {
	return page.evaluate(() => {
		const box = (e: Element) => e.getBoundingClientRect();
		const flow = document.querySelector('section[aria-labelledby="sleep-flow-title"]')!;
		const figure = document.querySelector('figure[aria-label="날짜별 수면 중 평균 산소포화도"]');
		const axis = flow.querySelector(':scope > div.mt-7')!.lastElementChild!;
		const labels = [...axis.querySelectorAll('span')]
			.filter((e) => getComputedStyle(e).visibility !== 'hidden')
			.map((e) => ({ left: box(e).left, right: box(e).right, font: getComputedStyle(e).fontSize }));
		const clocks = [...flow.querySelectorAll('a.absolute[data-chart-navigation]')].map((e) => {
			const spans = e.parentElement!.querySelector('div[aria-hidden]')!.querySelectorAll('span');
			return box(spans[1]).left - box(spans[0]).right;
		});
		return {
			overflow: document.documentElement.scrollWidth - innerWidth,
			axisGaps: labels.slice(1).map((b, i) => b.left - labels[i].right),
			axisFonts: labels.map((l) => l.font),
			clocks,
			dates: [...flow.querySelectorAll('a[aria-label$=" 수면 상세"]')].map((e) =>
				e.textContent?.trim()
			),
			points: figure
				? [...figure.querySelectorAll('[data-oxygen-point]')].map((e) => {
						const p = box(e),
							b = box(e.nextElementSibling!),
							card = box(figure.closest('section')!);
						return {
							w: p.width,
							h: p.height,
							bw: b.width,
							bh: b.height,
							dx: p.x + p.width / 2 - b.x - b.width / 2,
							dy: p.y + p.height / 2 - b.y - b.height / 2,
							inside: p.left >= card.left && p.right <= card.right
						};
					})
				: [],
			oxygenFonts: figure
				? [...figure.querySelectorAll(':scope > div[aria-hidden] > span')].map(
						(e) => getComputedStyle(e).fontSize
					)
				: [],
			oxygenDates: figure
				? [...figure.querySelectorAll(':scope > div[aria-hidden] > span')].slice(-7).map((e) => {
						const r = box(e),
							card = box(figure.closest('section')!);
						return {
							left: r.left,
							right: r.right,
							inside: r.left >= card.left && r.right <= card.right
						};
					})
				: [],
			metrics: [...document.querySelectorAll('#sleep-oxygen-title')].flatMap((e) =>
				[...e.closest('section')!.querySelectorAll('dd.text-metric')].map(
					(d) => getComputedStyle(d).fontSize
				)
			)
		};
	});
}
function assertGeometry(g: Awaited<ReturnType<typeof geometry>>) {
	expect(g.overflow).toBeLessThanOrEqual(1);
	expect(g.dates).toHaveLength(7);
	expect(g.axisFonts.length).toBeGreaterThan(0);
	for (const font of [...g.axisFonts, ...g.oxygenFonts]) expect(font).toBe('12px');
	for (const gap of [...g.axisGaps, ...g.clocks]) expect(gap).toBeGreaterThanOrEqual(0);
	for (const p of g.points) {
		expect(p.w).toBe(7);
		expect(p.h).toBe(7);
		expect(p.bw).toBe(32);
		expect(p.bh).toBe(32);
		expect(Math.abs(p.dx)).toBeLessThan(0.1);
		expect(Math.abs(p.dy)).toBeLessThan(0.1);
		expect(p.inside).toBe(true);
	}
	for (const f of g.metrics) expect(f).toBe('28px');
	for (const d of g.oxygenDates) expect(d.inside).toBe(true);
	for (let i = 1; i < g.oxygenDates.length; i++)
		expect(g.oxygenDates[i].left - g.oxygenDates[i - 1].right).toBeGreaterThanOrEqual(0);
}
function contrast(foreground: string, background: string) {
	const luminance = (color: string) => {
		const v = color
			.match(/[\d.]+/g)!
			.slice(0, 3)
			.map(Number)
			.map((c) => {
				c /= 255;
				return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
			});
		return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
	};
	const a = luminance(foreground),
		b = luminance(background);
	return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
test('합성 응답의 오늘 완료 기록·일별 집계·측정 범위·시각 계약', () => {
	const s = response('sleeps', '2026-11-03', '2026-11-09'),
		o = response('oxygen_saturations', '2026-11-03', '2026-11-09');
	expect(s.summary.avg).toBe(450);
	expect(s.summary.completedDays).toBe(7);
	expect(s.summary.daysWithData).toBe(7);
	expect(s.inProgress).toBeNull();
	expect(o.summary.avg).toBe(96.57);
	expect(o.summary.dailyMin).toEqual({ date: '2026-11-07', value: 95 });
	expect(o.detail.measuredMin).toEqual({ date: '2026-11-07', value: 94 });
	expect(response('sleeps', '2026-11-03', '2026-11-09', 'empty').summary.avg).toBeNull();
	expect(response('sleeps', '2026-11-03', '2026-11-09', 'one-mid').summary.dailyMax).toEqual({
		date: '2026-11-06',
		value: 480
	});
	expect(response('sleeps', '2026-11-03', '2026-11-09', 'missing').summary.avg).toBe(300);
	for (const kind of [
		'normal',
		'empty',
		'missing',
		'one-first',
		'one-mid',
		'one-last',
		'equal',
		'extreme',
		'short',
		'sleep-only',
		'oxygen-only'
	] as const)
		for (const metric of ['sleeps', 'oxygen_saturations'] as const) {
			const data = response(metric, '2026-11-03', '2026-11-09', kind),
				valid = data.days.filter((d) => !d.missing);
			expect(data.summary.daysWithData).toBe(valid.length);
			expect(data.summary.daysUsedForSummary).toBe(valid.length);
			expect(data.days.every((d) => !d.isInProgress)).toBe(true);
			expect(data.summary.total).toBeNull();
			expect(
				valid.length + data.summary.missingDays.length + data.summary.unsyncedDays.length
			).toBe(7);
			if (!valid.length) {
				expect(data.summary.dailyMin).toBeNull();
				expect(data.summary.dailyMax).toBeNull();
			}
			for (const n of data.detail.nights) {
				expect(new Date(Date.parse(n.endAt) + 9 * 3600000).toISOString().slice(0, 10)).toBe(n.date);
				expect(Date.parse(n.startAt)).toBeLessThan(Date.parse(n.endAt));
				expect(Date.parse(n.endAt)).toBeLessThanOrEqual(Date.parse(data.dataStatus.lastDataAt!));
			}
			for (const r of data.detail.dailyRanges)
				if (r.min !== null) {
					const value = data.days.find((d) => d.date === r.date)!.value!;
					expect(r.min).toBeGreaterThan(0);
					expect(r.min).toBeLessThanOrEqual(value);
					expect(r.max!).toBeGreaterThanOrEqual(value);
					expect(r.max!).toBeLessThanOrEqual(100);
				}
		}
});
test('반응형 경계에서 날짜 7개·12px·7px 정원·32px 조작 영역', async ({ page }) => {
	await setup(page, { kind: 'normal', today: '2026-11-09' });
	await page.setViewportSize({ width: 320, height: 900 });
	await ready(page);
	for (const width of [
		320, 360, 390, 414, 639, 640, 767, 768, 1023, 1024, 1366, 1535, 1536, 1920
	]) {
		await page.setViewportSize({ width, height: 900 });
		await expect
			.poll(async () => {
				assertGeometry(await geometry(page));
				return true;
			})
			.toBe(true);
	}
});
test('짧은 구간·20시간·0·결측·단일 기록·동일값의 비겹침', async ({ page }) => {
	const state: State = { kind: 'short', today: '2026-11-09' };
	await setup(page, state);
	for (const kind of [
		'short',
		'extreme',
		'missing',
		'one-first',
		'one-mid',
		'one-last',
		'equal',
		'sleep-only',
		'oxygen-only'
	] as const)
		for (const width of [320, 390, 640, 1024]) {
			state.kind = kind;
			await page.setViewportSize({ width, height: 900 });
			await ready(page);
			await expect
				.poll(async () => {
					assertGeometry(await geometry(page));
					return true;
				})
				.toBe(true);
		}
});
test('증가·감소·중립·결측 텍스트의 문구·부호·4.5:1 대비', async ({ page }) => {
	const state: State = { kind: 'increase', today: '2026-11-09' };
	await setup(page, state);
	await page.setViewportSize({ width: 320, height: 900 });
	for (const [kind, text] of [
		['increase', '+30분 증가'],
		['decrease', '-30분 감소'],
		['neutral', '변화 없음'],
		['oxygen-only', '비교할 기록 없음'],
		['previous-error', '이전 기간 조회 실패']
	] as const) {
		state.kind = kind;
		await ready(page);
		const badge = page.getByText(text, { exact: true });
		await expect(badge).toBeVisible();
		const c = await badge.evaluate((e) => ({
			color: getComputedStyle(e).color,
			bg: getComputedStyle(e.closest('section')!).backgroundColor,
			background: getComputedStyle(e).backgroundColor,
			padding: getComputedStyle(e).padding,
			radius: getComputedStyle(e).borderRadius,
			labelWeight: getComputedStyle(e.parentElement!).fontWeight
		}));
		expect(c.color).toBe('rgb(44, 46, 53)');
		expect(c.background).toBe('rgba(0, 0, 0, 0)');
		expect(c.padding).toBe('0px');
		expect(c.radius).toBe('0px');
		expect(c.labelWeight).toBe('400');
		expect(contrast(c.color, c.bg)).toBeGreaterThanOrEqual(4.5);
	}
});
async function tooltipInside(page: Page) {
	await expect
		.poll(async () =>
			page.getByRole('tooltip').evaluate((e) => {
				const r = e.getBoundingClientRect(),
					header = document.querySelector('header')!.getBoundingClientRect(),
					nav = document.querySelector('nav[aria-label="주 메뉴"]')!,
					bottom =
						getComputedStyle(nav).position === 'fixed'
							? nav.getBoundingClientRect().top
							: innerHeight;
				return (
					r.left >= 8 &&
					r.right <= innerWidth - 8 + 0.1 &&
					r.top >= header.bottom + 8 - 0.1 &&
					r.bottom <= bottom - 8 + 0.1
				);
			})
		)
		.toBe(true);
}
test('낮은 화면의 툴팁은 고정 메뉴를 피하고 스크롤·resize에 대응한다', async ({ page }) => {
	await setup(page, { kind: 'normal', today: '2026-11-09' });
	await page.setViewportSize({ width: 740, height: 360 });
	await ready(page);
	const figure = page.getByRole('figure', { name: '날짜별 수면 중 평균 산소포화도' });
	for (const i of [0, 3, 6]) {
		const b = figure.locator('button').nth(i);
		await b.evaluate((e) => {
			const r = e.getBoundingClientRect();
			scrollTo(0, scrollY + r.y + r.height / 2 - 112);
		});
		await b.focus();
		await tooltipInside(page);
		await page.evaluate(() => scrollBy(0, 20));
		await tooltipInside(page);
		await page.keyboard.press('Escape');
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await expect(b).toBeFocused();
	}
	await page.setViewportSize({ width: 740, height: 240 });
	await figure.locator('button').first().focus();
	const last = figure.locator('button').last();
	const tip = page.getByRole('tooltip');
	for (let attempt = 0; attempt < 2; attempt++) {
		await last.focus();
		await tooltipInside(page);
		await page.keyboard.press('Tab');
		await expect(tip).toBeFocused();
		expect(await tip.evaluate((e) => e.scrollHeight > e.clientHeight)).toBe(true);
		await expect
			.poll(async () => {
				await page.keyboard.press('End');
				return tip.evaluate((e) => e.scrollTop);
			})
			.toBeGreaterThan(0);
		// End 키의 기본 스크롤이 끝난 뒤 복귀 자체가 화면을 움직이는지 비교한다.
		const scroll = await page.evaluate(async () => {
			let previous = scrollY;
			let stableFrames = 0;
			while (stableFrames < 3) {
				await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
				stableFrames = scrollY === previous ? stableFrames + 1 : 0;
				previous = scrollY;
			}
			return { x: scrollX, y: scrollY };
		});
		await page.keyboard.press('Escape');
		await expect(tip).toHaveCount(0);
		await expect(last).toBeFocused();
		expect(await page.evaluate(() => ({ x: scrollX, y: scrollY }))).toEqual(scroll);
		await expect(tip).toHaveCount(0);

		await page.keyboard.press('Tab');
		await expect(page.getByRole('link', { name: 'AI 코치로 이동', exact: true })).toBeFocused();
		await page.keyboard.press('Shift+Tab');
		await expect(last).toBeFocused();
		await expect(tip).toContainText('11월 9일');
		await page.keyboard.press('Shift+Tab');
		await expect(figure.locator('button').nth(5)).toBeFocused();
		await expect(tip).toContainText('11월 8일');
	}
});

// tap()은 터치를 지원하는 context에서만 동작한다. 터치를 쓰는 두 테스트에만 켠다
test.describe('터치 입력', () => {
	test.use({ hasTouch: true });

	test('툴팁 외부의 마우스·터치 및 기간 변경은 포인트로 포커스를 강제 복귀시키지 않는다', async ({
		page
	}) => {
		await setup(page, { kind: 'normal', today: '2026-11-09' });
		await page.setViewportSize({ width: 390, height: 844 });
		await ready(page);
		const figure = page.getByRole('figure', { name: '날짜별 수면 중 평균 산소포화도' });
		const point = figure.locator('button').last();
		const picker = page.getByRole('button', { name: /^날짜 선택,/ });
		for (const viewport of [
			{ width: 390, height: 844 },
			{ width: 740, height: 240 }
		]) {
			await page.setViewportSize(viewport);
			for (const input of ['mouse', 'touch'] as const) {
				await page.mouse.move(0, 0);
				await point.focus();
				await expect(page.getByRole('tooltip')).toBeVisible();
				if (viewport.height === 240) {
					await page.keyboard.press('Tab');
					await expect(page.getByRole('tooltip')).toBeFocused();
				}
				if (input === 'mouse') await picker.click();
				else await picker.tap();
				await expect(page.getByRole('tooltip')).toHaveCount(0);
				await expect(point).not.toBeFocused();
				await expect(page.getByRole('dialog')).toBeVisible();
				await page.keyboard.press('Escape');
				await expect(picker).toBeFocused();
			}
		}
		await point.focus();
		const previous = page.getByRole('button', { name: '이전 주', exact: true });
		await previous.click();
		await expect(previous).toBeFocused();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await expect(
			page.getByRole('link', { name: '2026-10-27 수면 상세', exact: true })
		).toBeVisible();
	});
	test('hover·focus·Tab·터치와 기간 변경·연말 날짜·달력·이동 링크', async ({ page }) => {
		const state: State = { kind: 'normal', today: '2026-11-09' };
		await setup(page, state);
		await page.setViewportSize({ width: 390, height: 844 });
		await ready(page);
		const fig = page.getByRole('figure', { name: '날짜별 수면 중 평균 산소포화도' });
		for (const i of [0, 3, 6]) {
			const b = fig.locator('button').nth(i);
			await b.hover();
			await tooltipInside(page);
			await page.keyboard.press('Escape');
			await b.focus();
			await tooltipInside(page);
			await page.keyboard.press('Escape');
			await b.tap();
			await tooltipInside(page);
			await page.keyboard.press('Escape');
		}
		await fig.locator('button').first().focus();
		await page.keyboard.press('Tab');
		await expect(fig.locator('button').nth(1)).toBeFocused();
		await page.keyboard.press('Shift+Tab');
		await expect(fig.locator('button').first()).toBeFocused();
		await page.getByRole('button', { name: '이전 주', exact: true }).click();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await expect(
			page.getByRole('link', { name: '2026-10-27 수면 상세', exact: true })
		).toBeVisible();
		await page.getByRole('button', { name: '다음 주', exact: true }).click();
		await expect(page.getByRole('button', { name: '다음 주', exact: true })).toBeDisabled();
		state.today = '2027-01-03';
		await page.clock.setFixedTime(new Date(state.today + 'T03:00:00Z'));
		await ready(page);
		await expect(page.getByRole('group', { name: '주간 기간 선택' })).toContainText(
			'2026.12.28 ~ 2027.01.03'
		);
		expect((await geometry(page)).dates).toEqual([
			'12/28',
			'12/29',
			'12/30',
			'12/31',
			'01/01',
			'01/02',
			'01/03'
		]);
		await page.getByRole('button', { name: /^날짜 선택,/ }).click();
		await expect(page.getByRole('dialog')).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(page.getByRole('dialog')).toHaveCount(0);
		await expect(
			page.getByRole('link', { name: '2026-12-28 수면 상세', exact: true })
		).toHaveAttribute('href', '/health/sleep/2026-12-28');
		await expect(page.getByRole('link', { name: 'AI 코치로 이동', exact: true })).toHaveAttribute(
			'href',
			'/coaching'
		);
	});
});
test('빈 상태·부분 오류·긴 ID 원문과 재시도·로딩', async ({ page }) => {
	const state: State = { kind: 'empty', today: '2026-11-09' };
	await setup(page, state);
	await page.setViewportSize({ width: 320, height: 900 });
	await ready(page);
	await expect(page.getByText('선택한 기간에 수면 중 산소포화도 기록이 없어요.')).toBeVisible();
	await expect(page.locator('[data-oxygen-point]')).toHaveCount(0);
	for (const kind of ['oxygen-error', 'error', 'error-long'] as const) {
		state.kind = kind;
		await page.goto('/health/sleep');
		const alert = page.getByRole('alert');
		await expect(alert).toBeVisible();
		await expect(alert).toContainText(
			kind === 'error-long' ? 'R'.repeat(180) : '00000000-0000-4000-8000-000000000001'
		);
		expect(await alert.evaluate((e) => e.scrollWidth - e.clientWidth)).toBeLessThanOrEqual(1);
		expect(
			await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
		).toBeLessThanOrEqual(1);
		if (kind === 'oxygen-error') await expect(page.locator('#sleep-flow-title')).toBeVisible();
	}
	state.kind = 'normal';
	await page.getByRole('button', { name: '다시 시도', exact: true }).click();
	await expect(page.locator('#sleep-flow-title')).toBeVisible();
	state.kind = 'loading';
	await page.goto('/health/sleep');
	await expect(
		page.getByRole('status').filter({ hasText: '수면 기록을 불러오는 중' })
	).toBeVisible();
	await expect(page.locator('#sleep-flow-title')).toBeVisible();
});
