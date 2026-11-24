import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import {
	activityMetricFixture,
	mockActivityApi
} from '../../../../lib/testing/activity-api-fixture';

type State = 'none' | 'one' | 'many' | 'unknown' | 'sync' | 'error' | 'invalid';
async function mockExercises(page: Page, state: () => State) {
	await mockSignedIn(page);
	await mockActivityApi(page);
	await page.route('**/api/v1/health/metrics/exercises?*', (route) => {
		if (state() === 'error')
			return route.fulfill({
				status: 503,
				json: { error: { code: 'LOOKUP_FAILED', message: '운동 조회 실패', retryable: true } }
			});
		const url = new URL(route.request().url());
		const start = url.searchParams.get('start')!;
		const data = activityMetricFixture('exercises', start, url.searchParams.get('end')!);
		const seed = activityMetricFixture('exercises', '2026-10-19', '2026-10-25').detail.sessions[0];
		const names =
			state() === 'many'
				? ['걷기', '달리기', '실내 자전거']
				: state() === 'one' || state() === 'invalid'
					? ['걷기']
					: [];
		const minutes = state() === 'unknown' ? 30 : names.length * 30;
		data.detail.sessions = names.map((typeName, index) => ({
			...seed,
			date: start,
			typeName,
			durationMinutes: 30,
			countsAsExercise: true,
			startAt: `${start}T0${index + 1}:00:00Z`,
			endAt: `${start}T0${index + 1}:30:00Z`
		}));
		if (state() === 'invalid') Object.assign(data.detail.sessions[0], { typeName: null });
		data.days.forEach((day, index) =>
			Object.assign(day, {
				value: state() === 'sync' ? null : index === 0 ? minutes : 0,
				missing: state() === 'sync',
				reason: state() === 'sync' ? 'UNSYNCED' : null
			})
		);
		Object.assign(data.summary, {
			avg:
				state() === 'sync' ? null : minutes / data.days.filter((day) => !day.isInProgress).length,
			total: state() === 'sync' ? null : minutes,
			daysWithData: state() === 'sync' ? 0 : data.days.filter((day) => !day.isInProgress).length,
			missingDays: [],
			unsyncedDays: state() === 'sync' ? data.days.map((day) => day.date) : []
		});
		return route.fulfill({ json: { data } });
	});
}
async function selectDate(page: Page, date: string) {
	await page.getByRole('button', { name: /^날짜 선택,/ }).click();
	await page.getByRole('dialog').getByRole('button', { name: date, exact: true }).click();
}

for (const width of [1366, 1024, 768, 360]) {
	test(`운동 카드 ${width}: 종류 0·1·여러 건과 정보 누락·동기화 대기를 구분한다`, async ({
		page
	}, info) => {
		await page.setViewportSize({ width, height: 900 });
		let state: State = 'none';
		await mockExercises(page, () => state);
		const measurements = [];
		let chartHeight: number | undefined;
		for (const [index, next] of (['none', 'one', 'many', 'unknown', 'sync'] as const).entries()) {
			state = next;
			if (index === 0) await page.goto('/health/activity');
			else await selectDate(page, index % 2 ? '2026-10-25' : '2026-10-24');
			const time = page.getByRole('region', { name: '운동 시간', exact: true });
			const types = page.getByRole('region', { name: '운동 종류', exact: true });
			await expect(types).toBeVisible();
			if (state === 'none') {
				await expect(
					types.getByText('선택한 기간에 운동 기록이 없어요.', { exact: true })
				).toBeVisible();
				await expect(time.getByText('평균 0분', { exact: true })).toBeVisible();
			} else if (state === 'one' || state === 'many') {
				await expect(
					types.getByRole('list', { name: '기록된 운동 종류', exact: true }).getByRole('listitem')
				).toHaveCount(state === 'one' ? 1 : 3);
			} else {
				await expect(
					types.getByText(
						state === 'sync' ? '운동 기록이 아직 동기화되지 않았어요.' : '운동 종류 정보가 없어요.',
						{
							exact: true
						}
					)
				).toBeVisible();
				await expect(types.getByText('총 0종', { exact: true })).toHaveCount(0);
				if (state === 'sync') {
					await expect(types.locator('#exercise-types-title + p')).toHaveText('—');
				}
			}
			await page.evaluate(() => document.fonts.ready);
			const left = await time.boundingBox();
			const right = await types.boundingBox();
			if (width >= 1024) {
				expect(Math.abs(left!.height - right!.height)).toBeLessThanOrEqual(1);
				expect(Math.abs(left!.y - right!.y)).toBeLessThanOrEqual(1);
			} else if (state === 'none') expect(right!.height).toBeLessThan(left!.height);
			const plot = await time.getByRole('figure').boundingBox();
			chartHeight ??= plot!.height;
			expect(plot!.height).toBe(chartHeight);
			await expect(types).toHaveCSS('padding-top', '24px');
			await expect(types).toHaveCSS('padding-left', width < 640 ? '16px' : '28px');
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
			measurements.push({ state, width, timeHeight: left!.height, typesHeight: right!.height });
			await page.locator('#exercise').scrollIntoViewIfNeeded();
			await page.screenshot({ path: info.outputPath(`${state}-${width}.png`) });
		}
		await info.attach('exercise-card-heights', {
			body: JSON.stringify(measurements),
			contentType: 'application/json'
		});
	});
}

test('운동 종류 계약 오류와 API 오류는 빈 상태가 아니며 재시도로 복구한다', async ({ page }) => {
	let state: State = 'error';
	await mockExercises(page, () => state);
	await page.goto('/health/activity');
	await expect(page.getByRole('alert')).toBeVisible();
	await expect(page.getByRole('region', { name: '운동 종류', exact: true })).toHaveCount(0);
	state = 'invalid';
	const invalidResponse = page.waitForResponse('**/api/v1/health/metrics/exercises?*');
	await page.getByRole('button', { name: '다시 시도', exact: true }).click();
	expect((await (await invalidResponse).json()).data.detail.sessions[0].typeName).toBeNull();
	await expect(page.getByRole('alert')).toBeVisible();
	await expect(page.getByText('선택한 기간에 운동 기록이 없어요.', { exact: true })).toHaveCount(0);
	state = 'none';
	await page.getByRole('button', { name: '다시 시도', exact: true }).click();
	await expect(page.getByRole('region', { name: '운동 종류', exact: true })).toContainText(
		'선택한 기간에 운동 기록이 없어요.'
	);
});
