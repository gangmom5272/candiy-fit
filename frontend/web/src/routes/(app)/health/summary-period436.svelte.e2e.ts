import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockActivityApi } from '../../../lib/testing/activity-api-fixture';
import { mockSleepApi, sleepMetricFixture } from '../../../lib/testing/sleep-api-fixture';
import { mockHeartApi } from '../../../lib/testing/heart-api-fixture';
import { mockDayDetailApi } from '../../../lib/testing/day-detail-api-fixture';

const apiPrefix = '/api/v1/health/';
const current = {
	start: '2026-10-26',
	end: '2026-11-01',
	label: '2026.10.26 ~ 11.01'
};
const previous = {
	start: '2026-10-19',
	end: '2026-10-25',
	label: '2026.10.19 ~ 10.25'
};
const metrics = [
	{
		id: 'steps',
		name: '걸음 수',
		path: '/health/activity',
		hash: '',
		requests: ['metrics/steps', 'metrics/exercises', 'timeline']
	},
	{
		id: 'exercises',
		name: '운동',
		path: '/health/activity',
		hash: '#exercise',
		requests: ['metrics/steps', 'metrics/exercises', 'timeline']
	},
	{
		id: 'sleeps',
		name: '수면',
		path: '/health/sleep',
		hash: '',
		requests: ['metrics/sleeps', 'metrics/oxygen_saturations']
	},
	{
		id: 'heart_rates',
		name: '심박',
		path: '/health/heart',
		hash: '',
		requests: ['metrics/heart_rates']
	}
] as const;
type Metric = (typeof metrics)[number];

async function installApi(page: Page) {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await mockHeartApi(page);
	await page.clock.setFixedTime(new Date('2026-11-01T03:00:00Z'));
}

function periodResponses(page: Page, paths: readonly string[], period: typeof current) {
	return Promise.all(
		paths.map((path) =>
			page.waitForResponse((response) => {
				const url = new URL(response.url());
				return (
					response.request().method() === 'GET' &&
					response.status() === 200 &&
					url.pathname === apiPrefix + path &&
					url.searchParams.get('start') === period.start &&
					url.searchParams.get('end') === period.end
				);
			})
		)
	).then((responses) => Promise.all(responses.map((response) => response.finished())));
}

function trackRequests(page: Page) {
	const requests: URL[] = [];
	page.on('request', (request) => {
		const url = new URL(request.url());
		if (request.method() === 'GET' && url.pathname.startsWith(apiPrefix)) requests.push(url);
	});
	return requests;
}

function periodControl(page: Page) {
	return page
		.getByRole('group', { name: '주간 기간 선택' })
		.getByRole('button', { name: /^날짜 선택,/ });
}

function card(page: Page, metric: Metric) {
	return page.locator(`#health-summary-${metric.id} .health-summary-link`);
}

function href(metric: Metric, referenceDate: string) {
	return `${metric.path}?date=${referenceDate}${metric.hash}`;
}

async function openSummary(page: Page, completed = true) {
	const paths = ['metrics/steps', 'metrics/exercises', 'metrics/sleeps', 'metrics/heart_rates'];
	let loaded = periodResponses(page, paths, current);
	await page.goto('/health');
	await loaded;
	await expect(periodControl(page)).toHaveText(current.label);
	await expect(page.locator('.health-summary-link[href]')).toHaveCount(4);
	if (completed) {
		loaded = periodResponses(page, paths, previous);
		await page.getByRole('button', { name: '이전 주', exact: true }).click();
		await loaded;
		await expect(periodControl(page)).toHaveText(previous.label);
	}
}

async function detailReady(page: Page, metric: Metric, period: typeof current) {
	await expect(periodControl(page)).toHaveText(period.label);
	await expect(page.locator('[data-health-loading]')).toHaveCount(0);
	if (metric.hash) await expect(page.locator('#exercise')).toBeInViewport();
}

test.beforeEach(async ({ page }) => {
	await installApi(page);
});

