import { describe, expect, it } from 'vitest';
import {
	formatHeartPeriod,
	heartChartDomain,
	heartMissingMessage,
	heartWeekDates,
	heartX,
	heartY
} from './heart';
import { groupHeartMissingDays } from './heart';

it('연속된 같은 사유만 묶고 정상값·날짜 공백·다른 사유를 구분한다', () => {
	const days = getHeartMockWeek(-1).days;
	const fixture = [
		{ ...days[0], date: '2026-10-29', missing: true, reason: 'UNSYNCED' as const },
		{ ...days[0], date: '2026-10-30', missing: true, reason: 'UNSYNCED' as const },
		{ ...days[0], date: '2026-10-31', missing: true, reason: 'NULL_VALUE' as const },
		{ ...days[0], date: '2026-11-01', missing: false, value: 0, reason: null },
		{ ...days[0], date: '2026-11-02', missing: true, reason: 'UNSYNCED' as const },
		{ ...days[0], date: '2026-11-04', missing: true, reason: 'UNSYNCED' as const }
	];
	const original = structuredClone(fixture);
	expect(groupHeartMissingDays(fixture)).toEqual([
		{ start: '2026-10-29', end: '2026-10-30', reason: 'UNSYNCED' },
		{ start: '2026-10-31', end: '2026-10-31', reason: 'NULL_VALUE' },
		{ start: '2026-11-02', end: '2026-11-02', reason: 'UNSYNCED' },
		{ start: '2026-11-04', end: '2026-11-04', reason: 'UNSYNCED' }
	]);
	expect(groupHeartMissingDays([])).toEqual([]);
	expect(fixture).toEqual(original);
});
import { getHeartMockWeek, HEART_MOCK_TODAY } from './heart-mock';

it('연속 7일의 점·범위·라벨은 요일 대신 조회 시작일 순서로 배치한다', () => {
	for (const start of ['2026-10-27', '2026-12-29']) {
		for (let index = 0; index < 7; index++) {
			const date = new Date(Date.parse(start) + index * 86400000).toISOString().slice(0, 10);
			expect(heartX(date, start)).toBeCloseTo(((index + 0.5) / 7) * 100);
		}
	}
});

describe('심박 주간 응답', () => {
	it('월~일 완료 주와 오늘까지만 포함하는 현재 주를 구분한다', () => {
		expect(heartWeekDates(HEART_MOCK_TODAY, -1)).toEqual([
			'2026-10-19',
			'2026-10-20',
			'2026-10-21',
			'2026-10-22',
			'2026-10-23',
			'2026-10-24',
			'2026-10-25'
		]);
		expect(heartWeekDates(HEART_MOCK_TODAY, 0)).toEqual(['2026-10-26']);
		expect(heartWeekDates('2026-11-01', 0)).toHaveLength(7);
		expect(formatHeartPeriod(getHeartMockWeek(-1).period)).toBe('2026.10.19 ~ 10.25');
		expect(formatHeartPeriod(getHeartMockWeek(0).period)).toBe('2026.10.26');
	});
	it('모의 서버는 시간대 수 가중 평균과 결측 의미를 계약대로 제공한다', () => {
		const result = getHeartMockWeek(-1);
		expect(result.summary.avg).toBe(75.68);
		expect(result.summary.daysUsedForSummary).toBe(6);
		expect(result.days[2]).toMatchObject({ value: null, missing: true, reason: 'NO_RECORD' });
		expect(result.detail.dailyRanges[2]).toEqual({ date: '2026-10-21', min: null, max: null });
		expect(result.detail.dailyRanges.map((range) => range.date)).toEqual(
			result.days.map((day) => day.date)
		);
		expect(result.inProgress).toBeNull();
	});
	it('오늘의 유효 기록을 차트에 보존하지만 기간 summary에는 넣지 않는다', () => {
		const result = getHeartMockWeek(0);
		expect(result.days[0]).toMatchObject({ value: 74.2, isInProgress: true, missing: false });
		expect(result.summary.avg).toBeNull();
		expect(result.summary.daysWithData).toBe(0);
		expect(result.summary.completedDays).toBe(0);
		expect(result.inProgress).toMatchObject({ value: 74.2, asOf: result.dataStatus.lastDataAt });
		expect(result.detail.dailyRanges[0]).toMatchObject({ min: 52, max: 118 });
	});
	it('빈 기간과 연동 전 날짜를 0으로 바꾸지 않는다', () => {
		const empty = getHeartMockWeek(-2);
		expect(empty.summary.avg).toBeNull();
		expect(empty.days.every((day) => day.value === null && day.missing)).toBe(true);
		expect(empty.days[0].reason).toBe('NO_RECORD');
		expect(getHeartMockWeek(-4).days[0].reason).toBe('BEFORE_LINK');
		expect(empty.days.at(-1)?.reason).toBe('NO_RECORD');
		expect(
			empty.detail.dailyRanges.every((range) => range.min === null && range.max === null)
		).toBe(true);
	});
});

describe('심박 표시 좌표', () => {
	it('기본 범위를 유지하며 실제 범위 밖 값과 summary까지 포함한다', () => {
		const result = getHeartMockWeek(-1);
		expect(heartChartDomain(result)).toEqual({
			min: 40,
			max: 160,
			ticks: [40, 60, 80, 100, 120, 140, 160]
		});
		result.detail.dailyRanges[0] = { date: result.days[0].date, min: 30, max: 212 };
		result.summary.avg = 250;
		const domain = heartChartDomain(result);
		expect(domain.min).toBeLessThanOrEqual(30);
		expect(domain.max).toBeGreaterThanOrEqual(250);
		for (const value of [30, 212, 250]) {
			expect(heartY(value, domain)).toBeGreaterThanOrEqual(0);
			expect(heartY(value, domain)).toBeLessThanOrEqual(100);
		}
	});
	it('완료 주와 현재 주는 데이터 수와 무관하게 월~일 7-slot 좌표를 사용한다', () => {
		const completed = getHeartMockWeek(-1);
		const current = getHeartMockWeek(0);
		expect(current.days).toHaveLength(1);
		expect(heartX(current.days[0].date)).toBeCloseTo(100 / 14);
		expect(heartX(current.days[0].date)).toBe(heartX(completed.days[0].date));
		for (const [index, day] of completed.days.entries()) {
			expect(heartX(day.date)).toBeCloseTo(((index + 0.5) / 7) * 100);
		}
		// 배열 일부만 표시해도 수요일은 세 번째 slot이다. 일요일도 다음 월요일과 구분한다.
		expect(heartX('2026-10-28')).toBeCloseTo((2.5 / 7) * 100);
		expect(heartX('2026-11-01')).toBeCloseTo((6.5 / 7) * 100);
		expect(heartX('2026-11-02')).toBeCloseTo(100 / 14);
		expect(heartY(100, { min: 40, max: 160 })).toBe(50);
	});
	it('서버의 결측 사유만 사용자 문구로 변환한다', () => {
		expect(heartMissingMessage('BEFORE_LINK')).toContain('연동 전');
		expect(heartMissingMessage('UNSYNCED')).toContain('앱을 열어 동기화');
		expect(heartMissingMessage('NO_RECORD')).toContain('기록이 없어요');
		expect(heartMissingMessage('NULL_VALUE')).toContain('값이 없어요');
	});
});
