import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import type { HeartMetricResult } from '../../../../lib/features/health/heart';
import {
	normalHeartSamples,
	responsiveHeartFixture
} from '../../../../lib/testing/heart-responsive-fixture';

async function open(page: Page, samples = normalHeartSamples) {
	await mockSignedIn(page);
	await page.clock.setFixedTime(new Date('2026-11-10T03:00:00Z'));
	await page.route('**/api/v1/health/metrics/heart_rates?*', (route) => {
		const end = new URL(route.request().url()).searchParams.get('end')!;
		return route.fulfill({ json: { data: responsiveHeartFixture(end, samples) } });
	});
	await page.goto('/health/heart');
	await expect(page.getByRole('region', { name: '일별 심박수', exact: true })).toBeVisible();
	await page.evaluate(() => document.fonts.ready);
}

async function labelGeometry(page: Page) {
	return page.evaluate(() => {
		const plotNode = document.querySelectorAll('.heart-plot')[1];
		const plot = plotNode.getBoundingClientRect();
		const scale = plot.height / plotNode.clientHeight;
		const card = document.querySelector('.heart-card')!.getBoundingClientRect();
		const legend = document.querySelector('.heart-legend')!.getBoundingClientRect();
		const dates = document.querySelector('.heart-dates')!.getBoundingClientRect();
		// plot 경계 대신 실제 범례와 날짜 영역 사이의 표시 공간을 검사한다.
		const available = {
			top: Math.max(card.top, legend.bottom),
			bottom: Math.min(card.bottom, dates.top)
		};
		const surrounding = [
			legend,
			...[...document.querySelectorAll('[data-chart-navigation]')].map((node) =>
				node.getBoundingClientRect()
			)
		];
		const nodes = [...document.querySelectorAll('[data-heart-label]')];
		const isVisible = (node: Element) =>
			getComputedStyle(node).visibility === 'visible' && node.getBoundingClientRect().width > 0;
		const visible = nodes.filter(isVisible);
		const labels = visible.map((node) => node.getBoundingClientRect());
		const marks = [...document.querySelectorAll('[data-heart-point], [data-heart-range]')].map(
			(node) => {
				const box = node.getBoundingClientRect();
				const center = box.left + box.width / 2;
				return node.hasAttribute('data-heart-range')
					? {
							left: center - 9 * scale,
							right: center + 9 * scale,
							top: box.top - scale,
							bottom: box.bottom + scale
						}
					: box;
			}
		);
		const overlaps = (a: Pick<DOMRect, 'left' | 'right' | 'top' | 'bottom'>, b: typeof a) =>
			a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
		const point = (node: Element) => node.parentElement!.getBoundingClientRect();
		const placementErrors = nodes.filter((node, index) => {
			if (innerWidth < 640) return isVisible(node);
			const dot = point(node);
			const x = dot.left + dot.width / 2;
			const y = dot.top + dot.height / 2;
			const width = parseFloat(getComputedStyle(node).width) * scale;
			const height = parseFloat(getComputedStyle(node).height) * scale;
			const occupied = [
				...marks,
				...nodes
					.slice(0, index)
					.filter(isVisible)
					.map((label) => label.getBoundingClientRect())
			];
			// 실제 글자 크기·화면 좌표로 두 후보를 검사한다. 제품의 배치 함수는 호출하지 않는다.
			const candidates = [x + 12 * scale, x - 12 * scale - width].map((left) => ({
				left,
				right: left + width,
				top: y - height / 2,
				bottom: y + height / 2
			}));
			const expected = candidates.find(
				(box) =>
					box.left >= plot.left + scale &&
					box.right <= plot.right - scale &&
					box.top >= available.top &&
					box.bottom <= available.bottom &&
					!surrounding.some((other) => overlaps(box, other)) &&
					!occupied.some((other) =>
						overlaps(box, {
							left: other.left - scale,
							right: other.right + scale,
							top: other.top - scale,
							bottom: other.bottom + scale
						})
					)
			);
			if (!expected) return isVisible(node);
			const actual = node.getBoundingClientRect();
			return (
				!isVisible(node) ||
				Math.abs(actual.left - expected.left) > 0.1 * scale ||
				Math.abs(actual.top - expected.top) > 0.1 * scale
			);
		}).length;
		return {
			visibleCount: visible.length,
			rightCount: visible.filter((node) => node.getBoundingClientRect().left > point(node).right)
				.length,
			pointGaps: visible.map((node) => {
				const box = node.getBoundingClientRect();
				const dot = point(node);
				return (box.left > dot.right ? box.left - dot.right : dot.left - box.right) / scale;
			}),
			centerOffsets: visible.map((node) => {
				const box = node.getBoundingClientRect();
				const dot = point(node);
				return Math.abs(box.top + box.height / 2 - dot.top - dot.height / 2) / scale;
			}),
			placementErrors,
			labelFonts: nodes.map((node) => getComputedStyle(node).fontSize),
			associationErrors: nodes.filter((node) => {
				const date = node.getAttribute('data-heart-label');
				const navigation = document.querySelector(`a[aria-label="${date} 하루 상세"]`);
				if (date !== node.parentElement?.getAttribute('data-heart-point') || !navigation)
					return true;
				const dot = point(node);
				const dateBox = navigation.getBoundingClientRect();
				return Math.abs(dot.left + dot.width / 2 - dateBox.left - dateBox.width / 2) > 0.1 * scale;
			}).length,
			outside: labels.filter(
				(box) =>
					box.left < plot.left ||
					box.right > plot.right ||
					box.top < available.top ||
					box.bottom > available.bottom
			).length,
			collisions: labels.flatMap((box, i) =>
				[...labels.slice(i + 1), ...marks, ...surrounding].filter((other) => overlaps(box, other))
			).length,
			font: [...document.querySelectorAll('.heart-axis-label, [data-chart-navigation]')].map(
				(node) => parseFloat(getComputedStyle(node).fontSize)
			),
			plot: plotNode.clientHeight,
			overflow: document.documentElement.scrollWidth > innerWidth
		};
	});
}

