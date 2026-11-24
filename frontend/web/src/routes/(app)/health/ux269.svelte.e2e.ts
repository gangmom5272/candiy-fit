import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../../lib/testing/home-api-fixture';
import { activityMetricFixture } from '../../../lib/testing/activity-api-fixture';
import { mockDayDetailApi } from '../../../lib/testing/day-detail-api-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
});

for (const width of [1366, 1024, 1023, 768, 640, 639, 390, 360, 320]) {
	test(`로딩 전환 경계 ${width}: 카드와 아래 콘텐츠 위치를 유지한다`, async ({ page }, info) => {
		await page.setViewportSize({ width, height: 800 });
		const measurements = [];
		for (const tab of ['activity', 'sleep', 'heart']) {
			let release = () => {};
			const gate = new Promise<void>((resolve) => {
				release = resolve;
			});
			await page.route('**/api/v1/health/metrics/*?*', async (route) => {
				await gate;
				await route.fallback();
			});
			await page.route('**/api/v1/health/sync', async (route) => {
				await gate;
				await route.fallback();
			});
			try {
				await page.goto(`/health/${tab}`);
				const loader = page.locator('[data-health-loading]');
				await expect(loader).toBeVisible();
				await expect(loader).toHaveAttribute('role', 'status');
				await expect(loader.getByRole('button')).toHaveCount(0);
				await expect(loader.getByRole('link')).toHaveCount(0);
				await page.evaluate(() => document.fonts.ready);
				const before = await loader.locator('[data-loading-card]').boundingBox();
				const loadingDateHeight =
					tab === 'activity'
						? await loader
								.locator('[data-loading-card] [class*="mt-1.5"]')
								.evaluate((element) => element.getBoundingClientRect().height)
						: null;
				const nextBefore =
					tab === 'heart'
						? await loader.evaluate(
								(element) => element.nextElementSibling!.getBoundingClientRect().top
							)
						: await loader
								.locator('[data-loading-following]')
								.evaluate((element) => element.getBoundingClientRect().top);
				release();
				await expect(loader).toHaveCount(0);
				const card = page.locator(
					tab === 'activity'
						? '.daily-steps-card'
						: tab === 'sleep'
							? 'section[aria-labelledby="sleep-flow-title"]'
							: '.heart-card'
				);
				await expect(card).toBeVisible();
				const after = await card.boundingBox();
				if (tab === 'activity') {
					const date = card.locator('figure .grid-cols-7 a').first();
					await expect(date).toHaveCSS('font-size', '12px');
					await expect(date).toHaveCSS('line-height', '16px');
					expect((await date.boundingBox())!.height).toBe(loadingDateHeight);
				}
				const nextAfter = await card.evaluate(
					(element) => element.nextElementSibling!.getBoundingClientRect().top
				);
				measurements.push({
					tab,
					width,
					before: before!.height,
					after: after!.height,
					topDelta: after!.y - before!.y,
					nextDelta: nextAfter - nextBefore
				});
				expect(Math.abs(after!.height - before!.height), tab).toBeLessThanOrEqual(1);
				expect(Math.abs(after!.y - before!.y), tab).toBeLessThanOrEqual(1);
				expect(Math.abs(nextAfter - nextBefore), tab).toBeLessThanOrEqual(1);
				expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
					true
				);
				await page.screenshot({ path: info.outputPath(`${tab}-stable-${width}.png`) });
			} finally {
				release();
			}
		}
		await info.attach('loading-geometry', {
			body: JSON.stringify(measurements),
			contentType: 'application/json'
		});
	});
}

for (const width of [1366, 1024, 768, 360]) {
	test(`동기화 중 스켈레톤 ${width}: 홈과 건강 홈의 카드 구조를 먼저 표시한다`, async ({
		page
	}, info) => {
		await page.setViewportSize({ width, height: 800 });
		for (const path of ['/home', '/health']) {
			let release = () => {};
			const gate = new Promise<void>((resolve) => {
				release = resolve;
			});
			await page.route('**/api/v1/health/sync', async (route) => {
				await gate;
				await route.fulfill({ json: { data: { status: 'SYNCED' } } });
			});
			try {
				await page.goto(path);
				await expect(page.getByRole('main')).toBeVisible();
				const cards = page.locator(path === '/home' ? '.metric-card' : '.health-summary-card');
				await expect(cards).toHaveCount(4);
				for (const card of await cards.all())
					await expect(card).toHaveAttribute('aria-busy', 'true');
				await expect(page.locator('img[src*="character-loading"]')).toHaveCount(0);
				await expect.poll(() => page.locator('.health-skeleton').count()).toBeGreaterThan(0);
				await page.evaluate(() => document.fonts.ready);
				expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
					true
				);
				await page.screenshot({ path: info.outputPath(`${path.slice(1)}-skeleton-${width}.png`) });
				release();
				for (const card of await cards.all())
					await expect(card).toHaveAttribute('aria-busy', 'false');
			} finally {
				release();
			}
		}
	});
}

