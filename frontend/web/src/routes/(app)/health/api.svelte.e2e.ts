import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockActivityApi, activityMetricFixture } from '../../../lib/testing/activity-api-fixture';
import { mockSleepApi, sleepMetricFixture } from '../../../lib/testing/sleep-api-fixture';
import { mockHeartApi, heartMetricFixture } from '../../../lib/testing/heart-api-fixture';
import { mockHomeApi } from '../../../lib/testing/home-api-fixture';

test('KST 11월 2일 홈은 오늘 포함 7일과 실제 0·결측을 표시하고 서버 평균을 유지한다', async ({
	page
}) => {
	await page.clock.setFixedTime(new Date('2026-11-01T15:05:00Z'));
	const requests: URL[] = [];
	await page.route('**/api/v1/health/metrics/*?*', (route) => {
		const url = new URL(route.request().url());
		requests.push(url);
		const metric = url.pathname.split('/').at(-1) ?? '';
		const start = url.searchParams.get('start') ?? '';
		const end = url.searchParams.get('end') ?? '';
		const data =
			metric === 'heart_rates'
				? heartMetricFixture(start, end)
				: metric === 'sleeps'
					? sleepMetricFixture('sleeps', start, end)
					: activityMetricFixture(metric, start, end);
		data.days = data.days.map((day, index) => ({
			...day,
			value: index === 0 ? 0 : index === 1 ? 84 : index === 6 ? 999 : null,
			missing: index > 1 && index < 6,
			reason: index > 1 && index < 6 ? 'UNSYNCED' : null,
			isInProgress: index === 6
		}));
		Object.assign(data.summary, {
			avg: 42,
			total: metric === 'steps' || metric === 'exercises' ? 84 : null,
			daysWithData: 2,
			daysUsedForSummary: 2,
			completedDays: 6
		});
		// 응답이 월요일부터 오더라도 홈 차트는 날짜 오름차순을 보장한다.
		data.days = [...data.days.slice(-1), ...data.days.slice(0, -1)];
		return route.fulfill({ json: { data } });
	});
	await page.goto('/health');
	const cards = page.locator('.health-summary-card');
	await expect(cards.locator('.health-summary-link[href]')).toHaveCount(4);
	expect(requests).toHaveLength(4);
	for (const request of requests) {
		expect(request.searchParams.get('start')).toBe('2026-10-27');
		expect(request.searchParams.get('end')).toBe('2026-11-02');
	}
	for (const [index, average] of [
		'평균 42보',
		'평균 42분',
		'평균 0시간 42분',
		'평균 42 bpm'
	].entries()) {
		const card = cards.nth(index);
		await expect(card.getByText(average, { exact: true })).toBeVisible();
		await expect(card.locator('[data-summary-date]')).toHaveCount(7);
		await expect(card.locator('[data-summary-date="11.02"]')).toHaveCount(1);
		await expect(card.locator('[data-chart-point][data-date-target="2026-10-27"]')).toHaveCount(1);
		await expect(card.locator('[data-chart-point][data-date-target="2026-10-29"]')).toHaveCount(0);
		await expect(card.locator('[data-chart-point][data-date-target="2026-11-02"]')).toHaveCount(1);
		const missingDate = card.locator('a[data-date-target="2026-10-29"]');
		await expect(missingDate).toHaveCount(1);
		await expect(missingDate).toHaveAttribute('href', '/health/day/2026-10-29');
		const coordinates = await card
			.locator('[data-summary-date]')
			.evaluateAll((dates) => dates.map((date) => date.getBoundingClientRect().x));
		expect(coordinates).toEqual([...coordinates].sort((a, b) => a - b));
	}
	for (const width of [1366, 360]) {
		await page.setViewportSize({ width, height: 800 });
		for (const card of await cards.all()) {
			const lastDate = card.locator('[data-summary-date]').last();
			await expect(lastDate).toHaveAttribute('data-summary-date', '11.02');
			await expect(lastDate.locator('span:visible')).toHaveText(width < 640 ? '02' : '11.02');
			const x = await card
				.locator('[data-summary-date]')
				.evaluateAll((labels) => labels.map((label) => label.getBoundingClientRect().x));
			expect(x).toEqual([...x].sort((a, b) => a - b));
		}
		await test.info().attach(`health-home-date-order-${width}`, {
			body: await page.screenshot({ fullPage: true }),
			contentType: 'image/png'
		});
	}
});

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await mockHeartApi(page);
});

