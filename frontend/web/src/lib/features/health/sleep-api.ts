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
import { activityWeek } from './activity-api';
import { adjacentDay } from './day-detail';
import type { SleepMetricResult, SleepNight } from './sleep';
import type { SleepTimelineDay } from './sleep-detail';

function datedValue(value: unknown) {
	if (value === null) return null;
	const row = readRecord(value, 'dated value');
	return { date: readString(row, 'date'), value: readNumber(row, 'value') };
}

export async function getSleepMetric(
	metric: SleepMetricResult['metric'],
	start: string,
	end: string,
	signal?: AbortSignal,
	client: ApiClient = api
): Promise<SleepMetricResult> {
	const data = readRecord(
		await client.request(`/health/metrics/${metric}?${new URLSearchParams({ start, end })}`, {
			signal
		}),
		'metric'
	);
	const period = readRecord(data.period, 'period');
	const summary = readRecord(data.summary, 'summary');
	const status = readRecord(data.dataStatus, 'dataStatus');
	const detail = readRecord(data.detail, 'detail');
	if (
		data.metric !== metric ||
		data.unit !== (metric === 'sleeps' ? 'min' : '%') ||
		data.inProgress !== null ||
		summary.total !== null
	)
		throw new ResponseFormatError('sleep metric');
	const nights: SleepNight[] = readArray(detail, 'nights').map((value) => {
		const n = readRecord(value, 'night');
		return {
			date: readString(n, 'date'),
			startAt: readString(n, 'startAt'),
			endAt: readString(n, 'endAt'),
			sleepMinutes: readNullable(n, 'sleepMinutes', readNumber),
			deepMinutes: readNullable(n, 'deepMinutes', readNumber),
			remMinutes: readNullable(n, 'remMinutes', readNumber),
			lightMinutes: readNullable(n, 'lightMinutes', readNumber),
			awakeMinutes: readNullable(n, 'awakeMinutes', readNumber)
		};
	});
	return {
		metric,
		unit: metric === 'sleeps' ? 'min' : '%',
		period: { start: readString(period, 'start'), end: readString(period, 'end') },
		summary: {
			avg: readNullable(summary, 'avg', readNumber),
			dailyMin: datedValue(summary.dailyMin),
			dailyMax: datedValue(summary.dailyMax),
			total: null,
			daysWithData: readNumber(summary, 'daysWithData'),
			daysUsedForSummary: readNumber(summary, 'daysUsedForSummary'),
			completedDays: readNumber(summary, 'completedDays'),
			linkedFrom: readNullable(summary, 'linkedFrom', readString),
			missingDays: readArray(summary, 'missingDays').map((value) => readString({ value }, 'value')),
			unsyncedDays: readArray(summary, 'unsyncedDays').map((value) =>
				readString({ value }, 'value')
			)
		},
		days: readArray(data, 'days').map((value) => {
			const day = readRecord(value, 'day');
			return {
				date: readString(day, 'date'),
				value: readNullable(day, 'value', readNumber),
				missing: readBoolean(day, 'missing'),
				isInProgress: readBoolean(day, 'isInProgress'),
				reason: readNullable(day, 'reason', (r, key) =>
					readEnum(r, key, ['BEFORE_LINK', 'UNSYNCED', 'NO_RECORD', 'NULL_VALUE'] as const)
				)
			};
		}),
		inProgress: null,
		dataStatus: {
			lastDataAt: readNullable(status, 'lastDataAt', readString),
			syncedUntil: readNullable(status, 'syncedUntil', readString),
			lastSyncStatus: readNullable(status, 'lastSyncStatus', (r, key) =>
				readEnum(r, key, ['success', 'fail'] as const)
			),
			lastSuccessAt: readNullable(status, 'lastSuccessAt', readString),
			checkedAt: readString(status, 'checkedAt')
		},
		detail: {
			nights,
			measuredMin: datedValue(detail.measuredMin),
			measuredMax: datedValue(detail.measuredMax),
			sessions: [],
			dailyRanges: readArray(detail, 'dailyRanges').map((value) => {
				const row = readRecord(value, 'daily range');
				return {
					date: readString(row, 'date'),
					min: readNullable(row, 'min', readNumber),
					max: readNullable(row, 'max', readNumber)
				};
			})
		}
	};
}

export async function getSleepTimeline(
	date: string,
	signal?: AbortSignal,
	client: ApiClient = api
): Promise<SleepTimelineDay[]> {
	const data = readRecord(
		await client.request(`/health/timeline?${new URLSearchParams({ start: date, end: date })}`, {
			signal
		}),
		'timeline'
	);
	return readArray(data, 'days').map((value) => {
		const day = readRecord(value, 'timeline day');
		const main = day.mainSleep === null ? null : readRecord(day.mainSleep, 'mainSleep');
		return {
			date: readString(day, 'date'),
			mainSleep: main
				? { startAt: readString(main, 'startAt'), endAt: readString(main, 'endAt') }
				: null,
			sleepSegments: readArray(day, 'sleepSegments').map((value) => {
				const segment = readRecord(value, 'segment');
				return {
					startAt: readString(segment, 'startAt'),
					endAt: readString(segment, 'endAt'),
					stage: readEnum(segment, 'stage', [
						'awake',
						'rem',
						'light',
						'deep',
						'unknown',
						'unspecified'
					] as const)
				};
			})
		};
	});
}

function checkAuthentication(results: PromiseSettledResult<unknown>[]) {
	for (const result of results)
		if (
			result.status === 'rejected' &&
			result.reason instanceof ApiError &&
			result.reason.status === 401
		)
			throw result.reason;
}

export async function loadSleepWeek(
	week: ReturnType<typeof activityWeek>,
	signal: AbortSignal,
	client: ApiClient = api
) {
	const [sleep, oxygen, previous] = await Promise.allSettled([
		getSleepMetric('sleeps', week.start, week.end, signal, client),
		getSleepMetric('oxygen_saturations', week.start, week.end, signal, client),
		getSleepMetric(
			'sleeps',
			adjacentDay(week.start, -7),
			adjacentDay(week.start, -1),
			signal,
			client
		)
	]);
	checkAuthentication([sleep, oxygen, previous]);
	return { sleep, oxygen, previous };
}

export async function loadSleepDetail(date: string, signal: AbortSignal, client: ApiClient = api) {
	const [sleep, oxygen, timeline] = await Promise.allSettled([
		getSleepMetric('sleeps', date, date, signal, client),
		getSleepMetric('oxygen_saturations', date, date, signal, client),
		getSleepTimeline(date, signal, client)
	]);
	checkAuthentication([sleep, oxygen, timeline]);
	return { sleep, oxygen, timeline };
}

export function sleepRequestError(error: unknown): string {
	return error instanceof ApiError
		? `${error.message}${error.requestId ? ` (요청 ID: ${error.requestId})` : ''}`
		: '수면 데이터를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.';
}

/** 서버가 제공한 연동 시작일 이후의 달력 선택지. 기록 유무나 reason을 생성하지 않는다. */
export function sleepDateChoices(linkedFrom: string | null, today: string): string[] {
	if (linkedFrom === null || linkedFrom > today) return [];
	const days = Math.floor((Date.parse(today) - Date.parse(linkedFrom)) / 86400000) + 1;
	return Array.from({ length: days }, (_, index) => adjacentDay(linkedFrom, index));
}
