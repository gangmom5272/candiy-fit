import { exerciseSessions, recentStepRecords } from '../features/health/activity';
import { exerciseSessionFixture } from './health-api-fixture-fields';
import type { HealthMetricDay } from '../features/health/day-detail';

export function activityMetricFixture(metric: string, start: string, end: string) {
	const sessions = exerciseSessions.filter((s) => s.date >= start && s.date <= end);
	const dates = Array.from(
		{ length: Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1 },
		(_, i) => new Date(Date.parse(start) + i * 86400000).toISOString().slice(0, 10)
	);
	const days: HealthMetricDay[] = dates.map((date) => {
		const recorded = sessions.filter((s) => s.date === date);
		const value =
			metric === 'steps'
				? (recentStepRecords.find((d) => d.date === date)?.value ?? null)
				: recorded.length
					? recorded
							.filter((s) => s.countsAsExercise)
							.reduce((sum, s) => sum + s.durationMinutes, 0)
					: null;
		const reason: HealthMetricDay['reason'] =
			date < '2026-10-01'
				? 'BEFORE_LINK'
				: value !== null
					? null
					: date >= '2026-10-26'
						? 'UNSYNCED'
						: 'NO_RECORD';
		return {
			date,
			value,
			missing: reason !== null,
			reason,
			isInProgress: date === '2026-10-26',
			wearHours: null,
			isWellWorn: null
		};
	});
	const valid = days.filter((d) => !d.missing && !d.isInProgress);
	const total = valid.length ? valid.reduce((sum, d) => sum + (d.value ?? 0), 0) : null;
	const sorted = [...valid].sort((a, b) => (a.value ?? 0) - (b.value ?? 0));
	const dated = (day: (typeof days)[number] | undefined) =>
		day ? { date: day.date, value: day.value } : null;
	return {
		metric,
		unit: metric === 'steps' ? 'steps' : 'min',
		period: { start, end },
		summary: {
			avg: total === null ? null : Math.round((total / valid.length) * 100) / 100,
			total,
			daysWithData: valid.length,
			daysUsedForSummary: valid.length,
			completedDays: days.filter((d) => !d.isInProgress && d.reason !== 'BEFORE_LINK').length,
			dailyMin: dated(sorted[0]),
			dailyMax: dated(sorted.at(-1)),
			missingDays: days.filter((d) => d.reason === 'NO_RECORD').map((d) => d.date),
			unsyncedDays: days.filter((d) => d.reason === 'UNSYNCED').map((d) => d.date),
			linkedFrom: '2026-10-01'
		},
		days,
		inProgress: days.some((d) => d.isInProgress)
			? { date: end, value: null, missing: true, asOf: '2026-10-26T00:00:00Z' }
			: null,
		dataStatus: {
			lastDataAt: '2026-10-26T00:00:00Z',
			syncedUntil: '2026-10-26T01:00:00Z',
			lastSyncStatus: 'success',
			lastSuccessAt: '2026-10-26T01:10:00Z',
			checkedAt: '2026-10-26T02:00:00Z'
		},
		detail: {
			measuredMin: null,
			measuredMax: null,
			nights: [],
			dailyRanges: [],
			sessions:
				metric === 'exercises'
					? sessions.map(exerciseSessionFixture).sort((a, b) => a.startAt.localeCompare(b.startAt))
					: []
		}
	};
}
