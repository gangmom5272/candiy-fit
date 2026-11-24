import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import {
	activityMetricFixture,
	openCompletedActivityWeek,
	mockActivityApi
} from '../../../../lib/testing/activity-api-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockActivityApi(page);
});

test('운동 시간과 칼로리는 총합·평균·일별 최고를 같은 정보 위계로 표시한다', async ({ page }) => {
	await page.route('**/api/v1/health/metrics/exercises?*', (route) => {
		const url = new URL(route.request().url());
		const data = activityMetricFixture(
			'exercises',
			url.searchParams.get('start') ?? '',
			url.searchParams.get('end') ?? ''
		);
		const session = data.detail.sessions.find((item) => item.countsAsExercise);
		if (!session) return route.fulfill({ json: { data } });
		data.summary.avg = 42.5;
		data.summary.total = 85;
		data.summary.daysWithData = 2;
		data.days = data.days.map((day, index) => ({
			...day,
			value: index === 0 ? 45 : index === 1 ? 40 : null,
			missing: index > 1,
			reason: index > 1 ? 'NO_RECORD' : null
		}));
		data.detail.sessions = [
			{ day: 0, minutes: 20, calories: 100 },
			{ day: 0, minutes: 25, calories: 150 },
			{ day: 1, minutes: 40, calories: 200 }
		].map((value, index) => {
			const date = data.days[value.day].date;
			const startAt = `${date}T0${index + 1}:00:00Z`;
			return {
				...session,
				recordId: `time-maximum-${index}`,
				date,
				startAt,
				endAt: new Date(Date.parse(startAt) + value.minutes * 60000).toISOString(),
				durationMinutes: value.minutes,
				caloriesKcal: value.calories
			};
		});
		return route.fulfill({ json: { data } });
	});
	for (const width of [360, 1366]) {
		await page.setViewportSize({ width, height: 844 });
		await openCompletedActivityWeek(page);
		const time = page.getByRole('region', { name: '운동 시간', exact: true });
		const calories = page.getByRole('region', { name: '소모 칼로리', exact: true });
		for (const [card, total, average, maximum] of [
			[time, '총 1시간 25분', '평균 43분', '최고 45분'],
			[calories, '총 450 kcal', '평균 225 kcal', '최고 250 kcal']
		] as const) {
			await expect(card.locator(':scope > div:first-child > p')).toHaveText(total);
			await expect(card.locator(':scope > p').nth(0)).toHaveText(average);
			await expect(card.locator(':scope > p').nth(1)).toHaveText(maximum);
			await expect(card.locator(':scope > p').nth(1)).toBeVisible();
		}
		const styles = await page
			.getByRole('region', { name: /^(운동 시간|소모 칼로리)$/ })
			.evaluateAll((cards) =>
				cards.map((card) =>
					Array.from(card.querySelectorAll(':scope > div:first-child > p, :scope > p')).map(
						(element) => {
							const style = getComputedStyle(element);
							return [
								style.color,
								style.fontSize,
								style.fontWeight,
								style.lineHeight,
								style.marginTop
							];
						}
					)
				)
			);
		expect(styles[0]).toEqual(styles[1]);
		await time.locator('[data-chart-point="0"]').focus();
		await expect(page.getByRole('tooltip').locator('strong')).toHaveText('0시간 45분');
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
	}
});

