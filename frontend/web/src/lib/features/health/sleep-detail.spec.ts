import { describe, expect, it } from 'vitest';
import {
	isSleepDetailDate,
	sleepKstDate,
	sleepMidnights,
	sleepMissingText,
	sleepVisibleSegment,
	sleepStageDurations,
	sleepStagePercentage,
	sleepTimelinePosition,
	sleepTimelineTicks,
	type SleepSegment
} from './sleep-detail';
import {
	sleepDetailDates,
	sleepDetailDays,
	sleepDetailNights,
	sleepDetailTimelineMock,
	sleepDetailOxygenDays
} from './sleep-detail-mock';
import { sleepMockWeeks } from './sleep-mock';

const mainSleep = { startAt: '2026-10-22T14:50:00Z', endAt: '2026-10-22T22:30:00Z' };

describe('수면 상세 날짜와 시간축', () => {
	it.each(['2026-10-23', '2024-02-29'])('실제 달력 날짜 %s를 허용한다', (value) => {
		expect(isSleepDetailDate(value)).toBe(true);
	});
	it.each(['abc', '0000-01-01', '2026-02-30', '2026-02-29', '2026-13-01', '2026-1-01'])(
		'잘못된 날짜 %s를 거부한다',
		(value) => {
			expect(isSleepDetailDate(value)).toBe(false);
		}
	);
	it('KST 자정 경계를 시간축 비율로 표시하고 같은 날짜 수면에는 생성하지 않는다', () => {
		expect(sleepKstDate(mainSleep.startAt)).toBe('2026-10-22');
		expect(sleepKstDate(mainSleep.endAt)).toBe('2026-10-23');
		expect(sleepMidnights(mainSleep)).toEqual(['2026-10-22T15:00:00.000Z']);
		expect(sleepTimelinePosition('2026-10-22T15:00:00Z', mainSleep)).toBeCloseTo((10 / 460) * 100);
		expect(sleepTimelinePosition(mainSleep.startAt, mainSleep)).toBe(0);
		expect(sleepTimelinePosition(mainSleep.endAt, mainSleep)).toBe(100);
		expect(
			sleepMidnights({ startAt: '2026-10-22T16:00:00Z', endAt: '2026-10-22T22:00:00Z' })
		).toEqual([]);
		expect(
			sleepTimelineTicks(mainSleep).every(
				(tick) =>
					Date.parse(tick) > Date.parse(mainSleep.startAt) &&
					Date.parse(tick) < Date.parse(mainSleep.endAt)
			)
		).toBe(true);
	});
});

describe('수면 단계 통계', () => {
	const segments: SleepSegment[] = [
		{ startAt: '2026-10-22T15:00:00Z', endAt: '2026-10-22T15:30:00Z', stage: 'deep' },
		{ startAt: '2026-10-22T15:30:00Z', endAt: '2026-10-22T15:40:00Z', stage: 'awake' },
		{ startAt: '2026-10-22T15:40:00Z', endAt: '2026-10-22T16:00:00Z', stage: 'deep' },
		{ startAt: '2026-10-22T16:00:00Z', endAt: '2026-10-22T16:30:00Z', stage: 'unknown' },
		{ startAt: '2026-10-22T16:30:00Z', endAt: '2026-10-22T17:00:00Z', stage: 'unspecified' }
	];
	it('동일 단계의 시간을 합하고 분류되지 않은 구간은 4개 단계에 합산하지 않는다', () => {
		expect(sleepStageDurations(segments)).toEqual({ deep: 50, awake: 10, light: 0, rem: 0 });
		expect(sleepStageDurations([])).toEqual({ deep: null, awake: null, light: null, rem: null });
	});
	it('비율은 실제 수면 분을 분모로 반올림하며 null/0일 때 만들지 않는다', () => {
		expect(sleepStagePercentage(84, 394)).toBe(21);
		expect(sleepStagePercentage(84, null)).toBeNull();
		expect(sleepStagePercentage(84, 0)).toBeNull();
		expect(sleepStagePercentage(null, 394)).toBeNull();
		expect(sleepStagePercentage(0, 394)).toBe(0);
	});
});