test('최근 7일의 네 지표만 조회하고 진행 중 심박은 기간 평균과 구분한다', async ({ page }) => {
	const requests: string[] = [];
	page.on('request', (request) => {
		if (request.url().includes('/health/metrics/')) requests.push(request.url());
	});
	await page.goto('/health');
	await expect(page.locator('.health-summary-card .health-summary-link[href]')).toHaveCount(4);
	await expect(page.locator('.health-summary-card a button')).toHaveCount(0);
	await expect(
		page.getByRole('region', { name: '주간 건강 기록' }).locator('.health-summary-link[href]')
	).toHaveCount(4);
	expect(requests.map((url) => new URL(url).pathname).sort()).toEqual([
		'/api/v1/health/metrics/exercises',
		'/api/v1/health/metrics/heart_rates',
		'/api/v1/health/metrics/sleeps',
		'/api/v1/health/metrics/steps'
	]);
	for (const request of requests) {
		expect(new URL(request).searchParams.get('start')).toBe('2026-10-20');
		expect(new URL(request).searchParams.get('end')).toBe('2026-10-26');
	}
	const heart = page
		.locator('.health-summary-card')
		.filter({ has: page.getByRole('link', { name: /^일별 심박수 평균/ }) });
	await expect(heart).not.toContainText('오늘 기록은 기간 평균에서 제외해요.');
	await expect(heart.locator('[data-summary-date]')).toHaveCount(7);
	await expect(heart).toContainText('74.2');
	await expect(heart.getByText('평균선', { exact: true })).toHaveCount(1);
});

test('지연된 수면을 기다리지 않고 다른 카드를 표시하며 실패한 지표만 재시도한다', async ({
	page
}) => {
	let release!: () => void;
	const pending = new Promise<void>((resolve) => {
		release = resolve;
	});
	let attempts = 0;
	await page.route('**/api/v1/health/metrics/sleeps?*', async (route) => {
		attempts++;
		if (attempts > 1) return route.fallback();
		await pending;
		await route.fulfill({
			status: 503,
			json: {
				error: {
					code: 'HEALTH_DATA_UNAVAILABLE',
					message: '수면 데이터를 조회하지 못했습니다.',
					context: { reason: 'LOOKUP_FAILED', metrics: ['sleeps'] }
				}
			}
		});
	});
	await page.goto('/health');
	await expect(
		page.getByRole('region', { name: '주간 건강 기록' }).locator('.health-summary-link[href]')
	).toHaveCount(3);
	await expect(page.getByRole('region', { name: '주간 건강 기록' }).getByRole('status')).toHaveText(
		'기록을 불러오는 중이에요.'
	);
	release();
	await expect(page.getByRole('alert')).toContainText('수면');
	const retry = page.getByRole('button', { name: '수면 다시 시도' });
	await retry.hover();
	await expect(retry).toHaveCSS('background-color', 'rgb(243, 246, 251)');
	await retry.focus();
	await page.keyboard.press('Shift+Tab');
	await page.keyboard.press('Tab');
	await expect(retry).toBeFocused();
	await expect(retry).toHaveCSS('outline-style', 'solid');
	await expect(retry).toHaveCSS('outline-width', '2px');
	await page.keyboard.press('Enter');
	await expect(
		page.getByRole('region', { name: '주간 건강 기록' }).locator('.health-summary-link[href]')
	).toHaveCount(4);
	expect(attempts).toBe(2);
});