async function expectFocusedControlVisible(control: Locator) {
	await expect(control).toBeFocused();
	await expect
		.poll(() =>
			control.evaluate((node) => {
				const header = document.querySelector('header');
				const navigation = document.querySelector('nav[aria-label="주 메뉴"]');
				const top =
					header && ['fixed', 'sticky'].includes(getComputedStyle(header).position)
						? header.getBoundingClientRect().bottom
						: 0;
				const bottom =
					navigation && getComputedStyle(navigation).position === 'fixed'
						? navigation.getBoundingClientRect().top
						: innerHeight;
				const bounds = node.getBoundingClientRect();
				const index = (node as HTMLElement).dataset.chartPoint;
				const marker =
					index === undefined
						? null
						: node.closest('figure')?.querySelector(`[data-chart-focus-target="${index}"]`);
				const target =
					bounds.height > bottom - top ? (marker?.getBoundingClientRect() ?? bounds) : bounds;
				return (
					target.top >= top &&
					target.bottom <= bottom &&
					target.left >= 0 &&
					target.right <= innerWidth &&
					bounds.top < bottom &&
					bounds.bottom > top
				);
			})
		)
		.toBe(true);
}

for (const width of [320, 360, 390, 639, 640, 768, 1023, 1024, 1366, 1920]) {
	test(`${width}px에서 라벨·점·범위 비겹침과 날짜·높이를 유지한다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await open(
			page,
			normalHeartSamples.map(() => ({ avg: 100.1, min: 40, max: 160, hours: 12 }))
		);
		await expect(page.locator('[data-heart-label]')).toHaveCount(7);
		await expect(page.locator('[data-heart-label]')).toHaveText(Array(7).fill('100.1'));
		await expect(page.locator('[data-heart-label]:visible')).toHaveCount(
			width < 640 ? 0 : width <= 768 ? 6 : 7
		);
		await expect(page.locator('[data-period-average]')).toHaveText('100.1 bpm');
		await expect(page.locator('[data-heart-point]')).toHaveCount(7);
		await expect(page.locator('[data-heart-range]')).toHaveCount(7);
		await expect(page.locator('[data-heart-point]').first()).toBeVisible();
		await expect(page.locator('[data-heart-range]').first()).toBeVisible();
		await expect.poll(async () => (await labelGeometry(page)).collisions).toBe(0);
		await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
		const geometry = await labelGeometry(page);
		expect(geometry.pointGaps.every((gap) => Math.abs(gap - 5) < 0.1)).toBe(true);
		expect(geometry.centerOffsets.every((offset) => offset < 0.1)).toBe(true);
		expect(geometry.rightCount).toBe(geometry.visibleCount);
		expect(geometry.associationErrors).toBe(0);
		await expect(page.locator('[data-heart-label-connector], .heart-plot svg line')).toHaveCount(0);
		expect(geometry.outside).toBe(0);
		expect(geometry.overflow).toBe(false);
		expect(geometry.font.every((size) => size >= 12 && size <= 14)).toBe(true);
		if (width < 1024) expect(geometry.plot).toBe(width < 640 ? 192 : 240);
		await expect(page.locator('[data-chart-navigation]')).toHaveCount(7);
		for (const point of await page.locator('[data-heart-point]').all()) {
			const box = (await point.boundingBox())!;
			expect(box.width).toBe(14);
			expect(box.height).toBe(14);
		}
	});
}

for (const scenario of [
	{ name: '최상단 160 × 7일', width: 1366, value: 160, single: false, count: 7 },
	{ name: '최하단 40 × 7일', width: 1920, value: 40, single: false, count: 7 },
	{ name: '평균만 160인 하루', width: 1366, value: 160, single: true, count: 1 }
]) {
	test(`${scenario.name}: plot 밖의 여유 공간에 숫자를 표시하고 범례·날짜를 피한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width: scenario.width, height: 900 });
		await mockSignedIn(page);
		await page.clock.setFixedTime(new Date('2026-11-10T03:00:00Z'));
		const samples = Array.from({ length: 7 }, (_, index) =>
			scenario.single && index > 0
				? null
				: { avg: scenario.value, min: scenario.value, max: scenario.value, hours: 12 }
		);
		await page.route('**/api/v1/health/metrics/heart_rates?*', (route) => {
			const end = new URL(route.request().url()).searchParams.get('end')!;
			const fixture: HeartMetricResult = responsiveHeartFixture(end, samples);
			if (scenario.single) {
				fixture.detail.dailyRanges[0].min = null;
				fixture.detail.dailyRanges[0].max = null;
			}
			return route.fulfill({ json: { data: fixture } });
		});
		await page.goto('/health/heart');
		await page.evaluate(() => document.fonts.ready);
		await expect(page.locator('[data-heart-label]:visible')).toHaveCount(scenario.count);
		await expect(page.locator('[data-heart-label]')).toHaveText(
			Array(scenario.count).fill(String(scenario.value))
		);
		await expect(page.locator('[data-period-average]')).toHaveText(`${scenario.value} bpm`);
		await expect(page.locator('[data-heart-point]')).toHaveCount(scenario.count);
		await expect(page.locator('[data-heart-range]')).toHaveCount(scenario.single ? 0 : 7);
		await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
		const geometry = await labelGeometry(page);
		expect(geometry.rightCount).toBe(scenario.count);
		expect(geometry.collisions).toBe(0);
		expect(geometry.outside).toBe(0);
		expect(geometry.overflow).toBe(false);
		expect(geometry.centerOffsets.every((offset) => offset < 0.1)).toBe(true);
		expect(geometry.pointGaps.every((gap) => Math.abs(gap - 5) < 0.1)).toBe(true);
		const coordinates = () =>
			page.locator('[data-heart-point], [data-heart-range]').evaluateAll((nodes) =>
				nodes.map((node) => ({
					left: (node as HTMLElement).style.left,
					top: (node as HTMLElement).style.top,
					height: (node as HTMLElement).style.height
				}))
			);
		const before = await coordinates();
		const points = page.locator('[data-heart-point]');
		for (const index of scenario.single ? [0] : [0, 6]) {
			const style = await points.nth(index).evaluate((node) => ({
				left: (node as HTMLElement).style.left,
				top: (node as HTMLElement).style.top
			}));
			expect(Number.parseFloat(style.left)).toBeCloseTo(((index + 0.5) / 7) * 100);
			expect(style.top).toBe(scenario.value === 160 ? '0%' : '100%');
			await page.locator('button[data-chart-point]').nth(index).focus();
			await expect(page.getByRole('tooltip').locator('dd')).toHaveText([
				`${scenario.value} bpm`,
				scenario.single ? '—' : `${scenario.value} bpm`,
				scenario.single ? '—' : `${scenario.value} bpm`
			]);
			await page.keyboard.press('Escape');
			await expect(page.getByRole('tooltip')).toHaveCount(0);
		}
		const first = page.locator('button[data-chart-point]').first();
		await page.locator('[data-chart-navigation]').first().focus();
		await first.focus();
		await expect(page.getByRole('tooltip')).toBeVisible();
		await page.keyboard.press('Tab');
		await expect(
			scenario.single
				? page.locator('[data-chart-navigation]').first()
				: page.locator('button[data-chart-point]').nth(1)
		).toBeFocused();
		await page.keyboard.press('Escape');
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await expect(page.locator('[data-chart-navigation]').first()).toHaveAttribute(
			'href',
			'/health/day/2026-11-04'
		);
		const displayedPeriod = page
			.getByRole('group', { name: '주간 기간 선택' })
			.getByRole('button', { name: /^날짜 선택,/ });
		const dates = page.locator('.heart-card [data-chart-navigation]');
		await page.getByRole('button', { name: '이전 주', exact: true }).click();
		await expect(displayedPeriod).toHaveText('2026.10.28 ~ 11.03');
		await expect(dates.first()).toHaveAttribute('href', '/health/day/2026-10-28');
		await expect(dates.last()).toHaveAttribute('href', '/health/day/2026-11-03');
		await expect(page.locator('[data-heart-label]:visible')).toHaveCount(scenario.count);
		await page.getByRole('button', { name: '다음 주', exact: true }).click();
		await expect(displayedPeriod).toHaveText('2026.11.04 ~ 11.10');
		await expect(dates.first()).toHaveAttribute('href', '/health/day/2026-11-04');
		await expect(dates.last()).toHaveAttribute('href', '/health/day/2026-11-10');
		await expect(page.locator('[data-heart-label]:visible')).toHaveCount(scenario.count);
		expect(await coordinates()).toEqual(before);
	});
}

