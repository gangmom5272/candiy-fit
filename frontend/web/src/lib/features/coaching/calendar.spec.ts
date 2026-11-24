import { describe, expect, it } from 'vitest';
import {
	calendarAverage,
	calendarExtremes,
	chartExtremes,
	CALENDAR_MIN_WEEKS,
	weeksInMonth,
	calendarMonths,
	calendarStart,
	calendarWeeks,
	compareToAverage,
	dailyRangeOf,
	donutDash,
	donutFill,
	hasLowSpo2,
	nightSpo2Items,
	spo2LowState
} from './evidence-view';
import {
	evidenceCases,
	overlappingSleepEvidence,
	sleepAndStepsEvidence,
	sleepNightsSpo2,
	sleepSpo2Month,
	spo2Month
} from './fixtures';
import { formatMetricValue } from './format';
import { groupEvidenceByMetric, isMergedGroup } from './evidence';
import type { EvidenceSnapshot } from './types';

/**
 * 날짜별 기록 달력 계산의 전수·경계 검사(S15P31A403-430).
 * 달 나누기는 2000~2099년 모든 달을, 도넛은 여러 값 조합을, 실제 근거 모양은 모든 예시 데이터를 훑는다
 */

type Day = EvidenceSnapshot['days'][number];

const day = (date: string, value: number | null = 100, extra: Partial<Day> = {}): Day => ({
	date,
	value,
	missing: value === null,
	reason: value === null ? 'NO_RECORD' : null,
	is_in_progress: false,
	...extra
});

/** 비교 기준: 그 달의 날 수와 1일의 요일을 Date 로 따로 구한다 */
function reference(month: string) {
	const [year, monthNumber] = month.split('-').map(Number);
	return {
		length: new Date(Date.UTC(year, monthNumber, 0)).getUTCDate(),
		firstWeekday: new Date(Date.UTC(year, monthNumber - 1, 1)).getUTCDay()
	};
}

function nextDate(date: string): string {
	const next = new Date(`${date}T00:00:00Z`);
	next.setUTCDate(next.getUTCDate() + 1);
	return next.toISOString().slice(0, 10);
}

/** start 부터 count 일 */
function range(start: string, count: number, value: (index: number) => number | null = () => 100) {
	const days: Day[] = [];
	for (let index = 0, date = start; index < count; index += 1, date = nextDate(date))
		days.push(day(date, value(index)));
	return days;
}

describe('달 나누기: 2000~2099년 모든 달', () => {
	const months: string[] = [];
	for (let year = 2000; year < 2100; year += 1)
		for (let monthNumber = 1; monthNumber <= 12; monthNumber += 1)
			months.push(`${year}-${String(monthNumber).padStart(2, '0')}`);

	it('주는 일요일에 시작해 7칸이고, 그 달의 날을 빠짐없이 한 번씩, 날짜가 하루씩 이어진다', () => {
		for (const month of months) {
			const { length, firstWeekday } = reference(month);
			const weeks = calendarWeeks(month, []);
			const cells = weeks.flat();
			// 기본 5주, 6주가 필요한 달만 6주(S15P31A403-438 피드백)
			expect(weeks.length, month).toBe(
				Math.max(CALENDAR_MIN_WEEKS, Math.ceil((firstWeekday + length) / 7))
			);
			for (const week of weeks) {
				expect(week, month).toHaveLength(7);
				expect(week.map((cell) => cell.weekday)).toEqual([0, 1, 2, 3, 4, 5, 6]);
			}
			for (let index = 1; index < cells.length; index += 1)
				expect(cells[index].date, month).toBe(nextDate(cells[index - 1].date));
			const inside = cells.filter((cell) => cell.inMonth);
			expect(
				inside.map((cell) => cell.day),
				month
			).toEqual(Array.from({ length }, (_, index) => index + 1));
			expect(inside.every((cell) => cell.date.startsWith(month))).toBe(true);
			expect(
				cells.filter((cell) => !cell.inMonth).every((cell) => !cell.date.startsWith(month))
			).toBe(true);
			// 요일은 Date 가 구한 것과 같다
			expect(inside[0].weekday, month).toBe(firstWeekday);
			// 첫 주 앞은 6칸을 넘지 않고, 전체는 35칸 또는 42칸이다
			expect(cells.findIndex((cell) => cell.inMonth)).toBeLessThanOrEqual(6);
			expect([35, 42]).toContain(cells.length);
		}
	});

	it('윤년 2월과 해가 바뀌는 12월을 바르게 나눈다', () => {
		expect(
			calendarWeeks('2028-02', [])
				.flat()
				.filter((cell) => cell.inMonth)
		).toHaveLength(29);
		expect(
			calendarWeeks('2026-02', [])
				.flat()
				.filter((cell) => cell.inMonth)
		).toHaveLength(28);
		expect(
			calendarWeeks('2100-02', [])
				.flat()
				.filter((cell) => cell.inMonth)
		).toHaveLength(28);
		const december = calendarWeeks('2026-12', []).flat();
		expect(december.at(-1)?.date).toBe('2027-01-02');
		expect(calendarWeeks('2027-01', []).flat()[0].date).toBe('2026-12-27');
	});
});

