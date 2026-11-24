import { mockSleepApi } from '../../../../../lib/testing/sleep-api-fixture';
import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../../lib/testing/e2e-auth';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockSleepApi(page);
});

test('route 날짜의 요약과 날짜 선택, 돌아가기를 제공한다', async ({ page }) => {
	await page.goto('/health/sleep/2026-10-20');
	await expect(page.getByRole('heading', { name: '10월 20일 수면 상세' })).toBeVisible();
	await expect(page.getByLabel('날짜 선택')).toHaveValue('2026-10-20');
	const summary = page.getByRole('region', { name: '수면 요약', exact: true });
	await expect(summary.getByRole('term')).toHaveText([
		'총 수면 시간',
		'취침 시각',
		'기상 시각',
		'수면 중 평균 산소포화도'
	]);
	await expect(page.getByRole('link', { name: '하루 상세 보기', exact: true })).toHaveAttribute(
		'href',
		'/health/day/2026-10-20'
	);
	await expect(summary.getByRole('definition')).toHaveText([
		'6시간 39분',
		'00:42',
		'07:21',
		'96.2%'
	]);
	await expect(
		page
			.getByRole('navigation', { name: '건강 데이터', exact: true })
			.getByRole('link', { name: '수면' })
	).toHaveAttribute('aria-current', 'true');
	await expect(page.getByLabel(/자정 경계/)).toHaveCount(0);
	await page.getByLabel('날짜 선택').selectOption('2026-10-23');
	await expect(page).toHaveURL('/health/sleep/2026-10-23');
	await expect(page.getByRole('heading', { name: '10월 23일 수면 상세' })).toBeVisible();
	await expect(summary.getByRole('definition').nth(1)).toHaveText('23:50');
	await expect(summary.getByRole('definition').nth(2)).toHaveText('07:28');
	await expect(page.getByLabel('2026-10-23 자정 경계')).toBeVisible();
	await page.getByRole('link', { name: '돌아가기', exact: true }).click();
	await expect(page).toHaveURL('/health/sleep');
});

test('수면 분 null과 단계 없음, 산소포화도 결측을 각각 처리한다', async ({ page }) => {
	await page.goto('/health/sleep/2026-10-26');
	const summary = page.getByRole('region', { name: '수면 요약', exact: true });
	await expect(summary.getByRole('definition')).toHaveText([
		'동기화 대기',
		'00:52',
		'08:16',
		'95.8%'
	]);
	await expect(page.getByText('해당 날짜의 수면 단계 기록이 없어요.')).toBeVisible();
	await expect(
		page.getByRole('region', { name: '단계별 요약' }).getByRole('definition')
	).toHaveText(['기록 없음', '기록 없음', '기록 없음', '기록 없음']);
	await expect(page.getByText(/전체 수면의/)).toHaveCount(0);
	await page.getByLabel('날짜 선택').selectOption('2026-10-21');
	await expect(summary.getByRole('definition')).toHaveText([
		'6시간 26분',
		'01:10',
		'07:56',
		'96.8%'
	]);
	await expect(page.getByText('해당 날짜의 수면 단계 기록이 없어요.')).toBeVisible();
	await page.getByLabel('날짜 선택').selectOption('2026-10-24');
	await expect(summary.getByRole('definition').last()).toHaveText('값 없음');
	await expect(page.getByRole('figure', { name: '시간대별 수면 단계' })).toBeVisible();
});

test('수면 자체가 없는 날짜와 유효하지만 mock에 없는 날짜에서도 날짜 이동을 제공한다', async ({
	page
}) => {
	for (const date of ['2026-10-22', '2026-10-01']) {
		await page.goto(`/health/sleep/${date}`);
		await expect(page.getByText('해당 날짜의 수면 기록이 없어요.')).toBeVisible();
		await expect(page.getByRole('region', { name: '수면 요약', exact: true })).toHaveCount(0);
		await expect(page.getByLabel('날짜 선택')).toHaveValue(date);
		await expect(page.getByRole('link', { name: '돌아가기', exact: true })).toBeVisible();
	}
});

