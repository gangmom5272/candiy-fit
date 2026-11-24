<script lang="ts">
	import { onMount, untrack, type Snippet } from 'svelte';
	import { PUBLIC_APP_CONNECTION_GUIDE } from '$app/env/public';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import { AppConnectionState } from '../app-connection.svelte';
	import { provideAppConnection } from '../app-connection-context';

	let {
		children,
		enabled = PUBLIC_APP_CONNECTION_GUIDE
	}: { children: Snippet<[AppConnectionState]>; enabled?: boolean } = $props();

	// 호출부는 로그인 세대와 계정 ID로 key를 둔다. 프로필 갱신은 이 수명을 바꾸지 않는다.
	const owner = untrack(() => authSession.user?.id);
	const sessionVersion = untrack(() => authSession.sessionVersion);
	const active = untrack(() => enabled && !!owner && authSession.status === 'authenticated');
	const connection = new AppConnectionState(active, undefined, () => {
		if (
			authSession.status === 'authenticated' &&
			authSession.user?.id === owner &&
			authSession.sessionVersion === sessionVersion
		)
			authSession.clearLocalSession();
	});
	provideAppConnection(connection);

	onMount(() => {
		if (!active) return () => connection.dispose();
		void connection.refresh();
		let resumeTimer: ReturnType<typeof setTimeout> | undefined;
		const resume = () => {
			clearTimeout(resumeTimer);
			if (document.visibilityState !== 'visible' || connection.checking) return;
			// 예약 이후 수동 확인이 시작됐다면 그 요청으로 복귀 확인도 충족한다.
			const requestVersion = connection.requestVersion;
			resumeTimer = setTimeout(() => {
				if (document.visibilityState === 'visible' && connection.requestVersion === requestVersion)
					void connection.refresh();
			}, 50);
		};
		window.addEventListener('focus', resume);
		document.addEventListener('visibilitychange', resume);
		return () => {
			clearTimeout(resumeTimer);
			window.removeEventListener('focus', resume);
			document.removeEventListener('visibilitychange', resume);
			connection.dispose();
		};
	});
</script>

{@render children(connection)}