describe('달 나누기와 기간 기록 잇기', () => {
	it('기록은 기간 안의 같은 달 칸에만 붙고, 앞뒤 달 칸에는 그 날 기록이 있어도 붙지 않는다', () => {
		const days = range('2026-09-20', 59);
		for (const month of calendarMonths(days)) {
			for (const cell of calendarWeeks(month, days).flat()) {
				const inPeriod = days.some((entry) => entry.date === cell.date);
				if (cell.inMonth) expect(cell.entry !== null, cell.date).toBe(inPeriod);
				else expect(cell.entry, cell.date).toBeNull();
			}
		}
	});

	it('59일(9.20~11.17)은 세 달이고, 모든 날이 정확히 한 번 달력에 나온다', () => {
		const days = range('2026-09-20', 59);
		expect(calendarMonths(days)).toEqual(['2026-09', '2026-10', '2026-11']);
		const shown = calendarMonths(days).flatMap((month) =>
			calendarWeeks(month, days)
				.flat()
				.filter((cell) => cell.entry)
				.map((cell) => cell.date)
		);
		expect(shown).toEqual(days.map((entry) => entry.date));
	});

	it('해를 넘는 기간(12.15~1.20)도 달 순서가 맞다', () => {
		expect(calendarMonths(range('2026-12-15', 37))).toEqual(['2026-12', '2027-01']);
	});

	it('날짜 순서가 섞이거나 같은 날이 두 번 와도 달이 겹치거나 뒤바뀌지 않는다', () => {
		const days = [day('2026-11-02'), day('2026-10-30'), day('2026-11-01'), day('2026-10-30')];
		expect(calendarMonths(days)).toEqual(['2026-10', '2026-11']);
	});

	it('기록이 하나도 없으면 달도 없다', () => {
		expect(calendarMonths([])).toEqual([]);
		expect(calendarStart([])).toBeNull();
	});
});

describe('처음 고를 날과 달', () => {
	it('마지막으로 값이 있는 날과 그 달이다(오늘 수집 중도 값이 있으면 고른다)', () => {
		const days = range('2026-10-18', 31);
		days[30] = { ...days[30], is_in_progress: true };
		expect(calendarStart(days)).toEqual({ date: '2026-11-17', month: '2026-11' });
	});

	it('기간 끝 며칠이 비어 다음 달로 넘어가면, 고른 날이 있는 앞 달을 보여 준다', () => {
		// 10.05~11.02. 10.30 뒤로 기록이 없다
		const days = range('2026-10-05', 29, (index) => (index >= 26 ? null : 100));
		expect(calendarStart(days)).toEqual({ date: '2026-10-30', month: '2026-10' });
	});

	it('값이 하나도 없으면 기간 마지막 날이다', () => {
		expect(calendarStart(range('2026-10-01', 20, () => null))).toEqual({
			date: '2026-10-20',
			month: '2026-10'
		});
	});

	it('날짜 순서가 섞여 와도 마지막 날을 고른다', () => {
		const days = [day('2026-10-20'), day('2026-10-22'), day('2026-10-21')];
		expect(calendarStart(days)?.date).toBe('2026-10-22');
	});
});

