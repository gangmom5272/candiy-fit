import { describe, expect, it } from 'vitest';
import { groupEvidenceByMetric } from './evidence';
import {
	beforeLinkDays,
	calendarAverage,
	calendarMonths,
	calendarWeeks,
	chartBars,
	donutFill,
	hasLowSpo2,
	dayBars,
	chartSkipReason,
	coverage,
	coverageNote,
	dayHighlights,
	formatEvidencePeriod,
	dayListItems,
	hasAnyValue,
	listsAllDays,
	measuredRange,
	sessionItems,
	showsChart,
	sleepStageAverages,
	todayValue,
	spo2LowState,
	sleepSessionsView,
	hourlyColumns,
	nightSpo2Items,
	exerciseActivityView,
	measuredNote,
	spo2Summary,
	spo2SummaryNote,
	stageNote
} from './evidence-view';
import {
	evidenceCases,
	kindEvidenceJson,
	sleepAndStepsEvidence,
	sleepNightsSpo2,
	sleepSpo2Month
} from './fixtures';
import { parseEvidenceList } from './parse';
import { isKindEvidence } from './types';

const snapshotOf = (items: { snapshot: (typeof sleepAndStepsEvidence)[number]['snapshot'] }[]) =>
	items[0].snapshot;

describe('coverage', () => {
	it('기록된 날 수로 경우를 나눈다', () => {
		expect(coverage(snapshotOf(evidenceCases.noRecords)).kind).toBe('none');
		expect(coverage(snapshotOf(evidenceCases.oneDayOfSeven)).kind).toBe('single');
		expect(coverage(snapshotOf(sleepAndStepsEvidence)).kind).toBe('partial');
		expect(coverage(evidenceCases.sparseComparison[0].snapshot).kind).toBe('full');
	});

	it('AI 기준과 같게 기록 3일 이상·대상 날짜 절반 이상이어야 믿을 만하다', () => {
		expect(coverage(snapshotOf(sleepAndStepsEvidence)).isReliable).toBe(true);
		expect(coverage(evidenceCases.sparseComparison[0].snapshot).isReliable).toBe(true);
		// 7일 중 1일: 절반·3일 모두 못 미친다
		expect(coverage(evidenceCases.sparseComparison[1].snapshot).isReliable).toBe(false);
		// 하루 기간: 다 기록됐어도 3일에 못 미친다
		expect(coverage(snapshotOf(evidenceCases.singleDay)).isReliable).toBe(false);
		// 기록 없음: 평균이 없다
		expect(coverage(snapshotOf(evidenceCases.noRecords)).isReliable).toBe(false);
	});

	it('31일 중 15일은 절반에 못 미치고, 16일은 넘는다', () => {
		const month = snapshotOf(evidenceCases.month);
		const at = (recorded: number) =>
			coverage({
				...month,
				summary: { ...month.summary, completed_days: 31, days_with_data: recorded }
			}).isReliable;
		expect(at(15)).toBe(false);
		expect(at(16)).toBe(true);
	});
});

describe('근거 카드 글자', () => {
	it('하루 기간은 날짜를 한 번만 쓴다', () => {
		expect(formatEvidencePeriod('2026-10-27', '2026-10-27')).toBe('2026.10.27');
		expect(formatEvidencePeriod('2026-10-22', '2026-10-28')).toBe('2026.10.22 ~ 10.28');
	});

	it('지표 옆 기록 상태는 기록이 부족할 때만 붙여 평소 카드는 그대로 둔다', () => {
		expect(coverageNote(coverage(snapshotOf(evidenceCases.oneDayOfSeven)))).toBe('7일 중 1일');
		expect(coverageNote(coverage(snapshotOf(evidenceCases.noRecords)))).toBe('기록 없음');
		// 7일 다 기록, 7일 중 5일은 충분하다
		expect(coverageNote(coverage(evidenceCases.sparseComparison[0].snapshot))).toBeNull();
		expect(coverageNote(coverage(snapshotOf(sleepAndStepsEvidence)))).toBeNull();
		// 하루 기간은 일수를 쓰지 않는다
		expect(coverageNote(coverage(snapshotOf(evidenceCases.singleDay)))).toBeNull();
	});

	it('지난날 기록이 없어도 오늘 수집 중 값이 있으면 "기록 없음"이라 하지 않는다', () => {
		expect(coverageNote(coverage(snapshotOf(evidenceCases.todayOnly)))).toBe('수집 중');
		expect(coverageNote(coverage(snapshotOf(evidenceCases.todayOnlyWeek)))).toBe('오늘만 수집 중');
	});
});