for (const width of [360, 1366]) {
	for (const metric of metrics) {
		test(`${width}px 이전 주 ${metric.name} 카드는 같은 10/19~25 기간으로 이동한다`, async ({
			page
		}) => {
			await page.setViewportSize({ width, height: 800 });
			const requests = trackRequests(page);
			await openSummary(page);
			const target = card(page, metric);
			await expect(target).toHaveAttribute('href', href(metric, previous.end));
			requests.length = 0;
			const loaded = periodResponses(page, metric.requests, previous);
			await target.click();
			await loaded;
			await expect(page).toHaveURL(href(metric, previous.end));
			await detailReady(page, metric, previous);
			// 활동의 이전 주 비교·최근 4주 분석 요청은 그대로 두고, 현재 주로 바뀌는지 확인한다.
			expect(
				requests.some(
					(url) =>
						metric.requests.some((path) => url.pathname === apiPrefix + path) &&
						url.searchParams.get('start') === current.start &&
						url.searchParams.get('end') === current.end
				)
			).toBe(false);
		});
	}
}

for (const metric of metrics) {
	test(`현재 주 ${metric.name} 카드도 오늘까지의 기존 기간을 유지한다`, async ({ page }) => {
		await openSummary(page, false);
		const target = card(page, metric);
		await expect(target).toHaveAttribute('href', href(metric, current.end));
		const loaded = periodResponses(page, metric.requests, current);
		await target.click();
		await loaded;
		await expect(page).toHaveURL(href(metric, current.end));
		await detailReady(page, metric, current);
	});
}

for (const metric of [metrics[0], metrics[2], metrics[3]]) {
	test(`${metric.name} 직접 진입의 누락·잘못된·미래 날짜는 오늘 기준으로 조회한다`, async ({
		page
	}) => {
		for (const query of ['', '?date=not-a-date', '?date=2026-02-30', '?date=2026-11-02']) {
			const loaded = periodResponses(page, metric.requests, current);
			await page.goto(`${metric.path}${query}`);
			await loaded;
			await detailReady(page, metric, current);
		}
	});
}

test('기록이 없는 이전 주 수면 카드도 선택 기간을 오늘로 바꾸지 않는다', async ({ page }) => {
	await page.route('**/api/v1/health/metrics/sleeps?*', (route) => {
		const url = new URL(route.request().url());
		if (
			url.searchParams.get('start') !== previous.start ||
			url.searchParams.get('end') !== previous.end
		)
			return route.fallback();
		const data = sleepMetricFixture('sleeps', previous.start, previous.end);
		data.days = data.days.map((day) => ({
			...day,
			value: null,
			missing: true,
			reason: 'NO_RECORD' as const,
			isInProgress: false
		}));
		Object.assign(data.summary, {
			avg: null,
			dailyMin: null,
			dailyMax: null,
			daysWithData: 0,
			daysUsedForSummary: 0,
			missingDays: data.days.map((day) => day.date),
			unsyncedDays: []
		});
		Object.assign(data.detail, { nights: [], measuredMin: null, measuredMax: null });
		return route.fulfill({ json: { data } });
	});
	await page.setViewportSize({ width: 360, height: 800 });
	await openSummary(page);
	const metric = metrics[2];
	const target = card(page, metric);
	await expect(target).toContainText('기록 없음');
	await expect(target).toHaveAttribute('href', href(metric, previous.end));
	const loaded = periodResponses(page, metric.requests, previous);
	await target.click();
	await loaded;
	await expect(page).toHaveURL(href(metric, previous.end));
	await detailReady(page, metric, previous);
});

test('이전 주 운동 카드 링크를 같은 세션의 새 탭에 직접 열어도 기간과 운동 영역을 유지한다', async ({
	page,
	context
}) => {
	await openSummary(page);
	const metric = metrics[1];
	const selectedHref = await card(page, metric).getAttribute('href');
	expect(selectedHref).toBe(href(metric, previous.end));
	if (!selectedHref) throw new Error('운동 카드의 이동 주소가 없습니다.');
	const newTab = await context.newPage();
	try {
		// page.route는 새 탭에 상속되지 않으므로 API와 고정 시각을 이동 전에 설치한다.
		await installApi(newTab);
		await newTab.setViewportSize({ width: 360, height: 800 });
		const loaded = periodResponses(newTab, metric.requests, previous);
		await newTab.goto(selectedHref);
		await loaded;
		await expect(newTab).toHaveURL(href(metric, previous.end));
		await detailReady(newTab, metric, previous);
		await expect(page).toHaveURL('/health');
		await expect(periodControl(page)).toHaveText(previous.label);
	} finally {
		await newTab.close();
	}
});

