<script lang="ts">
	import type { Snippet } from 'svelte';
	import QuestionInput from './QuestionInput.svelte';

	type Props = {
		onsubmit?: (content: string) => void;
		/** 질문을 보내는 중이면 입력을 잠근다 */
		isGenerating?: boolean;
		/** 입력창 바로 아래 안내(전송 중·오류). 레이아웃 높이를 바꾸지 않도록 겹쳐 띄운다 */
		notice?: Snippet;
	};

	let { onsubmit, isGenerating = false, notice }: Props = $props();

	const EXAMPLES = [
		{
			label: '변화 확인',
			question: '최근 수면 시간이 어떻게 달라졌어?',
			description: '이전 기간과 수면 기록 비교'
		},
		{
			label: '지표 함께 보기',
			question: '잠을 짧게 잔 날 심박수는 어땠어?',
			description: '같은 날짜의 수면과 심박 기록 확인'
		},
		{
			label: '활동량 비교',
			question: '지난주보다 얼마나 더 걸었어?',
			description: '일평균과 총 걸음 수 비교'
		}
	];

	let question = $state('');

	/** 예시는 입력창에 넣지 않고 바로 질문한다. 보내는 중이면 다시 보내지 않는다 */
	function askExample(content: string) {
		if (isGenerating || !onsubmit) return;
		onsubmit(content);
	}
</script>

<!-- lg 이상 세로 간격은 화면이 정한 CSS 변수(--welcome-*)를 따르고, 없으면 시안 값을 쓴다 -->
<section
	class="mx-auto flex w-full max-w-[80rem] flex-col items-center gap-14 break-keep lg:gap-(--welcome-gap,3.5rem)"
	aria-labelledby="coaching-welcome-title"
>
	<div class="text-center">
		<h2 id="coaching-welcome-title" class="text-[40px] font-bold">무엇이 궁금하세요?</h2>
		<p class="mt-3 text-[15px] text-text-secondary">
			질문 예시를 누르면 바로 질문하고, 아래 입력창에 직접 질문할 수도 있어요.
		</p>
	</div>

	<div class="relative w-full">
		<QuestionInput bind:value={question} size="large" {isGenerating} {onsubmit} />
		{#if notice}
			<div class="absolute inset-x-0 top-full z-10 mt-2 flex justify-center">
				{@render notice()}
			</div>
		{/if}
	</div>

	<ul class="grid w-full max-w-[70rem] gap-5 md:grid-cols-3">
		{#each EXAMPLES as example (example.question)}
			<li>
				<!-- 누르면 바로 질문한다는 것을 커서·테두리·"바로 질문하기" 안내로 보여 준다 -->
				<button
					type="button"
					class="group flex h-full min-h-40 w-full cursor-pointer flex-col gap-4 rounded-lg border border-border bg-card px-6 py-6 text-left shadow-[0_2px_2px_rgb(0_0_0/0.08)] transition hover:-translate-y-0.5 hover:border-primary-light hover:shadow-[0_6px_16px_rgb(0_0_0/0.08)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0 disabled:hover:border-border disabled:hover:shadow-[0_2px_2px_rgb(0_0_0/0.08)] motion-reduce:transition-none motion-reduce:hover:translate-y-0 lg:min-h-(--welcome-example-height,10rem)"
					disabled={isGenerating}
					onclick={() => askExample(example.question)}
				>
					<span class="flex items-center justify-between gap-2 text-[13px] text-text-secondary">
						{example.label}
						<span
							class="font-semibold text-primary opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 group-disabled:opacity-0"
							aria-hidden="true">바로 질문하기 →</span
						>
					</span>
					<span class="text-lg font-medium text-text-primary">{example.question}</span>
					<span class="text-sm text-text-secondary">{example.description}</span>
				</button>
			</li>
		{/each}
	</ul>
</section>