for (const width of [360, 390, 768, 1366]) {
	test.describe(`${width}px 운동 심박 표시`, () => {
		test.use({ hasTouch: width < 768 });
		test('툴팁은 평균 오차만 정리하고 원본 좌표와 소수 정밀도를 유지한다', async ({ page }) => {
			await page.setViewportSize({ width, height: 844 });
			await page.route('**/api/v1/health/metrics/exercises?*', (route) => {
				const url = new URL(route.request().url());
				const data = activityMetricFixture(
					'exercises',
					url.searchParams.get('start') ?? '',
					url.searchParams.get('end') ?? ''
				);
				const session = data.detail.sessions.find((item) => item.countsAsExercise);
				if (!session) return route.fulfill({ json: { data } });
				data.detail.sessions = [70.2, 70.4, 72.55, 70, null].map((avgHeartRate, index) => {
					const date = data.days[Math.max(0, index - 1)].date;
					const startAt = `${date}T${index % 2 ? '04' : '03'}:00:00Z`;
					return {
						...session,
						recordId: `heart-precision-${index}`,
						date,
						startAt,
						endAt: new Date(Date.parse(startAt) + session.durationMinutes * 60000).toISOString(),
						avgHeartRate
					};
				});
				return route.fulfill({ json: { data } });
			});
			await openCompletedActivityWeek(page);
			const card = page.getByRole('region', { name: '운동 중 심박수', exact: true });
			await expect(card.getByText('평균 71 bpm', { exact: true })).toBeVisible();
			for (const [index, label] of ['70.3 bpm', '72.55 bpm', '70 bpm', '—'].entries()) {
				const point = card.locator(`[data-chart-point="${index}"]`);
				if (width < 768) await point.tap();
				else await point.focus();
				const tooltip = page.getByRole('tooltip');
				await expect(tooltip.locator('strong')).toHaveText(label);
				const bounds = await tooltip.boundingBox();
				if (!bounds) throw new Error('심박 툴팁의 위치를 확인할 수 없습니다.');
				expect(bounds.x).toBeGreaterThanOrEqual(0);
				expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
				await page.keyboard.press('Escape');
			}
			expect(
				await card
					.locator('polyline')
					.evaluate((line) => Number(line.getAttribute('points')?.split(' ')[0].split(',')[1]))
			).toBeCloseTo(58 - ((70.2 + 70.4) / 2 / 72.55) * 40, 12);
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
		});
	});
}

test('서버 평균의 작은 증감은 중립이며 유효한 증감과 이전 0·null 정책은 유지한다', async ({
	page
}) => {
	let current: number | null = 3000;
	let previous: number | null = 3001;
	await page.route('**/api/v1/health/metrics/steps?*', (route) => {
		const url = new URL(route.request().url());
		const data = activityMetricFixture(
			'steps',
			url.searchParams.get('start') ?? '',
			url.searchParams.get('end') ?? ''
		);
		data.summary.avg = data.period.start === '2026-10-12' ? previous : current;
		return route.fulfill({ json: { data } });
	});
	for (const [average, baseline, label, color] of [
		[3000, 3001, '0.0%', 'rgb(44, 46, 53)'],
		[3001, 3000, '0.0%', 'rgb(44, 46, 53)'],
		[3000, 3000, '0.0%', 'rgb(44, 46, 53)'],
		[90, 100, '-10.0%', 'rgb(44, 46, 53)'],
		[110, 100, '+10.0%', 'rgb(44, 46, 53)'],
		[3000, 0, '—', 'rgb(44, 46, 53)'],
		[3000, null, '—', 'rgb(44, 46, 53)'],
		[null, 3000, '—', 'rgb(44, 46, 53)']
	] as const) {
		current = average;
		previous = baseline;
		await openCompletedActivityWeek(page);
		const change = page
			.getByRole('region', { name: '걸음 수 기록' })
			.getByText(label, { exact: true });
		await expect(change).toHaveCSS('color', color);
		await expect(page.getByText('-0.0%', { exact: true })).toHaveCount(0);
	}
});

