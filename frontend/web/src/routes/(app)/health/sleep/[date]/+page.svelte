<script lang="ts">
	import { onDestroy, tick, untrack } from 'svelte';
	import HealthLoading from '#lib/features/health/components/HealthLoading.svelte';
	import { healthSyncContext } from '#lib/features/health/sync-context.js';
	const healthSync = healthSyncContext();
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { afterNavigate, goto } from '$app/navigation';
	import {
		readSleepReturn,
		shouldHandleHealthLink,
		type SleepReturnContext
	} from '#lib/features/health/health-navigation.js';
	import HealthPageHeader from '#lib/features/health/components/HealthPageHeader.svelte';
	import HealthBackLink from '#lib/features/health/components/HealthBackLink.svelte';
	import SleepStageTimeline from '#lib/features/health/components/SleepStageTimeline.svelte';
	import { formatSleepMinutes, sleepClock } from '#lib/features/health/sleep.js';
	import {
		sleepDateLabel,
		sleepMissingText,
		sleepStageStyles,
		sleepStageSummaryOrder,
		sleepStageDurations,
		sleepStagePercentage
	} from '#lib/features/health/sleep-detail.js';
	import {
		loadSleepDetail,
		sleepDateChoices,
		sleepRequestError
	} from '#lib/features/health/sleep-api.js';
	import { activityToday } from '#lib/features/health/activity-api.js';
	import { ApiError } from '#lib/api/errors.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';

	const date = $derived(page.params.date ?? '');
	const today = activityToday();
	const returnContext = untrack(() => readSleepReturn(page.state, today));
	let restorePending = $state(returnContext?.date === untrack(() => date));
	let navigationReady = $state(false);
	let dayLink = $state<HTMLAnchorElement>();
	let isActive = true;
	let isNavigating = false;
	onDestroy(() => (isActive = false));
	afterNavigate(() => (navigationReady = true));
	let data = $state<Awaited<ReturnType<typeof loadSleepDetail>> | null>(null);
	let isLoading = $state(true);
	let hasLoaded = $state(false);
	let notice = $state<string | null>(null);
	let retry = $state(0);
	const sleep = $derived(data?.sleep.status === 'fulfilled' ? data.sleep.value : null);
	const night = $derived(sleep?.detail.nights.find((item) => item.date === date));
	const sleepDay = $derived(sleep?.days.find((item) => item.date === date));
	const oxygen = $derived(
		data?.oxygen.status === 'fulfilled'
			? data.oxygen.value.days.find((item) => item.date === date)
			: undefined
	);
	const timeline = $derived(
		data?.timeline.status === 'fulfilled'
			? data.timeline.value.find((item) => item.date === date)
			: undefined
	);
	const sleepDetailDates = $derived(sleepDateChoices(sleep?.summary.linkedFrom ?? null, today));
	const durations = $derived(sleepStageDurations(timeline?.sleepSegments ?? []));

	const summary = $derived([
		{
			label: '총 수면 시간',
			value:
				night?.sleepMinutes == null
					? sleepMissingText(sleepDay?.reason)
					: formatSleepMinutes(night.sleepMinutes)
		},
		{ label: '취침 시각', value: night ? sleepClock(night.startAt) : '기록 없음' },
		{ label: '기상 시각', value: night ? sleepClock(night.endAt) : '기록 없음' },
		{
			label: '수면 중 평균 산소포화도',
			value:
				data?.oxygen.status === 'rejected'
					? '산소포화도 조회 실패'
					: oxygen && !oxygen.missing && oxygen.value !== null
						? `${oxygen.value}%`
						: sleepMissingText(oxygen?.reason)
		}
	]);
	async function load(selected: string, signal: AbortSignal) {
		isLoading = true;
		data = null;
		notice = null;
		try {
			const result = await healthSync.read(signal, (requestSignal, client) =>
				loadSleepDetail(selected, requestSignal, client)
			);
			if (!signal.aborted) {
				data = result;
				hasLoaded = true;
			}
		} catch (error) {
			if (signal.aborted) return;
			if (error instanceof ApiError && error.code === 'AUTH_REQUIRED')
				authSession.clearLocalSession();
			notice = sleepRequestError(error);
		} finally {
			if (!signal.aborted) isLoading = false;
		}
	}
	$effect(() => {
		void healthSync.revision;
		void retry;
		const controller = new AbortController();
		void load(date, controller.signal);
		return () => controller.abort();
	});
	$effect(() => {
		if (!restorePending || !navigationReady || !returnContext || isLoading) return;
		let cancelled = false;
		void tick().then(() => {
			if (
				cancelled ||
				!isActive ||
				page.route.id !== '/(app)/health/sleep/[date]' ||
				date !== returnContext.date
			)
				return;
			dayLink?.focus({ preventScroll: true });
			window.scrollTo({ top: returnContext.scrollY, behavior: 'instant' });
			restorePending = false;
		});
		return () => {
			cancelled = true;
		};
	});

	async function handleDayNavigate(event: MouseEvent) {
		if (!shouldHandleHealthLink(event)) return;
		event.preventDefault();
		if (isNavigating) return;
		isNavigating = true;
		const sleepReturn: SleepReturnContext = { date, scrollY: Math.max(0, window.scrollY) };
		try {
			// 직전 수면 상세 항목도 갱신해 브라우저 뒤로 가기에서 같은 위치를 복원한다.
			await goto(resolve('/(app)/health/sleep/[date]', { date: sleepReturn.date }), {
				shallow: true,
				replace: true,
				reset: false,
				state: { ...page.state, sleepReturn }
			});
			if (isActive && page.route.id === '/(app)/health/sleep/[date]' && date === sleepReturn.date)
				await goto(resolve('/(app)/health/day/[date]', { date: sleepReturn.date }), {
					state: { sleepReturn }
				});
		} finally {
			isNavigating = false;
		}
	}

	function handleDateSelect(event: Event) {
		const target = event.currentTarget;
		if (target instanceof HTMLSelectElement) {
			restorePending = false;
			void goto(resolve('/(app)/health/sleep/[date]', { date: target.value }));
		}
	}
