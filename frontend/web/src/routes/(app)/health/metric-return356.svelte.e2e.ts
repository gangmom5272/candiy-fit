import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockActivityApi } from '../../../lib/testing/activity-api-fixture';
import { mockSleepApi } from '../../../lib/testing/sleep-api-fixture';
import { mockHeartApi } from '../../../lib/testing/heart-api-fixture';
import { mockDayDetailApi } from '../../../lib/testing/day-detail-api-fixture';

type Source = 'activity' | 'heart';
const currentPeriod = '2026.10.26 ~ 11.01';
const completedPeriod = '2026.10.19 ~ 10.25';
const apiPrefix = '/api/v1/health/';

async function installApi(page: Page) {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await mockHeartApi(page);
	await mockDayDetailApi(page);
	await page.clock.setFixedTime(new Date('2026-11-01T03:00:00Z'));
}

function responses(page: Page, entries: [string, string, string][]) {
	return Promise.all(
		entries.map(([path, start, end]) =>
			page.waitForResponse((response) => {
				const url = new URL(response.url());
				return (
					response.request().method() === 'GET' &&
					response.status() === 200 &&
					url.pathname === apiPrefix + path &&
					url.searchParams.get('start') === start &&
					url.searchParams.get('end') === end
				);
			})
		)
	).then((items) => Promise.all(items.map((response) => response.finished())));
}

function weekResponses(page: Page, source: Source, completed: boolean) {
	const [start, end] = completed ? ['2026-10-19', '2026-10-25'] : ['2026-10-26', '2026-11-01'];
	return responses(
		page,
		source === 'heart'
			? [['metrics/heart_rates', start, end]]
			: [
					['metrics/steps', start, end],
					['metrics/exercises', start, end],
					[
						'metrics/steps',
						completed ? '2026-10-12' : '2026-10-19',
						completed ? '2026-10-18' : '2026-10-25'
					],
					['metrics/steps', '2026-10-05', '2026-11-01'],
					['timeline', start, end]
				]
	);
}

function dayResponses(page: Page, date: string) {
	return responses(
		page,
		['metrics/steps', 'metrics/exercises', 'metrics/sleeps', 'metrics/heart_rates', 'timeline'].map(
			(path) => [path, date, date]
		)
	);
}

async function metricReady(page: Page, source: Source, completed: boolean, exercise = false) {
	await expect(page).toHaveURL(`/health/${source}${exercise ? '#exercise' : ''}`);
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		completed ? completedPeriod : currentPeriod
	);
	await expect(page.locator('[data-health-loading]')).toHaveCount(0);
	await expect(
		page
			.getByRole('navigation', { name: '건강 데이터', exact: true })
			.locator('[aria-current="page"]')
	).toHaveText(source === 'activity' ? '활동' : '심박');
	await expect(
		source === 'activity'
			? page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true })
			: page.getByRole('region', { name: '일별 심박수', exact: true })
	).toBeVisible();
}

async function openMetric(page: Page, source: Source, completed = true, exercise = false) {
	const initial = weekResponses(page, source, false);
	await page.goto(`/health/${source}${exercise ? '#exercise' : ''}`);
	await initial;
	await metricReady(page, source, false, exercise);
	if (completed) {
		const previous = weekResponses(page, source, true);
		await page.getByRole('button', { name: '이전 주', exact: true }).click();
		await previous;
		await metricReady(page, source, true, exercise);
	}
}

async function dayReady(page: Page, date: string, returnHref: string) {
	await expect(page).toHaveURL(`/health/day/${date}`);
	const [, month, day] = date.split('-').map(Number);
	await expect(
		page.getByRole('heading', { name: `${month}월 ${day}일 하루 상세`, exact: true })
	).toBeVisible();
	await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveCount(4);
	await expect(page.locator('[data-health-loading]')).toHaveCount(0);
	await expect(page.getByRole('link', { name: '건강 데이터로', exact: true })).toHaveAttribute(
		'href',
		returnHref
	);
}

async function enterDay(
	page: Page,
	target: Locator,
	date: string,
	returnHref: string,
	input: 'mouse' | 'touch' | 'keyboard' = 'mouse'
) {
	await expect(target).toHaveAttribute('href', `/health/day/${date}`);
	const loaded = dayResponses(page, date);
	if (input === 'touch') await target.tap();
	else if (input === 'keyboard') {
		await target.focus();
		await page.keyboard.press('Enter');
	} else await target.click();
	await loaded;
	await dayReady(page, date, returnHref);
}

async function returnToMetric(page: Page, source: Source, completed: boolean, exercise = false) {
	const loaded = weekResponses(page, source, completed);
	await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
	await loaded;
	await metricReady(page, source, completed, exercise);
}

