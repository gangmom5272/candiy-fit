import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../../lib/testing/e2e-auth';
import {
	dayMetricFixture,
	dayTimelineFixture,
	mockDayDetailApi
} from '../../../../../lib/testing/day-detail-api-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockDayDetailApi(page);
});

for (const width of [360, 390, 1366]) {
	for (const [bedtime, midnightVisible, rangeStart] of [
		['20:59:00', true, -181],
		['21:00:00', true, -180],
		['21:01:00', false, -179],
		['21:00:01', false, -179.98333333333332]
	] as const) {
		test(`${width}px ${bedtime} 취침의 다음 자정 라벨과 원본 시간 좌표를 보존한다`, async ({
			page
		}) => {
			await page.setViewportSize({ width, height: 900 });
			const data = dayTimelineFixture('2026-10-20');
			const day = data.days[0];
			day.mainSleep = {
				startAt: `2026-10-19T${bedtime}+09:00`,
				endAt: '2026-10-20T07:00:00+09:00'
			};
			day.sleepSegments = [{ ...day.mainSleep, stage: 'light' }];
			await page.route('**/api/v1/health/timeline?*', (route) => route.fulfill({ json: { data } }));
			await page.goto('/health/day/2026-10-20');
			const timeline = page.getByRole('region', { name: '나의 하루', exact: true });
			await expect(timeline).toBeVisible();
			await page.evaluate(() => document.fonts.ready);
			await expect(timeline.locator('[data-time-minute="0"]')).toHaveCSS(
				'visibility',
				midnightVisible ? 'visible' : 'hidden'
			);
			await expect(timeline.locator('[data-time-minute="1440"]')).toHaveCSS(
				'visibility',
				'visible'
			);
			await expect(timeline.locator('[data-grid-minute="0"]')).toHaveCount(1);
			await expect(timeline.locator('[data-range-start]')).toHaveAttribute(
				'data-range-start',
				String(rangeStart)
			);
			await expect(timeline.locator('[data-sleep-start]')).toHaveAttribute(
				'data-sleep-start',
				String(rangeStart)
			);
			await expect(timeline.locator('[data-step-hour]')).toHaveCount(24);
			const bar = timeline.locator('[data-step-hour="3"]');
			await expect(bar).toHaveAttribute('data-step-value', String(day.hourlySteps?.[3]));
			const center = await bar.evaluate(
				(bar) => Number(bar.getAttribute('x')) + Number(bar.getAttribute('width')) / 2
			);
			expect(center).toBeCloseTo(((210 - rangeStart) / (1440 - rangeStart)) * 100);
			await timeline.locator('[data-sleep-start]').focus();
			await expect(page.getByRole('tooltip')).toContainText(`${bedtime.slice(0, 5)}~07:00`);
			await expect(page.getByRole('tooltip')).toContainText('2026.10.19');
			await expect(page.getByRole('tooltip')).toContainText('2026.10.20');
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
		});
	}
}

test('실제 일별 source를 표시하고 날짜 이동과 캘린더 선택은 해당 날짜 API를 재조회한다', async ({
	page
}) => {
	const paths: string[] = [];
	page.on('request', (request) => {
		if (request.method() === 'GET' && request.url().includes('/api/v1/health/'))
			paths.push(new URL(request.url()).pathname + new URL(request.url()).search);
	});
	await page.goto('/health/day/2026-10-19');
	const values = page.locator('dl[aria-label="하루 기록 요약"] dd');
	await expect(values).toHaveText(['8,120보', '60분', '6시간 16분', '76 bpm']);
	expect(paths).toHaveLength(5);
	expect(paths.every((path) => path.includes('start=2026-10-19&end=2026-10-19'))).toBe(true);
	await page.getByRole('link', { name: '다음 날짜', exact: true }).click();
	await expect(values).toHaveText(['7,680보', '38분', '6시간 39분', '73 bpm']);
	await page.getByRole('button', { name: '날짜 선택, 2026.10.20' }).click();
	await page
		.getByRole('dialog', { name: '날짜 선택 캘린더' })
		.getByRole('link', { name: '2026-10-23', exact: true })
		.click();
	await expect(page).toHaveURL('/health/day/2026-10-23');
	await expect(values).toHaveCount(4);
	await expect(page.getByRole('region', { name: '나의 하루' })).toBeVisible();
	await expect
		.poll(() => paths.filter((path) => path.includes('start=2026-10-23&end=2026-10-23')).length)
		.toBe(5);
	await page.reload();
	await expect(values).toHaveCount(4);
	await expect(page).toHaveURL('/health/day/2026-10-23');
});

