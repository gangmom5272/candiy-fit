import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import {
	heartMetricFixture,
	mockHeartApi,
	openCompletedHeartWeek
} from '../../../../lib/testing/heart-api-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHeartApi(page);
});

test('최근 7일을 요청하고 완료 기록 평균과 오늘 진행 중 데이터를 구분한다', async ({ page }) => {
	const request = page.waitForRequest(
		'**/api/v1/health/metrics/heart_rates?start=2026-10-20&end=2026-10-26'
	);
	await page.goto('/health/heart');
	// 웹 인증은 세션 쿠키라 Authorization header를 보내지 않는다
	expect((await request).headers().authorization).toBeUndefined();
	const chart = page.getByRole('region', { name: '일별 심박수' });
	await expect(chart.getByText('75.64 bpm', { exact: true })).toBeVisible();
	await expect(page.getByText('오늘 기록은 수집 중이며 기간 평균에서 제외해요.')).toBeVisible();
	await expect(chart.locator('[data-heart-point]')).toHaveCount(6);
	await expect(chart.getByRole('link')).toHaveCount(7);
	await expect(page.getByRole('button', { name: '다음 주', exact: true })).toBeDisabled();
	const previous = page.waitForRequest(
		'**/api/v1/health/metrics/heart_rates?start=2026-10-13&end=2026-10-19'
	);
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await previous;
	await expect(chart.locator('[data-period-average]')).toHaveText('76 bpm');
	await page.reload();
	await expect(chart.locator('[data-heart-point]')).toHaveCount(6);
	await expect(page).toHaveURL('/health/heart');
});

for (const [status, code, reason, message] of [
	[403, 'HEALTH_DATA_FORBIDDEN', '', '심박 조회 권한이 없습니다.'],
	[503, 'HEALTH_DATA_UNAVAILABLE', 'DATA_NOT_READY', '심박 데이터를 준비 중입니다.'],
	[503, 'HEALTH_DATA_UNAVAILABLE', 'LOOKUP_FAILED', '심박 데이터를 조회하지 못했습니다.']
] as const) {
	test(`${status} ${reason || code}는 기록 없음 대신 오류를 표시하고 재시도한다`, async ({
		page
	}) => {
		let failed = true;
		await page.route('**/api/v1/health/metrics/heart_rates?*', (route) =>
			failed
				? route.fulfill({
						status,
						json: {
							error: {
								code,
								message,
								...(reason ? { context: { reason, metrics: ['heart_rates'] } } : {})
							}
						}
					})
				: route.fallback()
		);
		await page.goto('/health/heart');
		await expect(page.getByRole('alert')).toContainText(message);
		await expect(page.getByRole('region', { name: '일별 심박수' })).toHaveCount(0);
		await expect(page.getByText('선택한 기간에 심박 기록이 없어요.')).toHaveCount(0);
		failed = false;
		await page.getByRole('button', { name: '다시 시도' }).click();
		await expect(page.locator('[data-heart-point]')).toHaveCount(6);
	});
}

test('느린 이전 응답은 새 기간을 덮지 않으며 loading 중에도 기간을 이동할 수 있다', async ({
	page
}) => {
	let release = () => {};
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.route(
		'**/api/v1/health/metrics/heart_rates?start=2026-10-20&end=2026-10-26',
		async (route) => {
			await gate;
			await route.fallback();
		}
	);
	await page.goto('/health/heart');
	await expect(page.getByText('심박 기록을 불러오는 중이에요.')).toBeVisible();
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(page.locator('[data-period-average]')).toHaveText('76 bpm');
	release();
	await page.waitForLoadState('networkidle');
	await expect(page.locator('[data-heart-point]')).toHaveCount(1);
	await expect(page.locator('[data-period-average]')).toHaveText('76 bpm');
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		'2026.10.13 ~ 10.19'
	);
});

test('nullable 계약의 방어적 표시에서 서버 평균·실제 0·부분 범위 null을 임의 보정하지 않는다', async ({
	page
}) => {
	await page.route(
		'**/api/v1/health/metrics/heart_rates?start=2026-10-19&end=2026-10-25',
		(route) => {
			const data = heartMetricFixture('2026-10-19', '2026-10-25');
			// min/max null은 REST 타입의 방어적 표시 테스트다. 현재 Backend의 정상 적재 결과는 아니다.
			data.days[0].value = 0;
			data.detail.dailyRanges[0] = { date: '2026-10-19', min: null, max: null };
			data.detail.dailyRanges[1].min = null;
			data.detail.dailyRanges[3] = { date: '2026-10-22', min: 25, max: 220 };
			data.summary.avg = 66.92;
			data.summary.dailyMin = { date: '2026-10-19', value: 0 };
			data.detail.measuredMin = { date: '2026-10-22', value: 25 };
			data.detail.measuredMax = { date: '2026-10-22', value: 220 };
			return route.fulfill({ json: { data } });
		}
	);
	await openCompletedHeartWeek(page);
	const chart = page.getByRole('region', { name: '일별 심박수' });
	await expect(chart.locator('[data-period-average]')).toHaveText('66.92 bpm');
	await expect(chart.locator('[data-heart-point="2026-10-19"]')).toHaveText('0');
	await expect(chart.locator('[data-heart-range="2026-10-19"]')).toHaveCount(0);
	await expect(chart.locator('[data-heart-range="2026-10-20"]')).toHaveCount(0);
	await expect(
		chart.getByRole('link', { name: '2026-10-19 하루 상세' })
	).toHaveAccessibleDescription('평균 0 bpm. 최저 기록 없음. 최고 기록 없음.');
	await expect(
		chart.getByRole('link', { name: '2026-10-22 하루 상세' })
	).toHaveAccessibleDescription('평균 78 bpm. 최저 25 bpm. 최고 220 bpm.');
	const contained = await chart.locator('[data-heart-range="2026-10-22"]').evaluate((node) => {
		const range = node.getBoundingClientRect();
		const plot = node.parentElement?.getBoundingClientRect();
		return plot && range.top >= plot.top - 1 && range.bottom <= plot.bottom + 1;
	});
	expect(contained).toBe(true);
});

