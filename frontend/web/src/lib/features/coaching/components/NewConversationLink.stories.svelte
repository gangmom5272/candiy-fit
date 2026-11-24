<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import type { ComponentProps } from 'svelte';
	import NewConversationLink from './NewConversationLink.svelte';

	// 대화 목록 맨 위(펼침)와 접힌 레일(아이콘만)에 두는 새 대화 링크. 코칭 첫 화면에는 두지 않는다
	const { Story } = defineMeta({
		title: 'Features/coaching/NewConversationLink',
		component: NewConversationLink,
		tags: ['autodocs'],
		render: template
	});
</script>

{#snippet template(args: ComponentProps<typeof NewConversationLink>)}
	<!-- 목록 카드 안 자리와 같은 폭·배경 -->
	<div class="w-67 rounded-3xl border border-border bg-card p-4">
		<div class="flex flex-col">
			<NewConversationLink {...args} />
		</div>
	</div>
{/snippet}

<Story name="펼친 목록 (글자 포함)" exportName="Default" />

<!-- 접힌 레일에서는 아이콘만 두고 이름은 aria-label(새 대화)로 읽힌다 -->
<Story name="접힌 레일 (아이콘만)" exportName="Compact" args={{ compact: true }} />
