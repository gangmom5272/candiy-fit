import type { SleepMetricResult, SleepNight } from './sleep';

export type SleepStage = 'awake' | 'rem' | 'light' | 'deep' | 'unspecified' | 'unknown';
export interface SleepSegment {
	startAt: string;
	endAt: string;
	stage: SleepStage;
}

/** api-spec v12 5.4의 날짜별 timeline 중 수면 화면이 사용하는 필드. */
export interface SleepTimelineDay {
	date: string;
	mainSleep: Pick<SleepNight, 'startAt' | 'endAt'> | null;
	sleepSegments: SleepSegment[];
}

export const sleepStageStyles = {
	awake: { label: '깨어 있음', color: 'var(--color-surface)' },
	rem: { label: '렘 수면', color: 'var(--color-coral-soft)' },
	light: { label: '얕은 수면', color: 'var(--color-coral-sub)' },
	deep: { label: '깊은 수면', color: 'var(--color-brand)' }
} as const;
export const sleepStageOrder = ['awake', 'rem', 'light', 'deep'] as const;
export const sleepStageSummaryOrder = ['deep', 'light', 'rem', 'awake'] as const;

export function isSleepDetailDate(value: string): boolean {
	return (
		/^\d{4}-\d{2}-\d{2}$/.test(value) &&
		Number(value.slice(0, 4)) >= 1 &&
		Number.isFinite(Date.parse(value)) &&
		new Date(value).toISOString().slice(0, 10) === value
	);
}

export function sleepDateLabel(date: string): string {
	return `${Number(date.slice(5, 7))}월 ${Number(date.slice(8, 10))}일`;
}

export function sleepKstDate(timestamp: string): string {
	return new Date(Date.parse(timestamp) + 9 * 3600000).toISOString().slice(0, 10);
}

export function sleepMissingText(
	reason: SleepMetricResult['days'][number]['reason'] | undefined,
	scope: 'record' | 'value' = 'value'
): string {
	switch (reason) {
		case 'BEFORE_LINK':
			return scope === 'record' ? '연동 전의 기록은 확인할 수 없어요.' : '연동 전';
		case 'UNSYNCED':
			return scope === 'record' ? '아직 건강 데이터가 동기화되지 않았어요.' : '동기화 대기';
		case 'NULL_VALUE':
			return scope === 'record' ? '해당 정보가 기록되지 않았어요.' : '값 없음';
		case 'NO_RECORD':
		default:
			return scope === 'record' ? '해당 날짜의 수면 기록이 없어요.' : '기록 없음';
	}
}

export function sleepStageDurations(segments: SleepSegment[]) {
	const durations: Record<(typeof sleepStageOrder)[number], number | null> = {
		awake: segments.length ? 0 : null,
		rem: segments.length ? 0 : null,
		light: segments.length ? 0 : null,
		deep: segments.length ? 0 : null
	};
	for (const segment of segments) {
		if (segment.stage === 'unknown' || segment.stage === 'unspecified') continue;
		const duration = (Date.parse(segment.endAt) - Date.parse(segment.startAt)) / 60000;
		durations[segment.stage] = (durations[segment.stage] ?? 0) + duration;
	}
	return durations;
}

export function sleepStagePercentage(
	duration: number | null,
	sleepMinutes: number | null
): number | null {
	return duration === null || sleepMinutes === null || sleepMinutes <= 0
		? null
		: Math.round((duration / sleepMinutes) * 100);
}

export function sleepTimelinePosition(
	timestamp: string,
	mainSleep: NonNullable<SleepTimelineDay['mainSleep']>
): number {
	const start = Date.parse(mainSleep.startAt);
	const span = Date.parse(mainSleep.endAt) - start;
	return span > 0 ? ((Date.parse(timestamp) - start) / span) * 100 : 0;
}

/** 표시용 좌표만 제한한다. 원본 segment와 통계 계산에는 적용하지 않는다. */
export function sleepVisibleSegment(
	segment: SleepSegment,
	mainSleep: NonNullable<SleepTimelineDay['mainSleep']>
): { left: number; width: number } | null {
	const start = Date.parse(mainSleep.startAt);
	const end = Date.parse(mainSleep.endAt);
	const visibleStart = Math.max(start, Date.parse(segment.startAt));
	const visibleEnd = Math.min(end, Date.parse(segment.endAt));
	if (visibleEnd <= visibleStart) return null;
	return {
		left: ((visibleStart - start) / (end - start)) * 100,
		width: ((visibleEnd - visibleStart) / (end - start)) * 100
	};
}

export function sleepMidnights(mainSleep: NonNullable<SleepTimelineDay['mainSleep']>): string[] {
	const result: string[] = [];
	let midnight = Date.parse(`${sleepKstDate(mainSleep.startAt)}T00:00:00+09:00`) + 86400000;
	while (midnight <= Date.parse(mainSleep.endAt)) {
		result.push(new Date(midnight).toISOString());
		midnight += 86400000;
	}
	return result;
}

/** 시작·끝 근처 tick을 제외해 축 글자 충돌을 방지한다. 모바일은 가운데 tick만 사용한다. */
export function sleepTimelineTicks(
	mainSleep: NonNullable<SleepTimelineDay['mainSleep']>
): string[] {
	const start = Date.parse(mainSleep.startAt);
	const end = Date.parse(mainSleep.endAt);
	const result: string[] = [];
	for (let time = Math.ceil(start / 3600000) * 3600000; time < end; time += 3600000) {
		if (time - start >= 45 * 60000 && end - time >= 45 * 60000)
			result.push(new Date(time).toISOString());
	}
	return result;
}
