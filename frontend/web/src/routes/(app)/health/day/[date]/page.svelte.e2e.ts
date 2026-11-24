import { mockDayDetailApi } from '../../../../../lib/testing/day-detail-api-fixture';
import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../../lib/testing/e2e-auth';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockDayDetailApi(page);
});

test('운동·수면 호버와 키보드에서 구간 종류·시각·길이를 확인한다', async ({ page }) => {
	await page.setViewportSize({ width: 1920, height: 1080 });
	await page.goto('/health/day/2026-10-19');
	await expect(page.locator('[data-exercise-start] .exercise-name').first()).toBeVisible();
	await expect(page.locator('[data-exercise-start] .exercise-name').first()).toHaveText('운동');
	await page.goto('/health/day/2026-10-20');
	const exercise = page.locator('[data-exercise-start]').first();
	await exercise.hover();
	const tooltip = page.getByRole('tooltip');
	await expect(tooltip).toContainText('구간 길이');
	await expect(tooltip).toContainText('2026.10.20');
	await expect(tooltip).toContainText('달리기');
	await expect(tooltip).toContainText('0시간 38분');
	await expect(exercise).toHaveAttribute('aria-describedby', /.+/);
	await page.locator('[data-sleep-start]').first().focus();
	await expect(tooltip).toContainText('수면');
	await expect(tooltip).toContainText('구간 길이');
	await expect(tooltip).toContainText('전체 수면 구간');
	await page.keyboard.press('Escape');
	await expect(tooltip).not.toBeVisible();
	await page.locator('[data-heart-point]').first().focus();
	await expect(tooltip).toContainText('평균');
	await expect(tooltip).toContainText('bpm');
	const stepValue = Number(
		await page.locator('[data-step-hour="3"]').getAttribute('data-step-value')
	);
	const step = page.locator('[data-step-trigger="3"]');
	await step.hover();
	await expect(tooltip).toContainText('03:00 ~ 04:00 걸음 수');
	await expect(tooltip).toContainText(`${stepValue.toLocaleString('ko-KR')}보`);
	await step.focus();
	await expect(step).toHaveAttribute('aria-describedby', /.+/);
});

test('모바일에서 좁은 운동·수면 구간을 탭하면 상세가 열리고 외부 탭으로 닫힌다', async ({
	browser
}) => {
	const context = await browser.newContext({
		viewport: { width: 360, height: 800 },
		hasTouch: true,
		isMobile: true
	});
	const page = await context.newPage();
	await mockSignedIn(page);
	await mockDayDetailApi(page);
	await page.goto('/health/day/2026-10-20');
	await page.locator('[data-exercise-start]').first().tap();
	const tooltip = page.getByRole('tooltip');
	await expect(tooltip).toBeVisible();
	await expect(tooltip).toContainText('구간 길이');
	await expect(tooltip).toContainText('달리기');
	const bounds = await tooltip.boundingBox();
	expect(bounds).not.toBeNull();
	if (bounds) {
		expect(bounds.x).toBeGreaterThanOrEqual(0);
		expect(bounds.x + bounds.width).toBeLessThanOrEqual(360);
	}
	await page.locator('[data-sleep-start]').first().tap();
	await expect(tooltip).toContainText('수면');
	await expect(tooltip).toContainText('전체 수면 구간');
	await page.locator('[data-step-trigger="3"]').tap();
	await expect(tooltip).toContainText('03:00 ~ 04:00 걸음 수');
	await page.getByRole('heading', { name: '나의 하루', exact: true }).tap();
	await expect(tooltip).not.toBeVisible();
	await context.close();
});

test('날짜 텍스트로 캘린더를 열고 월 이동·날짜 선택·미래 제한과 닫기를 제공한다', async ({
	page
}) => {
	await page.goto('/health/day/2026-10-20');
	await page.getByRole('button', { name: '날짜 선택, 2026.10.20' }).click();
	const calendar = page.getByRole('dialog', { name: '날짜 선택 캘린더' });
	await expect(calendar).toBeVisible();
	await expect(calendar.getByRole('link', { name: '2026-10-20', exact: true })).toBeFocused();
	await expect(calendar.getByRole('button', { name: '2026-10-27', exact: true })).toBeDisabled();
	await expect(calendar.getByRole('button', { name: '다음 달' })).toBeDisabled();
	await calendar.getByRole('button', { name: '이전 달' }).click();
	await expect(calendar.getByText('2026년 9월')).toBeVisible();
	await calendar.getByRole('button', { name: '다음 달' }).click();
	await calendar.getByRole('link', { name: '2026-10-19', exact: true }).click();
	await expect(page).toHaveURL('/health/day/2026-10-19');
	await expect(calendar).not.toBeVisible();
	await page.getByRole('button', { name: '날짜 선택, 2026.10.19' }).click();
	await page.keyboard.press('Escape');
	await expect(calendar).not.toBeVisible();
	await expect(page.getByRole('button', { name: '날짜 선택, 2026.10.19' })).toBeFocused();
});

