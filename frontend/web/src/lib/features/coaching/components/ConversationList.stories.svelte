<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import type { ComponentProps } from 'svelte';
	import { fn } from 'storybook/test';
	import { conversations, FIXTURE_NOW } from '../fixtures';
	import ConversationList from './ConversationList.svelte';

	const { Story } = defineMeta({
		title: 'Features/coaching/ConversationList',
		component: ConversationList,
		tags: ['autodocs'],
		args: { items: conversations, now: FIXTURE_NOW },
		render: template
	});
</script>

{#snippet template(args: ComponentProps<typeof ConversationList>)}
	<div class="h-[40rem] w-67">
		<ConversationList {...args} />
	</div>
{/snippet}

<Story
	name="답변 완료 대화 선택"
	exportName="CompletedSelected"
	args={{ activeId: conversations[0].id }}
/>

<Story
	name="답변 작성 중 대화 선택"
	exportName="GeneratingSelected"
	args={{ activeId: conversations[1].id }}
/>

<Story name="선택 없음" exportName="NoneSelected" args={{ activeId: null }} />

<!-- 코칭 첫 화면은 가운데 입력창이 곧 새 대화라 목록 맨 위 새 대화 버튼을 두지 않는다 -->
<Story
	name="코칭 첫 화면 (새 대화 버튼 없음)"
	exportName="NewConversation"
	args={{ activeId: null, isNewConversation: true }}
/>

<Story
	name="접기 버튼 포함"
	exportName="Collapsible"
	args={{ activeId: conversations[0].id, oncollapse: fn(), controls: 'story-list' }}
/>

<Story name="빈 목록" exportName="Empty" args={{ items: [], emptyText: '아직 대화가 없어요.' }} />

<Story
	name="불러오기 실패"
	exportName="LoadFailed"
	args={{ items: [], emptyText: '대화 목록을 불러오지 못했어요.', oncollapse: fn() }}
/>

<!-- 대화가 한 페이지(20개)보다 많으면 목록 끝에 빈 칸이 생기고, 그 칸까지 내리면 20개를 더 읽는다 -->
<Story
	name="이전 대화 더 있음"
	exportName="HasMore"
	args={{ activeId: null, more: { hasMore: true, status: 'idle', load: fn() } }}
/>

<Story
	name="이전 대화 불러오는 중 (동그라미)"
	exportName="LoadingMore"
	args={{ activeId: null, more: { hasMore: true, status: 'loading', load: fn() } }}
/>

<!-- 이어 읽기가 실패해도 보이던 목록은 그대로다. 자동으로 다시 읽지 않고 버튼으로 다시 시도한다 -->
<Story
	name="이전 대화 불러오기 실패"
	exportName="LoadMoreFailed"
	args={{ activeId: null, more: { hasMore: true, status: 'error', load: fn() } }}
/>

<!-- 목록을 처음 불러오는 동안은 "아직 대화가 없어요" 대신 항목 모양의 스켈레톤을 둔다 -->
<Story
	name="목록 처음 불러오는 중 (스켈레톤)"
	exportName="LoadingInitial"
	args={{ items: [], activeId: null, loading: true, emptyText: '아직 대화가 없어요.' }}
/>
