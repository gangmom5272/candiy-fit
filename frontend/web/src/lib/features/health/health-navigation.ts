import type { HealthSummaryMetric } from './health-summary-api';
import { isSleepDetailDate } from './sleep-detail';

/** 화면 복귀에 필요한 UI 상태만 담는다. API 응답이나 계정 정보는 보관하지 않는다. */
export type HealthReturnContext = {
	referenceDate: string;
	section: HealthSummaryMetric;
	date: string;
	scrollY: number;
	chartScrollLeft: Partial<Record<HealthSummaryMetric, number>>;
};

export type SleepReturnContext = {
	date: string;
	scrollY: number;
};

/** 기존 활동·심박 상세의 기간과 운동 영역만 보존한다. 임의 URL·픽셀 위치는 저장하지 않는다. */
export type MetricReturnContext = {
	source: 'activity' | 'heart';
	referenceDate: string;
	exercise: boolean;
};

function isRecord(value: unknown): value is Record<string, unknown> {
	if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
	const prototype = Object.getPrototypeOf(value);
	return prototype === Object.prototype || prototype === null;
}

function isSection(value: unknown): value is HealthSummaryMetric {
	return (
		value === 'steps' || value === 'exercises' || value === 'sleeps' || value === 'heart_rates'
	);
}

function isScroll(value: unknown): value is number {
	return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

function isDate(value: unknown, today: string): value is string {
	return (
		typeof value === 'string' &&
		!value.startsWith('0000') &&
		isSleepDetailDate(value) &&
		value <= today
	);
}

/** 요약 카드가 전달한 초기 조회 기준일. 부재·잘못된 날짜·미래 날짜는 사용하지 않는다. */
export function readHealthReferenceDate(value: unknown, today: string): string | null {
	return isDate(today, today) && isDate(value, today) ? value : null;
}

/** PageState는 직접 진입·새 탭에서는 없을 수 있으므로 잘못된 값은 복귀 정보 없이 처리한다. */
export function readHealthReturn(state: unknown, today: string): HealthReturnContext | null {
	try {
		if (!isSleepDetailDate(today) || !isRecord(state) || !isRecord(state.healthReturn)) return null;
		const context = state.healthReturn;
		const { referenceDate, section, date, scrollY, chartScrollLeft: storedScroll } = context;
		if (
			!isDate(referenceDate, today) ||
			!isDate(date, today) ||
			!isSection(section) ||
			!isScroll(scrollY) ||
			!isRecord(storedScroll)
		)
			return null;
		// 선택한 점은 출발 화면의 기준일까지 최근 7일 안에 있어야 한다.
		const dayOffset = Date.parse(referenceDate) - Date.parse(date);
		if (dayOffset < 0 || dayOffset > 6 * 86400000) return null;
		const chartScrollLeft: HealthReturnContext['chartScrollLeft'] = {};
		for (const [metric, left] of Object.entries(storedScroll)) {
			if (!isSection(metric) || !isScroll(left)) return null;
			chartScrollLeft[metric] = left;
		}
		return { referenceDate, section, date, scrollY, chartScrollLeft };
	} catch {
		// history state에 비정상 객체가 들어와도 화면 진입을 막지 않는다.
		return null;
	}
}

/** 하루 상세에서 돌아갈 직전 수면 상세의 날짜와 문서 위치만 읽는다. */
export function readSleepReturn(state: unknown, today: string): SleepReturnContext | null {
	try {
		if (!isSleepDetailDate(today) || !isRecord(state) || !isRecord(state.sleepReturn)) return null;
		const { date, scrollY } = state.sleepReturn;
		if (!isDate(date, today) || !isScroll(scrollY)) return null;
		return { date, scrollY };
	} catch {
		return null;
	}
}

export function readMetricReturn(state: unknown, today: string): MetricReturnContext | null {
	try {
		if (!isSleepDetailDate(today) || !isRecord(state) || !isRecord(state.metricReturn)) return null;
		const { source, referenceDate, exercise } = state.metricReturn;
		if (
			(source !== 'activity' && source !== 'heart') ||
			!isDate(referenceDate, today) ||
			typeof exercise !== 'boolean' ||
			(source === 'heart' && exercise)
		)
			return null;
		return { source, referenceDate, exercise };
	} catch {
		return null;
	}
}

export function shouldHandleHealthLink(
	event: Pick<
		MouseEvent,
		'button' | 'metaKey' | 'ctrlKey' | 'shiftKey' | 'altKey' | 'defaultPrevented'
	>
): boolean {
	return (
		event.button === 0 &&
		!event.metaKey &&
		!event.ctrlKey &&
		!event.shiftKey &&
		!event.altKey &&
		!event.defaultPrevented
	);
}