test('소수 칼로리는 표시만 한 자리로 반올림하고 원본 좌표와 심박 툴팁을 유지한다', async ({
	page
}) => {
	const values = [123.45000000000002, 85.04, 85.06, 100, 0.05, 0, null];
	const labels = ['123.5', '85', '85.1', '100', '0.1', '0', null];
	await page.route('**/api/v1/health/metrics/exercises?*', (route) => {
		const url = new URL(route.request().url());
		const data = activityMetricFixture(
			'exercises',
			url.searchParams.get('start') ?? '',
			url.searchParams.get('end') ?? ''
		);
		const session = data.detail.sessions.find((item) => item.countsAsExercise);
		if (!session) return route.fulfill({ json: { data } });
		data.detail.sessions = values.map((value, index) => {
			const date = data.days[index].date;
			const startAt = `${date}T03:00:00Z`;
			return {
				...session,
				recordId: `decimal-${index}`,
				date,
				startAt,
				endAt: new Date(Date.parse(startAt) + session.durationMinutes * 60000).toISOString(),
				caloriesKcal: value,
				avgHeartRate: 72.55
			};
		});
		return route.fulfill({ json: { data } });
	});
	for (const width of [360, 1366]) {
		await page.setViewportSize({ width, height: 800 });
		await openCompletedActivityWeek(page);
		const card = page.getByRole('region', { name: '소모 칼로리', exact: true });
		await expect(card.getByText('평균 66 kcal', { exact: true })).toBeVisible();
		await expect(card.locator(':scope > div:first-child > p')).toHaveText('총 393.6 kcal');
		await expect(card.locator(':scope > p').nth(1)).toHaveText('최고 123.5 kcal');
		const max = 123.45000000000002;
		for (const [index, value] of values.entries()) {
			const point = card.locator(`[data-chart-point="${index}"]`);
			await point.focus();
			await expect(page.getByRole('tooltip').locator('strong')).toHaveText(
				labels[index] === null ? '—' : `${labels[index]} kcal`
			);
			if (value !== null)
				expect(
					await card
						.locator('polyline')
						.evaluate(
							(line, dayIndex) =>
								Number(line.getAttribute('points')?.split(' ')[dayIndex].split(',')[1]),
							index
						)
				).toBeCloseTo(58 - (value / max) * 40, 10);
			await page.keyboard.press('Escape');
		}
		await page
			.getByRole('region', { name: '운동 중 심박수', exact: true })
			.locator('[data-chart-point="0"]')
			.focus();
		await expect(page.getByRole('tooltip').locator('strong')).toHaveText('72.55 bpm');
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
	}
});

test('운동 세 지표는 혼합된 7일을 연결하고 결측 툴팁은 대시로 표시한다', async ({ page }) => {
	test.setTimeout(60000);
	const values = [0, null, 72, null, 0, 144, null];
	await page.route('**/api/v1/health/metrics/exercises?*', (route) => {
		const url = new URL(route.request().url());
		const start = url.searchParams.get('start') ?? '';
		const end = url.searchParams.get('end') ?? '';
		const data = activityMetricFixture('exercises', start, end);
		const session = data.detail.sessions.find((item) => item.countsAsExercise);
		if (!session) return route.fulfill({ json: { data } });
		data.days = data.days.map((day, index) => ({
			...day,
			value: values[index],
			missing: values[index] === null,
			reason: values[index] === null ? 'NULL_VALUE' : null
		}));
		data.summary.avg = 54;
		data.summary.total = 216;
		// 날짜에 세션 자체가 없는 경우와 세션의 측정값이 null인 경우를 함께 검증한다.
		data.detail.sessions = values.flatMap((value, index) =>
			index === 1
				? []
				: [
						{
							...session,
							date: data.days[index].date,
							startAt: `${data.days[index].date}T03:00:00Z`,
							avgHeartRate: value,
							maxHeartRate: value,
							caloriesKcal: value
						}
					]
		);
		return route.fulfill({ json: { data } });
	});
	for (const width of [1366, 360]) {
		await page.setViewportSize({ width, height: 800 });
		await openCompletedActivityWeek(page);
		const card = page.getByRole('region', { name: '운동 중 심박수', exact: true });
		await expect(card.locator(':scope > p').first()).toHaveText('평균 54 bpm');
		for (const title of ['운동 시간', '소모 칼로리', '운동 중 심박수']) {
			const metric = page.getByRole('region', { name: title, exact: true });
			await expect(metric.locator('[data-trend-point]')).toHaveCount(4);
			await expect(metric.locator('[data-chart-point]')).toHaveCount(7);
			await expect(metric.locator('polyline')).toHaveCount(1);
			await expect(metric.locator('svg > line[y1="58"]')).toHaveCount(0);
			await expect(metric.locator('polyline')).toHaveAttribute(
				'points',
				'36,58 114,58 192,38 270,58 348,58 426,18 504,58'
			);
			for (const [index, value] of values.entries()) {
				const tooltip =
					value === null || (title === '운동 중 심박수' && value === 0)
						? '—'
						: title === '운동 시간'
							? `${Math.floor(value / 60)}시간 ${value % 60}분`
							: `${value} ${title === '소모 칼로리' ? 'kcal' : 'bpm'}`;
				const point = metric.locator(`[data-chart-point="${index}"]`);
				await point.focus();
				await expect(page.getByRole('tooltip').locator('strong')).toHaveText(tooltip);
				await page.keyboard.press('Escape');
				await expect(page.getByRole('tooltip')).toHaveCount(0);
			}
		}
		await page.evaluate(() => {
			if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
		});
		await page.locator('.activity-exercise').screenshot({
			path: `test-results/activity-graphs-${width}.png`,
			// 긴 영역 캡처에서 고정 내비게이션이 차트를 덮지 않게 한다.
			style: 'header, nav { visibility: hidden !important; }'
		});
		const missingCalories = page
			.getByRole('region', { name: '소모 칼로리', exact: true })
			.locator('[data-chart-point="1"]');
		await missingCalories.hover();
		await expect(page.getByRole('tooltip').locator('strong')).toHaveText('—');
		await page
			.getByRole('region', { name: '소모 칼로리', exact: true })
			.screenshot({ path: `test-results/activity-missing-tooltip-${width}.png` });
	}
});

