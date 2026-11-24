import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../../../lib/testing/e2e-auth';
import { sleepDetailFixture } from '../../../../../lib/testing/sleep-detail-fixture';
import type { SleepSegment } from '../../../../../lib/features/health/sleep-detail';

function detail(kind: 'normal' | 'short' | 'long') {
	const endAt = '2026-10-19T22:00:00Z'; // KST 10/20 07:00
	const parts: [SleepSegment['stage'], number][] =
		kind === 'short'
			? [
					['light', 50],
					['rem', 20],
					['deep', 20],
					['awake', 20]
				]
			: kind === 'long'
				? [
						['awake', 20],
						['light', 600],
						['rem', 300],
						['deep', 300]
					]
				: [
						['awake', 30],
						['light', 280],
						['rem', 90],
						['deep', 80]
					];
	let time = Date.parse(endAt) - parts.reduce((sum, [, minutes]) => sum + minutes, 0) * 60000;
	const startAt = new Date(time).toISOString();
	const sleepSegments = parts.map(([stage, minutes]) => {
		const segment = {
			stage,
			startAt: new Date(time).toISOString(),
			endAt: new Date(time + minutes * 60000).toISOString()
		};
		time += minutes * 60000;
		return segment;
	});
	return sleepDetailFixture({ date: '2026-10-20', mainSleep: { startAt, endAt }, sleepSegments });
}

async function setup(page: Page, kind: 'normal' | 'short' | 'long' = 'normal') {
	await mockSignedIn(page);
	await page.clock.setFixedTime(new Date('2026-11-10T03:00:00Z'));
	const fixture = detail(kind);
	await page.route('**/api/v1/health/metrics/*?*', (route) => {
		const metric = new URL(route.request().url()).pathname.split('/').at(-1);
		if (metric !== 'sleeps' && metric !== 'oxygen_saturations') return route.fallback();
		return route.fulfill({ json: { data: metric === 'sleeps' ? fixture.sleep : fixture.oxygen } });
	});
	await page.route('**/api/v1/health/timeline?*', (route) =>
		route.fulfill({ json: { data: fixture.timeline } })
	);
	return fixture;
}

async function open(page: Page) {
	await page.goto('/health/sleep/2026-10-20');
	await page.getByRole('figure', { name: '시간대별 수면 단계' }).waitFor();
	await page.evaluate(() => document.fonts.ready);
}

async function axisHasNoOverlap(page: Page) {
	await expect
		.poll(() =>
			page.locator('[data-sleep-time-axis]').evaluate((axis) => {
				const labels = Array.from(axis.children, (node) => node.getBoundingClientRect()).sort(
					(a, b) => a.left - b.left
				);
				return labels.every((label, index) => index === 0 || labels[index - 1].right <= label.left);
			})
		)
		.toBe(true);
	expect(
		await page
			.locator('[data-sleep-time-axis]')
			.evaluate((axis) =>
				Array.from(axis.children).every((node) => getComputedStyle(node).fontSize === '12px')
			)
	).toBe(true);
}

for (const kind of ['normal', 'short', 'long'] as const) {
	test(`${kind}: 실제 시간축 라벨은 크기 변경 후에도 양끝을 유지하며 겹치지 않는다`, async ({
		page
	}) => {
		const fixture = await setup(page, kind);
		await open(page);
		for (const width of [320, 360, 390, 639, 640, 768, 1023, 1024, 1366, 1920]) {
			await page.setViewportSize({ width, height: 900 });
			await axisHasNoOverlap(page);
			await expect(page.locator('[data-sleep-time-axis] > span').first()).toHaveText(
				kind === 'short' ? '05:10' : kind === 'long' ? '10:40' : '23:00'
			);
			await expect(page.locator('[data-sleep-time-axis] > span').last()).toHaveText('07:00');
			// 눈금 선별은 각 단계의 원래 시간 비율을 바꾸지 않는다.
			const sleep = fixture.timeline.days[0].mainSleep!;
			for (const segment of fixture.timeline.days[0].sleepSegments) {
				const ratio =
					(Date.parse(segment.endAt) - Date.parse(segment.startAt)) /
					(Date.parse(sleep.endAt) - Date.parse(sleep.startAt));
				const actual = await page
					.locator(`[data-stage="${segment.stage}"]`)
					.evaluate(
						(bar) =>
							bar.getBoundingClientRect().width / bar.parentElement!.getBoundingClientRect().width
					);
				expect(actual).toBeCloseTo(ratio, 3);
			}
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
		}
	});
}

