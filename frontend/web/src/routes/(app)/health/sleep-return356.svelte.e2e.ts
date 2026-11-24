import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockActivityApi } from '../../../lib/testing/activity-api-fixture';
import { mockSleepApi } from '../../../lib/testing/sleep-api-fixture';
import { mockHeartApi } from '../../../lib/testing/heart-api-fixture';
import { mockDayDetailApi } from '../../../lib/testing/day-detail-api-fixture';

const sleepPaths = [
	'/api/v1/health/metrics/sleeps',
	'/api/v1/health/metrics/oxygen_saturations',
	'/api/v1/health/timeline'
];
const dayPaths = [
	'/api/v1/health/metrics/steps',
	'/api/v1/health/metrics/exercises',
	'/api/v1/health/metrics/sleeps',
	'/api/v1/health/metrics/heart_rates',
	'/api/v1/health/timeline'
];

function responses(page: Page, date: string, paths: string[], failedPath?: string) {
	return Promise.all(
		paths.map((path) =>
			page.waitForResponse((response) => {
				const url = new URL(response.url());
				return (
					response.request().method() === 'GET' &&
					response.status() === (path === failedPath ? 503 : 200) &&
					url.pathname === path &&
					url.searchParams.get('start') === date &&
					url.searchParams.get('end') === date
				);
			})
		)
	).then((items) => Promise.all(items.map((response) => response.finished())));
}

function trackQueries(page: Page) {
	const queries: URL[] = [];
	page.on('request', (request) => {
		const url = new URL(request.url());
		if (request.method() === 'GET' && dayPaths.concat(sleepPaths).includes(url.pathname))
			queries.push(url);
	});
	return queries;
}

function expectQueries(queries: URL[], date: string, paths: string[]) {
	expect(queries).toHaveLength(paths.length);
	expect(queries.map((url) => url.pathname).sort()).toEqual([...paths].sort());
	for (const url of queries) expect(url.search).toBe(`?start=${date}&end=${date}`);
}

async function sleepReady(page: Page, date: string) {
	await expect(page).toHaveURL(`/health/sleep/${date}`);
	const [, month, day] = date.split('-').map(Number);
	await expect(
		page.getByRole('heading', { name: `${month}월 ${day}일 수면 상세`, exact: true })
	).toBeVisible();
	await expect(page.getByRole('combobox', { name: '날짜 선택', exact: true })).toHaveValue(date);
	await expect(page.getByRole('combobox', { name: '날짜 선택', exact: true })).toBeEnabled();
	await expect(page.locator('[data-health-loading]')).toHaveCount(0);
	const summary = page.getByRole('region', { name: '수면 요약', exact: true });
	if (date === '2026-10-22') {
		await expect(summary).toHaveCount(0);
		await expect(page.getByText('해당 날짜의 수면 기록이 없어요.', { exact: true })).toBeVisible();
	} else {
		const expected =
			date === '2026-10-23'
				? ['7시간 0분', '23:50', '07:28', '95.4%']
				: date === '2026-10-26'
					? ['동기화 대기', '00:52', '08:16', '95.8%']
					: ['6시간 39분', '00:42', '07:21', '96.2%'];
		await expect(summary.getByRole('definition')).toHaveText(expected);
		if (date === '2026-10-26')
			await expect(
				page.getByText('해당 날짜의 수면 단계 기록이 없어요.', { exact: true })
			).toBeVisible();
		else
			await expect(
				page.getByRole('figure', { name: '시간대별 수면 단계', exact: true })
			).toBeVisible();
	}
	await page.evaluate(() => document.fonts.ready);
}

async function visitSleep(page: Page, date = '2026-10-20') {
	const loaded = responses(page, date, sleepPaths);
	await page.goto(`/health/sleep/${date}`);
	await loaded;
	await sleepReady(page, date);
}

async function dayReady(page: Page, date: string) {
	await expect(page).toHaveURL(`/health/day/${date}`);
	const [, month, day] = date.split('-').map(Number);
	await expect(
		page.getByRole('heading', { name: `${month}월 ${day}일 하루 상세`, exact: true })
	).toBeVisible();
	const summary = page.locator('dl[aria-label="하루 기록 요약"] dd');
	if (date === '2026-10-20')
		await expect(summary).toHaveText(['7,680보', '38분', '6시간 39분', '73 bpm']);
	else if (date === '2026-10-21')
		await expect(summary).toHaveText(['기록 없음', '0분', '6시간 26분', '기록 없음']);
	else if (date === '2026-10-22')
		await expect(summary).toHaveText(['9,240보', '42분', '기록 없음', '78 bpm']);
	else if (date === '2026-10-26')
		await expect(summary).toHaveText(['동기화 대기', '동기화 대기', '동기화 대기', '74.2 bpm']);
	else await expect(summary).toHaveCount(4);
}