describe('오늘 수집 중 값', () => {
	it('통계 대상 0일이어도 명세 in_progress 값과 기준 시각을 쓴다', () => {
		const cover = coverage(snapshotOf(evidenceCases.todayOnly));
		expect(cover.recordedDays).toBe(0);
		expect(cover.today).toEqual({
			date: '2026-10-28',
			value: 4210,
			asOf: '2026-10-28T05:00:00Z'
		});
		expect(hasAnyValue(cover)).toBe(true);
	});

	it('in_progress가 비었으면 days의 진행 중인 날 값을 쓰고, 그것도 없으면 없다', () => {
		const snapshot = snapshotOf(evidenceCases.todayOnly);
		expect(todayValue({ ...snapshot, in_progress: null })).toEqual({
			date: '2026-10-28',
			value: 4210,
			asOf: null
		});
		const missing = { ...snapshot.days[0], value: null, missing: true };
		expect(
			todayValue({
				...snapshot,
				days: [missing],
				in_progress: { ...snapshot.in_progress!, value: null, missing: true }
			})
		).toBeNull();
	});

	it('오늘 값도 지난 기록도 없으면 보여 줄 값이 없다', () => {
		expect(hasAnyValue(coverage(snapshotOf(evidenceCases.noRecords)))).toBe(false);
	});
});

describe('showsChart', () => {
	it('기록이 하루 이하이거나 기간이 하루면 차트를 그리지 않는다', () => {
		expect(showsChart(snapshotOf(evidenceCases.oneDayOfSeven))).toBe(false);
		expect(showsChart(snapshotOf(evidenceCases.noRecords))).toBe(false);
		expect(showsChart(snapshotOf(evidenceCases.singleDay))).toBe(false);
		expect(showsChart(snapshotOf(sleepAndStepsEvidence))).toBe(true);
	});

	it('기록이 모두 0이면 빈 차트 대신 요약으로 보여 준다', () => {
		const snapshot = snapshotOf(evidenceCases.allZeroWeek);
		expect(snapshot.summary.days_with_data).toBe(7);
		expect(showsChart(snapshot)).toBe(false);
	});
});

describe('chartSkipReason', () => {
	it('차트를 그리지 않았는데 값이 있으면 이유를 알린다', () => {
		expect(chartSkipReason(snapshotOf(evidenceCases.singleDay))).toBe(
			'하루 기록이라 차트 대신 값으로 보여 줘요.'
		);
		expect(chartSkipReason(snapshotOf(evidenceCases.todayOnly))).toBe(
			'하루 기록이라 차트 대신 값으로 보여 줘요.'
		);
		expect(chartSkipReason(snapshotOf(evidenceCases.todayOnlyWeek))).toBe(
			'아직 오늘 기록만 있어 차트를 그리지 않았어요.'
		);
		expect(chartSkipReason(snapshotOf(evidenceCases.oneDayOfSeven))).toBe(
			'기록된 날이 하루뿐이라 차트를 그리지 않았어요.'
		);
		expect(chartSkipReason(snapshotOf(evidenceCases.allZeroWeek))).toBe(
			'모든 날 기록이 0이라 차트 대신 날짜별 기록으로 보여 줘요.'
		);
	});

	it('차트가 있거나 보여 줄 값이 없으면 쓰지 않는다', () => {
		expect(chartSkipReason(snapshotOf(sleepAndStepsEvidence))).toBeNull();
		// 기록 없음은 "이 기간에는 기록이 없어요" 안내가 따로 있다
		expect(chartSkipReason(snapshotOf(evidenceCases.noRecords))).toBeNull();
	});
});

