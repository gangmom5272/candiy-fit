<script lang="ts">
	import { onDestroy, tick, untrack } from 'svelte';
	import { afterNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { healthSyncContext } from '#lib/features/health/sync-context.js';
	const healthSync = healthSyncContext();
	import { appConnectionContext } from '#lib/features/health/app-connection-context.js';
	import { isHealthDataNotReady } from '#lib/features/health/data-readiness.js';
	import HealthDataPreparationNotice from '#lib/features/health/components/HealthDataPreparationNotice.svelte';
	const connection = appConnectionContext();
	import HealthCoachingCta from '#lib/features/health/components/HealthCoachingCta.svelte';
	import HealthSummaryCard from '#lib/features/health/components/HealthSummaryCard.svelte';
	import HealthPageHeader from '#lib/features/health/components/HealthPageHeader.svelte';
	import HealthPeriodNavigation from '#lib/features/health/components/HealthPeriodNavigation.svelte';
	import { adjacentDay } from '#lib/features/health/day-detail.js';
	import {
		getHealthSummary,
		healthSummaryCards,
		healthSummaryMetrics,
		healthSummaryError,
		type HealthSummaryMetric
	} from '#lib/features/health/health-summary-api.js';
	import type { HealthSummary } from '#lib/features/health/summary.js';
	import { activityToday, activityRecentWeek } from '#lib/features/health/activity-api.js';
	import { ApiError } from '#lib/api/errors.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import {
		readHealthReturn,
		shouldHandleHealthLink,
		type HealthReturnContext
	} from '#lib/features/health/health-navigation.js';

	const today = activityToday();
	const returnContext = untrack(() => readHealthReturn(page.state, today));
	let referenceDate = $state(returnContext?.referenceDate ?? today);
	let restorePending = $state(returnContext !== null);
	let navigationReady = $state(false);
	let isActive = true;
	let isNavigating = false;
	onDestroy(() => (isActive = false));
	afterNavigate(() => (navigationReady = true));
	let periodChanged = $state(false);
	const week = $derived(activityRecentWeek(referenceDate, 0));
	const summaryPeriodLabel = $derived(
		`${week.label} · 기준일 ${referenceDate.replaceAll('-', '.')}`
	);
	function selectPeriod(date: string) {
		restorePending = false;
		if (date !== referenceDate) periodChanged = true;
		referenceDate = date;
	}
	let results = $state<Partial<Record<HealthSummaryMetric, PromiseSettledResult<HealthSummary>>>>(
		{}
	);
	const hasPreparingData = $derived(
		Object.values(results).some(
			(result) => result?.status === 'rejected' && isHealthDataNotReady(result.reason)
		)
	);
	const hasRecords = $derived(
		Object.values(results).some(
			(result) =>
				result?.status === 'fulfilled' && result.value.values.some((value) => value !== null)
		)
	);
	let controller: AbortController | undefined;
	async function load(metric: HealthSummaryMetric, selected: typeof week, signal: AbortSignal) {
		results[metric] = undefined;
		try {
			const value = await healthSync.read(signal, (requestSignal, client) =>
				getHealthSummary(metric, selected.start, selected.end, requestSignal, client)
			);
			if (!signal.aborted) results[metric] = { status: 'fulfilled', value };
		} catch (reason) {
			if (signal.aborted) return;
			if (reason instanceof ApiError && reason.code === 'AUTH_REQUIRED')
				authSession.clearLocalSession();
			results[metric] = { status: 'rejected', reason };
		}
	}
	function retry(metric: HealthSummaryMetric) {
		if (controller && !controller.signal.aborted) void load(metric, week, controller.signal);
	}
	const dayHref = (date: string) => resolve('/(app)/health/day/[date]', { date });
	async function navigateWithReturn(
		event: MouseEvent,
		target: string,
		section: HealthSummaryMetric,
		date: string
	) {
		if (!shouldHandleHealthLink(event)) return;
		event.preventDefault();
		if (isNavigating) return;
		isNavigating = true;
		const chartScrollLeft: HealthReturnContext['chartScrollLeft'] = {};
		for (const metric of healthSummaryMetrics) {
			chartScrollLeft[metric] = Math.max(
				0,
				document
					.getElementById(`health-summary-${metric}`)
					?.querySelector<HTMLElement>('[data-health-chart-scroll]')?.scrollLeft ?? 0
			);
		}
		const healthReturn: HealthReturnContext = {
			referenceDate,
			section,
			date,
			scrollY: Math.max(0, window.scrollY),
			chartScrollLeft
		};
		try {
			// 원래 방문 항목에도 UI 상태를 남겨 브라우저 뒤로 가기와 앱 복귀를 함께 지원한다.
			await goto(resolve('/(app)/health'), {
				shallow: true,
				replace: true,
				reset: false,
				state: { ...page.state, healthReturn }
			});
			if (isActive && page.route.id === '/(app)/health') {
				// dayHref 또는 resolve한 카드 링크에서 받은 동일 origin의 경로다.
				// eslint-disable-next-line svelte/no-navigation-without-resolve
				await goto(target, { state: { healthReturn } });
			}
		} finally {
			isNavigating = false;
		}
	}
	function handleDayNavigate(event: MouseEvent, date: string, section: HealthSummaryMetric) {
		return navigateWithReturn(event, dayHref(date), section, date);
	}
	function handleMetricNavigate(event: MouseEvent) {
		if (!shouldHandleHealthLink(event)) return;
		const link =
			event.target instanceof Element
				? event.target.closest<HTMLAnchorElement>(
						'a.health-summary-link, a[data-metric-target], a[data-metric-frame-target]'
					)
				: null;
		const section = healthSummaryMetrics.find(
			(metric) => link?.closest('.health-summary-card')?.id === `health-summary-${metric}`
		);
		if (!link || !section) return;
		const target = new URL(link.href, page.url.href);
		if (target.origin !== page.url.origin) return;
		return navigateWithReturn(
			event,
			`${target.pathname}${target.search}${target.hash}`,
			section,
			referenceDate
		);
	}
	$effect(() => {
		void healthSync.revision;
		const selected = week;
		const active = new AbortController();
		controller = active;
		results = {};
		untrack(() => {
			for (const metric of healthSummaryMetrics) void load(metric, selected, active.signal);
		});
		return () => active.abort();
	});
	$effect(() => {
		if (
			!restorePending ||
			!navigationReady ||
			!returnContext ||
			!healthSummaryMetrics.every((metric) => results[metric] !== undefined)
		)
			return;
		let cancelled = false;
		void tick().then(() => {
			if (
				cancelled ||
				!isActive ||
				page.route.id !== '/(app)/health' ||
				referenceDate !== returnContext.referenceDate
			)
				return;
			for (const metric of healthSummaryMetrics) {
				const chart = document
					.getElementById(`health-summary-${metric}`)
					?.querySelector<HTMLElement>('[data-health-chart-scroll]');
				if (chart) chart.scrollLeft = returnContext.chartScrollLeft[metric] ?? 0;
			}
			document
				.getElementById(`health-summary-${returnContext.section}`)
				?.querySelector<HTMLElement>(`[data-date-target="${returnContext.date}"]`)
				?.focus({ preventScroll: true });
			window.scrollTo({ top: returnContext.scrollY, behavior: 'instant' });
			restorePending = false;
		});
		return () => {
			cancelled = true;
		};
	});