for (const width of [1366, 1024, 768, 360]) {
	test(`메인 카드 안쪽 여백 ${width}: 활동·수면·심박의 상하좌우 간격을 맞춘다`, async ({
		page
	}, info) => {
		await page.setViewportSize({ width, height: 800 });
		for (const tab of ['activity', 'sleep', 'heart']) {
			await page.goto(`/health/${tab}`);
			const cards = page.locator(
				tab === 'activity'
					? '.daily-steps-card, .step-summary-card, .exercise-card'
					: tab === 'sleep'
						? 'section[aria-labelledby="sleep-flow-title"], section[aria-labelledby="sleep-summary-title"], section[aria-labelledby="sleep-oxygen-title"]'
						: '.heart-card'
			);
			await expect(cards).toHaveCount(tab === 'activity' ? 7 : tab === 'sleep' ? 3 : 1);
			for (const card of await cards.all()) {
				await expect(card).toHaveCSS('padding-top', '24px');
				await expect(card).toHaveCSS('padding-bottom', '24px');
				await expect(card).toHaveCSS('padding-left', width < 640 ? '16px' : '28px');
				await expect(card).toHaveCSS('padding-right', width < 640 ? '16px' : '28px');
			}
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
			await page.screenshot({ path: info.outputPath(`${tab}-padding-${width}.png`) });
		}
	});
}

for (const width of [1366, 360]) {
	test(`평균 제외 안내 정렬 ${width}: 활동과 심박의 실제 위치와 글꼴이 같다`, async ({
		page
	}, info) => {
		await page.setViewportSize({ width, height: 900 });
		const positions = [];
		for (const tab of ['activity', 'heart']) {
			await page.goto(`/health/${tab}`);
			const note = page.getByText(
				tab === 'activity'
					? '오늘의 걸음 수와 운동 시간은 수집 중이며 기간 평균에서 제외해요.'
					: '오늘 기록은 수집 중이며 기간 평균에서 제외해요.',
				{ exact: true }
			);
			await expect(note).toBeVisible();
			await page.evaluate(() => document.fonts.ready);
			positions.push(
				await note.evaluate((element) => {
					const period = document
						.querySelector('[role="group"][aria-label="주간 기간 선택"]')!
						.parentElement!.getBoundingClientRect();
					const rect = element.getBoundingClientRect();
					const style = getComputedStyle(element);
					return {
						left: rect.left,
						top: rect.top,
						gap: rect.top - period.bottom,
						font: style.font,
						color: style.color
					};
				})
			);
			await page.screenshot({ path: info.outputPath(`${tab}-${width}.png`) });
		}
		expect(positions[1]).toEqual(positions[0]);
		expect(positions[1].gap).toBe(16);
	});
}