test('걸음 준비 중에도 정상 세 지표를 유지하고 걸음만 다시 확인해 실제 기록으로 복구한다', async ({
	page
}) => {
	const requests: string[] = [];
	let stepsAttempts = 0;
	await page.route('**/api/v1/health/metrics/*?*', (route) => {
		const url = new URL(route.request().url());
		const metric = url.pathname.split('/').at(-1) ?? '';
		requests.push(metric);
		if (metric === 'steps' && ++stepsAttempts === 1) {
			return route.fulfill({
				status: 503,
				json: {
					error: {
						code: 'HEALTH_DATA_UNAVAILABLE',
						message: '걸음 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.',
						context: { reason: 'DATA_NOT_READY', metrics: ['steps'] }
					}
				}
			});
		}
		const start = url.searchParams.get('start') ?? '';
		const end = url.searchParams.get('end') ?? '';
		const data =
			metric === 'heart_rates'
				? heartMetricFixture(start, end)
				: metric === 'sleeps'
					? sleepMetricFixture('sleeps', start, end)
					: activityMetricFixture(metric, start, end);
		data.summary.avg =
			metric === 'steps' ? 8123 : metric === 'exercises' ? 37 : metric === 'sleeps' ? 420 : 72.4;
		return route.fulfill({ json: { data } });
	});
	await page.goto('/health');
	const notice = page.getByRole('status').filter({
		has: page.getByRole('heading', { level: 2, name: '건강 기록을 준비하고 있어요' })
	});
	await expect(notice).toBeVisible();
	await expect(notice).toContainText('건강 기록을 화면에 표시할 준비를 하고 있어요.');
	await expect(notice).not.toContainText('앱 연동');
	await expect(page.getByRole('alert')).toHaveCount(0);
	const steps = page.locator('#health-summary-steps');
	await expect(steps.getByRole('status')).toHaveText('건강 기록을 준비 중이에요.');
	await expect(steps.getByRole('figure')).toHaveCount(0);
	await expect(steps.locator('[data-chart-point]')).toHaveCount(0);

	const healthyCharts = [
		{ metric: 'exercises', average: '평균 37분' },
		{ metric: 'sleeps', average: '평균 7시간 0분' },
		{ metric: 'heart_rates', average: '평균 72.4 bpm' }
	];
	const chartDetails = new Map<string, string[]>();
	for (const { metric, average } of healthyCharts) {
		const card = page.locator(`#health-summary-${metric}`);
		await expect(card.getByRole('figure')).toHaveCount(1);
		await expect(card.getByText(average, { exact: true })).toBeVisible();
		const labels = await card
			.locator('[data-chart-point]')
			.evaluateAll((points) => points.map((point) => point.getAttribute('aria-label') ?? ''));
		expect(labels.length).toBeGreaterThan(0);
		chartDetails.set(metric, labels);
	}
	await expect(
		page.getByRole('region', { name: '주간 건강 기록' }).getByRole('figure')
	).toHaveCount(3);

	await steps.getByRole('button', { name: '걸음 수 다시 확인', exact: true }).click();
	await expect(steps.getByText('평균 8,123보', { exact: true })).toBeVisible();
	await expect(
		page.getByRole('region', { name: '주간 건강 기록' }).getByRole('figure')
	).toHaveCount(4);
	await expect(notice).toHaveCount(0);
	await expect(steps.getByRole('status')).toHaveCount(0);
	await expect(page.getByRole('alert')).toHaveCount(0);
	expect(stepsAttempts).toBe(2);
	for (const { metric, average } of healthyCharts) {
		const card = page.locator(`#health-summary-${metric}`);
		await expect(card.getByText(average, { exact: true })).toBeVisible();
		expect(
			await card
				.locator('[data-chart-point]')
				.evaluateAll((points) => points.map((point) => point.getAttribute('aria-label') ?? ''))
		).toEqual(chartDetails.get(metric));
		expect(requests.filter((requestedMetric) => requestedMetric === metric)).toHaveLength(1);
	}
});

