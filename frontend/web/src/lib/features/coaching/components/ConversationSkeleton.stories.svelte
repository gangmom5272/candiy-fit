<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import ConversationSkeleton from './ConversationSkeleton.svelte';
	import ConversationListSkeleton from './ConversationListSkeleton.svelte';

	// 대화 화면이 지난 메시지를 불러오는 동안 대화 로그 자리에 두는 스켈레톤(질문·답변 두 쌍)
	const { Story } = defineMeta({
		title: 'Features/coaching/ConversationSkeleton',
		component: ConversationSkeleton,
		tags: ['autodocs']
	});
</script>

<Story name="지난 메시지 불러오는 중" exportName="History">
	<div class="max-w-[52rem] rounded-3xl border border-border bg-card px-6 py-6 md:px-9">
		<ConversationSkeleton />
	</div>
</Story>

<!-- 대화 목록을 처음 불러오는 동안 목록 자리에 두는 스켈레톤. ConversationList의 loading 상태가 쓴다 -->
<Story name="대화 목록 불러오는 중" exportName="List">
	<div class="w-67 rounded-3xl border border-border bg-card">
		<ConversationListSkeleton />
	</div>
</Story>
