import { api, type ApiClient } from '#lib/api/client.js';
import { ApiError, ResponseFormatError } from '#lib/api/errors.js';
import {
	readArray,
	readBoolean,
	readEnum,
	readNullable,
	readNumber,
	readRecord,
	readString
} from '#lib/api/parse.js';
import { dayMissingValue } from '#lib/features/health/day-detail.js';
import type { HealthMetricDay } from '#lib/features/health/day-detail.js';
import { getActivityMetric } from '#lib/features/health/activity-api.js';
import { getSleepMetric } from '#lib/features/health/sleep-api.js';
import { isHealthDataNotReady } from '#lib/features/health/data-readiness.js';
import type { ScoreStatus } from './HomeScoreSummary.svelte';
import { homeScoreDescriptions } from './home-score-description';

export type HomeDailyMetric = 'steps' | 'exercises' | 'sleeps';

export async function getHomeMetric(
	metric: HomeDailyMetric,
	date: string,
	signal?: AbortSignal,
	client: ApiClient = api
): Promise<HealthMetricDay> {
	const result =
		metric === 'sleeps'
			? await getSleepMetric('sleeps', date, date, signal, client)
			: await getActivityMetric(metric, date, date, signal, client);
	const day = result.days.find((day) => day.date === date);
	if (!day) throw new ResponseFormatError('home metric day');
	return day;
}

interface ScoreArea {
	score: number | null;
	grade: 1 | 2 | 3 | 4 | null;
}

/** API v14 §5.3. 주간 지표 요약과 달리 선택한 하루의 저장 점수다. */
export interface LifestyleScore {
	date: string;
	isInProgress: boolean;
	reason: 'BEFORE_LINK' | 'UNSYNCED' | 'NO_RECORD' | null;
	total: { score: number | null; missingAreas: ('activity' | 'sleep' | 'heart')[] };
	areas: {
		activity: ScoreArea & {
			steps: { score: number | null; value: number | null };
			exercise: { score: number | null; minutes: number | null };
		};
		sleep: ScoreArea & { sleepMinutes: number | null };
		heart: ScoreArea & {
			sleepHrAvg: number | null;
			baselineHrAvg: number | null;
			baselineNights: number | null;
			checkWear: boolean | null;
		};
	};
	computedAt: string | null;
}

function scoreArea(row: Record<string, unknown>): ScoreArea {
	const grade = readNullable(row, 'grade', readNumber);
	if (grade !== null && grade !== 1 && grade !== 2 && grade !== 3 && grade !== 4)
		throw new ResponseFormatError('grade');
	return { score: readNullable(row, 'score', readNumber), grade };
}

export async function getLifestyleScore(
	date: string,
	signal?: AbortSignal,
	client: ApiClient = api
): Promise<LifestyleScore> {
	const data = readRecord(
		await client.request(`/health/lifestyle-score?${new URLSearchParams({ date })}`, { signal }),
		'lifestyle score'
	);
	const total = readRecord(data.total, 'total');
	const areas = readRecord(data.areas, 'areas');
	const activity = readRecord(areas.activity, 'activity');
	const steps = readRecord(activity.steps, 'steps');
	const exercise = readRecord(activity.exercise, 'exercise');
	const sleep = readRecord(areas.sleep, 'sleep');
	const heart = readRecord(areas.heart, 'heart');
	return {
		date: readString(data, 'date'),
		isInProgress: readBoolean(data, 'isInProgress'),
		reason: readNullable(data, 'reason', (row, key) =>
			readEnum(row, key, ['BEFORE_LINK', 'UNSYNCED', 'NO_RECORD'] as const)
		),
		total: {
			score: readNullable(total, 'score', readNumber),
			missingAreas: readArray(total, 'missingAreas').map((value) =>
				readEnum({ value }, 'value', ['activity', 'sleep', 'heart'] as const)
			)
		},
		areas: {
			activity: {
				...scoreArea(activity),
				steps: {
					score: readNullable(steps, 'score', readNumber),
					value: readNullable(steps, 'value', readNumber)
				},
				exercise: {
					score: readNullable(exercise, 'score', readNumber),
					minutes: readNullable(exercise, 'minutes', readNumber)
				}
			},
			sleep: { ...scoreArea(sleep), sleepMinutes: readNullable(sleep, 'sleepMinutes', readNumber) },
			heart: {
				...scoreArea(heart),
				sleepHrAvg: readNullable(heart, 'sleepHrAvg', readNumber),
				baselineHrAvg: readNullable(heart, 'baselineHrAvg', readNumber),
				baselineNights: readNullable(heart, 'baselineNights', readNumber),
				checkWear: readNullable(heart, 'checkWear', readBoolean)
			}
		},
		computedAt: readNullable(data, 'computedAt', readString)
	};
}

const grades = {
	1: { label: '훌륭', tone: 'success' },
	2: { label: '안정', tone: 'info' },
	3: { label: '관심', tone: 'warning' },
	4: { label: '관리', tone: 'danger' }
} as const;

export function homeRequestError(error: unknown): string {
	if (error instanceof ApiError && error.status === 409 && error.code === 'CANDIY_USER_NOT_LINKED')
		return '앱 연동을 확인해 주세요. 이미 연동했다면 첫 데이터가 준비될 때까지 기다린 뒤 다시 시도해 주세요.';
	return isHealthDataNotReady(error)
		? '건강 기록을 준비 중이에요. 잠시 후 다시 확인해 주세요.'
		: '건강 기록을 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.';
}

/** 숫자로 등급을 다시 판정하지 않고 서버 grade의 표시명만 연결한다. */
export function homeScoreStatuses(
	result: LifestyleScore | null,
	loading: boolean,
	error: string | null,
	preparing = false
): ScoreStatus[] {
	const descriptions = homeScoreDescriptions(result);
	return (['activity', 'sleep', 'heart'] as const).map((key, index) => {
		const area = result?.areas[key];
		const grade = area?.grade === null || area?.grade === undefined ? null : grades[area.grade];
		return {
			metric: ['활동', '수면', '심박'][index],
			label: loading
				? '불러오는 중'
				: preparing
					? '준비 중'
					: error
						? '조회 실패'
						: (grade?.label ?? dayMissingValue(result?.reason ?? null)),
			tone: grade?.tone ?? 'info',
			score: area?.score ?? null,
			description: loading
				? '점수와 건강 기록을 불러오는 중이에요.'
				: preparing
					? '점수와 건강 기록을 준비 중이에요. 잠시 후 다시 확인해 주세요.'
					: error || descriptions[index]
		};
	});
}
