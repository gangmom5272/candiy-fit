import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import { mockHeartApi, heartMetricFixture } from '../../../../lib/testing/heart-api-fixture';
import { mockActivityApi } from '../../../../lib/testing/activity-api-fixture';
import { mockSleepApi } from '../../../../lib/testing/sleep-api-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHeartApi(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
});

for (const width of [1366, 768, 360]) {
	test(`${width}px에서 날짜 선택 정책과 상단 여백은 세 화면에서 일치한다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 800 });
		const gaps: number[] = [];
		for (const route of ['activity', 'sleep', 'heart']) {
			await page.goto(`/health/${route}`);
			const group = page.getByRole('group', { name: '주간 기간 선택' });
			await expect(group).toBeVisible();
			await expect(page.locator('[data-health-loading]')).toHaveCount(0);
			await page.evaluate(() => document.fonts.ready);
			// 글꼴 교체나 스크롤이 두 boundingBox 호출 사이에 일어나지 않도록 한 프레임에서 측정한다.
			gaps.push(
				await group.evaluate((node) => {
					const nav = document
						.querySelector('nav[aria-label="건강 데이터"]')
						?.getBoundingClientRect();
					const bounds = node.parentElement?.getBoundingClientRect();
					if (!nav || !bounds) throw new Error('기간 선택 위치 없음');
					return bounds.top - nav.bottom;
				})
			);
			const trigger = group.getByRole('button', { name: /^날짜 선택,/ });
			await expect(trigger).toHaveCSS('color', 'rgb(44, 46, 53)');
			if (width === 1366) {
				await trigger.focus();
				await trigger.press('Enter');
			} else await trigger.click();
			const dialog = page.getByRole('dialog', { name: '날짜 선택 캘린더' });
			await expect(dialog).toBeVisible();
			const today = dialog.getByRole('button', { name: '2026-10-26', exact: true });
			await expect(today).toHaveCSS('background-color', 'rgb(204, 56, 62)');
			await expect(today).toHaveAccessibleDescription('오늘');
			const calendarBounds = await dialog.boundingBox();
			const cellBounds = await today.boundingBox();
			if (!calendarBounds || !cellBounds) throw new Error('캘린더 크기 없음');
			expect(calendarBounds.width).toBe(width > 768 ? 448 : 320);
			expect(cellBounds.height).toBe(width > 768 ? 48 : 44);
			await expect(dialog.getByRole('button', { name: '2026-10-27', exact: true })).toBeDisabled();
			const request = page.waitForRequest((request) =>
				request.url().includes('start=2026-10-17&end=2026-10-23')
			);
			const selected = dialog.getByRole('button', { name: '2026-10-23', exact: true });
			if (width === 1366) {
				await selected.focus();
				await selected.press('Enter');
			} else await selected.click();
			await request;
			await expect(dialog).not.toBeVisible();
			await expect(group.getByRole('status')).toHaveText('2026.10.17 ~ 10.23');
			await trigger.click();
			await expect(selected).toHaveCSS('background-color', 'rgb(204, 56, 62)');
			await expect(selected).toHaveCSS('color', 'rgb(252, 249, 246)');
			await expect(today).toHaveCSS('background-color', 'rgb(255, 244, 243)');
			if (width === 1366) {
				await today.hover();
				await expect(today).toHaveCSS('background-color', 'rgb(204, 56, 62)');
				await expect(today).toHaveCSS('color', 'rgb(252, 249, 246)');
			}
			await page.screenshot({ path: `test-results/calendar-shared-${route}-${width}.png` });
			await page.keyboard.press('Escape');
			await expect(dialog).not.toBeVisible();
			await expect(page).toHaveURL(`/health/${route}`);
			await group.getByRole('button', { name: '이전 주', exact: true }).click();
			await expect(group.getByRole('status')).toHaveText('2026.10.10 ~ 10.16');
			await group.getByRole('button', { name: '다음 주', exact: true }).click();
			await expect(group.getByRole('status')).toHaveText('2026.10.17 ~ 10.23');
			await group.getByRole('button', { name: '다음 주', exact: true }).click();
			await expect(group.getByRole('status')).toHaveText('2026.10.20 ~ 10.26');
			await expect(group.getByRole('button', { name: '다음 주', exact: true })).toBeDisabled();
		}
		expect(gaps[0]).toBeCloseTo(gaps[1], 0);
		expect(gaps[1]).toBeCloseTo(gaps[2], 0);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
		await page.screenshot({ path: `test-results/heart-calendar-${width}.png`, fullPage: true });
	});
}

test.describe('심박 터치 및 키보드', () => {
	test.use({ hasTouch: true, isMobile: true, viewport: { width: 360, height: 800 } });
	test('API 값·null·0을 날짜별로 표시하고 기간 선택과 상세 이동을 구분한다', async ({ page }) => {
		await page.route('**/api/v1/health/metrics/heart_rates?*', (route) => {
			const url = new URL(route.request().url());
			const data = heartMetricFixture(
				url.searchParams.get('start') ?? '',
				url.searchParams.get('end') ?? ''
			);
			data.summary.avg = 76.12;
			data.days = data.days.map((day, index) => ({
				...day,
				value: index === 0 ? 76 : index === 1 ? 0 : null,
				missing: index > 2,
				reason:
					index > 2 ? (index < 5 ? 'UNSYNCED' : index === 5 ? 'NO_RECORD' : 'NULL_VALUE') : null
			}));
			data.detail.dailyRanges = data.days
				.map((day, index) => ({
					date: day.date,
					min: index === 0 ? 62 : index === 2 ? 63 : null,
					max: index === 0 ? 105 : index === 2 ? 88 : null
				}))
				.reverse();
			return route.fulfill({ json: { data } });
		});
		await page.goto('/health/heart');
		await expect(page.locator('[data-period-average]')).toHaveText('76.12 bpm');
		const first = page.getByRole('button', { name: '2026-10-20 심박수 확인', exact: true });
		const second = page.getByRole('button', { name: '2026-10-21 심박수 확인', exact: true });
		await first.tap();
		await expect(page.getByRole('tooltip').locator('dd')).toHaveText([
			'76 bpm',
			'62 bpm',
			'105 bpm'
		]);
		await second.tap();
		await expect(page.getByRole('tooltip').locator('dd')).toHaveText(['0 bpm', '—', '—']);
		await second.tap();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await page.getByRole('button', { name: '2026-10-22 심박수 확인', exact: true }).focus();
		await expect(page.getByRole('tooltip').locator('dd')).toHaveText(['—', '63 bpm', '88 bpm']);
		const box = await page.getByRole('tooltip').boundingBox();
		if (!box) throw new Error('툴팁 없음');
		expect(box.x).toBeGreaterThanOrEqual(0);
		expect(box.x + box.width).toBeLessThanOrEqual(360);
		await page.keyboard.press('Escape');
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		const guide = page.getByRole('list', { name: '심박 기록 안내' });
		await expect(guide.getByRole('listitem')).toHaveCount(3);
		await expect(guide.getByRole('listitem').first()).toContainText('2026.10.23 ~ 10.24');
		const date = page.getByRole('link', { name: '2026-10-20 하루 상세', exact: true });
		await date.focus();
		await expect(page.getByRole('tooltip')).toHaveText('클릭하여 하루 상세 보기');
		await page.keyboard.press('Escape');
		await page
			.getByRole('group', { name: '주간 기간 선택' })
			.getByRole('button', { name: /^날짜 선택,/ })
			.tap();
		await page.getByRole('dialog').getByRole('button', { name: '2026-10-26', exact: true }).tap();
		await expect(page.getByRole('dialog')).not.toBeVisible();
		await expect(page).toHaveURL('/health/heart');
		await date.focus();
		await page.screenshot({ path: 'test-results/heart-mobile-tooltip.png', fullPage: true });
		await date.tap();
		await expect(page).toHaveURL('/health/day/2026-10-20');
	});
});
