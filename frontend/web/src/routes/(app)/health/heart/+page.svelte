<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { isSleepDetailDate } from '#lib/features/health/sleep-detail.js';
	import {
		readHealthReferenceDate,
		readMetricReturn,
		shouldHandleHealthLink,
		type MetricReturnContext
	} from '#lib/features/health/health-navigation.js';
	import HealthLoading from '#lib/features/health/components/HealthLoading.svelte';
	import { healthSyncContext } from '#lib/features/health/sync-context.js';
	const healthSync = healthSyncContext();
	import HealthPeriodNavigation from '#lib/features/health/components/HealthPeriodNavigation.svelte';
	import HealthPageHeader from '#lib/features/health/components/HealthPageHeader.svelte';
	import HeartRateChart from '#lib/features/health/components/HeartRateChart.svelte';
	import HealthCoachingCta from '#lib/features/health/components/HealthCoachingCta.svelte';
	import { activityToday, activityRecentWeek } from '#lib/features/health/activity-api.js';
	import { getHeartMetric, heartRequestError } from '#lib/features/health/heart-api.js';
	import type { HeartMetricResult } from '#lib/features/health/heart.js';
	import { adjacentDay } from '#lib/features/health/day-detail.js';
	import { ApiError } from '#lib/api/errors.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';

	const today = activityToday();
	const returnContext = untrack(() => readMetricReturn(page.state, today));
	const initialReferenceDate = untrack(() =>
		readHealthReferenceDate(page.url.searchParams.get('date'), today)
	);
	let referenceDate = $state(
		returnContext?.source === 'heart'
			? returnContext.referenceDate
			: (initialReferenceDate ?? today)
	);
	let isActive = true;
	let isNavigating = false;
	onDestroy(() => (isActive = false));
	const week = $derived(activityRecentWeek(referenceDate, 0));
	let result = $state<HeartMetricResult | null>(null);
	const showProgressNote = $derived(result?.days.some((day) => day.isInProgress) ?? false);
	let isLoading = $state(true);
	let hasLoaded = $state(false);
	let notice = $state<string | null>(null);
	let retry = $state(0);

	async function load(selected: typeof week, signal: AbortSignal) {
		isLoading = true;
		result = null;
		notice = null;
		try {
			const data = await healthSync.read(signal, (requestSignal, client) =>
				getHeartMetric(selected.start, selected.end, requestSignal, client)
			);
			if (!signal.aborted) {
				result = data;
				hasLoaded = true;
			}
		} catch (error) {
			if (signal.aborted) return;
			if (error instanceof ApiError && error.code === 'AUTH_REQUIRED')
				authSession.clearLocalSession();
			notice = heartRequestError(error);
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

	async function handleDayNavigate(event: MouseEvent) {
		if (!shouldHandleHealthLink(event)) return;
		const link = event.target instanceof Element ? event.target.closest('a[href]') : null;
		const href = link?.getAttribute('href');
		if (!href) return;
		const target = new URL(href, page.url.href);
		const date = target.pathname.slice(-10);
		if (
			!isSleepDetailDate(date) ||
			date > today ||
			target.origin !== page.url.origin ||
			target.pathname !== resolve('/(app)/health/day/[date]', { date })
		)
			return;
		event.preventDefault();
		if (isNavigating) return;
		isNavigating = true;
		const metricReturn: MetricReturnContext = { source: 'heart', referenceDate, exercise: false };
		try {
			await goto(resolve('/(app)/health/heart'), {
				shallow: true,
				replace: true,
				reset: false,
				state: { ...page.state, metricReturn }
			});
			if (
				isActive &&
				page.route.id === '/(app)/health/heart' &&
				referenceDate === metricReturn.referenceDate
			)
				await goto(resolve('/(app)/health/day/[date]', { date }), { state: { metricReturn } });
		} finally {
			isNavigating = false;
		}
	}
</script>

<svelte:head><title>심박 · 건강 데이터 | 캔디핏</title></svelte:head>

<main
	onclickcapture={(event) => void handleDayNavigate(event)}
	data-health-ui
	class="heart-page mx-auto flex w-full max-w-health-page flex-1 flex-col px-5 pt-8 pb-6 sm:px-8 2xl:pt-10 [&>header>div>p]:sr-only"
>
	<HealthPageHeader />
	<div class="heart-period mt-7">
		<HealthPeriodNavigation
			periodLabel={week.label}
			note="기록이 없는 날은 평균에서 제외해요. 날짜를 누르면 하루 상세 기록을 볼 수 있어요."
			nextDisabled={referenceDate >= today}
			date={referenceDate}
			maxDate={today}
			onDateSelect={(date) => (referenceDate = date)}
			onPrevious={() => (referenceDate = adjacentDay(referenceDate, -7))}
			onNext={() => (referenceDate = [adjacentDay(referenceDate, 7), today].sort()[0])}
		/>
	</div>
	{#if showProgressNote}<p class="mt-4 text-sm text-text-secondary">
			오늘 기록은 수집 중이며 기간 평균에서 제외해요.
		</p>{:else if isLoading && week.end === today}<p
			aria-hidden="true"
			class="mt-4 text-sm text-transparent"
		>
			오늘 기록은 수집 중이며 기간 평균에서 제외해요.
		</p>{/if}
	<div
		class={[
			'heart-content flex flex-col gap-8',
			showProgressNote || (isLoading && week.end === today) ? 'mt-5' : 'mt-6'
		]}
	>
		{#if isLoading}
			<HealthLoading
				layout="heart"
				periodLabel={week.label}
				refreshing={hasLoaded}
				message="심박 기록을 불러오는 중이에요."
			/>
		{/if}
		{#if notice}
			<div
				role="alert"
				class="rounded-xl border border-transparent bg-danger-soft p-5 text-sm text-text-primary"
			>
				<p>{notice}</p>
				<button
					type="button"
					onclick={() => retry++}
					class="mt-3 cursor-pointer rounded-xl border border-border bg-card px-4 py-2 font-bold hover:bg-surface focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text-primary"
					>다시 시도</button
				>
			</div>
		{/if}
		{#if result}
			<HeartRateChart {result} />
		{/if}
		<HealthCoachingCta
			title="기록에서 궁금한 점을 발견했나요?"
			description="AI 코치에게 심박 기록에 대해 궁금한 점을 물어보세요."
			buttonLabel="AI 코치로 이동"
		/>
	</div>
</main>

<style>
	@media (min-width: 1024px) {
		.heart-content {
			gap: 24px;
		}
	}
	@media (min-width: 1024px) and (max-height: 900px) {
		.heart-page {
			padding-bottom: 16px;
		}
		.heart-content {
			gap: 16px;
		}
	}
</style>
