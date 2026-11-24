<script lang="ts">
	import HealthLoading from '#lib/features/health/components/HealthLoading.svelte';
	import { healthSyncContext } from '#lib/features/health/sync-context.js';
	const healthSync = healthSyncContext();
	import { onDestroy, tick, untrack } from 'svelte';
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
	import { ApiError } from '#lib/api/errors.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import HealthPeriodNavigation from '#lib/features/health/components/HealthPeriodNavigation.svelte';
	import HealthCoachingCta from '#lib/features/health/components/HealthCoachingCta.svelte';
	import HealthPageHeader from '#lib/features/health/components/HealthPageHeader.svelte';
	import ActivitySteps from '#lib/features/health/components/ActivitySteps.svelte';
	import ActivityExercise from '#lib/features/health/components/ActivityExercise.svelte';
	import {
		activityToday,
		activityRecentWeek,
		activityPeriod,
		loadActivity
	} from '#lib/features/health/activity-api.js';
	import { adjacentDay, dayMissingValue } from '#lib/features/health/day-detail.js';
	import { formatActivityChange, groupActivityMissingDays } from '#lib/features/health/activity.js';

	const today = activityToday();
	const returnContext = untrack(() => readMetricReturn(page.state, today));
	const initialReferenceDate = untrack(() =>
		readHealthReferenceDate(page.url.searchParams.get('date'), today)
	);
	let referenceDate = $state(
		returnContext?.source === 'activity'
			? returnContext.referenceDate
			: (initialReferenceDate ?? today)
	);
	let isActive = true;
	let isNavigating = false;
	onDestroy(() => (isActive = false));
	let retry = $state(0);
	const week = $derived(activityRecentWeek(referenceDate, 0));
	let data = $state<Awaited<ReturnType<typeof loadActivity>> | null>(null);
	let isLoading = $state(true);
	let hasLoaded = $state(false);
	let notice = $state<{ message: string; requestId: string | null } | null>(null);
	const period = $derived(data ? activityPeriod(data.steps) : null);
	const missingGroups = $derived(
		data
			? [
					{ label: '걸음 수', groups: groupActivityMissingDays(data.steps.days) },
					{ label: '운동', groups: groupActivityMissingDays(data.exercise.days) }
				].filter((metric) => metric.groups.length > 0)
			: []
	);
	const stepSummary = $derived.by(() => {
		const average = data?.steps.summary.avg ?? null;
		const previous = data?.previous?.summary.avg ?? null;
		const change =
			average !== null && previous !== null && previous > 0
				? ((average - previous) / previous) * 100
				: null;
		return {
			average:
				average === null ? '집계된 평균 없음' : `${Math.round(average).toLocaleString('ko-KR')}보`,
			recordedDays: data?.steps.summary.daysWithData ?? 0,
			change: formatActivityChange(change)
		};
	});
	async function load(selected: typeof week, signal: AbortSignal) {
		// 같은 기간 재조회는 기존 콘텐츠와 스크롤을 유지한다.
		const hasCurrentData = untrack(
			() => data?.steps.period.start === selected.start && data.steps.period.end === selected.end
		);
		isLoading = !hasCurrentData;
		if (!hasCurrentData) data = null;
		notice = null;
		try {
			const result = await healthSync.read(signal, (requestSignal, client) =>
				loadActivity(selected, today, requestSignal, client)
			);
			if (signal.aborted) return;
			data = result;
			hasLoaded = true;
			await tick();
			if (!hasCurrentData && !signal.aborted && location.hash === '#exercise')
				document.getElementById('exercise')?.scrollIntoView();
		} catch (error) {
			if (signal.aborted) return;
			if (error instanceof ApiError && error.code === 'AUTH_REQUIRED')
				authSession.clearLocalSession();
			notice = {
				message:
					error instanceof ApiError && error.code
						? error.message
						: '활동 데이터를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.',
				requestId: error instanceof ApiError ? error.requestId : null
			};
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
		// 기존 HTML/SVG 날짜 링크만 위임하고 그래프·버튼의 동작은 추가하지 않는다.
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
		const metricReturn: MetricReturnContext = {
			source: 'activity',
			referenceDate,
			exercise: Boolean(link?.closest('#exercise'))
		};
		try {
			await goto(
				resolve(
					metricReturn.exercise ? '/(app)/health/activity#exercise' : '/(app)/health/activity'
				),
				{
					shallow: true,
					replace: true,
					reset: false,
					state: { ...page.state, metricReturn }
				}
			);
			if (
				isActive &&
				page.route.id === '/(app)/health/activity' &&
				referenceDate === metricReturn.referenceDate
			)
				await goto(resolve('/(app)/health/day/[date]', { date }), { state: { metricReturn } });
		} finally {
			isNavigating = false;
		}
	}
</script>

<svelte:head><title>활동 · 건강 데이터 | 캔디핏</title></svelte:head>

<main
	onclickcapture={(event) => void handleDayNavigate(event)}
	data-health-ui
	class="activity-main mx-auto w-full max-w-health-page px-5 pt-8 pb-12 sm:px-8 2xl:pt-10 2xl:pb-22 [&_[data-loading-card]>div:first-child>div:first-child>p:last-child]:sr-only [&_[data-loading-following]>div>div:nth-child(2)]:sr-only [&>header>div>p]:sr-only"
>
	<HealthPageHeader />
	<div class="activity-period mt-7">
		<HealthPeriodNavigation
			periodLabel={week.label}
			nextDisabled={referenceDate >= today}
			date={referenceDate}
			maxDate={today}
			onDateSelect={(date) => (referenceDate = date)}
			note="기록이 없는 날은 평균에서 제외해요. 날짜를 누르면 하루 상세 기록을 볼 수 있어요."
			onPrevious={() => (referenceDate = adjacentDay(referenceDate, -7))}
			onNext={() => (referenceDate = [adjacentDay(referenceDate, 7), today].sort()[0])}
		/>
	</div>
	{#if isLoading}
		{#if week.end === today}<p aria-hidden="true" class="mt-4 text-sm text-transparent">
				오늘의 걸음 수와 운동 시간은 수집 중이며 기간 평균에서 제외해요.
			</p>{/if}
		<div class="mt-5">
			<HealthLoading
				layout="activity"
				periodLabel={week.label}
				refreshing={hasLoaded}
				message="활동 기록을 불러오는 중이에요."
			/>
		</div>
	{:else if notice}<div
			role="alert"
			class="mt-6 min-w-0 rounded-xl border border-transparent bg-danger-soft p-5 text-sm wrap-anywhere text-text-primary"
		>
			<p>{notice.message}</p>
			{#if notice.requestId}<p class="mt-2">요청 ID: {notice.requestId}</p>{/if}
			<button
				type="button"
				class="mt-3 cursor-pointer rounded-xl border border-border bg-card px-4 py-2 font-bold hover:bg-surface focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text-primary"
				onclick={() => retry++}>다시 시도</button
			>
		</div>{/if}
	{#if data && period}
		{#if data.warnings.length}<div
				role="alert"
				class="mt-5 rounded-xl bg-danger-soft p-5 text-sm text-text-primary"
			>
				{data.warnings.join(' · ')} 데이터를 불러오지 못했어요.
				<button
					type="button"
					class="ml-2 cursor-pointer rounded-sm font-bold text-text-primary underline underline-offset-4 hover:bg-surface focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text-primary"
					onclick={() => retry++}>다시 시도</button
				>
			</div>{/if}
		{#if data.steps.days.some((day) => day.isInProgress)}<p
				class="mt-4 text-sm text-text-secondary"
			>
				오늘의 걸음 수와 운동 시간은 수집 중이며 기간 평균에서 제외해요.
			</p>{/if}
		<div class="mt-5">
			<ActivitySteps
				{period}
				summary={stepSummary}
				average={data.steps.summary.avg}
				referenceDate={today}
				recentDays={data.recent ?? []}
				hourlyDays={data.hours ?? []}
				weekdayError={data.recent === null}
				hourlyError={data.hours === null}
			/>
			{#if missingGroups.length > 0}
				<section
					aria-label="날짜별 기록 안내"
					class="mt-3 grid gap-x-8 gap-y-3 text-xs leading-5 text-text-secondary md:grid-cols-2"
				>
					{#each missingGroups as metric (metric.label)}
						<div>
							<h3 class="font-semibold">{metric.label}</h3>
							<ul aria-label={`${metric.label} 날짜별 기록 상태`} class="mt-1 space-y-1">
								{#each metric.groups as group (group.start)}
									<li
										aria-label={`${group.start}${group.start === group.end ? '' : `부터 ${group.end}까지`} · ${dayMissingValue(group.reason)}`}
									>
										<time datetime={group.start}>{group.start.replaceAll('-', '.')}</time
										>{#if group.end !== group.start}
											~ <time datetime={group.end}>{group.end.replaceAll('-', '.')}</time>{/if} · {dayMissingValue(
											group.reason
										)}
									</li>
								{/each}
							</ul>
						</div>
					{/each}
				</section>
			{/if}
		</div>
		<div id="exercise" class="mt-12 scroll-mt-[calc(var(--app-header-height)+1rem)] lg:mt-18">
			<ActivityExercise
				{period}
				exerciseDays={data.exercise.days}
				sessions={data.exercise.detail.sessions}
				serverTime={{
					avg: data.exercise.summary.avg,
					total: data.exercise.summary.total,
					values: data.exercise.days.map((day) => (day.missing ? null : day.value))
				}}
			/>
		</div>
	{/if}
	<div class="activity-cta mt-10 lg:mt-17">
		<HealthCoachingCta
			title="기록에서 궁금한 점을 발견했나요?"
			description="선택한 기간의 기록을 AI 코치에게 물어보세요."
			buttonLabel="내 활동에 대해 물어보기"
		/>
	</div>
</main>
