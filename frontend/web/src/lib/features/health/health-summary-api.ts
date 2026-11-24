import { api, type ApiClient } from '#lib/api/client.js';
import { ApiError } from '#lib/api/errors.js';
import { getActivityMetric } from './activity-api';
import { getSleepMetric } from './sleep-api';
import { getHeartMetric } from './heart-api';
import { formatSleepMinutes } from './sleep';
import type { HealthSummary } from './summary';

export const healthSummaryMetrics = ['steps', 'exercises', 'sleeps', 'heart_rates'] as const;
export type HealthSummaryMetric = (typeof healthSummaryMetrics)[number];

export const healthSummaryCards: Record<
	HealthSummaryMetric,
	Pick<HealthSummary, 'id' | 'label' | 'path' | 'hash' | 'kind' | 'min' | 'max' | 'unit'>
> = {
	steps: {
		id: 'steps',
		label: '걸음 수',
		path: 'activity',
		kind: 'bar',
		min: 0,
		max: 10000,
		unit: '보'
	},
	exercises: {
		id: 'exercise',
		label: '운동 시간',
		path: 'activity',
		hash: '#exercise',
		kind: 'line',
		min: 0,
		max: 120,
		unit: '분'
	},
	sleeps: { id: 'sleep', label: '수면', path: 'sleep', kind: 'bar', min: 0, max: 480, unit: '분' },
	heart_rates: {
		id: 'heart',
		label: '일별 심박수',
		path: 'heart',
		kind: 'line',
		min: 60,
		max: 90,
		unit: 'bpm'
	}
};

/** 각 API의 서버 summary와 날짜 상태를 표시 모델로 옮긴다. 평균·합계·인정 여부는 재집계하지 않는다. */
export async function getHealthSummary(
	metric: HealthSummaryMetric,
	start: string,
	end: string,
	signal?: AbortSignal,
	client: ApiClient = api
): Promise<HealthSummary> {
	const result =
		metric === 'sleeps'
			? await getSleepMetric('sleeps', start, end, signal, client)
			: metric === 'heart_rates'
				? await getHeartMetric(start, end, signal, client)
				: await getActivityMetric(metric, start, end, signal, client);
	const average = result.summary.avg;
	const days = [...result.days].sort((a, b) => a.date.localeCompare(b.date));
	const hasRecords = result.days.some((day) => !day.missing && day.value !== null);
	const formatted =
		average === null
			? null
			: metric === 'steps'
				? `${Math.round(average).toLocaleString('ko-KR')}보`
				: metric === 'exercises'
					? `${Math.round(average)}분`
					: metric === 'sleeps'
						? formatSleepMinutes(average)
						: `${average} bpm`;
	const description =
		metric === 'steps'
			? result.summary.total === null
				? '집계된 합계 없음'
				: `총 ${result.summary.total.toLocaleString('ko-KR')}보`
			: metric === 'exercises'
				? '인정된 운동 기준'
				: metric === 'sleeps'
					? '실제 수면 시간'
					: '일별 평균 심박';
	return {
		...healthSummaryCards[metric],
		value:
			formatted === null ? (hasRecords ? '집계된 평균 없음' : '기록 없음') : `평균 ${formatted}`,
		description,
		average,
		values: days.map((day) => (day.missing ? null : day.value)),
		dates: days.map((day) => day.date.slice(5).replace('-', '.')),
		days,
		recordedDays: result.summary.daysWithData,
		completedDays: result.summary.completedDays
	};
}

export function healthSummaryError(error: unknown): string {
	return error instanceof ApiError
		? `${error.message}${error.requestId ? ` (요청 ID: ${error.requestId})` : ''}`
		: '건강 기록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.';
}