describe('도넛 채우기', () => {
	const averages = [0.5, 1, 18, 72.4, 420, 9560];

	it('어떤 값이든 두 바퀴 모두 0~1이고, 두 바퀴째는 첫 바퀴가 다 찬 뒤에만 생긴다', () => {
		for (const average of averages)
			for (const maxRatio of [null, 0.5, 1, 1.01, 2, 5])
				for (let ratio = 0; ratio <= 6; ratio += 0.05) {
					const max = maxRatio === null ? null : average * maxRatio;
					const { first, second } = donutFill(average * ratio, average, max);
					const label = `${average} ${maxRatio} ${ratio}`;
					expect(first, label).toBeGreaterThanOrEqual(0);
					expect(first, label).toBeLessThanOrEqual(1);
					expect(second, label).toBeGreaterThanOrEqual(0);
					expect(second, label).toBeLessThanOrEqual(1);
					if (second > 0) expect(first, label).toBe(1);
					expect(Number.isFinite(first) && Number.isFinite(second), label).toBe(true);
				}
	});

	it('값이 커질수록 도넛이 줄지 않는다', () => {
		for (const average of averages) {
			const max = average * 3;
			let previous = -1;
			for (let ratio = 0; ratio <= 4; ratio += 0.01) {
				const { first, second } = donutFill(average * ratio, average, max);
				expect(first + second).toBeGreaterThanOrEqual(previous);
				previous = first + second;
			}
		}
	});

	it('평균인 날은 첫 바퀴만 꽉 차고, 기간 최고인 날은 두 바퀴 모두 꽉 찬다', () => {
		for (const average of averages) {
			expect(donutFill(average, average, average * 2)).toEqual({ first: 1, second: 0 });
			expect(donutFill(average * 2, average, average * 2)).toEqual({ first: 1, second: 1 });
		}
	});

	it('오늘 수집 중 값이 기간 최고보다 커도(최고는 완료한 날에서만 고른다) 두 바퀴에서 멈춘다', () => {
		expect(donutFill(500, 100, 300)).toEqual({ first: 1, second: 1 });
	});

	it('평균이 낮은 운동(평균 18분)에서도 넘은 날끼리 양이 다르다', () => {
		const seconds = [30, 45, 60, 90].map((value) => donutFill(value, 18, 90).second);
		expect(new Set(seconds).size).toBe(4);
	});
});

