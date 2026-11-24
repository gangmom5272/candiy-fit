import { formatHealthWeek } from './period';
import type { HealthMetricDay } from './day-detail';
// 일별 걸음 수는 Figma 509:413 기준 mock이며, 기본 기간은 기준일 직전 완료된 월~일 주간이다.
const dayMilliseconds = 86_400_000;

/** 표시만 반올림한다. 집계·막대 높이에는 원본 평균값을 사용한다. */
export function formatAverageSteps(value: number): string {
	return value.toLocaleString('ko-KR', { maximumFractionDigits: 0 });
}

/** 칼로리는 표시할 때만 최대 소수 한 자리로 반올림한다. */
export function formatCalories(value: number): string {
	return `${value.toLocaleString('ko-KR', { maximumFractionDigits: 1 })} kcal`;
}

/** 운동 심박 표시만 유효숫자 15자리로 정리한다. 소수 자릿수를 고정하지 않고 원본·좌표는 유지한다. */
export function formatExerciseHeartRate(value: number): string {
	return `${value.toLocaleString('ko-KR', { maximumSignificantDigits: 15 })} bpm`;
}

/** 표시상 0인 증감률은 부호 없이 반환한다. 색상도 이 표시값으로 판단한다. */
export function formatActivityChange(value: number | null): string {
	if (value === null) return '—';
	const rounded = Number(value.toFixed(1));
	return rounded === 0 ? '0.0%' : `${rounded > 0 ? '+' : ''}${rounded.toFixed(1)}%`;
}

/** 같은 결측 사유가 연속된 날짜만 묶는다. 원본 날짜·값·사유는 변경하지 않는다. */
export function groupActivityMissingDays(
	days: readonly Pick<HealthMetricDay, 'date' | 'missing' | 'reason'>[]
) {
	const groups: { start: string; end: string; reason: HealthMetricDay['reason'] }[] = [];
	for (const day of days) {
		if (!day.missing) continue;
		const previous = groups.at(-1);
		if (
			previous &&
			previous.reason === day.reason &&
			Date.parse(day.date) - Date.parse(previous.end) === dayMilliseconds
		)
			previous.end = day.date;
		else groups.push({ start: day.date, end: day.date, reason: day.reason });
	}
	return groups;
}
export const activityReferenceDate = '2026-10-26';
const referenceTime = Date.parse(activityReferenceDate);
const daysSinceMonday = (new Date(referenceTime).getUTCDay() + 6) % 7;
const referenceStart = referenceTime - (daysSinceMonday + 7) * dayMilliseconds;
const referenceDates = Array.from({ length: 7 }, (_, index) =>
	new Date(referenceStart + index * dayMilliseconds).toISOString().slice(0, 10)
);
const weekdayLabels = ['일', '월', '화', '수', '목', '금', '토'] as const;
const stepValues = [8120, 7680, null, 9240, 8560, 5200, 7760] as const;

export interface ActivityDay {
	missing?: boolean;
	reason?: HealthMetricDay['reason'];
	date: string;
	label: string;
	weekday: string;
	steps: number | null;
}

export interface ActivityPeriod {
	label: string;
	hasRecords: boolean;
	days: ActivityDay[];
}

function dateParts(date: Date) {
	return {
		year: date.getUTCFullYear(),
		month: String(date.getUTCMonth() + 1).padStart(2, '0'),
		day: String(date.getUTCDate()).padStart(2, '0')
	};
}

export function getActivityPeriod(weekOffset: number): ActivityPeriod {
	const start = new Date(referenceStart + Math.min(weekOffset, 1) * 7 * dayMilliseconds);
	const hasRecords = weekOffset === 0;
	return {
		label: formatHealthWeek(start.toISOString().slice(0, 10)),
		hasRecords,
		days: stepValues.flatMap((steps, index) => {
			const date = new Date(start.getTime() + index * dayMilliseconds);
			if (date.getTime() > referenceTime) return [];
			const parts = dateParts(date);
			return [
				{
					date: `${parts.year}-${parts.month}-${parts.day}`,
					label: `${parts.month}/${parts.day}`,
					weekday: weekdayLabels[date.getUTCDay()],
					steps: hasRecords ? steps : null
				}
			];
		})
	};
}

export const stepSummary = { average: '7,760보', change: '+10.9%', recordedDays: 6 };
export const weekdayOrder = ['월', '화', '수', '목', '금', '토', '일'];

export interface StepRecord {
	date: string;
	value: number | null;
	missing: boolean;
	isInProgress?: boolean;
}

export interface HourlyStepsDay {
	date: string;
	hourlySteps: readonly number[] | null;
}

// 확인 가능한 퍼블리싱 기록만 제공한다. 이전 주에 없는 기록을 임의 생성하지 않는다.
export const recentStepRecords: readonly StepRecord[] = getActivityPeriod(0).days.map(
	({ date, steps }) => ({
		date,
		value: steps,
		missing: steps === null
	})
);

