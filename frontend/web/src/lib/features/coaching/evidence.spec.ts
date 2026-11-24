import { describe, expect, it } from 'vitest';
import { barTone, chartScale, formatAxisValue, isLineChart, scaleFraction } from './chart';
import { averageDelta, groupEvidenceByMetric, isMergedGroup } from './evidence';
import { evidenceCases, overlappingSleepEvidence, sleepAndStepsEvidence } from './fixtures';
import { nightSpo2Items, sleepStageAverages } from './evidence-view';

describe('groupEvidenceByMetric', () => {
	it('같은 지표의 늦은 기간을 현재, 앞 기간을 비교로 묶고 처음 나온 순서를 지킨다', () => {
		const groups = groupEvidenceByMetric([...sleepAndStepsEvidence].reverse());

		expect(groups.map((group) => group.metric)).toEqual(['sleeps', 'steps']);
		expect(groups[0].current.periodStart).toBe('2026-10-01');
		expect(groups[0].comparison?.periodStart).toBe('2026-09-24');
	});

	it('비교 기간이 없으면 comparison은 null이다', () => {
		const [group] = groupEvidenceByMetric(sleepAndStepsEvidence.slice(0, 1));
		expect(group.comparison).toBeNull();
		expect(averageDelta(group)).toBeNull();
	});

	it('평균 차이를 계산한다', () => {
		const [sleep] = groupEvidenceByMetric(sleepAndStepsEvidence);
		expect(averageDelta(sleep)).toBe(18);
	});

	it('이번 주와 지난주처럼 길이를 맞춘 두 기간은 날짜가 이어져도 비교로 둔다', () => {
		// sleepAndStepsEvidence 는 09.24~09.30 과 10.01~10.07 로 날짜가 바로 이어진다
		const [sleep] = groupEvidenceByMetric(sleepAndStepsEvidence);
		expect(isMergedGroup(sleep)).toBe(false);
		expect(sleep.parts).toEqual([sleep.current]);
		expect(sleep.comparison).not.toBeNull();
	});
});

