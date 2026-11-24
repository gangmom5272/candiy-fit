<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { fn } from 'storybook/test';
	import SuggestedQuestions from './SuggestedQuestions.svelte';

	const { Story } = defineMeta({
		title: 'Features/coaching/SuggestedQuestions',
		component: SuggestedQuestions,
		tags: ['autodocs'],
		args: { onselect: fn() },
		argTypes: {
			variant: { control: 'inline-radio', options: ['compact', 'prominent'] }
		}
	});
</script>

<Story
	name="이어서 물어보기"
	exportName="FollowUp"
	args={{
		title: '이어서 물어보기',
		questions: ['활동량이 많던 날 심박수는?', '수면 패턴 더 자세히 보기']
	}}
/>

<Story
	name="경계 안내 아래"
	exportName="Prominent"
	args={{
		title: '이렇게 물어볼 수 있어요.',
		questions: ['최근 수면 패턴을 알려줘', '활동량 변화를 확인해줘'],
		variant: 'prominent'
	}}
/>
