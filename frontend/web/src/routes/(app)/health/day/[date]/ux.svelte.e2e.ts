import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../../lib/testing/e2e-auth';
import {
	dayTimelineFixture,
	mockDayDetailApi
} from '../../../../../lib/testing/day-detail-api-fixture';

for (const width of [1366, 360]) {
	test(`자정 통과 초 단위 구간도 날짜·시간을 한 줄로 모두 표시한다 ${width}px`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height: 800 });
		await mockSignedIn(page);
		await mockDayDetailApi(page);
		await page.route('**/api/v1/health/timeline?*', (route) => {
			const data = dayTimelineFixture('2026-10-20');
			data.days[0].mainSleep = {
				startAt: '2026-10-19T23:59:50+09:00',
				endAt: '2026-10-20T00:00:10+09:00'
			};
			data.days[0].sleepSegments = [{ ...data.days[0].mainSleep, stage: 'light' }];
			data.days[0].exerciseSegments = [
				{
					startAt: '2026-10-20T23:59:50+09:00',
					endAt: '2026-10-21T00:00:10+09:00',
					exerciseType: 79,
					typeName: '걷기',
					title: null
				}
			];
			return route.fulfill({ json: { data } });
		});
		await page.goto('/health/day/2026-10-20');
		await page.evaluate(() => document.fonts.ready);
		for (const [kind, name, startDate, endDate] of [
			['sleep', '얕은 수면 23:59 ~ 00:00 상세', '2026.10.19', '2026.10.20'],
			['exercise', '걷기 23:59 ~ 00:00 상세', '2026.10.20', '2026.10.21']
		]) {
			const trigger = page
				.getByRole('region', { name: '하루 기록 시간축' })
				.getByRole('button', { name, exact: true });
			await trigger.scrollIntoViewIfNeeded();
			// 시간축 스크롤의 기존 닫기 처리가 끝난 다음 키보드 포커스를 확인한다.
			await page.evaluate(
				() =>
					new Promise<void>((resolve) =>
						requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
					)
			);
			await trigger.focus();
			const tooltip = page.getByRole('tooltip');
			await expect(tooltip).toBeVisible();
			await expect(tooltip.locator('[data-interval-date]')).toContainText(startDate);
			await expect(tooltip.locator('[data-interval-date]')).toContainText(endDate);
			await expect(tooltip.locator('[data-interval-time]')).toHaveText('23:59:50~00:00:10');
			await expect(tooltip).toContainText('구간 길이 1분 미만');
			await expect(tooltip).not.toContainText('(경과)');
			const layout = await tooltip.evaluate((element) => {
				const date = element.querySelector<HTMLElement>('[data-interval-date]')!;
				const time = element.querySelector<HTMLElement>('[data-interval-time]')!;
				const style = (target: HTMLElement) => {
					const value = getComputedStyle(target);
					return { size: value.fontSize, color: value.color, weight: value.fontWeight };
				};
				return {
					viewportWidth: document.documentElement.clientWidth,
					date: date.getBoundingClientRect().toJSON(),
					time: time.getBoundingClientRect().toJSON(),
					box: element.getBoundingClientRect().toJSON(),
					dateStyle: style(date),
					timeStyle: style(time),
					fits: date.scrollWidth <= date.clientWidth && time.scrollWidth <= time.clientWidth
				};
			});
			expect(layout.date.y).toBe(layout.time.y);
			expect(layout.time.x).toBeGreaterThanOrEqual(layout.date.right);
			expect(layout.time.right).toBeLessThanOrEqual(layout.box.right - 16);
			expect(layout.dateStyle).toEqual(layout.timeStyle);
			expect(layout.fits).toBe(true);
			expect(layout.box.left).toBeGreaterThanOrEqual(0);
			expect(layout.box.right).toBeLessThanOrEqual(layout.viewportWidth - 8);
			await tooltip.screenshot({ path: testInfo.outputPath(`midnight-${kind}-${width}.png`) });
			await testInfo.attach(`${kind}-geometry`, {
				body: JSON.stringify(layout, null, 2),
				contentType: 'application/json'
			});
			await page.keyboard.press('Escape');
			await expect(tooltip).not.toBeVisible();
		}
	});
}

