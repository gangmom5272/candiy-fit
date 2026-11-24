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
import type { ExerciseSession, ActivityPeriod, HourlyStepsDay } from './activity';
import type { HealthMetricDay } from './day-detail';
import { formatHealthWeek } from './period';
import { adjacentDay } from './day-detail';

/** Activity에서 사용하는 REST 응답 필드만 읽는다. 집계는 서버 summary를 사용한다. */
export interface ActivityMetricResult {
	period: { start: string; end: string };
	summary: {
		avg: number | null;
		total: number | null;
		daysWithData: number;
		completedDays: number;
	};
	days: HealthMetricDay[];
	detail: { sessions: ExerciseSession[] };
}

export function activityToday(now = new Date()): string {
	return new Date(now.getTime() + 9 * 3600000).toISOString().slice(0, 10);
}

/** -1은 직전 완료 주, 0은 현재 주. 표시만 일요일까지, 조회는 KST 오늘까지만 한다. */
export function activityWeek(today: string, offset: number) {
	const weekday = (new Date(`${today}T00:00:00Z`).getUTCDay() + 6) % 7;
	const start = adjacentDay(today, -weekday + Math.min(0, offset) * 7);
	return { start, end: [adjacentDay(start, 6), today].sort()[0], label: formatHealthWeek(start) };
}

/** 활동·수면·심박 상세 화면: 기준일을 포함한 연속 7일. */
export function activityRecentWeek(referenceDate: string, offset: number) {
	const end = adjacentDay(referenceDate, Math.min(0, offset) * 7);
	const start = adjacentDay(end, -6);
	return { start, end, label: formatHealthWeek(start) };
}

function parseMetric(value: unknown): ActivityMetricResult {
	const data = readRecord(value, 'metric');
	const period = readRecord(data.period, 'period');
	const summary = readRecord(data.summary, 'summary');
	const detail = readRecord(data.detail, 'detail');
	return {
		period: { start: readString(period, 'start'), end: readString(period, 'end') },
		summary: {
			avg: readNullable(summary, 'avg', readNumber),
			total: readNullable(summary, 'total', readNumber),
			daysWithData: readNumber(summary, 'daysWithData'),
			completedDays: readNumber(summary, 'completedDays')
		},
		days: readArray(data, 'days').map((value) => {
			const day = readRecord(value, 'day');
			return {
				date: readString(day, 'date'),
				value: readNullable(day, 'value', readNumber),
				missing: readBoolean(day, 'missing'),
				isInProgress: readBoolean(day, 'isInProgress'),
				reason: readNullable(day, 'reason', (record, field) =>
					readEnum(record, field, [
						'BEFORE_LINK',
						'UNSYNCED',
						'NO_RECORD',
						'NULL_VALUE',
						'ZERO_STEPS'
					] as const)
				)
			};
		}),
		detail: {
			sessions: readArray(detail, 'sessions').map((value, index) => {
				const session = readRecord(value, 'session');
				return {
					// REST 세션에는 id가 없다. 화면 목록 key만 timestamp와 응답 순서로 만든다.
					id: `${readString(session, 'startAt')}:${index}`,
					date: readString(session, 'date'),
					typeName: readString(session, 'typeName'),
					durationMinutes: readNumber(session, 'durationMinutes'),
					countsAsExercise: readBoolean(session, 'countsAsExercise'),
					avgSpeedMps: readNullable(session, 'avgSpeedMps', readNumber),
					caloriesKcal: readNullable(session, 'caloriesKcal', readNumber),
					avgHeartRate: readNullable(session, 'avgHeartRate', readNumber),
					maxHeartRate: readNullable(session, 'maxHeartRate', readNumber)
				};
			})
		}
	};
}

export async function getActivityMetric(
	metric: 'steps' | 'exercises',
	start: string,
	end: string,
	signal?: AbortSignal,
	client: ApiClient = api
) {
	return parseMetric(
		await client.request(`/health/metrics/${metric}?${new URLSearchParams({ start, end })}`, {
			signal
		})
	);
}

export async function getActivityHours(
	start: string,
	end: string,
	signal?: AbortSignal,
	client: ApiClient = api
): Promise<HourlyStepsDay[]> {
	const data = readRecord(
		await client.request(`/health/timeline?${new URLSearchParams({ start, end })}`, { signal }),
		'timeline'
	);
	return readArray(data, 'days').map((value) => {
		const day = readRecord(value, 'timeline day');
		const hours = day.hourlySteps;
		if (
			hours !== null &&
			(!Array.isArray(hours) ||
				hours.length !== 24 ||
				hours.some((n) => typeof n !== 'number' || !Number.isFinite(n)))
		) {
			throw new ResponseFormatError('hourlySteps');
		}
		return {
			date: readString(day, 'date'),
			hourlySteps:
				hours === null
					? null
					: readArray(day, 'hourlySteps').map((n) => readNumber({ value: n }, 'value'))
		};
	});
}

export async function loadActivity(
	week: ReturnType<typeof activityWeek>,
	today: string,
	signal: AbortSignal,
	client: ApiClient = api
) {
	const results = await Promise.allSettled([
		getActivityMetric('steps', week.start, week.end, signal, client),
		getActivityMetric('exercises', week.start, week.end, signal, client),
		getActivityMetric(
			'steps',
			adjacentDay(week.start, -7),
			adjacentDay(week.start, -1),
			signal,
			client
		),
		getActivityMetric('steps', adjacentDay(today, -27), today, signal, client),
		getActivityHours(week.start, week.end, signal, client)
	] as const);
	for (const result of results) {
		if (
			result.status === 'rejected' &&
			result.reason instanceof ApiError &&
			result.reason.status === 401
		)
			throw result.reason;
	}
	const [steps, exercise, previous, recent, hours] = results;
	if (steps.status === 'rejected') throw steps.reason;
	if (exercise.status === 'rejected') throw exercise.reason;
	return {
		steps: steps.value,
		exercise: exercise.value,
		previous: previous.status === 'fulfilled' ? previous.value : null,
		recent: recent.status === 'fulfilled' ? recent.value.days : null,
		hours: hours.status === 'fulfilled' ? hours.value : null,
		warnings: [
			previous.status === 'rejected' ? '이전 주 비교' : '',
			recent.status === 'rejected' ? '요일별 평균' : '',
			hours.status === 'rejected' ? '시간대별 걸음 수' : ''
		].filter(Boolean)
	};
}

export function activityPeriod(result: ActivityMetricResult): ActivityPeriod {
	return {
		label: formatHealthWeek(result.period.start),
		hasRecords: result.days.some((day) => !day.missing),
		days: result.days.map((day) => ({
			...day,
			steps: day.value,
			label: day.date.slice(5).replace('-', '/'),
			weekday: ['일', '월', '화', '수', '목', '금', '토'][
				new Date(`${day.date}T00:00:00Z`).getUTCDay()
			]
		}))
	};
}
