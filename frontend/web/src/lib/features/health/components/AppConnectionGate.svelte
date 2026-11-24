<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { appConnectionContext } from '../app-connection-context';
	import AppLinkGuide from './AppLinkGuide.svelte';

	let { children, protectedRoute = true }: { children: Snippet; protectedRoute?: boolean } =
		$props();
	const connection = appConnectionContext();
	let isRechecking = $state(false);

	async function handleRecheck(): Promise<void> {
		if (!connection || isRechecking) return;
		isRechecking = true;
		try {
			await connection.refresh();
		} finally {
			isRechecking = false;
		}
	}
</script>

<svelte:head>
	{#if protectedRoute && connection?.enabled && connection.blocked}
		<title
			>{connection.value?.status === 'NOT_CONNECTED'
				? '앱 연동 안내 | 캔디핏'
				: connection.error
					? '앱 연동 상태 확인 오류 | 캔디핏'
					: '앱 연동 상태 확인 중 | 캔디핏'}</title
		>
	{/if}
</svelte:head>

{#if !protectedRoute || !connection?.enabled}
	{@render children()}
{:else if !connection.value}
	<main class="mx-auto w-full max-w-health-page px-5 py-8 sm:px-8" aria-busy={connection.checking}>
		{#if connection.error}
			<h1 class="text-page-title text-card-foreground">앱 연동 상태를 확인하지 못했어요</h1>
			<p role="alert" class="mt-3 text-base leading-relaxed text-muted-foreground">
				잠시 후 다시 확인해 주세요. 프로필과 로그아웃은 계속 이용할 수 있어요.
			</p>
			<Button
				variant="outline"
				size="sm"
				class="mt-4"
				disabled={connection.checking}
				onclick={() => void handleRecheck()}>연동 상태 다시 확인</Button
			>
		{:else}
			<p role="status" class="text-base leading-relaxed text-muted-foreground">
				{connection.retrying
					? '연결이 원활하지 않아 앱 연동 상태를 다시 확인하고 있어요.'
					: '앱 연동 상태를 확인하고 있어요.'}
			</p>
		{/if}
	</main>
{:else if connection.value.status === 'NOT_CONNECTED'}
	<AppLinkGuide>
		{#snippet actions()}
			<div class="flex max-w-full min-w-0 flex-col items-center gap-2">
				{#if connection.error}
					<p role="alert" class="text-center text-sm leading-relaxed text-muted-foreground">
						앱 연동 상태를 확인하지 못했어요. 마지막으로 확인한 상태를 유지하고 있어요.
					</p>
				{:else if connection.retrying}
					<p role="status" class="text-center text-sm leading-relaxed text-muted-foreground">
						연결이 원활하지 않아 앱 연동 상태를 다시 확인하고 있어요.
					</p>
				{/if}
				<Button
					variant="outline"
					size="default"
					class="shrink-0"
					disabled={connection.checking}
					onclick={() => void handleRecheck()}
					>{connection.checking ? '연동 상태 확인 중' : '연동 상태 다시 확인'}</Button
				>
			</div>
		{/snippet}
	</AppLinkGuide>
{:else}
	{#if connection.error || connection.retrying || isRechecking || connection.showUnknownNotice}
		<div class="mx-auto w-full max-w-health-page px-5 pt-3 sm:px-8">
			{#if connection.error}
				<p role="alert" class="mb-2 text-sm text-muted-foreground">
					앱 연동 상태를 확인하지 못했어요. 마지막으로 확인한 상태를 유지하고 있어요.
				</p>
			{:else if connection.retrying}
				<p role="status" class="mb-2 text-sm text-muted-foreground">
					연결이 원활하지 않아 앱 연동 상태를 다시 확인하고 있어요.
				</p>
			{:else if isRechecking}
				<p role="status" class="mb-2 text-sm text-muted-foreground">
					앱 연동 상태를 확인하고 있어요.
				</p>
			{:else}
				<p role="status" class="mb-2 text-sm text-muted-foreground">
					앱 연동 완료 이력은 아직 확인되지 않았어요. 기존 화면은 계속 이용할 수 있어요. 앱에서 연동
					상태를 확인할 수 있어요.
				</p>
			{/if}
			<div class="flex flex-wrap items-center gap-2">
				<Button
					variant="outline"
					size="sm"
					disabled={connection.checking}
					onclick={() => void handleRecheck()}
					>{connection.checking ? '연동 상태 확인 중' : '연동 상태 다시 확인'}</Button
				>
				{#if connection.showUnknownNotice && !connection.error && !connection.retrying && !isRechecking}
					<Button
						variant="ghost"
						size="sm"
						aria-label="앱 연동 상태 안내 닫기"
						onclick={() => connection.dismissUnknownNotice()}>안내 닫기</Button
					>
				{/if}
			</div>
		</div>
	{/if}
	{@render children()}
{/if}
