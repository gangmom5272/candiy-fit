<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import type { ComponentProps } from 'svelte';
	import HealthMetricCard from './HealthMetricCard.svelte';
	import footIcon from '#lib/assets/icons/icon-foot.svg';
	import sleepIcon from '#lib/assets/icons/icon-sleep.svg';
	import heartIcon from '#lib/assets/icons/icon-heart.svg';

	const { Story } = defineMeta({
		title: 'Features/home/HealthMetricCard',
		component: HealthMetricCard,
		tags: ['autodocs'],
		parameters: { layout: 'centered' },
		args: {
			metric: 'steps',
			icon: footIcon,
			day: { date: '2026-10-29', value: 3200, missing: false, reason: null, isInProgress: true }
		},
		render: template
	});
</script>

{#snippet template(args: ComponentProps<typeof HealthMetricCard>)}
	<div class="w-72 max-w-full">
		<HealthMetricCard {...args} />
	</div>
{/snippet}

<Story name="정상 · 오늘 걸음 수" exportName="Normal" />
<Story name="로딩" exportName="Loading" args={{ day: undefined }} />
<Story
	name="동기화 대기"
	exportName="Waiting"
	args={{
		day: { date: '2026-10-29', value: null, missing: true, reason: 'UNSYNCED', isInProgress: true }
	}}
/>
<Story
	name="기록 없음"
	exportName="Missing"
	args={{
		day: {
			date: '2026-10-28',
			value: null,
			missing: true,
			reason: 'NO_RECORD',
			isInProgress: false
		}
	}}
/>
<Story
	name="유효한 운동 0분"
	exportName="ZeroExercise"
	args={{
		metric: 'exercises',
		day: { date: '2026-10-29', value: 0, missing: false, reason: null, isInProgress: true }
	}}
/>
<Story
	name="조회 실패"
	exportName="Error"
	args={{
		day: undefined,
		notice: '건강 기록을 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.'
	}}
/>
<Story
	name="오늘 수면"
	exportName="Sleep"
	args={{
		metric: 'sleeps',
		icon: sleepIcon,
		day: { date: '2026-10-29', value: 420, missing: false, reason: null, isInProgress: false }
	}}
/>
<Story
	name="안정 시 심박수"
	exportName="Heart"
	args={{
		metric: 'heart_rates',
		icon: heartIcon,
		day: undefined,
		value: '64.5 bpm',
		isStatus: false
	}}
/>
