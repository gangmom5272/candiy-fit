import { describe, expect, it } from 'vitest';
import {
	exerciseSessions,
	getActivityPeriod,
	getExerciseSummary,
	getHourlyStepAverages,
	getHourlyStepSummary,
	getWeekdayAverages,
	formatAverageSteps,
	formatCalories,
	groupActivityMissingDays,
	hourlyStepDays,
	recentStepRecords,
	type ExerciseSession
} from './activity';

it.each([
	[100, '100 kcal'],
	[123.45000000000002, '123.5 kcal'],
	[85.04, '85 kcal'],
	[85.06, '85.1 kcal'],
	[0.05, '0.1 kcal'],
	[0.01, '0 kcal'],
	[0, '0 kcal'],
	[1234.56, '1,234.6 kcal']
])('칼로리 %s의 표시만 최대 한 자리로 반올림한다', (value, expected) => {
	expect(formatCalories(value)).toBe(expected);
});

it('소수 칼로리 합산과 추이는 보존하고 총합·최고 표시만 반올림한다', () => {
	const period = getActivityPeriod(0);
	const values = [0.1, 0.2, 123.45000000000002];
	const sessions = values.map((caloriesKcal, index) => ({
		...exerciseSessions[0],
		id: `decimal-${index}`,
		caloriesKcal
	}));
	const total = values.reduce((sum, value) => sum + value, 0);
	expect(getExerciseSummary(period, sessions).metrics[1]).toMatchObject({
		value: `평균 ${Math.round(total)} kcal`,
		description: '최고 123.8 kcal',
		detail: '총 123.8 kcal',
		trend: [total, null, null, null, null, null, null]
	});
	expect(sessions.map((session) => session.caloriesKcal)).toEqual(values);
});

it('걸음 평균 표시는 정수로 반올림하며 원본 숫자는 유지한다', () => {
	const value = 5291.67;
	expect(formatAverageSteps(value)).toBe('5,292');
	expect(formatAverageSteps(0)).toBe('0');
	expect(value).toBe(5291.67);
});

it('연속된 같은 사유만 묶고 다른 사유·정상 0·날짜 공백은 구분한다', () => {
	const days = [
		{ date: '2026-10-27', missing: true, reason: 'UNSYNCED' },
		{ date: '2026-10-28', missing: true, reason: 'UNSYNCED' },
		{ date: '2026-10-29', missing: true, reason: 'NO_RECORD' },
		{ date: '2026-10-30', missing: true, reason: 'UNSYNCED' },
		{ date: '2026-10-31', missing: false, reason: null, value: 0 },
		{ date: '2026-11-01', missing: true, reason: 'UNSYNCED' },
		{ date: '2026-11-02', missing: true, reason: 'NULL_VALUE' }
	] as const;
	expect(groupActivityMissingDays(days)).toEqual([
		{ start: '2026-10-27', end: '2026-10-28', reason: 'UNSYNCED' },
		{ start: '2026-10-29', end: '2026-10-29', reason: 'NO_RECORD' },
		{ start: '2026-10-30', end: '2026-10-30', reason: 'UNSYNCED' },
		{ start: '2026-11-01', end: '2026-11-01', reason: 'UNSYNCED' },
		{ start: '2026-11-02', end: '2026-11-02', reason: 'NULL_VALUE' }
	]);
});

