import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../../lib/testing/home-api-fixture';
import { sleepMetricFixture } from '../../../lib/testing/sleep-api-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
	await page.clock.install({ time: new Date('2026-10-26T03:00:00Z') });
});

test('산소포화도만 15초 지연되어도 정상 수면 요약은 남고 늦은 응답은 적용하지 않는다', async ({
	page
}) => {
	await page.route('**/api/v1/health/metrics/sleeps?start=2026-10-20&end=2026-10-26', (route) => {
		const data = sleepMetricFixture('sleeps', '2026-10-20', '2026-10-26');
		data.summary.avg = 411;
		data.summary.daysWithData = data.summary.daysUsedForSummary = 1;
		Object.assign(data.days[6], { value: 411, missing: false, reason: null });
		const night = data.detail.nights.find((night) => night.date === '2026-10-26');
		if (!night) throw new Error('수면 기록 없음');
		night.sleepMinutes = 411;
		return route.fulfill({ json: { data } });
	});
	let release = () => {};
	let finished = false;
	let responses = 0;
	const held = new Promise<void>((resolve) => (release = resolve));
	page.on('response', (response) => {
		if (response.url().includes('/metrics/sleeps?') && response.status() === 200) responses++;
	});
	await page.route('**/api/v1/health/metrics/oxygen_saturations?*', async (route) => {
		await held;
		const url = new URL(route.request().url());
		await route.fulfill({
			json: {
				data: sleepMetricFixture(
					'oxygen_saturations',
					url.searchParams.get('start') ?? '',
					url.searchParams.get('end') ?? ''
				)
			}
		});
		finished = true;
	});
	try {
		await page.goto('/health/sleep');
		await expect.poll(() => responses).toBe(2);
		const canceled = page.waitForEvent('requestfailed', (request) =>
			request.url().includes('/metrics/oxygen_saturations?')
		);
		await page.clock.fastForward(15000);
		await canceled;
		await expect(page.getByRole('region', { name: '수면 기록 요약' })).toBeVisible();
		await expect(page.getByRole('region', { name: '수면 기록 요약' })).toContainText('6시간 51분');
		await expect(page.getByRole('alert')).toContainText('산소포화도:');
		await expect(
			page.getByRole('heading', { name: '수면 중 산소포화도', exact: true })
		).toHaveCount(0);
		release();
		await expect.poll(() => finished).toBe(true);
		await expect(page.getByRole('alert')).toContainText('산소포화도:');
		await expect(page.getByRole('region', { name: '수면 기록 요약' })).toBeVisible();
		await expect(page.getByRole('region', { name: '수면 기록 요약' })).toContainText('6시간 51분');
		await expect(
			page.getByRole('heading', { name: '수면 중 산소포화도', exact: true })
		).toHaveCount(0);
	} finally {
		release();
	}
});

for (const state of ['only-oxygen', 'all-fail'] as const) {
	test(`${state}: 복합 GET 실패를 정상 기록이나 0으로 바꾸지 않는다`, async ({ page }) => {
		await page.route('**/api/v1/health/metrics/*?*', (route) => {
			if (state === 'only-oxygen' && route.request().url().includes('/oxygen_saturations?'))
				return route.fallback();
			return route.fulfill({
				status: 503,
				json: {
					error: {
						code: 'HEALTH_DATA_UNAVAILABLE',
						message: '합성 조회 오류',
						context: { reason: 'LOOKUP_FAILED' }
					}
				}
			});
		});
		await page.goto('/health/sleep');
		await expect(page.getByRole('alert')).toContainText('수면:');
		await expect(page.getByRole('region', { name: '수면 기록 요약' })).toHaveCount(0);
		if (state === 'only-oxygen')
			await expect(
				page.getByRole('heading', { name: '수면 중 산소포화도', exact: true })
			).toBeVisible();
		else {
			await expect(page.getByRole('alert')).toContainText('산소포화도:');
			await expect(
				page.getByRole('heading', { name: '수면 중 산소포화도', exact: true })
			).toHaveCount(0);
		}
	});
}

