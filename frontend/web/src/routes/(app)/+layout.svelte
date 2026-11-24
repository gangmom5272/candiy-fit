<script lang="ts">
	import Toast from '#lib/components/feedback/Toast.svelte';
	import OfflineBanner from '#lib/components/feedback/OfflineBanner.svelte';
	import AppHeader from '#lib/components/layout/AppHeader.svelte';
	import AuthGate from '#lib/features/auth/components/AuthGate.svelte';
	import HealthSyncGate from '#lib/features/health/components/HealthSyncGate.svelte';
	import AppConnectionProvider from '#lib/features/health/components/AppConnectionProvider.svelte';
	import AppConnectionGate from '#lib/features/health/components/AppConnectionGate.svelte';
	import { page } from '$app/state';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import { LogoutController } from '#lib/features/auth/logout.svelte.js';
	import type { LayoutProps } from './$types';

	let { children: routeContent }: LayoutProps = $props();

	// 성공 시 이동은 기존대로 AuthGate가 담당한다.
	const logout = new LogoutController();
</script>

<AuthGate>
	{#key `${authSession.sessionVersion}:${authSession.user?.id}`}
		<AppConnectionProvider>
			{#snippet children(connection)}
				<!--
		코칭은 대화 목록·대화 내용만 각자 스크롤하므로 레이아웃을 화면 높이로 고정한다.
		코칭 화면이 헤더·오프라인 안내를 뺀 남은 높이를 채워(flex-1) 오프라인 안내가 떠도 문서 스크롤이 생기지 않는다
	-->
				<div
					class={[
						'app-layout flex min-h-dvh flex-col bg-background text-text-primary lg:[--app-header-height:4rem] 2xl:[--app-header-height:5rem]',
						// 대화 화면은 모든 화면 크기에서, 코칭 첫 화면은 데스크톱에서만 고정한다(휴대폰 첫 화면은 예시가 길어 페이지가 스크롤한다)
						!connection.blocked &&
							(page.route.id === '/(app)/coaching/[conversationId]'
								? 'h-dvh'
								: page.route.id?.startsWith('/(app)/coaching') && 'lg:h-dvh')
					]}
				>
					<AppHeader onLogout={logout.run} />
					<OfflineBanner />
					<AppConnectionGate
						protectedRoute={page.route.id === '/(app)/home' ||
							!!page.route.id?.startsWith('/(app)/health') ||
							!!page.route.id?.startsWith('/(app)/coaching')}
					>
						{#if page.route.id === '/(app)/home' || page.route.id?.startsWith('/(app)/health')}
							{#key `${authSession.user?.id}:${page.route.id}`}
								<HealthSyncGate showInternalNotice={page.route.id !== '/(app)/home'}
									>{@render routeContent()}</HealthSyncGate
								>
							{/key}
						{:else}
							{@render routeContent()}
						{/if}
					</AppConnectionGate>
					{#if logout.message}
						<Toast kind="error" message={logout.message} />
					{/if}
				</div>
			{/snippet}
		</AppConnectionProvider>
	{/key}
</AuthGate>

<style>
	@media (width < 768px) {
		.app-layout {
			--app-header-height: 4rem;
			--app-bottom-nav-height: calc(5rem + env(safe-area-inset-bottom, 0px));
			padding-bottom: var(--app-bottom-nav-height);
		}
	}
</style>
