import type { Page } from '@playwright/test';
import { getDayDetailMock, DAY_DETAIL_MOCK_TODAY } from '../features/health/day-detail-mock';
import { getSleepMockWeek } from '../features/health/sleep-mock';
import { getHeartMockWeek } from '../features/health/heart-mock';
import { exerciseSessions } from '../features/health/activity';
import { healthMockLinkedFrom, healthMockSyncedUntil } from '../features/health/mock-user';
import { exerciseSessionFixture, sleepNightFixture } from './health-api-fixture-fields';

export type DayMetric = 'steps' | 'exercises' | 'sleeps' | 'heart_rates';

/** 날짜별 HTTP 응답 전용. 운영 페이지는 이 fixture를 사용하지 않는다. */
export function dayMetricFixture(metric: DayMetric, date: string) {
	const record = getDayDetailMock(date);
	const day = {
		...{
			steps: record.steps,
			exercises: record.exercise,
			sleeps: record.sleep,
			heart_rates: record.heart
		}[metric]
	};
	const offset = Math.min(
		0,
		Math.floor((Date.parse(date) - Date.parse(DAY_DETAIL_MOCK_TODAY)) / (7 * 86400000))
	);
	const sleep = getSleepMockWeek(offset).sleep;
	const oxygen = getSleepMockWeek(offset).oxygen;
	const heart = getHeartMockWeek(offset);
	const valid = !day.missing && !day.isInProgress && day.value !== null;
	const dated = valid ? { date, value: day.value } : null;
	const range = heart.detail.dailyRanges.find((range) => range.date === date);
	return {
		metric,
		unit: metric === 'steps' ? 'steps' : metric === 'heart_rates' ? 'bpm' : 'min',
		period: { start: date, end: date },
		summary: {
			avg: valid ? day.value : null,
			total: valid && (metric === 'steps' || metric === 'exercises') ? day.value : null,
			dailyMin: dated,
			dailyMax: dated,
			daysWithData: valid ? 1 : 0,
			daysUsedForSummary: valid ? 1 : 0,
			completedDays: day.isInProgress || day.reason === 'BEFORE_LINK' ? 0 : 1,
			missingDays:
				day.reason === 'NO_RECORD' || day.reason === 'NULL_VALUE' || day.reason === 'ZERO_STEPS'
					? [date]
					: [],
			unsyncedDays: day.reason === 'UNSYNCED' ? [date] : [],
			linkedFrom: healthMockLinkedFrom
		},
		days: [{ ...day, wearHours: null, isWellWorn: null }],
		inProgress: day.isInProgress
			? { date, value: day.value, missing: day.missing, asOf: '2026-10-26T00:00:00Z' }
			: null,
		dataStatus: {
			lastDataAt: '2026-10-26T00:00:00Z',
			syncedUntil: healthMockSyncedUntil,
			lastSyncStatus: 'success',
			lastSuccessAt: '2026-10-26T01:00:00Z',
			checkedAt: '2026-10-26T03:00:00Z'
		},
		detail: {
			measuredMin:
				metric === 'heart_rates' && valid && range?.min != null ? { date, value: range.min } : null,
			measuredMax:
				metric === 'heart_rates' && valid && range?.max != null ? { date, value: range.max } : null,
			nights:
				metric === 'sleeps'
					? sleep.detail.nights
							.filter((night) => night.date === date)
							.map((night) => {
								const range = oxygen.detail.dailyRanges.find((row) => row.date === date);
								return sleepNightFixture(night, {
									avg: oxygen.days.find((row) => row.date === date)?.value ?? null,
									min: range?.min ?? null,
									max: range?.max ?? null
								});
							})
					: [],
			dailyRanges: metric === 'heart_rates' ? [range ?? { date, min: null, max: null }] : [],
			sessions:
				metric === 'exercises'
					? exerciseSessions.filter((session) => session.date === date).map(exerciseSessionFixture)
					: []
		}
	};
}

export function dayTimelineFixture(date: string) {
	return {
		period: { start: date, end: date },
		days: [structuredClone(getDayDetailMock(date).timeline)]
	};
}

export async function mockDayDetailApi(page: Page) {
	await page.clock.setFixedTime(new Date(`${DAY_DETAIL_MOCK_TODAY}T03:00:00Z`));
	await page.route('**/api/v1/health/metrics/*?*', (route) => {
		const url = new URL(route.request().url());
		const date = url.searchParams.get('start') ?? '';
		const metric = url.pathname.split('/').at(-1);
		if (
			date !== url.searchParams.get('end') ||
			(metric !== 'steps' &&
				metric !== 'exercises' &&
				metric !== 'sleeps' &&
				metric !== 'heart_rates')
		)
			return route.fallback();
		return route.fulfill({ json: { data: dayMetricFixture(metric, date) } });
	});
	await page.route('**/api/v1/health/timeline?*', (route) => {
		const url = new URL(route.request().url());
		const date = url.searchParams.get('start') ?? '';
		if (date !== url.searchParams.get('end')) return route.fallback();
		return route.fulfill({ json: { data: dayTimelineFixture(date) } });
	});
}
