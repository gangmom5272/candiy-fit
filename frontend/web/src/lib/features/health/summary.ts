import {
	activityReferenceDate,
	getActivityPeriod,
	getExerciseSummary,
	exerciseSessions
} from './activity';
import { completedSleepResult } from './sleep-mock';
import { getHeartMockWeek } from './heart-mock';
import { formatSleepMinutes } from './sleep';
import type { HealthMetricDay } from './day-detail';

export interface HealthSummary {
	id: string;
	label: string;
	path: 'activity' | 'sleep' | 'heart';
	hash?: '#exercise';
	value: string;
	description: string;
	kind: 'bar' | 'line';
	values: (number | null)[];
	average: number | null;
	min: number;
	max: number;
	unit: string;
	dates?: string[];
	days?: HealthMetricDay[];
	recordedDays?: number;
	completedDays?: number;
}
// 공유 차트 테스트용 예시 데이터. 운영 Home/Health는 실제 API 응답을 사용한다.
const period = getActivityPeriod(0);
export const summaryDates = period.days.map((day) => day.date.slice(5).replace('-', '.'));
export const summaryPeriodLabel = `${period.label} · 기준일 ${activityReferenceDate.replaceAll('-', '.')}`;
const steps = period.days.map((day) => day.steps);
const validSteps = steps.filter((value) => value !== null);
const stepsTotal = validSteps.reduce((sum, value) => sum + value, 0);
const stepsAverage = validSteps.length ? stepsTotal / validSteps.length : null;
const exercise = getExerciseSummary(period, exerciseSessions).metrics.find(
	(metric) => metric.kind === 'time'
);
const minutes = exercise?.trend.filter((value) => value !== null) ?? [];
const exerciseAverage = minutes.length
	? minutes.reduce((sum, value) => sum + value, 0) / minutes.length
	: null;
const heart = getHeartMockWeek(-1);
export const healthSummaries: HealthSummary[] = [
	{
		id: 'steps',
		dates: summaryDates,
		label: '걸음 수',
		path: 'activity',
		value:
			stepsAverage === null
				? '기록 없음'
				: `평균 ${Math.round(stepsAverage).toLocaleString('ko-KR')}보`,
		description: `총 ${stepsTotal.toLocaleString('ko-KR')}보`,
		kind: 'bar',
		values: steps,
		average: stepsAverage,
		min: 0,
		max: 10000,
		unit: '보'
	},
	{
		id: 'exercise',
		dates: summaryDates,
		label: '운동 시간',
		path: 'activity',
		hash: '#exercise',
		value: exercise?.value ?? '기록 없음',
		description: '직전 완료 주 · 인정된 운동 기준',
		kind: 'line',
		values: [...(exercise?.trend ?? [])],
		average: exerciseAverage,
		min: 0,
		max: 120,
		unit: '분'
	},
	{
		id: 'sleep',
		dates: summaryDates,
		label: '수면',
		path: 'sleep',
		value: `평균 ${formatSleepMinutes(completedSleepResult.summary.avg)}`,
		description: '직전 완료 주 · 실제 수면 시간',
		kind: 'bar',
		values: completedSleepResult.days.map((day) => (day.missing ? null : day.value)),
		average: completedSleepResult.summary.avg,
		min: 0,
		max: 480,
		unit: '분'
	},
	{
		id: 'heart',
		dates: summaryDates,
		label: '수면 중 심박수',
		path: 'heart',
		value: heart.summary.avg === null ? '기록 없음' : `평균 ${heart.summary.avg} bpm`,
		description: '직전 완료 주 · 일별 평균 심박',
		kind: 'line',
		values: heart.days.map((day) => (day.missing ? null : day.value)),
		average: heart.summary.avg,
		min: 60,
		max: 90,
		unit: 'bpm'
	}
];