describe('실제 근거 모양: 모든 예시 데이터', () => {
	const snapshots: [string, EvidenceSnapshot][] = [
		...Object.entries(evidenceCases).flatMap(([name, items]) =>
			(items as { snapshot: unknown }[])
				.filter((item) => (item.snapshot as Partial<EvidenceSnapshot>).days !== undefined)
				.map(
					(item, index) =>
						[`${name}#${index}`, item.snapshot as EvidenceSnapshot] as [string, EvidenceSnapshot]
				)
		),
		...[sleepAndStepsEvidence, sleepNightsSpo2, overlappingSleepEvidence, sleepSpo2Month, spo2Month]
			.flat()
			.map((item, index) => [`extra#${index}`, item.snapshot] as [string, EvidenceSnapshot])
	];

	it('예시가 충분히 모였다', () => {
		expect(snapshots.length).toBeGreaterThan(40);
	});

	it('달력을 쓰는 근거는 평균이 0보다 크고, 모든 날이 정확히 한 번 달력 칸에 나온다', () => {
		let used = 0;
		for (const [name, snapshot] of snapshots) {
			const base = calendarAverage(snapshot);
			if (base === null) continue;
			const average = base.value;
			used += 1;
			expect(average, name).toBeGreaterThan(0);
			const months = calendarMonths(snapshot.days);
			const shown = months.flatMap((month) =>
				calendarWeeks(month, snapshot.days)
					.flat()
					.flatMap((cell) => (cell.entry ? [cell.date] : []))
			);
			expect(shown.sort(), name).toEqual(snapshot.days.map((entry) => entry.date).sort());
			const start = calendarStart(snapshot.days);
			expect(start, name).not.toBeNull();
			expect(months, name).toContain(start!.month);
			// 도넛 값은 모두 0~1
			const max = snapshot.summary.daily_max?.value ?? null;
			for (const entry of snapshot.days) {
				if (entry.missing || entry.value === null) continue;
				const fill = donutFill(entry.value, average, max);
				expect(fill.first + fill.second, `${name} ${entry.date}`).toBeLessThanOrEqual(2);
			}
			// AI 가 받은 기간 최고는 두 바퀴째가 꽉 찬다
			if (max !== null && max > average) expect(donutFill(max, average, max).second).toBe(1);
		}
		expect(used).toBeGreaterThan(10);
	});

	it('신체 지표·산소포화도 근거에는 달력을 두지 않는다', () => {
		for (const [name, snapshot] of snapshots)
			if (!['steps', 'exercises', 'sleeps', 'heart_rates'].includes(snapshot.metric))
				expect(calendarAverage(snapshot), name).toBeNull();
	});

	it('산소포화도 점은 90% 미만이 있었던 밤에만, 날짜는 그 밤 기록 날짜(깬 날)다', () => {
		for (const [name, snapshot] of snapshots) {
			for (const night of nightSpo2Items(snapshot)) {
				const source = snapshot.detail.nights.find((item) => item.start_at === night.key)!;
				const low = (source.spo2_low_count ?? 0) > 0 || (source.spo2_low_times?.length ?? 0) > 0;
				expect(hasLowSpo2(night.state), `${name} ${night.date}`).toBe(low);
				expect(night.date).toBe(source.date);
			}
		}
	});

	it('산소포화도 네 상태 중 측정 없음·없음·0회는 점을 찍지 않는다', () => {
		expect(hasLowSpo2(spo2LowState(null, null)!)).toBe(false);
		expect(hasLowSpo2(spo2LowState(0, [])!)).toBe(false);
		expect(hasLowSpo2(spo2LowState(0, null)!)).toBe(false);
		expect(hasLowSpo2(spo2LowState(3, null)!)).toBe(true);
		expect(hasLowSpo2(spo2LowState(1, ['2026-10-20T03:12:00+09:00'])!)).toBe(true);
	});
});

describe('평균과 비교하는 문구', () => {
	it('지표에 맞는 말과 화면 자리로 쓴다', () => {
		expect(compareToAverage('steps', 10560, 9560)).toBe('평균보다 1,000걸음 많아요');
		expect(compareToAverage('steps', 8560, 9560)).toBe('평균보다 1,000걸음 적어요');
		expect(compareToAverage('sleeps', 452, 422)).toBe('평균보다 30분 길어요');
		expect(compareToAverage('sleeps', 302, 422)).toBe('평균보다 2시간 짧아요');
		expect(compareToAverage('exercises', 90, 18)).toBe('평균보다 1시간 12분 길어요');
		expect(compareToAverage('heart_rates', 73.84, 72.4)).toBe('평균보다 1.4bpm 높아요');
		expect(compareToAverage('heart_rates', 70, 72.4)).toBe('평균보다 2.4bpm 낮아요');
	});

	it('보이는 값이 같으면 "평균과 같아요", 다르면 차이를 쓴다(모든 지표, 여러 값)', () => {
		const cases: [Parameters<typeof compareToAverage>[0], number, number][] = [
			['steps', 9560.4, 400],
			['sleeps', 421.6, 30],
			['exercises', 18.4, 20],
			['heart_rates', 72.36, 3]
		];
		for (const [metric, average, spread] of cases) {
			for (let offset = -spread; offset <= spread; offset += spread / 997) {
				const value = Math.max(0, average + offset);
				const text = compareToAverage(metric, value, average);
				const same = formatMetricValue(metric, value) === formatMetricValue(metric, average);
				expect(text === '평균과 같아요', `${metric} ${value}`).toBe(same);
				expect(text, `${metric} ${value}`).not.toMatch(/평균보다 0(걸음|분|bpm)/);
			}
		}
	});

	it('그날 값과 평균이 같은 "7시간 2분"으로 보이면 1분 차이라고 하지 않는다', () => {
		expect(compareToAverage('sleeps', 422.4, 421.6)).toBe('평균과 같아요');
		expect(compareToAverage('heart_rates', 72.44, 72.36)).toBe('평균과 같아요');
	});
});