for (const [status, code, reason] of [
	[403, 'HEALTH_DATA_FORBIDDEN', ''],
	[503, 'HEALTH_DATA_UNAVAILABLE', 'DATA_NOT_READY'],
	[503, 'HEALTH_DATA_UNAVAILABLE', 'LOOKUP_FAILED']
] as const) {
	test(`${status} ${reason || code} 실패 지표만 구분하고 정상 카드와 timeline을 유지한다`, async ({
		page
	}) => {
		let failed = true;
		await page.route('**/api/v1/health/metrics/steps?*', (route) =>
			failed
				? route.fulfill({
						status,
						json: {
							error: {
								code,
								message: '걸음 조회 실패',
								...(reason ? { context: { reason, metrics: ['steps'] } } : {})
							}
						}
					})
				: route.fallback()
		);
		await page.goto('/health/day/2026-10-19');
		const values = page.locator('dl[aria-label="하루 기록 요약"] dd');
		await expect(values).toHaveText(['조회 실패', '60분', '6시간 16분', '76 bpm']);
		await expect(page.getByRole('alert')).toContainText('걸음 조회 실패');
		await expect(page.locator('[data-heart-point]')).not.toHaveCount(0);
		failed = false;
		await page.getByRole('button', { name: '다시 시도' }).click();
		await expect(values.first()).toHaveText('8,120보');
		await expect(page.getByRole('alert')).toHaveCount(0);
	});
}

test('timeline 실패를 빈 기록으로 표시하지 않고 정상 요약은 유지한다', async ({ page }) => {
	await page.route('**/api/v1/health/timeline?*', (route) =>
		route.fulfill({
			status: 503,
			json: {
				error: {
					code: 'HEALTH_DATA_UNAVAILABLE',
					message: '시간대 조회 실패',
					context: {
						reason: 'LOOKUP_FAILED',
						metrics: ['steps', 'heart_rates', 'sleeps', 'exercises']
					}
				}
			}
		})
	);
	await page.goto('/health/day/2026-10-19');
	await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
		'8,120보',
		'60분',
		'6시간 16분',
		'76 bpm'
	]);
	await expect(page.getByRole('alert')).toContainText('시간대 조회 실패');
	await expect(page.getByRole('region', { name: '나의 하루' })).toHaveCount(0);
});

test('지표 API가 실패해도 정상 timeline은 독립적으로 표시한다', async ({ page }) => {
	await page.route('**/api/v1/health/metrics/*?*', (route) => route.abort('failed'));
	await page.goto('/health/day/2026-10-25');
	await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
		'조회 실패',
		'조회 실패',
		'조회 실패',
		'조회 실패'
	]);
	await expect(page.locator('[data-range-start]')).toHaveAttribute('data-range-start', '-148');
	await expect(page.getByRole('alert')).toBeVisible();
});

