<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import HealthCoachingCta from './HealthCoachingCta.svelte';

	const { Story } = defineMeta({
		title: 'Features/health/HealthCoachingCta',
		component: HealthCoachingCta
	});
</script>

<Story
	name="Home"
	args={{
		title: '내 건강에 맞는 생활 습관, AI 코치와 함께 찾아볼까요?',
		description: '건강 데이터를 확인했다면 AI 코치에게 궁금한 점을 물어보세요.',
		buttonLabel: 'AI 코치로 이동'
	}}
/>
<Story
	name="Activity"
	args={{
		title: '기록에서 궁금한 점을 발견했나요?',
		description: '선택한 기간의 기록을 AI 코치에게 물어보세요.',
		buttonLabel: '내 활동에 대해 물어보기'
	}}
/>
<Story
	name="Sleep"
	args={{
		title: '기록에서 궁금한 점을 발견했나요?',
		description: 'AI 코치에게 수면 기록에 대해 궁금한 점을 물어보세요.',
		buttonLabel: 'AI 코치로 이동'
	}}
/>
<Story
	name="Heart"
	args={{
		title: '기록에서 궁금한 점을 발견했나요?',
		description: 'AI 코치에게 심박 기록에 대해 궁금한 점을 물어보세요.',
		buttonLabel: 'AI 코치로 이동'
	}}
/>
