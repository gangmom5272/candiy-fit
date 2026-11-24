import {
	readArray,
	readBoolean,
	readEnum,
	readNullable,
	readNumber,
	readRecord,
	readString
} from '#lib/api/parse.js';
import { ResponseFormatError } from '#lib/api/errors.js';
import {
	COACHING_STAGES,
	CONNECTION_ERROR_CODES,
	HEALTH_ERROR_REASONS,
	HEALTH_METRICS,
	MISSING_REASONS,
	REQUEST_STATUSES,
	type AssistantMessageSnapshot,
	type CoachingConnectionEvent,
	type CoachingErrorData,
	type CoachingRequestEvent,
	type CoachingMessage,
	type ConversationItem,
	type ConversationPage,
	type ConversationSummary,
	type Evidence,
	type EvidenceSnapshot,
	type HourlyStepsSnapshot,
	type KindEvidence,
	type MetricEvidence,
	type SleepSession,
	type SleepSessionsSnapshot,
	EXERCISE_ACTIVITY_FIELDS,
	EXERCISE_ACTIVITY_SOURCES,
	type ExerciseActivity,
	type ExerciseActivityField,
	type ExerciseSession,
	type HealthErrorContext,
	type MessagePage,
	type RequestSnapshot,
	type SleepNight
} from './types';

const UNITS = ['steps', 'bpm', '%', 'min', 'kg', 'kcal/day', 'm'] as const;
const SYNC_STATUSES = ['success', 'fail'] as const;

function readStrings(record: Record<string, unknown>, field: string): string[] {
	return readArray(record, field).map((item) => {
		if (typeof item !== 'string') throw new ResponseFormatError(field);
		return item;
	});
}

function readMetrics(record: Record<string, unknown>, field: string) {
	return readArray(record, field).map((item) => readEnum({ [field]: item }, field, HEALTH_METRICS));
}

function readDatedValue(record: Record<string, unknown>, field: string) {
	const value = readRecord(record[field], field);
	return { date: readString(value, 'date'), value: readNumber(value, 'value') };
}

function readMinutes(record: Record<string, unknown>, field: string) {
	return readNullable(record, field, readNumber);
}

/**
 * 오프셋이 붙은 시각 문자열(RFC 3339, Z·+09:00 모두). 화면이 KST 로 바꿀 때 잘못된 값이면 Intl 이 RangeError 를 내
 * 근거 창 전체가 깨지므로, 읽을 때 형식 오류로 보고 그 근거만 숨긴다(S15P31A403-312 QA)
 */
const ISO_WITH_OFFSET = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;

function readIsoTime(record: Record<string, unknown>, field: string): string {
	const value = readString(record, field);
	if (!ISO_WITH_OFFSET.test(value) || Number.isNaN(Date.parse(value)))
		throw new ResponseFormatError(field);
	return value;
}

function readIsoTimes(record: Record<string, unknown>, field: string): string[] {
	return readArray(record, field).map((item) => readIsoTime({ [field]: item }, field));
}

/** 90% 미만 산소포화도 칸(spo2_low_count·spo2_low_times). null 은 그대로 두고 빈 배열로 바꾸지 않는다 */
/**
 * 날짜별 평균·최저·최고 산소포화도(S15P31A403-388). 덧붙는 값이라 형식이 달라도 밤 기록을 버리지 않고 그 값만 뺀다.
 * 값이 없으면 키를 두지 않는다(예전 근거와 같은 모양)
 */
function readSpo2Stats(record: Record<string, unknown>) {
	const stats: Pick<SleepNight, 'spo2_avg' | 'spo2_min' | 'spo2_max'> = {};
	for (const key of ['spo2_avg', 'spo2_min', 'spo2_max'] as const) {
		const value = record[key];
		if (typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= 100)
			stats[key] = value;
	}
	return stats;
}

function readSpo2Low(record: Record<string, unknown>) {
	return {
		spo2_low_count: readNullable(record, 'spo2_low_count', readNumber),
		spo2_low_times: readNullable(record, 'spo2_low_times', readIsoTimes)
	};
}

function parseNight(value: unknown): SleepNight {
	const night = readRecord(value, 'nights[]');
	return {
		date: readString(night, 'date'),
		start_at: readString(night, 'start_at'),
		end_at: readString(night, 'end_at'),
		sleep_minutes: readMinutes(night, 'sleep_minutes'),
		deep_minutes: readMinutes(night, 'deep_minutes'),
		rem_minutes: readMinutes(night, 'rem_minutes'),
		light_minutes: readMinutes(night, 'light_minutes'),
		awake_minutes: readMinutes(night, 'awake_minutes'),
		...readSpo2Stats(night),
		// 예전 근거에는 산소포화도 칸이 없다. 키가 없으면 undefined 로 두어 화면에서 "측정 없음"으로 보이지 않게 한다
		...('spo2_low_count' in night || 'spo2_low_times' in night
			? { ...readSpo2Low(night), start_at: readIsoTime(night, 'start_at') }
			: {})
	};
}

