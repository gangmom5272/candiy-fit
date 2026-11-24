<script lang="ts">
	import Skeleton from '#lib/components/feedback/Skeleton.svelte';

	// 답변 글줄 폭. 실제 답변처럼 요약 한 줄 + 본문 세 줄
	const LINES = ['h-6 w-2/3 max-w-[22rem]', 'h-4 w-full', 'h-4 w-11/12', 'h-4 w-3/4'];
</script>

<!--
	지난 메시지를 불러오는 동안 질문·답변 두 쌍 모양으로 자리를 잡는다.
	질문 말풍선은 UserMessage(오른쪽·2xl 모서리, 흔한 짧은 질문 폭), 답변은 AssistantMessage(캐릭터 pl-7·본문 pl-9)와 같은 자리·여백이라
	불러온 뒤 대화가 같은 자리에 들어온다. 대화 로그(gap-6) 안에 바로 둔다.
-->
<div role="status" aria-label="이전 대화를 불러오고 있어요." class="flex flex-col gap-6">
	<span class="sr-only">이전 대화를 불러오고 있어요.</span>
	{#each [0, 1] as pair (pair)}
		<div aria-hidden="true" class="flex justify-end">
			<Skeleton rounded="2xl" class="h-12.5 w-[min(70%,16rem)]" />
		</div>
		<div aria-hidden="true" class="flex flex-col gap-6">
			<div class="pl-7">
				<Skeleton rounded="full" class="size-10" />
			</div>
			<div class="flex max-w-[40rem] flex-col gap-3 pl-9">
				{#each LINES as line (line)}
					<Skeleton class={line} />
				{/each}
			</div>
		</div>
	{/each}
</div>
