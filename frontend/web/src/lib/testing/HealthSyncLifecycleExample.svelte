<script lang="ts">
	import { onMount } from 'svelte';
	import { authSession } from '../features/auth/session.svelte';
	import type { User } from '../features/auth/types';
	import HealthSyncGate from '../features/health/components/HealthSyncGate.svelte';
	import SleepPage from '../../routes/(app)/health/sleep/+page.svelte';

	let { first, second }: { first: User; second: User } = $props();
	let ready = $state(false);
	let visible = $state(true);
	onMount(() => {
		const previousUser = authSession.user;
		const previousStatus = authSession.status;
		authSession.user = first;
		authSession.status = 'authenticated';
		ready = true;
		return () => {
			authSession.user = previousUser;
			authSession.status = previousStatus;
		};
	});
</script>

<button type="button" onclick={() => (authSession.user = second)}>합성 계정 전환</button>
<button type="button" onclick={() => (visible = false)}>테스트 화면 이탈</button>
{#if ready && visible}
	{#key authSession.user?.id}
		<HealthSyncGate><SleepPage /></HealthSyncGate>
	{/key}
{/if}
