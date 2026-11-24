import type { SleepMetricResult, SleepNight } from './sleep';
import {
	healthMockLinkedFrom as linkedFrom,
	healthMockSyncedDate,
	healthMockSyncedUntil
} from './mock-user';

/** 합성 데이터만 사용. 시계/실제 API에 의존하지 않는 퍼블리싱 기준일. */
export const SLEEP_MOCK_TODAY = '2026-10-26';

function datesEnding(end: string): string[] {
	return Array.from({ length: 7 }, (_, i) =>
		new Date(Date.parse(`${end}T00:00:00Z`) - (6 - i) * 86400000).toISOString().slice(0, 10)
	);
}

function night(date: string, start: string, end: string, minutes: number | null): SleepNight {
	const startDate =
		start >= end
			? new Date(Date.parse(`${date}T00:00:00Z`) - 86400000).toISOString().slice(0, 10)
			: date;
	return {
		date,
		startAt: new Date(`${startDate}T${start}:00+09:00`).toISOString(),
		endAt: new Date(`${date}T${end}:00+09:00`).toISOString(),
		sleepMinutes: minutes,
		deepMinutes: minutes === null ? null : 80,
		remMinutes: minutes === null ? null : 90,
		lightMinutes: minutes === null ? null : minutes - 170,
		awakeMinutes:
			minutes === null
				? null
				: (Date.parse(`${date}T${end}:00+09:00`) - Date.parse(`${startDate}T${start}:00+09:00`)) /
						60000 -
					minutes
	};
}

/** 모의 응답 생성에만 사용. 운영 API 계층이나 응답 adapter가 아니다. */
function metricResult(
	metric: SleepMetricResult['metric'],
	dates: string[],
	values: (number | null)[],
	nights: SleepNight[] = []
): SleepMetricResult {
	const days: SleepMetricResult['days'] = dates.map((date, i) => ({
		date,
		value: values[i],
		missing: values[i] === null,
		reason:
			date < linkedFrom
				? 'BEFORE_LINK'
				: values[i] !== null
					? null
					: date >= healthMockSyncedDate
						? 'UNSYNCED'
						: nights.some((n) => n.date === date) ||
							  (metric === 'oxygen_saturations' && allNights.some((n) => n.date === date))
							? 'NULL_VALUE'
							: 'NO_RECORD',
		isInProgress: false
	}));
	const valid = days.flatMap((d) => (d.value === null ? [] : [{ date: d.date, value: d.value }]));
	const sorted = [...valid].sort((a, b) => a.value - b.value);
	return {
		metric,
		period: { start: dates[0], end: dates[dates.length - 1] },
		unit: metric === 'sleeps' ? 'min' : '%',
		summary: {
			avg: valid.length
				? Math.round((valid.reduce((s, d) => s + d.value, 0) / valid.length) * 100) / 100
				: null,
			dailyMin: sorted[0] ?? null,
			dailyMax: valid.reduce<{ date: string; value: number } | null>(
				(best, d) => (best === null || d.value > best.value ? d : best),
				null
			),
			total: null,
			daysWithData: valid.length,
			daysUsedForSummary: valid.length,
			completedDays: dates.filter((date) => date >= linkedFrom).length,
			missingDays: days
				.filter((d) => d.reason === 'NO_RECORD' || d.reason === 'NULL_VALUE')
				.map((d) => d.date),
			unsyncedDays: days.filter((d) => d.reason === 'UNSYNCED').map((d) => d.date),
			linkedFrom
		},
		days,
		inProgress: null,
		dataStatus: {
			lastDataAt: `${SLEEP_MOCK_TODAY}T00:00:00Z`,
			syncedUntil: healthMockSyncedUntil,
			lastSyncStatus: 'success',
			lastSuccessAt: `${SLEEP_MOCK_TODAY}T01:10:00Z`,
			checkedAt: `${SLEEP_MOCK_TODAY}T02:00:00Z`
		},
		detail: { measuredMin: null, measuredMax: null, nights, dailyRanges: [], sessions: [] }
	};
}

