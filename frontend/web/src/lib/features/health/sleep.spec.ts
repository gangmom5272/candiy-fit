import { describe, expect, it } from 'vitest';
import {
	averageSleepClock,
	formatSleepMinutes,
	formatSleepPeriod,
	groupSleepMissingDays,
	type SleepMetricResult,
	oxygenSegments,
	oxygenDomain,
	oxygenY,
	sleepAverageDelta,
	sleepClock,
	sleepInterval
} from './sleep';

describe('수면 날짜별 결측 안내', () => {
	const day = (
		date: string,
		reason: SleepMetricResult['days'][number]['reason'],
		missing = true
	): SleepMetricResult['days'][number] => ({
		date,
		reason,
		missing,
		value: null,
		isInProgress: false
	});
	it('안내 0건·1건과 정상 0·null 값은 missing 플래그로 구분한다', () => {
		expect(groupSleepMissingDays([])).toEqual([]);
		expect(
			groupSleepMissingDays([
				{ ...day('2026-10-28', null, false), value: 0 },
				day('2026-10-29', null, false)
			])
		).toEqual([]);
		expect(groupSleepMissingDays([day('2026-10-29', 'UNSYNCED')])).toEqual([
			{ start: '2026-10-29', end: '2026-10-29', reason: 'UNSYNCED' }
		]);
	});
	it('월·연도 경계의 동일 사유 연속 날짜만 묶고 원본을 유지한다', () => {
		const days = [
			day('2026-12-31', 'UNSYNCED'),
			day('2027-01-01', 'UNSYNCED'),
			day('2027-01-02', 'UNSYNCED')
		];
		const before = structuredClone(days);
		const groups = groupSleepMissingDays(days);
		expect(groups).toEqual([{ start: '2026-12-31', end: '2027-01-02', reason: 'UNSYNCED' }]);
		expect(formatSleepPeriod(groups[0])).toBe('2026.12.31 ~ 2027.01.02');
		expect(days).toEqual(before);
	});
	it('정상 날짜·날짜 공백·다른 사유와 사유 없는 결측을 구분한다', () => {
		expect(
			groupSleepMissingDays([
				day('2026-10-28', 'UNSYNCED'),
				day('2026-10-29', null, false),
				day('2026-10-30', 'UNSYNCED'),
				day('2026-11-01', 'UNSYNCED'),
				day('2026-11-02', 'NO_RECORD'),
				day('2026-11-03', 'NULL_VALUE'),
				day('2026-11-04', 'BEFORE_LINK'),
				day('2026-11-05', null)
			])
		).toEqual([
			{ start: '2026-10-28', end: '2026-10-28', reason: 'UNSYNCED' },
			{ start: '2026-10-30', end: '2026-10-30', reason: 'UNSYNCED' },
			{ start: '2026-11-01', end: '2026-11-01', reason: 'UNSYNCED' },
			{ start: '2026-11-02', end: '2026-11-02', reason: 'NO_RECORD' },
			{ start: '2026-11-03', end: '2026-11-03', reason: 'NULL_VALUE' },
			{ start: '2026-11-04', end: '2026-11-04', reason: 'BEFORE_LINK' },
			{ start: '2026-11-05', end: '2026-11-05', reason: null }
		]);
	});
});

it('산소포화도 기본 범위 밖의 값도 domain을 넓혀 표시하고 단일 값과 결측을 보존한다', () => {
	const day = { date: '2026-10-26', value: 82, missing: false, reason: null, isInProgress: true };
	const domain = oxygenDomain([day]);
	expect(domain.min).toBeLessThanOrEqual(82);
	expect(oxygenY(82, domain)).toBeGreaterThanOrEqual(16);
	expect(oxygenY(82, domain)).toBeLessThanOrEqual(104);
	expect(
		oxygenSegments([day, { ...day, date: '2026-10-27', value: null, missing: true }])
	).toHaveLength(1);
});
import {
	currentSleepResult,
	currentOxygenResult,
	completedSleepResult,
	completedOxygenResult,
	previousSleepResult,
	sleepMockWeeks
} from './sleep-mock';

it('수면·산소포화도는 오늘도 완료 값으로 취급하고 미래 날짜는 만들지 않는다', () => {
	for (const result of [currentSleepResult, currentOxygenResult]) {
		expect(result.days).toHaveLength(1);
		expect(result.days[0]).toMatchObject({ date: '2026-10-26', isInProgress: false });
		expect(result.inProgress).toBeNull();
	}
});

