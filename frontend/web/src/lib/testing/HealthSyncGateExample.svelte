<script lang="ts">
	import { onMount } from 'svelte';
	import { authSession } from '../features/auth/session.svelte';
	import HealthSyncGate from '../features/health/components/HealthSyncGate.svelte';
	import HealthPageHeader from '../features/health/components/HealthPageHeader.svelte';
	import { e2eUser } from './e2e-auth';
	import type { HealthSyncOutcome } from '../features/health/sync-api';
	let {
		outcome,
		showHeader = false
	}: { outcome: HealthSyncOutcome | 'LOADING'; showHeader?: boolean } = $props();
	let ready = $state(false);
	onMount(() => {
		const previousFetch = window.fetch;
		const previousUser = authSession.user;
		const previousStatus = authSession.status;
		window.fetch = async (input, options) => {
			if (!String(input).endsWith('/api/v1/health/sync')) return previousFetch(input, options);
			if (outcome === 'LOADING')
				return new Promise<Response>((_, reject) => {
					const signal = options?.signal;
					if (signal?.aborted) reject(signal.reason);
					else signal?.addEventListener('abort', () => reject(signal.reason), { once: true });
				});
			if (outcome === 'NETWORK') throw new TypeError('합성 연결 오류');
			if (outcome === 'INVALID') return Response.json({ data: { status: 'INVALID' } });
			if (['TIMEOUT', 'UNAVAILABLE', 'NOT_LINKED'].includes(outcome)) {
				return Response.json(
					{
						error: {
							code: outcome === 'NOT_LINKED' ? 'CANDIY_USER_NOT_LINKED' : 'HEALTH_SYNC_UNAVAILABLE',
							context: { reason: outcome === 'TIMEOUT' ? 'TIMEOUT' : 'METRIC_FAILED', metrics: [] }
						}
					},
					{ status: outcome === 'NOT_LINKED' ? 409 : 503 }
				);
			}
			return Response.json({ data: { status: outcome } });
		};
		authSession.user = { ...e2eUser, socialType: 'GOOGLE', gender: 'FEMALE' };
		authSession.status = 'authenticated';
		ready = true;
		return () => {
			window.fetch = previousFetch;
			authSession.user = previousUser;
			authSession.status = previousStatus;
		};
	});
</script>

{#if ready}
	<HealthSyncGate>
		<section class="mx-auto w-full max-w-health-page px-5 py-8">
			{#if showHeader}<HealthPageHeader />{:else}<h2 class="font-bold">
					기존 건강 기록 조회 영역
				</h2>{/if}
			<p class="mt-2 text-sm text-text-secondary">
				합성 API 응답으로 동기화 상태 안내와 재시도를 확인하는 예시입니다.
			</p>
		</section>
	</HealthSyncGate>
{/if}