describe('기존 수면 mock 재사용', () => {
	it('모든 날짜의 취침·기상·수면 분·산소포화도가 메인과 일치한다', () => {
		for (const week of sleepMockWeeks) {
			for (const day of week.sleep.days)
				expect(sleepDetailDays.find((item) => item.date === day.date)).toBe(day);
			for (const night of week.sleep.detail.nights) {
				expect(sleepDetailNights.find((item) => item.date === night.date)).toBe(night);
				const timeline = sleepDetailTimelineMock.days.find((item) => item.date === night.date);
				expect(timeline?.mainSleep).toEqual({ startAt: night.startAt, endAt: night.endAt });
				if (timeline?.sleepSegments.length) {
					expect(timeline.sleepSegments[0].startAt).toBe(night.startAt);
					expect(timeline.sleepSegments.at(-1)?.endAt).toBe(night.endAt);
					expect(sleepStageDurations(timeline.sleepSegments)).toEqual({
						deep: night.deepMinutes,
						rem: night.remMinutes,
						light: night.lightMinutes,
						awake: night.awakeMinutes
					});
				}
			}
			for (const day of week.oxygen.days)
				expect(sleepDetailOxygenDays.find((item) => item.date === day.date)).toBe(day);
		}
		expect(sleepDetailDates.every((date) => date <= '2026-10-26')).toBe(true);
	});
	it('수면 없음, 단계만 없음, 수면 분 null을 서로 구분한다', () => {
		expect(
			sleepDetailTimelineMock.days.find((day) => day.date === '2026-10-22')?.mainSleep
		).toBeNull();
		expect(
			sleepDetailTimelineMock.days.find((day) => day.date === '2026-10-21')?.sleepSegments
		).toEqual([]);
		expect(sleepDetailNights.find((night) => night.date === '2026-10-26')?.sleepMinutes).toBeNull();
	});
});

describe('결측 사유 표시', () => {
	it.each([
		['NO_RECORD', '해당 날짜의 수면 기록이 없어요.', '기록 없음'],
		['UNSYNCED', '아직 건강 데이터가 동기화되지 않았어요.', '동기화 대기'],
		['BEFORE_LINK', '연동 전의 기록은 확인할 수 없어요.', '연동 전'],
		['NULL_VALUE', '해당 정보가 기록되지 않았어요.', '값 없음']
	] as const)('%s는 서버 사유를 그대로 문구로 매핑한다', (reason, record, value) => {
		expect(sleepMissingText(reason, 'record')).toBe(record);
		expect(sleepMissingText(reason)).toBe(value);
	});
});

describe('표시 구간 제한', () => {
	const main = { startAt: '2026-10-22T15:00:00Z', endAt: '2026-10-22T22:00:00Z' };
	it('앞뒤 초과는 표시 좌표만 제한하고 통계는 원본 구간을 사용한다', () => {
		const segments: SleepSegment[] = [
			{ startAt: '2026-10-22T14:00:00Z', endAt: '2026-10-22T16:00:00Z', stage: 'deep' },
			{ startAt: '2026-10-22T21:00:00Z', endAt: '2026-10-22T23:00:00Z', stage: 'rem' },
			{ startAt: '2026-10-22T23:00:00Z', endAt: '2026-10-23T00:00:00Z', stage: 'light' }
		];
		const original = structuredClone(segments);
		const before = sleepStageDurations(segments);
		const first = sleepVisibleSegment(segments[0], main);
		const last = sleepVisibleSegment(segments[1], main);
		expect(first?.left).toBe(0);
		expect(first?.width).toBeCloseTo(100 / 7);
		expect(last?.left).toBeCloseTo(600 / 7);
		expect((last?.left ?? 0) + (last?.width ?? 0)).toBeCloseTo(100);
		expect(sleepVisibleSegment(segments[2], main)).toBeNull();
		expect(sleepStageDurations(segments)).toEqual(before);
		expect(before).toEqual({ deep: 120, rem: 120, light: 60, awake: 0 });
		expect(sleepStagePercentage(before.deep, 300)).toBe(40);
		expect(segments).toEqual(original);
	});
	it('자정에 정확히 종료해도 KST 다음 날짜 경계를 포함한다', () => {
		const midnight = { startAt: '2026-10-22T14:00:00Z', endAt: '2026-10-22T15:00:00Z' };
		expect(sleepMidnights(midnight)).toEqual(['2026-10-22T15:00:00.000Z']);
		expect(sleepTimelinePosition(sleepMidnights(midnight)[0], midnight)).toBe(100);
		expect(
			sleepMidnights({ startAt: '2026-10-22T16:00:00Z', endAt: '2026-10-22T22:00:00Z' })
		).toEqual([]);
	});
});