describe('6개월 운동: 31일씩 여섯 번 나눠 조회한 기간(S15P31A403-430)', () => {
	const [group] = groupEvidenceByMetric(evidenceCases.exerciseSixMonths);
	const snapshot = group.current.snapshot;

	it('여섯 기간이 181일 하나로 합쳐지고, 운동한 날만 평균한 값을 원 기준으로 쓴다(S15P31A403-435)', () => {
		expect(isMergedGroup(group)).toBe(true);
		expect(snapshot.days).toHaveLength(181);
		expect(snapshot.summary.avg).toBeNull();
		const base = calendarAverage(snapshot);
		expect(base?.basis).toBe('active-days');
		const values = snapshot.days
			.filter((day) => !day.is_in_progress && !day.missing && day.value !== null)
			.map((day) => day.value!);
		expect(values).toHaveLength(180);
		const active = values.filter((value) => value > 0);
		expect(active.length).toBeLessThan(values.length);
		expect(base?.value).toBeCloseTo(active.reduce((sum, value) => sum + value, 0) / active.length);
	});

	it('5월부터 11월까지 일곱 달이고, 처음에는 오늘이 있는 11월을 보여 준다', () => {
		expect(calendarMonths(snapshot.days)).toEqual([
			'2026-05',
			'2026-06',
			'2026-07',
			'2026-08',
			'2026-09',
			'2026-10',
			'2026-11'
		]);
		expect(calendarStart(snapshot.days)).toEqual({ date: '2026-11-17', month: '2026-11' });
	});

	it('181일이 달력에 정확히 한 번씩, 나눈 경계(6.21·7.22 등)에서도 빠지거나 겹치지 않는다', () => {
		const shown = calendarMonths(snapshot.days).flatMap((month) =>
			calendarWeeks(month, snapshot.days)
				.flat()
				.flatMap((cell) => (cell.entry ? [cell.date] : []))
		);
		expect(shown).toEqual(snapshot.days.map((day) => day.date));
		for (const part of evidenceCases.exerciseSixMonths) expect(shown).toContain(part.periodStart);
	});

	it('두 바퀴째 끝은 여섯 기간 가운데 가장 높은 날이고, 그날만 두 바퀴가 꽉 찬다', () => {
		const base = calendarAverage(snapshot)!.value;
		const max = snapshot.summary.daily_max!.value;
		expect(max).toBe(
			Math.max(
				...evidenceCases.exerciseSixMonths.map((part) => part.snapshot.summary.daily_max!.value)
			)
		);
		const full = snapshot.days.filter(
			(day) => !day.missing && day.value !== null && donutFill(day.value, base, max).second === 1
		);
		expect(full.every((day) => day.value === max)).toBe(true);
		// 0분인 날은 빈 도넛, 운동한 날은 모두 무언가 찬다
		for (const day of snapshot.days) {
			if (day.missing || day.value === null) continue;
			const fill = donutFill(day.value, base, max);
			expect(fill.first > 0, day.date).toBe(day.value > 0);
		}
	});

	it('운동 기록은 날마다 그날 것만 모이고, 합친 뒤에도 하나도 사라지지 않는다', () => {
		const sessions = snapshot.detail.sessions;
		const total = evidenceCases.exerciseSixMonths.reduce(
			(sum, part) => sum + part.snapshot.detail.sessions.length,
			0
		);
		expect(sessions).toHaveLength(total);
		for (const day of snapshot.days)
			expect(sessions.filter((item) => item.date === day.date).length, day.date).toBe(
				day.value && day.value > 0 ? 1 : 0
			);
	});
});