for (const [status, code, message] of [
	[403, 'HEALTH_DATA_FORBIDDEN', '건강데이터 접근 권한이 없습니다.'],
	[409, 'CANDIY_USER_NOT_LINKED', '앱 연동이 필요합니다.'],
	[503, 'HEALTH_DATA_UNAVAILABLE', '건강데이터를 조회하지 못했습니다.']
] as const) {
	test(`${status} 전체 실패를 정상 빈 기록으로 대체하지 않는다`, async ({ page }) => {
		await page.route('**/api/v1/health/metrics/**', (route) =>
			route.fulfill({ status, json: { error: { code, message } } })
		);
		await page.goto('/health');
		await expect(page.getByRole('alert')).toHaveCount(4);
		for (const card of await page.locator('.health-summary-card').all()) {
			await expect(card).toHaveCSS('background-color', 'rgb(252, 235, 236)');
			await expect(card.getByRole('alert')).toHaveCSS('color', 'rgb(44, 46, 53)');
		}
		await expect(page.getByRole('button', { name: /다시 시도/ })).toHaveCount(4);
		await expect(
			page.getByRole('region', { name: '주간 건강 기록' }).locator('.health-summary-link[href]')
		).toHaveCount(0);
		await expect(page.locator('[data-summary-date]')).toHaveCount(0);
	});
}

test('네트워크 실패를 표시하고 인증 거절 시 기존 로그인 흐름으로 돌아간다', async ({ page }) => {
	await page.route('**/api/v1/health/metrics/steps?*', (route) => route.abort('failed'));
	await page.goto('/health');
	await expect(page.getByRole('alert')).toHaveCount(1);
	await expect(page.getByRole('button', { name: '걸음 수 다시 시도' })).toBeVisible();
	await page.route('**/api/v1/health/metrics/steps?*', (route) =>
		route.fulfill({
			status: 401,
			json: { error: { code: 'AUTH_REQUIRED', message: '로그인이 필요합니다.' } }
		})
	);
	await page.getByRole('button', { name: '걸음 수 다시 시도' }).click();
	await expect(page).toHaveURL(/\/login/);
});

test('세션 만료(AUTH_EXPIRED)는 갱신·재요청 없이 로그인으로 이동한다', async ({ page }) => {
	let refreshes = 0;
	page.on('request', (request) => {
		if (request.url().endsWith('/api/v1/auth/refresh')) refreshes++;
	});
	await page.route('**/api/v1/health/metrics/steps?*', (route) =>
		route.fulfill({
			status: 401,
			json: { error: { code: 'AUTH_EXPIRED', message: '인증이 만료되었습니다.' } }
		})
	);
	await page.goto('/health');
	await expect(page).toHaveURL(/\/login/);
	expect(refreshes).toBe(0);
});

test('화면을 떠나면 대기 중 요청을 취소하고 재진입 시 다시 조회한다', async ({ page }) => {
	await mockHomeApi(page);
	let release!: () => void;
	const pending = new Promise<void>((resolve) => {
		release = resolve;
	});
	let attempts = 0;
	await page.route(
		'**/api/v1/health/metrics/sleeps?start=2026-10-20&end=2026-10-26',
		async (route) => {
			if (new URL(route.request().frame().url()).pathname !== '/health') return route.fallback();
			attempts++;
			if (attempts === 1) await pending;
			await route.fallback();
		}
	);
	await page.goto('/health');
	await expect(
		page.getByRole('region', { name: '주간 건강 기록' }).locator('.health-summary-link[href]')
	).toHaveCount(3);
	await expect(page.getByRole('region', { name: '주간 건강 기록' }).getByRole('status')).toHaveText(
		'기록을 불러오는 중이에요.'
	);
	const canceled = page.waitForEvent('requestfailed', (request) =>
		request.url().includes('/metrics/sleeps?')
	);
	await page
		.getByRole('navigation', { name: '주 메뉴' })
		.getByRole('link', { name: '홈', exact: true })
		.click();
	await canceled;
	release();
	await expect(page).toHaveURL('/home');
	await page
		.getByRole('navigation', { name: '주 메뉴' })
		.getByRole('link', { name: '건강 데이터' })
		.click();
	await expect(
		page.getByRole('region', { name: '주간 건강 기록' }).locator('.health-summary-link[href]')
	).toHaveCount(4);
	expect(attempts).toBe(2);
});
