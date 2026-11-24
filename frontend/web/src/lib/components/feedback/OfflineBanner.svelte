<script lang="ts">
	let isOnline = $state(true);
</script>

<svelte:window bind:online={isOnline} />

<div
	role={isOnline ? undefined : 'status'}
	aria-label={isOnline ? undefined : '네트워크 연결 상태'}
	aria-live="polite"
	aria-atomic="true"
	class="shrink-0"
>
	{#if !isOnline}
		<div class="border-b border-coral-soft bg-primary-soft">
			<div class="mx-auto flex w-full max-w-[1792px] items-center gap-3 px-5 py-3 sm:px-8">
				<svg
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="1.8"
					stroke-linecap="round"
					stroke-linejoin="round"
					aria-hidden="true"
					class="size-5 shrink-0 text-brand"
				>
					<path d="m3 3 18 18" />
					<path d="M9 16a4.5 4.5 0 0 1 6 0" />
					<path d="M5.5 12.5a9 9 0 0 1 3-1.8M15.8 10.8a9 9 0 0 1 2.7 1.7" />
					<path d="M2.5 9a14 14 0 0 1 3-2M10.5 5.5A14 14 0 0 1 21.5 9" />
					<circle cx="12" cy="20" r="1" fill="currentColor" stroke="none" />
				</svg>
				<div class="flex min-w-0 flex-col gap-1 break-keep sm:flex-row sm:items-center sm:gap-3">
					<p class="text-sm leading-normal font-semibold text-text-primary">
						인터넷 연결이 끊겼어요
					</p>
					<p class="text-xs leading-normal text-text-primary sm:text-sm">
						네트워크 연결을 확인해 주세요.
					</p>
				</div>
			</div>
		</div>
	{/if}
</div>
