import type { HealthSummary } from './summary';

type AxisInput = Pick<HealthSummary, 'min' | 'max' | 'unit' | 'values' | 'average'>;

/** 3개 눈금과 기존 차트 높이를 유지하면서 10,001보를 20,000보까지 늘리지 않는다. */
function integerInterval(required: number, unit: string): number {
	const magnitude = 10 ** Math.floor(Math.log10(required));
	const multiples = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
	// 걸음 축의 인접 간격 비율을 1.25 이하로 제한한다. 심박의 기존 눈금은 유지한다.
	if (unit === '보') multiples.push(1.2, 1.8, 3.5, 7.5);
	multiples.sort((a, b) => a - b);
	const multiple = multiples.find((value) => value * magnitude >= required) ?? 10;
	const interval = Math.ceil(multiple * magnitude);
	return Number.isFinite(interval) ? interval : required;
}

/** 표시 축만 계산한다. 일별 값·서버 평균·결측 상태는 변경하지 않는다. */
export function healthSummaryAxis(summary: AxisInput) {
	const values = summary.values.filter((value): value is number => value !== null);
	const min = Math.floor(Math.min(summary.min, ...values, summary.average ?? summary.min));
	const requiredMax = Math.max(summary.max, ...values, summary.average ?? summary.max);
	// 양 끝이 매우 큰 반대 부호일 때 차를 먼저 구하면 overflow가 발생한다.
	const requiredInterval = Math.max(1, requiredMax / 2 - min / 2);
	// 짧은 시간 축은 15분, 긴 시간 축은 30분·1시간 간격으로 정렬한다.
	const minuteStep = requiredInterval <= 120 ? 15 : requiredInterval <= 360 ? 30 : 60;
	const interval =
		summary.unit === '분'
			? Math.ceil(requiredInterval / minuteStep) * minuteStep
			: integerInterval(requiredInterval, summary.unit);
	const roundedMax = min + interval * 2;
	// 표현 가능한 범위를 넘는 올림만 포기한다. 실제 최댓값은 축에 그대로 포함한다.
	const max = Number.isFinite(roundedMax) ? Math.max(requiredMax, roundedMax) : requiredMax;
	return { min, max, ticks: [max, min / 2 + max / 2, min] };
}

/** 정상 범위는 기존 좌표식을 유지하고, 차가 overflow하는 극단 범위만 정규화한다. */
export function healthSummaryY(value: number, axis: { min: number; max: number }): number {
	const span = axis.max - axis.min;
	if (Number.isFinite(span)) return 96 - ((value - axis.min) / Math.max(1, span)) * 72;
	const scale = Math.max(Math.abs(axis.min), Math.abs(axis.max));
	return 96 - ((value / scale - axis.min / scale) / (axis.max / scale - axis.min / scale)) * 72;
}

export function formatHealthSummaryTick(value: number, unit: string): string {
	if (unit === '분' && value !== 0) {
		if (!Number.isSafeInteger(value)) return `${value.toExponential()}m`;
		if (value < 60) return `${value}분`;
		const hours = Math.floor(value / 60);
		const minutes = value % 60;
		return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
	}
	if (!Number.isSafeInteger(value)) return value.toExponential();
	return value.toLocaleString('ko-KR', { maximumFractionDigits: 0 });
}