test('CSS 200%에서도 상하 경계의 숫자 7개를 범례·날짜와 겹치지 않게 표시한다', async ({ page }) => {
	await page.setViewportSize({ width: 1920, height: 900 });
	for (const value of [160, 40]) {
		await open(
			page,
			normalHeartSamples.map(() => ({ avg: value, min: value, max: value, hours: 12 }))
		);
		await page.evaluate(() => {
			document.documentElement.style.zoom = '2';
		});
		await expect(page.locator('[data-heart-label]:visible')).toHaveCount(7);
		await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
		const geometry = await labelGeometry(page);
		expect(geometry.rightCount).toBe(7);
		expect(geometry.collisions).toBe(0);
		expect(geometry.outside).toBe(0);
		expect(geometry.centerOffsets.every((offset) => offset < 0.1)).toBe(true);
		expect(geometry.pointGaps.every((gap) => Math.abs(gap - 5) < 0.1)).toBe(true);
		await expect(page.locator('[data-heart-label]')).toHaveText(Array(7).fill(String(value)));
		await page.locator('button[data-chart-point]').first().focus();
		await expect(page.getByRole('tooltip').locator('dd')).toHaveText(Array(3).fill(`${value} bpm`));
		await page.keyboard.press('Escape');
		await expect(page.getByRole('tooltip')).toHaveCount(0);
	}
});