// Figma의 시간대별 모양을 바탕으로 하루 합계와 일치하는 24칸의 합성 걸음 수를 만든다.
const hourlyWeights = [
	0.2, 0.1333, 0.0667, 0.1, 0.1667, 0.3, 0.5, 0.7333, 0.6, 0.3333, 0.4333, 0.5667, 0.6667, 0.3667,
	0.3, 0.6333, 0.8333, 0.9333, 1, 0.7667, 0.5667, 0.3, 0.1667, 0.1
];
export const hourlyStepDays: readonly HourlyStepsDay[] = getActivityPeriod(0).days.map((day) => {
	if (day.steps === null) return { date: day.date, hourlySteps: null };
	const totalWeight = hourlyWeights.reduce((sum, value) => sum + value, 0);
	const total = day.steps;
	const hourlySteps = hourlyWeights.map((weight) => Math.floor((total * weight) / totalWeight));
	hourlySteps[18] += total - hourlySteps.reduce((sum, value) => sum + value, 0);
	return { date: day.date, hourlySteps };
});

export function getWeekdayAverages(
	records: readonly StepRecord[],
	endDate = activityReferenceDate
) {
	const end = Date.parse(endDate);
	const start = end - 27 * dayMilliseconds;
	return weekdayOrder.map((weekday) => {
		const values = records.flatMap((record) => {
			const date = Date.parse(record.date);
			return date >= start &&
				date <= end &&
				record.isInProgress !== true &&
				record.missing === false &&
				record.value !== null &&
				weekdayLabels[new Date(date).getUTCDay()] === weekday
				? [record.value]
				: [];
		});
		return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
	});
}

export function getHourlyStepAverages(days: readonly HourlyStepsDay[]): number[] | null {
	const measured = days.flatMap(({ hourlySteps }) =>
		hourlySteps !== null && hourlySteps.length === 24 ? [hourlySteps] : []
	);
	if (!measured.length) return null;
	return Array.from(
		{ length: 24 },
		(_, hour) => measured.reduce((sum, values) => sum + values[hour], 0) / measured.length
	);
}

export function getHourlyStepSummary(values: readonly number[] | null) {
	if (values === null || !values.length) return null;
	const min = Math.min(...values);
	const max = Math.max(...values);
	return {
		average: values.reduce((sum, value) => sum + value, 0) / values.length,
		min: { hour: values.indexOf(min), value: min },
		max: { hour: values.indexOf(max), value: max }
	};
}

export interface ExerciseMetric {
	average?: number | null;
	kind: 'time' | 'calories' | 'heart';
	title: string;
	value: string;
	description: string;
	detail: string;
	trend: readonly (number | null)[];
	hasRecords: boolean;
}

export interface ExerciseType {
	label: string;
	count: string;
	duration: string;
}

export interface ExerciseSession {
	id: string;
	date: string;
	typeName: string;
	durationMinutes: number;
	countsAsExercise: boolean;
	avgSpeedMps: number | null;
	caloriesKcal: number | null;
	avgHeartRate: number | null;
	maxHeartRate: number | null;
}

// 인정 여부는 저장된 API 값에 대응한다. 속도는 표시용이며 인정 여부 계산에 사용하지 않는다.
export const exerciseSessions: readonly ExerciseSession[] = [
	{
		id: 'walk-1',
		date: referenceDates[0],
		typeName: '걷기',
		durationMinutes: 60,
		countsAsExercise: true,
		avgSpeedMps: 1.4,
		caloriesKcal: 340,
		avgHeartRate: 120,
		maxHeartRate: 140
	},
	{
		id: 'run-1',
		date: referenceDates[1],
		typeName: '달리기',
		durationMinutes: 38,
		countsAsExercise: true,
		avgSpeedMps: null,
		caloriesKcal: 420,
		avgHeartRate: 130,
		maxHeartRate: 150
	},
	{
		id: 'walk-2',
		date: referenceDates[3],
		typeName: '걷기',
		durationMinutes: 42,
		countsAsExercise: true,
		avgSpeedMps: 1.5,
		caloriesKcal: 370,
		avgHeartRate: 138,
		maxHeartRate: 154
	},
	{
		id: 'bike-1',
		date: referenceDates[5],
		typeName: '실내 자전거',
		durationMinutes: 24,
		countsAsExercise: true,
		avgSpeedMps: null,
		caloriesKcal: 220,
		avgHeartRate: 120,
		maxHeartRate: 137
	},
	{
		id: 'stretch-1',
		date: referenceDates[6],
		typeName: '스트레칭',
		durationMinutes: 15,
		countsAsExercise: true,
		avgSpeedMps: null,
		caloriesKcal: 280,
		avgHeartRate: 132,
		maxHeartRate: 145
	},
	...[
		{ date: referenceDates[0], speed: 3.2, duration: 30 },
		{ date: referenceDates[2], speed: 3.5, duration: 18 },
		{ date: referenceDates[4], speed: 2.9, duration: 24 },
		{ date: referenceDates[6], speed: 3.6, duration: 20 }
	].map(({ date, speed, duration }) => ({
		id: `excluded-walk-${date}`,
		date,
		typeName: '걷기',
		durationMinutes: duration,
		countsAsExercise: false,
		avgSpeedMps: speed / 3.6,
		caloriesKcal: null,
		avgHeartRate: null,
		maxHeartRate: null
	}))
];

