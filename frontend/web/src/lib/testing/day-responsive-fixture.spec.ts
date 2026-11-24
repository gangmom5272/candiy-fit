import { describe, expect, it } from 'vitest';
import { dayResponsiveFixture } from './day-responsive-fixture';

describe('하루 상세 반응형 합성 응답의 계약 정합성', () => {
	it('완료된 날짜의 요약·원본 시간대·수면 단계와 세션을 일치시킨다', () => {
		const { metrics, timeline } = dayResponsiveFixture('2026-11-09');
		expect(metrics.steps.summary).toMatchObject({
			avg: 11100,
			total: 11100,
			completedDays: 1,
			daysWithData: 1
		});
		expect(metrics.exercises.summary.total).toBe(30);
		expect(metrics.sleeps.days[0].value).toBe(450);
		expect(metrics.sleeps.detail.nights[0]).toMatchObject({
			startAt: '2026-11-08T14:00:00.000Z',
			endAt: '2026-11-08T22:00:00.000Z',
			sleepMinutes: 450,
			sessionMinutes: 480,
			deepMinutes: 160,
			lightMinutes: 290,
			awakeMinutes: 30
		});
		expect(metrics.heart_rates.summary).toMatchObject({
			avg: 71.5,
			dailyMin: { date: '2026-11-09', value: 71.5 }
		});
		expect(metrics.heart_rates.detail).toMatchObject({
			measuredMin: { value: 60 },
			measuredMax: { value: 93 },
			dailyRanges: [{ date: '2026-11-09', min: 60, max: 93, sampleCount: 72 }]
		});
		expect(timeline.days[0].hourlySteps).toHaveLength(24);
		expect(timeline.days[0].hourlyHeartRates).toHaveLength(24);
		expect(timeline.days[0].exerciseSegments[0]).toMatchObject({
			startAt: '2026-11-09T03:00:00.000Z',
			endAt: '2026-11-09T03:30:00.000Z'
		});
	});
	it('오늘 완료된 수면은 포함하고 활동·심박의 진행 중 요약은 제외한다', () => {
		const { metrics, timeline } = dayResponsiveFixture('2026-11-10');
		expect(metrics.steps.summary).toMatchObject({ completedDays: 0, daysWithData: 0, total: null });
		expect(metrics.steps.inProgress).toMatchObject({ value: 2886, asOf: '2026-11-10T02:50:00Z' });
		expect(metrics.sleeps.summary).toMatchObject({ completedDays: 1, daysWithData: 1, avg: 450 });
		expect(metrics.sleeps.inProgress).toBeNull();
		expect(metrics.heart_rates.detail.dailyRanges[0].sampleCount).toBe(36);
		expect(timeline.days[0].hourlyHeartRates?.slice(12)).toEqual(Array(12).fill(null));
		expect(timeline.days[0].hourlySteps?.slice(12)).toEqual(Array(12).fill(0));
		const todayZero = dayResponsiveFixture('2026-11-10', 'zero').metrics.steps;
		expect(todayZero.days[0]).toMatchObject({ value: 0, reason: 'UNSYNCED', isInProgress: true });
		expect(todayZero.summary).toMatchObject({
			completedDays: 0,
			missingDays: [],
			unsyncedDays: ['2026-11-10']
		});
	});
	it('전체 결측·유효한 0·null을 각각 보존한다', () => {
		const empty = dayResponsiveFixture('2026-11-09', 'empty');
		expect(empty.metrics.steps.summary).toMatchObject({
			completedDays: 1,
			daysWithData: 0,
			dailyMin: null
		});
		expect(empty.timeline.days[0]).toMatchObject({
			hourlySteps: null,
			hourlyHeartRates: null,
			mainSleep: null,
			sleepSegments: [],
			exerciseSegments: []
		});
		expect(empty.metrics.heart_rates.detail.dailyRanges).toEqual([
			{ date: '2026-11-09', min: null, max: null, sampleCount: null }
		]);
		const zero = dayResponsiveFixture('2026-11-09', 'zero');
		expect(zero.metrics.steps.days[0]).toMatchObject({
			value: 0,
			missing: true,
			reason: 'ZERO_STEPS'
		});
		expect(zero.metrics.exercises.days[0]).toMatchObject({ value: 0, missing: false });
		expect(zero.timeline.days[0].hourlySteps).toEqual(Array(24).fill(0));
		expect(dayResponsiveFixture('2026-11-09', 'sleepzero').metrics.sleeps.days[0]).toMatchObject({
			value: 0,
			missing: false
		});
		expect(dayResponsiveFixture('2026-11-09', 'sleepnull').metrics.sleeps.days[0]).toMatchObject({
			value: null,
			reason: 'NULL_VALUE'
		});
	});
	it('단일 기록·큰 값·자정 통과 세션의 경계 조건을 유지하고 공유 객체를 변경하지 않는다', () => {
		const single = dayResponsiveFixture('2026-11-09', 'single');
		expect(single.metrics.steps.summary.total).toBe(481);
		expect(single.metrics.heart_rates.detail.dailyRanges[0]).toMatchObject({
			min: 60,
			max: 90,
			sampleCount: 3
		});
		const dense = dayResponsiveFixture('2026-11-09', 'dense');
		expect(dense.metrics.steps.summary.total).toBe(2400000);
		expect(dense.metrics.exercises.summary.total).toBe(32);
		expect(dense.timeline.days[0].exerciseSegments[1]).toMatchObject({
			startAt: '2026-11-09T14:59:00.000Z',
			endAt: '2026-11-09T15:01:00.000Z'
		});
		dense.timeline.days[0].hourlySteps?.fill(0);
		expect(dayResponsiveFixture('2026-11-09', 'dense').timeline.days[0].hourlySteps?.[0]).toBe(
			100000
		);
	});
});