/** 화면에 쓰는 키만 읽는다. 나머지(거리·칼로리 등)까지 엄격히 보면 형식 하나로 근거 전체를 잃는다 */
function parseSession(value: unknown): ExerciseSession {
	const session = readRecord(value, 'sessions[]');
	return {
		date: readString(session, 'date'),
		start_at: readString(session, 'start_at'),
		duration_minutes: readNumber(session, 'duration_minutes'),
		type_name: readString(session, 'type_name'),
		title: readNullable(session, 'title', readString),
		counts_as_exercise: readBoolean(session, 'counts_as_exercise')
	};
}

/**
 * 운동 근거의 횟수·칼로리·거리(S15P31A403-375). 화면에 쓰는 키만 읽는다.
 * 키가 없는 예전 근거·다른 지표는 null 이고, 모양이 다르면 이 묶음만 버린다 —
 * 덧붙은 값 하나 때문에 운동 시간·세션 근거까지 잃지 않게 한다
 */
function parseExerciseActivity(value: unknown): ExerciseActivity | null {
	if (value === undefined || value === null) return null;
	try {
		const record = readRecord(value, 'exercise_activity');
		const summary = readRecord(record.summary, 'summary');
		const statuses =
			typeof record.source_statuses === 'object' && record.source_statuses !== null
				? (record.source_statuses as Record<string, unknown>)
				: {};
		const readReason = (day: Record<string, unknown>, field: string) =>
			readNullable(day, field, (r, f) => readEnum(r, f, MISSING_REASONS));
		const readStat = (field: ExerciseActivityField) => {
			const stat = readRecord(summary[field], field);
			return {
				total: readNullable(stat, 'total', readNumber),
				avg: readNullable(stat, 'avg', readNumber),
				days_with_data: readNumber(stat, 'days_with_data')
			};
		};
		const sources: ExerciseActivity['source_statuses'] = {};
		for (const name of EXERCISE_ACTIVITY_SOURCES) {
			if (typeof statuses[name] !== 'object' || statuses[name] === null) continue;
			sources[name] = {
				last_sync_status: readNullable(
					statuses[name] as Record<string, unknown>,
					'last_sync_status',
					(r, f) => readEnum(r, f, SYNC_STATUSES)
				)
			};
		}
		return {
			days: readArray(record, 'days').map((item) => {
				const day = readRecord(item, 'exercise_activity.days[]');
				return {
					date: readString(day, 'date'),
					exercise_count: readNullable(day, 'exercise_count', readNumber),
					exercise_calories_kcal: readNullable(day, 'exercise_calories_kcal', readNumber),
					exercise_distance_m: readNullable(day, 'exercise_distance_m', readNumber),
					is_in_progress: readBoolean(day, 'is_in_progress'),
					reason: readReason(day, 'reason'),
					calories_reason: readReason(day, 'calories_reason'),
					distance_reason: readReason(day, 'distance_reason')
				};
			}),
			summary: Object.fromEntries(
				EXERCISE_ACTIVITY_FIELDS.map((field) => [field, readStat(field)])
			) as NonNullable<ExerciseActivity['summary']>,
			source_statuses: sources
		};
	} catch (error) {
		if (error instanceof ResponseFormatError) return null;
		throw error;
	}
}