describe('모든 예시의 나눠 조회한 묶음도 달력 계산이 맞다', () => {
	it('묶음마다 모든 날이 한 번씩 나오고 기준 평균이 0보다 크다', () => {
		let used = 0;
		for (const [name, items] of Object.entries(evidenceCases)) {
			const metricItems = (items as { snapshot: Partial<EvidenceSnapshot> }[]).filter(
				(item) => item.snapshot.days !== undefined
			) as unknown as Parameters<typeof groupEvidenceByMetric>[0];
			for (const group of groupEvidenceByMetric(metricItems)) {
				const snapshot = group.current.snapshot;
				const base = calendarAverage(snapshot);
				if (base === null) continue;
				used += 1;
				expect(base.value, name).toBeGreaterThan(0);
				expect(base.basis, name).toBe(
					snapshot.metric === 'exercises'
						? 'active-days'
						: isMergedGroup(group)
							? 'merged'
							: 'summary'
				);
				const shown = calendarMonths(snapshot.days).flatMap((month) =>
					calendarWeeks(month, snapshot.days)
						.flat()
						.flatMap((cell) => (cell.entry ? [cell.date] : []))
				);
				expect(shown, name).toEqual(snapshot.days.map((day) => day.date));
			}
		}
		expect(used).toBeGreaterThan(15);
	});
});

describe('심박 날짜별 측정 최저·최고(S15P31A403-430)', () => {
	const snapshot = evidenceCases.heartWeekMeasured[0].snapshot;

	it('그날 칸의 최저·최고를 읽고, 측정이 없는 날은 null 이다', () => {
		expect(dailyRangeOf(snapshot, '2026-10-26')).toEqual({ min: 60, max: 141 });
		expect(dailyRangeOf(snapshot, '2026-10-28')).toBeNull();
		expect(dailyRangeOf(snapshot, '2026-11-01')).toBeNull();
	});

	it('기간의 측정 최저·최고는 날짜별 최저·최고 가운데 있다', () => {
		const { measured_min: min, measured_max: max } = snapshot.detail;
		expect(dailyRangeOf(snapshot, min!.date)?.min).toBe(min!.value);
		expect(dailyRangeOf(snapshot, max!.date)?.max).toBe(max!.value);
	});

	it('모양이 다른 칸은 건너뛰고, 한쪽만 있으면 그 값만 둔다', () => {
		const odd = {
			...snapshot,
			detail: {
				...snapshot.detail,
				daily_ranges: [
					null,
					'2026-10-22',
					{ date: '2026-10-22', min: '55', max: Number.NaN },
					{ date: '2026-10-23', min: 58, max: null }
				]
			}
		};
		expect(dailyRangeOf(odd, '2026-10-22')).toBeNull();
		expect(dailyRangeOf(odd, '2026-10-23')).toEqual({ min: 58, max: null });
	});

	it('나눠 조회해 겹친 날 칸이 두 번 오면 뒤 기간 값을 쓴다', () => {
		const merged = {
			...snapshot,
			detail: {
				...snapshot.detail,
				daily_ranges: [
					{ date: '2026-10-26', min: 50, max: 120 },
					{ date: '2026-10-26', min: 60, max: 141 }
				]
			}
		};
		expect(dailyRangeOf(merged, '2026-10-26')).toEqual({ min: 60, max: 141 });
	});

	it('심박이 아닌 근거(산소포화도 등)는 읽지 않는다', () => {
		expect(dailyRangeOf({ ...snapshot, metric: 'oxygen_saturations' }, '2026-10-26')).toBeNull();
	});
});

