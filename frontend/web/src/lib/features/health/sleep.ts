/** REST 지표 결과 중 수면 화면에서 사용하는 계약 (api-spec 2.2). */
export interface SleepNight {
	date: string;
	startAt: string;
	endAt: string;
	sleepMinutes: number | null;
	deepMinutes: number | null;
	remMinutes: number | null;
	lightMinutes: number | null;
	awakeMinutes: number | null;
}

export interface SleepMetricResult {
	metric: 'sleeps' | 'oxygen_saturations';
	period: { start: string; end: string };
	unit: 'min' | '%';
	summary: {
		avg: number | null;
		dailyMin: { date: string; value: number } | null;
		dailyMax: { date: string; value: number } | null;
		total: null;
		daysWithData: number;
		daysUsedForSummary: number;
		completedDays: number;
		missingDays: string[];
		unsyncedDays: string[];
		linkedFrom: string | null;
	};
	days: {
		date: string;
		value: number | null;
		missing: boolean;
		reason: 'NO_RECORD' | 'NULL_VALUE' | 'UNSYNCED' | 'BEFORE_LINK' | null;
		isInProgress: boolean;
	}[];
	inProgress: null;
	dataStatus: {
		lastDataAt: string | null;
		syncedUntil: string | null;
		lastSyncStatus: 'success' | 'fail' | null;
		lastSuccessAt: string | null;
		checkedAt: string;
	};
	detail: {
		measuredMin: { date: string; value: number } | null;
		measuredMax: { date: string; value: number } | null;
		nights: SleepNight[];
		dailyRanges: { date: string; min: number | null; max: number | null }[];
		sessions: [];
	};
}

export function formatSleepMinutes(value: number | null): string {
	if (value === null) return '기록 없음';
	const minutes = Math.round(value);
	return `${Math.floor(minutes / 60)}시간 ${minutes % 60}분`;
}

export function formatSleepPeriod(period: SleepMetricResult['period']): string {
	if (period.start === period.end) return period.start.replaceAll('-', '.');
	const end =
		period.start.slice(0, 4) === period.end.slice(0, 4) ? period.end.slice(5) : period.end;
	return `${period.start.replaceAll('-', '.')} ~ ${end.replaceAll('-', '.')}`;
}

/** 같은 사유의 연속 결측일만 묶는다. 정상 날짜와 날짜 공백은 보존한다. */
export function groupSleepMissingDays(days: SleepMetricResult['days']) {
	const groups: {
		start: string;
		end: string;
		reason: SleepMetricResult['days'][number]['reason'];
	}[] = [];
	for (const day of days) {
		if (!day.missing) continue;
		const previous = groups.at(-1);
		if (
			previous &&
			previous.reason === day.reason &&
			Date.parse(day.date) - Date.parse(previous.end) === 86400000
		)
			previous.end = day.date;
		else groups.push({ start: day.date, end: day.date, reason: day.reason });
	}
	return groups;
}

export function kstMinutes(timestamp: string): number {
	const date = new Date(new Date(timestamp).getTime() + 9 * 60 * 60 * 1000);
	return date.getUTCHours() * 60 + date.getUTCMinutes();
}

function formatClock(minutes: number): string {
	const wrapped = ((Math.round(minutes) % 1440) + 1440) % 1440;
	return `${String(Math.floor(wrapped / 60)).padStart(2, '0')}:${String(wrapped % 60).padStart(2, '0')}`;
}

export function sleepClock(timestamp: string): string {
	return formatClock(kstMinutes(timestamp));
}

/** 원형 평균으로 23:50과 00:10의 평균이 00:00이 되도록 한다. */
export function averageSleepClock(timestamps: string[]): string | null {
	if (timestamps.length === 0) return null;
	const angles = timestamps.map((timestamp) => (kstMinutes(timestamp) / 1440) * Math.PI * 2);
	const sin = angles.reduce((sum, angle) => sum + Math.sin(angle), 0);
	const cos = angles.reduce((sum, angle) => sum + Math.cos(angle), 0);
	if (Math.hypot(sin, cos) < 1e-8) return null;
	return formatClock((Math.atan2(sin, cos) / (Math.PI * 2)) * 1440);
}

export function sleepAverageDelta(current: number | null, previous: number | null): number | null {
	return current === null || previous === null ? null : Math.round(current - previous);
}

/** 종료일의 전날 00:00부터 센 분. 날짜 차이도 반영하므로 자정을 넘는 구간을 보존한다. */
export function sleepInterval(night: SleepNight): { start: number; end: number } {
	const origin = Date.parse(`${night.date}T00:00:00+09:00`) - 86400000;
	return {
		start: (Date.parse(night.startAt) - origin) / 60000,
		end: (Date.parse(night.endAt) - origin) / 60000
	};
}

/** 결측일을 건너 선을 잇지 않는다. x는 전체 날짜 배열의 순서를 유지한다. */
export function oxygenSegments(
	days: SleepMetricResult['days'],
	domain = oxygenDomain(days)
): string[] {
	const segments: string[] = [];
	let points: string[] = [];
	for (const [index, day] of days.entries()) {
		if (day.missing || day.value === null) {
			if (points.length) segments.push(points.join(' '));
			points = [];
		} else {
			points.push(`${oxygenX(index, days.length)},${oxygenY(day.value, domain)}`);
		}
	}
	if (points.length) segments.push(points.join(' '));
	return segments;
}

export const oxygenX = (index: number, count: number) =>
	36 + (index / Math.max(1, count - 1)) * 528;
export function oxygenDomain(days: SleepMetricResult['days']) {
	const values = days.flatMap((day) => (!day.missing && day.value !== null ? [day.value] : []));
	return {
		min: Math.floor(Math.min(90, ...values) / 2) * 2,
		max: Math.ceil(Math.max(100, ...values) / 2) * 2
	};
}
export const oxygenY = (value: number, domain = { min: 90, max: 100 }) =>
	16 + ((domain.max - value) / (domain.max - domain.min)) * 88;