async function pointAt(page: Page, index: number, y: number) {
	const point = page.locator('button[data-chart-point]').nth(index);
	await point.evaluate((node, center) => {
		const rect = node.getBoundingClientRect();
		scrollTo(0, scrollY + rect.top + rect.height / 2 - center);
		node.focus({ preventScroll: true });
	}, y);
	return point;
}

test('Paperlogy 로딩 전후 실제 라벨 경계를 다시 측정한다', async ({ page }) => {
	await page.setViewportSize({ width: 640, height: 900 });
	await setup(page, 'long');
	let release = () => {};
	const fonts = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.route('**/*.woff2', async (route) => {
		await fonts;
		await route.continue();
	});
	try {
		await page.goto('/health/sleep/2026-10-20', { waitUntil: 'domcontentloaded' });
		await page.getByRole('figure', { name: '시간대별 수면 단계' }).waitFor();
		expect(await page.evaluate(() => document.fonts.status)).toBe('loading');
		await axisHasNoOverlap(page);
		release();
		await page.evaluate(() => document.fonts.ready);
		await axisHasNoOverlap(page);
		await expect(page.locator('[data-sleep-time-axis] > span').first()).toHaveText('10:40');
		await expect(page.locator('[data-sleep-time-axis] > span').last()).toHaveText('07:00');
	} finally {
		release();
	}
});

test('유효한 0·null·단계 없음·전체 기록 없음의 의미를 보존한다', async ({ page }) => {
	await page.setViewportSize({ width: 320, height: 800 });
	const fixture = await setup(page);
	const day = {
		date: '2026-10-20',
		mainSleep: { startAt: '2026-10-19T21:40:00Z', endAt: '2026-10-19T22:00:00Z' },
		sleepSegments: []
	};
	for (const [value, label] of [
		[0, '0시간 0분'],
		[null, '값 없음']
	] as const) {
		Object.assign(fixture, sleepDetailFixture(day, value));
		await page.goto('/health/sleep/2026-10-20');
		await expect(
			page.getByRole('region', { name: '수면 요약', exact: true }).getByRole('definition').first()
		).toHaveText(label);
		await expect(page.getByText('해당 날짜의 수면 단계 기록이 없어요.')).toBeVisible();
		await expect(page.getByText(/전체 수면의/)).toHaveCount(0);
	}
	Object.assign(fixture, sleepDetailFixture({ ...day, mainSleep: null }));
	await page.goto('/health/sleep/2026-10-20');
	await expect(page.getByText('해당 날짜의 수면 기록이 없어요.')).toBeVisible();
	await expect(page.getByRole('region', { name: '수면 요약', exact: true })).toHaveCount(0);
});

async function safeTooltip(page: Page) {
	await expect
		.poll(() =>
			page.getByRole('tooltip').evaluate((panel) => {
				const box = panel.getBoundingClientRect();
				const header = document.querySelector('header')!.getBoundingClientRect();
				const nav = document.querySelector('nav[aria-label="주 메뉴"]')!;
				const bottom =
					getComputedStyle(nav).position === 'fixed'
						? nav.getBoundingClientRect().top
						: innerHeight;
				return box.top >= header.bottom && box.bottom <= bottom;
			})
		)
		.toBe(true);
}