describe('신체 지표 차트(S15P31A403-311)', () => {
	it('90일 체중은 주 단위 막대에 평균이 아니라 그 주 마지막 측정값을 쓴다', () => {
		const snapshot = snapshotOf(evidenceCases.bodyQuarter);
		const { unit, bars } = chartBars(snapshot);
		expect(unit).toBe('week');
		const lastOfFirstWeek = snapshot.days.slice(0, 7).at(-1)!.value;
		expect(bars[0].value).toBe(lastOfFirstWeek);
	});

	it('오늘 체중만 있으면 차트 대신 오늘 값을 보여 준다', () => {
		const snapshot = snapshotOf(evidenceCases.bodyTodayOnly);
		expect(snapshot.summary.avg).toBeNull();
		expect(todayValue(snapshot)).toMatchObject({ value: 68.3 });
		expect(chartSkipReason(snapshot)).toBe('아직 오늘 기록만 있어 차트를 그리지 않았어요.');
	});
});

describe('chartBars', () => {
	it('31일까지는 하루 한 칸이고, 축 날짜는 6개 안팎만 둔다', () => {
		const { unit, bars } = chartBars(snapshotOf(evidenceCases.month));
		expect(unit).toBe('day');
		expect(bars).toHaveLength(31);
		expect(bars.filter((bar) => bar.labelWide).length).toBeLessThanOrEqual(7);
		expect(bars[bars.length - 1].inProgress).toBe(true);
	});

	it('하루 막대는 축 글자는 날짜만, 툴팁·보조기기 이름에는 요일까지 둔다', () => {
		const [first] = chartBars(snapshotOf(evidenceCases.month)).bars;
		expect(first.axisLabel).toBe('10.01');
		expect(first.title).toBe('10.01(목)');
	});

	it('넓은 화면은 7일 이하면 축 날짜를 모두, 휴대폰은 처음·가운데·끝만 둔다', () => {
		const { bars } = chartBars(snapshotOf(sleepAndStepsEvidence));
		expect(bars.every((bar) => bar.labelWide)).toBe(true);
		expect(bars.flatMap((bar, index) => (bar.labelCompact ? [index] : []))).toEqual([0, 3, 6]);
		const month = chartBars(snapshotOf(evidenceCases.month)).bars;
		expect(month.filter((bar) => bar.labelCompact)).toHaveLength(3);
	});

	it('31일보다 길면 7일씩 묶은 하루 평균이다', () => {
		const { unit, bars } = chartBars(snapshotOf(evidenceCases.quarter));
		expect(unit).toBe('week');
		expect(bars).toHaveLength(13);
		expect(bars[0].title).toBe('07.31 ~ 08.06');
		// 주가 많으면 축 글자는 첫날만 둔다
		expect(bars[0].axisLabel).toBe('07.31');
	});

	it('주가 6개 이하면 축 글자를 기간으로 적어 막대 하나가 한 주임을 보인다(S15P31A403-418)', () => {
		const [merged] = groupEvidenceByMetric(evidenceCases.weeklySplit);
		const { unit, bars } = chartBars(merged.current.snapshot);
		expect(unit).toBe('week');
		expect(bars).toHaveLength(6);
		expect(bars[0].axisLabel).toBe('09.03 ~ 09.09');
		// 날짜별 수면 단계는 주로 묶지 않은 하루 칸을 쓴다
		expect(dayBars(merged.current.snapshot)).toHaveLength(42);
	});

	it('주 평균은 기록 없는 날을 0으로 넣지 않는다', () => {
		const snapshot = snapshotOf(evidenceCases.quarter);
		const firstWeek = snapshot.days.slice(0, 7).flatMap((day) => (day.missing ? [] : [day.value!]));
		const expected = firstWeek.reduce((sum, value) => sum + value, 0) / firstWeek.length;
		expect(chartBars(snapshot).bars[0].value).toBeCloseTo(expected);
	});

	it('연동 전 날짜는 차트에서 뺀다', () => {
		const snapshot = snapshotOf(evidenceCases.beforeLink);
		expect(beforeLinkDays(snapshot)).toBe(4);
		expect(chartBars(snapshot).bars).toHaveLength(3);
	});
});

