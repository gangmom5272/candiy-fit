import type { HealthMetric } from './types';

/** 눈금 단위. 수면·운동은 분 단위 값을 시간 눈금으로 나눈다. */
const TICK_STEP: Record<HealthMetric, number> = {
	sleeps: 60,
	exercises: 30,
	steps: 2000,
	heart_rates: 20,
	oxygen_saturations: 5,
	weights: 10,
	body_fats: 5,
	basal_metabolic_rates: 500,
	// 키는 m 값이라 0.5m(=50cm) 간격
	heights: 0.5
};

/** 0을 빼고 눈금 칸은 최대 4개 */
const MAX_TICK_INTERVALS = 4;

/** 산소포화도는 100%를 넘을 수 없어 축도 100에서 멈춘다 */
const AXIS_CEILING: Partial<Record<HealthMetric, number>> = {
	oxygen_saturations: 100,
	body_fats: 100
};

/** min 은 축의 맨 아래 값. 대부분 0이고 산소포화도만 90(더 낮은 값이 있으면 그 아래)에서 시작한다 */
export type ChartScale = { max: number; ticks: number[]; min: number };

/** 값이 축에서 차지하는 높이(0~1) */
export function scaleFraction(scale: ChartScale, value: number): number {
	return Math.min(1, Math.max(0, (value - scale.min) / (scale.max - scale.min)));
}

/**
 * 막대 대신 꺾은선과 점으로 그리는 지표(S15P31A403-388, 앱 384와 같다).
 * 산소포화도는 값이 90~100에 모여 있어 0에서 시작하는 막대로는 차이가 보이지 않는다
 */
export function isLineChart(metric: HealthMetric): boolean {
	return metric === 'oxygen_saturations';
}

const SPO2_AXIS_FLOOR = 90;
const SPO2_AXIS_TOP = 100;

/**
 * 산소포화도 축. 90에서 시작해 100까지, 눈금은 5 간격(90·95·100)이다.
 * 90보다 낮은 값이 있으면 그 값이 들어가도록 아래로 넓히고, 칸이 4개를 넘으면 간격을 10·15…로 늘린다
 */
function spo2Scale(values: (number | null)[], average: number | null): ChartScale {
	const present = [...values, average].filter((value): value is number => value !== null);
	const smallest = Math.min(SPO2_AXIS_FLOOR, ...present);
	const floorFor = (step: number) =>
		SPO2_AXIS_TOP - Math.ceil((SPO2_AXIS_TOP - smallest) / step) * step;
	let interval = 5;
	while ((SPO2_AXIS_TOP - floorFor(interval)) / interval > MAX_TICK_INTERVALS) interval += 5;
	const min = Math.max(0, floorFor(interval));
	return {
		max: SPO2_AXIS_TOP,
		min,
		ticks: Array.from(
			{ length: Math.round((SPO2_AXIS_TOP - min) / interval) + 1 },
			(_, index) => min + index * interval
		)
	};
}

/**
 * 값과 평균이 모두 들어가는 축. 눈금 간격은 TICK_STEP의 배수라 2h·4h, 20·40처럼 딱 떨어진다.
 * 간격을 키워 가며 칸이 4개 이하가 되는 첫 간격을 쓴다. 값이 없으면 눈금 하나 높이다.
 * 가장 큰 값 위에 여유를 따로 두지 않는다(S15P31A403-404). 예전에는 10%를 더해 올림해서
 * 8시간 36분이 12h 축이 되고 막대가 상자의 절반만 썼다. 평균 수치를 선 옆에 쓰지 않게 된 뒤로는 여유가 필요 없다.
 * references 는 비교 기간 평균처럼 함께 그리는 기준선 값이다
 */
export function chartScale(
	metric: HealthMetric,
	values: (number | null)[],
	average: number | null,
	references: number[] = []
): ChartScale {
	if (isLineChart(metric)) return spo2Scale(values, average);
	const step = TICK_STEP[metric];
	const largest = Math.max(0, average ?? 0, ...references, ...values.map((value) => value ?? 0));
	const target = Math.min(largest, AXIS_CEILING[metric] ?? Infinity);

	let interval = step;
	let max = Math.max(step, Math.ceil(target / step) * step);
	while (max / interval > MAX_TICK_INTERVALS) {
		interval += step;
		max = Math.ceil(target / interval) * interval;
	}
	return {
		max,
		min: 0,
		ticks: Array.from({ length: max / interval + 1 }, (_, index) => index * interval)
	};
}

export function formatAxisValue(metric: HealthMetric, value: number): string {
	switch (metric) {
		case 'sleeps':
			return `${Math.round(value / 60)}h`;
		case 'exercises':
			return `${Math.round(value)}분`;
		case 'steps':
			return value === 0 ? '0' : `${(value / 1000).toFixed(1).replace(/\.0$/, '')}k`;
		case 'heights':
			return `${Math.round(value * 100)}`;
		default:
			return `${Math.round(value)}`;
	}
}

export type BarTone = 'high' | 'normal';

/**
 * 막대 색 기준: 평균 이상은 진하게, 평균 미만은 옅게. 두 가지뿐이다(S15P31A403-404).
 * 기간 최저값을 따로 칠하던 세 번째 색은 없앴다. 가장 높은 날과 낮은 날은 막대 위 꼬리표로 알린다.
 * 수집 중인 오늘도 같은 규칙으로 칠한다. 예전에는 점선 테두리로 따로 그렸는데, 기록 없는 날의 점선 칸과 헷갈렸다.
 * 막대 위에는 "오늘"이라고만 적는다
 */
export function barTone(value: number, average: number | null): BarTone {
	return average !== null && value >= average ? 'high' : 'normal';
}