test('CSS 200% 확대에서도 분기점 전후 숫자 노출과 점·범위·툴팁·날짜 링크를 유지한다', async ({
	page
}) => {
	await page.setViewportSize({ width: 360, height: 900 });
	await open(
		page,
		normalHeartSamples.map(() => ({ avg: 100.1, min: 40, max: 160, hours: 12 }))
	);
	await page.evaluate(() => {
		document.documentElement.style.zoom = '2';
	});
	for (const width of [360, 390, 639, 640, 768, 1366, 1920]) {
		await page.setViewportSize({ width, height: 900 });
		await page.evaluate(
			() =>
				new Promise<void>((resolve) =>
					requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
				)
		);
		// 기존 확대 화면의 배치를 기준으로 숫자 비표시가 좌표·넘침을 바꾸지 않는지 비교한다.
		const originalVisibility = await page.addStyleTag({
			content: '[data-heart-label] { visibility: visible !important; }'
		});
		const before = await labelGeometry(page);
		const coordinates = () =>
			page
				.locator('.heart-card')
				.evaluate((card) =>
					[
						...card.querySelectorAll(
							'[data-heart-point], [data-heart-range], .heart-axis-label, [data-chart-navigation], [data-period-average]'
						)
					].map((node) => node.getBoundingClientRect().toJSON())
				);
		const beforeCoordinates = await coordinates();
		await originalVisibility.evaluate((node) => node.parentNode?.removeChild(node));
		await expect(page.locator('[data-heart-label]:visible')).toHaveCount(
			width <= 768 ? 0 : width === 1366 ? 6 : 7
		);
		await expect(page.locator('[data-heart-label]')).toHaveText(Array(7).fill('100.1'));
		await expect(page.locator('[data-period-average]')).toBeVisible();
		await expect(page.locator('[data-period-average]')).toHaveText('100.1 bpm');
		await expect(page.locator('[data-heart-point]')).toHaveCount(7);
		await expect(page.locator('[data-heart-range]')).toHaveCount(7);
		await expect(page.locator('[data-heart-point]').first()).toBeVisible();
		await expect(page.locator('[data-heart-range]').first()).toBeVisible();
		await expect.poll(async () => (await labelGeometry(page)).collisions).toBe(0);
		await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
		const geometry = await labelGeometry(page);
		expect(geometry.outside, `${width}px`).toBe(0);
		expect(geometry.centerOffsets.every((offset) => offset < 0.1)).toBe(true);
		expect(
			{ plot: geometry.plot, font: geometry.font, overflow: geometry.overflow },
			`${width}px`
		).toEqual({
			plot: before.plot,
			font: before.font,
			overflow: before.overflow
		});
		expect(await coordinates(), `${width}px`).toEqual(beforeCoordinates);
		for (const index of [0, 6]) {
			const date = `2026-11-${String(index + 4).padStart(2, '0')}`;
			await expect(page.locator(`[data-chart-navigation="${index}"]`)).toHaveAttribute(
				'href',
				`/health/day/${date}`
			);
		}
		await page.locator('[data-chart-navigation]').first().focus();
		await page.locator('button[data-chart-point]').first().focus();
		await expect(page.getByRole('tooltip').locator('dd')).toHaveText([
			'100.1 bpm',
			'40 bpm',
			'160 bpm'
		]);
		await page.keyboard.press('Escape');
		await expect(page.getByRole('tooltip')).toHaveCount(0);
	}
});