for (const width of [1366, 1024, 768, 360]) {
	test.describe(`${width}px 하루 상세`, () => {
		test.use({
			viewport: { width, height: 900 },
			hasTouch: width === 360,
			isMobile: width === 360
		});
		test('캘린더 크기·오늘·과거 선택과 운동 툴팁을 확인한다', async ({ page }, testInfo) => {
			await mockSignedIn(page);
			await mockDayDetailApi(page);
			await page.goto('/health/day/2026-10-20');
			const trigger = page.getByRole('button', { name: '날짜 선택, 2026.10.20' });
			await trigger.focus();
			await trigger.press('Enter');
			const calendar = page.getByRole('dialog', { name: '날짜 선택 캘린더' });
			const selected = calendar.getByRole('link', { name: '2026-10-20', exact: true });
			const today = calendar.getByRole('link', { name: '2026-10-26', exact: true });
			await expect(selected).toBeFocused();
			await expect(selected).toHaveCSS('background-color', 'rgb(204, 56, 62)');
			await expect(selected).toHaveCSS('color', 'rgb(252, 249, 246)');
			await expect(today).toHaveAccessibleDescription('오늘');
			await expect(today).toHaveCSS('background-color', 'rgb(255, 244, 243)');
			await expect(today).toHaveCSS('color', 'rgb(44, 46, 53)');
			if (width === 1366) {
				const other = calendar.getByRole('link', { name: '2026-10-21', exact: true });
				for (const day of [other, today]) {
					await day.hover();
					await expect(day).toHaveCSS('background-color', 'rgb(204, 56, 62)');
					await expect(day).toHaveCSS('color', 'rgb(252, 249, 246)');
				}
				await page.mouse.move(0, 0);
				await expect(today).toHaveCSS('background-color', 'rgb(255, 244, 243)');
			}
			await expect(
				calendar.getByRole('button', { name: '2026-10-27', exact: true })
			).toBeDisabled();
			const bounds = await calendar.boundingBox();
			const cell = await selected.boundingBox();
			if (!bounds || !cell) throw new Error('캘린더 크기 없음');
			expect(bounds.width).toBe(width > 768 ? 448 : 320);
			expect(cell.height).toBe(width > 768 ? 48 : 44);
			expect(cell.width).toBeGreaterThan(width > 768 ? 48 : 39);
			expect(bounds.x).toBeGreaterThanOrEqual(0);
			expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
			await page.screenshot({ path: testInfo.outputPath(`calendar-${width}.png`) });
			if (width === 360) await today.tap();
			else {
				await today.focus();
				await today.press('Enter');
			}
			await expect(page).toHaveURL('/health/day/2026-10-26');
			await page.getByRole('button', { name: '날짜 선택, 2026.10.26' }).click();
			await expect(calendar.getByRole('link', { name: '2026-10-26', exact: true })).toHaveCSS(
				'background-color',
				'rgb(204, 56, 62)'
			);
			await page.keyboard.press('Escape');
			await expect(calendar).not.toBeVisible();
			if (width === 1366) {
				await page.getByRole('button', { name: '날짜 선택, 2026.10.26' }).click();
				await calendar.getByRole('link', { name: '2026-10-07', exact: true }).click();
				await expect(page).toHaveURL('/health/day/2026-10-07');
				await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
					'기록 없음',
					'기록 없음',
					'기록 없음',
					'기록 없음'
				]);
			}
			await page.goto('/health/day/2026-10-20');
			const exercise = page.locator('[data-exercise-start]').first();
			if (width === 360) await exercise.tap();
			else await exercise.hover();
			const tooltip = page.getByRole('tooltip');
			await expect(tooltip).toContainText('운동');
			await expect(tooltip).toContainText('달리기');
			const time = tooltip.locator('[data-interval-time]');
			await expect(time).toHaveCSS('white-space', 'nowrap');
			await expect(time).toHaveCSS('font-size', '12px');
			const dateBounds = await tooltip.locator('[data-interval-date]').boundingBox();
			const timeBounds = await time.boundingBox();
			if (!dateBounds || !timeBounds) throw new Error('날짜·시간 위치 없음');
			expect(timeBounds.y).toBe(dateBounds.y);
			expect(timeBounds.x).toBeGreaterThanOrEqual(dateBounds.x + dateBounds.width);
			expect(await time.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
			const tipBounds = await tooltip.boundingBox();
			if (!tipBounds) throw new Error('툴팁 위치 없음');
			expect(tipBounds.x).toBeGreaterThanOrEqual(0);
			expect(tipBounds.x + tipBounds.width).toBeLessThanOrEqual(width);
			await page.screenshot({ path: testInfo.outputPath(`exercise-${width}.png`), fullPage: true });
			await page.locator('[data-sleep-start]').first().focus();
			await expect(tooltip).toContainText('전체 수면 구간');
			await expect(tooltip).not.toContainText('경과');
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
		});
	});
}

