import type { ExerciseSession } from '../features/health/activity';
import type { SleepNight } from '../features/health/sleep';
import type { HealthMetricDay } from '../features/health/day-detail';

// backend/alembic/versions/20261022_1602_da67f306e376_baseline_schema.py의 운동 기준표.
const exerciseTypes: Record<string, { code: number; category: 'cardio' | 'flexibility' }> = {
	걷기: { code: 79, category: 'cardio' },
	달리기: { code: 56, category: 'cardio' },
	'실내 자전거': { code: 9, category: 'cardio' },
	스트레칭: { code: 71, category: 'flexibility' }
};

/** 화면용 id를 REST recordId로 바꾸되 저장된 인정 여부·속도·수치는 재판정하지 않는다. */
export function exerciseSessionFixture(
	session: ExerciseSession,
	index: number
): Omit<ExerciseSession, 'id'> & {
	recordId: string;
	startAt: string;
	endAt: string;
	exerciseType: number;
	category: 'cardio' | 'strength' | 'flexibility' | 'other';
	title: string | null;
	distanceM: number | null;
} {
	const { id, ...values } = session;
	const type = exerciseTypes[session.typeName];
	if (!type) throw new Error(`운동 fixture 기준표에 없는 종류: ${session.typeName}`);
	const startAt = `${session.date}T${index % 2 ? '09' : '03'}:00:00Z`;
	return {
		...values,
		recordId: id,
		startAt,
		endAt: new Date(Date.parse(startAt) + session.durationMinutes * 60000).toISOString(),
		exerciseType: type.code,
		category: type.category,
		title: null,
		distanceM: null
	};
}

/** 기존 수면 화면용 부분 Mock을 HTTP 응답으로 만든다. 없는 측정값은 0으로 추정하지 않는다. */
export function sleepNightFixture(
	night: SleepNight,
	oxygen?: { avg: number | null; min: number | null; max: number | null }
) {
	return {
		...night,
		sessionMinutes: Math.round((Date.parse(night.endAt) - Date.parse(night.startAt)) / 60000),
		unspecifiedMinutes: null,
		unknownMinutes: null,
		avgHeartRate: null,
		minHeartRate: null,
		maxHeartRate: null,
		spo2Avg: oxygen?.avg ?? null,
		spo2Min: oxygen?.min ?? null,
		spo2Max: oxygen?.max ?? null,
		spo2Count: null,
		spo2LowCount: null,
		spo2LowTimes: null
	};
}

/** 화면 매핑 결과는 HTTP 전체가 아니다. 기존 5개 필드의 엄격한 동등 비교를 유지한다. */
export function metricDayViewFixture({
	date,
	value,
	missing,
	reason,
	isInProgress
}: HealthMetricDay) {
	return { date, value, missing, reason, isInProgress };
}

export function sleepNightViewFixture({
	date,
	startAt,
	endAt,
	sleepMinutes,
	deepMinutes,
	remMinutes,
	lightMinutes,
	awakeMinutes
}: SleepNight) {
	return {
		date,
		startAt,
		endAt,
		sleepMinutes,
		deepMinutes,
		remMinutes,
		lightMinutes,
		awakeMinutes
	};
}