for (const width of [320, 360, 390, 639, 640, 768, 1024, 1366]) {
	test(`${width}px: 비슷하거나 동일한 평균은 점 옆에 정렬하고 공간이 없으면 툴팁을 유지한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		for (const values of [
			[72.8, 70.2, 72.7, 70.9, 73.2, 64.2, 71.5],
			Array<number>(7).fill(72.8)
		]) {
			const samples = normalHeartSamples.map(
				(sample, i) => sample && { ...sample, avg: values[i] }
			);
			await open(page, samples);
			await expect(page.locator('[data-heart-label]')).toHaveText(values.map(String));
			await expect(page.locator('[data-heart-label]:visible')).toHaveCount(
				width < 640 ? 0 : width === 640 ? 6 : 7
			);
			await expect.poll(async () => (await labelGeometry(page)).collisions).toBe(0);
			await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
			const geometry = await labelGeometry(page);
			expect(geometry.pointGaps.every((gap) => Math.abs(gap - 5) < 0.1)).toBe(true);
			expect(geometry.centerOffsets.every((offset) => offset < 0.1)).toBe(true);
			expect(geometry.associationErrors).toBe(0);
			expect(geometry.outside).toBe(0);
			expect(geometry.labelFonts).toEqual(Array(7).fill('12px'));
		}
	});
}

for (const width of [640, 1366]) {
	test(`${width}px: 긴 소수와 크게 다른 평균도 원본 좌표·툴팁을 유지하고 가능한 위치만 표시한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		for (const values of [
			Array<number>(7).fill(72.12345678901234),
			[40, 160, 45, 155, 60, 145, 75],
			[72.8, 72.8, 72.8, 72.8, 72.8, 120, 72.8]
		]) {
			await open(
				page,
				values.map((avg) => ({ avg, min: 40, max: 160, hours: 12 }))
			);
			await expect(page.locator('[data-heart-label]')).toHaveText(values.map(String));
			await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
			const geometry = await labelGeometry(page);
			expect(geometry.collisions).toBe(0);
			expect(geometry.outside).toBe(0);
			expect(geometry.overflow).toBe(false);
			expect(geometry.associationErrors).toBe(0);
			expect(geometry.centerOffsets.every((offset) => offset < 0.1)).toBe(true);
			if (values[0] === 72.12345678901234)
				expect(geometry.visibleCount).toBe(width === 640 ? 0 : 6);
			if (values[5] === 120) {
				expect(geometry.visibleCount).toBe(7);
				expect(geometry.rightCount).toBe(width === 640 ? 6 : 7);
			}
			for (const index of [0, 6]) {
				const dot = page.locator('[data-heart-point]').nth(index);
				const original = await dot.evaluate((node) => ({
					left: (node as HTMLElement).style.left,
					top: (node as HTMLElement).style.top
				}));
				expect(Number.parseFloat(original.left)).toBeCloseTo(((index + 0.5) / 7) * 100);
				expect(Number.parseFloat(original.top)).toBeCloseTo(((160 - values[index]) / 120) * 100);
				await page.locator('button[data-chart-point]').nth(index).focus();
				await expect(page.getByRole('tooltip').locator('dd')).toHaveText([
					`${values[index]} bpm`,
					'40 bpm',
					'160 bpm'
				]);
				await page.keyboard.press('Escape');
				await expect(page.getByRole('tooltip')).toHaveCount(0);
			}
		}
	});
}