describe('나눠 조회한 기간(S15P31A403-368)', () => {
	/** 앞 기간을 11.05 까지로 줄여, 어느 기간도 다른 기간에 통째로 덮이지 않고 일부만 겹치게 한다 */
	const partlyOverlapping = () => {
		const evidence = structuredClone(overlappingSleepEvidence);
		const [long] = evidence;
		long.periodEnd = long.snapshot.period.end = '2026-11-05';
		long.snapshot.days = long.snapshot.days.filter((day) => day.date <= '2026-11-05');
		long.snapshot.detail.nights = long.snapshot.detail.nights.filter(
			(night) => night.date <= '2026-11-05'
		);
		return evidence;
	};

	it('다른 기간에 통째로 겹치는 기간은 기간 구분에서 빼고 따로 남긴다(S15P31A403-418)', () => {
		const before = structuredClone(overlappingSleepEvidence);
		const [group] = groupEvidenceByMetric(overlappingSleepEvidence);

		// 6주 전체 안에 지난주·이번 주가 들어 있다. 6주를 그대로 보여 주고 두 주는 겹치는 기간으로 둔다
		expect(group.current).toBe(overlappingSleepEvidence[0]);
		expect(isMergedGroup(group)).toBe(false);
		expect(group.current.snapshot.summary.avg).not.toBeNull();
		expect(group.overlaps.map((part) => part.periodStart)).toEqual(['2026-11-02', '2026-11-09']);
		expect(overlappingSleepEvidence).toEqual(before);
	});

	it('이전 30일·최근 30일과 두 달 전체를 함께 조회하면 겹치는 전체를 빼고 두 기간을 비교한다(S15P31A403-418)', () => {
		const [group] = groupEvidenceByMetric(evidenceCases.sleepMonthCompareOverlap);

		expect(isMergedGroup(group)).toBe(false);
		expect([group.current.periodStart, group.current.periodEnd]).toEqual([
			'2026-10-18',
			'2026-11-16'
		]);
		expect([group.comparison?.periodStart, group.comparison?.periodEnd]).toEqual([
			'2026-09-18',
			'2026-10-17'
		]);
		expect(group.overlaps.map((part) => [part.periodStart, part.periodEnd])).toEqual([
			['2026-09-20', '2026-11-16']
		]);
		expect(averageDelta(group)).not.toBeNull();
	});

	it('주별로 나눠 조회한 기간에 전체 기간이 함께 오면 전체만 빼고 주별 구분은 그대로 둔다(S15P31A403-418)', () => {
		const weeks = evidenceCases.weeklySplit;
		const whole = {
			...weeks[0],
			id: 'whole',
			position: 9,
			periodStart: '2026-09-03',
			periodEnd: '2026-10-14'
		};
		const [group] = groupEvidenceByMetric([...weeks, whole]);

		expect(isMergedGroup(group)).toBe(true);
		expect(group.parts).toHaveLength(6);
		expect(group.overlaps).toEqual([whole]);
	});

	it('일부만 겹치는 두 기간은 긴 쪽을 보여 주고 다른 기간을 겹치는 기간으로 남긴다(S15P31A403-439)', () => {
		// 35일(10.02~11.05)과 그 끝에 사흘 더 걸친 7일(11.02~11.08). 전에는 늦게 끝나는 7일을 보여 줬다
		const [first, second] = partlyOverlapping();
		const [group] = groupEvidenceByMetric([first, second]);

		expect(group.current).toBe(first);
		expect(group.comparison).toBeNull();
		expect(group.parts).toEqual([first]);
		expect(group.overlaps).toEqual([second]);
	});

	it('28일과 하루 더 늦게 끝나는 최근 7일을 함께 조회하면 28일을 보여 줘 다른 지표와 기간이 맞는다(S15P31A403-439)', () => {
		const groups = groupEvidenceByMetric(evidenceCases.partialOverlapHeart);
		const heart = groups.find((group) => group.metric === 'heart_rates')!;
		const steps = groups.find((group) => group.metric === 'steps')!;

		expect([heart.current.periodStart, heart.current.periodEnd]).toEqual([
			steps.current.periodStart,
			steps.current.periodEnd
		]);
		expect(heart.comparison).toBeNull();
		expect(heart.overlaps.map((item) => [item.periodStart, item.periodEnd])).toEqual([
			['2026-10-22', '2026-10-28']
		]);
	});

	it('일부만 겹치는 두 기간의 길이가 같으면 늦게 끝나는 쪽을 보여 준다', () => {
		// 하루 어긋난 같은 길이의 두 기간(운동을 14일씩 두 번 조회한 경우). 오늘이 든 쪽이 남는다
		const [, long] = evidenceCases.partialOverlapHeart;
		const shifted = structuredClone(long);
		shifted.id = 'shifted';
		shifted.periodStart = shifted.snapshot.period.start = '2026-10-01';
		shifted.periodEnd = shifted.snapshot.period.end = '2026-10-28';
		const [group] = groupEvidenceByMetric([long, shifted]);

		expect(group.current).toBe(shifted);
		expect(group.overlaps).toEqual([long]);
	});

	it('일부만 겹치는 세 기간의 밤과 날짜는 한 번만 세고 기간별 평균과 입력은 보존한다', () => {
		const before = partlyOverlapping();
		const evidence = structuredClone(before);
		const [group] = groupEvidenceByMetric(evidence);
		const snapshot = group.current.snapshot;
		const original = overlappingSleepEvidence[0].snapshot;

		expect(snapshot.days).toHaveLength(42);
		expect(snapshot.detail.nights).toHaveLength(42);
		expect(new Set(nightSpo2Items(snapshot).map((night) => night.key)).size).toBe(42);
		expect(snapshot.summary).toMatchObject({
			completed_days: 42,
			days_with_data: 42,
			days_used_for_summary: 42,
			avg: null,
			total: null
		});
		expect(sleepStageAverages(snapshot)).toEqual(sleepStageAverages(original));
		expect(group.parts.map((part) => part.snapshot.summary.avg)).toEqual(
			before.map((part) => part.snapshot.summary.avg)
		);
		expect(group.overlaps).toEqual([]);
		expect(evidence).toEqual(before);
	});

	it('단일 수면 근거의 밤과 집계는 그대로 둔다', () => {
		const [original] = overlappingSleepEvidence;
		const [group] = groupEvidenceByMetric([original]);
		expect(group.current).toBe(original);
		expect(nightSpo2Items(group.current.snapshot)).toHaveLength(42);
	});

	it('중복 날짜의 연동 전·진행 중은 집계에서 빼고 결측·미동기화·실제 0을 구분한다', () => {
		const evidence = partlyOverlapping();
		for (const [index, part] of evidence.entries()) {
			part.metric = part.snapshot.metric = 'steps';
			part.snapshot.unit = 'steps';
			part.snapshot.detail.nights = [];
			for (const day of part.snapshot.days) {
				if (day.date === '2026-10-02')
					Object.assign(day, { missing: true, value: null, reason: 'BEFORE_LINK' });
				if (day.date === '2026-11-02')
					Object.assign(day, { missing: true, value: null, reason: 'NULL_VALUE' });
				if (day.date === '2026-11-03')
					Object.assign(day, { missing: true, value: null, reason: 'UNSYNCED' });
				if (day.date === '2026-11-11') day.value = 0;
				if (day.date === '2026-11-12') day.is_in_progress = true;
			}
			Object.assign(part.snapshot.summary, {
				completed_days: [40, 7, 3][index],
				days_with_data: [38, 5, 3][index],
				days_used_for_summary: [38, 5, 3][index],
				missing_days: index < 2 ? ['2026-11-02'] : [],
				unsynced_days: index < 2 ? ['2026-11-03'] : []
			});
		}
		const [group] = groupEvidenceByMetric(evidence);
		expect(group.current.snapshot.summary).toMatchObject({
			completed_days: 40,
			days_with_data: 38,
			days_used_for_summary: 38,
			missing_days: ['2026-11-02'],
			unsynced_days: ['2026-11-03']
		});
		expect(group.current.snapshot.days.find((day) => day.date === '2026-11-11')?.value).toBe(0);
	});

	it('겹친 운동 기록은 같은 실제 시작 시각을 한 번만 남긴다', () => {
		const evidence = partlyOverlapping();
		for (const [index, part] of evidence.entries()) {
			part.metric = part.snapshot.metric = 'exercises';
			part.snapshot.detail.nights = [];
			part.snapshot.detail.sessions = [
				{
					date: '2026-11-03',
					start_at: index === 1 ? '2026-11-03T19:00:00+09:00' : '2026-11-03T10:00:00Z',
					duration_minutes: 30,
					type_name: '걷기',
					title: null,
					counts_as_exercise: true
				}
			];
		}
		// 마지막 기간에는 별도 날짜의 세션이 있어 서로 다른 운동은 그대로 보존한다.
		evidence[2].snapshot.detail.sessions[0].date = '2026-11-09';
		evidence[2].snapshot.detail.sessions[0].start_at = '2026-11-09T10:00:00Z';
		const [group] = groupEvidenceByMetric(evidence);
		expect(group.current.snapshot.detail.sessions).toEqual([
			evidence[1].snapshot.detail.sessions[0],
			evidence[2].snapshot.detail.sessions[0]
		]);
	});

	it('겹친 밤의 값이 다르면 날짜별 값처럼 뒤 기간을 선택한다', () => {
		const evidence = partlyOverlapping();
		// 11.02 는 앞 기간(10.02 ~ 11.05)과 둘째 기간(11.02 ~ 11.08)에 모두 있다
		const replacement = evidence[1].snapshot.detail.nights[0];
		replacement.deep_minutes = 123;
		const [group] = groupEvidenceByMetric(evidence);
		expect(
			group.current.snapshot.detail.nights.filter((night) => night.date === replacement.date)
		).toEqual([replacement]);
	});
	it('세 번 이상 나눠 조회하면 처음 날부터 마지막 날까지 한 범위로 합치고 조회한 기간을 모두 남긴다', () => {
		const [sleep] = groupEvidenceByMetric(evidenceCases.weeklySplit);

		expect(isMergedGroup(sleep)).toBe(true);
		expect(sleep.comparison).toBeNull();
		expect(sleep.current.periodStart).toBe('2026-09-03');
		expect(sleep.current.periodEnd).toBe('2026-10-14');
		expect(sleep.current.snapshot.days).toHaveLength(42);
		// 조회는 최근 주부터 했지만 화면에는 시작 순으로 둔다
		expect(sleep.parts.map((part) => part.periodStart)).toEqual([
			'2026-09-03',
			'2026-09-10',
			'2026-09-17',
			'2026-09-24',
			'2026-10-01',
			'2026-10-08'
		]);
	});

	it('31일 제한으로 둘로 나눠 조회한 이어진 기간은 비교가 아니라 한 범위다', () => {
		const [steps] = groupEvidenceByMetric(evidenceCases.longSplit);

		expect(isMergedGroup(steps)).toBe(true);
		expect(steps.comparison).toBeNull();
		expect(steps.current.periodStart).toBe('2026-10-01');
		expect(steps.current.periodEnd).toBe('2026-11-11');
		expect(steps.parts).toHaveLength(2);
	});

	it('합친 범위의 평균·합계는 다시 계산하지 않고, 날 수와 최저·최고는 조회한 기간의 값에서 가져온다', () => {
		const [steps] = groupEvidenceByMetric(evidenceCases.longSplit);
		const { summary } = steps.current.snapshot;
		const parts = steps.parts.map((part) => part.snapshot.summary);

		expect(summary.avg).toBeNull();
		expect(summary.total).toBeNull();
		expect(summary.days_with_data).toBe(parts[0].days_with_data + parts[1].days_with_data);
		expect(summary.completed_days).toBe(42);
		expect(summary.daily_max?.value).toBe(
			Math.max(parts[0].daily_max?.value ?? 0, parts[1].daily_max?.value ?? 0)
		);
		expect(summary.daily_min?.value).toBe(
			Math.min(parts[0].daily_min?.value ?? Infinity, parts[1].daily_min?.value ?? Infinity)
		);
		// 기간별 평균은 AI 가 받은 값 그대로 남아 있다
		expect(steps.parts.every((part) => part.snapshot.summary.avg !== null)).toBe(true);
	});

	it('달력 한 달씩 맞춘 두 기간(지난달·이번 달)은 이어져도 비교로 둔다', () => {
		const [september, october] = [
			{ ...evidenceCases.longSplit[0], periodStart: '2026-09-01', periodEnd: '2026-09-30' },
			{ ...evidenceCases.longSplit[1], periodStart: '2026-10-01', periodEnd: '2026-10-31' }
		];
		const [steps] = groupEvidenceByMetric([october, september]);

		expect(isMergedGroup(steps)).toBe(false);
		expect(steps.current.periodStart).toBe('2026-10-01');
		expect(steps.comparison?.periodStart).toBe('2026-09-01');
	});

	// AI 는 49일까지만 "남는 날 + 뒤 31일"로 나눈다. 그 모양이 아니면 길이가 달라도 비교다(S15P31A403-374)
	const pair = (earlier: [string, string], later: [string, string]) => {
		const [steps] = groupEvidenceByMetric([
			{ ...evidenceCases.longSplit[0], periodStart: earlier[0], periodEnd: earlier[1] },
			{ ...evidenceCases.longSplit[1], periodStart: later[0], periodEnd: later[1] }
		]);
		return steps;
	};

	it('수요일에 묻는 이번 주와 지난주(7일·3일)는 길이가 달라도 비교다', () => {
		const steps = pair(['2026-11-02', '2026-11-08'], ['2026-11-09', '2026-11-11']);

		expect(isMergedGroup(steps)).toBe(false);
		expect(steps.current.periodStart).toBe('2026-11-09');
		expect(steps.comparison?.periodStart).toBe('2026-11-02');
	});

	it('월 중에 묻는 이번 달과 지난달(31일·12일)은 비교다', () => {
		const steps = pair(['2026-10-01', '2026-10-31'], ['2026-11-01', '2026-11-12']);

		expect(isMergedGroup(steps)).toBe(false);
		expect(steps.comparison?.periodStart).toBe('2026-10-01');
	});

	it('나눠 부른 모양의 경계: 앞 18일 + 뒤 31일(49일)까지 한 범위, 앞이 19일이면 비교다', () => {
		expect(isMergedGroup(pair(['2026-09-24', '2026-10-11'], ['2026-10-12', '2026-11-11']))).toBe(
			true
		);
		expect(isMergedGroup(pair(['2026-10-11', '2026-10-11'], ['2026-10-12', '2026-11-11']))).toBe(
			true
		);
		expect(isMergedGroup(pair(['2026-09-23', '2026-10-11'], ['2026-10-12', '2026-11-11']))).toBe(
			false
		);
	});

	it('31일씩 이어진 두 기간은 나눠 부른 것이 아니라 비교다(62일은 나눠 조회하지 않는다)', () => {
		const steps = pair(['2026-09-11', '2026-10-11'], ['2026-10-12', '2026-11-11']);

		expect(isMergedGroup(steps)).toBe(false);
		expect(steps.comparison?.periodStart).toBe('2026-09-11');
	});

	it('뒤 기간이 31일이 아니면(11일 + 30일) 비교다', () => {
		expect(isMergedGroup(pair(['2026-10-01', '2026-10-11'], ['2026-10-12', '2026-11-10']))).toBe(
			false
		);
	});

	it('같은 기간을 두 번 조회한 근거는 하나로 센다', () => {
		const [first] = evidenceCases.longSplit;
		const [steps] = groupEvidenceByMetric([first, { ...first, id: 'again', position: 9 }]);

		expect(isMergedGroup(steps)).toBe(false);
		expect(steps.current.id).toBe(first.id);
	});
});