/** 명세 2.3 snapshot. 공통 원형의 snake_case 키를 그대로 검증한다. */
function parseSnapshot(value: unknown): EvidenceSnapshot {
	const record = readRecord(value, 'snapshot');
	const period = readRecord(record.period, 'period');
	const summary = readRecord(record.summary, 'summary');
	const status = readRecord(record.data_status, 'data_status');
	const detail = readRecord(record.detail, 'detail');
	const inProgress =
		record.in_progress === null ? null : readRecord(record.in_progress, 'in_progress');
	const activity = parseExerciseActivity(detail.exercise_activity);

	return {
		metric: readEnum(record, 'metric', HEALTH_METRICS),
		period: { start: readString(period, 'start'), end: readString(period, 'end') },
		unit: readEnum(record, 'unit', UNITS),
		summary: {
			avg: readNullable(summary, 'avg', readNumber),
			daily_min: readNullable(summary, 'daily_min', readDatedValue),
			daily_max: readNullable(summary, 'daily_max', readDatedValue),
			total: readNullable(summary, 'total', readNumber),
			days_with_data: readNumber(summary, 'days_with_data'),
			days_used_for_summary: readNumber(summary, 'days_used_for_summary'),
			completed_days: readNumber(summary, 'completed_days'),
			missing_days: readStrings(summary, 'missing_days'),
			unsynced_days: readStrings(summary, 'unsynced_days'),
			linked_from: readNullable(summary, 'linked_from', readString)
		},
		days: readArray(record, 'days').map((item) => {
			const day = readRecord(item, 'days[]');
			return {
				date: readString(day, 'date'),
				value: readNullable(day, 'value', readNumber),
				missing: readBoolean(day, 'missing'),
				reason: readNullable(day, 'reason', (r, f) => readEnum(r, f, MISSING_REASONS)),
				is_in_progress: readBoolean(day, 'is_in_progress')
			};
		}),
		in_progress: inProgress && {
			date: readString(inProgress, 'date'),
			value: readNullable(inProgress, 'value', readNumber),
			missing: readBoolean(inProgress, 'missing'),
			as_of: readNullable(inProgress, 'as_of', readString)
		},
		data_status: {
			last_data_at: readNullable(status, 'last_data_at', readString),
			synced_until: readNullable(status, 'synced_until', readString),
			last_sync_status: readNullable(status, 'last_sync_status', (r, f) =>
				readEnum(r, f, SYNC_STATUSES)
			),
			last_success_at: readNullable(status, 'last_success_at', readString),
			checked_at: readString(status, 'checked_at')
		},
		detail: {
			measured_min: readNullable(detail, 'measured_min', readDatedValue),
			measured_max: readNullable(detail, 'measured_max', readDatedValue),
			nights: readArray(detail, 'nights').map(parseNight),
			// 날짜별 최저·최고는 아직 화면에서 쓰지 않아 배열 여부만 확인한다
			daily_ranges: readArray(detail, 'daily_ranges'),
			sessions: readArray(detail, 'sessions').map(parseSession),
			// 값이 없으면 키를 두지 않는다(예전 근거와 같은 모양)
			...(activity ? { exercise_activity: activity } : {})
		}
	};
}

function readPeriod(record: Record<string, unknown>) {
	const period = readRecord(record.period, 'period');
	return { start: readString(period, 'start'), end: readString(period, 'end') };
}

/** kind sleep_sessions. 화면에 쓰는 키만 엄격히 읽고 심박·단계 등 나머지는 버린다 */
function parseSleepSession(value: unknown): SleepSession {
	const session = readRecord(value, 'sessions[]');
	return {
		session_id: readString(session, 'session_id'),
		is_main: readBoolean(session, 'is_main'),
		date: readString(session, 'date'),
		start_at: readIsoTime(session, 'start_at'),
		end_at: readIsoTime(session, 'end_at'),
		sleep_minutes: readMinutes(session, 'sleep_minutes'),
		session_minutes: readNumber(session, 'session_minutes'),
		...readSpo2Low(session)
	};
}

function parseSleepSessions(record: Record<string, unknown>): SleepSessionsSnapshot {
	return {
		kind: 'sleep_sessions',
		period: readPeriod(record),
		sessions: readArray(record, 'sessions').map(parseSleepSession)
	};
}

const HOURLY_REASONS = ['NO_RECORD', 'NULL_VALUE'] as const;

function parseHourlySteps(record: Record<string, unknown>): HourlyStepsSnapshot {
	return {
		kind: 'hourly_steps',
		period: readPeriod(record),
		start_hour: readNumber(record, 'start_hour'),
		end_hour: readNumber(record, 'end_hour'),
		days: readArray(record, 'days').map((item) => {
			const day = readRecord(item, 'days[]');
			return {
				date: readString(day, 'date'),
				is_in_progress: readBoolean(day, 'is_in_progress'),
				values: readNullable(day, 'values', (r, f) =>
					readArray(r, f).map((entry) => readNumber({ value: entry }, 'value'))
				),
				total: readNullable(day, 'total', readNumber),
				reason: readNullable(day, 'reason', (r, f) => readEnum(r, f, HOURLY_REASONS))
			};
		}),
		total: readNullable(record, 'total', readNumber)
	};
}

/**
 * 모양이 다른 근거(snapshot.kind). 웹이 그리는 종류만 읽고, 모르는 종류(운동 구간 등 아직 모양이 정해지지 않은 것)는 null 이다.
 * data_status 는 종류마다 키가 달라(sleep_sessions 는 synced_until 이 없다) 화면에 쓰지 않으므로 읽지 않는다
 */