describe('dayListItems', () => {
	it('이어지는 기록 없는 날은 이유가 같으면 한 줄로 묶는다', () => {
		const items = dayListItems(snapshotOf(evidenceCases.oneDayOfSeven).days);
		expect(items.map((item) => item.kind)).toEqual(['gap', 'day', 'gap']);
		expect(items[0]).toMatchObject({ start: '2026-10-22', end: '2026-10-26', count: 5 });
	});

	it('이유가 다르면 따로 묶는다', () => {
		const items = dayListItems(snapshotOf(evidenceCases.noRecords).days);
		expect(items).toMatchObject([
			{ kind: 'gap', count: 5, reason: 'NO_RECORD' },
			{ kind: 'gap', count: 2, reason: 'UNSYNCED' }
		]);
	});
});

describe('긴 기간 날짜 목록', () => {
	it('14일이 넘으면 주요 날짜만 먼저 보여 준다', () => {
		expect(listsAllDays(snapshotOf(sleepAndStepsEvidence))).toBe(true);
		expect(listsAllDays(snapshotOf(evidenceCases.month))).toBe(false);
	});

	it('가장 높은 날·낮은 날·수집 중인 오늘을 고르고, 지표에 맞는 말로 부른다(S15P31A403-427)', () => {
		const labels = (snapshot: Parameters<typeof dayHighlights>[0]) =>
			dayHighlights(snapshot).map((item) => item.label);
		const month = snapshotOf(evidenceCases.month);
		expect(labels(month)).toEqual(['가장 많이 걸은 날', '가장 적게 걸은 날', '오늘(수집 중)']);
		expect(labels({ ...month, metric: 'sleeps' }).slice(0, 2)).toEqual([
			'가장 길게 잔 날',
			'가장 짧게 잔 날'
		]);
		expect(labels({ ...month, metric: 'exercises' }).slice(0, 2)).toEqual([
			'가장 오래 운동한 날',
			'가장 짧게 운동한 날'
		]);
		// 수치의 높낮이가 그대로 뜻인 지표는 바꾸지 않는다
		expect(labels({ ...month, metric: 'heart_rates' }).slice(0, 2)).toEqual([
			'가장 높은 날',
			'가장 낮은 날'
		]);
	});
});

describe('measuredRange', () => {
	it('심박은 하루 평균이 아닌 실제 측정 최저·최고를 돌려준다', () => {
		expect(measuredRange(snapshotOf(evidenceCases.heartOneDay))).toEqual({
			min: { date: '2026-10-27', value: 54 },
			max: { date: '2026-10-27', value: 138 }
		});
	});

	it('측정값이 없거나 심박·산소포화도가 아니면 null이다', () => {
		expect(measuredRange(snapshotOf(evidenceCases.heartWeek))).toBeNull();
		expect(measuredRange(snapshotOf(evidenceCases.sleepStagesWeek))).toBeNull();
	});
});

describe('sleepStageAverages', () => {
	it('단계 값이 있는 밤만 평균한다', () => {
		// 7일 중 10.25 밤은 단계가 없어 6개 밤의 평균이다: 깊은 잠 (78+70+84+62+88+74)/6 = 76
		expect(sleepStageAverages(snapshotOf(evidenceCases.sleepStagesWeek))).toEqual([
			{ label: '깊은 잠', minutes: 76, nights: 6 },
			{ label: '렘수면', minutes: 91, nights: 6 },
			{ label: '얕은 잠', minutes: 212, nights: 6 },
			{ label: '깬 시간', minutes: 22, nights: 6 }
		]);
	});

	it('밤 기록이 없으면 빈 목록이다', () => {
		expect(sleepStageAverages(snapshotOf(evidenceCases.oneDayOfSeven))).toEqual([]);
	});
});