describe('API 운동 인정 여부와 집계', () => {
	const session = (patch: Partial<ExerciseSession>): ExerciseSession => ({
		...exerciseSessions[0],
		...patch
	});
	it('운동 최고는 단일 세션이 아닌 날짜별 인정 시간 합계이며 칼로리와 기준이 같다', () => {
		const period = getActivityPeriod(0);
		const sessions = [
			session({ id: 'a', durationMinutes: 20, caloriesKcal: 100 }),
			session({ id: 'b', durationMinutes: 25, caloriesKcal: 150 }),
			session({
				id: 'c',
				date: period.days[1].date,
				durationMinutes: 40,
				caloriesKcal: 200
			}),
			session({ id: 'excluded', durationMinutes: 999, countsAsExercise: false }),
			session({ id: 'outside', date: '2026-10-18', durationMinutes: 999 })
		];
		const result = getExerciseSummary(period, sessions);
		expect(result.metrics[0]).toMatchObject({
			value: '평균 43분',
			average: 42.5,
			detail: '총 1시간 25분',
			description: '최고 45분',
			trend: [45, 40, null, null, null, null, null]
		});
		expect(result.metrics[1]).toMatchObject({
			value: '평균 225 kcal',
			detail: '총 450 kcal',
			description: '최고 250 kcal'
		});
		expect(sessions.map((item) => item.durationMinutes)).toEqual([20, 25, 40, 999, 999]);
	});
	it('서버 총합·평균은 유지하고 최고는 진행 중 날짜를 포함한 차트 값으로 표시만 반올림한다', () => {
		const values = [20, 40, null, null, null, null, 60.1];
		const result = getExerciseSummary(getActivityPeriod(0), [session({ durationMinutes: 999 })], {
			avg: 30,
			total: 60,
			values
		});
		expect(result.metrics[0]).toMatchObject({
			value: '평균 30분',
			average: 30,
			detail: '총 1시간 0분',
			description: '최고 1시간 0분',
			trend: [20, 40, null, null, null, null, 60.1]
		});
		expect(values).toEqual([20, 40, null, null, null, null, 60.1]);
	});
	it.each([
		[59.5, '최고 1시간 0분'],
		[60.6, '최고 1시간 1분'],
		[0.4, '최고 0분']
	])('소수 운동 시간 %s는 최고 표시만 분 단위로 반올림한다', (value, description) => {
		const metric = getExerciseSummary(getActivityPeriod(0), [session({ durationMinutes: value })])
			.metrics[0];
		expect(metric.description).toBe(description);
		expect(metric.trend).toEqual([value, null, null, null, null, null, null]);
	});
	it('심박 0의 요약·최고값·원본 추이와 혼합 평균을 보존한다', () => {
		const zero = session({ avgHeartRate: 0, maxHeartRate: 0, caloriesKcal: 0 });
		const result = getExerciseSummary(getActivityPeriod(0), [zero]);
		expect(result.metrics[2]).toMatchObject({
			value: '평균 0 bpm',
			description: '최고 0 bpm',
			trend: [0, null, null, null, null, null, null],
			hasRecords: true
		});
		expect(result.metrics[1].value).toBe('평균 0 kcal');
		const mixed = getExerciseSummary(getActivityPeriod(0), [
			zero,
			session({ date: '2026-10-20', avgHeartRate: 72, maxHeartRate: 84 })
		]);
		expect(mixed.metrics[2]).toMatchObject({
			value: '평균 36 bpm',
			description: '최고 84 bpm',
			trend: [0, 72, null, null, null, null, null]
		});
		expect(zero.avgHeartRate).toBe(0);
		const roundedZero = getExerciseSummary(getActivityPeriod(0), [
			session({ avgHeartRate: 0.2, maxHeartRate: 1 })
		]);
		expect(roundedZero.metrics[2]).toMatchObject({
			value: '평균 0 bpm',
			trend: [0.2, null, null, null, null, null, null]
		});
		const missing = getExerciseSummary(getActivityPeriod(0), [
			session({ avgHeartRate: null, maxHeartRate: null })
		]);
		expect(missing.metrics[2]).toMatchObject({
			value: '기록 없음',
			description: '최고 심박수 기록 없음',
			hasRecords: false
		});
	});
	it('속도와 무관하게 인정된 세션만 시간·횟수·종류·추이에 포함한다', () => {
		const result = getExerciseSummary(getActivityPeriod(0), [
			session({ id: 'recognized-low-speed', durationMinutes: 10, avgSpeedMps: 0.1 }),
			session({
				id: 'excluded-high-speed',
				date: '2026-10-20',
				durationMinutes: 99,
				countsAsExercise: false,
				avgSpeedMps: 10,
				typeName: '달리기',
				caloriesKcal: 9999,
				avgHeartRate: 250,
				maxHeartRate: 300
			}),
			session({
				id: 'recognized-null-speed',
				date: '2026-10-21',
				durationMinutes: 20,
				avgSpeedMps: null
			})
		]);
		expect(result.metrics[0]).toMatchObject({
			value: '평균 10분',
			detail: '총 30분',
			trend: [10, 0, 20, null, null, null, null]
		});
		expect(result.types).toEqual([{ label: '걷기', count: '2회', duration: '30분' }]);
		expect(result.metrics[1]).toMatchObject({
			description: '최고 340 kcal',
			detail: '총 680 kcal'
		});
		expect(result.metrics[2].value).toBe('평균 120 bpm');
	});
	it('미인정 걷기만 있는 날은 기록 있는 0분이고 세션은 보존한다', () => {
		const excluded = session({ countsAsExercise: false, avgSpeedMps: null });
		const result = getExerciseSummary(getActivityPeriod(0), [excluded]);
		expect(result.hasRecords).toBe(true);
		expect(result.metrics[0]).toMatchObject({
			hasRecords: true,
			value: '평균 0분',
			detail: '총 0분',
			description: '최고 0분',
			trend: [0, null, null, null, null, null, null]
		});
		expect(result.types).toEqual([]);
		expect(result.excludedWalks).toEqual([excluded]);
		expect(getExerciseSummary(getActivityPeriod(0), []).metrics[0]).toMatchObject({
			hasRecords: false,
			value: '기록 없음',
			description: '—',
			trend: Array(7).fill(null)
		});
	});
	it('mock 운동 시간은 인정 세션과 유효한 0분 날짜를 기준으로 일치한다', () => {
		const summary = getExerciseSummary(getActivityPeriod(0), exerciseSessions);
		expect(summary.metrics[0]).toMatchObject({ value: '평균 26분', detail: '총 2시간 59분' });
		expect(summary.types).toHaveLength(4);
		expect(summary.types[0]).toEqual({ label: '걷기', count: '2회', duration: '1시간 42분' });
		expect(summary.excludedWalks).toHaveLength(4);
		expect(getExerciseSummary(getActivityPeriod(1), exerciseSessions).hasRecords).toBe(false);
	});
});

