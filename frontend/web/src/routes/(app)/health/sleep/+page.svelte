<script lang="ts">
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { readHealthReferenceDate } from '#lib/features/health/health-navigation.js';
	import HealthLoading from '#lib/features/health/components/HealthLoading.svelte';
	import { healthSyncContext } from '#lib/features/health/sync-context.js';
	const healthSync = healthSyncContext();
	import HealthPeriodNavigation from '#lib/features/health/components/HealthPeriodNavigation.svelte';
	import HealthPageHeader from '#lib/features/health/components/HealthPageHeader.svelte';
	import HealthCoachingCta from '#lib/features/health/components/HealthCoachingCta.svelte';
	import SleepWeeklyFlow from '#lib/features/health/components/SleepWeeklyFlow.svelte';
	import SleepOxygenSaturation from '#lib/features/health/components/SleepOxygenSaturation.svelte';
	import { activityToday, activityRecentWeek } from '#lib/features/health/activity-api.js';
	import { loadSleepWeek, sleepRequestError } from '#lib/features/health/sleep-api.js';
	import { adjacentDay } from '#lib/features/health/day-detail.js';
	import { ApiError } from '#lib/api/errors.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import { sleepMissingText } from '#lib/features/health/sleep-detail.js';
	import {
		averageSleepClock,
		formatSleepMinutes,
		formatSleepPeriod,
		groupSleepMissingDays
	} from '#lib/features/health/sleep.js';

	const today = activityToday();
	let referenceDate = $state(
		untrack(() => readHealthReferenceDate(page.url.searchParams.get('date'), today)) ?? today
	);
	const week = $derived(activityRecentWeek(referenceDate, 0));
	let data = $state<Awaited<ReturnType<typeof loadSleepWeek>> | null>(null);
	let isLoading = $state(true);
	let hasLoaded = $state(false);
	let notice = $state<string | null>(null);
	let retry = $state(0);
	const sleep = $derived(data?.sleep.status === 'fulfilled' ? data.sleep.value : null);
	const oxygen = $derived(data?.oxygen.status === 'fulfilled' ? data.oxygen.value : null);
	const previous = $derived(data?.previous.status === 'fulfilled' ? data.previous.value : null);
	const period = $derived(formatSleepPeriod(week));
	const sleepMissingGroups = $derived(groupSleepMissingDays(sleep?.days ?? []));
	const oxygenMissingGroups = $derived(groupSleepMissingDays(oxygen?.days ?? []));
	const averageBedtime = $derived(
		averageSleepClock(sleep?.detail.nights.map((n) => n.startAt) ?? [])
	);
	const averageWakeTime = $derived(
		averageSleepClock(sleep?.detail.nights.map((n) => n.endAt) ?? [])
	);
	async function load(selected: typeof week, signal: AbortSignal) {
		isLoading = true;
		data = null;
		notice = null;
		try {
			const result = await healthSync.read(signal, (requestSignal, client) =>
				loadSleepWeek(selected, requestSignal, client)
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
		void load(week, controller.signal);
		return () => controller.abort();
	});
</script>

<svelte:head><title>수면 · 건강 데이터 | 캔디핏</title></svelte:head>

<main
	data-health-ui
	class="mx-auto flex w-full max-w-health-page flex-1 flex-col px-5 pt-8 pb-6 sm:px-8 2xl:pt-10 [&_[data-loading-card]>div:first-child>div:first-child>p:last-child]:sr-only [&>header>div>p]:sr-only"
>
	<HealthPageHeader />
	<div class="mt-7">
		<HealthPeriodNavigation
			periodLabel={week.label}
			note="주간은 취침·기상 흐름을 중심으로 보여줘요. 날짜를 선택하면 수면 단계를 확인할 수 있어요."
			nextDisabled={referenceDate >= today}
			date={referenceDate}
			maxDate={today}
			onDateSelect={(date) => (referenceDate = date)}
			onPrevious={() => (referenceDate = adjacentDay(referenceDate, -7))}
			onNext={() => (referenceDate = [adjacentDay(referenceDate, 7), today].sort()[0])}
		/>
	</div>
	{#if isLoading}<div class="mt-6">
			<HealthLoading
				layout="sleep"
				periodLabel={period}
				refreshing={hasLoaded}
				message="수면 기록을 불러오는 중이에요."
			/>
		</div>{/if}
	{#if notice || data?.sleep.status === 'rejected' || data?.oxygen.status === 'rejected' || data?.previous.status === 'rejected'}
		<div
			role="alert"
			class="mt-6 min-w-0 rounded-xl border border-transparent bg-danger-soft p-5 text-sm wrap-anywhere text-text-primary"
		>
			{#if notice}<p>{notice}</p>{/if}
			{#if data}
				{#each [['수면', data.sleep], ['산소포화도', data.oxygen], ['이전 기간 비교', data.previous]] as [label, result] (label)}
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
	<div class="mt-6 flex flex-col gap-8">
		{#if sleep}<SleepWeeklyFlow result={sleep} {previous} />{/if}
		<div class="grid min-w-0 gap-6 lg:grid-cols-[0.9fr_1.1fr] lg:grid-rows-[auto_auto] lg:gap-y-2">
			{#if sleep}
				<div
					role="group"
					aria-label="수면 기록 요약 및 안내"
					class="grid min-w-0 gap-2 lg:row-span-2 lg:grid-rows-subgrid"
				>
					<section
						aria-labelledby="sleep-summary-title"
						class="min-w-0 rounded-2xl border border-border bg-card px-4 py-6 sm:px-7"
					>
						<h2 id="sleep-summary-title" class="text-card-title">수면 기록 요약</h2>
						<p class="sr-only">{period} · 수집된 {sleep.summary.daysWithData}일 기준</p>
						<dl class="mt-4 divide-y divide-border">
							{#each [['평균 취침', averageBedtime ?? '기록 없음'], ['평균 기상', averageWakeTime ?? '기록 없음'], ['평균 수면 시간', formatSleepMinutes(sleep.summary.avg)]] as [label, value] (label)}
								<div class="flex flex-wrap items-center justify-between gap-2 py-3">
									<dt class="text-sm text-text-secondary">{label}</dt>
									<dd class="text-lg font-bold">{value}</dd>
								</div>
							{/each}
						</dl>
					</section>
					{#if sleepMissingGroups.length > 0}
						<ul
							aria-label="수면 날짜별 기록 안내"
							class="min-w-0 space-y-2 text-xs leading-5 break-words text-text-secondary"
						>
							{#each sleepMissingGroups as group (group.start)}
								<li
									aria-label={`${group.start}${group.start === group.end ? '' : `부터 ${group.end}까지`} · ${sleepMissingText(group.reason)}`}
								>
									수면 {formatSleepPeriod(group)}: {sleepMissingText(group.reason)}
								</li>
							{/each}
						</ul>
					{/if}
				</div>
			{/if}
			{#if oxygen}<div
					role="group"
					aria-label="산소포화도 카드 및 안내"
					class="grid min-w-0 gap-2 lg:row-span-2 lg:grid-rows-subgrid"
				>
					<SleepOxygenSaturation result={oxygen} />
					{#if oxygenMissingGroups.length > 0}
						<ul
							aria-label="산소포화도 날짜별 기록 안내"
							class="min-w-0 space-y-2 text-xs leading-5 break-words text-text-secondary"
						>
							{#each oxygenMissingGroups as group (group.start)}
								<li
									aria-label={`${group.start}${group.start === group.end ? '' : `부터 ${group.end}까지`} · ${sleepMissingText(group.reason)}`}
								>
									산소포화도 {formatSleepPeriod(group)}: {sleepMissingText(group.reason)}
								</li>
							{/each}
						</ul>
					{/if}
				</div>{/if}
		</div>
		<HealthCoachingCta
			title="기록에서 궁금한 점을 발견했나요?"
			description="AI 코치에게 수면 기록에 대해 궁금한 점을 물어보세요."
			buttonLabel="AI 코치로 이동"
		/>
	</div>
</main>
