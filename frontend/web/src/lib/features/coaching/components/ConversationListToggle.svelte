<script lang="ts">
	import type { ClassValue } from 'svelte/elements';

	type Props = {
		/** 열고 닫을 대화 목록 요소의 id */
		controls: string;
		/** 지금 목록이 펼쳐져 있는지. 이름(열기·접기)과 aria-expanded 가 따라 바뀐다 */
		expanded: boolean;
		ontoggle: () => void;
		class?: ClassValue;
	};

	let { controls, expanded, ontoggle, class: className }: Props = $props();
</script>

<!--
	대화 목록 열기·접기 버튼. 열 때와 접을 때 같은 모양·같은 자리에 둔다(ChatGPT·Claude 의 사이드바 버튼과 같은 방식).
	넓은 화면에서는 목록(접으면 얇은 레일) 맨 위 왼쪽, 좁은 화면에서는 제목 왼쪽과 서랍 맨 위 왼쪽에 있다.
-->
<button
	type="button"
	class={[
		// 키보드 포커스는 테두리 대신 배경으로 보여 준다(#308 팀 결정)
		'grid size-10 shrink-0 place-items-center rounded-lg text-text-primary hover:bg-surface focus-visible:bg-primary-soft focus-visible:outline-none',
		className
	]}
	aria-label={expanded ? '대화 목록 접기' : '대화 목록 열기'}
	aria-expanded={expanded}
	aria-controls={controls}
	onclick={ontoggle}
>
	<svg
		class="size-6"
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		aria-hidden="true"
	>
		<rect x="3" y="4" width="18" height="16" rx="2" />
		<path d="M9 4v16" />
	</svg>
</button>