describe('sessionItems', () => {
	it('최근 기록부터, 인정된 운동 중 가장 긴 것 하나에 표시한다', () => {
		const items = sessionItems(snapshotOf(evidenceCases.exerciseSessionsMonth));
		expect(items.map((item) => item.date).slice(0, 3)).toEqual([
			'2026-10-23',
			'2026-10-20',
			'2026-10-17'
		]);
		expect(items.filter((item) => item.isLongest)).toEqual([
			expect.objectContaining({ date: '2026-10-23', duration_minutes: 90 })
		]);
		// 인정되지 않은 기록도 목록에는 남는다
		expect(items.find((item) => !item.counts_as_exercise)?.date).toBe('2026-10-15');
	});

	it('기록이 없으면 빈 목록이다', () => {
		expect(sessionItems(snapshotOf(evidenceCases.allZeroWeek))).toEqual([]);
	});
});

describe('수면 중 산소포화도 90% 미만 네 가지(S15P31A403-312)', () => {
	it.each([
		[null, null, { kind: 'unmeasured' }],
		[3, null, { kind: 'count-only', count: 3 }],
		[0, [], { kind: 'none' }],
		[
			2,
			['2026-11-05T04:31:00+09:00', '2026-11-04T17:02:00Z'],
			{ kind: 'times', times: ['2026-11-04T17:02:00Z', '2026-11-05T04:31:00+09:00'] }
		]
	] as const)('횟수 %s · 시각 %j', (count, times, expected) => {
		expect(spo2LowState(count, times as string[] | null)).toEqual(expected);
	});

	it('예전 근거처럼 칸이 없으면 표시하지 않는다', () => {
		expect(spo2LowState(undefined, undefined)).toBeNull();
		expect(nightSpo2Items(snapshotOf(evidenceCases.sleepStagesWeek))).toEqual([]);
	});

	it('공통 수면 근거의 밤마다 상태를 시작 순서대로 낸다', () => {
		expect(nightSpo2Items(snapshotOf(sleepNightsSpo2)).map((item) => item.state.kind)).toEqual([
			'unmeasured',
			'none',
			'count-only',
			'times'
		]);
	});
});

describe('모양이 다른 근거 보기(S15P31A403-312)', () => {
	const [sleep, hourly] = parseEvidenceList({ items: Object.values(kindEvidenceJson) }, 'items');

	it('수면 세션은 시작 순서대로 두고 주 수면 평균은 is_main 만으로 낸다', () => {
		if (!isKindEvidence(sleep) || sleep.snapshot.kind !== 'sleep_sessions')
			throw new Error('수면 세션 근거 없음');
		const view = sleepSessionsView(sleep.snapshot);
		expect(view.sessions.map((session) => session.session_id)).toEqual(['s-003', 's-001', 's-002']);
		expect(view.mainCount).toBe(2);
		expect(view.extraCount).toBe(1);
		expect(view.mainAverage).toBe((420 + 330) / 2);
	});

	it('시간대 걸음 표의 열은 시작 시각부터 끝 시각 전까지다', () => {
		if (!isKindEvidence(hourly) || hourly.snapshot.kind !== 'hourly_steps')
			throw new Error('시간대 걸음 근거 없음');
		expect(hourlyColumns(hourly.snapshot)).toEqual([6, 7, 8, 9]);
	});
});

describe('한 건뿐이거나 비어 있는 운동 근거(S15P31A403-439)', () => {
	it('인정된 운동이 한 건뿐이면 "가장 길게"를 표시하지 않는다', () => {
		const items = sessionItems(snapshotOf(evidenceCases.exerciseOneSession));
		expect(items).toHaveLength(1);
		expect(items.some((item) => item.isLongest)).toBe(false);
	});

	it('인정된 운동이 둘 이상이면 가장 긴 한 건에만 표시한다', () => {
		const items = sessionItems(snapshotOf(evidenceCases.exerciseSessionsMonth));
		expect(items.filter((item) => item.isLongest)).toHaveLength(1);
	});

	it('횟수·칼로리·거리가 모두 비어 있으면 묶음을 두지 않는다', () => {
		expect(exerciseActivityView(snapshotOf(evidenceCases.exerciseTodayEmpty))).toBeNull();
	});

	it('하나라도 값이 있으면 묶음을 둔다', () => {
		const view = exerciseActivityView(snapshotOf(evidenceCases.exerciseOneSession));
		expect(view?.cards.map((card) => card.value)).toEqual(['1회', '548kcal', '-']);
	});

	it('모두 비어 있어도 수집에 실패한 원천이 있으면 알리려고 묶음을 둔다', () => {
		const snapshot = structuredClone(snapshotOf(evidenceCases.exerciseTodayEmpty));
		snapshot.detail.exercise_activity!.source_statuses.calories_burned = {
			last_sync_status: 'fail'
		};
		expect(exerciseActivityView(snapshot)?.notes.join(' ')).toContain('칼로리');
	});
});

