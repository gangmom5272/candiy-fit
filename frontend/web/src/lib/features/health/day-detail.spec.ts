import { describe, expect, it } from 'vitest';
import {
	adjacentDay,
	clipDayInterval,
	dayHeartSegments,
	dayHourCenter,
	dayTimeX,
	dayTimelineStart,
	formatDayMetric
} from './day-detail';
import { getDayDetailMock } from './day-detail-mock';
import {
	exerciseSessions,
	getActivityPeriod,
	getExerciseSummary,
	hourlyStepDays
} from './activity';
import { getHeartMockWeek } from './heart-mock';
import { sleepDetailDays, sleepDetailNights } from './sleep-detail-mock';

describe('하루 상세 API projection과 기존 mock 정합성', () => {
	it('같은 날짜의 Activity·Sleep·Heart 일별 값을 그대로 표시한다', () => {
		const period = getActivityPeriod(0);
		const exercise = getExerciseSummary(period, exerciseSessions).metrics.find(
			(metric) => metric.kind === 'time'
		);
		for (const [index, day] of period.days.entries()) {
			const result = getDayDetailMock(day.date);
			expect(result.steps.value).toBe(day.steps);
			expect(result.exercise.value).toBe(exercise?.trend[index]);
			expect(result.sleep.value).toBe(
				sleepDetailDays.find((item) => item.date === day.date)?.value
			);
			expect(result.heart.value).toBe(getHeartMockWeek(-1).days[index].value);
			expect(result.timeline.hourlySteps).toEqual(hourlyStepDays[index].hourlySteps);
			expect(result.timeline.exerciseSegments).toHaveLength(
				exerciseSessions.filter((session) => session.date === day.date && session.countsAsExercise)
					.length
			);
		}
	});
	it('운동 0분을 유효하게 보존하고 미인정 세션은 타임라인에서 제외한다', () => {
		const result = getDayDetailMock('2026-10-21');
		expect(result.exercise).toMatchObject({ value: 0, missing: false, reason: null });
		expect(formatDayMetric(result.exercise, 'min')).toBe('0분');
		expect(result.timeline.exerciseSegments).toHaveLength(0);
		expect(result.steps.value).toBeNull();
		expect(result.heart.value).toBeNull();
		expect(result.sleep.value).not.toBeNull();
	});
	it('총 수면은 실제 수면 분이며 구간 경과 시간으로 대체하지 않는다', () => {
		const result = getDayDetailMock('2026-10-19');
		const night = sleepDetailNights.find((night) => night.date === result.sleep.date);
		expect(night).toBeDefined();
		if (!night) throw new Error('테스트 수면 없음');
		expect(result.sleep.value).toBe(night.sleepMinutes);
		expect(result.sleep.value).not.toBe(
			(Date.parse(night.endAt) - Date.parse(night.startAt)) / 60000
		);
	});
	it('시간대 심박과 기존 일평균·기간 가중 평균·측정 범위가 서로 맞는다', () => {
		let total = 0;
		let hours = 0;
		for (const day of getHeartMockWeek(-1).days) {
			const record = getDayDetailMock(day.date);
			const values = record.timeline.hourlyHeartRates?.filter((hour) => hour !== null) ?? [];
			if (day.missing) {
				expect(record.timeline.hourlyHeartRates).toBeNull();
				continue;
			}
			expect(values.reduce((sum, hour) => sum + hour.avg, 0) / values.length).toBeCloseTo(
				day.value ?? NaN,
				1
			);
			const range = getHeartMockWeek(-1).detail.dailyRanges.find(
				(range) => range.date === day.date
			);
			expect(Math.min(...values.map((hour) => hour.min))).toBe(range?.min);
			expect(Math.max(...values.map((hour) => hour.max))).toBe(range?.max);
			total += values.reduce((sum, hour) => sum + hour.avg, 0);
			hours += values.length;
		}
		expect(Math.round((total / hours) * 100) / 100).toBe(getHeartMockWeek(-1).summary.avg);
	});
	it('현재 주는 당일 일평균을 사용하고 summary.avg=null을 대신 사용하지 않는다', () => {
		const result = getDayDetailMock('2026-10-26');
		expect(result.heart.value).toBe(74.2);
		expect(result.timeline.isInProgress).toBe(true);
		expect(result.sleep).toMatchObject({ value: null, reason: 'UNSYNCED', isInProgress: false });
		expect(result.timeline.mainSleep).not.toBeNull();
		expect(result.steps).toMatchObject({ value: null, reason: 'UNSYNCED' });
	});
	it('존재하지 않는 날짜의 데이터를 0으로 채우지 않는다', () => {
		const result = getDayDetailMock('2026-10-07');
		expect(
			[result.steps, result.exercise, result.sleep, result.heart].every(
				(day) => day.value === null && day.missing
			)
		).toBe(true);
		expect(result.timeline.hourlySteps).toBeNull();
		expect(result.timeline.hourlyHeartRates).toBeNull();
		expect(result.timeline.mainSleep).toBeNull();
	});
});

