<script lang="ts">
	import { onMount, untrack, type Snippet } from 'svelte';
	import { ApiError } from '#lib/api/errors.js';
	import type { ApiClient } from '#lib/api/client.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import {
		requestHealthSync,
		healthReadClient,
		syncOutcome,
		type HealthSyncOutcome
	} from '../sync-api';
	import { provideHealthSync } from '../sync-context';
	import { appConnectionContext } from '../app-connection-context';

	let { children, showInternalNotice = true }: { children: Snippet; showInternalNotice?: boolean } =
		$props();
	let ready = $state(false);
	let syncing = $state(true);
	let outcome = $state<HealthSyncOutcome | null>(null);
	// 합성 계정 안내와 조치 가능한 오류는 내부 진행 상태와 구분해 유지한다.
	const isInternalNotice = $derived(
		outcome === 'SKIPPED_RECENT' || outcome === 'BUSY' || outcome === 'TIMEOUT'
	);
	const showOutcomeNotice = $derived(
		outcome !== null &&
			outcome !== 'SYNCED' &&
			outcome !== 'DISABLED' &&
			(showInternalNotice || !isInternalNotice)
	);
	let revision = $state(0);
	let reads = $state(0);
	let controller: AbortController | undefined;
	let timer: ReturnType<typeof setTimeout> | undefined;
	let disposed = false;
	let sequence = 0;
	const appConnection = appConnectionContext();
	let finishInitialSync: () => void;
	const initialSync = new Promise<void>((resolve) => {
		finishInitialSync = resolve;
	});
	function waitForInitialSync(signal: AbortSignal): Promise<void> {
		if (untrack(() => ready)) return Promise.resolve();
		return new Promise((resolve, reject) => {
			const abort = () => reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
			if (signal.aborted) {
				abort();
				return;
			}
			signal.addEventListener('abort', abort, { once: true });
			void initialSync.then(() => {
				signal.removeEventListener('abort', abort);
				if (disposed || signal.aborted) abort();
				else resolve();
			});
		});
	}
	const messages: Record<HealthSyncOutcome, string | null> = {
		SYNCED: '건강 기록을 갱신했어요.',
		SKIPPED_RECENT: '최근 갱신 시도가 있어 이번 갱신은 생략했어요. 최신화 여부는 확인할 수 없어요.',
		BUSY: '건강 기록을 준비 중이에요. 다른 갱신 작업의 완료 여부는 확인할 수 없어요.',
		TIMEOUT: '갱신 완료를 확인하지 못했어요. 서버에서 작업이 계속될 수 있어요.',
		SYNTHETIC: '샘플 계정이므로 외부 데이터 갱신을 생략했어요.',
		DISABLED: null,
		NOT_LINKED:
			'앱 연동을 확인해 주세요. 이미 연동했다면 첫 데이터가 준비될 때까지 기다린 뒤 다시 시도해 주세요.',
		UNAVAILABLE: '건강 기록을 갱신하지 못했어요. 기존 기록 조회를 시도해요.',
		NETWORK: '연결 문제로 갱신 결과를 확인하지 못했어요. 기존 기록 조회를 시도해요.',
		INVALID: '갱신 응답을 확인하지 못했어요. 기존 기록 조회를 시도해요.',
		AUTH: '로그인 상태를 확인하지 못했어요. 잠시 후 다시 시도해 주세요.'
	};
	provideHealthSync({
		get revision() {
			return revision;
		},
		get rechecking() {
			return syncing || reads > 0;
		},
		async recheck() {
			if (reads === 0) await run(true);
		},
		async read<T>(
			signal: AbortSignal,
			request: (signal: AbortSignal, client: ApiClient) => Promise<T>
		): Promise<T> {
			untrack(() => reads++);
			try {
				// 페이지는 스켈레톤을 먼저 표시하고, 최초 GET은 기존처럼 POST 완료를 기다린다.
				await waitForInitialSync(signal);
				signal.throwIfAborted();
				return await request(signal, healthReadClient);
			} finally {
				if (!disposed) reads--;
			}
		}
	});
	async function run(recheckConnection = false) {
		if (disposed || (controller && syncing)) return;
		const userId = authSession.user?.id;
		const sessionVersion = authSession.sessionVersion;
		if (!userId || authSession.status !== 'authenticated') {
			// 기존 AuthGate의 개발 전용 우회에서도 POST 없이 화면을 확인할 수 있다.
			outcome = 'AUTH';
			syncing = false;
			ready = true;
			finishInitialSync();
			return;
		}
		const id = ++sequence;
		controller?.abort();
		clearTimeout(timer);
		controller = new AbortController();
		const signal = controller.signal;
		syncing = true;
		if (recheckConnection && appConnection) {
			const allowed = await appConnection.recheck(signal);
			if (
				disposed ||
				signal.aborted ||
				id !== sequence ||
				authSession.user?.id !== userId ||
				authSession.status !== 'authenticated' ||
				authSession.sessionVersion !== sessionVersion
			)
				return;
			if (!allowed) {
				syncing = false;
				return;
			}
		}
		let result: HealthSyncOutcome;
		try {
			result = await requestHealthSync(userId, signal);
		} catch (error) {
			if (
				signal.aborted ||
				disposed ||
				id !== sequence ||
				authSession.user?.id !== userId ||
				authSession.sessionVersion !== sessionVersion ||
				authSession.status !== 'authenticated'
			)
				return;
			if (error instanceof ApiError && error.code === 'AUTH_REQUIRED')
				authSession.clearLocalSession();
			result = syncOutcome(error);
		}
		if (
			disposed ||
			signal.aborted ||
			id !== sequence ||
			authSession.user?.id !== userId ||
			authSession.sessionVersion !== sessionVersion ||
			authSession.status !== 'authenticated'
		)
			return;
		outcome = result;
		syncing = false;
		if (ready) revision++;
		else {
			ready = true;
			finishInitialSync();
		}
		if (result === 'BUSY' || result === 'TIMEOUT') {
			let attempts = 0;
			const requery = () => {
				if (disposed || signal.aborted || id !== sequence) return;
				revision++;
				if (++attempts < 2) timer = setTimeout(requery, 3000);
			};
			timer = setTimeout(requery, 2000);
		}
	}
	onMount(() => {
		void run();
		return () => {
			disposed = true;
			finishInitialSync();
			sequence++;
			controller?.abort();
			clearTimeout(timer);
		};
	});
</script>

{#if ready && ((showInternalNotice && syncing) || showOutcomeNotice)}
	<div
		class="mx-auto w-full max-w-health-page px-5 pt-3 text-xs leading-relaxed text-text-secondary sm:px-8"
	>
		<p role="status" aria-live="polite">
			{syncing && showInternalNotice
				? '건강 데이터를 갱신하고 있어요.'
				: outcome
					? messages[outcome]
					: ''}
			{#if showInternalNotice && reads > 0}
				기존 건강 기록을 조회하고 있어요.{/if}
		</p>
		{#if ready && outcome !== 'SYNTHETIC' && outcome !== 'DISABLED'}
			<button
				type="button"
				onclick={() => void run(true)}
				disabled={syncing}
				class="mt-1 min-h-11 cursor-pointer rounded-lg border border-border bg-card px-3 py-2 font-semibold text-text-primary focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-wait"
				>{syncing ? '확인하는 중' : '건강 기록 다시 확인'}</button
			>
		{/if}
	</div>
{:else if ready && reads > 0}
	<p class="sr-only" role="status">기존 건강 기록을 조회하고 있어요.</p>
{/if}
{@render children()}