const currentDates = datesEnding(SLEEP_MOCK_TODAY);
const previousDates = datesEnding('2026-10-19');
const currentNights = [
	night(currentDates[0], '00:42', '07:21', 399),
	night(currentDates[1], '01:10', '07:56', 386),
	// 10/22는 주 수면 자체가 없는 날.
	night(currentDates[3], '23:50', '07:28', 420),
	night(currentDates[4], '00:10', '07:32', 374),
	night(currentDates[5], '21:32', '06:42', 380),
	// 시각은 있지만 실제 수면 분을 알 수 없는 날도 보존한다.
	night(currentDates[6], '00:52', '08:16', null)
];
const previousNights = previousDates.map((date, i) => night(date, '00:20', '07:20', 370 + i));

// 기존 날짜별 값을 유지한 채 월요일 경계로만 다시 묶는다.
const allNights = [...previousNights, ...currentNights];
const oxygenValues = [96.2, 96.8, null, 95.4, null, 96.6, 95.8];
const oxygenByDate = new Map(currentDates.map((date, i) => [date, oxygenValues[i]]));

function weekDates(offset: number): string[] {
	const today = new Date(`${SLEEP_MOCK_TODAY}T00:00:00Z`);
	const monday = today.getTime() - ((today.getUTCDay() + 6) % 7) * 86400000;
	return Array.from({ length: 7 }, (_, i) =>
		new Date(monday + (offset * 7 + i) * 86400000).toISOString().slice(0, 10)
	).filter((date) => date <= SLEEP_MOCK_TODAY);
}

function sleepForWeek(dates: string[]): SleepMetricResult {
	const nights = allNights.filter((n) => dates.includes(n.date));
	return metricResult(
		'sleeps',
		dates,
		dates.map((date) => nights.find((n) => n.date === date)?.sleepMinutes ?? null),
		nights
	);
}

function oxygenForWeek(dates: string[]): SleepMetricResult {
	const result = metricResult(
		'oxygen_saturations',
		dates,
		dates.map((date) => oxygenByDate.get(date) ?? null)
	);
	result.detail.dailyRanges = result.days.map((day) => ({
		date: day.date,
		min: day.value === null ? null : day.value - 2,
		max: day.value === null ? null : 99
	}));
	for (const range of result.detail.dailyRanges) {
		if (
			range.min !== null &&
			(result.detail.measuredMin === null || range.min < result.detail.measuredMin.value)
		)
			result.detail.measuredMin = { date: range.date, value: range.min };
		if (
			range.max !== null &&
			(result.detail.measuredMax === null || range.max > result.detail.measuredMax.value)
		)
			result.detail.measuredMax = { date: range.date, value: range.max };
	}
	return result;
}

export const previousSleepResult = sleepForWeek(weekDates(-2));
export const completedSleepResult = sleepForWeek(weekDates(-1));
export const currentSleepResult = sleepForWeek(weekDates(0));
export const completedOxygenResult = oxygenForWeek(weekDates(-1));
export const currentOxygenResult = oxygenForWeek(weekDates(0));

/** 이전 주 → 현재 주. 선택 상태는 페이지 안에서만 관리한다. */
export const sleepMockWeeks = [
	{
		sleep: completedSleepResult,
		previousSleep: previousSleepResult,
		oxygen: completedOxygenResult
	},
	{ sleep: currentSleepResult, previousSleep: completedSleepResult, oxygen: currentOxygenResult }
];

/** 과거 주는 제한 없이 탐색한다. 기록이 없는 날짜에는 수치를 만들지 않는다. */
export function getSleepMockWeek(offset: number) {
	const selected = Math.min(0, offset);
	return {
		sleep: sleepForWeek(weekDates(selected)),
		previousSleep: sleepForWeek(weekDates(selected - 1)),
		oxygen: oxygenForWeek(weekDates(selected))
	};
}
