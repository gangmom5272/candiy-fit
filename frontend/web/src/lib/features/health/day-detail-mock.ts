import {
	activityReferenceDate,
	exerciseSessions,
	hourlyStepDays,
	recentStepRecords
} from './activity';
import { getHeartMockHours, getHeartMockWeek } from './heart-mock';
import { sleepDetailTimelineMock } from './sleep-detail-mock';
import { getSleepMockWeek } from './sleep-mock';
import { healthMockLinkedFrom, healthMockSyncedDate } from './mock-user';
import type { HealthMetricDay, HealthTimelineDay } from './day-detail';

export const DAY_DETAIL_MOCK_TODAY = activityReferenceDate;

function metricDay(
	date: string,
	value: number | null,
	reason: HealthMetricDay['reason'] = null,
	isInProgress = date === DAY_DETAIL_MOCK_TODAY
): HealthMetricDay {
	return {
		date,
		value,
		missing: reason !== null,
		reason,
		isInProgress
	};
}

/** §5.2의 네 days[] 항목과 §5.4의 days[] 한 항목을 모은다. 화면용 재집계는 하지 않는다. */
export function getDayDetailMock(date: string) {
	const missingReason =
		date < healthMockLinkedFrom
			? 'BEFORE_LINK'
			: date >= healthMockSyncedDate
				? 'UNSYNCED'
				: 'NO_RECORD';
	const stepsValue = recentStepRecords.find((day) => day.date === date)?.value ?? null;
	const steps = metricDay(date, stepsValue, stepsValue === null ? missingReason : null);
	const sessions = exerciseSessions.filter((session) => session.date === date);
	// 모의 서버 응답 생성에만 사용. 인정 여부는 기존 fixture 값을 그대로 따른다.
	const exercise = metricDay(
		date,
		sessions.length
			? sessions.filter((s) => s.countsAsExercise).reduce((sum, s) => sum + s.durationMinutes, 0)
			: null,
		sessions.length ? null : missingReason
	);
	const weekOffset = Math.min(
		0,
		Math.floor(
			(Date.parse(`${date}T00:00:00Z`) - Date.parse(`${DAY_DETAIL_MOCK_TODAY}T00:00:00Z`)) /
				(7 * 86400000)
		)
	);
	const heart =
		getHeartMockWeek(weekOffset).days.find((day) => day.date === date) ??
		metricDay(date, null, missingReason);
	const sleep =
		getSleepMockWeek(weekOffset).sleep.days.find((day) => day.date === date) ??
		metricDay(date, null, missingReason, false);
	const sleepTimeline = sleepDetailTimelineMock.days.find((day) => day.date === date);
	const timeline: HealthTimelineDay = {
		date,
		isInProgress: date === DAY_DETAIL_MOCK_TODAY,
		hourlySteps: hourlyStepDays.find((day) => day.date === date)?.hourlySteps?.slice() ?? null,
		hourlyHeartRates: getHeartMockHours(date),
		mainSleep: sleepTimeline?.mainSleep ?? null,
		sleepSegments: sleepTimeline?.sleepSegments ?? [],
		// 하루 상세에는 기존 원천의 인정 여부가 true인 운동만 제공한다.
		exerciseSegments: sessions.flatMap((session, index) => {
			if (!session.countsAsExercise) return [];
			const start = new Date(`${date}T${index === 0 ? '12' : '18'}:00:00+09:00`);
			return [
				{
					startAt: start.toISOString(),
					endAt: new Date(start.getTime() + session.durationMinutes * 60000).toISOString(),
					exerciseType:
						session.typeName === '걷기'
							? 79
							: session.typeName === '달리기'
								? 56
								: session.typeName === '실내 자전거'
									? 9
									: 71,
					typeName: session.typeName,
					title: null
				}
			];
		})
	};
	return { steps, exercise, sleep, heart, timeline };
}