test('느린 날짜 응답과 페이지 이탈은 최신 날짜의 값을 덮지 않는다', async ({ page }) => {
	let release = () => {};
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.route(
		'**/api/v1/health/metrics/steps?start=2026-10-19&end=2026-10-19',
		async (route) => {
			await gate;
			await route.fallback();
		}
	);
	await page.goto('/health/day/2026-10-19');
	await expect(page.getByText('하루 기록을 불러오는 중이에요.')).toBeVisible();
	await page.getByRole('link', { name: '다음 날짜', exact: true }).click();
	await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
		'7,680보',
		'38분',
		'6시간 39분',
		'73 bpm'
	]);
	release();
	await page.waitForLoadState('networkidle');
	await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
		'7,680보',
		'38분',
		'6시간 39분',
		'73 bpm'
	]);
	let releaseLeaving = () => {};
	const leavingGate = new Promise<void>((resolve) => {
		releaseLeaving = resolve;
	});
	await page.route(
		'**/api/v1/health/metrics/steps?start=2026-10-21&end=2026-10-21',
		async (route) => {
			await leavingGate;
			await route.fallback();
		}
	);
	await page.getByRole('link', { name: '다음 날짜', exact: true }).click();
	await expect(page.getByText('하루 기록을 불러오는 중이에요.')).toBeVisible();
	const cancelled = page.waitForEvent('requestfailed', {
		predicate: (request) => request.url().includes('/metrics/steps?start=2026-10-21&end=2026-10-21')
	});
	await page.getByRole('link', { name: '건강 데이터로', exact: true }).click();
	await expect(page).toHaveURL('/health');
	releaseLeaving();
	expect((await cancelled).failure()).not.toBeNull();
	await page.waitForLoadState('networkidle');
	await expect(page.getByRole('heading', { level: 1, name: '건강 데이터' })).toBeVisible();
	await expect(page.locator('dl[aria-label="하루 기록 요약"]')).toHaveCount(0);
});

test('오늘 카드는 null summary 대신 일별 값과 sleepMinutes null을 보존하고 시간대별 합산으로 보정하지 않는다', async ({
	page
}) => {
	await page.route('**/api/v1/health/metrics/steps?*', (route) => {
		const data = dayMetricFixture('steps', '2026-10-26');
		data.days[0] = { ...data.days[0], value: 0, missing: true, reason: 'UNSYNCED' };
		data.inProgress = {
			date: '2026-10-26',
			value: 0,
			missing: true,
			asOf: data.dataStatus.lastDataAt
		};
		data.summary.unsyncedDays = ['2026-10-26'];
		data.summary.missingDays = [];
		return route.fulfill({ json: { data } });
	});
	await page.goto('/health/day/2026-10-26');
	await expect(page.locator('dl[aria-label="하루 기록 요약"] dd')).toHaveText([
		'동기화 대기',
		'동기화 대기',
		'동기화 대기',
		'74.2 bpm'
	]);
	await expect(page.getByRole('button', { name: '다음 날짜' })).toBeDisabled();
});

test('server가 선정한 원본 구간과 시간대 값을 임의 생성하거나 연결하지 않는다', async ({
	page
}) => {
	await page.route('**/api/v1/health/timeline?*', (route) => {
		const data = dayTimelineFixture('2026-10-25');
		data.days[0].hourlyHeartRates = Array.from({ length: 24 }, (_, hour) =>
			hour === 3
				? { avg: 70, min: 54, max: 100 }
				: hour === 5
					? { avg: 81.6, min: 54, max: 131 }
					: null
		);
		data.days[0].hourlySteps = Array(24).fill(0);
		data.days[0].hourlySteps[1] = 7760;
		return route.fulfill({ json: { data } });
	});
	await page.goto('/health/day/2026-10-25');
	await expect(page.locator('[data-range-start]')).toHaveAttribute('data-range-start', '-148');
	await expect(page.locator('[data-heart-point]')).toHaveCount(2);
	await expect(page.locator('[data-heart-segment]')).toHaveCount(0);
	await expect(page.locator('[data-step-hour]')).toHaveCount(24);
	await expect(page.locator('[data-step-trigger="0"]')).toHaveAccessibleName(
		'00:00 ~ 01:00 걸음 수 0보 상세'
	);
	await expect(page.locator('dl[aria-label="하루 기록 요약"] dd').first()).toHaveText('7,760보');
});

test('인증 거절은 기존 AuthGate로 로그인 화면에 이동한다', async ({ page }) => {
	await page.route('**/api/v1/health/metrics/steps?*', (route) =>
		route.fulfill({
			status: 401,
			json: { error: { code: 'AUTH_REQUIRED', message: '로그인이 필요합니다.' } }
		})
	);
	await page.goto('/health/day/2026-10-19');
	await expect(page).toHaveURL('/login');
});