test('기록 없는 기간도 날짜 링크와 서버 결측 안내를 유지한다', async ({ page }) => {
	await page.goto('/health/heart');
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(page.locator('[data-heart-point]')).toHaveCount(1);
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	const chart = page.getByRole('region', { name: '일별 심박수' });
	await expect(chart.getByRole('status')).toHaveText('선택한 기간에 심박 기록이 없어요.');
	await expect(chart.getByRole('link')).toHaveCount(7);
	await expect(chart.locator('[data-heart-point]')).toHaveCount(0);
	await expect(chart.getByRole('link').first()).toHaveAccessibleDescription('심박 기록이 없어요.');
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(chart.getByRole('link').first()).toHaveAccessibleDescription('연동 전 날짜예요.');
});

for (const code of ['AUTH_EXPIRED', 'AUTH_REQUIRED'] as const) {
	test(`${code}는 갱신·재요청 없이 로그인으로 이동한다`, async ({ page }) => {
		await page.goto('/health/heart');
		await expect(page.locator('[data-heart-point]')).toHaveCount(6);
		await page.route('**/api/v1/health/metrics/heart_rates?*', (route) =>
			route.fulfill({ status: 401, json: { error: { code, message: '로그인이 필요합니다.' } } })
		);
		await page.getByRole('button', { name: '이전 주', exact: true }).click();
		await expect(page).toHaveURL('/login');
	});
}

test('네트워크 실패는 mock 차트를 표시하지 않는다', async ({ page }) => {
	await page.route('**/api/v1/health/metrics/heart_rates?*', (route) => route.abort('failed'));
	await page.goto('/health/heart');
	await expect(page.getByRole('alert')).toContainText('심박 기록을 불러오지 못했어요.');
	await expect(page.locator('[data-heart-point]')).toHaveCount(0);
});

test('최근 7일의 마지막 두 기록을 날짜 위치에 배치하고 오늘 값을 기간 평균에 다시 합산하지 않는다', async ({
	page
}) => {
	await page.clock.setFixedTime(new Date('2026-10-27T03:00:00Z'));
	await page.route(
		'**/api/v1/health/metrics/heart_rates?start=2026-10-21&end=2026-10-27',
		(route) => {
			const data = heartMetricFixture('2026-10-21', '2026-10-27');
			data.days = data.days.map((day) => ({
				...day,
				value: null,
				missing: true,
				reason: 'NO_RECORD',
				isInProgress: false
			}));
			data.days[5] = {
				date: '2026-10-26',
				value: 74.2,
				missing: false,
				reason: null,
				isInProgress: false
			};
			data.days[6] = {
				date: '2026-10-27',
				value: 90,
				missing: false,
				reason: null,
				isInProgress: true
			};
			data.detail.dailyRanges = data.days.map((day) => ({ date: day.date, min: null, max: null }));
			data.detail.dailyRanges[5] = { date: '2026-10-26', min: 52, max: 118 };
			data.detail.dailyRanges[6] = { date: '2026-10-27', min: 60, max: 130 };
			data.summary = {
				...data.summary,
				avg: 74.2,
				daysWithData: 1,
				daysUsedForSummary: 1,
				completedDays: 6,
				missingDays: data.days.slice(0, 5).map((day) => day.date),
				dailyMin: { date: '2026-10-26', value: 74.2 },
				dailyMax: { date: '2026-10-26', value: 74.2 }
			};
			data.detail.measuredMin = { date: '2026-10-26', value: 52 };
			data.detail.measuredMax = { date: '2026-10-26', value: 118 };
			data.inProgress = {
				date: '2026-10-27',
				value: 90,
				missing: false,
				asOf: '2026-10-27T03:00:00Z'
			};
			data.dataStatus = {
				lastDataAt: '2026-10-27T03:00:00Z',
				syncedUntil: '2026-10-27T03:00:00Z',
				lastSuccessAt: '2026-10-27T03:05:00Z',
				lastSyncStatus: 'success',
				checkedAt: '2026-10-27T03:06:00Z'
			};
			return route.fulfill({ json: { data } });
		}
	);
	await page.goto('/health/heart');
	const chart = page.getByRole('region', { name: '일별 심박수' });
	await expect(chart.locator('[data-period-average]')).toHaveText('74.2 bpm');
	await expect(chart.locator('[data-heart-point]')).toHaveCount(2);
	await expect(chart.getByRole('link')).toHaveCount(7);
	for (const [index, date] of ['2026-10-26', '2026-10-27'].entries()) {
		const coordinates = await chart.evaluate((element, date) => {
			const point = element.querySelector<HTMLElement>(`[data-heart-point="${date}"]`);
			const range = element.querySelector<HTMLElement>(`[data-heart-range="${date}"]`);
			const label = element.querySelector<HTMLElement>(`a[aria-label="${date} 하루 상세"]`);
			return [point, range, label].map((node) => Number.parseFloat(node?.style.left ?? 'NaN'));
		}, date);
		for (const x of coordinates) expect(x).toBeCloseTo(((index + 5.5) / 7) * 100);
	}
});
