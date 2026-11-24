<script lang="ts">
	import { resolve } from '$app/paths';
	import characterCoach from '#lib/assets/images/character-coach.webp';
	import { parseMarkdown, splitAnswer, withCursor } from '../markdown';
	import type { CoachingMessage, PendingAnswerExtras } from '../types';
	import BoundaryNotice from './BoundaryNotice.svelte';
	import CoachingErrorNotice from './CoachingErrorNotice.svelte';
	import CoachingProgress from './CoachingProgress.svelte';
	import EvidenceSummary from './EvidenceSummary.svelte';
	import MarkdownBlocks from './MarkdownBlocks.svelte';
	import MarkdownInline from './MarkdownInline.svelte';
	import SuggestedQuestions from './SuggestedQuestions.svelte';

	type Props = {
		message: CoachingMessage;
		extras?: PendingAnswerExtras;
		onquestion?: (question: string) => void;
		onopenevidence?: () => void;
		/** 답변 대기 중 문구(진행 단계 등). 없으면 기본 문구 */
		progressText?: string;
		/** 실패·미접수 안내. 부분 답변은 그대로 두고 아래에 보여 준다 */
		errorMessage?: string;
		/** 있으면 실패 안내 옆에 다시 질문하기 버튼을 보여 준다. 실패 뒤 다시 묻기는 새 질문이다(명세 범위) */
		onretry?: () => void;
	};

	let {
		message,
		extras = {},
		onquestion,
		onopenevidence,
		progressText = '답변 작성 중',
		errorMessage,
		onretry
	}: Props = $props();

	const isGenerating = $derived(
		message.requestStatus === 'accepted' || message.requestStatus === 'generating'
	);
	/**
	 * 답변은 Markdown(문단·목록·소제목·표)으로 그린다. 첫 블록이 일반 문단이면 그 첫 줄을 요약으로 조금 크게 보여 주고,
	 * 목록·소제목 등으로 시작하면 요약 없이 본문만 둔다. 스트리밍 중에는 덜 쓴 기호를 감추고 끝에 입력 표시를 붙인다.
	 */
	const answer = $derived.by(() => {
		const parts = splitAnswer(parseMarkdown(message.content, { streaming: isGenerating }));
		return isGenerating ? withCursor(parts) : parts;
	});
</script>

<!-- 한국어는 단어 중간에서 줄을 바꾸지 않는다(break-keep). 띄어쓰기 없이 긴 글만 칸 끝에서 끊는다 -->
<article class="flex flex-col gap-6 wrap-break-word break-keep" aria-busy={isGenerating}>
	<!--
		코치 캐릭터는 연분홍 반투명 원 안에 조금 크게 둔다(캐릭터가 코칭하는 느낌, S15P31A403-368).
		원의 왼쪽 끝을 아래 답변 글·근거 카드와 같은 위치(pl-9)에 맞춘다
	-->
	<div class="flex items-center gap-3 pl-9">
		<!-- 캐릭터는 꾸밈이라 드래그 선택·복사에서 뺀다(S15P31A403-438, copy.ts) -->
		<span
			class="grid size-14 shrink-0 place-items-center rounded-full bg-primary-soft/80 ring-1 ring-coral-soft select-none"
			data-copy-skip
		>
			<img src={characterCoach} alt="AI 코치" class="size-12 object-contain" draggable="false" />
		</span>
		{#if isGenerating}
			<CoachingProgress text={progressText} />
		{/if}
	</div>

	{#if extras.boundary}
		<div class="pl-9">
			<BoundaryNotice content={extras.boundary} onselect={onquestion} />
		</div>
	{:else if message.content}
		<div class="flex flex-col gap-3 pl-9">
			{#if answer.headline.length}
				<p class="text-[17px] leading-normal font-semibold">
					<MarkdownInline nodes={answer.headline} />
				</p>
			{/if}
			{#if answer.body.length}
				<div class="flex flex-col gap-3 text-base leading-[1.7] text-text-primary">
					<MarkdownBlocks blocks={answer.body} />
				</div>
			{/if}
		</div>
	{/if}

	{#if errorMessage}
		<CoachingErrorNotice class="ml-9" message={errorMessage} {onretry} retryLabel="다시 질문하기" />
	{/if}

	{#if !isGenerating && message.evidence.length > 0}
		<!--
			근거 카드는 답변 글과 같은 위치에서 시작한다. 들여 쓰지 않으면 카드만 왼쪽으로 튀어나와 보였다.
			휴대폰 폭에서는 들여 쓰지 않는다. 카드 폭이 줄어 글자를 200%로 키웠을 때 기간이 카드 밖으로 나갔다
		-->
		<div class="sm:pl-9">
			<EvidenceSummary evidence={message.evidence} onopen={onopenevidence} />
		</div>
	{/if}

	{#if !isGenerating && extras.followUpQuestions?.length}
		<div class="sm:pl-9">
			<SuggestedQuestions
				title="이어서 물어보기"
				questions={extras.followUpQuestions}
				onselect={onquestion}
				trailing={message.evidence.length > 0 ? healthLink : undefined}
			/>
		</div>
	{/if}
</article>

{#snippet healthLink()}
	<a
		href={resolve('/(app)/health')}
		class="flex items-center gap-2 text-sm font-semibold text-text-secondary hover:text-text-primary"
	>
		건강 데이터 보러가기
		<svg
			class="size-5"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			stroke-width="2"
			aria-hidden="true"
		>
			<path d="M5 12h14m-6-6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round" />
		</svg>
	</a>
{/snippet}