test('잘못된 날짜는 공통 404 화면으로 안내한다', async ({ page }) => {
	for (const date of ['abc', '2026-02-30', '0000-01-01']) {
		await page.goto(`/health/sleep/${date}`);
		await expect(page.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
		await expect(page.getByText('404', { exact: true })).toBeVisible();
		await expect(page.getByRole('region', { name: '수면 요약', exact: true })).toHaveCount(0);
	}
});

test('미래 날짜는 기록 없음이나 selector 옵션으로 노출하지 않는다', async ({ page }) => {
	await page.goto('/health/sleep/2026-10-27');
	await expect(page.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
	await expect(page.getByText('404', { exact: true })).toBeVisible();
	await expect(page.getByLabel('날짜 선택')).toHaveCount(0);
	await expect(page.getByText('해당 날짜의 수면 기록이 없어요.')).toHaveCount(0);
	await page.goto('/health/sleep/2026-10-26');
	await expect(page.getByLabel('날짜 선택')).toHaveValue('2026-10-26');
	expect(
		await page
			.getByLabel('날짜 선택')
			.locator('option:not([disabled])')
			.evaluateAll((options) =>
				options.every(
					(option) => option instanceof HTMLOptionElement && option.value <= '2026-10-26'
				)
			)
	).toBe(true);
	await page.goto('/health/sleep/2026-10-22');
	await expect(page.getByText('해당 날짜의 수면 기록이 없어요.')).toBeVisible();
});

test('날짜 목록의 선택 상태와 클릭·Escape·외부 클릭 동작을 유지한다', async ({ page }) => {
	await page.goto('/health/sleep/2026-10-23');
	const selector = page.getByRole('combobox', { name: '날짜 선택' });
	await expect(selector).toHaveValue('2026-10-23');
	await selector.click();
	await expect(page.getByRole('option', { selected: true })).toHaveText('2026.10.23');
	await expect(page.getByRole('option', { name: '2026.10.27' })).toHaveCount(0);
	await page.keyboard.press('Escape');
	await expect.poll(() => selector.evaluate((element) => element.matches(':open'))).toBe(false);
	await expect(selector).toBeFocused();
	await selector.click();
	await page.getByRole('heading', { name: '건강 데이터', exact: true }).click();
	await expect.poll(() => selector.evaluate((element) => element.matches(':open'))).toBe(false);
	await selector.click();
	await page.getByRole('option', { name: '2026.10.25', exact: true }).click();
	await expect(page).toHaveURL('/health/sleep/2026-10-25');
	await expect(selector).toHaveValue('2026-10-25');
	await page.goto('/health/sleep/2026-10-01');
	await expect(selector).toHaveValue('2026-10-01');
	await selector.click();
	await expect(page.getByRole('option', { name: '2026.10.01', exact: true })).toHaveCount(1);
	await page.getByRole('option', { name: '2026.10.20', exact: true }).click();
	await expect(page).toHaveURL('/health/sleep/2026-10-20');
});

test('키보드로 날짜 목록을 열고 이동·선택한다', async ({ page }) => {
	await page.goto('/health/sleep/2026-10-23');
	const selector = page.getByRole('combobox', { name: '날짜 선택' });
	// 로딩 중에는 disabled이므로 focus() 전에 실제 조작 가능 상태를 기다린다.
	await expect(selector).toBeEnabled();
	await selector.focus();
	await expect(selector).toBeFocused();
	await page.keyboard.press('Space');
	await expect.poll(() => selector.evaluate((element) => element.matches(':open'))).toBe(true);
	await page.keyboard.press('ArrowDown');
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL('/health/sleep/2026-10-24');
	await expect(selector).toHaveValue('2026-10-24');
});

for (const width of [1920, 1366, 768, 360]) {
	test(`${width}px에서 카드와 timeline 축, 자정 경계가 잘리지 않는다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 1000 });
		await page.goto('/health/sleep/2026-10-23');
		await page.getByRole('figure', { name: '시간대별 수면 단계' }).waitFor();
		await page.evaluate(() => document.fonts.ready);
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
			)
		).toBe(true);
		for (const name of ['수면 요약', '단계별 요약']) {
			const cards = page.getByRole('region', { name, exact: true }).locator('dl > div');
			const bounds = await cards.evaluateAll((elements) =>
				elements.map((element) => {
					const rect = element.getBoundingClientRect();
					return { x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom };
				})
			);
			if (width >= 1024) expect(new Set(bounds.map((bound) => bound.y)).size).toBe(1);
			else expect(new Set(bounds.map((bound) => bound.y)).size).toBeGreaterThan(1);
			for (const bound of bounds) {
				expect(bound.x).toBeGreaterThanOrEqual(0);
				expect(bound.right).toBeLessThanOrEqual(width);
			}
		}
		const figure = page.getByRole('figure', { name: '시간대별 수면 단계' });
		expect(
			await figure.evaluate((element) => {
				const outer = element.getBoundingClientRect();
				return Array.from(element.querySelectorAll('[data-stage]')).every((child) => {
					const rect = child.getBoundingClientRect();
					return rect.left >= outer.left && rect.right <= outer.right + 1;
				});
			})
		).toBe(true);
		const labels = figure
			.locator('[aria-hidden="true"]')
			.filter({ has: page.locator('span') })
			.last();
		expect(
			await labels.evaluate((element) => {
				const visible = Array.from(element.querySelectorAll('span'))
					.filter((span) => span.getClientRects().length)
					.map((span) => span.getBoundingClientRect())
					.sort((a, b) => a.left - b.left);
				return visible.every((rect, index) => index === 0 || visible[index - 1].right <= rect.left);
			})
		).toBe(true);
		await page.screenshot({ path: `test-results/sleep-detail-${width}.png`, fullPage: true });
		const selector = page.getByRole('combobox', { name: '날짜 선택' });
		await selector.scrollIntoViewIfNeeded();
		const before = await selector.boundingBox();
		const summaryBefore = await page
			.getByRole('region', { name: '수면 요약', exact: true })
			.boundingBox();
		await selector.click();
		await expect(page.getByRole('option', { selected: true })).toBeVisible();
		expect(await selector.boundingBox()).toEqual(before);
		expect(
			await page.getByRole('region', { name: '수면 요약', exact: true }).boundingBox()
		).toEqual(summaryBefore);
		const picker = await selector.evaluate((element) => {
			const style = getComputedStyle(element, '::picker(select)');
			const item = element.querySelector('option');
			return {
				width: Number.parseFloat(style.width),
				height: Number.parseFloat(style.height),
				top: Number.parseFloat(style.top),
				left: Number.parseFloat(style.left),
				optionHeight: item?.getBoundingClientRect().height ?? 0,
				overflowY: style.overflowY,
				appearance: style.appearance
			};
		});
		if (!before) throw new Error('날짜 선택 위치 없음');
		expect(picker.appearance).toBe('base-select');
		expect(Math.abs(picker.width - before.width)).toBeLessThanOrEqual(1);
		expect(picker.height).toBeLessThanOrEqual(picker.optionHeight * 8 + 10);
		expect(picker.overflowY).toBe('auto');
		expect(picker.left).toBeGreaterThanOrEqual(0);
		expect(picker.left + picker.width).toBeLessThanOrEqual(width);
		expect(picker.top).toBeGreaterThanOrEqual(before.y + before.height);
		expect(picker.top + picker.height).toBeLessThanOrEqual(1000);
		expect(
			await selector.evaluate((element) =>
				Array.from(element.querySelectorAll('option')).every(
					(option) => getComputedStyle(option).whiteSpace === 'nowrap'
				)
			)
		).toBe(true);
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
			)
		).toBe(true);
		await page.screenshot({
			path: `test-results/sleep-detail-date-picker-${width}.png`,
			fullPage: true
		});
		await page.keyboard.press('Escape');
	});
}