function parseKindEvidence(record: Record<string, unknown>): KindEvidence | null {
	const snapshot = readRecord(record.snapshot, 'snapshot');
	const kind = snapshot.kind;
	const parsed =
		kind === 'sleep_sessions'
			? parseSleepSessions(snapshot)
			: kind === 'hourly_steps'
				? parseHourlySteps(snapshot)
				: null;
	if (!parsed) return null;
	return {
		id: readString(record, 'id'),
		position: readNumber(record, 'position'),
		metric: readEnum(record, 'metric', HEALTH_METRICS),
		periodStart: readString(record, 'periodStart'),
		periodEnd: readString(record, 'periodEnd'),
		snapshot: parsed
	};
}

export function parseEvidence(value: unknown): MetricEvidence {
	const record = readRecord(value, 'evidence[]');
	return {
		id: readString(record, 'id'),
		position: readNumber(record, 'position'),
		metric: readEnum(record, 'metric', HEALTH_METRICS),
		periodStart: readString(record, 'periodStart'),
		periodEnd: readString(record, 'periodEnd'),
		snapshot: parseSnapshot(record.snapshot)
	};
}

function hasSnapshotKind(item: unknown): boolean {
	if (typeof item !== 'object' || item === null) return false;
	const snapshot = (item as Record<string, unknown>).snapshot;
	return typeof snapshot === 'object' && snapshot !== null && 'kind' in snapshot;
}

/**
 * 근거 목록. 근거 하나의 모양이 다르면(예전에 저장된 근거, 새로 생긴 칸 등) 그 근거만 빼고 나머지는 보여 준다.
 * 서버는 저장해 둔 근거를 다시 검사하지 않고 돌려주므로, 하나 때문에 대화 전체가 "불러오지 못함"이 되거나
 * 실시간 완료 이벤트를 통째로 버려 "생성 중"에 멈추지 않게 한다. 목록 자체가 배열이 아니면 그대로 오류다
 */
export function parseEvidenceList(record: Record<string, unknown>, field: string): Evidence[] {
	return readArray(record, field).flatMap((item): Evidence[] => {
		try {
			// snapshot.kind 가 있는 근거는 아는 종류(수면 세션·시간대 걸음)만 읽고, 모르는 종류는 그 근거만 숨긴다.
			// 나머지 근거와 답변은 그대로 보여 준다(S15P31A403-311·312)
			if (hasSnapshotKind(item)) {
				const kind = parseKindEvidence(readRecord(item, 'evidence[]'));
				return kind ? [kind] : [];
			}
			return [parseEvidence(item)];
		} catch (error) {
			if (error instanceof ResponseFormatError) return [];
			throw error;
		}
	});
}

/** 명세 2.4 errorContext. 메시지·요청 조회에서 항상 키가 있고 정상이면 null이다. */
export function parseErrorContext(value: unknown): HealthErrorContext | null {
	if (value === null || value === undefined) return null;
	const record = readRecord(value, 'errorContext');
	return {
		reason: readEnum(record, 'reason', HEALTH_ERROR_REASONS),
		metrics: readMetrics(record, 'metrics')
	};
}

function parseConversationSummary(value: unknown): ConversationSummary {
	const record = readRecord(value, 'conversation');
	return {
		id: readString(record, 'id'),
		title: readString(record, 'title'),
		summary: readNullable(record, 'summary', readString),
		createdAt: readString(record, 'createdAt'),
		updatedAt: readString(record, 'updatedAt')
	};
}

function parseConversationItem(value: unknown): ConversationItem {
	const record = readRecord(value, 'items[]');
	return {
		...parseConversationSummary(record),
		lastRequestStatus: readEnum(record, 'lastRequestStatus', REQUEST_STATUSES)
	};
}

export function parseConversationPage(value: unknown): ConversationPage {
	const record = readRecord(value, 'data');
	return {
		items: readArray(record, 'items').map(parseConversationItem),
		nextCursor: readNullable(record, 'nextCursor', readString)
	};
}

function parseMessage(value: unknown): CoachingMessage {
	const record = readRecord(value, 'items[]');
	return {
		id: readString(record, 'id'),
		requestId: readString(record, 'requestId'),
		role: readEnum(record, 'role', ['user', 'assistant'] as const),
		sequenceNo: readNumber(record, 'sequenceNo'),
		content: readString(record, 'content'),
		requestStatus: readEnum(record, 'requestStatus', REQUEST_STATUSES),
		errorCode: readNullable(record, 'errorCode', readString),
		createdAt: readString(record, 'createdAt'),
		evidence: parseEvidenceList(record, 'evidence'),
		errorContext: parseErrorContext(record.errorContext)
	};
}