</script>

<svelte:head><title>건강 데이터 | 캔디핏</title></svelte:head>

<main
	onclickcapture={(event) => void handleMetricNavigate(event)}
	data-health-ui
	class="mx-auto w-full max-w-health-page px-5 pt-8 pb-6 sm:px-8 2xl:pt-10 [&>header>div>p]:sr-only"
>
	<HealthPageHeader />
	<div class="mt-7">
		<HealthPeriodNavigation
			periodLabel={week.label}
			date={referenceDate}
			maxDate={today}
			nextDisabled={referenceDate >= today}
			onDateSelect={selectPeriod}
			onPrevious={() => selectPeriod(adjacentDay(referenceDate, -7))}
			onNext={() => selectPeriod([adjacentDay(referenceDate, 7), today].sort()[0])}
			note="기록이 없는 날은 평균에서 제외해요. 선택한 날짜까지 최근 7일을 보여줘요."
		/>
	</div>
	{#if hasPreparingData}
		<div class="mt-6">
			<HealthDataPreparationNotice
				connected={connection?.value?.status === 'CONNECTED'}
				waitingForFirstRecord={connection?.value?.firstRecord?.status === 'UNCONFIRMED' &&
					!hasRecords}
				rechecking={healthSync.rechecking}
				onRecheck={() => void healthSync.recheck()}
			/>
		</div>
	{/if}
	<section
		aria-label="주간 건강 기록"
		class="mt-6 grid auto-rows-fr grid-cols-1 gap-8 lg:grid-cols-2 [&_#health-summary-exercises_.health-summary-link>div>div>p:first-child]:sr-only [&_#health-summary-exercises_.health-summary-link>p]:sr-only [&_#health-summary-sleeps_.health-summary-link>div>div>p:first-child]:sr-only [&_#health-summary-sleeps_.health-summary-link>p]:sr-only [&_#health-summary-steps_.health-summary-link>div>div>p:first-child]:sr-only"
	>
		{#each healthSummaryMetrics as metric (metric)}
			{@const result = results[metric]}
			<HealthSummaryCard
				sectionId={`health-summary-${metric}`}
				{referenceDate}
				{dayHref}
				onDayNavigate={(event, date) => void handleDayNavigate(event, date, metric)}
				loadingMessage={periodChanged ? '선택한 기간의 기록을 다시 불러오는 중이에요.' : undefined}
				label={healthSummaryCards[metric].label}
				summary={result?.status === 'fulfilled' ? result.value : undefined}
				error={result?.status === 'rejected' ? healthSummaryError(result.reason) : undefined}
				preparing={result?.status === 'rejected' && isHealthDataNotReady(result.reason)}
				onRetry={() => retry(metric)}
			/>
		{/each}
	</section>
	<p class="mt-6 text-[13px] leading-[normal] text-text-secondary">
		— 기록 없음<span class="sr-only"> · {summaryPeriodLabel}</span>
	</p>
	<div class="mt-7">
		<HealthCoachingCta
			title="내 건강에 맞는 생활 습관, AI 코치와 함께 찾아볼까요?"
			description="건강 데이터를 확인했다면 AI 코치에게 궁금한 점을 물어보세요."
			buttonLabel="AI 코치로 이동"
		/>
	</div>
</main>
