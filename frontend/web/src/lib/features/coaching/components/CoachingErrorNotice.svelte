<script lang="ts">
	import { Button } from '#lib/components/ui/button/index.js';

	type Props = {
		message: string;
		/** 있으면 오른쪽에 다시 시도 버튼을 둔다 */
		onretry?: () => void;
		retryLabel?: string;
		class?: string;
	};

	let { message, onretry, retryLabel = '다시 시도', class: className = '' }: Props = $props();
</script>

<!--
	코칭 화면의 오류 안내 한 가지 모양. 답변 실패, 지난 대화 불러오기 실패, 특정 질문에 붙일 수 없는 오류(명세 7.3)가
	같은 연분홍 상자와 같은 버튼을 쓴다(마이페이지·회원가입 오류 상자와 같은 바탕색)
-->
<div
	role="alert"
	class={[
		'flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl bg-danger-soft px-5 py-3 text-sm break-keep text-text-primary',
		className
	]}
>
	<p>{message}</p>
	{#if onretry}
		<Button variant="outline" size="sm" class="shrink-0" onclick={onretry}>{retryLabel}</Button>
	{/if}
</div>
