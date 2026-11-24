<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { chartBars } from '../evidence-view';
	import { evidenceCases, sleepAndStepsEvidence } from '../fixtures';
	import DailyBarChart from './DailyBarChart.svelte';

	const [sleep, , steps] = sleepAndStepsEvidence;
	const [month] = evidenceCases.month;
	const [quarter] = evidenceCases.quarter;
	const [heart] = evidenceCases.heartWeek;
	const [exercise] = evidenceCases.exerciseSessionsMonth;

	const { Story } = defineMeta({
		title: 'Features/coaching/DailyBarChart',
		component: DailyBarChart,
		tags: ['autodocs'],
		parameters: {
			docs: {
				description: {
					component:
						'평균 이상은 brand, 평균 미만은 coral-sub. 수집 중인 오늘도 같은 색 규칙의 채운 막대이고 막대 위에 "오늘"이라고 적는다. 가장 높은 날과 낮은 날은 색 대신 막대 위 꼬리표로 알린다. 비교 기간이 있으면 이전 기간 평균을 회색 실선으로 겹치고 선 끝에 이름과 값을 붙인다. 기록이 없는 날과 값이 0인 날(운동을 안 한 날)은 0으로 그리지 않고 점선 테두리 칸으로 표시한다. 막대가 7개를 넘으면 축 날짜는 6개 안팎만 보인다.'
				}
			}
		}
	});
</script>

<Story
	name="수면 · 기록 없는 날 포함"
	exportName="SleepWithMissingDays"
	args={{
		metric: 'sleeps',
		bars: chartBars(sleep.snapshot).bars,
		average: sleep.snapshot.summary.avg,
		label: '일별 수면 시간 막대 차트'
	}}
/>

<Story
	name="걸음"
	exportName="Steps"
	args={{
		metric: 'steps',
		bars: chartBars(steps.snapshot).bars,
		average: steps.snapshot.summary.avg,
		label: '일별 걸음 수 막대 차트'
	}}
/>

<Story
	name="심박 · 눈금"
	exportName="HeartRate"
	args={{
		metric: 'heart_rates',
		bars: chartBars(heart.snapshot).bars,
		average: heart.snapshot.summary.avg,
		label: '일별 평균 심박 막대 차트'
	}}
/>

<Story
	name="31일 · 오늘 수집 중"
	exportName="Month"
	args={{
		metric: 'steps',
		bars: chartBars(month.snapshot).bars,
		average: month.snapshot.summary.avg,
		label: '일별 걸음 수 막대 차트'
	}}
/>

<Story
	name="90일 · 주 단위"
	exportName="Quarter"
	args={{
		metric: 'steps',
		bars: chartBars(quarter.snapshot).bars,
		average: quarter.snapshot.summary.avg,
		label: '주별 평균 걸음 수 막대 차트'
	}}
/>

<Story
	name="기록 없음"
	exportName="NoRecords"
	args={{
		metric: 'sleeps',
		bars: chartBars({
			...sleep.snapshot,
			days: sleep.snapshot.days.map((day) => ({
				...day,
				value: null,
				missing: true,
				reason: 'NO_RECORD' as const
			}))
		}).bars,
		average: null,
		label: '일별 수면 시간 막대 차트'
	}}
/>

<!-- S15P31A403-404 최고·최저 꼬리표, 이전 기간 평균선 -->
<Story
	name="최고·최저 꼬리표"
	exportName="Extremes"
	args={{
		metric: 'steps',
		bars: chartBars(steps.snapshot).bars,
		average: steps.snapshot.summary.avg,
		extremes: {
			max: {
				key: steps.snapshot.summary.daily_max?.date ?? '',
				value: steps.snapshot.summary.daily_max?.value ?? 0
			},
			min: {
				key: steps.snapshot.summary.daily_min?.date ?? '',
				value: steps.snapshot.summary.daily_min?.value ?? 0
			}
		},
		label: '일별 걸음 수 막대 차트'
	}}
/>

<Story
	name="이전 기간 평균선"
	exportName="PreviousAverage"
	args={{
		metric: 'sleeps',
		bars: chartBars(sleep.snapshot).bars,
		average: sleep.snapshot.summary.avg,
		previous: { average: 320, delta: '+1시간 13분', reference: false },
		label: '일별 수면 시간 막대 차트'
	}}
/>

<Story
	name="이전 기간 평균선 · 두 선이 가까움"
	exportName="PreviousAverageClose"
	args={{
		metric: 'sleeps',
		bars: chartBars(sleep.snapshot).bars,
		average: sleep.snapshot.summary.avg,
		previous: { average: 385, delta: '+8분', reference: true },
		label: '일별 수면 시간 막대 차트'
	}}
/>

<!-- 운동을 안 한 0분인 날이 21일. 점선 테두리 칸으로 보여 조금 운동한 날과 구분된다 -->
<Story
	name="운동 30일 · 0분인 날 포함"
	exportName="ExerciseWithZeroDays"
	args={{
		metric: 'exercises',
		bars: chartBars(exercise.snapshot).bars,
		average: exercise.snapshot.summary.avg,
		label: '일별 운동 시간 막대 차트'
	}}
/>
