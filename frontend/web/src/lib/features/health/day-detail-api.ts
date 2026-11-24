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
import { getActivityMetric } from './activity-api';
import { getSleepMetric } from './sleep-api';
import { getHeartMetric } from './heart-api';
import type { HealthMetricDay, HealthTimelineDay } from './day-detail';

function metricDay(result: { days: HealthMetricDay[] }, date: string): HealthMetricDay {
	const day = result.days.find((day) => day.date === date);
	if (!day) throw new ResponseFormatError('metric day');
	return day;
}

async function sleepDay(date: string, signal: AbortSignal, client: ApiClient) {
	const result = await getSleepMetric('sleeps', date, date, signal, client);
	const day = metricDay(result, date);
	if (day.missing) return day;
	const night = result.detail.nights.find((night) => night.date === date);
	if (!night) throw new ResponseFormatError('sleep night');
	return { ...day, value: night.sleepMinutes };
}

/** v14 timeline은 인정 운동만 반환하고 주 수면의 전날 시작 timestamp도 보존한다. */
export async function getDayTimeline(
	date: string,
	signal?: AbortSignal,
	client: ApiClient = api
): Promise<HealthTimelineDay> {
	const data = readRecord(
		await client.request(`/health/timeline?${new URLSearchParams({ start: date, end: date })}`, {
			signal
		}),
		'timeline'
	);
	const day = readArray(data, 'days')
		.map((value) => readRecord(value, 'timeline day'))
		.find((day) => day.date === date);
	if (!day) throw new ResponseFormatError('timeline day');
	const main = day.mainSleep === null ? null : readRecord(day.mainSleep, 'mainSleep');
	const steps = readNullable(day, 'hourlySteps', readArray);
	const hearts = readNullable(day, 'hourlyHeartRates', readArray);
	if ((steps !== null && steps.length !== 24) || (hearts !== null && hearts.length !== 24))
		throw new ResponseFormatError('timeline hours');
	return {
		date: readString(day, 'date'),
		isInProgress: readBoolean(day, 'isInProgress'),
		mainSleep: main
			? { startAt: readString(main, 'startAt'), endAt: readString(main, 'endAt') }
			: null,
		sleepSegments: readArray(day, 'sleepSegments').map((value) => {
			const segment = readRecord(value, 'sleep segment');
			return {
				startAt: readString(segment, 'startAt'),
				endAt: readString(segment, 'endAt'),
				stage: readEnum(segment, 'stage', [
					'awake',
					'light',
					'deep',
					'rem',
					'unspecified',
					'unknown'
				] as const)
			};
		}),
		exerciseSegments: readArray(day, 'exerciseSegments').map((value) => {
			const segment = readRecord(value, 'exercise segment');
			return {
				startAt: readString(segment, 'startAt'),
				endAt: readString(segment, 'endAt'),
				exerciseType: readNumber(segment, 'exerciseType'),
				typeName: readString(segment, 'typeName'),
				title: readNullable(segment, 'title', readString)
			};
		}),
		hourlySteps: steps?.map((value) => readNumber({ value }, 'value')) ?? null,
		hourlyHeartRates:
			hearts?.map((value) => {
				if (value === null) return null;
				const point = readRecord(value, 'hourly heart');
				return {
					avg: readNumber(point, 'avg'),
					min: readNumber(point, 'min'),
					max: readNumber(point, 'max')
				};
			}) ?? null
	};
}

export async function loadDayDetail(date: string, signal: AbortSignal, client: ApiClient = api) {
	const [steps, exercise, sleep, heart, timeline] = await Promise.allSettled([
		getActivityMetric('steps', date, date, signal, client).then((result) =>
			metricDay(result, date)
		),
		getActivityMetric('exercises', date, date, signal, client).then((result) =>
			metricDay(result, date)
		),
		sleepDay(date, signal, client),
		getHeartMetric(date, date, signal, client).then((result) => metricDay(result, date)),
		getDayTimeline(date, signal, client)
	]);
	for (const result of [steps, exercise, sleep, heart, timeline])
		if (
			result.status === 'rejected' &&
			result.reason instanceof ApiError &&
			result.reason.status === 401
		)
			throw result.reason;
	return { steps, exercise, sleep, heart, timeline };
}

export function dayRequestError(error: unknown): string {
	return error instanceof ApiError
		? `${error.message}${error.requestId ? ` (요청 ID: ${error.requestId})` : ''}`
		: '하루 기록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.';
}