describe('exerciseActivityView (S15P31A403-375)', () => {
	const [week] = evidenceCases.exerciseActivityWeek;

	it('값이 실려 오지 않은 근거(예전 근거·다른 지표)는 null 이다', () => {
		expect(exerciseActivityView(evidenceCases.exerciseSessionsMonth[0].snapshot)).toBeNull();
	});

	it('합계·평균은 받은 값을 그대로 쓰고 기록 일수를 붙인다', () => {
		expect(exerciseActivityView(week.snapshot)?.cards).toEqual([
			{ label: '운동 횟수', value: '4회', captions: ['하루 평균 0.8회', '기록 5일'] },
			{ label: '운동 칼로리', value: '1,170kcal', captions: ['하루 평균 234kcal', '기록 5일'] },
			{ label: '운동 거리', value: '12.5km', captions: ['하루 평균 3.1km', '기록 4일'] }
		]);
	});

	it('날짜별 칸: 0은 값으로, 값이 없으면 항목별 이유로 쓴다', () => {
		const rows = exerciseActivityView(week.snapshot)?.rows ?? [];
		expect(rows.map((row) => row.cells)).toEqual([
			['1회', '240kcal', '4.2km'],
			['0회', '0kcal', '0.0km'],
			['2회', '410kcal', '기록 없음'],
			['기록 없음', '기록 없음', '기록 없음'],
			['1회', '520kcal', '8.3km'],
			['0회', '0kcal', '0.0km'],
			['1회', '100kcal', '2.0km']
		]);
		expect(rows.at(-1)?.inProgress).toBe(true);
	});

	it('기준·오늘 제외·수집 실패를 안내한다', () => {
		const notes = exerciseActivityView(week.snapshot)?.notes ?? [];
		expect(notes[0]).toContain('인정된 기록만');
		expect(notes[1]).toContain('하루 전체 소모 칼로리나 이동 거리가 아니에요');
		expect(notes).toContain('오늘 값은 수집 중이며 합계·평균에서 제외해요.');
		expect(notes).toContain('칼로리 수집에 실패해 이전에 저장된 기록 기준이에요.');
	});

	it('합계 없이 오늘 값만 있으면("오늘 운동 얼마나 했어?") 오늘 값을 보여 준다', () => {
		const activity = week.snapshot.detail.exercise_activity!;
		const todayOnly = {
			...week.snapshot,
			detail: {
				...week.snapshot.detail,
				exercise_activity: {
					...activity,
					days: activity.days.slice(-1),
					summary: {
						exercise_count: { total: null, avg: null, days_with_data: 0 },
						exercise_calories_kcal: { total: null, avg: null, days_with_data: 0 },
						exercise_distance_m: { total: null, avg: null, days_with_data: 0 }
					}
				}
			}
		};
		const view = exerciseActivityView(todayOnly);
		expect(view?.cards[0]).toEqual({
			label: '오늘 운동 횟수',
			value: '1회',
			captions: ['수집 중']
		});
		expect(view?.notes).not.toContain('오늘 값은 수집 중이며 합계·평균에서 제외해요.');
	});

	it('연동 전·동기화 전은 그 이유를 쓴다', () => {
		const activity = week.snapshot.detail.exercise_activity!;
		const [first, second, ...rest] = activity.days;
		const view = exerciseActivityView({
			...week.snapshot,
			detail: {
				...week.snapshot.detail,
				exercise_activity: {
					...activity,
					days: [
						{
							...first,
							exercise_count: null,
							exercise_calories_kcal: null,
							exercise_distance_m: null,
							reason: 'BEFORE_LINK',
							calories_reason: 'BEFORE_LINK',
							distance_reason: 'BEFORE_LINK'
						},
						{ ...second, exercise_calories_kcal: null, calories_reason: 'UNSYNCED' },
						...rest
					]
				}
			}
		});
		expect(view?.rows[0].cells).toEqual(['연동 전', '연동 전', '연동 전']);
		expect(view?.rows[1].cells).toEqual(['0회', '동기화 전', '0.0km']);
	});
});