</script>

<svelte:head><title>수면 상세 | 캔디핏</title></svelte:head>

<main
	data-health-ui
	class="mx-auto flex w-full max-w-health-page flex-1 flex-col px-5 pt-8 pb-8 sm:px-8 2xl:pt-10 [&>header>div>p]:sr-only"
>
	<HealthPageHeader />
	<div class="mt-5 flex flex-wrap items-center justify-between gap-3">
		<HealthBackLink href="/(app)/health/sleep" />
		<a
			bind:this={dayLink}
			data-sleep-day-link
			href={resolve('/(app)/health/day/[date]', { date })}
			onclick={(event) => void handleDayNavigate(event)}
			class="inline-flex min-h-11 items-center rounded-xl px-4 text-sm font-bold text-primary hover:bg-primary-soft focus-visible:outline-2 focus-visible:outline-ring"
			>하루 상세 보기</a
		>
	</div>
	<div class="mt-5 flex flex-wrap items-start justify-between gap-5">
		<div class="min-w-0">
			<h2 class="text-2xl font-bold sm:text-[30px]">{sleepDateLabel(date)} 수면 상세</h2>
		</div>
		<div class="w-full sm:w-auto sm:min-w-60">
			<label for="sleep-detail-date" class="sr-only">날짜 선택</label>
			<select
				id="sleep-detail-date"
				value={date}
				disabled={isLoading}
				onchange={handleDateSelect}
				class="sleep-date-select min-h-12 w-full cursor-pointer rounded-xl border border-border bg-card px-5 text-base font-bold text-text-primary focus-visible:outline-2 focus-visible:outline-ring"
			>
				{#if !sleepDetailDates.includes(date)}<option value={date} disabled
						>{date.replaceAll('-', '.')}</option
					>{/if}
				{#each sleepDetailDates as choice (choice)}<option value={choice}
						>{choice.replaceAll('-', '.')}</option
					>{/each}
			</select>
		</div>
	</div>
	{#if isLoading}<div class="mt-6">
			<HealthLoading refreshing={hasLoaded} message="수면 상세를 불러오는 중이에요." />
		</div>{/if}
	{#if notice || data?.sleep.status === 'rejected' || data?.oxygen.status === 'rejected' || data?.timeline.status === 'rejected'}
		<div
			role="alert"
			class="mt-6 rounded-xl border border-transparent bg-danger-soft p-5 text-sm text-text-primary"
		>
			{#if notice}<p>{notice}</p>{/if}
			{#if data}
				{#each [['수면', data.sleep], ['산소포화도', data.oxygen], ['수면 단계', data.timeline]] as [label, result] (label)}
					{#if typeof result !== 'string' && result.status === 'rejected'}<p>
							{label}: {sleepRequestError(result.reason)}
						</p>{/if}
				{/each}
			{/if}
			<button
				type="button"
				onclick={() => retry++}
				class="mt-3 cursor-pointer rounded-xl border border-border bg-card px-4 py-2 font-bold hover:bg-surface focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text-primary"
				>다시 시도</button
			>
		</div>
	{/if}
	{#if data?.sleep.status === 'rejected' && oxygen}
		<p class="mt-6 text-sm">
			수면 중 평균 산소포화도: {oxygen.missing
				? sleepMissingText(oxygen.reason)
				: `${oxygen.value}%`}
		</p>
	{/if}
	{#if !isLoading && sleep && !night}
		<p
			class="mt-8 grid min-h-64 place-items-center rounded-3xl border border-border bg-card p-6 text-center text-text-secondary"
		>
			{sleepMissingText(sleepDay?.reason, 'record')}
		</p>
	{:else if !isLoading && night}
		<section aria-label="수면 요약" class="mt-8">
			<dl class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 lg:gap-6">
				{#each summary as item (item.label)}<div
						class="min-w-0 rounded-2xl border border-border bg-card p-6"
					>
						<dt class="text-sm font-bold text-text-secondary">{item.label}</dt>
						<dd class="mt-4 text-[25px] font-bold">{item.value}</dd>
					</div>{/each}
			</dl>
		</section>
		{#if data?.timeline.status === 'fulfilled'}
			<div class="mt-8"><SleepStageTimeline {timeline} /></div>
			<section aria-label="단계별 요약" class="mt-8">
				<dl class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
					{#each sleepStageSummaryOrder as stage (stage)}
						{@const percentage =
							stage === 'awake' ? null : sleepStagePercentage(durations[stage], night.sleepMinutes)}
						<div class="min-w-0 rounded-2xl border border-border bg-card p-5">
							<dt class="flex items-center gap-3 text-sm font-bold text-text-secondary">
								<span
									aria-hidden="true"
									class={['size-3 rounded-sm', stage === 'awake' ? 'border border-border' : '']}
									style:background={sleepStageStyles[stage].color}
								></span>{sleepStageStyles[stage].label}
							</dt>
							<dd class="mt-4 flex flex-wrap items-center justify-between gap-2">
								<span class="text-[22px] font-bold">{formatSleepMinutes(durations[stage])}</span
								>{#if percentage !== null}<span class="text-xs text-text-secondary"
										>전체 수면의 {percentage}%</span
									>{/if}
							</dd>
						</div>
					{/each}
				</dl>
			</section>
		{/if}
	{/if}
	{#if !isLoading && data?.sleep.status === 'rejected' && data.timeline.status === 'fulfilled' && timeline?.mainSleep}
		<div class="mt-8"><SleepStageTimeline {timeline} /></div>
	{/if}
	<p class="mt-7 text-sm text-text-secondary">
		이 기록은 진단을 위한 정보가 아니라 생활 습관을 돌아볼 때 참고할 수 있는 수면 기록이에요.
	</p>
</main>

<style>
	/* 지원하지 않는 브라우저는 기존 native select 동작을 유지한다. */
	@supports (appearance: base-select) {
		.sleep-date-select,
		.sleep-date-select::picker(select) {
			appearance: base-select;
		}

		.sleep-date-select {
			anchor-name: --sleep-detail-date;
			align-items: center;
			white-space: nowrap;
		}

		.sleep-date-select::picker-icon {
			color: var(--color-text-secondary);
			font-size: 0.75rem;
		}

		.sleep-date-select::picker(select) {
			inset: auto;
			position-area: none;
			position-anchor: --sleep-detail-date;
			position-try-order: normal;
			top: anchor(bottom);
			left: anchor(left);
			width: anchor-size(width);
			max-width: calc(100vw - 2rem);
			max-height: min(18rem, 50dvh);
			margin: 6px 0 0;
			padding: 4px;
			overflow-y: auto;
			border: 1px solid var(--color-border);
			border-radius: 12px;
			background: var(--color-card);
			box-shadow: 0 2px 2px 0 var(--color-border);
			position-try-fallbacks: flip-block, flip-inline;
		}

		.sleep-date-select option {
			min-height: 36px;
			padding: 8px 12px;
			border-radius: 8px;
			color: var(--color-text-primary);
			font-size: 14px;
			font-weight: 400;
			line-height: 20px;
			white-space: nowrap;
		}

		.sleep-date-select option:hover,
		.sleep-date-select option:focus-visible {
			background: var(--color-surface);
			outline: 2px solid var(--color-border);
			outline-offset: -2px;
		}

		.sleep-date-select option:disabled {
			display: none;
		}

		.sleep-date-select option:checked {
			background: var(--color-primary-soft);
			color: var(--color-primary);
			font-weight: 600;
		}

		.sleep-date-select option::checkmark {
			margin-inline-start: auto;
			order: 1;
			color: var(--color-brand);
		}
	}
</style>