describe('KST 공통 24시간 축과 표시용 변환', () => {
	it('이전/다음 날짜와 월·연도 경계를 로컬 시간대와 무관하게 계산한다', () => {
		expect(adjacentDay('2026-11-01', -1)).toBe('2026-10-31');
		expect(adjacentDay('2026-12-31', 1)).toBe('2027-01-01');
		expect(dayTimeX(0)).toBe(0);
		expect(dayTimeX(720)).toBe(50);
		expect(dayTimeX(1440)).toBe(100);
		expect(dayTimeX(dayHourCenter(3))).toBeCloseTo((210 / 1440) * 100);
	});
	it('전날 실제 취침까지 축을 확장하고 원본 구간을 보존한다', () => {
		const span = { startAt: '2026-10-18T14:30:00Z', endAt: '2026-10-18T21:00:00Z' };
		const before = JSON.stringify(span);
		const start = dayTimelineStart({ date: '2026-10-19', mainSleep: span });
		expect(start).toBe(-30);
		expect(dayTimeX(-30, start)).toBe(0);
		expect(dayTimeX(1440, start)).toBe(100);
		expect(clipDayInterval('2026-10-19', span.startAt, span.endAt, start)).toEqual({
			start: -30,
			end: 360
		});
		expect(JSON.stringify(span)).toBe(before);
		expect(
			clipDayInterval('2026-10-19', '2026-10-19T14:45:00Z', '2026-10-19T16:00:00Z', start)
		).toEqual({
			start: 1425,
			end: 1440
		});
		expect(clipDayInterval('2026-10-20', span.startAt, span.endAt, 0)).toBeNull();
		expect(dayTimelineStart({ date: '2026-10-19', mainSleep: null })).toBe(0);
		expect(dayTimelineStart({ date: '2026-10-18', mainSleep: span })).toBe(0);
	});
	it('심박 결측 시간은 line을 끊고 고립된 값도 보존한다', () => {
		const hours = [
			null,
			{ avg: 60, min: 50, max: 70 },
			null,
			{ avg: 80, min: 70, max: 90 },
			{ avg: 90, min: 80, max: 100 }
		];
		expect(dayHeartSegments(hours)).toEqual([
			[{ minute: 90, value: 60 }],
			[
				{ minute: 210, value: 80 },
				{ minute: 270, value: 90 }
			]
		]);
		expect(dayHeartSegments(null)).toEqual([]);
	});
	it('서버 reason을 유지하고 0보 결측을 실제 0bpm으로 만들지 않는다', () => {
		const missing = { date: '2026-10-19', value: null, missing: true, isInProgress: false };
		expect(formatDayMetric({ ...missing, reason: 'UNSYNCED' }, 'bpm')).toBe('동기화 대기');
		expect(formatDayMetric({ ...missing, reason: 'BEFORE_LINK' }, 'steps')).toBe('연동 전');
		expect(formatDayMetric({ ...missing, reason: 'NULL_VALUE' }, 'min')).toBe('값 없음');
		expect(formatDayMetric({ ...missing, reason: 'ZERO_STEPS', value: 0 }, 'steps')).toBe(
			'기록 없음'
		);
	});
});
