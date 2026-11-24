<script lang="ts">
	import { resolve } from '$app/paths';
	import { Button } from '#lib/components/ui/button/index.js';
	import { formatConversationTime, REQUEST_STATUS_LABELS } from '../format';
	import type { ConversationItem } from '../types';
	import ConversationListSkeleton from './ConversationListSkeleton.svelte';
	import ConversationListToggle from './ConversationListToggle.svelte';
	import NewConversationLink from './NewConversationLink.svelte';

	type Props = {
		items: ConversationItem[];
		activeId?: string | null;
		/** 상대 시각 기준. 지정하지 않으면 현재 시각 */
		now?: Date;
		/** 있으면 제목 왼쪽에 접기 버튼을 둔다. 열기 버튼과 같은 모양·같은 자리다 */
		oncollapse?: () => void;
		/** 접기 버튼이 가리키는 목록 요소의 id */
		controls?: string;
		/** 목록을 처음 불러오는 중. 항목이 아직 없으면 안내 대신 자리 잡는 상자(스켈레톤)를 보여 준다 */
		loading?: boolean;
		/** 항목이 없을 때 보여 줄 안내(빈 목록·불러오기 실패) */
		emptyText?: string;
		/** 있으면 안내 아래에 다시 불러오기 버튼을 둔다(목록 불러오기 실패) */
		onretry?: () => void;
		/** 지금 새 대화(코칭 첫 화면)에 있는지. 가운데 입력창과 겹치므로 새 대화 버튼을 두지 않는다 */
		isNewConversation?: boolean;
		/**
		 * 이전 대화 이어 읽기(명세 6.2 nextCursor). 목록 끝까지 내리면 자동으로 읽고 그동안 동그라미가 돈다.
		 * 실패하면 그 자리에 다시 불러오기 버튼을 둔다
		 */
		more?: { hasMore: boolean; status: 'idle' | 'loading' | 'error'; load: () => void };
	};

	let {
		items,
		activeId = null,
		now,
		oncollapse,
		controls = '',
		loading = false,
		emptyText,
		onretry,
		isNewConversation = false,
		more
	}: Props = $props();

	const titleId = $props.id();
	// 목록 안 다시 불러오기 버튼(처음 불러오기 실패·이어 읽기 실패 공통). 보조색이었다가 올리면 주요색이 된다
	const RETRY_BUTTON = 'text-muted-foreground hover:text-foreground';

	/** 휠 한 번 굴림에 이벤트가 여러 번 오므로, 읽기를 시작한 뒤 잠시는 휠로 다시 읽지 않는다 */
	const WHEEL_PAUSE_MS = 1000;
	let lastLoadAt = 0;

	/**
	 * 목록 맨 아래에 닿았을 때 한 번에 20개만 더 읽는다. 미리 읽거나 연달아 읽지 않는다.
	 * 읽은 20개가 아래에 붙으면 스크롤 위치는 그대로라 맨 아래가 아니게 되고, 다시 맨 아래까지 내려야 다음 20개를 읽는다.
	 * 읽는 중·실패 상태에서는 부르지 않는다(실패하면 다시 불러오기 버튼으로 맡긴다)
	 */
	function loadMoreAtBottom(list: HTMLElement) {
		if (!more?.hasMore || more.status !== 'idle') return;
		if (list.scrollHeight - list.scrollTop - list.clientHeight > 2) return;
		lastLoadAt = Date.now();
		more.load();
	}

	/** 목록이 짧아 스크롤이 생기지 않을 때도 아래로 굴리면 읽는다 */
	function onwheel(event: WheelEvent & { currentTarget: HTMLElement }) {
		if (event.deltaY <= 0 || Date.now() - lastLoadAt < WHEEL_PAUSE_MS) return;
		loadMoreAtBottom(event.currentTarget);
	}
</script>

<!-- overflow-clip: 안쪽 목록(ul)이 스크롤될 때 스크롤바 트랙이 둥근 모서리 밖으로 삐져나오지 않게 카드 모양대로 자른다 -->
<nav
	class="flex h-full min-h-0 flex-col overflow-clip rounded-3xl border border-border bg-card"
	aria-labelledby={titleId}
