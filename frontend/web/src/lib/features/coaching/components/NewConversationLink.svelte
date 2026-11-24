<script lang="ts">
	import { resolve } from '$app/paths';

	type Props = {
		/** 접힌 레일에서는 아이콘만 둔다(이름은 aria-label) */
		compact?: boolean;
	};

	let { compact = false }: Props = $props();
</script>

<!--
	새 대화 시작. 대화 목록 맨 위(펼침)와 목록 버튼 아래(레일)에 둔다(ChatGPT·Claude 의 사이드바와 같은 자리).
	예전에는 대화 화면 제목 오른쪽에 있었는데 목록과 떨어져 있어 찾기 어렵고 제목 줄이 복잡했다.
	코칭 첫 화면은 가운데 입력창이 곧 새 대화라 두지 않는다(ConversationList·ConversationSidebar 의 isNewConversation).
-->
<a
	href={resolve('/(app)/coaching')}
	class={[
		'flex shrink-0 items-center rounded-lg font-bold text-primary transition-colors hover:bg-primary-soft',
		compact ? 'size-10 justify-center' : 'gap-2 px-3 py-2.5 text-sm'
	]}
	aria-label={compact ? '새 대화' : undefined}
>
	<svg
		class="size-5"
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		aria-hidden="true"
	>
		<path d="M12 5v14M5 12h14" stroke-linecap="round" />
	</svg>
	{#if !compact}새 대화{/if}
</a>
