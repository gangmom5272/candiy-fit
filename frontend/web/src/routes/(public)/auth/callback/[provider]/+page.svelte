<script lang="ts">
	import { onMount } from 'svelte';
	import { goto, replaceState } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { ApiError } from '#lib/api/errors.js';
	import { readCallback, type CallbackFailure } from '#lib/features/auth/oauth.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import AuthShell from '#lib/features/auth/components/AuthShell.svelte';
	import AuthStatusPanel from '#lib/features/auth/components/AuthStatusPanel.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	type ViewState =
		{ kind: 'loading' } | { kind: 'failed'; message: string; requestId: string | null };

	const providerName = $derived(data.provider === 'google' ? 'Google' : '카카오');
	let view = $state<ViewState>({ kind: 'loading' });

	function getFailureMessage(reason: CallbackFailure): string {
		if (reason === 'cancelled') return '로그인을 취소했어요.';
		if (reason === 'state-mismatch') {
			return '로그인 요청을 확인할 수 없어요. 처음부터 다시 시도해 주세요.';
		}
		return `${providerName} 로그인 중 문제가 생겼어요. 다시 시도해 주세요.`;
	}

	onMount(async () => {
		const result = readCallback(data.provider, new URL(window.location.href));
		// 인가 코드·state는 확인 직후 주소에서 지운다(명세 3.1)
		replaceState(resolve('/(public)/auth/callback/[provider]', { provider: data.provider }), {});

		if (!result.ok) {
			view = { kind: 'failed', message: getFailureMessage(result.reason), requestId: null };
			return;
		}

		// 인가 코드는 한 번만 쓸 수 있으므로 실패해도 자동으로 다시 보내지 않는다
		try {
			const status = await authSession.loginWithAuthorizationCode(result.input);
			await goto(
				status === 'LOGGED_IN' ? resolve('/(app)/home') : resolve('/(onboarding)/onboarding'),
				{ replaceState: true }
			);
		} catch (error) {
			view = {
				kind: 'failed',
				message:
					error instanceof ApiError && error.code
						? error.message
						: '로그인을 완료하지 못했어요. 잠시 후 다시 시도해 주세요.',
				requestId: error instanceof ApiError ? error.requestId : null
			};
		}
	});
</script>

<svelte:head><title>로그인 | 캔디핏</title></svelte:head>

<AuthShell>
	{#if view.kind === 'loading'}
		<AuthStatusPanel
			tone="loading"
			title="{providerName} 계정으로 로그인하는 중이에요"
			message="잠시만 기다려 주세요. 로그인이 끝나면 바로 이동해요."
		/>
	{:else}
		<AuthStatusPanel
			tone="error"
			title="로그인하지 못했어요"
			message={view.message}
			requestId={view.requestId}
		>
			{#snippet actions()}
				<Button size="lg" class="w-full" href={resolve('/(public)/login')}>로그인 화면으로</Button>
			{/snippet}
		</AuthStatusPanel>
	{/if}
</AuthShell>