describe('운동은 운동한 날만 평균·최고·최저로 센다(S15P31A403-435)', () => {
	const snapshot = evidenceCases.exerciseSessionsMonth[0].snapshot;
	const active = snapshot.days.filter(
		(day) => !day.is_in_progress && !day.missing && day.value !== null && day.value > 0
	);

	it('평균은 0분인 날(쉰 날)을 빼고 낸다', () => {
		const base = calendarAverage(snapshot)!;
		expect(base.basis).toBe('active-days');
		expect(base.value).toBeCloseTo(
			active.reduce((sum, day) => sum + day.value!, 0) / active.length
		);
		expect(base.value).toBeGreaterThan(snapshot.summary.avg!);
	});

	it('최저는 운동한 날 가운데 가장 짧은 날이고 0분인 날을 고르지 않는다', () => {
		const { max, min } = calendarExtremes(snapshot);
		expect(max?.value).toBe(Math.max(...active.map((day) => day.value!)));
		expect(min?.value).toBe(Math.min(...active.map((day) => day.value!)));
		expect(min?.value).toBeGreaterThan(0);
		expect(snapshot.summary.daily_min?.value).toBe(0);
	});

	it('같은 값이 여러 날이면 이른 날을 고르고, 운동한 날이 하루면 최저를 두지 않는다', () => {
		const days = (values: number[]) => ({
			...snapshot,
			days: values.map((value, index) => ({
				date: `2026-10-0${index + 1}`,
				value,
				missing: false,
				reason: null,
				is_in_progress: false
			}))
		});
		expect(calendarExtremes(days([0, 30, 60, 30, 60]))).toEqual({
			max: { date: '2026-10-03', value: 60 },
			min: { date: '2026-10-02', value: 30 }
		});
		expect(calendarExtremes(days([0, 45, 0]))).toEqual({
			max: { date: '2026-10-02', value: 45 },
			min: null
		});
		expect(calendarAverage(days([0, 0, 0]))).toBeNull();
	});

	it('오늘 수집 중 값은 평균·최고·최저에 넣지 않는다', () => {
		const withToday = {
			...snapshot,
			days: [
				...snapshot.days,
				{ date: '2026-10-29', value: 500, missing: false, reason: null, is_in_progress: true }
			]
		};
		expect(calendarExtremes(withToday).max?.value).toBe(calendarExtremes(snapshot).max?.value);
		expect(calendarAverage(withToday)?.value).toBeCloseTo(calendarAverage(snapshot)!.value);
	});

	it('다른 지표는 AI 가 받은 최고·최저를 쓰고 최저 0은 두지 않는다', () => {
		const steps = evidenceCases.month[0].snapshot;
		expect(calendarExtremes(steps).max).toEqual(steps.summary.daily_max);
		const zero = {
			...steps,
			summary: { ...steps.summary, daily_min: { date: '2026-10-02', value: 0 } }
		};
		expect(calendarExtremes(zero).min).toBeNull();
	});

	// S15P31A403-439 일별 막대 차트의 꼬리표는 달력 체크와 같은 날을 가리킨다
	it('차트 꼬리표는 달력 체크와 같은 날이고, 운동은 0분인 날을 빼고 고른다', () => {
		const marks = calendarExtremes(snapshot);
		expect(chartExtremes(snapshot)).toEqual({
			max: { key: marks.max!.date, value: marks.max!.value },
			min: { key: marks.min!.date, value: marks.min!.value }
		});
		expect(chartExtremes(snapshot)!.min!.value).toBeGreaterThan(0);
		expect(snapshot.summary.daily_min?.value).toBe(0);
	});

	it('차트 꼬리표는 심박에도 달고, 견줄 날이 없으면 달지 않는다', () => {
		const heart = evidenceCases.heartWeek[0].snapshot;
		expect(chartExtremes(heart)).toEqual({
			max: { key: heart.summary.daily_max!.date, value: heart.summary.daily_max!.value },
			min: { key: heart.summary.daily_min!.date, value: heart.summary.daily_min!.value }
		});
		const steps = evidenceCases.month[0].snapshot;
		const top = { key: steps.summary.daily_max!.date, value: steps.summary.daily_max!.value };
		// 모든 날이 같은 값이면 달지 않는다
		expect(
			chartExtremes({ ...steps, summary: { ...steps.summary, daily_min: steps.summary.daily_max } })
		).toBeNull();
		// 기록이 없으면 달지 않는다
		expect(
			chartExtremes({ ...steps, summary: { ...steps.summary, daily_max: null, daily_min: null } })
		).toBeNull();
		// 최저가 0이면 최고만 단다
		expect(
			chartExtremes({
				...steps,
				summary: { ...steps.summary, daily_min: { date: '2026-10-02', value: 0 } }
			})
		).toEqual({ max: top, min: null });
	});

	it('운동한 날이 하루뿐이면 최고만 달고, 운동한 날이 없으면 달지 않는다', () => {
		const days = (values: number[]) => ({
			...snapshot,
			days: values.map((value, index) => ({
				date: `2026-10-0${index + 1}`,
				value,
				missing: false,
				reason: null,
				is_in_progress: false
			}))
		});
		expect(chartExtremes(days([0, 45, 0]))).toEqual({
			max: { key: '2026-10-02', value: 45 },
			min: null
		});
		expect(chartExtremes(days([0, 0, 0]))).toBeNull();
	});
});

