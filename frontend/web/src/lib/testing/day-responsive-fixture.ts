import type { Page } from '@playwright/test';

export type DayScenario =
	| 'normal'
	| 'empty'
	| 'partial'
	| 'single'
	| 'zero'
	| 'dense'
	| 'wide'
	| 'short'
	| 'long'
	| 'sleepnull'
	| 'sleepzero';
export const dayResponsiveToday = '2026-11-10';

/** 일별 지표와 타임라인을 같은 KST 원본에서 만드는 반응형 검증용 응답. */
export function dayResponsiveFixture(date: string, scenario: DayScenario = 'normal') {
	const origin = Date.parse(`${date}T00:00:00+09:00`);
	const instant = (minutes: number) => new Date(origin + minutes * 60000).toISOString();
	const today = date === dayResponsiveToday;
	const empty = scenario === 'empty';
	const partial = scenario === 'partial';
	const single = scenario === 'single';
	const zero = scenario === 'zero';
	const hourlySteps =
		empty || partial
			? null
			: Array.from({ length: 24 }, (_, hour) =>
					zero || (single && hour !== 12) || (today && hour >= 12)
						? 0
						: scenario === 'dense'
							? 100000
							: (hour + 1) * 37
				);
	const hourlyHeartRates =
		empty || zero
			? null
			: Array.from({ length: 24 }, (_, hour) => {
					if ((single && hour !== 12) || (partial && hour % 3 !== 0) || (today && hour >= 12))
						return null;
					const avg = scenario === 'wide' ? 40 + hour * 6 : 70 + (hour % 4);
					return {
						avg,
						min: scenario === 'wide' ? avg - 5 : avg - 10,
						max: scenario === 'wide' ? avg + 5 : avg + 20,
						sampleCount: 3
					};
				});
	const start = scenario === 'long' ? -480 : scenario === 'short' ? -5 : -60;
	const end = scenario === 'long' ? 720 : scenario === 'short' ? 10 : 420;
	const length = end - start;
	const awake = scenario === 'sleepzero' ? length : scenario === 'short' ? 0 : 30;
	const deep = scenario === 'sleepzero' ? 0 : Math.floor(length / 3);
	const hasSleep = !empty && !partial && !single && !zero;
	const mainSleep = hasSleep ? { startAt: instant(start), endAt: instant(end) } : null;
	const sleepSegments = !hasSleep
		? []
		: scenario === 'sleepzero'
			? [{ startAt: instant(start), endAt: instant(end), stage: 'awake' }]
			: [
					{ startAt: instant(start), endAt: instant(start + deep), stage: 'deep' },
					{ startAt: instant(start + deep), endAt: instant(end - awake), stage: 'light' },
					...(awake ? [{ startAt: instant(end - awake), endAt: instant(end), stage: 'awake' }] : [])
				];
	const night = mainSleep
		? {
				date,
				...mainSleep,
				sleepMinutes: scenario === 'sleepnull' ? null : length - awake,
				sessionMinutes: length,
				deepMinutes: deep,
				lightMinutes: length - deep - awake,
				remMinutes: 0,
				awakeMinutes: awake,
				unspecifiedMinutes: 0,
				unknownMinutes: 0,
				avgHeartRate: null,
				minHeartRate: null,
				maxHeartRate: null,
				spo2Avg: null,
				spo2Min: null,
				spo2Max: null,
				spo2Count: null,
				spo2LowCount: null,
				spo2LowTimes: null
			}
		: null;
	const sessions = !hasSleep
		? []
		: (scenario === 'dense' && !today
				? [
						[720, 750],
						[1439, 1441]
					]
				: [[today ? 540 : 720, today ? 570 : 750]]
			).map(([from, to], index) => ({
				recordId: `synthetic-${index}`,
				date,
				startAt: instant(from),
				endAt: instant(to),
				durationMinutes: to - from,
				exerciseType: 79,
				typeName: '걷기',
				title: null,
				category: 'cardio',
				caloriesKcal: null,
				distanceM: null,
				avgHeartRate: null,
				maxHeartRate: null,
				countsAsExercise: true,
				avgSpeedMps: null
			}));
	const hearts = hourlyHeartRates?.filter((point) => point !== null) ?? [];
	const values = {
		steps: hourlySteps === null ? null : hourlySteps.reduce((sum, value) => sum + value, 0),
		exercises: sessions.length
			? sessions.reduce((sum, session) => sum + session.durationMinutes, 0)
			: zero
				? 0
				: null,
		sleeps: night?.sleepMinutes ?? null,
		heart_rates: hearts.length
			? Math.round((hearts.reduce((sum, point) => sum + point.avg, 0) / hearts.length) * 10) / 10
			: null
	};
	const dataStatus = {
		lastDataAt: '2026-11-10T02:50:00Z',
		syncedUntil: '2026-11-10T02:50:00Z',
		lastSyncStatus: 'success',
		lastSuccessAt: '2026-11-10T02:59:00Z',
		checkedAt: '2026-11-10T03:00:00Z'
	};
	const metric = (name: keyof typeof values) => {
		const value = values[name];
		const reason =
			name === 'steps' && value === 0
				? today
					? 'UNSYNCED'
					: 'ZERO_STEPS'
				: value !== null
					? null
					: today
						? 'UNSYNCED'
						: name === 'sleeps' && night
							? 'NULL_VALUE'
							: 'NO_RECORD';
		const isInProgress = today && name !== 'sleeps';
		const valid = reason === null && !isInProgress;
		const dated = valid ? { date, value } : null;
		const min = hearts.length ? Math.min(...hearts.map((point) => point.min)) : null;
		const max = hearts.length ? Math.max(...hearts.map((point) => point.max)) : null;
		return {
			metric: name,
			unit: name === 'steps' ? 'steps' : name === 'heart_rates' ? 'bpm' : 'min',
			period: { start: date, end: date },
			summary: {
				avg: valid ? value : null,
				total: valid && (name === 'steps' || name === 'exercises') ? value : null,
				dailyMin: dated,
				dailyMax: dated,
				daysWithData: valid ? 1 : 0,
				daysUsedForSummary: valid ? 1 : 0,
				completedDays: isInProgress ? 0 : 1,
				missingDays: reason && reason !== 'UNSYNCED' ? [date] : [],
				unsyncedDays: reason === 'UNSYNCED' ? [date] : [],
				linkedFrom: '2025-01-01'
			},
			days: [
				{
					date,
					value,
					missing: reason !== null,
					reason,
					isInProgress,
					wearHours: null,
					isWellWorn: null
				}
			],
			inProgress: isInProgress
				? { date, value, missing: reason !== null, asOf: dataStatus.lastDataAt }
				: null,
			dataStatus,
			detail: {
				measuredMin: name === 'heart_rates' && valid ? { date, value: min } : null,
				measuredMax: name === 'heart_rates' && valid ? { date, value: max } : null,
				nights: name === 'sleeps' && night ? [night] : [],
				dailyRanges:
					name === 'heart_rates'
						? [{ date, min, max, sampleCount: hearts.length ? hearts.length * 3 : null }]
						: [],
				sessions: name === 'exercises' ? sessions : []
			}
		};
	};
	return {
		metrics: {
			steps: metric('steps'),
			exercises: metric('exercises'),
			sleeps: metric('sleeps'),
			heart_rates: metric('heart_rates')
		},
		timeline: {
			period: { start: date, end: date },
			days: [
				{
					date,
					isInProgress: today,
					hourlySteps,
					hourlyHeartRates,
					mainSleep,
					sleepSegments,
					exerciseSegments: sessions.map(({ startAt, endAt, exerciseType, typeName, title }) => ({
						startAt,
						endAt,
						exerciseType,
						typeName,
						title
					}))
				}
			]
		}
	};
}

export async function mockDayResponsiveApi(page: Page, scenario: DayScenario = 'normal') {
	await page.clock.setFixedTime(new Date('2026-11-10T03:00:00Z'));
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
		const response = dayResponsiveFixture(date, scenario);
		return route.fulfill({ json: { data: response.metrics[metric] } });
	});
	await page.route('**/api/v1/health/timeline?*', (route) => {
		const url = new URL(route.request().url());
		const date = url.searchParams.get('start') ?? '';
		if (date !== url.searchParams.get('end')) return route.fallback();
		return route.fulfill({ json: { data: dayResponsiveFixture(date, scenario).timeline } });
	});
}
