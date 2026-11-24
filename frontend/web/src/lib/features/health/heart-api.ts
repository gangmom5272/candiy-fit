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
import type { HeartMetricResult } from './heart';

function datedValue(value: unknown) {
	if (value === null) return null;
	const row = readRecord(value, 'dated value');
	return { date: readString(row, 'date'), value: readNumber(row, 'value') };
}

/** v13 heart_rates 응답을 보존한다. 기간 평균과 결측 상태를 재계산하지 않는다. */
export async function getHeartMetric(
	start: string,
	end: string,
	signal?: AbortSignal,
	client: ApiClient = api
): Promise<HeartMetricResult> {
	const data = readRecord(
		await client.request(`/health/metrics/heart_rates?${new URLSearchParams({ start, end })}`, {
			signal
		}),
		'heart metric'
	);
	const period = readRecord(data.period, 'period');
	const summary = readRecord(data.summary, 'summary');
	const status = readRecord(data.dataStatus, 'dataStatus');
	const detail = readRecord(data.detail, 'detail');
	if (
		data.metric !== 'heart_rates' ||
		data.unit !== 'bpm' ||
		summary.total !== null ||
		readArray(detail, 'nights').length !== 0 ||
		readArray(detail, 'sessions').length !== 0
	)
		throw new ResponseFormatError('heart metric');
	const progress = data.inProgress === null ? null : readRecord(data.inProgress, 'inProgress');
	return {
		metric: 'heart_rates',
		unit: 'bpm',
		period: { start: readString(period, 'start'), end: readString(period, 'end') },
		summary: {
			avg: readNullable(summary, 'avg', readNumber),
			dailyMin: datedValue(summary.dailyMin),
			dailyMax: datedValue(summary.dailyMax),
			total: null,
			daysWithData: readNumber(summary, 'daysWithData'),
			daysUsedForSummary: readNumber(summary, 'daysUsedForSummary'),
			completedDays: readNumber(summary, 'completedDays'),
			missingDays: readArray(summary, 'missingDays').map((value) => readString({ value }, 'value')),
			unsyncedDays: readArray(summary, 'unsyncedDays').map((value) =>
				readString({ value }, 'value')
			),
			linkedFrom: readNullable(summary, 'linkedFrom', readString)
		},
		days: readArray(data, 'days').map((value) => {
			const day = readRecord(value, 'day');
			return {
				date: readString(day, 'date'),
				value: readNullable(day, 'value', readNumber),
				missing: readBoolean(day, 'missing'),
				reason: readNullable(day, 'reason', (r, key) =>
					readEnum(r, key, ['BEFORE_LINK', 'UNSYNCED', 'NO_RECORD', 'NULL_VALUE'] as const)
				),
				isInProgress: readBoolean(day, 'isInProgress')
			};
		}),
		inProgress: progress
			? {
					date: readString(progress, 'date'),
					value: readNullable(progress, 'value', readNumber),
					missing: readBoolean(progress, 'missing'),
					asOf: readNullable(progress, 'asOf', readString)
				}
			: null,
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
			measuredMin: datedValue(detail.measuredMin),
			measuredMax: datedValue(detail.measuredMax),
			nights: [],
			sessions: [],
			dailyRanges: readArray(detail, 'dailyRanges').map((value) => {
				const range = readRecord(value, 'daily range');
				return {
					date: readString(range, 'date'),
					min: readNullable(range, 'min', readNumber),
					max: readNullable(range, 'max', readNumber)
				};
			})
		}
	};
}

export function heartRequestError(error: unknown): string {
	return error instanceof ApiError
		? `${error.message}${error.requestId ? ` (요청 ID: ${error.requestId})` : ''}`
		: '심박 기록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.';
}
