<script lang="ts">
	import HealthLoading from '#lib/features/health/components/HealthLoading.svelte';
	import { healthSyncContext } from '#lib/features/health/sync-context.js';
	const healthSync = healthSyncContext();
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { goto } from '$app/navigation';
	import {
		readHealthReturn,
		readMetricReturn,
		readSleepReturn,
		shouldHandleHealthLink
	} from '#lib/features/health/health-navigation.js';
	import HealthCoachingCta from '#lib/features/health/components/HealthCoachingCta.svelte';
	import HealthMetricCard from '#lib/features/health/components/HealthMetricCard.svelte';
	import DailyHealthTimeline from '#lib/features/health/components/DailyHealthTimeline.svelte';
	import HealthBackLink from '#lib/features/health/components/HealthBackLink.svelte';
	import DayDatePicker from '#lib/features/health/components/DayDatePicker.svelte';
	import { loadDayDetail, dayRequestError } from '#lib/features/health/day-detail-api.js';
	import { activityToday } from '#lib/features/health/activity-api.js';
	import { ApiError } from '#lib/api/errors.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import {
		adjacentDay,
		dayMissingValue,
		formatDayMetric
	} from '#lib/features/health/day-detail.js';
	import { sleepDateLabel } from '#lib/features/health/sleep-detail.js';
	import { formatSleepMinutes } from '#lib/features/health/sleep.js';
	import leftIcon from '#lib/assets/icons/icon-chevron-left-small.svg';
	import rightIcon from '#lib/assets/icons/icon-chevron-right-small.svg';
	import stepsIcon from '#lib/assets/icons/icon-foot.svg';
	import exerciseIcon from '#lib/assets/icons/icon-exercise.svg';
	import sleepIcon from '#lib/assets/icons/icon-sleep.svg';
	import heartIcon from '#lib/assets/icons/icon-heart.svg';

	const date = $derived(page.params.date ?? '');
	const today = activityToday();
	const returnContext = $derived(readHealthReturn(page.state, today));
	const sleepReturnContext = $derived(readSleepReturn(page.state, today));
	const metricReturnContext = $derived(readMetricReturn(page.state, today));
	const metricReturnRoute = $derived(
		metricReturnContext?.source === 'heart'
			? '/(app)/health/heart'
			: metricReturnContext?.exercise
				? '/(app)/health/activity#exercise'
				: '/(app)/health/activity'
	);
	const returnState = $derived(
		sleepReturnContext
			? { sleepReturn: sleepReturnContext }
			: metricReturnContext
				? { metricReturn: metricReturnContext }
				: returnContext
					? { healthReturn: returnContext }
					: null
	);
	function handleReturn(event: MouseEvent) {
		if (!returnState || !shouldHandleHealthLink(event)) return;
		event.preventDefault();
		void goto(
			sleepReturnContext
				? resolve('/(app)/health/sleep/[date]', { date: sleepReturnContext.date })
				: metricReturnContext
					? resolve(metricReturnRoute)
					: resolve('/(app)/health'),
			{ state: returnState }
		);
	}
	function handleDateNavigate(event: MouseEvent, selected: string) {
		if (!returnState || !shouldHandleHealthLink(event)) return;
		event.preventDefault();
		void goto(resolve('/(app)/health/day/[date]', { date: selected }), {
			state: returnState
		});
	}
	let record = $state<Awaited<ReturnType<typeof loadDayDetail>> | null>(null);
	let isLoading = $state(true);
	let hasLoaded = $state(false);
	let notice = $state<string | null>(null);
	let retry = $state(0);
	const steps = $derived(record?.steps.status === 'fulfilled' ? record.steps.value : null);
	const exercise = $derived(record?.exercise.status === 'fulfilled' ? record.exercise.value : null);
	const sleep = $derived(record?.sleep.status === 'fulfilled' ? record.sleep.value : null);
	const heart = $derived(record?.heart.status === 'fulfilled' ? record.heart.value : null);
	const timeline = $derived(record?.timeline.status === 'fulfilled' ? record.timeline.value : null);
	const previousDate = $derived(date > '0001-01-01' ? adjacentDay(date, -1) : null);
	const nextDate = $derived(date < today ? adjacentDay(date, 1) : null);
	const summary = $derived([
		{
			label: '걸음 수',
			value: steps ? formatDayMetric(steps, 'steps') : '조회 실패',
			icon: stepsIcon
		},
		{
			label: '운동',
			value: exercise ? formatDayMetric(exercise, 'min') : '조회 실패',
			icon: exerciseIcon
		},
		{
			label: '총 수면',
			value: !sleep
				? '조회 실패'
				: sleep.missing || sleep.value === null
					? dayMissingValue(sleep.reason)
					: formatSleepMinutes(sleep.value),
			icon: sleepIcon
		},
		{
			label: '평균 심박',
			value: heart ? formatDayMetric(heart, 'bpm') : '조회 실패',
			icon: heartIcon
		}
	]);
	async function load(selected: string, signal: AbortSignal) {
		isLoading = true;
		record = null;
		notice = null;
		try {
			const data = await healthSync.read(signal, (requestSignal, client) =>
				loadDayDetail(selected, requestSignal, client)
			);
			if (!signal.aborted) {
				record = data;
				hasLoaded = true;
			}
		} catch (error) {
			if (signal.aborted) return;
			if (error instanceof ApiError && error.code === 'AUTH_REQUIRED')
				authSession.clearLocalSession();
			notice = dayRequestError(error);
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
</script>

<svelte:head><title>{sleepDateLabel(date)} 하루 상세 | 캔디핏</title></svelte:head>

<main data-health-ui class="mx-auto w-full max-w-health-page px-5 pt-8 pb-8 sm:px-8 2xl:pt-10">
	<h1 class="text-page-title">
		{sleepDateLabel(date)} 하루 상세
	</h1>
	<p class="mt-3 text-[15px] text-text-secondary">건강 데이터 / 상세 기록</p>
	<div class="mt-7 flex flex-wrap items-center justify-between gap-4">
		<HealthBackLink
			href={sleepReturnContext
				? '/(app)/health/sleep/[date]'
				: metricReturnContext
					? metricReturnRoute
					: '/(app)/health'}
			date={sleepReturnContext?.date}
			label="건강 데이터로"
			onclick={handleReturn}
		/>
		<nav aria-label="하루 기록 날짜 이동" class="flex items-center gap-2">
			{#if previousDate}<a
					href={resolve('/(app)/health/day/[date]', { date: previousDate })}
					onclick={(event) => handleDateNavigate(event, previousDate)}
					aria-label="이전 날짜"
					class="grid size-11 place-items-center rounded-lg hover:bg-primary-soft focus-visible:outline-2 focus-visible:outline-ring"
					><img src={leftIcon} alt="" width="24" height="24" /></a
				>{:else}<button
					type="button"
					aria-label="이전 날짜"
					disabled
					class="grid size-11 place-items-center opacity-40"
					><img src={leftIcon} alt="" width="24" height="24" /></button
				>{/if}
			{#key date}<DayDatePicker
					{date}
					maxDate={today}
					{today}
					onNavigate={handleDateNavigate}
				/>{/key}
			{#if nextDate}<a
					href={resolve('/(app)/health/day/[date]', { date: nextDate })}
					onclick={(event) => handleDateNavigate(event, nextDate)}
					aria-label="다음 날짜"
					class="grid size-11 place-items-center rounded-lg hover:bg-primary-soft focus-visible:outline-2 focus-visible:outline-ring"
					><img src={rightIcon} alt="" width="24" height="24" /></a
				>{:else}<button
					type="button"
					aria-label="다음 날짜"
					disabled
					class="grid size-11 place-items-center opacity-40"
					><img src={rightIcon} alt="" width="24" height="24" /></button
				>{/if}
		</nav>
	</div>
	{#if isLoading}<div class="mt-6">
			<HealthLoading refreshing={hasLoaded} message="하루 기록을 불러오는 중이에요." />
		</div>{/if}
	{#if notice || (record && Object.values(record).some((result) => result.status === 'rejected'))}
		<div
			role="alert"
			class="mt-6 rounded-xl border border-transparent bg-danger-soft p-5 text-sm text-text-primary"
		>
			{#if notice}<p>{notice}</p>{/if}
			{#if record}
				{#each [['걸음 수', record.steps], ['운동', record.exercise], ['수면', record.sleep], ['심박', record.heart], ['타임라인', record.timeline]] as [label, result] (label)}
					{#if typeof result !== 'string' && result.status === 'rejected'}<p>
							{label}: {dayRequestError(result.reason)}
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
	{#if record}<dl
			aria-label="하루 기록 요약"
			class="mt-7 grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-4 lg:gap-7"
		>
			{#each summary as metric (metric.label)}<HealthMetricCard {...metric} />{/each}
		</dl>{/if}
	{#if timeline}<div class="mt-6">
			{#key date}<DailyHealthTimeline {timeline} />{/key}
		</div>{/if}
	<div class="mt-7">
		<HealthCoachingCta
			keepFocusVisible
			title="기록에서 궁금한 점을 발견했나요?"
			description="건강 데이터를 확인했다면 AI 코치에게 궁금한 점을 물어보세요."
			buttonLabel="이날 기록에 대해 물어보기"
		/>
	</div>
	<p class="mt-6 flex items-start gap-2 text-xs text-text-secondary">
		<span class="mt-1 size-1.5 shrink-0 rounded-full bg-coral-main" aria-hidden="true"></span>빈
		구간은 표시할 기록이 없거나 시간대별 정보를 확인할 수 없는 경우예요. 운동 기록은 인정된 운동
		시간만 표시해요.
	</p>
</main>