test('결측·평균만 존재·범위만 존재하는 날짜의 표시와 접근 가능한 원본 수치를 유지한다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	await mockSignedIn(page);
	await page.clock.setFixedTime(new Date('2026-11-10T03:00:00Z'));
	const fixture: HeartMetricResult = responsiveHeartFixture('2026-11-10');
	fixture.detail.dailyRanges[0].min = null;
	fixture.detail.dailyRanges[0].max = null;
	fixture.days[1].value = null;
	fixture.days[2].value = null;
	fixture.days[2].missing = true;
	fixture.days[2].reason = 'NULL_VALUE';
	fixture.detail.dailyRanges[2].min = null;
	fixture.detail.dailyRanges[2].max = null;
	await page.route('**/api/v1/health/metrics/heart_rates?*', (route) =>
		route.fulfill({ json: { data: fixture } })
	);
	await page.goto('/health/heart');
	await page.evaluate(() => document.fonts.ready);
	await expect(page.locator('[data-heart-label]')).toHaveCount(5);
	await expect(page.locator('[data-heart-point]')).toHaveCount(5);
	await expect(page.locator('[data-heart-range]')).toHaveCount(5);
	await expect(page.locator('[data-period-average]')).toHaveText(`${fixture.summary.avg} bpm`);
	await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
	expect((await labelGeometry(page)).collisions).toBe(0);
	expect((await labelGeometry(page)).outside).toBe(0);
	await page.locator('button[data-chart-point="0"]').focus();
	await expect(page.getByRole('tooltip').locator('dd')).toHaveText(['68.2 bpm', '—', '—']);
	await page.locator('button[data-chart-point="1"]').focus();
	await expect(page.getByRole('tooltip').locator('dd')).toHaveText(['—', '54 bpm', '105 bpm']);
	await expect(
		page.getByRole('link', { name: '2026-11-04 하루 상세' })
	).toHaveAccessibleDescription('평균 68.2 bpm. 최저 기록 없음. 최고 기록 없음.');
	await expect(page.getByRole('link', { name: '2026-11-05 하루 상세' })).toHaveAttribute(
		'href',
		'/health/day/2026-11-05'
	);
});

test('글꼴·리사이즈·기간 변경과 큰 범위·0·결측 후 라벨을 다시 배치한다', async ({ page }) => {
	await page.setViewportSize({ width: 320, height: 900 });
	await open(page);
	for (const width of [390, 640, 639, 1024, 320, 640]) {
		await page.setViewportSize({ width, height: 900 });
		await expect.poll(async () => (await labelGeometry(page)).collisions).toBe(0);
		await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
		expect((await labelGeometry(page)).associationErrors).toBe(0);
	}
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(
		page.getByRole('group', { name: '주간 기간 선택' }).getByRole('button', { name: /^날짜 선택,/ })
	).toHaveText('2026.10.28 ~ 11.03');
	const dates = page.locator('.heart-card [data-chart-navigation]');
	await expect(dates.first()).toHaveAttribute('href', '/health/day/2026-10-28');
	await expect(dates.last()).toHaveAttribute('href', '/health/day/2026-11-03');
	await expect.poll(async () => (await labelGeometry(page)).collisions).toBe(0);
	await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
	const font = await page.addStyleTag({ content: '[data-heart-label]{font-family:monospace}' });
	await expect.poll(async () => (await labelGeometry(page)).collisions).toBe(0);
	await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
	await font.evaluate((node) => node.parentNode?.removeChild(node));
	await expect.poll(async () => (await labelGeometry(page)).collisions).toBe(0);
	await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
});