for (const width of [1366, 768, 360]) {
	test(`공통 표시 ${width}: 기간·캘린더·이동 반응·스크롤 범위를 확인한다`, async ({
		page,
		hasTouch
	}, info) => {
		await page.setViewportSize({ width, height: 900 });
		await page.goto('/health');
		await expect(page.getByRole('figure')).toHaveCount(4);
		if (!hasTouch) {
			const card = page
				.locator('.health-summary-card')
				.filter({ has: page.getByRole('heading', { name: '걸음 수', exact: true }) });
			await card.getByRole('heading').hover();
			await expect(card).toHaveCSS('top', '-2px');
			await expect(card.getByRole('heading')).toHaveCSS('transform', 'none');
			await page.keyboard.press('Tab');
			await card.locator('[data-chart-point]').first().focus();
			const tooltip = page.getByRole('tooltip');
			await expect(tooltip).toBeVisible();
			const box = await tooltip.boundingBox();
			expect(box!.x).toBeGreaterThanOrEqual(0);
			expect(box!.x + box!.width).toBeLessThanOrEqual(width);
			await page.keyboard.press('Escape');
		}
		const period = page.getByRole('group', { name: '주간 기간 선택' });
		const request = page.waitForRequest(
			'**/api/v1/health/metrics/steps?start=2026-10-13&end=2026-10-19'
		);
		await period.getByRole('button', { name: '이전 주', exact: true }).click();
		await request;
		await expect(period.getByRole('status')).toHaveText('2026.10.13 ~ 10.19');
		const calendarButton = period.getByRole('button', { name: /^날짜 선택,/ });
		if (!hasTouch) {
			await calendarButton.hover();
			await expect(calendarButton).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
			await expect(calendarButton.locator('.date-picker-label')).toHaveCSS(
				'transform',
				'matrix(1, 0, 0, 1, 0, -1)'
			);
		}
		if (hasTouch) await calendarButton.tap();
		else await calendarButton.click();
		const dialog = page.getByRole('dialog', { name: '날짜 선택 캘린더' });
		await expect(dialog.getByRole('button', { name: '2026-10-27', exact: true })).toBeDisabled();
		if (!hasTouch) {
			await dialog.getByRole('button', { name: '닫기', exact: true }).hover();
			await expect(dialog.getByRole('button', { name: '닫기', exact: true })).toHaveCSS(
				'background-color',
				'rgba(0, 0, 0, 0)'
			);
		}
		await page.screenshot({ path: info.outputPath(`calendar-${width}.png`) });
		await dialog.getByRole('button', { name: '2026-10-23', exact: true }).focus();
		await page.keyboard.press('Enter');
		await expect(dialog).not.toBeVisible();
		await expect(period.getByRole('status')).toHaveText('2026.10.17 ~ 10.23');
		await expect(page.getByRole('figure')).toHaveCount(4);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
		await page.mouse.move(-10, -10);
		const styling = await page.evaluate(() => ({
			webkit: CSS.supports('selector(::-webkit-scrollbar)'),
			color: getComputedStyle(document.documentElement).scrollbarColor,
			thumb: getComputedStyle(document.documentElement, '::-webkit-scrollbar-thumb').backgroundColor
		}));
		if (styling.webkit) expect(styling.thumb).toBe('rgb(245, 199, 188)');
		else expect(styling.color).toBe('rgb(245, 199, 188) rgb(243, 246, 251)');
		await info.attach('scrollbar-support', {
			body: JSON.stringify(styling),
			contentType: 'application/json'
		});
		await page.screenshot({ path: info.outputPath(`health-${width}.png`), fullPage: true });
		// 스크롤바는 앱 전체가 같다. 건강 화면 표시(data-health-ui)가 없어도 그대로다
		await page
			.locator('[data-health-ui]')
			.evaluate((element) => element.removeAttribute('data-health-ui'));
		const unscoped = await page.evaluate(() => ({
			color: getComputedStyle(document.documentElement).scrollbarColor,
			thumb: getComputedStyle(document.documentElement, '::-webkit-scrollbar-thumb').backgroundColor
		}));
		if (styling.webkit) expect(unscoped.thumb).toBe(styling.thumb);
		else expect(unscoped.color).toBe(styling.color);
		await page.goto('/home');
		const cards = page.locator('.metric-card');
		await expect(cards).toHaveCount(4);
		await expect(cards.first()).not.toHaveAttribute('aria-busy', 'true');
		if (!hasTouch) {
			await cards.first().hover();
			await expect
				.poll(() => cards.first().evaluate((element) => getComputedStyle(element).transform))
				.not.toBe('none');
		}
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await expect(cards.first()).toHaveCSS('transform', 'none');
		await cards.first().focus();
		await expect(cards.first()).toHaveCSS('outline-style', 'solid');
		await page.screenshot({ path: info.outputPath(`home-${width}.png`), fullPage: true });
		if (hasTouch) {
			await cards.first().tap();
			await expect(page).toHaveURL(/\/health\/activity$/);
		}
	});
}

