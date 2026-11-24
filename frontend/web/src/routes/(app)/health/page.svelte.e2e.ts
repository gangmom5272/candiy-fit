import { mockHeartApi, heartMetricFixture } from '../../../lib/testing/heart-api-fixture';
import { mockSleepApi, sleepMetricFixture } from '../../../lib/testing/sleep-api-fixture';
import { mockActivityApi, activityMetricFixture } from '../../../lib/testing/activity-api-fixture';
import { formatSleepMinutes } from '../../../lib/features/health/sleep';
import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await mockHeartApi(page);
});

for (const width of [360, 390, 768, 1366]) {
	test(`${width}px에서 활동·심박은 같은 사용 안내를, 수면은 별도 안내를 실제 표시한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 844 });
		for (const [path, note] of [
			[
				'/health/activity',
				'기록이 없는 날은 평균에서 제외해요. 날짜를 누르면 하루 상세 기록을 볼 수 있어요.'
			],
			[
				'/health/heart',
				'기록이 없는 날은 평균에서 제외해요. 날짜를 누르면 하루 상세 기록을 볼 수 있어요.'
			],
			[
				'/health/sleep',
				'주간은 취침·기상 흐름을 중심으로 보여줘요. 날짜를 선택하면 수면 단계를 확인할 수 있어요.'
			],
			['/health', '기록이 없는 날은 평균에서 제외해요. 선택한 날짜까지 최근 7일을 보여줘요.']
		]) {
			await page.goto(path);
			const controls = page.getByRole('group', { name: '주간 기간 선택' });
			const guide = controls.locator('..').locator(':scope > p');
			await expect(guide).toHaveText(note);
			await expect(guide).toHaveCSS('position', 'static');
			await expect(guide.locator('span')).toHaveAttribute('aria-hidden', 'true');
			const controlBox = await controls.boundingBox();
			const guideBox = await guide.boundingBox();
			if (!controlBox || !guideBox) throw new Error('기간 선택 또는 안내 영역 없음');
			expect(guideBox.height).toBeGreaterThan(10);
			expect(guideBox.x).toBeGreaterThanOrEqual(0);
			expect(guideBox.x + guideBox.width).toBeLessThanOrEqual(width);
			if (width >= 1024) expect(guideBox.x).toBeGreaterThanOrEqual(controlBox.x + controlBox.width);
			else expect(guideBox.y).toBeGreaterThanOrEqual(controlBox.y + controlBox.height);
		}
	});
}

test('건강 데이터 홈의 지표, 활성 메뉴와 안내를 표시한다', async ({ page }) => {
	await page.goto('/health');
	await expect(page.getByRole('heading', { level: 1, name: '건강 데이터' })).toBeVisible();
	await expect(page.getByRole('banner')).toHaveCount(1);
	const navigation = page.getByRole('navigation', { name: '건강 데이터', exact: true });
	await expect(navigation.getByRole('link')).toHaveText(['홈', '활동', '수면', '심박']);
	await expect(navigation.getByRole('link', { name: '홈', exact: true })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(
		page.getByRole('navigation', { name: '주 메뉴' }).getByRole('link', { name: '건강 데이터' })
	).toHaveAttribute('aria-current', 'page');
	const records = page.getByRole('region', { name: '주간 건강 기록' });
	await expect(records.locator('.health-summary-link')).toHaveCount(4);
	await expect(records.getByRole('heading')).toHaveText([
		'걸음 수',
		'운동 시간',
		'수면',
		'일별 심박수'
	]);
	for (const [label, metric] of [
		['걸음 수', 'steps'],
		['운동 시간', 'exercises'],
		['수면', 'sleeps'],
		['일별 심박수', 'heart_rates']
	] as const) {
		const response =
			metric === 'heart_rates'
				? heartMetricFixture('2026-10-20', '2026-10-26')
				: metric === 'sleeps'
					? sleepMetricFixture(metric, '2026-10-20', '2026-10-26')
					: activityMetricFixture(metric, '2026-10-20', '2026-10-26');
		const avg = response.summary.avg;
		if (avg === null) throw new Error('완료 기록이 있는 최근 7일 fixture 필요');
		const value =
			metric === 'sleeps'
				? formatSleepMinutes(avg)
				: metric === 'heart_rates'
					? `${avg} bpm`
					: `${Math.round(avg).toLocaleString('ko-KR')}${metric === 'steps' ? '보' : '분'}`;
		const card = records
			.locator('.health-summary-card')
			.filter({ has: page.getByRole('link', { name: new RegExp('^' + label + ' ') }) });
		await expect(card.getByText(`평균 ${value}`, { exact: true })).toBeVisible();
		const recordedDays = card.getByText(`평균에 반영된 기록 ${response.summary.daysWithData}일`, {
			exact: true
		});
		if (metric === 'heart_rates') await expect(recordedDays).toHaveCount(0);
		else await expect(recordedDays).toBeVisible();
		await expect(card.locator('[data-summary-date]')).toHaveCount(7);
		const metricTarget = card.locator('a[data-metric-target]');
		await expect(metricTarget).toHaveAttribute(
			'href',
			metric === 'sleeps'
				? '/health/sleep?date=2026-10-26'
				: metric === 'heart_rates'
					? '/health/heart?date=2026-10-26'
					: `/health/activity?date=2026-10-26${metric === 'exercises' ? '#exercise' : ''}`
		);
		await expect(metricTarget).toHaveAttribute('tabindex', '-1');
		await expect(card.locator('a[data-date-target]')).toHaveCount(response.days.length);
		for (const day of response.days)
			await expect(card.locator(`a[data-date-target="${day.date}"]`)).toHaveAttribute(
				'href',
				`/health/day/${day.date}`
			);
	}
	const missingLegend = page.locator('main > p');
	await expect(missingLegend).toBeVisible();
	await expect(missingLegend).toContainText('— 기록 없음');
	await expect(missingLegend.locator('.sr-only')).toHaveText(
		'· 2026.10.20 ~ 10.26 · 기준일 2026.10.26'
	);
	await expect(
		page.getByRole('heading', { name: '내 건강에 맞는 생활 습관, AI 코치와 함께 찾아볼까요?' })
	).toBeVisible();
});

for (const [scope, name, path, href = path] of [
	['nav', '홈', '/health'],
	['nav', '활동', '/health/activity'],
	['nav', '수면', '/health/sleep'],
	['nav', '심박', '/health/heart'],
	['cards', '걸음 수', '/health/activity', '/health/activity?date=2026-10-26'],
	['cards', '운동 시간', '/health/activity#exercise', '/health/activity?date=2026-10-26#exercise'],
	['cards', '수면', '/health/sleep', '/health/sleep?date=2026-10-26'],
	['cards', '일별 심박수', '/health/heart', '/health/heart?date=2026-10-26'],
	['cta', 'AI 코치로 이동', '/coaching']
]) {
	test(`${scope} ${name} 링크와 키보드 이동 → ${href}`, async ({ page }) => {
		await page.goto('/health');
		const container =
			scope === 'nav'
				? page.getByRole('navigation', { name: '건강 데이터', exact: true })
				: scope === 'cards'
					? page.getByRole('region', { name: '주간 건강 기록' })
					: page.getByRole('main');
		const link =
			scope === 'cards'
				? container.locator('.health-summary-link').filter({
						has: page.getByRole('heading', { name, exact: true })
					})
				: container.getByRole('link', { name, exact: true });
		await expect(link).toHaveAttribute('href', href);
		await link.focus();
		await page.keyboard.press('Enter');
		await expect(page).toHaveURL(href);
		if (path === '/health/activity') {
			await expect(page.getByRole('heading', { name: '일별 걸음 수', exact: true })).toBeVisible();
		} else if (path === '/health/sleep') {
			await expect(page.getByRole('heading', { name: '주간 수면 흐름' })).toBeVisible();
		} else if (path === '/health/heart') {
			await expect(page.getByRole('heading', { name: '일별 심박수', exact: true })).toBeVisible();
		}
		if (scope === 'cta')
			await expect(page.getByRole('heading', { name: 'AI 코치', exact: true })).toBeVisible();
	});
}

for (const [path, activeName] of [
	['/health', '홈'],
	['/health/activity', '활동'],
	['/health/sleep', '수면'],
	['/health/heart', '심박']
]) {
	test(`${path}에서 HealthTabs의 현재 탭과 링크를 표시한다`, async ({ page }) => {
		const response = await page.goto(path);
		expect(response?.status()).toBe(200);
		await expect(page).toHaveTitle(
			path === '/health' ? '건강 데이터 | 캔디핏' : `${activeName} · 건강 데이터 | 캔디핏`
		);
		const navigation = page.getByRole('navigation', { name: '건강 데이터', exact: true });
		await expect(navigation.locator('[aria-current]')).toHaveText(activeName);
		await expect(navigation.getByRole('link', { name: activeName, exact: true })).toHaveCSS(
			'border-bottom-color',
			'rgb(248, 90, 90)'
		);
		for (const [name, href] of [
			['홈', '/health'],
			['활동', '/health/activity'],
			['수면', '/health/sleep'],
			['심박', '/health/heart']
		]) {
			await expect(navigation.getByRole('link', { name, exact: true })).toHaveAttribute(
				'href',
				href
			);
		}
		if (path !== '/health') {
			await expect(page.getByRole('banner')).toHaveCount(1);
			if (path === '/health/activity') {
				await expect(
					page.getByRole('heading', { name: '일별 걸음 수', exact: true })
				).toBeVisible();
			} else if (path === '/health/sleep') {
				await expect(page.getByRole('heading', { name: '주간 수면 흐름' })).toBeVisible();
			} else if (path === '/health/heart') {
				await expect(page.getByRole('heading', { name: '일별 심박수', exact: true })).toBeVisible();
			}
			await navigation.getByRole('link', { name: '홈', exact: true }).focus();
			await page.keyboard.press('Enter');
			await expect(page).toHaveURL('/health');
		}
	});
}

for (const viewport of [
	{ width: 360, height: 800 },
	{ width: 768, height: 1024 },
	{ width: 1366, height: 768 },
	{ width: 1440, height: 900 },
	{ width: 1536, height: 864 },
	{ width: 1920, height: 1080 },
	{ width: 2560, height: 1440 }
]) {
	test(`${viewport.width}×${viewport.height}에서 카드와 차트가 잘리지 않고 CTA에 접근한다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await page.goto('/health', { waitUntil: 'networkidle' });
		await page.evaluate(() => document.fonts.ready);
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
			viewport.width
		);
		const cards = page.locator('.health-summary-card');
		const tabs = await page
			.getByRole('navigation', { name: '건강 데이터', exact: true })
			.boundingBox();
		const title = await page.getByRole('heading', { level: 1, name: '건강 데이터' }).boundingBox();
		const description = await page
			.getByText('기록을 둘러보고, 궁금한 점은 AI 코치에게 물어보세요.', { exact: true })
			.boundingBox();
		if (!tabs || !title || !description) throw new Error('Health 공통 헤더 요소 위치 없음');
		expect(tabs.y).toBeGreaterThanOrEqual(
			Math.max(title.y + title.height, description.y + description.height)
		);
		expect(description.width).toBeLessThanOrEqual(1);
		expect(description.height).toBeLessThanOrEqual(1);
		await expect(
			page.getByText('기록을 둘러보고, 궁금한 점은 AI 코치에게 물어보세요.', { exact: true })
		).toHaveCSS('position', 'absolute');
		const bounds = [];
		for (const card of await cards.all()) {
			const rect = await card.boundingBox();
			if (!rect) throw new Error('카드 위치 없음');
			bounds.push(rect);
			expect(
				await card.evaluate((element) => {
					const outer = element.getBoundingClientRect();
					return Array.from(element.querySelectorAll('h2, p, svg')).every((child) => {
						const rect = child.getBoundingClientRect();
						const style = getComputedStyle(child);
						if (
							style.position === 'absolute' &&
							(style.clip !== 'auto' || style.clipPath !== 'none')
						)
							return rect.width <= 1 && rect.height <= 1 && style.overflow === 'hidden';
						return (
							rect.left >= outer.left &&
							rect.right <= outer.right &&
							rect.top >= outer.top &&
							rect.bottom <= outer.bottom &&
							child.scrollWidth <= child.clientWidth
						);
					});
				})
			).toBe(true);
		}
		if (viewport.width >= 1024) {
			expect(bounds[1].x).toBeGreaterThanOrEqual(bounds[0].x + bounds[0].width);
			expect(bounds[2].y).toBeGreaterThanOrEqual(bounds[0].y + bounds[0].height);
		} else {
			for (let i = 1; i < bounds.length; i++)
				expect(bounds[i].y).toBeGreaterThanOrEqual(bounds[i - 1].y + bounds[i - 1].height);
		}
		const cta = page.getByRole('link', { name: 'AI 코치로 이동' });
		await cta.scrollIntoViewIfNeeded();
		await expect(cta).toBeInViewport({ ratio: 1 });
		if (viewport.width === 1920) {
			await page.evaluate(() => scrollTo(0, 0));
			const main = await page.getByRole('main').boundingBox();
			const available = await page.locator('body').boundingBox();
			if (!main || !available) throw new Error('Health 본문 위치 없음');
			expect(main.width).toBe(1344);
			expect(
				Math.abs(main.x - available.x - (available.x + available.width - main.x - main.width))
			).toBeLessThanOrEqual(1);
			expect(Math.abs(bounds[0].x - main.x - 32)).toBeLessThanOrEqual(1);
			expect(
				Math.abs(main.x + main.width - bounds[1].x - bounds[1].width - 32)
			).toBeLessThanOrEqual(1);
			expect(Math.abs(bounds[1].x - bounds[0].x - bounds[0].width - 32)).toBeLessThanOrEqual(1);
			expect(Math.abs(bounds[2].y - bounds[0].y - bounds[0].height - 32)).toBeLessThanOrEqual(1);
			for (const card of bounds) {
				expect(card.width).toBeCloseTo(bounds[0].width, 1);
				expect(card.height).toBeCloseTo(bounds[0].height, 1);
			}
			expect(
				await page.evaluate(
					() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
				)
			).toBe(true);
			await page.screenshot({ path: 'test-results/health-1920.png', fullPage: true });
		}
		if (viewport.width === 360)
			await page.screenshot({ path: 'test-results/health-mobile.png', fullPage: true });
	});
}