test('스크롤 상세는 실제 Tab으로 진입하고 Escape 후 같은 점에 복귀하며 다시 열리지 않는다', async ({
	page
}) => {
	await page.setViewportSize({ width: 740, height: 240 });
	await open(page);
	for (const index of [0, 3, 6, 6]) {
		const point = page.locator('button[data-chart-point]').nth(index);
		if (await point.evaluate((node) => node === document.activeElement)) {
			await page.keyboard.press('Tab');
			await page.keyboard.press('Shift+Tab');
		}
		await point.evaluate((node) => {
			const box = node.getBoundingClientRect();
			scrollTo(0, scrollY + box.top - 90);
			node.focus({ preventScroll: true });
		});
		const tooltip = page.getByRole('tooltip');
		await expect(tooltip).toHaveAttribute('data-scrollable', 'true');
		await page.keyboard.press('Tab');
		await expect(tooltip).toBeFocused();
		await expect(tooltip.locator('dd')).toHaveCount(3);
		await expect(tooltip).toContainText(`2026.11.${String(4 + index).padStart(2, '0')}`);
		const scrollY = await page.evaluate(() => window.scrollY);
		await page.keyboard.press('End');
		await expect.poll(() => tooltip.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
		expect(await page.evaluate(() => window.scrollY)).toBe(scrollY);
		await tooltip.hover();
		await page.mouse.wheel(0, -180);
		await expect.poll(() => tooltip.evaluate((node) => node.scrollTop)).toBe(0);
		await expect(tooltip).toBeFocused();
		expect(await page.evaluate(() => window.scrollY)).toBe(scrollY);
		await page.keyboard.press('Shift+Tab');
		await expect(point).toBeFocused();
		await expectFocusedControlVisible(point);
		await page.keyboard.press('Tab');
		await expect(tooltip).toBeFocused();
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
		await page.keyboard.press('Tab');
		await expect(point).not.toBeFocused();
		await page.keyboard.press('Shift+Tab');
		await expect(point).toBeFocused();
		await expect(tooltip).toBeVisible();
		await page.keyboard.press('Tab');
		await expect(tooltip).toBeFocused();
		await page.keyboard.press('Tab');
		await expect(
			index < 6
				? page.locator('button[data-chart-point]').nth(index + 1)
				: page.locator('[data-chart-navigation]').first()
		).toBeFocused();
		await expectFocusedControlVisible(
			index < 6
				? page.locator('button[data-chart-point]').nth(index + 1)
				: page.locator('[data-chart-navigation]').first()
		);
		await page.keyboard.press('Escape');
	}
});

test('실제 Paperlogy 로딩 전후에도 측정한 라벨은 겹치지 않는다', async ({ page }) => {
	await page.setViewportSize({ width: 640, height: 900 });
	let release!: () => void;
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.route('**/*Paperlogy*.woff2', async (route) => {
		await gate;
		await route.continue();
	});
	const loading = open(
		page,
		normalHeartSamples.map(() => ({ avg: 100.1, min: 40, max: 160, hours: 12 }))
	);
	try {
		await expect(page.locator('[data-heart-label]')).toHaveCount(7);
		await expect.poll(async () => (await labelGeometry(page)).collisions).toBe(0);
		await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
	} finally {
		release();
	}
	await loading;
	await expect.poll(async () => (await labelGeometry(page)).collisions).toBe(0);
	await expect.poll(async () => (await labelGeometry(page)).placementErrors).toBe(0);
	expect((await labelGeometry(page)).outside).toBe(0);
});

test('날짜 기본·hover·focus의 실제 글자 대비가 4.5 이상이다', async ({ page }) => {
	await open(page);
	const date = page.locator('[data-chart-navigation]').last();
	for (const state of ['default', 'hover', 'focus']) {
		if (state === 'hover') await date.hover();
		if (state === 'focus') {
			await page.mouse.move(0, 0);
			await date.focus();
		}
		const ratio = await date.evaluate((node) => {
			const luminance = (color: string) =>
				color
					.match(/[\d.]+/g)!
					.slice(0, 3)
					.map(Number)
					.map((value) => value / 255)
					.map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
					.reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
			const fg = luminance(getComputedStyle(node).color),
				bg = luminance(getComputedStyle(node.closest('section')!).backgroundColor);
			return (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
		});
		expect(ratio, state).toBeGreaterThanOrEqual(4.5);
	}
});

test('넓은 범위·긴 평균·부분 결측·단일 기록·전체 결측·유효한 0을 보존한다', async ({ page }) => {
	await page.setViewportSize({ width: 320, height: 900 });
	await open(page);
	for (const samples of [
		normalHeartSamples.map((_, i) => ({ avg: (1101 + i) / 10, min: 35, max: 250, hours: 12 })),
		normalHeartSamples.map((sample, i) => (i % 2 ? null : sample)),
		normalHeartSamples.map((_, i) => (i === 2 ? { avg: 0, min: 0, max: 0, hours: 1 } : null)),
		normalHeartSamples.map(() => null)
	]) {
		await page.route('**/api/v1/health/metrics/heart_rates?*', (route) =>
			route.fulfill({ json: { data: responsiveHeartFixture('2026-11-10', samples) } })
		);
		await page.reload();
		await expect(page.getByRole('region', { name: '일별 심박수', exact: true })).toBeVisible();
		await expect(page.locator('[data-heart-label]')).toHaveCount(samples.filter(Boolean).length);
		await expect.poll(async () => (await labelGeometry(page)).collisions).toBe(0);
		expect((await labelGeometry(page)).outside).toBe(0);
		expect((await labelGeometry(page)).associationErrors).toBe(0);
		if (samples[2]?.avg === 0) {
			await page.locator('button[data-chart-point]').focus();
			await expect(page.getByRole('tooltip').locator('dd')).toHaveText(['0 bpm', '0 bpm', '0 bpm']);
		}
		if (!samples.some(Boolean)) await expect(page.locator('.heart-empty')).toBeVisible();
	}
});

test('심박 로딩 plot과 완료 plot의 높이·위치가 분기점 양쪽에서 일치한다', async ({ page }) => {
	await open(page);
	for (const width of [320, 390, 639, 640, 768, 1023, 1024, 1366]) {
		await page.setViewportSize({ width, height: 900 });
		let release!: () => void;
		const gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		await page.route('**/api/v1/health/metrics/heart_rates?*', async (route) => {
			await gate;
			await route.fulfill({ json: { data: responsiveHeartFixture('2026-11-10') } });
		});
		await page.reload({ waitUntil: 'domcontentloaded' });
		const skeleton = page.locator('.heart-loading-plot');
		await expect(skeleton).toBeVisible();
		// reload한 새 문서의 폰트도 준비해 같은 글꼴 상태에서 두 plot을 비교한다.
		await page.evaluate(() => document.fonts.ready);
		const before = (await skeleton.boundingBox())!;
		release();
		await expect(page.locator('.heart-card')).toBeVisible();
		const after = (await page.locator('.heart-card .heart-plot').nth(1).boundingBox())!;
		expect(after.height).toBe(before.height);
		expect(Math.abs(after.y - before.y)).toBeLessThanOrEqual(1);
	}
});

for (const scenario of [
	{
		name: '낮은 화면·큰 값 변화',
		width: 740,
		height: 240,
		samples: normalHeartSamples.map((_, i) => ({
			avg: i % 2 ? 40 : 160,
			min: i % 2 ? 40 : 160,
			max: i % 2 ? 40 : 160,
			hours: 12
		}))
	},
	{ name: '낮은 화면·정상', width: 740, height: 240, samples: normalHeartSamples },
	{
		name: '낮은 화면·일부 결측',
		width: 740,
		height: 240,
		samples: normalHeartSamples.map((sample, i) => (i === 1 || i === 4 ? null : sample))
	},
	{ name: '모바일', width: 390, height: 844, samples: normalHeartSamples },
	{ name: '태블릿', width: 768, height: 900, samples: normalHeartSamples },
	{ name: '데스크톱', width: 1366, height: 900, samples: normalHeartSamples }
]) {
	test(`${scenario.name}: Tab으로 이동한 포인트·날짜는 고정 UI 안에 보인다`, async ({ page }) => {
		await page.setViewportSize({ width: scenario.width, height: scenario.height });
		await open(page, scenario.samples);
		for (const index of [0, 3, 6]) {
			const point = page.locator(`button[data-chart-point="${index}"]`);
			await page.getByRole('button', { name: /^날짜 선택,/ }).focus();
			for (let steps = 0; steps < 16; steps++) {
				await page.keyboard.press('Tab');
				await page.evaluate(
					() =>
						new Promise<void>((resolve) =>
							requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
						)
				);
				if (await point.evaluate((node) => node === document.activeElement)) break;
			}
			await expect(point).toBeFocused();
			const tooltip = page.getByRole('tooltip');
			await expect(tooltip).toContainText(`2026.11.${String(index + 4).padStart(2, '0')}`);
			if ((await tooltip.getAttribute('data-scrollable')) === 'true') {
				const before = await page.evaluate(() => scrollY);
				await page.keyboard.press('Tab');
				await expect(tooltip).toBeFocused();
				await page.keyboard.press('End');
				await expect.poll(() => tooltip.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
				expect(await page.evaluate(() => scrollY)).toBe(before);
				await page.keyboard.press('Shift+Tab');
				await expectFocusedControlVisible(point);
				await page.keyboard.press('Tab');
				await expect(tooltip).toBeFocused();
				const beforeEscape = await page.evaluate(() => scrollY);
				await page.keyboard.press('Escape');
				await expect(point).toBeFocused();
				await expect(tooltip).toHaveCount(0);
				expect(await page.evaluate(() => scrollY)).toBe(beforeEscape);
				await page.keyboard.press('Tab');
				await page.keyboard.press('Shift+Tab');
				await expect(point).toBeFocused();
				await expect(tooltip).toHaveAttribute('data-scrollable', 'true');
				await page.keyboard.press('Tab');
				await expect(tooltip).toBeFocused();
			}
			await page.keyboard.press('Tab');
			const nextIndex = scenario.samples.findIndex((sample, i) => i > index && sample !== null);
			const next =
				nextIndex < 0
					? page.locator('[data-chart-navigation]').first()
					: page.locator(`button[data-chart-point="${nextIndex}"]`);
			await expectFocusedControlVisible(next);
			await expect(tooltip).toContainText(
				nextIndex < 0
					? '클릭하여 하루 상세 보기'
					: `2026.11.${String(nextIndex + 4).padStart(2, '0')}`
			);
			await page.keyboard.press('Shift+Tab');
			await expectFocusedControlVisible(point);
			await page.keyboard.press('Escape');
			await expect(tooltip).toHaveCount(0);
		}
	});
}

test('다음 포인트가 이미 안전 영역에 있으면 Tab 이동으로 문서를 스크롤하지 않는다', async ({
	page
}) => {
	await page.setViewportSize({ width: 740, height: 240 });
	await open(
		page,
		normalHeartSamples.map(() => ({ avg: 72, min: 72, max: 72, hours: 12 }))
	);
	await page.getByRole('button', { name: /^날짜 선택,/ }).focus();
	await page.keyboard.press('Tab');
	await expect(page.locator('button[data-chart-point]').first()).toBeFocused();
	await expect(page.getByRole('tooltip')).toHaveAttribute('data-scrollable', 'true');
	const before = await page.evaluate(() => scrollY);
	await page.keyboard.press('Tab');
	await expect(page.getByRole('tooltip')).toBeFocused();
	await page.keyboard.press('Tab');
	await expectFocusedControlVisible(page.locator('button[data-chart-point]').nth(1));
	expect(await page.evaluate(() => scrollY)).toBe(before);
});
