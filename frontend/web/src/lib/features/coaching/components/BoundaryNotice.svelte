<script lang="ts">
	import type { BoundaryNoticeContent } from '../types';
	import SuggestedQuestions from './SuggestedQuestions.svelte';

	type Props = {
		content: BoundaryNoticeContent;
		onselect?: (question: string) => void;
	};

	let { content, onselect }: Props = $props();
</script>

<div class="flex flex-col gap-6 break-keep">
	<section class="rounded-2xl border border-coral-sub bg-primary-soft px-9 py-8">
		<h2 class="text-section-title text-text-primary">{content.title}</h2>
		<ul class="mt-5 flex flex-col gap-3 text-base text-text-primary">
			{#each content.lines as line (line)}
				<li>{line}</li>
			{/each}
		</ul>
		<hr class="my-6 border-coral-sub" />
		<p class="text-base font-bold text-text-primary">{content.alternativeTitle}</p>
		<p class="mt-2 text-[15px] text-text-primary">{content.alternativeBody}</p>
	</section>

	{#if content.suggestions.length > 0}
		<SuggestedQuestions
			title="이렇게 물어볼 수 있어요."
			questions={content.suggestions}
			variant="prominent"
			{onselect}
		/>
	{/if}

	<p class="flex items-center gap-2 text-[13px] text-text-secondary">
		<span
			class="grid size-5 place-items-center rounded-full bg-primary-soft text-[11px] font-bold text-primary"
			aria-hidden="true">i</span
		>
		질병 판단과 약 복용 안내는 전문가 상담이 필요한 영역이에요.
	</p>
</div>
