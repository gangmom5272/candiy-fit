<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { fn } from 'storybook/test';
	import ConversationListToggle from './ConversationListToggle.svelte';

	const { Story } = defineMeta({
		title: 'Features/coaching/ConversationListToggle',
		component: ConversationListToggle,
		tags: ['autodocs'],
		args: { controls: 'story-list', ontoggle: fn() },
		parameters: {
			docs: {
				description: {
					component:
						'대화 목록 열기·접기 버튼. 열 때와 접을 때 같은 아이콘·같은 자리에 두고 이름과 aria-expanded 만 바뀐다(ChatGPT·Claude 사이드바 버튼과 같은 방식).'
				}
			}
		}
	});
</script>

<Story name="열기(목록 접힘)" exportName="Collapsed" args={{ expanded: false }} />

<Story name="접기(목록 펼침)" exportName="Expanded" args={{ expanded: true }} />
