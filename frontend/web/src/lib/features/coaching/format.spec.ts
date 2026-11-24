import { describe, expect, it } from 'vitest';
import {
	formatClockTime,
	formatConversationTime,
	formatDayWithWeekday,
	formatExerciseActivity,
	formatMetricDelta,
	formatMetricValue,
	formatPeriod,
	formatShortDate,
	formatSleepLabel
} from './format';

describe('formatMetricValue', () => {
	it('값이 없으면 0이 아닌 "-"로 표시한다', () => {
		expect(formatMetricValue('sleeps', null)).toBe('-');
		expect(formatMetricValue('steps', null)).toBe('-');
	});

	it.each([
		['sleeps', 393, '6시간 33분'],
		['sleeps', 45, '45분'],
		['sleeps', 420, '7시간'],
		['steps', 7820.4, '7,820걸음'],
		['steps', 0, '0걸음'],
		['heart_rates', 71.6, '71.6bpm'],
		['heart_rates', 72, '72bpm'],
		['oxygen_saturations', 96.44, '96.4%'],
		// 신체 지표(S15P31A403-311). 키는 m 로 와서 cm 로 보인다
		['weights', 68.04, '68kg'],
		['weights', 68.25, '68.3kg'],
		['body_fats', 23.6, '23.6%'],
		['basal_metabolic_rates', 1478.4, '1,478kcal'],
		['heights', 1.725, '172.5cm'],
		['heights', 1.7, '170cm']
	] as const)('%s %d → %s', (metric, value, expected) => {
		expect(formatMetricValue(metric, value)).toBe(expected);
	});
});

describe('formatMetricDelta', () => {
	it('부호를 붙여 차이를 표시한다', () => {
		expect(formatMetricDelta('sleeps', 18)).toBe('+18분');
		expect(formatMetricDelta('sleeps', -62)).toBe('-1시간 2분');
		expect(formatMetricDelta('steps', 0)).toBe('0걸음');
	});
});

describe('날짜 표시', () => {
	it('짧은 날짜와 기간을 표시한다', () => {
		expect(formatShortDate('2026-10-07')).toBe('10.07');
		expect(formatPeriod('2026-10-01', '2026-10-07')).toBe('2026.10.01 ~ 10.07');
		expect(formatPeriod('2026-12-29', '2027-01-04')).toBe('2026.12.29 ~ 2027.01.04');
	});

	it.each([
		['2026-10-23', '10.23(금)'],
		['2026-10-31', '10.31(토)'],
		['2026-11-01', '11.01(일)'],
		['2026-11-02', '11.02(월)'],
		// 윤년 2월 29일과 해가 바뀌는 날도 달력 그대로 센다
		['2028-02-29', '02.29(화)'],
		['2027-01-01', '01.01(금)']
	])('%s → %s (요일)', (date, expected) => {
		expect(formatDayWithWeekday(date)).toBe(expected);
	});
});

describe('formatConversationTime', () => {
	// KST 2026-10-14 14:00
	const now = new Date('2026-10-14T05:00:00Z');

	it.each([
		['2026-10-14T04:59:30Z', '방금 전'],
		['2026-10-14T00:18:00Z', '오늘 09:18'],
		['2026-10-13T15:30:00Z', '오늘 00:30'],
		['2026-10-13T09:02:00Z', '어제 18:02'],
		['2026-10-12T02:00:00Z', '10.12']
	])('%s → %s (KST 기준)', (iso, expected) => {
		expect(formatConversationTime(iso, now)).toBe(expected);
	});
});

describe('formatClockTime', () => {
	it('수집 중 값의 기준 시각을 KST 시:분으로 쓴다', () => {
		expect(formatClockTime('2026-10-28T05:00:00Z')).toBe('14:00');
		expect(formatClockTime('2026-10-28T15:05:00Z')).toBe('00:05');
	});
});

/** 경민 님 계산 규칙 11.1(S15P31A403-312). 잠든 시각(start_at)의 KST 시각으로 정한다 */
describe('formatSleepLabel', () => {
	it.each([
		// 백엔드 합의 예시
		['2026-11-04T14:00:00Z', '11월 4일 밤'], // 11/4 23:00 KST
		['2026-11-04T16:00:00Z', '11월 4일 밤'], // 11/5 01:00 KST → 전날 밤
		['2026-11-05T00:00:00Z', '11월 5일 아침'], // 11/5 09:00 KST
		['2026-11-05T05:00:00Z', '11월 5일 낮'], // 11/5 14:00 KST
		// 경계
		['2026-11-05T05:59:00+09:00', '11월 4일 밤'],
		['2026-11-05T06:00:00+09:00', '11월 5일 아침'],
		['2026-11-05T09:59:00+09:00', '11월 5일 아침'],
		['2026-11-05T10:00:00+09:00', '11월 5일 낮'],
		['2026-11-05T17:59:00+09:00', '11월 5일 낮'],
		['2026-11-05T18:00:00+09:00', '11월 5일 밤'],
		['2026-11-05T23:59:00+09:00', '11월 5일 밤'],
		['2026-11-06T00:00:00+09:00', '11월 5일 밤'],
		// 달·해가 바뀌는 전날 밤
		['2026-12-01T02:00:00+09:00', '11월 30일 밤'],
		['2027-01-01T03:00:00+09:00', '12월 31일 밤']
	])('%s → %s', (startAt, expected) => {
		expect(formatSleepLabel(startAt)).toBe(expected);
	});
});

describe('formatExerciseActivity (S15P31A403-375)', () => {
	it('횟수·칼로리는 정수, 거리는 m 를 km 소수 한 자리로 쓴다', () => {
		expect(formatExerciseActivity('exercise_count', 4)).toBe('4회');
		expect(formatExerciseActivity('exercise_calories_kcal', 1170.4)).toBe('1,170kcal');
		expect(formatExerciseActivity('exercise_distance_m', 12500)).toBe('12.5km');
		expect(formatExerciseActivity('exercise_distance_m', 2000)).toBe('2.0km');
		expect(formatExerciseActivity('exercise_distance_m', 0)).toBe('0.0km');
	});

	it('하루 평균 횟수만 소수 한 자리까지 둔다', () => {
		expect(formatExerciseActivity('exercise_count', 0.8, true)).toBe('0.8회');
		expect(formatExerciseActivity('exercise_count', 2, true)).toBe('2회');
		expect(formatExerciseActivity('exercise_calories_kcal', 234.5, true)).toBe('235kcal');
	});
});
