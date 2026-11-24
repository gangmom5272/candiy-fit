import type { Page } from '@playwright/test';
import { activityMetricFixture, mockActivityApi } from './activity-api-fixture';
import { mockSleepApi, sleepMetricFixture } from './sleep-api-fixture';
import { mockHeartApi } from './heart-api-fixture';
import type { HomeDailyMetric, LifestyleScore } from '../features/home/home-api';
import { dailyMessageFixture } from './daily-message-fixture';
import { mockHealthSync } from './e2e-auth';
import { sleepNightFixture } from './health-api-fixture-fields';

export function homeDailyMetricFixture(metric: HomeDailyMetric, date = '2026-10-26') {
	const data =
		metric === 'sleeps'
			? sleepMetricFixture('sleeps', date, date)
			: activityMetricFixture(metric, date, date);
	const value = { steps: 3200, exercises: 18, sleeps: 420 }[metric];
	Object.assign(data.days[0], {
		value,
		missing: false,
		reason: null,
		isInProgress: metric !== 'sleeps'
	});
	Object.assign(data.summary, {
		avg: metric === 'sleeps' ? value : null,
		total: null,
		dailyMin: metric === 'sleeps' ? { date, value } : null,
		dailyMax: metric === 'sleeps' ? { date, value } : null,
		daysWithData: metric === 'sleeps' ? 1 : 0,
		daysUsedForSummary: metric === 'sleeps' ? 1 : 0,
		completedDays: metric === 'sleeps' ? 1 : 0,
		missingDays: [],
		unsyncedDays: []
	});
	if (metric === 'sleeps') {
		// 날짜 경계 테스트의 합성 수면 값에도 같은 기상일의 주 수면을 제공한다.
		if (data.detail.nights.length === 0) {
			const endAt = new Date(`${date}T00:00:00+09:00`).toISOString();
			data.detail.nights = [
				sleepNightFixture({
					date,
					endAt,
					startAt: new Date(Date.parse(endAt) - value * 60000).toISOString(),
					sleepMinutes: value,
					deepMinutes: null,
					remMinutes: null,
					lightMinutes: null,
					awakeMinutes: null
				})
			];
		}
		data.detail.nights.forEach((night) => {
			night.sleepMinutes = value;
		});
	}
	return {
		...data,
		inProgress:
			metric === 'sleeps' ? null : { date, value, missing: false, asOf: data.dataStatus.lastDataAt }
	};
}

/** API v14 일별 저장값. 일반 심박 주 평균 75.68과 다른 수면 심박을 사용한다. */
export function lifestyleScoreFixture(date = '2026-10-26'): LifestyleScore {
	return {
		date,
		isInProgress: true,
		reason: null,
		total: { score: 71, missingAreas: [] },
		areas: {
			activity: {
				score: 85,
				grade: 1,
				steps: { score: 60, value: 7200 },
				exercise: { score: 25, minutes: 19 }
			},
			sleep: { score: 58, grade: 3, sleepMinutes: 366 },
			heart: {
				score: 70,
				grade: 2,
				sleepHrAvg: 64.5,
				baselineHrAvg: 58,
				baselineNights: 20,
				checkWear: false
			}
		},
		computedAt: '2026-10-26T03:00:00Z'
	};
}

export async function mockHomeApi(page: Page, dates = ['2026-10-26']) {
	await mockHealthSync(page);
	await page.route('**/api/v1/health/daily-message', (route) =>
		route.fulfill({ json: { data: dailyMessageFixture() } })
	);
	await mockActivityApi(page);
	await mockSleepApi(page);
	await mockHeartApi(page);
	await page.route('**/api/v1/health/metrics/*?*', (route) => {
		const url = new URL(route.request().url());
		const metric = url.pathname.split('/').at(-1);
		const date = url.searchParams.get('start');
		if (
			!date ||
			!dates.includes(date) ||
			url.searchParams.get('end') !== date ||
			(metric !== 'steps' && metric !== 'exercises' && metric !== 'sleeps')
		)
			return route.fallback();
		return route.fulfill({ json: { data: homeDailyMetricFixture(metric, date) } });
	});
	await page.route('**/api/v1/health/lifestyle-score?*', (route) => {
		const date = new URL(route.request().url()).searchParams.get('date');
		if (!date || !dates.includes(date)) throw new Error('Home fixture에 없는 점수 날짜');
		return route.fulfill({ json: { data: lifestyleScoreFixture(date) } });
	});
}