function formatDuration(minutes: number) {
	return minutes >= 60 ? `${Math.floor(minutes / 60)}시간 ${minutes % 60}분` : `${minutes}분`;
}

export function getExerciseSummary(
	period: ActivityPeriod,
	sessions: readonly ExerciseSession[],
	serverTime?: { avg: number | null; total: number | null; values: (number | null)[] }
) {
	const selected = sessions.filter((session) =>
		period.days.some((day) => day.date === session.date)
	);
	const recognized = selected.filter((session) => session.countsAsExercise);
	const timeTrend =
		serverTime?.values ??
		period.days.map((day) => {
			const recorded = selected.filter((session) => session.date === day.date);
			return recorded.length
				? recorded
						.filter((session) => session.countsAsExercise)
						.reduce((sum, session) => sum + session.durationMinutes, 0)
				: null;
		});
	const caloriesTrend = period.days.map((day) => {
		const values = recognized.flatMap((session) =>
			session.date === day.date && session.caloriesKcal !== null ? [session.caloriesKcal] : []
		);
		return values.length ? values.reduce((sum, value) => sum + value, 0) : null;
	});
	const heartTrend = period.days.map((day) => {
		const values = recognized.flatMap((session) =>
			session.date === day.date && session.avgHeartRate !== null ? [session.avgHeartRate] : []
		);
		return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
	});
	const minutes = timeTrend.filter((value) => value !== null);
	const calories = caloriesTrend.filter((value) => value !== null);
	const hearts = recognized.flatMap((session) =>
		session.avgHeartRate === null ? [] : [session.avgHeartRate]
	);
	const peaks = recognized.flatMap((session) =>
		session.maxHeartRate === null ? [] : [session.maxHeartRate]
	);
	const total = serverTime ? serverTime.total : minutes.reduce((sum, value) => sum + value, 0);
	const timeAverage = serverTime
		? serverTime.avg
		: minutes.length
			? (total ?? 0) / minutes.length
			: null;
	const totalCalories = calories.reduce((sum, value) => sum + value, 0);
	const heartAverage = hearts.length
		? hearts.reduce((sum, value) => sum + value, 0) / hearts.length
		: null;
	const heartMaximum = peaks.length ? Math.max(...peaks) : null;
	const metrics: ExerciseMetric[] = [
		{
			kind: 'time',
			title: '운동 시간',
			value: timeAverage === null ? '기록 없음' : `평균 ${Math.round(timeAverage)}분`,
			average: timeAverage,
			description: minutes.length
				? `최고 ${formatDuration(Math.round(Math.max(...minutes)))}`
				: '—',
			detail: total === null ? '집계된 합계 없음' : `총 ${formatDuration(total)}`,
			trend: timeTrend,
			hasRecords: minutes.length > 0
		},
		{
			kind: 'calories',
			title: '소모 칼로리',
			value: calories.length
				? `평균 ${Math.round(totalCalories / calories.length)} kcal`
				: '기록 없음',
			description: calories.length ? `최고 ${formatCalories(Math.max(...calories))}` : '—',
			detail: `총 ${formatCalories(totalCalories)}`,
			trend: caloriesTrend,
			hasRecords: calories.length > 0
		},
		{
			kind: 'heart',
			title: '운동 중 심박수',
			value: heartAverage === null ? '기록 없음' : `평균 ${Math.round(heartAverage)} bpm`,
			description: heartMaximum === null ? '최고 심박수 기록 없음' : `최고 ${heartMaximum} bpm`,
			detail: '운동 기록 기준',
			trend: heartTrend,
			hasRecords: hearts.length > 0
		}
	];
	const types: ExerciseType[] = [...new Set(recognized.map((session) => session.typeName))].map(
		(label) => {
			const group = recognized.filter((session) => session.typeName === label);
			return {
				label,
				count: `${group.length}회`,
				duration: formatDuration(group.reduce((sum, session) => sum + session.durationMinutes, 0))
			};
		}
	);
	return {
		metrics,
		types,
		hasRecords: minutes.length > 0,
		excludedWalks: selected.filter(
			(session) => !session.countsAsExercise && session.typeName === '걷기'
		)
	};
}
