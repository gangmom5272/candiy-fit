<script lang="ts">
	import { onMount } from 'svelte';
	import { authSession } from '../features/auth/session.svelte';
	import type { User } from '../features/auth/types';
	import AppConnectionProvider from '../features/health/components/AppConnectionProvider.svelte';
	import AppConnectionGate from '../features/health/components/AppConnectionGate.svelte';
	import HealthSyncGate from '../features/health/components/HealthSyncGate.svelte';
	import AppConnectionReadProbe from './AppConnectionReadProbe.svelte';

	let {
		first,
		second,
		enabled,
		protectedRoute = true,
		withSync = true
	}: {
		first: User;
		second: User;
		enabled?: boolean;
		protectedRoute?: boolean;
		withSync?: boolean;
	} = $props();
	let ready = $state(false);
	let mounts = $state(0);
	let destroys = $state(0);

	function login() {
		authSession.user = first;
		authSession.status = 'authenticated';
	}
	onMount(() => {
		const previousUser = authSession.user;
		const previousStatus = authSession.status;
		login();
		ready = true;
		return () => {
			authSession.user = previousUser;
			authSession.status = previousStatus;
		};
	});
</script>

<button type="button" onclick={() => (authSession.user = second)}>합성 계정 전환</button>
<button
	type="button"
	onclick={() => {
		if (authSession.user)
			authSession.updateUser({ ...authSession.user, displayName: '프로필 갱신' });
	}}>프로필 객체 갱신</button
>
<button type="button" onclick={() => authSession.clearLocalSession()}>합성 로그아웃</button>
<button type="button" onclick={login}>합성 다시 로그인</button>
<button
	type="button"
	onclick={() => {
		authSession.clearLocalSession();
		login();
	}}>합성 같은 계정 새 세션</button
>
<output aria-label="자식 마운트 횟수">{mounts}</output>
<output aria-label="자식 해제 횟수">{destroys}</output>
{#if ready && authSession.status === 'authenticated' && authSession.user}
	{#key `${authSession.sessionVersion}:${authSession.user.id}`}
		<AppConnectionProvider {enabled}>
			{#snippet children(connection)}
				<output aria-label="연동 상태">{connection.value?.status ?? 'NONE'}</output>
				<output aria-label="연동 조회 중">{String(connection.checking)}</output>
				<button type="button" onclick={() => void connection.refresh()}>하네스 재조회</button>
				<AppConnectionGate {protectedRoute}>
					{#if withSync}
						<HealthSyncGate>
							<AppConnectionReadProbe
								readHealth={true}
								onMounted={() => mounts++}
								onDestroyed={() => destroys++}
							/>
						</HealthSyncGate>
					{:else}
						<AppConnectionReadProbe
							readHealth={false}
							onMounted={() => mounts++}
							onDestroyed={() => destroys++}
						/>
					{/if}
				</AppConnectionGate>
			{/snippet}
		</AppConnectionProvider>
	{/key}
{:else if ready}
	<p>하네스 로그아웃 상태</p>
{/if}