describe('chart', () => {
	it('값과 평균이 들어가도록 눈금 단위로 올림한다', () => {
		expect(chartScale('sleeps', [312, 468, null], 393)).toEqual({
			max: 480,
			min: 0,
			ticks: [0, 120, 240, 360, 480]
		});
		expect(chartScale('steps', [], null)).toEqual({ max: 2000, min: 0, ticks: [0, 2000] });
	});

	it('가장 큰 값 위에 여유를 두지 않는다: 8시간 36분은 12h 가 아니라 9h 축이다(S15P31A403-404)', () => {
		expect(chartScale('sleeps', [387, 516, 184], 343).ticks).toEqual([0, 180, 360, 540]);
		// 값이 눈금과 같아도 한 칸 더 올리지 않는다
		expect(chartScale('sleeps', [540, 300], 420).max).toBe(540);
	});

	it('비교 기간 평균처럼 함께 그리는 기준선도 축 안에 들어간다(S15P31A403-404)', () => {
		expect(chartScale('sleeps', [300, 320], 310).max).toBe(360);
		expect(chartScale('sleeps', [300, 320], 310, [422]).max).toBe(480);
		expect(scaleFraction(chartScale('sleeps', [300, 320], 310, [422]), 422)).toBeLessThan(1);
	});

	it('눈금은 딱 떨어지는 값이다 (심박 33·67, 수면 2h·5h 같은 눈금이 나오지 않는다)', () => {
		expect(chartScale('heart_rates', [72, 80, 68], 74).ticks).toEqual([0, 20, 40, 60, 80]);
		expect(chartScale('heart_rates', [110, 102], 106).ticks).toEqual([0, 40, 80, 120]);
		expect(chartScale('sleeps', [352, 412, 380], 381).ticks).toEqual([0, 120, 240, 360, 480]);
		for (const metric of ['sleeps', 'steps', 'heart_rates', 'exercises'] as const) {
			const { ticks } = chartScale(metric, [123, 4567, 89], 1500);
			expect(ticks.length).toBeLessThanOrEqual(5);
			expect(ticks.every((tick) => Number.isInteger(tick))).toBe(true);
		}
	});

	it('산소포화도 축은 90에서 시작해 100%를 넘지 않는다(S15P31A403-388)', () => {
		expect(isLineChart('oxygen_saturations')).toBe(true);
		expect(isLineChart('heart_rates')).toBe(false);
		const usual = chartScale('oxygen_saturations', [96.5, 97.2, null], 96.8);
		expect(usual).toEqual({ max: 100, min: 90, ticks: [90, 95, 100] });
		expect(scaleFraction(usual, 90)).toBe(0);
		expect(scaleFraction(usual, 95)).toBe(0.5);
		expect(scaleFraction(usual, 100)).toBe(1);
		// 기록이 없어도 90~100 축을 둔다
		expect(chartScale('oxygen_saturations', [null], null).ticks).toEqual([90, 95, 100]);
	});

	it('산소포화도에 90보다 낮은 값이 있으면 그 값이 들어가게 축을 아래로 넓힌다', () => {
		expect(chartScale('oxygen_saturations', [96, 86], 91).ticks).toEqual([85, 90, 95, 100]);
		// 칸이 4개를 넘게 되면 간격을 10으로 늘린다
		expect(chartScale('oxygen_saturations', [96, 72], null).ticks).toEqual([70, 80, 90, 100]);
		// 축 밖의 값은 맨 아래·맨 위에 붙인다
		expect(scaleFraction(chartScale('oxygen_saturations', [96], null), 80)).toBe(0);
	});

	it('신체 지표 눈금: 체중 kg, 기초대사량 kcal, 키는 m 값을 cm 눈금으로 보여 준다(S15P31A403-311)', () => {
		expect(chartScale('weights', [68.4, 68.1, null], 68.2).ticks).toEqual([0, 20, 40, 60, 80]);
		expect(chartScale('basal_metabolic_rates', [1480, 1476], 1478).ticks).toEqual([
			0, 500, 1000, 1500
		]);
		expect(chartScale('body_fats', [24.1, 23.6], 23.9).max).toBeLessThanOrEqual(100);
		const heights = chartScale('heights', [1.725, 1.724], 1.72);
		expect(heights.ticks).toEqual([0, 0.5, 1, 1.5, 2]);
		expect(heights.ticks.map((tick) => formatAxisValue('heights', tick))).toEqual([
			'0',
			'50',
			'100',
			'150',
			'200'
		]);
	});

	it('축 값을 지표 단위로 표시한다', () => {
		expect(formatAxisValue('sleeps', 540)).toBe('9h');
		expect(formatAxisValue('steps', 10000)).toBe('10k');
		expect(formatAxisValue('steps', 2500)).toBe('2.5k');
	});

	it('막대 색은 평균 이상과 미만 두 가지다. 기간 최저값을 따로 칠하지 않는다(S15P31A403-404)', () => {
		expect(barTone(468, 393)).toBe('high');
		expect(barTone(393, 393)).toBe('high');
		expect(barTone(312, 393)).toBe('normal');
		expect(barTone(384, null)).toBe('normal');
	});

	it('막대 색은 값과 평균만으로 정한다. 수집 중인 날도 같은 규칙이다(S15P31A403-404)', () => {
		expect(barTone(20, 27)).toBe('normal');
		expect(barTone(40, 27)).toBe('high');
	});
});

