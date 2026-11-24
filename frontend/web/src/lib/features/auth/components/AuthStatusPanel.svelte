<script lang="ts">
	import type { Snippet } from 'svelte';

	type Props = {
		/** `loading`: 로그인 처리 중, `error`: 실패·다시 로그인 안내 */
		tone: 'loading' | 'error';
		title: string;
		message?: string;
		/** 서버 오류의 요청 ID. 문의할 때 쓰도록 문구 아래에 둔다 */
		requestId?: string | null;
		/** 버튼·링크 */
		actions?: Snippet;
	};

	let { tone, title, message, requestId = null, actions }: Props = $props();
</script>

<!-- 로그인 처리 중·실패, 가입 시간 초과, 로그인 상태 확인 실패를 같은 모양으로 보여 준다 -->
<div class="flex flex-col">
	<span
		class={[
			'grid size-14 place-items-center rounded-full',
			tone === 'loading' ? 'bg-primary-soft text-brand' : 'bg-danger-soft text-danger'
		]}
		aria-hidden="true"
	>
		{#if tone === 'loading'}
			<svg
				class="size-7 animate-spin motion-reduce:animate-[spin_1.6s_linear_infinite]"
				viewBox="0 0 24 24"
				fill="none"
			>
				<circle cx="12" cy="12" r="9" class="stroke-coral-soft" stroke-width="2.5" />
				<path
					d="M21 12a9 9 0 0 0-9-9"
					stroke="currentColor"
					stroke-width="2.5"
					stroke-linecap="round"
				/>
			</svg>
		{:else}
			<svg class="size-7" viewBox="0 0 24 24" fill="none">
				<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" />
				<path d="M12 7.5v5.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
				<circle cx="12" cy="16.4" r="1.25" fill="currentColor" />
			</svg>
		{/if}
	</span>

	{#if tone === 'loading'}
		<h1
			class="mt-6 text-[26px] leading-[1.35] font-bold break-keep sm:text-[30px]"
			aria-live="polite"
		>
			{title}
		</h1>
		{#if message}
			<p class="mt-3 text-[15px] leading-[1.6] break-keep text-text-secondary sm:text-base">
				{message}
			</p>
		{/if}
	{:else}
		<h1 class="mt-6 text-[26px] leading-[1.35] font-bold break-keep sm:text-[30px]">{title}</h1>
		{#if message}
			<div
				role="alert"
				class="mt-3 text-[15px] leading-[1.6] break-keep text-text-secondary sm:text-base"
			>
				<p>{message}</p>
				{#if requestId}
					<p
						class="mt-3 inline-flex rounded-lg bg-surface px-3 py-1.5 text-xs leading-[normal] text-text-primary"
					>
						요청 ID: {requestId}
					</p>
				{/if}
			</div>
		{/if}
	{/if}

	{#if actions}
		<div class="mt-9 flex flex-col gap-3">{@render actions()}</div>
	{/if}
</div>