test('route 날짜, 네 지표 source, 날짜 이동과 건강 데이터 복귀', async ({ page }) => {
	await page.goto('/health/day/2026-10-19');
	await expect(
		page.getByRole('heading', { name: '10월 19일 하루 상세', exact: true })
	).toBeVisible();
	// dl의 native semantics는 definition list이므로 DOM 범위로 지표를 확인한다.
	const cards = page.locator('dl[aria-label="하루 기록 요약"]');
	await expect(cards.locator('dt')).toHaveText(['걸음 수', '운동', '총 수면', '평균 심박']);
	await expect(cards.locator('dd')).toHaveText(['8,120보', '60분', '6시간 16분', '76 bpm']);
	await expect(page.getByRole('link', { name: '건강 데이터로', exact: true })).toHaveAttribute(
		'href',
		'/health'
	);
	await expect(page.getByRole('link', { name: '이날 기록에 대해 물어보기' })).toHaveAttribute(
		'href',
		'/coaching'
	);
	await page.getByRole('link', { name: '다음 날짜', exact: true }).focus();
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL('/health/day/2026-10-20');
	await expect(
		page.getByRole('heading', { name: '10월 20일 하루 상세', exact: true })
	).toBeVisible();
	await expect(cards.locator('dd')).toHaveText(['7,680보', '38분', '6시간 39분', '73 bpm']);
	await page.getByRole('link', { name: '이전 날짜', exact: true }).click();
	await expect(page).toHaveURL('/health/day/2026-10-19');
	await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
	await expect(page).toHaveURL('/health');
});

test('각 지표 결측과 실제 운동 0분을 독립적으로 표시한다', async ({ page }) => {
	await page.goto('/health/day/2026-10-21');
	const values = page.locator('dl[aria-label="하루 기록 요약"] dd');
	await expect(values).toHaveText(['기록 없음', '0분', '6시간 26분', '기록 없음']);
	const timeline = page.getByRole('region', { name: '나의 하루' });
	await expect(timeline.getByText('시간대별 심박 기록 없음', { exact: true })).toBeVisible();
	await expect(timeline.getByText('시간대별 걸음 기록 없음', { exact: true })).toBeVisible();
	await expect(timeline.locator('[data-exercise-start]')).toHaveCount(0);
	await expect(timeline.getByRole('list', { name: '타임라인 범례' })).not.toContainText(
		'운동 구간'
	);
	await expect(timeline.locator('[data-step-hour]')).toHaveCount(0);
	await page.goto('/health/day/2026-10-22');
	await expect(values).toHaveText(['9,240보', '42분', '기록 없음', '78 bpm']);
	await expect(timeline.locator('[data-sleep-start]')).toHaveCount(0);
});

test('연동 시작일 이전과 이후의 빈 날짜를 서로 다른 상태로 안내한다', async ({ page }) => {
	await page.goto('/health/day/2026-09-30');
	const values = page.locator('dl[aria-label="하루 기록 요약"] dd');
	await expect(values).toHaveText(['연동 전', '연동 전', '연동 전', '연동 전']);
	await page.getByRole('link', { name: '다음 날짜', exact: true }).click();
	await expect(page).toHaveURL('/health/day/2026-10-01');
	await expect(values).toHaveText(['기록 없음', '기록 없음', '기록 없음', '기록 없음']);
});

test('기준일에는 다음 날짜를 비활성화하고 당일 평균과 수면 동기화 대기를 보존한다', async ({
	page
}) => {
	await page.goto('/health/day/2026-10-26');
	await expect(page.getByRole('button', { name: '다음 날짜' })).toBeDisabled();
	await expect(page.getByRole('link', { name: '다음 날짜' })).toHaveCount(0);
	await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
		'동기화 대기',
		'동기화 대기',
		'동기화 대기',
		'74.2 bpm'
	]);
	await expect(
		page.getByRole('region', { name: '나의 하루' }).getByText('오늘 기록은 수집 중이에요.')
	).toBeVisible();
	await page.getByRole('link', { name: '이날 기록에 대해 물어보기' }).click();
	await expect(page).toHaveURL('/coaching');
});