test('11월 2일 기준 최근 7일은 10월 27일부터 11월 2일까지 결측 날짜도 유지한다', async ({
	page
}) => {
	await page.clock.setFixedTime(new Date('2026-11-02T03:00:00Z'));
	const request = page.waitForRequest((request) =>
		request.url().includes('/health/metrics/steps?start=2026-10-27&end=2026-11-02')
	);
	await page.goto('/health/activity');
	await request;
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		'2026.10.27 ~ 11.02'
	);
	const dates = page.locator('.daily-steps-chart a:not(svg a)');
	await expect(dates).toHaveCount(7);
	await expect(dates.first()).toHaveAttribute('href', '/health/day/2026-10-27');
	await expect(dates.last()).toHaveAttribute('href', '/health/day/2026-11-02');
	await expect(page.getByText('선택한 기간의 걸음 수 기록이 없어요.')).toBeVisible();
	await expect(
		page.getByText('선택한 7일의 일평균 걸음 수를 직전 7일과 비교한 값이에요.', { exact: true })
	).toHaveCount(0);
	const guide = page.getByRole('region', { name: '날짜별 기록 안내' });
	await expect(guide.getByRole('listitem')).toHaveCount(2);
	for (const [index, name] of [
		'2026-10-27부터 2026-11-02까지 · 동기화 대기',
		'2026-10-27부터 2026-11-02까지 · 동기화 대기'
	].entries())
		await expect(guide.getByRole('listitem').nth(index)).toHaveAccessibleName(name);
	for (const width of [1920, 1366, 768, 360]) {
		await page.setViewportSize({ width, height: 1080 });
		expect(await guide.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
		const boxes = await guide.locator(':scope > div').evaluateAll((nodes) =>
			nodes.map((node) => {
				const { left, right, top, bottom } = node.getBoundingClientRect();
				return { left, right, top, bottom };
			})
		);
		expect(boxes).toHaveLength(2);
		if (width >= 768) {
			expect(boxes[1].left).toBeGreaterThan(boxes[0].right);
			expect(boxes[1].top).toBeCloseTo(boxes[0].top, 1);
		} else expect(boxes[1].top).toBeGreaterThanOrEqual(boxes[0].bottom);
		await test.info().attach(`waiting-guide-${width}`, {
			body: await guide.screenshot(),
			contentType: 'image/png'
		});
	}
	await page.route('**/api/v1/health/metrics/steps?start=2026-10-27&end=2026-11-02', (route) => {
		const data = activityMetricFixture('steps', '2026-10-27', '2026-11-02');
		const reasons = [
			'UNSYNCED',
			'UNSYNCED',
			'NO_RECORD',
			'NULL_VALUE',
			'BEFORE_LINK',
			'ZERO_STEPS',
			null
		] as const;
		data.days = data.days.map((day, index) => ({
			...day,
			value: index >= 5 ? 0 : null,
			missing: index !== 6,
			reason: reasons[index],
			isInProgress: index === 6
		}));
		return route.fulfill({ json: { data } });
	});
	await page.reload();
	const stepsGuide = guide.getByRole('heading', { name: '걸음 수', exact: true }).locator('..');
	await expect(stepsGuide.getByRole('listitem')).toHaveCount(5);
	for (const [index, name] of [
		'2026-10-27부터 2026-10-28까지 · 동기화 대기',
		'2026-10-29 · 기록 없음',
		'2026-10-30 · 값 없음',
		'2026-10-31 · 연동 전',
		'2026-11-01 · 기록 없음'
	].entries())
		await expect(stepsGuide.getByRole('listitem').nth(index)).toHaveAccessibleName(name);
	await expect(page.getByRole('link', { name: /^0보.*11\/02/ })).toBeVisible();
	await expect(stepsGuide).not.toContainText('2026.11.02');
});