describe('최근 4주 요일별 평균', () => {
	it('실제로 제공한 mock 기록만 사용하고 없는 이전 주의 값을 만들지 않는다', () => {
		expect(recentStepRecords).toHaveLength(7);
		expect(recentStepRecords[0].date).toBe('2026-10-19');
		expect(recentStepRecords.at(-1)?.date).toBe('2026-10-25');
		expect(getWeekdayAverages(recentStepRecords)).toEqual([
			8120,
			7680,
			null,
			9240,
			8560,
			5200,
			7760
		]);
	});
	it('최근 28일의 같은 요일 네 표본만 사용한다', () => {
		const records = [
			'2026-09-28',
			'2026-10-05',
			'2026-10-12',
			'2026-10-19',
			'2026-10-26',
			'2026-11-02'
		].map((date, index) => ({ date, value: index * 100, missing: false }));
		expect(getWeekdayAverages(records)).toEqual([250, null, null, null, null, null, null]);
	});
	it('missing·null·결측인 0보는 분모에서 빼고 빈 요일을 0으로 만들지 않는다', () => {
		expect(
			getWeekdayAverages([
				{ date: '2026-10-05', value: 100, missing: false },
				{ date: '2026-10-12', value: 999, missing: true },
				{ date: '2026-10-19', value: null, missing: true },
				{ date: '2026-10-26', value: 0, missing: true }
			])
		).toEqual([100, null, null, null, null, null, null]);
		expect(getWeekdayAverages([])).toEqual(Array(7).fill(null));
	});
});

describe('시간대별 평균과 요약', () => {
	it('24칸이 아닌 배열은 유효한 하루로 평균에 포함하지 않는다', () => {
		expect(
			getHourlyStepAverages([
				{ date: '2026-10-20', hourlySteps: [] },
				{ date: '2026-10-21', hourlySteps: Array(23).fill(999) },
				{ date: '2026-10-22', hourlySteps: Array(25).fill(999) }
			])
		).toBeNull();
	});
	it('null 하루는 제외하고 유효 배열의 0은 평균에 포함한다', () => {
		const first = Array.from({ length: 24 }, (_, hour) => (hour === 0 ? 0 : 100));
		const second = Array(24).fill(200);
		const values = getHourlyStepAverages([
			{ date: '2026-10-20', hourlySteps: first },
			{ date: '2026-10-21', hourlySteps: second },
			{ date: '2026-10-22', hourlySteps: null }
		]);
		expect(values).toEqual([100, ...Array(23).fill(150)]);
		expect(getHourlyStepSummary(values)).toEqual({
			average: 3550 / 24,
			min: { hour: 0, value: 100 },
			max: { hour: 1, value: 150 }
		});
	});
	it('데이터 없음과 실제 24시간의 0을 구분한다', () => {
		expect(getHourlyStepAverages([{ date: '2026-10-22', hourlySteps: null }])).toBeNull();
		expect(getHourlyStepSummary(null)).toBeNull();
		const zero = getHourlyStepAverages([{ date: '2026-10-20', hourlySteps: Array(24).fill(0) }]);
		expect(zero).toEqual(Array(24).fill(0));
		expect(getHourlyStepSummary(zero)).toMatchObject({
			average: 0,
			min: { value: 0 },
			max: { value: 0 }
		});
	});
	it('합성 시간대 mock은 유효한 날의 걸음 수 합계와 일치한다', () => {
		const days = getActivityPeriod(0).days;
		for (const [index, record] of hourlyStepDays.entries()) {
			if (record.hourlySteps === null) expect(days[index].steps).toBeNull();
			else {
				expect(record.hourlySteps).toHaveLength(24);
				expect(record.hourlySteps.reduce((sum, value) => sum + value, 0)).toBe(days[index].steps);
			}
		}
	});
});