describe('수면 계약과 계산', () => {
	it('UTC 시각을 KST로 표시하고 자정 양쪽 시각의 평균을 구한다', () => {
		expect(sleepClock('2026-10-20T14:50:00Z')).toBe('23:50');
		expect(averageSleepClock(['2026-10-20T14:50:00Z', '2026-10-20T15:10:00Z'])).toBe('00:00');
		expect(averageSleepClock(['2026-10-20T22:20:00Z', '2026-10-21T22:40:00Z'])).toBe('07:30');
		expect(averageSleepClock([])).toBeNull();
	});
	it('유효한 수면 분만 통계에 포함하고 세션 길이로 대체하지 않는다', () => {
		const valid = completedSleepResult.detail.nights.flatMap((n) =>
			n.sleepMinutes === null ? [] : [n.sleepMinutes]
		);
		expect(completedSleepResult.summary.avg).toBe(
			Math.round((valid.reduce((a, b) => a + b, 0) / valid.length) * 100) / 100
		);
		expect(completedSleepResult.summary.daysWithData).toBe(6);
		const unknown = currentSleepResult.detail.nights.find((n) => n.sleepMinutes === null);
		expect(unknown).toBeDefined();
		if (!unknown) throw new Error('null fixture 없음');
		expect(sleepInterval(unknown).end - sleepInterval(unknown).start).toBe(444);
		expect(formatSleepMinutes(unknown.sleepMinutes)).toBe('기록 없음');
		expect(completedSleepResult.days[3].reason).toBe('NO_RECORD');
		expect(currentSleepResult.days[0].reason).toBe('UNSYNCED');
	});
	it('실제 날짜를 사용해 자정과 22시 이전, 08시 이후 구간을 보존한다', () => {
		const nights = [...completedSleepResult.detail.nights, ...currentSleepResult.detail.nights];
		expect(sleepInterval(nights[3])).toEqual({ start: 1430, end: 1888 });
		expect(sleepInterval(nights[5]).start).toBe(1292);
		expect(sleepInterval(nights[6]).end).toBe(1936);
		for (const night of nights) {
			const endKst = new Date(Date.parse(night.endAt) + 9 * 3600000).toISOString().slice(0, 10);
			expect(night.date).toBe(endKst);
		}
	});
	it('각 기간 평균 차이를 비교하고 데이터 없는 비교를 만들지 않는다', () => {
		expect(
			sleepAverageDelta(completedSleepResult.summary.avg, previousSleepResult.summary.avg)
		).toBe(17);
		expect(sleepAverageDelta(390, null)).toBeNull();
		expect(sleepAverageDelta(null, 390)).toBeNull();
		expect(
			sleepAverageDelta(currentSleepResult.summary.avg, completedSleepResult.summary.avg)
		).toBeNull();
		expect(formatSleepMinutes(null)).toBe('기록 없음');
	});
});

describe('주 수면 산소포화도', () => {
	it('일별 평균의 단순 평균과 실제 측정 최저를 구분한다', () => {
		expect(completedOxygenResult.summary.avg).toBe(96.25);
		expect(completedOxygenResult.summary.dailyMin?.value).toBe(95.4);
		expect(completedOxygenResult.detail.measuredMin?.value).toBe(93.4);
		expect(currentOxygenResult.summary.avg).toBe(95.8);
	});
	it('결측일에 점을 만들거나 선을 연결하지 않는다', () => {
		const segments = oxygenSegments(completedOxygenResult.days);
		expect(segments).toHaveLength(3);
		expect(segments.map((s) => s.split(' ').length)).toEqual([2, 1, 1]);
		expect(oxygenSegments([])).toEqual([]);
		expect(completedOxygenResult.days[3].reason).toBe('NO_RECORD');
		expect(completedOxygenResult.days[5].reason).toBe('NULL_VALUE');
	});
});

describe('월요일 시작 주간', () => {
	it('완료 주는 월~일이며 현재 주는 오늘까지만 포함한다', () => {
		expect(completedSleepResult.period).toEqual({ start: '2026-10-19', end: '2026-10-25' });
		expect(
			completedSleepResult.days.map((day) => new Date(`${day.date}T00:00:00Z`).getUTCDay())
		).toEqual([1, 2, 3, 4, 5, 6, 0]);
		expect(currentSleepResult.period).toEqual({ start: '2026-10-26', end: '2026-10-26' });
		expect(currentSleepResult.days.map((day) => day.date)).toEqual(['2026-10-26']);
		expect(currentSleepResult.summary.completedDays).toBe(1);
		expect(formatSleepPeriod(currentSleepResult.period)).toBe('2026.10.26');
		for (const week of sleepMockWeeks) {
			expect(week.oxygen.period).toEqual(week.sleep.period);
			expect(week.sleep.days.every((day) => day.date <= '2026-10-26')).toBe(true);
		}
	});
	it('기존 날짜의 기록을 다른 날짜로 옮기지 않는다', () => {
		expect(completedSleepResult.days.find((d) => d.date === '2026-10-19')?.value).toBe(376);
		expect(completedSleepResult.days.find((d) => d.date === '2026-10-20')?.value).toBe(399);
		expect(completedSleepResult.detail.nights.find((n) => n.date === '2026-10-25')?.startAt).toBe(
			'2026-10-24T12:32:00.000Z'
		);
		expect(currentSleepResult.detail.nights[0].sleepMinutes).toBeNull();
		expect(currentSleepResult.detail.nights[0].startAt).toBe('2026-10-25T15:52:00.000Z');
	});
});
