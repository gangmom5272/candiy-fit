<script lang="ts">
	import Skeleton from '#lib/components/feedback/Skeleton.svelte';

	// 제목 폭을 조금씩 다르게 해 실제 목록처럼 보이게 한다
	const TITLES = ['w-5/6', 'w-2/3', 'w-11/12', 'w-3/4', 'w-1/2'];
</script>

<!--
	대화 목록을 처음 불러오는 동안 항목 다섯 개 모양으로 자리를 잡는다.
	항목(ConversationList의 a: px-3 py-3·제목 두 줄·시각 한 줄)과 같은 자리·여백이다.
-->
<div role="status" aria-label="대화 목록을 불러오고 있어요." class="flex flex-col gap-2 p-4">
	<span class="sr-only">대화 목록을 불러오고 있어요.</span>
	{#each TITLES as width (width)}
		<div aria-hidden="true" class="flex flex-col gap-2 px-3 py-3">
			<Skeleton class={`h-4 ${width}`} />
			<Skeleton class="h-3 w-1/3" />
		</div>
	{/each}
</div>