describe('근거 화면 문구·요약(S15P31A403-388, 앱 384와 같은 규칙)', () => {
	it('수면 단계 평균 안내는 며칠 중 며칠의 평균인지 날 수로 말한다', () => {
		expect(stageNote(7, 3, 0)).toBe('7일 중 3일의 평균이에요.');
		expect(stageNote(7, 5, 2)).toBe('7일 중 5일의 평균이에요. 수면 단계 기록이 없는 2일은 뺐어요.');
		expect(stageNote(7, 7, 0)).toBe('7일 평균이에요.');
		expect(stageNote(1, 1, 0)).toBe('이 날 하루의 수면 단계예요.');
	});

	it('측정 최저·최고 안내는 기간의 날 수로 말한다', () => {
		expect(measuredNote(7)).toBe('7일 동안 측정된 값 중 가장 낮은 값과 가장 높은 값이에요.');
		expect(measuredNote(1)).toBe('이 날 측정된 값 중 가장 낮은 값과 가장 높은 값이에요.');
	});

	it('날짜별 산소포화도에 그 밤의 평균을 싣고, 값이 없는 근거는 null 이다', () => {
		const [month] = sleepSpo2Month;
		expect(nightSpo2Items(month.snapshot)[0].average).toBeCloseTo(95.5);
		expect(
			nightSpo2Items(sleepNightsSpo2[0].snapshot).every((night) => night.average === null)
		).toBe(true);
	});

	it('밤이 많으면 평균·최저·최고로 요약하고, 한 주 이하면 요약하지 않는다', () => {
		expect(spo2Summary(sleepNightsSpo2[0].snapshot)).toBeNull();

		const summary = spo2Summary(sleepSpo2Month[0].snapshot);
		expect(summary?.nights).toBe(30);
		expect(summary?.lowNights).toBe(2);
		// 최저·최고는 밤마다 실려 온 값 중에서 고르고 그 밤을 함께 돌려준다
		expect(summary?.lowest).toEqual({
			value: 86,
			date: sleepSpo2Month[0].snapshot.detail.nights[6].date
		});
		expect(summary?.highest).toEqual({
			value: 99.5,
			date: sleepSpo2Month[0].snapshot.detail.nights[18].date
		});
		// 평균은 날짜별 평균의 평균(화면에서 낸 값)
		expect(summary?.average).toBeCloseTo(95.95);
		expect(spo2SummaryNote(2)).toBe('90% 미만으로 측정된 날은 2일이에요.');
		expect(spo2SummaryNote(0)).toBe('90% 미만으로 측정된 날은 없어요.');
	});
});

