import { expect, it } from 'vitest';
import { normalHeartSamples, responsiveHeartFixture } from './heart-responsive-fixture';

it('오늘 7번째 기록은 유지하지만 summary는 완료된 6일의 유효 시간으로 가중한다', () => {
	const result = responsiveHeartFixture('2026-11-10');
	expect(result.summary).toMatchObject({
		avg: 69.77,
		completedDays: 6,
		daysWithData: 6,
		dailyMin: { date: '2026-11-06', value: 65.5 },
		dailyMax: { date: '2026-11-07', value: 74 }
	});
	expect(result.detail.measuredMin).toEqual({ date: '2026-11-06', value: 50 });
	expect(result.detail.measuredMax).toEqual({ date: '2026-11-07', value: 110 });
	expect(result.days).toHaveLength(7);
	expect(result.inProgress).toEqual({
		date: '2026-11-10',
		value: 71.2,
		missing: false,
		asOf: result.dataStatus.lastDataAt
	});
	expect(result.detail.dailyRanges.map((range) => range.sampleCount)).toEqual([
		120, 180, 160, 200, 140, 240, 90
	]);
});

it('단일 기록·전체 결측·유효한 0과 완료일을 구분하며 공유 데이터를 변경하지 않는다', () => {
	const before = structuredClone(normalHeartSamples);
	const samples = normalHeartSamples.map((_, i) =>
		i === 2 ? { avg: 0, min: 0, max: 0, hours: 1 } : null
	);
	const single = responsiveHeartFixture('2026-11-10', samples);
	expect(single.summary).toMatchObject({
		avg: 0,
		completedDays: 6,
		daysWithData: 1,
		dailyMin: { date: '2026-11-06', value: 0 },
		dailyMax: { date: '2026-11-06', value: 0 }
	});
	expect(single.days[2]).toMatchObject({ value: 0, missing: false });
	const empty = responsiveHeartFixture(
		'2026-11-10',
		samples.map(() => null)
	);
	expect(empty.summary).toMatchObject({
		avg: null,
		dailyMin: null,
		dailyMax: null,
		completedDays: 6,
		daysWithData: 0
	});
	expect(empty.detail.measuredMin).toBeNull();
	expect(empty.detail.measuredMax).toBeNull();
	expect(empty.summary.missingDays).toHaveLength(6);
	expect(empty.summary.unsyncedDays).toEqual(['2026-11-10']);
	expect(normalHeartSamples).toEqual(before);
});
