import { describe, expect, it } from 'vitest';
import {
	activityReferenceDate,
	exerciseSessions,
	formatActivityChange,
	formatExerciseHeartRate,
	getActivityPeriod,
	getExerciseSummary,
	weekdayOrder
} from './activity';

describe('활동 숫자 표시 정밀도', () => {
	it.each([
		[70.30000000000001, '70.3 bpm'],
		[72.55, '72.55 bpm'],
		[72.55555, '72.55555 bpm'],
		[72.1234567890123, '72.1234567890123 bpm'],
		[70, '70 bpm'],
		[0, '0 bpm']
	])('운동 심박 %s의 표시만 %s로 정리한다', (value, label) => {
		expect(formatExerciseHeartRate(value)).toBe(label);
	});

	it('여러 세션의 원본 심박 평균과 null은 표시와 독립적으로 유지한다', () => {
		const period = getActivityPeriod(0);
		const sessions = [70.2, 70.4, null].map((avgHeartRate, index) => ({
			...exerciseSessions[0],
			id: `precision-${index}`,
			avgHeartRate
		}));
		const heart = getExerciseSummary(period, sessions).metrics.find(
			(metric) => metric.kind === 'heart'
		);
		expect(heart?.trend[0]).toBe(70.30000000000001);
		expect(heart?.trend[1]).toBeNull();
		expect(heart?.value).toBe('평균 70 bpm');
		expect(sessions.map((session) => session.avgHeartRate)).toEqual([70.2, 70.4, null]);
	});

	it.each([
		[3000, 3001, '0.0%'],
		[3001, 3000, '0.0%'],
		[3000, 3000, '0.0%'],
		[90, 100, '-10.0%'],
		[110, 100, '+10.0%']
	])('현재 %s·이전 %s의 증감률을 %s로 표시한다', (current, previous, label) => {
		const change = ((current - previous) / previous) * 100;
		expect(formatActivityChange(change)).toBe(label);
	});

	it.each([
		[-0.049, '0.0%'],
		[0.049, '0.0%'],
		[-0.05, '-0.1%'],
		[0.05, '+0.1%'],
		[-0, '0.0%'],
		[null, '—']
	])('증감률 경계 %s를 기존 한 자리 정책으로 %s 표시한다', (value, label) => {
		expect(formatActivityChange(value)).toBe(label);
	});
});

describe('활동 기간 퍼블리싱 데이터', () => {
	it('기준일 직전 완료된 월요일부터 일요일까지의 7일을 표시한다', () => {
		const period = getActivityPeriod(0);
		expect(activityReferenceDate).toBe('2026-10-26');
		expect(period.label).toBe('2026.10.19 ~ 10.25');
		expect(period.days.map(({ steps }) => steps)).toEqual([
			8120,
			7680,
			null,
			9240,
			8560,
			5200,
			7760
		]);
		expect(period.days.map(({ weekday }) => weekday)).toEqual([
			'월',
			'화',
			'수',
			'목',
			'금',
			'토',
			'일'
		]);
		expect(period.days[0]).toMatchObject({ date: '2026-10-19', weekday: '월' });
		expect(period.days.at(-1)).toMatchObject({ date: '2026-10-25', weekday: '일' });
		expect(weekdayOrder).toEqual(['월', '화', '수', '목', '금', '토', '일']);
		const recorded = period.days.flatMap(({ steps }) => (steps === null ? [] : [steps]));
		expect(recorded.reduce((sum, value) => sum + value, 0) / recorded.length).toBe(7760);
	});

	it.each([[-1, '2026.10.12 ~ 10.18', '2026-10-12', '2026-10-18']])(
		'%i주 이동하면 날짜만 변경하고 없는 측정값은 null로 둔다',
		(offset, label, first, last) => {
			const period = getActivityPeriod(offset);
			expect(period.label).toBe(label);
			expect(period.hasRecords).toBe(false);
			expect(period.days).toHaveLength(7);
			expect(period.days[0].date).toBe(first);
			expect(period.days[6].date).toBe(last);
			expect(period.days.every(({ steps }) => steps === null)).toBe(true);
		}
	);
	it('현재 주와 범위를 벗어난 미래 요청에서도 미래 날짜를 생성하지 않는다', () => {
		for (const offset of [1, 10]) {
			const period = getActivityPeriod(offset);
			expect(period.days.map((day) => day.date)).toEqual(['2026-10-26']);
			expect(period.days[0].steps).toBeNull();
		}
	});

	it('다른 기간을 조회해도 기준 주 데이터가 바뀌지 않는다', () => {
		getActivityPeriod(-1);
		getActivityPeriod(13);
		expect(getActivityPeriod(0).days.filter(({ steps }) => steps !== null)).toHaveLength(6);
		expect(getActivityPeriod(0).hasRecords).toBe(true);
	});
});
