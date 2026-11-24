<script lang="ts">
	import AssistantMessage from '../features/coaching/components/AssistantMessage.svelte';
	import ConversationListToggle from '../features/coaching/components/ConversationListToggle.svelte';
	import ConversationSidebar from '../features/coaching/components/ConversationSidebar.svelte';
	import EvidenceDialog from '../features/coaching/components/EvidenceDialog.svelte';
	import QuestionInput from '../features/coaching/components/QuestionInput.svelte';
	import UserMessage from '../features/coaching/components/UserMessage.svelte';
	import type { ConversationFixture } from '../features/coaching/fixtures';
	import type { Evidence } from '../features/coaching/types';

	/**
	 * Storybook 전용 코칭 대화 화면 조합. 실제 화면(routes/(app)/coaching/[conversationId]/+page.svelte)은
	 * 소켓·API·전역 상태를 써서 Storybook에 그대로 못 띄운다. 그 화면의 배치를 옮기고 데이터만 픽스처로 넣는다.
	 * 화면 배치(클래스)를 바꾸면 여기도 같이 바꾼다.
	 */
	type Props = {
		fixture: ConversationFixture;
		items: ConversationFixture['conversation'][];
		/** 마지막 답변에 붙일 상태. 없으면 저장된 답변 그대로 */
		lastAnswer?: { progressText?: string; errorMessage?: string; retryable?: boolean };
		isGenerating?: boolean;
		question?: string;
	};

	let { fixture, items, lastAnswer, isGenerating = false, question = '' }: Props = $props();

	const LIST_ID = 'story-coaching-list';
	let isListOpen = $state(true);
	let isDrawerOpen = $state(false);
	// 스토리 Controls에서 question을 바꾸면 입력창에도 반영하고, 입력하면 그 값으로 바뀐다
	let value = $derived(question);
	let dialogEvidence: Evidence[] = $state([]);
	let isDialogOpen = $state(false);
	const lastIndex = $derived(fixture.messages.length - 1);
</script>

<main
	class={[
		'mx-auto grid w-full max-w-[1792px] gap-8 px-5 py-9 break-keep sm:px-8',
		'lg:h-dvh lg:min-h-[36rem] lg:grid-cols-[auto_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)]'
	]}
>
	<ConversationSidebar
		id={LIST_ID}
		{items}
		activeId={fixture.conversation.id}
		emptyText="아직 대화가 없어요."
		bind:open={isListOpen}
		bind:drawerOpen={isDrawerOpen}
	/>

	<section class="flex min-h-0 min-w-0 flex-col rounded-3xl border border-border bg-card">
		<header class="px-6 pt-7 md:px-9 lg:pt-4">
			<div class="flex items-center gap-3 lg:h-10">
				<ConversationListToggle
					class="lg:hidden"
					controls="{LIST_ID}-drawer"
					expanded={isDrawerOpen}
					ontoggle={() => (isDrawerOpen = true)}
				/>
				<h1 class="text-[28px] font-bold">AI 코치</h1>
			</div>
			<p class="mt-2 truncate border-b border-border pb-5 text-sm text-text-secondary">
				{fixture.conversation.title}
			</p>
		</header>

		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<div
			class="flex flex-1 flex-col px-6 pt-6 pb-8 md:px-9 lg:min-h-0 lg:overflow-y-auto"
			role="log"
			aria-label="대화 내용"
			tabindex="0"
		>
			<div class="flex flex-col gap-6">
				{#each fixture.messages as message, index (message.id)}
					{#if message.role === 'user'}
						<UserMessage content={message.content} />
					{:else}
						{@const state = index === lastIndex ? lastAnswer : undefined}
						<AssistantMessage
							{message}
							extras={fixture.extras[message.id]}
							progressText={state?.progressText}
							errorMessage={state?.errorMessage}
							onretry={state?.retryable ? () => {} : undefined}
							onquestion={(text) => (value = text)}
							onopenevidence={() => {
								dialogEvidence = message.evidence;
								isDialogOpen = true;
							}}
						/>
					{/if}
				{/each}
			</div>
		</div>

		<div
			class="sticky bottom-0 flex flex-col gap-4 rounded-b-3xl bg-card px-6 pt-2 pb-6 md:px-9 lg:static"
		>
			<QuestionInput bind:value {isGenerating} onsubmit={() => (value = '')} />
			<p class="text-xs text-text-secondary">
				AI 코치는 생활 습관 참고용이며, 진단이나 처방을 제공하지 않아요.
			</p>
		</div>
	</section>

	<EvidenceDialog evidence={dialogEvidence} bind:open={isDialogOpen} />
</main>
