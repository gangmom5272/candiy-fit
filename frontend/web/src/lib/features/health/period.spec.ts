import { expect, it } from 'vitest';
import { formatHealthWeek } from './period';
import { getSleepMockWeek } from './sleep-mock';
import { getHeartMockWeek } from './heart-mock';
import { getActivityPeriod } from './activity';

it('현재 주도 월요일부터 일요일까지 표시하고 실제 미래 날짜는 생성하지 않는다', () => {
	expect(formatHealthWeek('2026-10-26')).toBe('2026.10.26 ~ 11.01');
	expect(getActivityPeriod(1).label).toBe('2026.10.26 ~ 11.01');
	for (const days of [
		getActivityPeriod(1).days,
		getSleepMockWeek(0).sleep.days,
		getHeartMockWeek(0).days
	]) {
		expect(days.map((day) => day.date)).toEqual(['2026-10-26']);
	}
});
it('연도 경계와 오래된 주도 표기하고 없는 수치를 만들지 않는다', () => {
	expect(formatHealthWeek('2026-12-28')).toBe('2026.12.28 ~ 2027.01.03');
	const week = getSleepMockWeek(-20);
	expect(week.sleep.days).toHaveLength(7);
	expect(week.sleep.days.every((day) => day.value === null)).toBe(true);
	expect(week.sleep.days.every((day) => day.reason === 'BEFORE_LINK')).toBe(true);
	expect(week.sleep.summary.completedDays).toBe(0);
	expect(week.sleep.summary.missingDays).toEqual([]);
});