async function activateWithScroll(
	page: Page,
	target: Locator,
	input: 'mouse' | 'touch' | 'keyboard'
) {
	let capture!: (value: number) => void;
	const captured = new Promise<number>((resolve) => (capture = resolve));
	await page.exposeFunction('__captureSleepReturn356', capture);
	await target.evaluate((element) => {
		element.addEventListener(
			'click',
			() => {
				void (
					window as unknown as { __captureSleepReturn356: (value: number) => Promise<void> }
				).__captureSleepReturn356(scrollY);
			},
			{ capture: true, once: true }
		);
	});
	if (input === 'touch') await target.tap();
	else if (input === 'keyboard') {
		await page.keyboard.press('Tab');
		await target.focus();
		await page.keyboard.press('Enter');
	} else await target.click();
	return captured;
}

async function enterDay(page: Page, date: string, input: 'mouse' | 'touch' | 'keyboard' = 'mouse') {
	const link = page.getByRole('link', { name: '하루 상세 보기', exact: true });
	await expect(link).toHaveAttribute('href', `/health/day/${date}`);
	const loaded = responses(page, date, dayPaths);
	const scrollY = await activateWithScroll(page, link, input);
	await loaded;
	await dayReady(page, date);
	await expect(page.getByRole('link', { name: '건강 데이터로', exact: true })).toHaveAttribute(
		'href',
		`/health/sleep/${date}`
	);
	return scrollY;
}

async function returnToSleep(page: Page, date: string, scrollY: number) {
	const loaded = responses(page, date, sleepPaths);
	await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
	await loaded;
	await sleepReady(page, date);
	await expect
		.poll(async () => Math.abs((await page.evaluate(() => window.scrollY)) - scrollY))
		.toBeLessThanOrEqual(1);
	await expect(page.getByRole('link', { name: '돌아가기', exact: true })).toHaveAttribute(
		'href',
		'/health/sleep'
	);
}

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await mockHeartApi(page);
	await mockDayDetailApi(page);
	await page.clock.setFixedTime(new Date('2026-10-26T03:00:00Z'));
	await page.setViewportSize({ width: 1366, height: 768 });
});

test('수면 상세 → 하루 상세 → 앱 복귀는 원래 수면 날짜·조회·스크롤을 복원한다', async ({
	page
}) => {
	const queries = trackQueries(page);
	await visitSleep(page);
	expectQueries(queries, '2026-10-20', sleepPaths);
	await page.evaluate(() => scrollTo(0, 64));
	const dayStart = queries.length;
	const scrollY = await enterDay(page, '2026-10-20');
	expect(scrollY).toBeGreaterThan(0);
	expectQueries(queries.slice(dayStart), '2026-10-20', dayPaths);
	const returnStart = queries.length;
	await returnToSleep(page, '2026-10-20', scrollY);
	expectQueries(queries.slice(returnStart), '2026-10-20', sleepPaths);
});

test.describe('모바일 수면 상세 복귀', () => {
	test.use({ hasTouch: true });
	test('360px touch 왕복도 수면 상세 날짜와 실제 선택 시점 스크롤을 유지한다', async ({ page }) => {
		await page.setViewportSize({ width: 360, height: 800 });
		await visitSleep(page);
		await page.evaluate(() => scrollTo(0, 64));
		const scrollY = await enterDay(page, '2026-10-20', 'touch');
		await returnToSleep(page, '2026-10-20', scrollY);
	});
});

test('Enter로 연 하루 상세의 복귀 링크도 원래 수면 상세로 이동한다', async ({ page }) => {
	await visitSleep(page);
	const scrollY = await enterDay(page, '2026-10-20', 'keyboard');
	await returnToSleep(page, '2026-10-20', scrollY);
});

test('수면 날짜를 23일로 선택한 뒤 왕복하면 20일이나 건강 메인으로 돌아가지 않는다', async ({
	page
}) => {
	await visitSleep(page);
	const changed = responses(page, '2026-10-23', sleepPaths);
	await page.getByRole('combobox', { name: '날짜 선택', exact: true }).selectOption('2026-10-23');
	await changed;
	await sleepReady(page, '2026-10-23');
	const scrollY = await enterDay(page, '2026-10-23');
	await returnToSleep(page, '2026-10-23', scrollY);
	await expect(page.getByLabel('2026-10-23 자정 경계')).toBeVisible();
});