>
	<!-- 버튼 자리(위 py-4·왼쪽 pl-3)는 접었을 때의 레일(ConversationSidebar)과 같아 열고 닫아도 버튼이 움직이지 않는다 -->
	<div
		class={[
			'flex items-center gap-2 border-b border-border py-4 pr-4',
			oncollapse ? 'pl-3' : 'pl-6'
		]}
	>
		{#if oncollapse}
			<ConversationListToggle {controls} expanded ontoggle={oncollapse} />
		{/if}
		<h2 id={titleId} class="text-card-title-lg whitespace-nowrap">대화 목록</h2>
	</div>
	{#if !isNewConversation}
		<div class="flex flex-col px-4 pt-4">
			<NewConversationLink />
		</div>
	{/if}
	{#if loading && items.length === 0}
		<ConversationListSkeleton />
	{:else if items.length === 0 && emptyText}
		<div class="flex flex-col items-start gap-2 p-6">
			<p class="text-sm leading-relaxed break-keep text-text-secondary">{emptyText}</p>
			{#if onretry}
				<Button variant="ghost" size="sm" class={RETRY_BUTTON} onclick={onretry}
					>다시 불러오기</Button
				>
			{/if}
		</div>
	{/if}
	<!-- 스크롤바 길을 둥근 카드 모서리만큼 줄이고 바탕을 비워 카드 밖으로 튀어나와 보이지 않게 한다(S15P31A403-438, 대화 상자와 같다) -->
	<ul
		class="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-4 [&::-webkit-scrollbar-track]:my-6 [&::-webkit-scrollbar-track]:bg-transparent"
		onscroll={(event) => loadMoreAtBottom(event.currentTarget)}
		{onwheel}
	>
		{#each items as item (item.id)}
			{@const isActive = item.id === activeId}
			<li>
				<a
					href={resolve('/(app)/coaching/[conversationId]', { conversationId: item.id })}
					class={[
						'relative flex flex-col gap-2 rounded-xl px-3 py-3 transition-colors',
						isActive ? 'bg-primary-soft' : 'hover:bg-surface'
					]}
					aria-current={isActive ? 'page' : undefined}
				>
					{#if isActive}
						<span
							class="absolute top-0 bottom-0 -left-1 w-1 rounded-full bg-brand"
							aria-hidden="true"
						></span>
					{/if}
					<span class="line-clamp-2 text-sm font-bold text-text-primary">{item.title}</span>
					<span class="text-xs text-text-secondary">
						{formatConversationTime(item.updatedAt, now)}
						{#if isActive}
							· {REQUEST_STATUS_LABELS[item.lastRequestStatus]}
						{/if}
					</span>
				</a>
			</li>
		{/each}
		{#if items.length > 0 && more?.hasMore}
			<!-- 높이를 고정해 대기(빈 칸) ↔ 불러오는 중(동그라미) ↔ 실패(다시 불러오기)로 바뀌어도 목록이 튀지 않는다 -->
			<li class="flex min-h-14 flex-col items-center justify-center gap-1 pb-2">
				{#if more.status === 'loading'}
					<span role="status" class="flex items-center justify-center">
						<svg
							class="size-6 animate-spin motion-reduce:animate-[spin_1.6s_linear_infinite]"
							viewBox="0 0 24 24"
							fill="none"
							aria-hidden="true"
						>
							<circle cx="12" cy="12" r="9" class="stroke-primary-soft" stroke-width="2.5" />
							<path
								d="M21 12a9 9 0 0 0-9-9"
								class="stroke-brand"
								stroke-width="2.5"
								stroke-linecap="round"
							/>
						</svg>
						<span class="sr-only">이전 대화를 불러오는 중이에요</span>
					</span>
				{:else if more.status === 'error'}
					<p class="text-xs text-text-secondary">이전 대화를 불러오지 못했어요.</p>
					<Button variant="ghost" size="sm" class={RETRY_BUTTON} onclick={more.load}
						>다시 불러오기</Button
					>
				{/if}
			</li>
		{/if}
	</ul>
</nav>