for (const count of [1, 2]) {
	test(`유효 데이터 ${count}개와 부분 결측을 날짜 위치 그대로 표시한다`, async ({ page }) => {
		await page.route('**/api/v1/health/metrics/steps?start=2026-10-19&end=2026-10-25', (route) => {
			const base = activityMetricFixture('steps', '2026-10-19', '2026-10-25');
			return route.fulfill({
				json: {
					data: {
						...base,
						summary: { ...base.summary, avg: 500, total: 500 * count, daysWithData: count },
						days: base.days.map((day, index) => ({
							...day,
							value: index < count ? 500 : null,
							missing: index >= count,
							reason: index < count ? null : 'NO_RECORD'
						}))
					}
				}
			});
		});
		await openCompletedActivityWeek(page);
		const chart = page.getByRole('figure', { name: '일별 걸음 수 차트' });
		await expect(chart.locator('rect')).toHaveCount(count);
		await expect(chart.locator('a:not(svg a)')).toHaveCount(7);
		await expect(page.getByText('500보', { exact: true })).toBeVisible();
	});
}

test('이전 기간의 늦은 응답은 새로 선택한 기간을 덮어쓰지 않는다', async ({ page }) => {
	let release = () => {};
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.route(
		'**/api/v1/health/metrics/steps?start=2026-10-19&end=2026-10-25',
		async (route) => {
			await gate;
			await route.fulfill({
				json: { data: activityMetricFixture('steps', '2026-10-19', '2026-10-25') }
			});
		}
	);
	await openCompletedActivityWeek(page);
	await expect(page.getByText('활동 기록을 불러오는 중이에요.')).toBeVisible();
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	await expect(page.getByRole('heading', { name: '일별 걸음 수', exact: true })).toBeVisible();
	release();
	await expect(page.getByRole('group', { name: '주간 기간 선택' }).getByRole('status')).toHaveText(
		'2026.10.12 ~ 10.18'
	);
	await expect(page.locator('.daily-steps-chart rect')).toHaveCount(0);
});

test('서버 summary가 화면과 평균선의 원천이며 기간 이동은 새 요청을 보낸다', async ({ page }) => {
	await page.route('**/api/v1/health/metrics/steps?start=2026-10-19&end=2026-10-25', (route) => {
		const data = activityMetricFixture('steps', '2026-10-19', '2026-10-25');
		data.summary.avg = 9999;
		return route.fulfill({ json: { data } });
	});
	await openCompletedActivityWeek(page);
	await expect(page.getByText('9,999보', { exact: true })).toBeVisible();
	const request = page.waitForRequest((r) =>
		r.url().includes('/health/metrics/steps?start=2026-10-26&end=2026-11-01')
	);
	await page.getByRole('button', { name: '다음 주', exact: true }).click();
	// 웹 인증은 세션 쿠키라 Authorization header를 보내지 않는다
	expect((await request).headers().authorization).toBeUndefined();
	await expect(page.getByRole('button', { name: '다음 주', exact: true })).toBeDisabled();
	await expect(page.locator('.daily-steps-chart a:not(svg a)')).toHaveCount(7);
});