export function parseMessagePage(value: unknown): MessagePage {
	const record = readRecord(value, 'data');
	return {
		conversation: parseConversationSummary(record.conversation),
		items: readArray(record, 'items').map(parseMessage),
		nextBeforeSequenceNo: readNullable(record, 'nextBeforeSequenceNo', readNumber)
	};
}

export function parseAssistantMessage(value: unknown): AssistantMessageSnapshot {
	const record = readRecord(value, 'assistantMessage');
	return {
		id: readString(record, 'id'),
		role: readEnum(record, 'role', ['assistant'] as const),
		sequenceNo: readNumber(record, 'sequenceNo'),
		content: readString(record, 'content'),
		createdAt: readString(record, 'createdAt')
	};
}

export function parseRequestSnapshot(value: unknown): RequestSnapshot {
	const record = readRecord(value, 'data');
	return {
		requestId: readString(record, 'requestId'),
		clientRequestId: readString(record, 'clientRequestId'),
		conversationId: readString(record, 'conversationId'),
		status: readEnum(record, 'status', REQUEST_STATUSES),
		userMessageId: readString(record, 'userMessageId'),
		assistantMessage: parseAssistantMessage(record.assistantMessage),
		evidence: parseEvidenceList(record, 'evidence'),
		errorCode: readNullable(record, 'errorCode', readString),
		createdAt: readString(record, 'createdAt'),
		finishedAt: readNullable(record, 'finishedAt', readString),
		errorContext: parseErrorContext(record.errorContext)
	};
}

function readEventBase(record: Record<string, unknown>) {
	return {
		clientRequestId: readNullable(record, 'clientRequestId', readString),
		requestId: readNullable(record, 'requestId', readString),
		conversationId: readNullable(record, 'conversationId', readString),
		eventSeq: readNumber(record, 'eventSeq')
	};
}

function parseErrorData(data: Record<string, unknown>): CoachingErrorData {
	return {
		code: readString(data, 'code'),
		message: readString(data, 'message'),
		retryable: readBoolean(data, 'retryable'),
		accepted: readBoolean(data, 'accepted'),
		reason: readNullable(data, 'reason', (r, f) => readEnum(r, f, HEALTH_ERROR_REASONS)),
		metrics: readNullable(data, 'metrics', readMetrics)
	};
}

/** 명세 7.2·7.4 서버 메시지. 모르는 type이면 ResponseFormatError를 던진다. */
export function parseServerMessage(value: unknown): CoachingConnectionEvent | CoachingRequestEvent {
	const record = readRecord(value, 'message');
	const type = readString(record, 'type');
	const data = readRecord(record.data, 'data');

	switch (type) {
		case 'ready':
			return { type, data: { protocolVersion: readString(data, 'protocolVersion') } };
		case 'connection_error':
			return {
				type,
				data: {
					code: readEnum(data, 'code', CONNECTION_ERROR_CODES),
					message: readString(data, 'message')
				}
			};
		case 'accepted': {
			const conversation = readRecord(data.conversation, 'conversation');
			return {
				...readEventBase(record),
				type,
				data: {
					userMessageId: readString(data, 'userMessageId'),
					assistantMessageId: readString(data, 'assistantMessageId'),
					userSequenceNo: readNumber(data, 'userSequenceNo'),
					assistantSequenceNo: readNumber(data, 'assistantSequenceNo'),
					conversation: {
						id: readString(conversation, 'id'),
						title: readString(conversation, 'title'),
						createdAt: readString(conversation, 'createdAt'),
						updatedAt: readString(conversation, 'updatedAt')
					}
				}
			};
		}
		case 'status':
			return {
				...readEventBase(record),
				type,
				data: { stage: readEnum(data, 'stage', COACHING_STAGES) }
			};
		case 'delta':
			return { ...readEventBase(record), type, data: { text: readString(data, 'text') } };
		case 'evidence':
			return { ...readEventBase(record), type, data: { items: parseEvidenceList(data, 'items') } };
		case 'completed':
			return {
				...readEventBase(record),
				type,
				data: {
					assistantMessage: parseAssistantMessage(data.assistantMessage),
					evidence: parseEvidenceList(data, 'evidence'),
					finishedAt: readString(data, 'finishedAt')
				}
			};
		case 'error':
			return { ...readEventBase(record), type, data: parseErrorData(data) };
		case 'request_snapshot':
			return { ...readEventBase(record), type, data: parseRequestSnapshot(data) };
		default:
			throw new ResponseFormatError(`type: ${type}`);
	}
}
