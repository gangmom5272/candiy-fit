<script lang="ts">
	import type { Snippet } from 'svelte';

	type Props = {
		title: string;
		questions: string[];
		/** `prominent`: 경계 안내 아래의 큰 버튼, `compact`: 답변 아래 이어서 물어보기 */
		variant?: 'prominent' | 'compact';
		onselect?: (question: string) => void;
		/** 질문 버튼 줄 오른쪽 끝에 같은 세로 기준으로 놓을 내용 (예: 건강 데이터 보러가기 링크) */
		trailing?: Snippet;
	};

	let { title, questions, variant = 'compact', onselect, trailing }: Props = $props();
</script>

<section class="flex flex-col gap-3 break-keep" aria-label={title}>
	<h2 class={variant === 'prominent' ? 'text-card-title-lg' : 'text-sm font-semibold'}>{title}</h2>
	<div class="flex flex-wrap items-center gap-3">
		<ul class="flex flex-wrap gap-3">
			{#each questions as question (question)}
				<li>
					<button
						type="button"
						class={[
							'rounded-xl border border-border bg-card font-semibold text-text-primary transition-colors hover:border-primary-light hover:bg-primary-soft',
							variant === 'prominent'
								? 'min-w-56 px-8 py-4 text-sm'
								: 'min-w-52 px-6 py-2.5 text-sm'
						]}
						onclick={() => onselect?.(question)}
					>
						{question}
					</button>
				</li>
			{/each}
		</ul>
		{#if trailing}
			<div class="ml-auto">{@render trailing()}</div>
		{/if}
	</div>
</section>
