import { previousSleepResult, sleepMockWeeks, SLEEP_MOCK_TODAY } from './sleep-mock';
import type { SleepNight } from './sleep';
import type { SleepSegment, SleepTimelineDay } from './sleep-detail';

// 메인에서 사용하는 객체를 그대로 참조한다. 상세 전용 수면/산소포화도 값은 만들지 않는다.
const sleepResults = [previousSleepResult, ...sleepMockWeeks.map((week) => week.sleep)];
export const sleepDetailDates = [
	...new Set(sleepResults.flatMap((result) => result.days.map((day) => day.date)))
].sort();
export const sleepDetailNights = sleepResults.flatMap((result) => result.detail.nights);
export const sleepDetailDays = sleepResults.flatMap((result) => result.days);
export const sleepDetailOxygenDays = sleepMockWeeks.flatMap((week) => week.oxygen.days);

/** 기존 단계별 분을 시간순 구간으로 나눈 합성 timeline. 일별 실제 수면 분은 재계산하지 않는다. */
function mockSleepSegments(night: SleepNight): SleepSegment[] {
	// 일별 기록이 있어도 단계 기록은 없을 수 있다.
	if (
		night.date === '2026-10-21' ||
		night.lightMinutes === null ||
		night.deepMinutes === null ||
		night.remMinutes === null ||
		night.awakeMinutes === null
	)
		return [];
	const parts: { stage: SleepSegment['stage']; minutes: number }[] = [
		{ stage: 'light', minutes: night.lightMinutes / 4 },
		{ stage: 'deep', minutes: night.deepMinutes / 2 },
		{ stage: 'light', minutes: night.lightMinutes / 4 },
		{ stage: 'rem', minutes: night.remMinutes / 3 },
		{ stage: 'awake', minutes: night.awakeMinutes / 2 },
		{ stage: 'light', minutes: night.lightMinutes / 4 },
		{ stage: 'deep', minutes: night.deepMinutes / 2 },
		{ stage: 'rem', minutes: night.remMinutes / 3 },
		{ stage: 'light', minutes: night.lightMinutes / 4 },
		{ stage: 'awake', minutes: night.awakeMinutes / 2 },
		{ stage: 'rem', minutes: night.remMinutes / 3 }
	];
	let cursor = Date.parse(night.startAt);
	return parts
		.filter((part) => part.minutes > 0)
		.map((part) => {
			const startAt = new Date(cursor).toISOString();
			cursor += part.minutes * 60000;
			return { startAt, endAt: new Date(cursor).toISOString(), stage: part.stage };
		});
}

/** GET /health/timeline 응답 data와 같은 구조. 수면 외 시간대 데이터는 미수집 상태다. */
export const sleepDetailTimelineMock = {
	period: { start: sleepDetailDates[0], end: SLEEP_MOCK_TODAY },
	days: sleepDetailDates.map((date) => {
		const night = sleepDetailNights.find((item) => item.date === date);
		return {
			date,
			isInProgress: date === SLEEP_MOCK_TODAY,
			hourlySteps: null,
			hourlyHeartRates: null,
			exerciseSegments: [],
			mainSleep: night ? { startAt: night.startAt, endAt: night.endAt } : null,
			sleepSegments: night ? mockSleepSegments(night) : []
		} satisfies SleepTimelineDay & {
			isInProgress: boolean;
			hourlySteps: null;
			hourlyHeartRates: null;
			exerciseSegments: [];
		};
	})
};