test('360px에서 인접 초 단위 운동은 오선택 없이 별도 버튼·키보드·터치로 구분한다', async ({
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
	await page.route('**/api/v1/health/timeline?*', (route) => {
		const data = dayTimelineFixture('2026-10-20');
		data.days[0].exerciseSegments = [
			{
				startAt: '2026-10-20T14:14:00+09:00',
				endAt: '2026-10-20T14:14:10+09:00',
				exerciseType: 1,
				typeName: '근력 운동',
				title: null
			},
			{
				startAt: '2026-10-20T14:14:20+09:00',
				endAt: '2026-10-20T14:14:25+09:00',
				exerciseType: 79,
				typeName: '걷기',
				title: null
			},
			{
				startAt: '2026-10-20T14:14:30+09:00',
				endAt: '2026-10-20T14:14:40+09:00',
				exerciseType: 999,
				typeName: '',
				title: null
			},
			{
				startAt: '2026-10-20T23:50:00+09:00',
				endAt: '2026-10-21T00:20:00+09:00',
				exerciseType: 79,
				typeName: '걷기',
				title: null
			}
		];
		return route.fulfill({ json: { data } });
	});
	await page.goto('/health/day/2026-10-20');
	const choices = page.locator('[aria-label="짧은 운동 구간 선택"] button');
	await expect(choices).toHaveCount(4);
	const tooltip = page.getByRole('tooltip');
	for (const [index, value] of ['근력 운동', '걷기', '운동'].entries()) {
		await choices.nth(index).tap();
		await expect(tooltip).toContainText(value);
		await expect(tooltip).toContainText('1분 미만');
		await expect(tooltip).toContainText(
			['14:14:00~14:14:10', '14:14:20~14:14:25', '14:14:30~14:14:40'][index]
		);
		await choices.nth(index).tap();
		await expect(tooltip).not.toBeVisible();
	}
	await choices.nth(3).tap();
	await expect(tooltip).toContainText('23:50~00:20');
	await expect(tooltip).toContainText('2026.10.21');
	await expect(tooltip).toContainText('0시간 30분');
	await page.keyboard.press('Escape');
	await expect(tooltip).not.toBeVisible();
	const bars = page.locator('[data-exercise-start]');
	await bars.first().scrollIntoViewIfNeeded();
	await bars.first().focus();
	await expect(tooltip).toContainText('근력 운동');
	await bars.nth(1).focus();
	await expect(tooltip).toContainText('14:14:20~14:14:25');
	await page.keyboard.press('Escape');
	await bars.first().evaluate((element) => element.scrollIntoView({ block: 'center' }));
	await expect
		.poll(() =>
			bars.first().evaluate((element) => {
				const bounds = element.getBoundingClientRect();
				return !!document
					.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)
					?.closest('[aria-label="수면과 운동 구간"]');
			})
		)
		.toBe(true);
	const bounds = await bars.first().boundingBox();
	if (!bounds) throw new Error('짧은 운동 위치 없음');
	await page.mouse.click(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
	await expect(tooltip).not.toBeVisible();
	await choices.first().tap();
	await expect(tooltip).toContainText('근력 운동');
	await page.getByRole('heading', { name: '나의 하루', exact: true }).tap();
	await expect(tooltip).not.toBeVisible();
	await context.close();
});
