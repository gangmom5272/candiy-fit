import { heartWeekDates, type HeartMetricResult } from './heart';
import { healthMockLinkedFrom, healthMockSyncedDate, healthMockSyncedUntil } from './mock-user';

export const HEART_MOCK_TODAY = '2026-10-26';

/** 합성 원천. 시간대 수는 모의 서버 summary를 만들 때만 쓰고 응답에는 추가하지 않는다. */
const records = [
	{ date: '2026-10-19', value: 76, min: 54, max: 132, hours: 12 },
	{ date: '2026-10-20', value: 73, min: 58, max: 124, hours: 18 },
	{ date: '2026-10-22', value: 78, min: 53, max: 140, hours: 16 },
	{ date: '2026-10-23', value: 75, min: 57, max: 126, hours: 20 },
	{ date: '2026-10-24', value: 77, min: 55, max: 134, hours: 14 },
	{ date: '2026-10-25', value: 75.8, min: 54, max: 131, hours: 24 },
	{ date: '2026-10-26', value: 74.2, min: 52, max: 118, hours: 9 }
];

/** 하루 상세용 §5.4 합성 시간대 값. 기존 일평균·유효 시간대 수·측정 범위를 공유한다. */
export function getHeartMockHours(
	date: string
): ({ avg: number; min: number; max: number } | null)[] | null {
	const record = records.find((record) => record.date === date);
	if (!record) return null;
	const order = [
		0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20, 21, 22, 23, 11, 13, 15, 17, 19
	];
	const active = order.slice(0, record.hours).sort((a, b) => a - b);
	return Array.from({ length: 24 }, (_, hour) => {
		const index = active.indexOf(hour);
		return index === -1
			? null
			: {
					avg: Math.round((record.value + index - (record.hours - 1) / 2) * 10) / 10,
					min: record.min,
					max: record.max
				};
	});
}

/** 모의 응답 생성에만 사용한다. 화면은 기간 평균이나 결측 사유를 재판정하지 않는다. */
export function getHeartMockWeek(
	offset: number,
	period?: HeartMetricResult['period']
): HeartMetricResult {
	const dates = period
		? Array.from(
				{ length: (Date.parse(period.end) - Date.parse(period.start)) / 86400000 + 1 },
				(_, index) =>
					new Date(Date.parse(period.start) + index * 86400000).toISOString().slice(0, 10)
			)
		: heartWeekDates(HEART_MOCK_TODAY, Math.min(0, offset));
	const days: HeartMetricResult['days'] = dates.map((date) => {
		const record = records.find((record) => record.date === date);
		const reason =
			date < healthMockLinkedFrom
				? 'BEFORE_LINK'
				: record
					? null
					: date >= healthMockSyncedDate
						? 'UNSYNCED'
						: 'NO_RECORD';
		return {
			date,
			value: record?.value ?? null,
			missing: reason !== null,
			reason,
			isInProgress: date === HEART_MOCK_TODAY
		};
	});
	const completed = days.filter((day) => !day.isInProgress && day.reason !== 'BEFORE_LINK');
	const valid = records.filter((record) =>
		completed.some((day) => !day.missing && day.date === record.date)
	);
	const sorted = [...valid].sort((a, b) => a.value - b.value);
	const dated = (record: (typeof records)[number] | undefined) =>
		record ? { date: record.date, value: record.value } : null;
	const progress = days.find((day) => day.isInProgress);
	const lastDataAt = '2026-10-26T00:00:00Z';
	return {
		metric: 'heart_rates',
		unit: 'bpm',
		period: { start: dates[0], end: dates[dates.length - 1] },
		summary: {
			avg: valid.length
				? Math.round(
						(valid.reduce((sum, record) => sum + record.value * record.hours, 0) /
							valid.reduce((sum, record) => sum + record.hours, 0)) *
							100
					) / 100
				: null,
			dailyMin: dated(sorted[0]),
			dailyMax: dated(sorted.at(-1)),
			total: null,
			daysWithData: valid.length,
			daysUsedForSummary: valid.length,
			completedDays: completed.length,
			missingDays: days
				.filter((day) => day.reason === 'NO_RECORD' || day.reason === 'NULL_VALUE')
				.map((day) => day.date),
			unsyncedDays: [],
			linkedFrom: healthMockLinkedFrom
		},
		days,
		inProgress: progress
			? { date: progress.date, value: progress.value, missing: progress.missing, asOf: lastDataAt }
			: null,
		dataStatus: {
			lastDataAt,
			syncedUntil: healthMockSyncedUntil,
			lastSyncStatus: 'success',
			lastSuccessAt: '2026-10-26T01:10:00Z',
			checkedAt: '2026-10-26T02:00:00Z'
		},
		detail: {
			measuredMin: valid.length
				? {
						date: [...valid].sort((a, b) => a.min - b.min)[0].date,
						value: Math.min(...valid.map((record) => record.min))
					}
				: null,
			measuredMax: valid.length
				? {
						date: [...valid].sort((a, b) => b.max - a.max)[0].date,
						value: Math.max(...valid.map((record) => record.max))
					}
				: null,
			nights: [],
			sessions: [],
			dailyRanges: days.map((day) => {
				const record = records.find((record) => record.date === day.date);
				return { date: day.date, min: record?.min ?? null, max: record?.max ?? null };
			})
		}
	};
}