test('복합 GET 도중 이탈하면 요청을 취소하고 해제한 이전 응답을 새 화면에 적용하지 않는다', async ({
	page
}) => {
	let release = () => {};
	let block = true;
	let pending = 0;
	let finished = 0;
	const held = new Promise<void>((resolve) => (release = resolve));
	await page.route('**/api/v1/health/metrics/oxygen_saturations?*', async (route) => {
		if (!block) return route.fallback();
		pending++;
		await held;
		await route.abort();
		finished++;
	});
	try {
		await page.goto('/health/sleep');
		await expect.poll(() => pending).toBe(1);
		const canceled = page.waitForEvent('requestfailed', (request) =>
			request.url().includes('/metrics/oxygen_saturations?')
		);
		block = false;
		await page
			.getByRole('navigation', { name: '주 메뉴' })
			.getByRole('link', { name: '건강 데이터' })
			.click();
		await canceled;
		await expect(page.locator('.health-summary-card svg')).toHaveCount(4);
		release();
		await expect.poll(() => finished).toBe(pending);
		await expect(page).toHaveURL('/health');
		await expect(page.locator('.health-summary-card svg')).toHaveCount(4);
		await expect(page.getByRole('region', { name: '수면 기록 요약' })).toHaveCount(0);
	} finally {
		release();
	}
});

for (const width of [1366, 360]) {
	for (const state of ['BUSY', 'TIMEOUT']) {
		test(`${width}px ${state}: 최초 운동 앵커만 이동하고 두 번의 재조회와 명시적 해시 탐색을 구분한다`, async ({
			page
		}) => {
			await page.setViewportSize({ width, height: 800 });
			await page.addInitScript(() => {
				const original = Element.prototype.scrollIntoView;
				const calls: string[] = [];
				Object.defineProperty(window, 'exerciseScrollCalls', { value: calls });
				Element.prototype.scrollIntoView = function (...args) {
					calls.push(this.id);
					return original.apply(this, args);
				};
			});
			await page.route('**/api/v1/health/sync', (route) =>
				state === 'BUSY'
					? route.fulfill({ json: { data: { status: state } } })
					: route.fulfill({
							status: 503,
							json: {
								error: {
									code: 'HEALTH_SYNC_UNAVAILABLE',
									context: { reason: 'TIMEOUT', metrics: [] }
								}
							}
						})
			);
			const calls = () =>
				page.evaluate(
					() =>
						Reflect.get(window, 'exerciseScrollCalls').filter((id: string) => id === 'exercise')
							.length as number
				);
			await page.goto('/health/activity#exercise');
			await expect.poll(calls).toBe(1);
			await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(0);
			await page.evaluate(() => scrollTo(0, 0));
			const documentHeight = await page.evaluate(() => document.documentElement.scrollHeight);
			for (const elapsed of [2000, 3000]) {
				await page.clock.fastForward(elapsed);
				await expect(
					page.getByRole('status').filter({ hasText: '기존 건강 기록을 조회하고 있어요.' })
				).toHaveCount(0);
				// loader의 DOM 반영 및 앵커 이동이 실행될 프레임까지 진행한다.
				await page.clock.runFor(32);
				expect(await page.evaluate(() => scrollY)).toBe(0);
				expect(await calls()).toBe(1);
				expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBe(
					documentHeight
				);
			}
			await page
				.getByRole('navigation', { name: '건강 데이터', exact: true })
				.getByRole('link', { name: '활동', exact: true })
				.click();
			await expect(page).toHaveURL('/health/activity');
			await page.evaluate(() => {
				location.hash = 'exercise';
			});
			await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(0);
			await page.goBack();
			await expect(page).toHaveURL('/health/activity');
			await page.goForward();
			await expect(page).toHaveURL('/health/activity#exercise');
		});
	}
	test(`${width}px 일반 활동 진입은 운동 앵커로 이동하지 않는다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 800 });
		await page.goto('/health/activity');
		await expect(page.locator('#exercise')).toBeVisible();
		expect(await page.evaluate(() => scrollY)).toBe(0);
	});
}
