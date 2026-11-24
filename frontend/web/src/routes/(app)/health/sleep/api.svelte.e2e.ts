import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import { mockSleepApi, sleepMetricFixture } from '../../../../lib/testing/sleep-api-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockSleepApi(page);
});

test('현재 주를 조회하고 서버 평균을 표시하며 미래 날짜와 mock fallback을 만들지 않는다', async ({
	page
}) => {
	await page.route('**/api/v1/health/metrics/sleeps?start=2026-10-20&end=2026-10-26', (route) => {
		const data = sleepMetricFixture('sleeps', '2026-10-20', '2026-10-26');
		data.days[6] = { ...data.days[6], value: 411, missing: false, reason: null };
		const night = data.detail.nights.find((night) => night.date === '2026-10-26');
		if (!night) throw new Error('수면 기록 없음');
		night.sleepMinutes = 411;
		data.summary.avg = 411;
		data.summary.daysWithData = data.summary.daysUsedForSummary = 1;
		data.summary.dailyMin = data.summary.dailyMax = { date: '2026-10-26', value: 411 };
		data.summary.missingDays = data.summary.unsyncedDays = [];
		return route.fulfill({ json: { data } });
	});
	await page.goto('/health/sleep');
	await expect(
		page.getByRole('region', { name: '수면 기록 요약' }).getByText('6시간 51분', { exact: true })
	).toBeVisible();
	await expect(page.getByRole('button', { name: '다음 주', exact: true })).toBeDisabled();
	await expect(
		page.getByRole('region', { name: '주간 수면 흐름' }).getByRole('link', { name: / 수면 상세$/ })
	).toHaveCount(7);
	const request = page.waitForRequest(
		'**/api/v1/health/metrics/sleeps?start=2026-10-13&end=2026-10-19'
	);
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	// 웹 인증은 세션 쿠키라 Authorization header를 보내지 않는다
	expect((await request).headers().authorization).toBeUndefined();
});

test('산소포화도 준비 전 503은 오류로 알리고 수면은 표시하며 재시도한다', async ({ page }) => {
	let fail = true;
	await page.route('**/api/v1/health/metrics/oxygen_saturations?*', (route) =>
		fail
			? route.fulfill({
					status: 503,
					json: {
						error: {
							code: 'HEALTH_DATA_UNAVAILABLE',
							message: '산소포화도를 준비 중입니다.',
							context: { reason: 'DATA_NOT_READY' }
						}
					}
				})
			: route.fallback()
	);
	await page.goto('/health/sleep');
	await expect(page.getByRole('alert')).toContainText('산소포화도를 준비 중입니다.');
	await expect(page.getByRole('heading', { name: '주간 수면 흐름' })).toBeVisible();
	await expect(page.getByText('선택한 기간에 수면 중 산소포화도 기록이 없어요.')).toHaveCount(0);
	fail = false;
	await page.getByRole('button', { name: '다시 시도' }).click();
	await expect(page.getByRole('heading', { name: '수면 중 산소포화도' })).toBeVisible();
});

test('이전 기간의 늦은 응답은 현재 기간을 덮지 않는다', async ({ page }) => {
	let release = () => {};
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.route(
		'**/api/v1/health/metrics/sleeps?start=2026-10-20&end=2026-10-26',
		async (route) => {
			await gate;
			await route.fulfill({
				json: { data: sleepMetricFixture('sleeps', '2026-10-26', '2026-10-26') }
			});
		}
	);
	await page.goto('/health/sleep');
	await expect(page.getByText('수면 기록을 불러오는 중이에요.')).toBeVisible();
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(
		page.getByRole('region', { name: '주간 수면 흐름' }).getByText('비교할 기록 없음')
	).toBeVisible();
	release();
	await page.waitForLoadState('networkidle');
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		'2026.10.13 ~ 10.19'
	);
	await expect(
		page.getByRole('region', { name: '주간 수면 흐름' }).getByRole('link', { name: / 수면 상세$/ })
	).toHaveCount(7);
});

test('빠른 상세 날짜 변경과 timeline 실패가 정상 요약을 덮거나 빈 단계로 위장하지 않는다', async ({
	page
}) => {
	let release = () => {};
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.goto('/health/sleep/2026-10-23');
	await expect(page.getByRole('region', { name: '수면 요약', exact: true })).toBeVisible();
	await page.route('**/api/v1/health/timeline?start=2026-10-20&end=2026-10-20', async (route) => {
		await gate;
		await route.fallback();
	});
	await page.getByLabel('날짜 선택').selectOption('2026-10-20');
	await expect(page.getByText('수면 상세를 불러오는 중이에요.')).toBeVisible();
	await page.goBack();
	await expect(page.getByRole('region', { name: '수면 요약', exact: true })).toBeVisible();
	release();
	await page.waitForLoadState('networkidle');
	await expect(page.getByRole('heading', { name: '10월 23일 수면 상세' })).toBeVisible();
	await expect(page.getByRole('region', { name: '수면 요약', exact: true })).toBeVisible();
	await page.route('**/api/v1/health/timeline?*', (route) =>
		route.fulfill({
			status: 503,
			json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: '수면 단계 조회 실패' } }
		})
	);
	await page.getByLabel('날짜 선택').selectOption('2026-10-24');
	await expect(page.getByRole('alert')).toContainText('수면 단계 조회 실패');
	await expect(page.getByRole('region', { name: '수면 요약', exact: true })).toBeVisible();
	await expect(page.getByText('해당 날짜의 수면 단계 기록이 없어요.')).toHaveCount(0);
});

test('수면 지표만 503이어도 정상 산소포화도와 실제 timeline은 유지한다', async ({ page }) => {
	await page.route('**/api/v1/health/metrics/sleeps?*', (route) =>
		route.fulfill({
			status: 503,
			json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: '수면 지표 조회 실패' } }
		})
	);
	await page.goto('/health/sleep/2026-10-23');
	await expect(page.getByRole('alert')).toContainText('수면 지표 조회 실패');
	await expect(page.getByText(/수면 중 평균 산소포화도:/)).toBeVisible();
	await expect(page.getByRole('figure', { name: '시간대별 수면 단계' })).toBeVisible();
	await expect(page.getByRole('region', { name: '수면 요약', exact: true })).toHaveCount(0);
	await expect(page.getByText('해당 날짜의 수면 기록이 없어요.')).toHaveCount(0);
});