test('유효하지만 기록이 없는 날짜에서도 날짜 이동과 빈 타임라인을 제공한다', async ({ page }) => {
	await page.goto('/health/day/2026-10-07');
	await expect(
		page.getByRole('heading', { name: '10월 7일 하루 상세', exact: true })
	).toBeVisible();
	await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
		'기록 없음',
		'기록 없음',
		'기록 없음',
		'기록 없음'
	]);
	await expect(page.getByRole('link', { name: '다음 날짜' })).toHaveAttribute(
		'href',
		'/health/day/2026-10-08'
	);
	await expect(page.locator('[data-step-hour], [data-heart-segment]')).toHaveCount(0);
});

for (const date of ['bad-date', '2026-02-30', '0000-01-01', '2026-10-27']) {
	test(`${date} invalid/future route는 공통 404 화면으로 처리한다`, async ({ page }) => {
		await page.goto(`/health/day/${date}`);
		await expect(page.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
		await expect(page.getByText('404', { exact: true })).toBeVisible();
		await expect(page.getByRole('heading', { name: /하루 상세/ })).toHaveCount(0);
	});
}

for (const viewport of [
	{ width: 1920, height: 1080 },
	{ width: 1366, height: 768 },
	{ width: 768, height: 1024 },
	{ width: 360, height: 800 }
]) {
	test(`${viewport.width}에서 카드·시간축 정렬과 overflow 및 아이콘을 확인한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize(viewport);
		await page.goto('/health/day/2026-10-20');
		await expect(
			page.getByRole('heading', { name: '10월 20일 하루 상세', exact: true })
		).toBeVisible();
		await page.evaluate(() => document.fonts.ready);
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
			viewport.width
		);
		const timeline = page.getByRole('region', { name: '나의 하루' });
		await expect(timeline.locator('[data-step-hour]')).toHaveCount(24);
		const alignment = await timeline.evaluate((element) => {
			const plot = element.querySelector('.day-time-plot')?.getBoundingClientRect();
			if (!plot) throw new Error('시간축 위치 없음');
			const rect = element.querySelector('[data-step-hour="3"]')?.getBoundingClientRect();
			if (!rect) throw new Error('시간대 걸음 없음');
			return Math.abs(rect.left + rect.width / 2 - (plot.left + (plot.width * 210) / 1440));
		});
		expect(alignment).toBeLessThan(0.2);
		const navImages = page
			.getByRole('navigation', { name: '하루 기록 날짜 이동' })
			.locator('img:visible');
		expect(
			await navImages.evaluateAll((images) =>
				images.every(
					(image) =>
						image instanceof HTMLImageElement &&
						image.complete &&
						image.naturalWidth === 24 &&
						image.getBoundingClientRect().width === 24
				)
			)
		).toBe(true);
		const cards = page.locator('dl[aria-label="하루 기록 요약"] > div');
		expect(
			await cards.evaluateAll((elements) =>
				elements.every((element) => element.scrollWidth <= element.clientWidth)
			)
		).toBe(true);
		const cta = page.getByRole('link', { name: '이날 기록에 대해 물어보기' });
		await cta.scrollIntoViewIfNeeded();
		await expect(cta).toBeInViewport({ ratio: 1 });
		await page.screenshot({
			path: testInfo.outputPath(`day-${viewport.width}.png`),
			fullPage: true
		});
		expect(
			await timeline
				.locator('[data-timeline-scroll]')
				.evaluate((el) => el.scrollWidth <= el.clientWidth)
		).toBe(true);
		await page.goto('/health/day/2026-10-25');
		const scroll = timeline.locator('[data-timeline-scroll]');
		await expect(timeline.locator('[data-range-start]')).toHaveAttribute(
			'data-range-start',
			'-148'
		);
		await expect(timeline.locator('[data-sleep-start]').first()).toHaveAttribute(
			'data-sleep-start',
			'-148'
		);
		expect(await scroll.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
			viewport.width
		);
		const movement = await scroll.evaluate((el) => {
			const grid = el.querySelector('[data-grid-minute="0"]');
			const bar = el.querySelector('[data-step-hour="0"]');
			if (!grid || !bar) throw new Error('공통 축 요소 없음');
			const before = [grid.getBoundingClientRect().left, bar.getBoundingClientRect().left];
			el.scrollLeft = el.scrollWidth;
			return {
				scrollLeft: el.scrollLeft,
				grid: before[0] - grid.getBoundingClientRect().left,
				bar: before[1] - bar.getBoundingClientRect().left
			};
		});
		expect(movement.scrollLeft).toBeGreaterThan(0);
		expect(movement.grid).toBeCloseTo(movement.scrollLeft, 1);
		expect(movement.bar).toBeCloseTo(movement.grid, 1);
		await scroll.evaluate((el) => {
			el.scrollLeft = 0;
		});
		await page.screenshot({
			path: testInfo.outputPath(`day-extended-${viewport.width}.png`),
			fullPage: true
		});
	});
}