describe('날짜별 기록 달력(S15P31A403-430)', () => {
	const day = (date: string, value: number | null = 100) => ({
		date,
		value,
		missing: value === null,
		reason: value === null ? ('NO_RECORD' as const) : null,
		is_in_progress: false
	});

	it('도넛은 평균까지 첫 바퀴, 평균에서 기간 최고까지 두 바퀴째를 채운다', () => {
		expect(donutFill(50, 100, 300)).toEqual({ first: 0.5, second: 0 });
		expect(donutFill(100, 100, 300)).toEqual({ first: 1, second: 0 });
		expect(donutFill(200, 100, 300)).toEqual({ first: 1, second: 0.5 });
		expect(donutFill(300, 100, 300)).toEqual({ first: 1, second: 1 });
		expect(donutFill(0, 100, 300)).toEqual({ first: 0, second: 0 });
	});

	it('평균이 낮아도(운동) 넘은 날끼리 두 바퀴째 양이 다르다', () => {
		const average = 18;
		const fills = [40, 70, 90].map((value) => donutFill(value, average, 90).second);
		expect(new Set(fills).size).toBe(3);
		expect(fills[2]).toBe(1);
	});

	it('기간 최고가 없거나 평균 이하이면 두 바퀴째를 그리지 않는다', () => {
		expect(donutFill(150, 100, null)).toEqual({ first: 1, second: 0 });
		expect(donutFill(150, 100, 100)).toEqual({ first: 1, second: 0 });
	});

	it('기간이 걸친 달을 앞에서부터 고른다', () => {
		const days = [day('2026-09-29'), day('2026-09-30'), day('2026-10-01'), day('2026-11-02')];
		expect(calendarMonths(days)).toEqual(['2026-09', '2026-10', '2026-11']);
	});

	it('달은 일요일부터 시작하고 앞뒤 달의 날로 주를 채우며, 기간 밖의 날은 기록이 없다', () => {
		const days = [day('2026-10-18'), day('2026-10-19', null)];
		const weeks = calendarWeeks('2026-10', days);
		// 2026-10-01 은 목요일이라 앞에 9월 27~30일이 온다. 10-31 은 토요일이고 5주로 끝난다
		expect(weeks).toHaveLength(5);
		expect(weeks.every((week) => week.length === 7)).toBe(true);
		expect(weeks[0][0]).toMatchObject({ date: '2026-09-27', inMonth: false, entry: null });
		expect(weeks[0][4]).toMatchObject({ date: '2026-10-01', day: 1, weekday: 4, inMonth: true });
		expect(weeks[0][4].entry).toBeNull();
		const cells = weeks.flat();
		expect(cells.find((cell) => cell.date === '2026-10-18')?.entry?.value).toBe(100);
		expect(cells.find((cell) => cell.date === '2026-10-19')?.entry?.missing).toBe(true);
		expect(cells.filter((cell) => cell.inMonth).at(-1)?.date).toBe('2026-10-31');
		expect(cells.at(-1)).toMatchObject({ date: '2026-10-31', inMonth: true });
	});

	it('11월은 마지막 주를 12월 날로 채운다', () => {
		const weeks = calendarWeeks('2026-11', []);
		expect(weeks.at(-1)?.at(-1)).toMatchObject({ date: '2026-12-05', inMonth: false });
	});

	it('평균 대비가 뜻을 갖는 네 지표만, 평균이 있을 때 달력을 둔다', () => {
		const steps = snapshotOf(evidenceCases.month);
		expect(calendarAverage(steps)).toEqual({ value: steps.summary.avg, basis: 'summary' });
		expect(calendarAverage({ ...steps, metric: 'weights' })).toBeNull();
		expect(calendarAverage({ ...steps, summary: { ...steps.summary, avg: 0 } })).toBeNull();
		// 나눠 조회해 합친 기간처럼 평균이 없으면 하루 값(오늘·값 없는 날 제외)을 평균해 쓴다
		const values = steps.days.flatMap((day) =>
			day.is_in_progress || day.missing || day.value === null ? [] : [day.value]
		);
		const merged = calendarAverage({ ...steps, summary: { ...steps.summary, avg: null } });
		expect(merged?.basis).toBe('merged');
		expect(merged?.value).toBeCloseTo(
			values.reduce((sum, value) => sum + value, 0) / values.length
		);
		expect(
			calendarAverage({
				...steps,
				summary: { ...steps.summary, avg: null },
				days: steps.days.map((day) => ({ ...day, missing: true, value: null }))
			})
		).toBeNull();
	});

	it('산소포화도 90% 미만이 있었던 밤만 점을 찍는다', () => {
		expect(hasLowSpo2({ kind: 'times', times: ['2026-10-20T03:12:00+09:00'] })).toBe(true);
		expect(hasLowSpo2({ kind: 'count-only', count: 2 })).toBe(true);
		expect(hasLowSpo2({ kind: 'count-only', count: 0 })).toBe(false);
		expect(hasLowSpo2({ kind: 'none' })).toBe(false);
		expect(hasLowSpo2({ kind: 'unmeasured' })).toBe(false);
	});
});
