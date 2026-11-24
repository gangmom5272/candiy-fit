import type { SleepSegment } from '../../../../lib/features/health/sleep-detail';
import { sleepDetailFixture } from '../../../../lib/testing/sleep-detail-fixture';
import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import { mockSleepApi } from '../../../../lib/testing/sleep-api-fixture';

const A = '깊은 수면 00:14:00~00:14:10 · 1분 미만';
const B = '깊은 수면 00:14:20~00:14:25 · 1분 미만';

async function openStages(page: Page, crowded: boolean, many = false) {
	await mockSignedIn(page);
	await mockSleepApi(page);
	await page.clock.setFixedTime(new Date('2026-11-10T03:00:00Z'));
	const mainSleep = crowded
		? { startAt: '2026-10-19T23:29:00+09:00', endAt: '2026-10-20T01:00:00+09:00' }
		: { startAt: '2026-10-19T23:59:30+09:00', endAt: '2026-10-20T00:20:00+09:00' };
	const fixture = sleepDetailFixture({
		date: '2026-10-20',
		mainSleep,
		sleepSegments: [
			['00:14:00', '00:14:10'],
			['00:14:20', '00:14:25']
		]
			.concat(
				many
					? [
							['00:14:26', '00:14:27'],
							['00:14:28', '00:14:29']
						]
					: []
			)
			.map<SleepSegment>(([start, end]) => ({
				stage: 'deep' as const,
				startAt: `2026-10-20T${start}+09:00`,
				endAt: `2026-10-20T${end}+09:00`
			}))
			.concat([
				{
					stage: 'light' as const,
					startAt: '2026-10-19T23:59:30+09:00',
					endAt: '2026-10-20T00:01:00+09:00'
				}
			])
	});
	await page.route('**/api/v1/health/metrics/*?*', (route) => {
		const metric = new URL(route.request().url()).pathname.split('/').at(-1);
		if (metric !== 'sleeps' && metric !== 'oxygen_saturations') return route.fallback();
		return route.fulfill({ json: { data: metric === 'sleeps' ? fixture.sleep : fixture.oxygen } });
	});
	await page.route('**/api/v1/health/timeline?*', (route) =>
		route.fulfill({ json: { data: fixture.timeline } })
	);
	await page.goto('/health/sleep/2026-10-20');
	await expect(page.getByRole('button', { name: A, exact: true })).toBeVisible();
	await expect(page.locator('[data-health-loading]')).toHaveCount(0);
	await page.evaluate(() => document.fonts.ready);
	return page.getByRole('region', { name: '수면 단계', exact: true });
}

test('360px 마우스: 인접 구간의 선택·호버·포커스와 자정 구간을 유지한다', async ({ page }) => {
	await page.setViewportSize({ width: 360, height: 800 });
	const card = await openStages(page, false);
	const first = card.getByRole('button', { name: A, exact: true });
	const second = card.getByRole('button', { name: B, exact: true });
	const a = await first.boundingBox();
	const b = await second.boundingBox();
	if (!a || !b) throw new Error('선택 영역 없음');
	expect(a.x + a.width).toBeLessThanOrEqual(b.x + 0.02);
	for (const [point, note] of [
		[first, '00:14:00~00:14:10'],
		[second, '00:14:20~00:14:25']
	] as const) {
		await point.hover();
		await expect(page.getByRole('tooltip')).toContainText(note);
		await point.click();
		await expect(page.getByRole('tooltip')).toContainText(note);
		await page.keyboard.press('Escape');
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await point.blur();
		await point.focus();
		await expect(page.getByRole('tooltip')).toContainText(note);
	}
	for (const [index, note] of ['00:14:00~00:14:10', '00:14:20~00:14:25'].entries()) {
		const bar = card.locator('[data-stage="deep"]').nth(index);
		await bar.scrollIntoViewIfNeeded();
		const rect = await bar.boundingBox();
		if (!rect) throw new Error('실제 막대 없음');
		await page.mouse.click(rect.x + rect.width / 2, rect.y + rect.height / 2);
		await expect(page.getByRole('tooltip')).toContainText(note);
	}
	await card.getByRole('button', { name: '얕은 수면 23:59~00:01 · 2분', exact: true }).focus();
	await expect(page.getByRole('tooltip')).toContainText('23:59~00:01');
	// 실제 막대의 기하·색상은 선택 영역 계산과 독립적이다.
	const visual = card.locator('[data-stage="deep"]').first();
	expect(await visual.evaluate((node) => node.getBoundingClientRect().height)).toBe(24);
	expect(await visual.evaluate((node) => getComputedStyle(node).backgroundColor)).toBe(
		'rgb(248, 90, 90)'
	);
});

test.describe('360px 터치 에뮬레이션', () => {
	test.use({ hasTouch: true, isMobile: true, viewport: { width: 360, height: 800 } });
	test('재현 A/B를 정확히 선택하며 구분 불가능한 좌표에서는 임의 선택하지 않는다', async ({
		page
	}) => {
		const card = await openStages(page, true);
		const first = card.getByRole('button', { name: A, exact: true });
		const second = card.getByRole('button', { name: B, exact: true });
		await first.tap();
		await expect(page.getByRole('tooltip')).toContainText('00:14:00~00:14:10');
		await second.tap();
		await expect(page.getByRole('tooltip')).toContainText('00:14:20~00:14:25');
		await second.tap();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await first.tap();
		await page.getByRole('heading', { name: '수면 단계', exact: true }).tap();
		await expect(page.getByRole('tooltip')).toHaveCount(0);
		await first.focus();
		await page.keyboard.press('Tab');
		await expect(page.getByRole('tooltip')).toContainText('00:14:20~00:14:25');
		const deepRow = card.locator('[data-stage-row="deep"]');
		for (const visual of [
			card.locator('[data-stage="deep"]').first(),
			card.locator('[data-stage="deep"]').last()
		]) {
			// 좌표 탭이 고정 하단 메뉴에 닿지 않도록 전체 행을 화면 중앙에 배치한다.
			await deepRow.evaluate((node) =>
				node.scrollIntoView({ block: 'center', behavior: 'instant' })
			);
			// 짧은 막대의 subpixel 너비를 유지한 실제 DOM 좌표를 사용한다.
			const rect = await visual.evaluate((node) => node.getBoundingClientRect().toJSON());
			expect(rect.width).toBeGreaterThan(0);
			expect(rect.height).toBe(24);
			const navigation = await page.getByRole('navigation', { name: '주 메뉴' }).boundingBox();
			if (!navigation) throw new Error('하단 메뉴 위치 없음');
			expect(rect.bottom).toBeLessThanOrEqual(navigation.y);
			await page.touchscreen.tap(rect.x + rect.width / 2, rect.y + rect.height / 2);
			await expect(page.getByRole('tooltip')).toHaveCount(0);
		}
		await first.blur();
		await first.focus();
		await expect(page.getByRole('tooltip')).toContainText('00:14:00~00:14:10');
	});
	test('매우 짧은 구간 여러 개도 키보드로 각각 확인할 수 있다', async ({ page }) => {
		const card = await openStages(page, true, true);
		await card.getByRole('button', { name: A, exact: true }).focus();
		for (const note of [
			'00:14:00~00:14:10',
			'00:14:20~00:14:25',
			'00:14:26~00:14:27',
			'00:14:28~00:14:29'
		]) {
			await expect(page.getByRole('tooltip')).toContainText(note);
			await page.keyboard.press('Tab');
		}
	});
});
