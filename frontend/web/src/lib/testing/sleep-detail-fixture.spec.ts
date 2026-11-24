import { expect, it } from 'vitest';
import { sleepDetailFixture } from './sleep-detail-fixture';

it('단일 기상일의 요약·세션·단계를 일치시키고 원본 단계 배열을 변경하지 않는다', () => {
	const segments = [
		{
			stage: 'deep' as const,
			startAt: '2026-10-20T00:00:00+09:00',
			endAt: '2026-10-20T01:00:00+09:00'
		},
		{
			stage: 'light' as const,
			startAt: '2026-10-19T23:00:00+09:00',
			endAt: '2026-10-20T00:00:00+09:00'
		}
	];
	const result = sleepDetailFixture({
		date: '2026-10-20',
		mainSleep: { startAt: segments[1].startAt, endAt: segments[0].endAt },
		sleepSegments: segments
	});
	expect(result.sleep.summary).toMatchObject({
		avg: 120,
		completedDays: 1,
		daysWithData: 1,
		daysUsedForSummary: 1,
		total: null,
		dailyMin: { date: '2026-10-20', value: 120 },
		dailyMax: { date: '2026-10-20', value: 120 }
	});
	expect(result.sleep.detail.nights[0]).toMatchObject({
		startAt: '2026-10-19T14:00:00.000Z',
		endAt: '2026-10-19T16:00:00.000Z',
		sleepMinutes: 120,
		sessionMinutes: 120,
		lightMinutes: 60,
		deepMinutes: 60,
		remMinutes: 0
	});
	expect(result.timeline.days[0].mainSleep).toEqual({
		startAt: '2026-10-19T14:00:00.000Z',
		endAt: '2026-10-19T16:00:00.000Z'
	});
	expect(result.timeline.days[0].sleepSegments.map((segment) => segment.stage)).toEqual([
		'light',
		'deep'
	]);
	expect(segments.map((segment) => segment.stage)).toEqual(['deep', 'light']);
	expect(result.sleep.inProgress).toBeNull();
	expect(result.oxygen.detail.measuredMin).toEqual({ date: '2026-10-20', value: 96 });
	expect(result.sleep.detail.nights[0]).toMatchObject({ spo2Avg: 97 });
});

it('유효한 0·null·전체 기록 없음을 구분하며 시나리오 사이에 객체를 공유하지 않는다', () => {
	const day = {
		date: '2026-10-20',
		mainSleep: { startAt: '2026-10-19T21:40:00Z', endAt: '2026-10-19T22:00:00Z' },
		sleepSegments: []
	};
	const zero = sleepDetailFixture(day, 0);
	const missing = sleepDetailFixture(day, null);
	const empty = sleepDetailFixture({ ...day, mainSleep: null });
	expect(zero.sleep.days[0]).toMatchObject({ value: 0, missing: false, reason: null });
	expect(zero.sleep.summary.dailyMin).toEqual({ date: day.date, value: 0 });
	expect(missing.sleep.days[0]).toMatchObject({ value: null, missing: true, reason: 'NULL_VALUE' });
	expect(missing.sleep.summary).toMatchObject({
		avg: null,
		dailyMin: null,
		dailyMax: null,
		daysWithData: 0
	});
	expect(empty.sleep.detail.nights).toEqual([]);
	expect(empty.oxygen.days[0]).toMatchObject({ value: null, reason: 'NO_RECORD' });
	expect(empty.oxygen.detail.dailyRanges[0]).toMatchObject({
		date: day.date,
		min: null,
		max: null,
		sampleCount: null
	});
	expect(zero.sleep.detail.nights[0].sleepMinutes).toBe(0);
});
