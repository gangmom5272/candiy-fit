import { expect, it } from 'vitest';
import { getSleepMockWeek } from './sleep-mock';
import { getHeartMockWeek } from './heart-mock';
import { getDayDetailMock } from './day-detail-mock';
import { formatDayMetric } from './day-detail';
import { sleepMissingText } from './sleep-detail';
import { heartMissingMessage } from './heart';
import { healthSummaries } from './summary';

it.each([
	['2026-09-30', -4, 'BEFORE_LINK', '연동 전'],
	['2026-10-01', -4, 'NO_RECORD', '기록 없음'],
	['2026-10-07', -3, 'NO_RECORD', '기록 없음']
] as const)(
	'%s의 연동 상태를 지표와 하루 상세에서 동일하게 표현한다',
	(date, offset, reason, text) => {
		const week = getSleepMockWeek(offset);
		const heart = getHeartMockWeek(offset);
		const detail = getDayDetailMock(date);
		for (const result of [week.sleep, week.oxygen, heart]) {
			expect(result.summary.linkedFrom).toBe('2026-10-01');
			expect(result.days.find((day) => day.date === date)).toMatchObject({
				value: null,
				missing: true,
				reason,
				isInProgress: false
			});
			if (reason === 'BEFORE_LINK') expect(result.summary.missingDays).not.toContain(date);
			else expect(result.summary.missingDays).toContain(date);
		}
		for (const day of [detail.steps, detail.exercise, detail.sleep, detail.heart]) {
			expect(day).toMatchObject({ value: null, missing: true, reason, isInProgress: false });
			expect(formatDayMetric(day, 'min')).toBe(text);
		}
		expect(sleepMissingText(week.sleep.days.find((day) => day.date === date)?.reason)).toBe(text);
		expect(heartMissingMessage(reason)).toContain(
			reason === 'BEFORE_LINK' ? '연동 전' : '심박 기록이 없어요'
		);
	}
);

it('오늘 수면의 수집 상태와 완료 여부는 별개이며 유효 SpO2는 오늘 summary에 포함한다', () => {
	const { sleep, oxygen } = getSleepMockWeek(0);
	expect(sleep.days[0]).toMatchObject({ value: null, reason: 'UNSYNCED', isInProgress: false });
	expect(sleep.summary.unsyncedDays).toEqual(['2026-10-26']);
	expect(sleep.summary.missingDays).toEqual([]);
	expect(sleepMissingText(sleep.days[0].reason)).toBe('동기화 대기');
	expect(getDayDetailMock('2026-10-26').sleep).toEqual(sleep.days[0]);
	expect(oxygen.days[0]).toMatchObject({
		value: 95.8,
		missing: false,
		reason: null,
		isInProgress: false
	});
	expect(oxygen.summary.avg).toBe(95.8);
	expect(oxygen.summary.daysUsedForSummary).toBe(1);
	for (const result of [sleep, oxygen]) {
		expect(result.inProgress).toBeNull();
		expect(result.summary.completedDays).toBe(1);
	}
	expect(getHeartMockWeek(0).days[0].isInProgress).toBe(true);
});

it('동기화 완료일의 원천 행 유무를 구분하고 Home·Health의 기존 수치를 보존한다', () => {
	const { oxygen } = getSleepMockWeek(-1);
	expect(oxygen.days.find((day) => day.date === '2026-10-24')?.reason).toBe('NULL_VALUE');
	expect(sleepMissingText('NULL_VALUE')).toBe('값 없음');
	expect(oxygen.days.find((day) => day.date === '2026-10-22')?.reason).toBe('NO_RECORD');
	expect(healthSummaries.map((summary) => summary.value)).toEqual([
		'평균 7,760보',
		'평균 26분',
		'평균 6시간 29분',
		'평균 75.68 bpm'
	]);
});
