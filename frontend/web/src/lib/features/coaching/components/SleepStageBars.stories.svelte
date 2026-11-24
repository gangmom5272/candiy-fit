<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { chartBars } from '../evidence-view';
	import { evidenceCases } from '../fixtures';
	import SleepStageBars from './SleepStageBars.svelte';

	const [week] = evidenceCases.sleepStagesWeek;
	const [month] = evidenceCases.sleepSpo2MonthClean;

	const { Story } = defineMeta({
		title: 'Features/coaching/SleepStageBars',
		component: SleepStageBars,
		tags: ['autodocs'],
		parameters: {
			docs: {
				description: {
					component:
						'날짜별 수면 단계를 쌓은 막대. 아래부터 깊은 잠(primary), 렘수면(coral-main), 얕은 잠(coral-sub). 깬 시간은 넣지 않는다. 단계 기록이 없는 날은 막대를 비운다. 구간에 마우스를 올리면 그 단계의 시간이 옆에 뜨고, 방향키로 구간을 옮길 수 있다. 일주일을 넘는 기간은 최근 7일만 먼저 보이고, 차트나 아래의 가는 막대를 끌어 앞의 날을 자유롭게 넘겨 보거나 기간 전체 보기로 바꾼다.'
				}
			}
		}
	});
</script>

<!-- 10.25 는 단계가 없는 워치 기록이라 막대가 비어 있다 -->
<Story
	name="7일 · 단계 없는 날 포함"
	exportName="Week"
	args={{
		bars: chartBars(week.snapshot).bars,
		nights: week.snapshot.detail.nights,
		label: '날짜별 수면 단계 막대 차트'
	}}
/>

<Story
	name="30일"
	exportName="Month"
	args={{
		bars: chartBars(month.snapshot).bars,
		nights: month.snapshot.detail.nights,
		label: '날짜별 수면 단계 막대 차트'
	}}
/>
