import { api, type ApiClient } from '#lib/api/client.js';
import { ApiError, ResponseFormatError, isRecord } from '#lib/api/errors.js';
import { readArray, readEnum, readRecord, readString } from '#lib/api/parse.js';
import { isHealthDataNotReady } from '#lib/features/health/data-readiness.js';

export const messageMetrics = ['steps', 'exercises', 'sleeps'] as const;
export const messageMetricLabels = { steps: '걸음', exercises: '운동', sleeps: '수면' };

export interface DailyMessage {
	date: string;
	status: 'READY' | 'PARTIAL' | 'EMPTY' | 'WAITING';
	code: string;
	area: 'activity' | 'sleep' | null;
	title: string;
	description: string;
	dataAsOf: Record<(typeof messageMetrics)[number], string | null>;
	scoreComputedAt: string | null;
	notices: {
		metric: (typeof messageMetrics)[number];
		code: 'LOOKUP_FAILED' | 'DATA_NOT_READY' | 'SYNC_FAILED';
		message: string;
	}[];
}

function timestamp(record: Record<string, unknown>, field: string): string | null {
	if (record[field] === null) return null;
	const value = readString(record, field);
	const parsed = Date.parse(value);
	if (
		!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)$/.test(value) ||
		!Number.isFinite(parsed) ||
		new Date(parsed).toISOString().slice(0, 10) !== value.slice(0, 10)
	)
		throw new ResponseFormatError(field);
	return value;
}

export async function getDailyMessage(
	signal?: AbortSignal,
	client: ApiClient = api
): Promise<DailyMessage> {
	const data = readRecord(
		await client.request('/health/daily-message', { signal }),
		'daily-message'
	);
	const date = readString(data, 'date');
	if (
		!/^\d{4}-\d{2}-\d{2}$/.test(date) ||
		!Number.isFinite(Date.parse(date)) ||
		new Date(date).toISOString().slice(0, 10) !== date
	)
		throw new ResponseFormatError('date');
	const asOf = readRecord(data.dataAsOf, 'dataAsOf');
	return {
		date,
		status: readEnum(data, 'status', ['READY', 'PARTIAL', 'EMPTY', 'WAITING']),
		code: readString(data, 'code'),
		area: data.area === null ? null : readEnum(data, 'area', ['activity', 'sleep'] as const),
		title: readString(data, 'title'),
		description: readString(data, 'description'),
		dataAsOf: {
			steps: timestamp(asOf, 'steps'),
			exercises: timestamp(asOf, 'exercises'),
			sleeps: timestamp(asOf, 'sleeps')
		},
		scoreComputedAt: timestamp(data, 'scoreComputedAt'),
		notices: readArray(data, 'notices').map((value) => {
			const notice = readRecord(value, 'notice');
			return {
				metric: readEnum(notice, 'metric', messageMetrics),
				code: readEnum(notice, 'code', ['LOOKUP_FAILED', 'DATA_NOT_READY', 'SYNC_FAILED']),
				message: readString(notice, 'message')
			};
		})
	};
}

export function dailyMessageError(error: unknown): {
	title: string;
	description: string;
	retryable: boolean;
	preparing?: boolean;
} {
	if (error instanceof ApiError) {
		if (
			error.status === 503 &&
			error.code === 'HEALTH_DATA_UNAVAILABLE' &&
			isRecord(error.context)
		) {
			if (isHealthDataNotReady(error))
				return {
					title: '첫 기록을 보여 드릴 준비 중이에요',
					description: '아직 집계가 끝나지 않은 항목이 있어요. 잠시 후 다시 확인해 주세요.',
					retryable: true,
					preparing: true
				};
			if (error.context.reason === 'LOOKUP_FAILED')
				return {
					title: '오늘의 한마디를 불러오지 못했어요',
					description: '잠시 후 다시 시도해 주세요.',
					retryable: true
				};
		}
		if (error.status === 401)
			return {
				title: '로그인이 필요해요',
				description: '다시 로그인한 뒤 확인해 주세요.',
				retryable: false
			};
		if (error.status === 403)
			return {
				title: '건강 기록을 확인할 권한이 없어요',
				description: '계정의 건강 데이터 접근 권한을 확인해 주세요.',
				retryable: false
			};
		if (error.status === 409 && error.code === 'CANDIY_USER_NOT_LINKED')
			return {
				title: '기록을 만나려면 연결 확인이 필요해요',
				description: '앱에서 건강 데이터 연결 상태를 확인해 주세요.',
				retryable: false
			};
	}
	return {
		title: '오늘의 한마디를 불러오지 못했어요',
		description: '새로고침하거나 잠시 후 다시 방문해 주세요.',
		retryable: false
	};
}

export function messageTime(value: string): string {
	const parts = new Intl.DateTimeFormat('ko-KR', {
		timeZone: 'Asia/Seoul',
		month: '2-digit',
		day: '2-digit',
		hour: '2-digit',
		minute: '2-digit',
		hourCycle: 'h23'
	}).formatToParts(new Date(value));
	const fields: Record<string, string> = {};
	for (const part of parts) fields[part.type] = part.value;
	return `${fields.month}/${fields.day} ${fields.hour}:${fields.minute}`;
}