test('지연·재조회·빈 기록·오류는 서로 구분하고 이전 기간 데이터를 남기지 않는다', async ({
	page
}, info) => {
	let release = () => {};
	let gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.route('**/api/v1/health/metrics/*?*', async (route) => {
		await gate;
		return route.fallback();
	});
	await page.goto('/health');
	await expect(page.locator('.health-summary-card[aria-busy="true"]')).toHaveCount(4);
	await expect(page.getByRole('figure')).toHaveCount(0);
	const loadingHeights = await page
		.locator('.health-summary-card')
		.evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().height));
	await expect(page.locator('.health-skeleton').first()).toHaveCSS('opacity', '1');
	await page.screenshot({ path: info.outputPath('loading.png') });
	release();
	await expect(page.getByRole('figure')).toHaveCount(4);
	const loadedHeights = await page
		.locator('.health-summary-card')
		.evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().height));
	expect(loadingHeights).toEqual(loadedHeights);
	await info.attach('loading-heights', {
		body: JSON.stringify({ loadingHeights, loadedHeights }),
		contentType: 'application/json'
	});
	gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(page.getByRole('figure')).toHaveCount(0);
	await expect(
		page.getByText('선택한 기간의 기록을 다시 불러오는 중이에요.', { exact: true })
	).toHaveCount(4);
	release();
	await expect(page.getByRole('figure')).toHaveCount(4);
	await page.route('**/api/v1/health/metrics/steps?*', (route) =>
		route.fulfill({
			status: 503,
			json: { error: { code: 'LOOKUP_FAILED', message: '지표 조회 실패', retryable: true } }
		})
	);
	await page.getByRole('button', { name: '다음 주', exact: true }).click();
	await expect(page.getByRole('alert')).toBeVisible();
	await expect(page.getByRole('button', { name: '걸음 수 다시 시도' })).toBeVisible();
	await expect(page.getByRole('figure')).toHaveCount(3);
	await expect(page.locator('.health-summary-card[aria-busy="true"]')).toHaveCount(0);
	await page.route('**/api/v1/health/metrics/steps?*', (route) => {
		const url = new URL(route.request().url());
		const data = activityMetricFixture(
			'steps',
			url.searchParams.get('start')!,
			url.searchParams.get('end')!
		);
		data.days.forEach((day) =>
			Object.assign(day, { value: null, missing: true, reason: 'UNSYNCED' })
		);
		Object.assign(data.summary, {
			avg: null,
			total: null,
			dailyMin: null,
			dailyMax: null,
			daysWithData: 0,
			daysUsedForSummary: 0,
			unsyncedDays: data.days.map((day) => day.date)
		});
		return route.fulfill({ json: { data } });
	});
	await page.getByRole('button', { name: '걸음 수 다시 시도' }).click();
	const empty = page.getByRole('figure', { name: '걸음 수 주간 기록' });
	await expect(empty).toContainText('기록 없음');
	await expect(empty).toContainText('동기화 대기');
	await expect(page.getByRole('alert')).toHaveCount(0);
	await expect(page.locator('.health-summary-card[aria-busy="true"]')).toHaveCount(0);
});

for (const route of ['activity', 'sleep', 'heart']) {
	test(`${route} 최초 로딩과 기간 재조회는 기록 없음과 구분한다`, async ({ page }) => {
		let release = () => {};
		let gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		await page.route('**/api/v1/health/metrics/*?*', async (route) => {
			await gate;
			return route.fallback();
		});
		await page.goto(`/health/${route}`);
		await expect(page.locator('[data-health-loading]')).toHaveAttribute(
			'data-loading-phase',
			'initial'
		);
		release();
		await expect(page.locator('[data-health-loading]')).toHaveCount(0);
		gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		await page.getByRole('button', { name: '이전 주', exact: true }).click();
		await expect(page.locator('[data-health-loading]')).toHaveAttribute(
			'data-loading-phase',
			'refresh'
		);
		release();
		await expect(page.locator('[data-health-loading]')).toHaveCount(0);
		if (route === 'heart') {
			await page.getByRole('button', { name: '다음 주', exact: true }).click();
			const note = page.getByText('오늘 기록은 수집 중이며 기간 평균에서 제외해요.', {
				exact: true
			});
			await expect(note).toBeVisible();
			expect(
				await note.evaluate((element) =>
					element.closest('section')?.getAttribute('aria-labelledby')
				)
			).not.toBe('heart-chart-title');
		}
	});
}

for (const detail of ['day', 'sleep']) {
	test(`${detail} 상세의 지연 응답은 모바일 구조를 유지하고 실제 기록으로 바뀐다`, async ({
		page
	}) => {
		await page.setViewportSize({ width: 360, height: 800 });
		if (detail === 'day') await mockDayDetailApi(page);
		let release = () => {};
		const gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		await page.route(/\/api\/v1\/health\/(metrics|timeline)/, async (route) => {
			await gate;
			return route.fallback();
		});
		await page.goto(`/health/${detail}/2026-10-20`);
		await expect(page.locator('[data-health-loading]')).toHaveAttribute(
			'data-loading-phase',
			'initial'
		);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
		release();
		await expect(page.locator('[data-health-loading]')).toHaveCount(0);
		await expect(
			page.getByLabel(detail === 'day' ? '하루 기록 요약' : '수면 요약', { exact: true })
		).toBeVisible();
	});
}