for (const { metric, width } of [
	{ metric: metrics[0], width: 360 },
	{ metric: metrics[3], width: 1366 }
]) {
	test(`436 query-return ${metric.name} 카드의 과거 기간은 하루 상세에서 복귀해도 유지된다`, async ({
		page
	}) => {
		await mockDayDetailApi(page);
		await page.clock.setFixedTime(new Date('2026-11-01T03:00:00Z'));
		await page.setViewportSize({ width, height: 800 });
		const requests = trackRequests(page);
		await openSummary(page);
		const target = card(page, metric);
		await expect(target).toHaveAttribute('href', href(metric, previous.end));
		let loaded = periodResponses(page, metric.requests, previous);
		await target.click();
		await loaded;
		await expect(page).toHaveURL(href(metric, previous.end));
		await detailReady(page, metric, previous);

		const date = '2026-10-21';
		const dayLink =
			metric.id === 'steps'
				? page
						.getByRole('figure', { name: '일별 걸음 수 차트', exact: true })
						.getByRole('link', { name: /2026-10-21 하루 상세 보기$/ })
				: page
						.getByRole('region', { name: '일별 심박수', exact: true })
						.getByRole('link', { name: '2026-10-21 하루 상세', exact: true });
		loaded = periodResponses(
			page,
			['metrics/steps', 'metrics/exercises', 'metrics/sleeps', 'metrics/heart_rates', 'timeline'],
			{ start: date, end: date, label: date }
		);
		await dayLink.click();
		await loaded;
		await expect(page).toHaveURL(`/health/day/${date}`);
		await expect(
			page.getByRole('heading', { name: '10월 21일 하루 상세', exact: true })
		).toBeVisible();
		await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveCount(4);
		const back = page.getByRole('link', { name: '건강 데이터로', exact: true });
		await expect(back).toHaveAttribute('href', metric.path);

		requests.length = 0;
		loaded = periodResponses(page, metric.requests, previous);
		await back.click();
		await loaded;
		// 복귀 URL에는 query가 없어도 기존 metricReturn 기준일이 오늘보다 우선한다.
		await expect(page).toHaveURL(metric.path);
		await detailReady(page, metric, previous);
		expect(
			requests.some(
				(url) =>
					metric.requests.some((path) => url.pathname === apiPrefix + path) &&
					url.searchParams.get('start') === current.start &&
					url.searchParams.get('end') === current.end
			)
		).toBe(false);
	});
}

for (const { metric, width } of [
	{ metric: metrics[0], width: 360 },
	{ metric: metrics[2], width: 1366 }
]) {
	test(`436 browser-back ${width}px ${metric.name} 카드에서 뒤로 가면 건강홈의 과거 주와 네 카드를 복원한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 800 });
		const requests = trackRequests(page);
		await openSummary(page);
		const target = card(page, metric);
		await expect(target).toHaveAttribute('href', href(metric, previous.end));
		let loaded = periodResponses(page, metric.requests, previous);
		await target.click();
		await loaded;
		await expect(page).toHaveURL(href(metric, previous.end));
		await detailReady(page, metric, previous);

		requests.length = 0;
		const paths = ['metrics/steps', 'metrics/exercises', 'metrics/sleeps', 'metrics/heart_rates'];
		loaded = periodResponses(page, paths, previous);
		await page.goBack();
		await loaded;
		await expect(page).toHaveURL('/health');
		await expect(periodControl(page)).toHaveText(previous.label);
		await expect(page.locator('.health-summary-link[href]')).toHaveCount(4);
		for (const restoredMetric of metrics)
			await expect(card(page, restoredMetric)).toHaveAttribute(
				'href',
				href(restoredMetric, previous.end)
			);
		expect(
			requests.some(
				(url) =>
					paths.some((path) => url.pathname === apiPrefix + path) &&
					url.searchParams.get('start') === current.start &&
					url.searchParams.get('end') === current.end
			)
		).toBe(false);
	});
}