test.beforeEach(async ({ page }) => {
	await installApi(page);
	await page.setViewportSize({ width: 1366, height: 768 });
});

test('활동의 기존 걸음 막대에서 연 하루 상세는 날짜를 바꿔도 출발 활동·완료기간으로 앱 복귀한다', async ({
	page
}) => {
	await openMetric(page, 'activity');
	await enterDay(
		page,
		page
			.getByRole('figure', { name: '일별 걸음 수 차트', exact: true })
			.locator('svg a[data-chart-point="0"]'),
		'2026-10-19',
		'/health/activity'
	);
	let loaded = dayResponses(page, '2026-10-20');
	await page.getByRole('link', { name: '다음 날짜', exact: true }).click();
	await loaded;
	await dayReady(page, '2026-10-20', '/health/activity');
	await page.getByRole('button', { name: '날짜 선택, 2026.10.20', exact: true }).click();
	loaded = dayResponses(page, '2026-10-23');
	await page
		.getByRole('dialog', { name: '날짜 선택 캘린더' })
		.getByRole('link', { name: '2026-10-23', exact: true })
		.click();
	await loaded;
	await dayReady(page, '2026-10-23', '/health/activity');
	await returnToMetric(page, 'activity', true);
	await expect(page.getByRole('link', { name: /^기록 없음.*10\/21/ })).toHaveAttribute(
		'href',
		'/health/day/2026-10-21'
	);
});

test.describe('운동 날짜 터치 복귀', () => {
	test.use({ hasTouch: true });
	test('360px 실제 운동 날짜 링크는 하루 상세 이후 원래 운동 영역·기간으로 돌아온다', async ({
		page
	}) => {
		await page.setViewportSize({ width: 360, height: 800 });
		await openMetric(page, 'activity', true, true);
		const exercise = page.getByRole('region', { name: '운동 시간', exact: true });
		await enterDay(
			page,
			exercise.getByRole('link', { name: '2026-10-21 하루 상세 보기', exact: true }),
			'2026-10-21',
			'/health/activity#exercise',
			'touch'
		);
		await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
			'기록 없음',
			'0분',
			'6시간 26분',
			'기록 없음'
		]);
		const loaded = dayResponses(page, '2026-10-20');
		await page.getByRole('link', { name: '이전 날짜', exact: true }).click();
		await loaded;
		await dayReady(page, '2026-10-20', '/health/activity#exercise');
		await returnToMetric(page, 'activity', true, true);
		await expect(page.locator('#exercise')).toBeInViewport();
	});
});

test('심박의 기존 결측 날짜 Enter는 하루 상세 이후 출발 심박·완료기간으로 복귀한다', async ({
	page
}) => {
	await openMetric(page, 'heart');
	const chart = page.getByRole('region', { name: '일별 심박수', exact: true });
	const target = chart.getByRole('link', { name: '2026-10-21 하루 상세', exact: true });
	await expect(target).toHaveAccessibleDescription('심박 기록이 없어요.');
	await enterDay(page, target, '2026-10-21', '/health/heart', 'keyboard');
	const loaded = dayResponses(page, '2026-10-22');
	await page.getByRole('link', { name: '다음 날짜', exact: true }).click();
	await loaded;
	await dayReady(page, '2026-10-22', '/health/heart');
	await returnToMetric(page, 'heart', true);
	await expect(chart.getByRole('link')).toHaveCount(7);
	await expect(chart.locator('[data-heart-point]')).toHaveCount(6);
});

test('현재 주 심박의 복귀기간은 과거 day 선택에도 유지하고 새 탭 Day에는 출발 문맥을 넘기지 않는다', async ({
	page,
	context
}) => {
	await openMetric(page, 'heart', false);
	await enterDay(
		page,
		page.getByRole('link', { name: '2026-10-26 하루 상세', exact: true }),
		'2026-10-26',
		'/health/heart'
	);
	const separate = await context.newPage();
	try {
		await installApi(separate);
		const loaded = dayResponses(separate, '2026-10-26');
		await separate.goto('/health/day/2026-10-26');
		await loaded;
		await dayReady(separate, '2026-10-26', '/health');
	} finally {
		await separate.close();
	}
	await page.getByRole('button', { name: '날짜 선택, 2026.10.26', exact: true }).click();
	const changed = dayResponses(page, '2026-10-20');
	await page
		.getByRole('dialog', { name: '날짜 선택 캘린더' })
		.getByRole('link', { name: '2026-10-20', exact: true })
		.click();
	await changed;
	await dayReady(page, '2026-10-20', '/health/heart');
	await returnToMetric(page, 'heart', false);
	await expect(page.getByRole('button', { name: '다음 주', exact: true })).toBeDisabled();
});
