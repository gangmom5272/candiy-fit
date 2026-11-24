<script lang="ts">
	import { onMount } from 'svelte';
	import { healthSyncContext } from '../features/health/sync-context';

	let {
		readHealth,
		onMounted,
		onDestroyed
	}: { readHealth: boolean; onMounted: () => void; onDestroyed: () => void } = $props();
	const sync = healthSyncContext();
	let revisionRead = $state(-1);
	let failed = $state(false);

	onMount(() => {
		onMounted();
		return onDestroyed;
	});
	$effect(() => {
		if (!readHealth) return;
		const revision = sync.revision;
		const controller = new AbortController();
		void sync
			.read(controller.signal, (signal, client) =>
				client.request('/testing/health-record', { signal })
			)
			.then(
				() => {
					if (!controller.signal.aborted) revisionRead = revision;
				},
				() => {
					if (!controller.signal.aborted) failed = true;
				}
			);
		return () => controller.abort();
	});
</script>

<section aria-label="기존 화면" data-testid="connection-child">
	<h2>기존 화면</h2>
	<p>
		{!readHealth
			? '프로필 표시'
			: failed
				? '기록 조회 오류'
				: revisionRead < 0
					? '기록 대기'
					: '기록 표시'}
	</p>
	<output aria-label="기록 리비전">{revisionRead}</output>
</section>