describe('원 그리기: 둥근 끝까지 합친 길이가 값과 같다(S15P31A403-438 피드백)', () => {
	const cap = (2 / (2 * Math.PI * 11)) * 100;

	it('둥근 끝 두 개를 더한 보이는 길이가 채울 양과 같다', () => {
		for (let amount = 0.06; amount < 1; amount += 0.01) {
			const dash = donutDash(amount);
			const drawn = Number(dash.dasharray.split(' ')[0]);
			expect(dash.linecap).toBe('round');
			expect(drawn + cap * 2).toBeCloseTo(amount * 100, 6);
			// 시작점을 둥근 끝만큼 밀어 원 꼭대기(0)에서 보이기 시작한다
			expect(dash.dashoffset).toBeCloseTo(-cap, 6);
		}
	});

	it('95% 인 날은 꽉 찬 원과 다르게 보인다', () => {
		const ninetyFive = donutDash(0.95);
		expect(Number(ninetyFive.dasharray.split(' ')[0]) + cap * 2).toBeLessThan(100);
		expect(donutDash(1)).toEqual({ dasharray: '100 100', dashoffset: 0, linecap: 'butt' });
	});

	it('아주 작은 값은 점 하나로 남고 음수 길이가 되지 않는다', () => {
		const tiny = donutDash(0.01);
		expect(Number(tiny.dasharray.split(' ')[0])).toBeGreaterThan(0);
		expect(tiny.linecap).toBe('round');
	});
});

describe('달력 줄 수: 기본 5주, 꼭 필요한 달만 6주(S15P31A403-438 피드백)', () => {
	it('1일이 일요일인 평년 2월(4주)은 5주로 채운다', () => {
		expect(weeksInMonth('2026-02')).toBe(4);
		const weeks = calendarWeeks('2026-02', []);
		expect(weeks).toHaveLength(5);
		expect(weeks.at(-1)!.every((cell) => !cell.inMonth)).toBe(true);
	});

	it('1일이 토요일인 31일 달은 6주다', () => {
		expect(weeksInMonth('2026-08')).toBe(6);
		expect(calendarWeeks('2026-08', [])).toHaveLength(6);
	});

	it('2000~2099년에 6주인 달은 마지막 줄에 그 달 날이 있고, 5주인 달은 빈 줄이 하나를 넘지 않는다', () => {
		for (let year = 2000; year < 2100; year += 1)
			for (let monthNumber = 1; monthNumber <= 12; monthNumber += 1) {
				const month = `${year}-${String(monthNumber).padStart(2, '0')}`;
				const weeks = calendarWeeks(month, []);
				const emptyRows = weeks.filter((week) => week.every((cell) => !cell.inMonth)).length;
				expect(emptyRows, month).toBeLessThanOrEqual(1);
				if (weeks.length === 6)
					expect(
						weeks[5].some((cell) => cell.inMonth),
						month
					).toBe(true);
			}
	});
});
