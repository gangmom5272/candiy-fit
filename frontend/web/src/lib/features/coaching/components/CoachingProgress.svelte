<script lang="ts">
	let { text }: { text: string } = $props();
</script>

<!-- 답변 작성 중·질문 보내는 중 같은 진행 안내. 세 점이 차례로 깜빡인다(움직임 줄이기 설정이면 멈춤) -->
<p
	class="flex w-fit items-center gap-2 rounded-full bg-primary-soft px-4 py-1.5 text-xs text-text-primary"
	role="status"
>
	<span class="flex gap-1" aria-hidden="true">
		<span class="size-2 animate-pulse rounded-full bg-brand motion-reduce:animate-none"></span>
		<span
			class="size-2 animate-pulse rounded-full bg-primary-light [animation-delay:150ms] motion-reduce:animate-none"
		></span>
		<span
			class="size-2 animate-pulse rounded-full bg-coral-sub [animation-delay:300ms] motion-reduce:animate-none"
		></span>
	</span>
	{text}
</p>