test('하루 상세 이전·다음·캘린더로 날짜를 바꿔도 출발한 수면 20일로 복귀한다', async ({ page }) => {
	await visitSleep(page);
	const scrollY = await enterDay(page, '2026-10-20');
	for (const [name, date] of [
		['다음 날짜', '2026-10-21'],
		['이전 날짜', '2026-10-20']
	]) {
		const loaded = responses(page, date, dayPaths);
		await page.getByRole('link', { name, exact: true }).click();
		await loaded;
		await dayReady(page, date);
		await expect(page.getByRole('link', { name: '건강 데이터로', exact: true })).toHaveAttribute(
			'href',
			'/health/sleep/2026-10-20'
		);
	}
	await page.getByRole('button', { name: '날짜 선택, 2026.10.20', exact: true }).click();
	const changed = responses(page, '2026-10-23', dayPaths);
	await page
		.getByRole('dialog', { name: '날짜 선택 캘린더' })
		.getByRole('link', { name: '2026-10-23', exact: true })
		.click();
	await changed;
	await dayReady(page, '2026-10-23');
	await expect(page.getByRole('link', { name: '건강 데이터로', exact: true })).toHaveAttribute(
		'href',
		'/health/sleep/2026-10-20'
	);
	await returnToSleep(page, '2026-10-20', scrollY);
});

test('브라우저 뒤로가기도 직전 수면 상세 날짜·요약·스크롤을 복원한다', async ({ page }) => {
	await visitSleep(page);
	await page.evaluate(() => scrollTo(0, 64));
	const scrollY = await enterDay(page, '2026-10-20');
	const loaded = responses(page, '2026-10-20', sleepPaths);
	await page.goBack();
	await loaded;
	await sleepReady(page, '2026-10-20');
	await expect
		.poll(async () => Math.abs((await page.evaluate(() => window.scrollY)) - scrollY))
		.toBeLessThanOrEqual(1);
});

for (const date of ['2026-10-22', '2026-10-26']) {
	test(`${date}: 기록 없음과 수면 분 null도 같은 수면 상세로 왕복하고 기존 상태를 보존한다`, async ({
		page
	}) => {
		await visitSleep(page, date);
		const scrollY = await enterDay(page, date);
		await returnToSleep(page, date, scrollY);
	});
}

test('복귀 수면 조회의 지연·503에도 출발 날짜를 유지하고 기존 오류·재시도로 복구한다', async ({
	page
}) => {
	await visitSleep(page);
	await enterDay(page, '2026-10-20');
	let release = () => {};
	const delayed = new Promise<void>((resolve) => (release = resolve));
	let attempt = 0;
	const failedPath = '/api/v1/health/metrics/sleeps';
	await page.route(
		'**/api/v1/health/metrics/sleeps?start=2026-10-20&end=2026-10-20',
		async (route) => {
			if (++attempt > 1) return route.fallback();
			await delayed;
			return route.fulfill({
				status: 503,
				json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: '수면 복귀 조회 실패' } }
			});
		}
	);
	try {
		const failed = responses(page, '2026-10-20', sleepPaths, failedPath);
		await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
		await expect(page).toHaveURL('/health/sleep/2026-10-20');
		const selector = page.getByRole('combobox', { name: '날짜 선택', exact: true });
		await expect(selector).toHaveValue('2026-10-20');
		await expect(selector).toBeDisabled();
		await expect(page.getByText('수면 상세를 불러오는 중이에요.', { exact: true })).toBeVisible();
		release();
		await failed;
		await expect(selector).toBeEnabled();
		await expect(page.getByRole('alert')).toContainText('수면 복귀 조회 실패');
		await expect(page).toHaveURL('/health/sleep/2026-10-20');
		const recovered = responses(page, '2026-10-20', sleepPaths);
		await page.getByRole('button', { name: '다시 시도', exact: true }).click();
		await recovered;
		await sleepReady(page, '2026-10-20');
		await expect(page.getByRole('alert')).toHaveCount(0);
		// 오류 시 콘텐츠 높이 변화로 scroll clamp가 가능하므로 오류 중 exact scroll은 요구하지 않는다.
		expect(attempt).toBe(2);
	} finally {
		release();
	}
});
