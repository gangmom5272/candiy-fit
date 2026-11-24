import type { HeartMetricResult } from '../features/health/heart';

export const HEART_RESPONSIVE_TODAY = '2026-11-10';
type Sample = { avg: number; min: number; max: number; hours: number } | null;
export const normalHeartSamples: Sample[] = [
	{ avg: 68.2, min: 52, max: 98, hours: 12 },
	{ avg: 72.1, min: 54, max: 105, hours: 18 },
	{ avg: 65.5, min: 50, max: 90, hours: 16 },
	{ avg: 74, min: 56, max: 110, hours: 20 },
	{ avg: 70.3, min: 53, max: 100, hours: 14 },
	{ avg: 67.8, min: 51, max: 96, hours: 24 },
	{ avg: 71.2, min: 54, max: 104, hours: 9 }
];

/** 유효 시간 수로 가중하며 오늘은 summary에서 제외한다. sampleCount는 시간당 10회다. */
export function responsiveHeartFixture(
	end: string,
	samples: readonly Sample[] = normalHeartSamples
) {
	const dates = Array.from({ length: 7 }, (_, i) =>
		new Date(Date.parse(`${end}T00:00:00Z`) - (6 - i) * 86400000).toISOString().slice(0, 10)
	);
	const days: HeartMetricResult['days'] = dates.map((date, i) => ({
		date,
		value: samples[i]?.avg ?? null,
		missing: samples[i] == null,
		reason: samples[i] ? null : date === HEART_RESPONSIVE_TODAY ? 'UNSYNCED' : 'NULL_VALUE',
		isInProgress: date === HEART_RESPONSIVE_TODAY
	}));
	const completed = days.filter((day) => !day.isInProgress);
	const valid = completed.filter((day) => !day.missing);
	const extreme = (getValue: (index: number) => number, maximum = false) => {
		const values = valid.map((day) => ({
			date: day.date,
			value: getValue(dates.indexOf(day.date))
		}));
		values.sort((a, b) => (maximum ? b.value - a.value : a.value - b.value));
		return values[0] ?? null;
	};
	const totalHours = valid.reduce(
		(sum, day) => sum + (samples[dates.indexOf(day.date)]?.hours ?? 0),
		0
	);
	const today = days.find((day) => day.isInProgress);
	const lastDataAt = '2026-11-10T02:30:00Z';
	return {
		metric: 'heart_rates',
		period: { start: dates[0], end },
		unit: 'bpm',
		days,
		summary: {
			avg: totalHours
				? Number(
						(
							valid.reduce(
								(sum, day) =>
									sum + (day.value ?? 0) * (samples[dates.indexOf(day.date)]?.hours ?? 0),
								0
							) / totalHours
						).toFixed(2)
					)
				: null,
			dailyMin: extreme((i) => samples[i]!.avg),
			dailyMax: extreme((i) => samples[i]!.avg, true),
			total: null,
			daysWithData: valid.length,
			daysUsedForSummary: valid.length,
			completedDays: completed.length,
			missingDays: days.filter((d) => d.reason === 'NULL_VALUE').map((d) => d.date),
			unsyncedDays: days.filter((d) => d.reason === 'UNSYNCED').map((d) => d.date),
			linkedFrom: '2025-01-01'
		},
		inProgress: today
			? { date: today.date, value: today.value, missing: today.missing, asOf: lastDataAt }
			: null,
		dataStatus: {
			lastDataAt,
			syncedUntil: '2026-11-10T02:45:00Z',
			lastSyncStatus: 'success',
			lastSuccessAt: lastDataAt,
			checkedAt: '2026-11-10T03:00:00Z'
		},
		detail: {
			measuredMin: extreme((i) => samples[i]!.min),
			measuredMax: extreme((i) => samples[i]!.max, true),
			nights: [],
			sessions: [],
			dailyRanges: dates.map((date, i) => ({
				date,
				min: samples[i]?.min ?? null,
				max: samples[i]?.max ?? null,
				sampleCount: (samples[i]?.hours ?? 0) * 10
			}))
		}
	} satisfies HeartMetricResult & { detail: { dailyRanges: { sampleCount: number }[] } };
}
