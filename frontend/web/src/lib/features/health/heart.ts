/** api-spec v13 §2.2 REST heart_rates 응답. 오늘은 days에 포함되고 summary에서는 제외된다. */
export interface HeartMetricResult {
	metric: 'heart_rates';
	period: { start: string; end: string };
	unit: 'bpm';
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
		reason: 'BEFORE_LINK' | 'UNSYNCED' | 'NO_RECORD' | 'NULL_VALUE' | null;
		isInProgress: boolean;
	}[];
	inProgress: {
		date: string;
		value: number | null;
		missing: boolean;
		asOf: string | null;
	} | null;
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
		nights: [];
		dailyRanges: { date: string; min: number | null; max: number | null }[];
		sessions: [];
	};
}

export function formatHeartPeriod(period: HeartMetricResult['period']): string {
	if (period.start === period.end) return period.start.replaceAll('-', '.');
	const end =
		period.start.slice(0, 4) === period.end.slice(0, 4) ? period.end.slice(5) : period.end;
	return `${period.start.replaceAll('-', '.')} ~ ${end.replaceAll('-', '.')}`;
}

/** 날짜는 KST 달력 날짜 문자열이며 브라우저의 로컬 시간대로 변환하지 않는다. */
export function heartWeekDates(today: string, offset: number): string[] {
	const date = new Date(`${today}T00:00:00Z`);
	const monday = date.getTime() - ((date.getUTCDay() + 6) % 7) * 86400000;
	return Array.from({ length: 7 }, (_, index) =>
		new Date(monday + (offset * 7 + index) * 86400000).toISOString().slice(0, 10)
	).filter((day) => day <= today);
}

/** 차트 표시 범위만 계산한다. 건강 상태를 판정하는 기준이 아니다. */
export function heartChartDomain(result: HeartMetricResult): {
	min: number;
	max: number;
	ticks: number[];
} {
	const values = result.days.flatMap((day) => {
		if (day.missing) return [];
		const range = result.detail.dailyRanges.find((range) => range.date === day.date);
		return [day.value, range?.min, range?.max].filter((value): value is number => value != null);
	});
	if (result.summary.avg !== null) values.push(result.summary.avg);
	const lower = Math.floor(Math.min(40, ...values) / 20) * 20;
	const upper = Math.ceil(Math.max(160, ...values) / 20) * 20;
	// 넓은 범위에서도 축 글자가 겹치지 않도록 눈금 수를 제한한다.
	const step = Math.ceil((upper - lower) / 120) * 20;
	const min = Math.floor(lower / step) * step;
	const max = Math.ceil(upper / step) * step;
	const ticks = Array.from(
		{ length: Math.floor((max - min) / step) + 1 },
		(_, i) => min + i * step
	);
	return { min, max, ticks };
}

/** 시작일이 주어지면 해당 날짜부터 7칸에 배치한다. 기존 달력 주간 호출도 유지한다. */
export function heartX(date: string, start?: string): number {
	const slot = start
		? (Date.parse(`${date}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000
		: (new Date(`${date}T00:00:00Z`).getUTCDay() + 6) % 7;
	return ((slot + 0.5) / 7) * 100;
}
export const heartY = (value: number, domain: { min: number; max: number }): number =>
	((domain.max - value) / (domain.max - domain.min)) * 100;

export function heartMissingMessage(reason: HeartMetricResult['days'][number]['reason']): string {
	switch (reason) {
		case 'BEFORE_LINK':
			return '연동 전 날짜예요.';
		case 'UNSYNCED':
			return '아직 동기화되지 않았어요. 앱을 열어 동기화해 주세요.';
		case 'NULL_VALUE':
			return '기록에 심박수 값이 없어요.';
		case 'NO_RECORD':
			return '심박 기록이 없어요.';
		default:
			return '심박 기록이 없어요.';
	}
}

/** 안내 표시만 축약한다. 원본 결측 사유와 측정값은 변경하지 않는다. */
export function groupHeartMissingDays(days: HeartMetricResult['days']) {
	const groups: {
		start: string;
		end: string;
		reason: HeartMetricResult['days'][number]['reason'];
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
