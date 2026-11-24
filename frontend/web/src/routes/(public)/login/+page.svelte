<script lang="ts">
	import { onMount } from 'svelte';
	import { PUBLIC_GOOGLE_CLIENT_ID, PUBLIC_KAKAO_REST_API_KEY } from '$app/env/public';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { createAuthorizeUrl, type OAuthProvider } from '#lib/features/auth/oauth.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import AuthShell from '#lib/features/auth/components/AuthShell.svelte';
	import LoginPanel from '#lib/features/auth/components/LoginPanel.svelte';

	// client ID가 빌드에 없으면 제공자 화면으로 보낼 수 없어 버튼을 끄고 사용자에게는 준비 중으로 안내한다
	const providers: Array<{ id: OAuthProvider; label: string; clientId: string | undefined }> = [
		{ id: 'kakao', label: '카카오로 시작하기', clientId: PUBLIC_KAKAO_REST_API_KEY },
		{ id: 'google', label: 'Google로 시작하기', clientId: PUBLIC_GOOGLE_CLIENT_ID }
	];

	let startingProvider = $state<OAuthProvider | null>(null);
	let hasStartError = $state(false);

	/** 이미 로그인한 사람이 주소창·즐겨찾기로 로그인 화면을 열면 홈으로 보낸다 */
	async function leaveIfSignedIn() {
		// 새로고침 직후(unknown)에는 (app) 화면처럼 갱신 쿠키로 먼저 복원해 본다(AuthGate 와 같은 restore).
		// 쿠키가 없거나 복원에 실패하면 로그인 화면에 그대로 둔다
		if (authSession.status === 'unknown') await authSession.restore();
		// 확인하는 사이 제공자 버튼을 눌렀으면 그 로그인을 이어 간다
		if (authSession.status === 'authenticated' && startingProvider === null)
			goto(resolve('/(app)/home'), { replaceState: true });
	}

	onMount(() => {
		leaveIfSignedIn();
	});

	async function handleStart(provider: OAuthProvider, clientId: string) {
		if (startingProvider) return;
		startingProvider = provider;
		hasStartError = false;
		try {
			window.location.assign(await createAuthorizeUrl(provider, clientId, window.location.origin));
		} catch {
			// HTTPS가 아닌 주소(crypto.subtle 없음)나 저장소 차단(sessionStorage)일 때
			startingProvider = null;
			hasStartError = true;
		}
	}
</script>

<!-- 제공자 화면에서 뒤로 가기로 돌아오면(bfcache) 버튼을 다시 쓸 수 있게 한다 -->
<svelte:window onpageshow={() => (startingProvider = null)} />

<svelte:head><title>로그인 | 캔디핏</title></svelte:head>

<AuthShell>
	<LoginPanel
		providers={providers.map(({ id, label, clientId }) => ({ id, label, isAvailable: !!clientId }))}
		{startingProvider}
		{hasStartError}
		onstart={(id) => {
			const clientId = providers.find((provider) => provider.id === id)?.clientId;
			if (clientId) handleStart(id, clientId);
		}}
	/>
</AuthShell>
