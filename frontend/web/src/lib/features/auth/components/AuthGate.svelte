<script lang="ts">
	import { onMount, type Snippet } from 'svelte';
	import { dev } from '$app/env';
	import { PUBLIC_SKIP_AUTH_GUARD } from '$app/env/public';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { authSession } from '../session.svelte';
	import AuthShell from './AuthShell.svelte';
	import AuthStatusPanel from './AuthStatusPanel.svelte';
	import { Button } from '#lib/components/ui/button/index.js';

	let { children }: { children: Snippet } = $props();

	// 백엔드 없이 화면을 볼 때만 쓰는 개발 서버 전용 예외. 빌드에서는 항상 확인한다.
	const isGuardSkipped = dev && PUBLIC_SKIP_AUTH_GUARD;

	let isChecking = $state(false);
	let hasRestoreError = $state(false);

	/** 새로고침 직후(unknown)에만 세션 쿠키로 복원한다. 결과 처리는 docs/api-integration.md 빠른 참조를 따른다. */
	async function restore() {
		isChecking = true;
		hasRestoreError = false;
		const outcome = await authSession.restore();
		isChecking = false;
		// 503·403·422·500·응답 유실은 자격 무효가 아니므로 로그인으로 보내지 않고 다시 시도하게 한다
		hasRestoreError = outcome === 'unavailable';
	}

	onMount(() => {
		if (!isGuardSkipped && authSession.status === 'unknown') restore();
	});

	// 첫 확인 결과든 사용 중 세션 만료든 로그인 상태가 정리되면 로그인으로 보낸다
	$effect(() => {
		if (!isGuardSkipped && authSession.status === 'unauthenticated') {
			goto(resolve('/(public)/login'), { replaceState: true });
		}
	});
</script>

{#if isGuardSkipped || authSession.status === 'authenticated'}
	{@render children()}
{:else if hasRestoreError}
	<AuthShell>
		<AuthStatusPanel
			tone="error"
			title="로그인 상태를 확인하지 못했어요"
			message="잠시 후 다시 시도해 주세요."
		>
			{#snippet actions()}
				<Button size="lg" class="w-full" disabled={isChecking} onclick={restore}>다시 시도</Button>
				<!-- 원인이 계속되면(Origin 설정 등) 다시 시도만으로는 벗어날 수 없으므로 로그인으로 가는 길을 둔다 -->
				<Button size="lg" variant="outline" class="w-full" href={resolve('/(public)/login')}
					>로그인 화면으로</Button
				>
			{/snippet}
		</AuthStatusPanel>
	</AuthShell>
{:else}
	<p class="sr-only" role="status">로그인 상태를 확인하고 있어요.</p>
{/if}
