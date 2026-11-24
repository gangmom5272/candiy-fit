import { expect, it } from 'vitest';
import { healthSummaries, summaryDates } from './summary';
import { getActivityPeriod } from './activity';
import { completedSleepResult } from './sleep-mock';
import { getHeartMockWeek } from './heart-mock';

it('차트 테스트용 요약은 기존 지표 fixture의 직전 완료 주와 같은 날짜·값을 유지한다', () => {
	expect(summaryDates).toEqual(['10.19', '10.20', '10.21', '10.22', '10.23', '10.24', '10.25']);
	expect(healthSummaries[0].values).toEqual(getActivityPeriod(0).days.map((day) => day.steps));
	expect(healthSummaries[2].average).toBe(completedSleepResult.summary.avg);
	expect(healthSummaries[3].average).toBe(getHeartMockWeek(-1).summary.avg);
	expect(healthSummaries[1].hash).toBe('#exercise');
});
