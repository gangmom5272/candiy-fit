import type { HeartMetricResult } from './heart';
import type { SleepTimelineDay } from './sleep-detail';

/** API v14 §2.2의 days[] 중 한 날짜. 0보 결측도 원래 값과 사유를 보존한다. */
export type HealthMetricDay = Omit<HeartMetricResult['days'][number], 'reason'> & {
	reason: HeartMetricResult['days'][number]['reason'] | 'ZERO_STEPS';
};

/** API v14 §5.4 days[] 원형. 시간대 걸음은 원소별 null이 아닌 배열 전체 null이다. */
export interface HealthTimelineDay extends SleepTimelineDay {
	isInProgress: boolean;
	hourlySteps: number[] | null;
	hourlyHeartRates: ({ avg: number; min: number; max: number } | null)[] | null;
	exerciseSegments: {
		startAt: string;
		endAt: string;
		exerciseType: number;
		typeName: string;
		title: string | null;
	}[];
}

export const dayTimeTicks = Array.from({ length: 9 }, (_, index) => index * 180);
export const dayTimeX = (minutes: number, start = 0): number =>
	((minutes - start) / (1440 - start)) * 100;
export const dayHourCenter = (hour: number): number => hour * 60 + 30;

export function adjacentDay(date: string, offset: number): string {
	return new Date(Date.parse(`${date}T00:00:00Z`) + offset * 86400000).toISOString().slice(0, 10);
}

/** 주 수면이 전날 시작했다면 원본 취침 시각까지 표시 범위만 확장한다. */
export function dayTimelineStart(timeline: Pick<HealthTimelineDay, 'date' | 'mainSleep'>): number {
	return timeline.mainSleep
		? Math.min(
				0,
				(Date.parse(timeline.mainSleep.startAt) - Date.parse(`${timeline.date}T00:00:00+09:00`)) /
					60000
			)
		: 0;
}

/** 원본 timestamp는 그대로 두고 타임라인 표시 범위와 겹치는 분만 구한다. */
export function clipDayInterval(date: string, startAt: string, endAt: string, rangeStart: number) {
	const origin = Date.parse(`${date}T00:00:00+09:00`);
	const start = Math.max(rangeStart, (Date.parse(startAt) - origin) / 60000);
	const end = Math.min(1440, (Date.parse(endAt) - origin) / 60000);
	return end > start ? { start, end } : null;
}

/** 결측 시간을 건너 이어 그리지 않는다. 각 점은 해당 KST 시간대의 가운데에 놓는다. */
export function dayHeartSegments(hours: HealthTimelineDay['hourlyHeartRates']) {
	const groups: { minute: number; value: number }[][] = [];
	let current: { minute: number; value: number }[] = [];
	for (const [hour, point] of (hours ?? []).entries()) {
		if (point === null) {
			if (current.length) groups.push(current);
			current = [];
		} else current.push({ minute: dayHourCenter(hour), value: point.avg });
	}
	if (current.length) groups.push(current);
	return groups;
}

export function dayMissingValue(reason: HealthMetricDay['reason']): string {
	switch (reason) {
		case 'BEFORE_LINK':
			return '연동 전';
		case 'UNSYNCED':
			return '동기화 대기';
		case 'NULL_VALUE':
			return '값 없음';
		default:
			return '기록 없음';
	}
}

export function formatDayMetric(day: HealthMetricDay, unit: 'steps' | 'min' | 'bpm'): string {
	if (day.missing || day.value === null) return dayMissingValue(day.reason);
	if (unit === 'steps') return `${day.value.toLocaleString('ko-KR')}보`;
	return `${day.value}${unit === 'min' ? '분' : ' bpm'}`;
}