describe('나눠 조회한 운동 근거의 횟수·칼로리·거리(S15P31A403-375)', () => {
	it('날짜별 값은 이어 붙이고 합계·평균은 다시 내지 않는다', () => {
		const [week] = evidenceCases.exerciseActivityWeek;
		const activity = week.snapshot.detail.exercise_activity!;
		const part = (
			id: string,
			position: number,
			start: string,
			end: string,
			from: number,
			to: number
		) => ({
			...week,
			id,
			position,
			periodStart: start,
			periodEnd: end,
			snapshot: {
				...week.snapshot,
				detail: {
					...week.snapshot.detail,
					exercise_activity: { ...activity, days: activity.days.slice(from, to) }
				}
			}
		});
		const [group] = groupEvidenceByMetric([
			part('a', 0, '2026-10-26', '2026-10-28', 4, 7),
			part('b', 1, '2026-10-24', '2026-10-25', 2, 4),
			part('c', 2, '2026-10-22', '2026-10-23', 0, 2)
		]);
		const merged = group.current.snapshot.detail.exercise_activity;

		expect(isMergedGroup(group)).toBe(true);
		expect(merged?.days.map((day) => day.date)).toEqual(activity.days.map((day) => day.date));
		expect(merged?.summary).toBeNull();
		// 한 기간이라도 수집에 실패했으면 표시를 남긴다
		expect(merged?.source_statuses.calories_burned?.last_sync_status).toBe('fail');
	});
});