for (const width of [320, 390]) {
	test(`${width}×360: 툴팁이 로고·하단 메뉴를 가리지 않고 실제 클릭이 가능하다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 360 });
		await setup(page);
		await open(page);
		const point = await pointAt(page, 0, 100);
		await safeTooltip(page);
		await page.keyboard.press('Escape');
		await expect(point).toBeFocused();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await point.blur();
		await pointAt(page, 0, 330);
		await safeTooltip(page);
		const menu = page
			.getByRole('navigation', { name: '주 메뉴' })
			.getByRole('link', { name: '건강 데이터', exact: true });
		const menuBox = await menu.boundingBox();
		if (!menuBox) throw new Error('메뉴 위치 없음');
		await page.mouse.click(menuBox.x + menuBox.width / 2, menuBox.y + menuBox.height / 2);
		await expect(page).toHaveURL('/health');
		await open(page);
		await pointAt(page, 0, 100);
		await safeTooltip(page);
		const logo = await page.getByRole('link', { name: 'CANDiY-FiT 홈' }).boundingBox();
		if (!logo) throw new Error('로고 위치 없음');
		await page.mouse.click(logo.x + logo.width / 2, logo.y + logo.height / 2);
		await expect(page).toHaveURL('/home');
	});
}

test('좁은 안전 영역에서 실제 Tab 진입·내부 스크롤·Escape 포커스 복귀를 유지한다', async ({
	page
}) => {
	await page.setViewportSize({ width: 390, height: 180 });
	await setup(page);
	await open(page);
	const point = await pointAt(page, 3, 83);
	await safeTooltip(page);
	await page.keyboard.press('Tab');
	const tip = page.getByRole('tooltip');
	await expect(tip).toBeFocused();
	expect(await tip.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true);
	await page.keyboard.press('End');
	await expect.poll(() => tip.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
	const scroll = await page.evaluate(() => scrollY);
	await page.keyboard.press('Escape');
	await expect(tip).toHaveCount(0);
	await expect(point).toBeFocused();
	expect(await page.evaluate(() => scrollY)).toBe(scroll);
	await page.keyboard.press('Shift+Tab');
	await expect(page.getByRole('tooltip')).toBeVisible();
	await page.keyboard.press('Tab');
	await expect(point).toBeFocused();
	await expect(page.getByRole('tooltip')).toBeVisible();
});

test('안전 영역이 없는 높이에서는 툴팁이 고정 UI를 가로막지 않는다', async ({ page }) => {
	await page.setViewportSize({ width: 320, height: 140 });
	await setup(page);
	await open(page);
	await pointAt(page, 0, 72);
	await expect(page.getByRole('tooltip', { includeHidden: true })).toBeHidden();
	expect(
		await page.getByRole('link', { name: 'CANDiY-FiT 홈' }).evaluate((logo) => {
			const rect = logo.getBoundingClientRect();
			return logo.contains(
				document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
			);
		})
	).toBe(true);
	await page.setViewportSize({ width: 320, height: 360 });
	await safeTooltip(page);
});

test('작은 빨간 글자 4곳의 실제 색상과 합성 배경 대비가 4.5 이상이다', async ({ page }) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	await setup(page);
	await open(page);
	const targets = [
		page.getByRole('link', { name: '하루 상세 보기', exact: true }),
		page
			.getByRole('navigation', { name: '건강 데이터', exact: true })
			.getByRole('link', { name: '수면', exact: true })
	];
	const back = page.getByRole('link', { name: '돌아가기', exact: true });
	await back.hover();
	targets.push(back);
	const selector = page.getByLabel('날짜 선택', { exact: true });
	targets.push(page.getByRole('option', { selected: true }));
	for (const target of targets) {
		if (target === back) await back.hover();
		if (target === targets[3]) await selector.click();
		const contrast = await target.evaluate((node) => {
			const rgb = (value: string) => value.match(/[\d.]+/g)!.map(Number);
			const color = rgb(getComputedStyle(node).color);
			let bg = [255, 255, 255];
			const ancestors: Element[] = [];
			for (let current: Element | null = node; current; current = current.parentElement)
				ancestors.unshift(current);
			for (const current of ancestors) {
				const layer = rgb(getComputedStyle(current).backgroundColor);
				const alpha = layer[3] ?? 1;
				bg = bg.map((channel, index) => layer[index] * alpha + channel * (1 - alpha));
			}
			const luminance = (channels: number[]) =>
				channels
					.slice(0, 3)
					.map((v) => v / 255)
					.map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
					.reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
			const a = luminance(color),
				b = luminance(bg);
			return {
				color: getComputedStyle(node).color,
				ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
			};
		});
		expect(contrast.color).toBe('rgb(204, 56, 62)');
		expect(contrast.ratio).toBeGreaterThanOrEqual(4.5);
	}
	await page.keyboard.press('Escape');
	await expect(selector).toBeFocused();
});
