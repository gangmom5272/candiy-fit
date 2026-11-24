import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../../../../lib/testing/home-api-fixture';
import {
	mockDayResponsiveApi,
	type DayScenario
} from '../../../../../lib/testing/day-responsive-fixture';

async function openDay(page: Page, scenario: DayScenario = 'normal') {
	await mockSignedIn(page);
	await mockDayResponsiveApi(page, scenario);
	await page.goto('/health/day/2026-11-09');
	await expect(page.locator('[data-timeline-scroll]')).toBeVisible();
	await page.evaluate(() => document.fonts.ready);
}

async function safeBounds(page: Page) {
	return page.evaluate(() => {
		const header = document.querySelector('header');
		const nav = document.querySelector('nav[aria-label="주 메뉴"]');
		return {
			top:
				header && ['fixed', 'sticky'].includes(getComputedStyle(header).position)
					? header.getBoundingClientRect().bottom
					: 0,
			bottom:
				nav && getComputedStyle(nav).position === 'fixed'
					? nav.getBoundingClientRect().top
					: innerHeight
		};
	});
}

async function expectSafe(page: Page, target: Locator) {
	await expect
		.poll(async () => {
			const box = await target.boundingBox();
			const safe = await safeBounds(page);
			return !!box && box.y >= safe.top && box.y + box.height <= safe.bottom;
		})
		.toBe(true);
}

async function tabTo(page: Page, target: Locator) {
	for (let count = 0; count < 120; count++) {
		if (await target.evaluate((element) => element === document.activeElement)) return;
		await page.keyboard.press('Tab');
	}
	throw new Error('Tab 순서에서 대상을 찾지 못했습니다.');
}

async function expectAxis(page: Page, midnightVisible: boolean) {
	const data = await page.locator('.day-time-plot').evaluate((plot) => {
		const bounds = plot.getBoundingClientRect();
		return {
			left: bounds.left,
			right: bounds.right,
			start: Number((plot as HTMLElement).dataset.rangeStart),
			labels: [...plot.querySelectorAll<HTMLElement>('[data-time-minute]')]
				.filter((label) => getComputedStyle(label).visibility !== 'hidden')
				.map((label) => ({
					minute: Number(label.dataset.timeMinute),
					size: getComputedStyle(label).fontSize,
					box: label.getBoundingClientRect().toJSON()
				}))
		};
	});
	expect(data.labels[0].minute).toBe(data.start);
	expect(data.labels.at(-1)?.minute).toBe(1440);
	expect(data.labels.some((label) => label.minute === 0)).toBe(midnightVisible);
	for (const label of data.labels) {
		expect(label.size).toBe('12px');
		expect(label.box.left).toBeGreaterThanOrEqual(data.left);
		expect(label.box.right).toBeLessThanOrEqual(data.right);
	}
	for (let index = 1; index < data.labels.length; index++) {
		const previous = data.labels[index - 1].box;
		const current = data.labels[index].box;
		if (current.top < previous.bottom && current.bottom > previous.top)
			expect(current.left).toBeGreaterThanOrEqual(previous.right);
	}
	await expect(page.getByRole('list', { name: '타임라인 범례' })).toHaveCSS('font-size', '12px');
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

for (const width of [320, 360, 390, 639, 640, 768, 769, 1023, 1024, 1366, 1920]) {
	test(`${width}px 요약·12px 시간축·좌표·원형을 유지한다`, async ({ page }, testInfo) => {
		await page.setViewportSize({ width, height: 900 });
		await openDay(page);
		await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
			'11,100보',
			'30분',
			'7시간 30분',
			'71.5 bpm'
		]);
		await expectAxis(page, false);
		const geometry = await page
			.locator('[data-heart-point] span')
			.nth(12)
			.evaluate((dot) => {
				const box = dot.getBoundingClientRect();
				const element = document.querySelector('.day-time-plot');
				if (!element) throw new Error('시간축 없음');
				const plot = element.getBoundingClientRect();
				return {
					width: box.width,
					height: box.height,
					x: box.x + box.width / 2 - plot.x,
					expectedX: (plot.width * 810) / 1500
				};
			});
		expect(geometry.width).toBe(6);
		expect(geometry.height).toBe(6);
		expect(Math.abs(geometry.x - geometry.expectedX)).toBeLessThan(1);
		const summary = page.locator('dl[aria-label="하루 기록 요약"]');
		const first = await summary.locator(':scope > div').first().boundingBox();
		const second = await summary.locator(':scope > div').nth(1).boundingBox();
		if (!first || !second) throw new Error('요약 카드 경계 없음');
		if (width < 640) {
			expect(first.height).toBe(102);
			expect(second.y - first.y - first.height).toBe(12);
			await expect(summary.locator('dd').first()).toHaveCSS('font-size', '24px');
		} else {
			expect(first.height).toBe(128);
			await expect(summary.locator('dd').first()).toHaveCSS('font-size', '28px');
		}
		if ([320, 390, 1366].includes(width))
			await page.screenshot({ path: testInfo.outputPath(`day-${width}.png`), fullPage: true });
	});
}

