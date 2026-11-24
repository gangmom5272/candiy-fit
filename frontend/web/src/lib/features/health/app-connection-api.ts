import { api, type ApiClient } from '#lib/api/client.js';
import { ResponseFormatError } from '#lib/api/errors.js';
import { readEnum, readRecord, readString } from '#lib/api/parse.js';

export const appConnectionStatuses = ['NOT_CONNECTED', 'CONNECTED', 'UNKNOWN'] as const;
export type AppConnectionStatus = (typeof appConnectionStatuses)[number];

export type FirstRecord =
	{ status: 'CONFIRMED'; firstArrivedAt: string } | { status: 'UNCONFIRMED'; firstArrivedAt: null };

export type AppConnection = (
	| { status: 'CONNECTED'; connectedAt: string }
	| { status: 'NOT_CONNECTED' | 'UNKNOWN'; connectedAt: null }
) & { firstRecord?: FirstRecord };

function readUtcTimestamp(record: Record<string, unknown>, field: string): string {
	const value = readString(record, field);
	const parsed = Date.parse(value);
	if (
		!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)$/.test(value) ||
		!Number.isFinite(parsed) ||
		new Date(parsed).toISOString().slice(0, 10) !== value.slice(0, 10)
	) {
		throw new ResponseFormatError(field);
	}
	return value;
}

function readFirstRecord(data: Record<string, unknown>): FirstRecord {
	const record = readRecord(data.firstRecord, 'firstRecord');
	const status = readEnum(record, 'status', ['CONFIRMED', 'UNCONFIRMED'] as const);
	if (status === 'CONFIRMED') {
		return { status, firstArrivedAt: readUtcTimestamp(record, 'firstArrivedAt') };
	}
	if (record.firstArrivedAt !== null) throw new ResponseFormatError('firstArrivedAt');
	return { status, firstArrivedAt: null };
}

/** 최초 앱 설정과 기록 도착 이력을 구분한다. 집계 준비·현재 권한·연결 유지 상태는 판정하지 않는다. */
export async function getAppConnection(
	signal?: AbortSignal,
	client: ApiClient = api
): Promise<AppConnection> {
	const data = readRecord(
		// 늦게 도착한 이전 계정의 401은 호출부가 현재 계정·요청 세대를 확인한 뒤 처리한다.
		await client.request('/health/data-status', { signal, cache: 'no-store', auth: false }),
		'data-status'
	);
	const connection = readRecord(data.appConnection, 'appConnection');
	const status = readEnum(connection, 'status', appConnectionStatuses);
	if (status === 'CONNECTED')
		return {
			status,
			connectedAt: readUtcTimestamp(connection, 'connectedAt'),
			firstRecord: readFirstRecord(data)
		};
	if (connection.connectedAt !== null) throw new ResponseFormatError('connectedAt');
	return { status, connectedAt: null, firstRecord: readFirstRecord(data) };
}
