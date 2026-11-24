<script lang="ts">
	type Props = {
		message: string;
		/** error는 바로 읽어 주는 alert, info는 차례를 기다리는 status로 알린다 */
		kind?: 'error' | 'info';
		/** 안내와 함께 누를 수 있는 버튼. 사라지기 전에 누를 수 있게 띄우는 쪽이 표시 시간을 늘린다 */
		action?: { label: string; onclick: () => void };
	};

	let { message, kind = 'info', action }: Props = $props();
</script>

<!-- 화면 영역을 차지하지 않는 일시 안내. 표시 시간은 띄우는 쪽이 정한다 -->
<div
	class="pointer-events-none fixed inset-x-0 bottom-[calc(2rem+var(--app-bottom-nav-height,0px))] z-50 flex justify-center px-5"
>
	<div
		role={kind === 'error' ? 'alert' : 'status'}
		class="flex max-w-md flex-wrap items-center justify-center gap-x-4 gap-y-2 rounded-2xl bg-primary px-5 py-3 text-center text-sm leading-[normal] font-semibold break-keep text-primary-foreground shadow-[0_2px_2px_rgb(0_0_0/0.12)]"
	>
		<p>{message}</p>
		{#if action}
			<button
				type="button"
				class="pointer-events-auto rounded-lg bg-card px-3 py-1.5 text-text-primary hover:bg-surface focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-card"
				onclick={action.onclick}>{action.label}</button
			>
		{/if}
	</div>
</div>