test('로딩 후 서버 오류는 기록 없음과 구분하고 재시도 시 실제 요청을 다시 보낸다', async ({
	page
}) => {
	let release = () => {};
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	let fail = true;
	await page.route('**/api/v1/health/metrics/exercises?*', async (route) => {
		if (!fail) return route.fallback();
		await gate;
		return route.fulfill({
			status: 503,
			headers: { 'X-Request-Id': 'activity-test' },
			json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: '운동 데이터를 준비 중입니다.' } }
		});
	});
	await openCompletedActivityWeek(page);
	await expect(page.getByText('활동 기록을 불러오는 중이에요.')).toBeVisible();
	release();
	await expect(page.getByRole('alert')).toContainText('운동 데이터를 준비 중입니다.');
	await expect(page.getByRole('alert')).toContainText('activity-test');
	await expect(page.locator('.activity-exercise')).toHaveCount(0);
	fail = false;
	await page.getByRole('button', { name: '다시 시도', exact: true }).click();
	await expect(page.getByText('평균 26분', { exact: true })).toBeVisible();
});

test('시간대 API 실패는 보조 조회 오류로 알리고 다른 활동은 유지한다', async ({ page }) => {
	await page.route('**/api/v1/health/timeline?*', (route) => route.abort('failed'));
	await openCompletedActivityWeek(page);
	await expect(page.getByRole('alert')).toContainText(
		'시간대별 걸음 수 데이터를 불러오지 못했어요.'
	);
	await expect(page.getByText('평균 26분', { exact: true })).toBeVisible();
	await expect(page.getByRole('figure', { name: '시간대별 평균 걸음 수 분포' })).toHaveCount(0);
});

test('0보 결측과 인정 운동 0분은 서로 다른 의미로 표시한다', async ({ page }) => {
	await page.route('**/api/v1/health/metrics/steps?start=2026-10-19&end=2026-10-25', (route) => {
		const base = activityMetricFixture('steps', '2026-10-19', '2026-10-25');
		return route.fulfill({
			json: {
				data: {
					...base,
					days: base.days.map((d) => ({ ...d, value: 0, missing: true, reason: 'ZERO_STEPS' })),
					summary: { ...base.summary, avg: null, total: null, daysWithData: 0 }
				}
			}
		});
	});
	await page.route('**/api/v1/health/metrics/exercises?*', (route) => {
		const base = activityMetricFixture('exercises', '2026-10-19', '2026-10-25');
		return route.fulfill({
			json: {
				data: {
					...base,
					days: base.days.map((d) => ({ ...d, value: 0, missing: false, reason: null })),
					detail: { ...base.detail, sessions: [] },
					summary: { ...base.summary, avg: 0, total: 0 }
				}
			}
		});
	});
	await openCompletedActivityWeek(page);
	await expect(page.getByText('평균 0분', { exact: true })).toBeVisible();
	await expect(page.locator('.daily-steps-chart rect')).toHaveCount(0);
	await expect(page.getByRole('link', { name: /^기록 없음.*10\/19/ })).toBeVisible();
});

for (const code of ['AUTH_EXPIRED', 'AUTH_REQUIRED'] as const) {
	test(`${code}는 갱신·재요청 없이 로그인으로 이동한다`, async ({ page }) => {
		await openCompletedActivityWeek(page);
		await expect(page.getByText('평균 26분', { exact: true })).toBeVisible();
		await page.route('**/api/v1/health/metrics/exercises?*', (route) =>
			route.fulfill({ status: 401, json: { error: { code, message: 'login required' } } })
		);
		await page.getByRole('button', { name: '다음 주', exact: true }).click();
		await expect(page).toHaveURL('/login');
	});
}