for (const width of [320, 390, 1024]) {
	for (const scenario of [
		'empty',
		'partial',
		'single',
		'zero',
		'dense',
		'wide',
		'short',
		'long',
		'sleepnull',
		'sleepzero'
	] as const) {
		test(`${width}px ${scenario} 값과 시간축 경계를 보존한다`, async ({ page }) => {
			await page.setViewportSize({ width, height: 900 });
			await openDay(page, scenario);
			await expectAxis(page, ['empty', 'partial', 'single', 'zero', 'long'].includes(scenario));
			const values = page.locator('dl[aria-label="하루 기록 요약"] dd');
			if (scenario === 'empty') await expect(values).toHaveText(Array(4).fill('기록 없음'));
			if (scenario === 'partial')
				await expect(values).toHaveText(['기록 없음', '기록 없음', '기록 없음', '71.5 bpm']);
			if (scenario === 'single')
				await expect(values).toHaveText(['481보', '기록 없음', '기록 없음', '70 bpm']);
			if (scenario === 'zero')
				await expect(values).toHaveText(['기록 없음', '0분', '기록 없음', '기록 없음']);
			if (scenario === 'dense')
				await expect(values).toHaveText(['2,400,000보', '32분', '7시간 30분', '71.5 bpm']);
			if (scenario === 'wide') await expect(values.last()).toHaveText('109 bpm');
			if (scenario === 'short') await expect(values.nth(2)).toHaveText('0시간 15분');
			if (scenario === 'long') await expect(values.nth(2)).toHaveText('19시간 30분');
			if (scenario === 'sleepnull') await expect(values.nth(2)).toHaveText('값 없음');
			if (scenario === 'sleepzero') await expect(values.nth(2)).toHaveText('0시간 0분');
		});
	}
}

for (const [width, height] of [
	[740, 240],
	[390, 360],
	[390, 180]
]) {
	test(`${width}×${height} 캘린더가 고정 UI 사이에서 스크롤되고 모든 조작에 접근한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height });
		await openDay(page);
		const trigger = page.getByRole('button', { name: '날짜 선택, 2026.11.09' });
		await trigger.focus();
		await page.keyboard.press('Enter');
		const calendar = page.getByRole('dialog', { name: '날짜 선택 캘린더' });
		await expectSafe(page, calendar);
		expect(await calendar.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(
			true
		);
		await expect(calendar.getByRole('link', { name: '2026-11-09', exact: true })).toBeFocused();
		// Native focus scrolling must expose the control within this scroll panel.
		const previous = calendar.getByRole('button', { name: '이전 달' });
		for (let count = 0; count < 15; count++) {
			if (await previous.evaluate((element) => element === document.activeElement)) break;
			await page.keyboard.press('Shift+Tab');
		}
		await expect(previous).toBeFocused();
		await page.keyboard.press('Space');
		await expect(calendar.getByText('2026년 10월')).toBeVisible();
		const chosen = calendar.getByRole('link', { name: '2026-10-31', exact: true });
		await chosen.click();
		await expect(page).toHaveURL('/health/day/2026-10-31');
		await page.getByRole('button', { name: '날짜 선택, 2026.10.31' }).click();
		await expectSafe(page, calendar);
		await calendar.getByRole('button', { name: '닫기' }).click();
		await expect(calendar).not.toBeVisible();
		await expect(page.getByRole('button', { name: '날짜 선택, 2026.10.31' })).toBeFocused();
		await page.screenshot({ path: testInfo.outputPath('calendar-closed.png') });
	});
}

test('키보드 자동 가로 스크롤은 상세를 유지하고 사용자 휠 스크롤은 닫는다', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 900 });
	await openDay(page);
	await page.mouse.move(0, 0);
	const axis = page.getByRole('region', { name: '하루 기록 시간축', exact: true });
	await axis.focus();
	const point = page.locator('[data-heart-point]').nth(8);
	await tabTo(page, point);
	await expect(point).toBeFocused();
	await expectSafe(page, point);
	await expect(page.getByRole('tooltip')).toContainText('08:00 ~ 09:00');
	expect(await axis.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
	await axis.hover({ position: { x: 20, y: 10 } });
	await page.mouse.wheel(100, 0);
	await expect(page.getByRole('tooltip')).toHaveCount(0);
});

test('심박 스크롤 상세의 Tab·Shift+Tab·Escape·다음 포인트와 실제 가시성을 보존한다', async ({
	page
}) => {
	await page.setViewportSize({ width: 740, height: 240 });
	await openDay(page, 'wide');
	await page.mouse.move(0, 0);
	await page.getByRole('region', { name: '하루 기록 시간축', exact: true }).focus();
	const point = page.locator('[data-heart-point]').nth(1);
	await tabTo(page, point);
	await expectSafe(page, point);
	const tooltip = page.getByRole('tooltip');
	await expect(tooltip).toHaveAttribute('data-scrollable', 'true');
	await page.keyboard.press('Tab');
	await expect(tooltip).toBeFocused();
	await expectSafe(page, tooltip);
	const documentScroll = await page.evaluate(() => scrollY);
	await page.keyboard.press('End');
	await expect.poll(() => tooltip.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
	await expect
		.poll(() =>
			tooltip.evaluate(
				(element) => element.scrollTop === element.scrollHeight - element.clientHeight
			)
		)
		.toBe(true);
	expect(await page.evaluate(() => scrollY)).toBe(documentScroll);
	await tooltip.hover();
	for (let count = 0; count < 2; count++) {
		await page.mouse.wheel(0, 100);
		await page.waitForTimeout(100);
	}
	expect(await page.evaluate(() => scrollY)).toBe(documentScroll);
	await expect(tooltip).toBeFocused();
	await expectSafe(page, point);
	await page.mouse.wheel(0, -100);
	await expect.poll(() => tooltip.evaluate((element) => element.scrollTop)).toBe(0);
	await page.keyboard.press('Shift+Tab');
	await expect(point).toBeFocused();
	await page.keyboard.press('Tab');
	await expect(tooltip).toBeFocused();
	await page.keyboard.press('Tab');
	const next = page.locator('[data-heart-point]').nth(2);
	await expect(next).toBeFocused();
	await expectSafe(page, next);
	await expect(tooltip).toContainText('02:00 ~ 03:00');
	await page.keyboard.press('Tab');
	await expect(tooltip).toBeFocused();
	const beforeEscape = await page.evaluate(() => scrollY);
	await page.keyboard.press('Escape');
	await expect(next).toBeFocused();
	await expectSafe(page, next);
	await expect(tooltip).toHaveCount(0);
	await page.waitForTimeout(200);
	await expect(tooltip).toHaveCount(0);
	expect(await page.evaluate(() => scrollY)).toBe(beforeEscape);
	await page.keyboard.press('Shift+Tab');
	await expect(point).toBeFocused();
	await expect(tooltip).toContainText('01:00 ~ 02:00');
});

for (const index of [0, 12, 23]) {
	test(`${index}시: Escape는 보이는 포인트를 움직이지 않고 가려진 포인트만 드러낸다`, async ({
		page
	}) => {
		await page.setViewportSize({ width: 740, height: 240 });
		await openDay(page);
		await page.mouse.move(0, 0);
		const axis = page.getByRole('region', { name: '하루 기록 시간축', exact: true });
		const point = page.locator('[data-heart-point]').nth(index);
		const tooltip = page.getByRole('tooltip');
		await axis.focus();
		await tabTo(page, point);
		await page.keyboard.press('Tab');
		await expect(tooltip).toBeFocused();
		const visibleScroll = await page.evaluate(() => scrollY);
		await page.keyboard.press('Escape');
		await expect(point).toBeFocused();
		await expectSafe(page, point);
		expect(await page.evaluate(() => scrollY)).toBe(visibleScroll);
		await expect(tooltip).toHaveCount(0);

		await axis.focus();
		await tabTo(page, point);
		await page.keyboard.press('Tab');
		await expect(tooltip).toBeFocused();
		const beforeOutsideScroll = await page.evaluate(() => scrollY);
		// Deliberate page scrolling outside the panel is still allowed.
		await page.mouse.move(710, 110);
		await page.mouse.wheel(0, 150);
		await page.waitForTimeout(180);
		await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(beforeOutsideScroll);
		const before = await point.boundingBox();
		if (!before) throw new Error('복귀 포인트 경계 없음');
		const safe = await safeBounds(page);
		expect(before.y).toBeLessThan(safe.top);
		const hiddenScroll = await page.evaluate(() => scrollY);
		await page.keyboard.press('Escape');
		await expect(point).toBeFocused();
		await expectSafe(page, point);
		await expect(tooltip).toHaveCount(0);
		const restoredScroll = await page.evaluate(() => scrollY);
		expect(restoredScroll).toBeLessThan(hiddenScroll);
		// Reveal only the covered distance, plus the existing 8px focus gap.
		expect(hiddenScroll - restoredScroll).toBeLessThanOrEqual(Math.ceil(safe.top - before.y + 8));
		await page.waitForTimeout(150);
		await expect(tooltip).toHaveCount(0);
		// A later Escape with no open tooltip must not undo deliberate page scrolling.
		await page.mouse.wheel(0, 150);
		await page.waitForTimeout(180);
		const closedScroll = await page.evaluate(() => scrollY);
		expect(closedScroll).toBeGreaterThan(restoredScroll);
		await page.keyboard.press('Escape');
		expect(await page.evaluate(() => scrollY)).toBe(closedScroll);
		await expect(tooltip).toHaveCount(0);
	});
}

test('스크롤되지 않는 일반 툴팁은 문서 휠 스크롤과 보이는 포인트의 Escape를 유지한다', async ({
	page
}) => {
	await page.setViewportSize({ width: 390, height: 900 });
	await openDay(page);
	await page.mouse.move(0, 0);
	await page.getByRole('region', { name: '하루 기록 시간축', exact: true }).focus();
	const point = page.locator('[data-heart-point]').nth(1);
	await tabTo(page, point);
	const tooltip = page.getByRole('tooltip');
	await expect(tooltip).toHaveAttribute('data-scrollable', 'false');
	await expect(tooltip).toHaveCSS('overscroll-behavior-y', 'auto');
	const before = await page.evaluate(() => scrollY);
	await tooltip.hover();
	await page.mouse.wheel(0, 100);
	await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(before);
	await expectSafe(page, point);
	const afterWheel = await page.evaluate(() => scrollY);
	await page.keyboard.press('Escape');
	await expect(point).toBeFocused();
	await expectSafe(page, point);
	await expect(tooltip).toHaveCount(0);
	expect(await page.evaluate(() => scrollY)).toBe(afterWheel);
});

test('일반 툴팁의 Tab은 다음 포인트로 이동하고 CTA는 실제로 보이며 이미 보이면 움직이지 않는다', async ({
	page
}) => {
	await page.setViewportSize({ width: 390, height: 900 });
	await openDay(page);
	await page.mouse.move(0, 0);
	await page.getByRole('region', { name: '하루 기록 시간축', exact: true }).focus();
	const first = page.locator('[data-heart-point]').first();
	await tabTo(page, first);
	await expect(page.getByRole('tooltip')).toHaveAttribute('data-scrollable', 'false');
	await page.keyboard.press('Tab');
	await expect(page.locator('[data-heart-point]').nth(1)).toBeFocused();
	const cta = page.getByRole('link', { name: '이날 기록에 대해 물어보기' });
	await tabTo(page, cta);
	await expectSafe(page, cta);
	const currentScroll = await page.evaluate(() => scrollY);
	await page.keyboard.press('Shift+Tab');
	await page.keyboard.press('Tab');
	await expect(cta).toBeFocused();
	await expectSafe(page, cta);
	expect(await page.evaluate(() => scrollY)).toBe(currentScroll);
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL('/coaching');
});

test('리사이즈·폰트 로딩·월말 및 연말 이동 후 눈금과 선택을 갱신한다', async ({ page }) => {
	let releaseFonts = () => {};
	const fontsReady = new Promise<void>((resolve) => {
		releaseFonts = resolve;
	});
	await page.route('**/*.woff2', async (route) => {
		await fontsReady;
		await route.continue();
	});
	await page.setViewportSize({ width: 320, height: 900 });
	await mockSignedIn(page);
	await mockDayResponsiveApi(page, 'partial');
	try {
		await page.goto('/health/day/2026-11-09', { waitUntil: 'domcontentloaded' });
		await expect(page.locator('[data-timeline-scroll]')).toBeVisible();
		await expectAxis(page, true);
	} finally {
		releaseFonts();
	}
	await page.evaluate(() => document.fonts.ready);
	await page.waitForTimeout(50);
	await expectAxis(page, true);
	for (const width of [639, 640, 768, 769, 1023, 1024, 390]) {
		await page.setViewportSize({ width, height: 900 });
		await expect.poll(async () => page.locator('[data-time-minute]').count()).toBeGreaterThan(1);
		await page.waitForTimeout(50);
		await expectAxis(page, true);
	}
	await page.goto('/health/day/2026-10-31');
	await page.getByRole('link', { name: '다음 날짜', exact: true }).click();
	await expect(page).toHaveURL('/health/day/2026-11-01');
	await page.goto('/health/day/2025-12-31');
	await page.getByRole('link', { name: '다음 날짜', exact: true }).click();
	await expect(page).toHaveURL('/health/day/2026-01-01');
	await expect(page.getByRole('tooltip')).toHaveCount(0);
});

test('터치 선택·외부 닫기와 낮은 캘린더 날짜 선택을 유지한다', async ({ browser }) => {
	const context = await browser.newContext({
		viewport: { width: 390, height: 360 },
		hasTouch: true,
		isMobile: true
	});
	try {
		const page = await context.newPage();
		await openDay(page, 'single');
		await page.locator('[data-step-trigger="12"]').tap();
		await expect(page.getByRole('tooltip')).toContainText('481보');
		await page.getByRole('heading', { name: '나의 하루' }).tap();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await page.getByRole('button', { name: '날짜 선택, 2026.11.09' }).tap();
		await expectSafe(page, page.getByRole('dialog'));
		await page.getByRole('dialog').getByRole('link', { name: '2026-11-08', exact: true }).tap();
		await expect(page).toHaveURL('/health/day/2026-11-08');
	} finally {
		await context.close();
	}
});

for (const path of ['/health', '/health/activity', '/health/sleep', '/health/heart']) {
	for (const width of [390, 1366]) {
		test(`${path} ${width}px 공용 캘린더의 날짜 선택과 기존 CTA를 보존한다`, async ({ page }) => {
			await page.setViewportSize({ width, height: width === 390 ? 360 : 900 });
			await mockSignedIn(page);
			await mockHomeApi(page);
			await page.goto(path);
			const trigger = page
				.getByRole('group', { name: '주간 기간 선택' })
				.getByRole('button', { name: /^날짜 선택/ });
			await trigger.click();
			const calendar = page.getByRole('dialog', { name: '날짜 선택 캘린더' });
			await expectSafe(page, calendar);
			await calendar.getByRole('button', { name: '2026-10-20', exact: true }).click();
			await expect(calendar).not.toBeVisible();
			await expect(page).toHaveURL(path);
			await trigger.click();
			await expect(
				calendar.getByRole('button', { name: '2026-10-20', exact: true })
			).toHaveAttribute('aria-current', 'date');
			await page.keyboard.press('Escape');
			await expect(trigger).toBeFocused();
			const cta = page.locator('main a[href="/coaching"]');
			await expect(cta).toHaveCSS('font-size', '14px');
			await expect(cta).toHaveCSS('border-radius', '12px');
			await cta.click();
			await expect(page).toHaveURL('/coaching');
		});
	}
}
