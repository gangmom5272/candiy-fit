<script lang="ts">
	import { healthSyncContext } from '#lib/features/health/sync-context.js';
	const healthSync = healthSyncContext();
	import { appConnectionContext } from '#lib/features/health/app-connection-context.js';
	import HealthDataPreparationNotice from '#lib/features/health/components/HealthDataPreparationNotice.svelte';
	import { isHealthDataNotReady } from '#lib/features/health/data-readiness.js';
	const connection = appConnectionContext();
	import HealthMetricCard from '#lib/features/home/HealthMetricCard.svelte';
	import HomeHighlights from '#lib/features/home/HomeHighlights.svelte';
	import HomeScoreSummary from '#lib/features/home/HomeScoreSummary.svelte';
	import healthDashboard from '#lib/assets/images/health-dashboard.webp';
	import footIcon from '#lib/assets/icons/icon-foot.svg';
	import exerciseIcon from '#lib/assets/icons/icon-exercise.svg';
	import sleepIcon from '#lib/assets/icons/icon-sleep.svg';
	import heartIcon from '#lib/assets/icons/icon-heart.svg';
	import { healthSummaryMetrics } from '#lib/features/health/health-summary-api.js';
	import type { HealthMetricDay } from '#lib/features/health/day-detail.js';
	import { activityToday } from '#lib/features/health/activity-api.js';
	import { dayMissingValue } from '#lib/features/health/day-detail.js';
	import {
		getLifestyleScore,
		getHomeMetric,
		type HomeDailyMetric,
		homeScoreStatuses,
		homeRequestError,
		type LifestyleScore
	} from '#lib/features/home/home-api.js';
	import { ApiError } from '#lib/api/errors.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';

	const icons = [footIcon, exerciseIcon, sleepIcon, heartIcon];
	let today = $state(activityToday());
	let score = $state<LifestyleScore | null>(null);
	let scoreLoading = $state(true);
	let scoreError = $state<string | null>(null);
	let scorePreparing = $state(false);
	let results = $state<Partial<Record<HomeDailyMetric, PromiseSettledResult<HealthMetricDay>>>>({});
	const statuses = $derived(homeScoreStatuses(score, scoreLoading, scoreError, scorePreparing));
	const total = $derived(score?.total.score ?? null);
	const hasPreparingData = $derived(
		scorePreparing ||
			Object.values(results).some(
				(result) => result?.status === 'rejected' && isHealthDataNotReady(result.reason)
			)
	);
	// 상태 조회 이후 기록이 도착한 경우에도 이미 표시 중인 실제 기록과 안내가 모순되지 않게 한다.
	const hasRecords = $derived(
		total !== null ||
			(score !== null && score.areas.heart.sleepHrAvg !== null) ||
			(['steps', 'sleeps'] as const).some((metric) => {
				const result = results[metric];
				return (
					result?.status === 'fulfilled' && !result.value.missing && result.value.value !== null
				);
			})
	);
	const scoreLabel = $derived(
		scoreLoading
			? '불러오는 중'
			: scorePreparing
				? '준비 중'
				: scoreError
					? '조회 실패'
					: total === null
						? dayMissingValue(score?.reason ?? null)
						: `${total}`
	);
	const sleepHr = $derived(score?.areas.heart.sleepHrAvg ?? null);
	const heartValue = $derived(
		scoreLoading
			? '불러오는 중'
			: scorePreparing
				? '준비 중'
				: scoreError
					? '조회 실패'
					: sleepHr === null
						? dayMissingValue(score?.reason ?? null)
						: `${sleepHr} bpm`
	);

	async function loadScore(date: string, signal: AbortSignal) {
		try {
			const data = await healthSync.read(signal, (requestSignal, client) =>
				getLifestyleScore(date, requestSignal, client)
			);
			if (!signal.aborted) score = data;
		} catch (error) {
			if (signal.aborted) return;
			if (error instanceof ApiError && error.code === 'AUTH_REQUIRED')
				authSession.clearLocalSession();
			scoreError = homeRequestError(error);
			scorePreparing = isHealthDataNotReady(error);
		} finally {
			if (!signal.aborted) scoreLoading = false;
		}
	}
	async function loadMetric(metric: HomeDailyMetric, date: string, signal: AbortSignal) {
		try {
			const value = await healthSync.read(signal, (requestSignal, client) =>
				getHomeMetric(metric, date, requestSignal, client)
			);
			if (!signal.aborted) results[metric] = { status: 'fulfilled', value };
		} catch (reason) {
			if (signal.aborted) return;
			if (reason instanceof ApiError && reason.code === 'AUTH_REQUIRED')
				authSession.clearLocalSession();
			results[metric] = { status: 'rejected', reason };
		}
	}
	$effect(() => {
		void healthSync.revision;
		const date = today;
		const controller = new AbortController();
		scoreLoading = true;
		scoreError = null;
		scorePreparing = false;
		score = null;
		results = {};
		void loadScore(date, controller.signal);
		for (const metric of healthSummaryMetrics)
			if (metric !== 'heart_rates') void loadMetric(metric, date, controller.signal);
		return () => controller.abort();
	});
</script>

<svelte:head>
	<title>홈 | 캔디핏</title>
</svelte:head>

<main
	data-health-ui
	class="home-main mx-auto flex w-full max-w-health-page flex-col gap-4 px-5 pt-4 sm:px-8"
>
	{#if hasPreparingData}
		<HealthDataPreparationNotice
			connected={connection?.value?.status === 'CONNECTED'}
			waitingForFirstRecord={connection?.value?.firstRecord?.status === 'UNCONFIRMED' &&
				!hasRecords}
			rechecking={healthSync.rechecking}
			onRecheck={() => void healthSync.recheck()}
		/>
	{/if}
	<section
		class="home-hero grid items-center gap-4 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]"
		aria-labelledby="home-title"
	>
		<div class="hero-score flex flex-wrap items-center gap-6">
			<div class="hero-copy grow">
				<div class="hero-score-body">
					<h1 id="home-title" class="hero-title leading-[1.18] font-extrabold">오늘의 점수</h1>
					<p
						class="hero-total mt-4 font-medium"
						class:hero-total--status={total === null}
						aria-label={total === null
							? `오늘의 점수 ${scoreLabel}`
							: `오늘의 점수 100점 만점에 ${total}점`}
					>
						{#if scoreLoading}<span role="status"
								><span class="sr-only">{scoreLabel}</span><span
									aria-hidden="true"
									class="health-skeleton inline-block h-[1lh] w-32"
								></span></span
							>{:else}<span class="font-bold text-brand"
								>{scoreLabel}
							</span>{/if}{#if total !== null}<span class="score-denominator text-text-disabled">
								/ 100</span
							>{/if}
					</p>
				</div>
			</div>
			<HomeScoreSummary
				score={total}
				{statuses}
				notice={scoreError ??
					(scoreLoading
						? '점수를 불러오는 중이에요.'
						: score?.reason === 'UNSYNCED'
							? '건강 기록의 동기화를 기다리고 있어요.'
							: null)}
			/>
		</div>
		<img
			src={healthDashboard}
			alt=""
			width="512"
			height="384"
			class="hero-image h-auto w-full max-w-lg justify-self-center object-contain md:max-w-sm md:justify-self-start"
			fetchpriority="high"
		/>
		{#if scoreError && !scorePreparing}
			<p role="alert" class="col-span-full text-xs leading-relaxed text-text-secondary">
				{scoreError}
			</p>
		{/if}
	</section>

	<section class="health-records mb-2 md:mb-4" aria-label="최근 건강 기록">
		<div class="grid grid-cols-1 gap-3 min-[360px]:grid-cols-2 md:gap-4 lg:grid-cols-4 lg:gap-6">
			{#each healthSummaryMetrics as metric, index (metric)}
				{@const result = metric === 'heart_rates' ? undefined : results[metric]}
				{@const day = result?.status === 'fulfilled' ? result.value : undefined}
				{@const preparing = result?.status === 'rejected' && isHealthDataNotReady(result.reason)}
				<HealthMetricCard
					loading={metric === 'heart_rates' ? scoreLoading : !result}
					{metric}
					{day}
					{preparing}
					icon={icons[index]}
					value={metric === 'heart_rates' ? heartValue : undefined}
					isStatus={metric === 'heart_rates'
						? scoreLoading || scoreError !== null || sleepHr === null
						: undefined}
					notice={result?.status === 'rejected' && !preparing
						? homeRequestError(result.reason)
						: undefined}
				/>
			{/each}
		</div>
	</section>

	<HomeHighlights onDateChange={(date) => (today = date)} />
</main>

<footer
	class="home-footer mx-auto flex w-full max-w-health-page justify-end gap-5 px-5 pt-6 pb-4 text-sm text-text-secondary sm:px-8"
>
	<button type="button" disabled>이용약관</button>
	<button type="button" disabled>개인정보처리방침</button>
</footer>

<style>
	.hero-title {
		font-size: clamp(1.75rem, 3.1vw, 3.5rem);
	}

	.hero-total {
		--score-size: clamp(2rem, 3.5vw, 4rem);
		font-size: var(--score-size);
		text-align: center;
	}

	.hero-total--status {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 1lh;
		white-space: nowrap;
	}
	.hero-total--status > span {
		font-size: 1.25rem;
	}

	.hero-copy,
	.hero-score-body {
		min-width: 0;
	}

	@media (width < 64rem) {
		.home-hero {
			grid-template-columns: minmax(0, 1fr) clamp(8rem, calc(50vw - 3.25rem), 10rem);
			gap: 0.75rem;
		}
		.hero-score {
			display: contents;
		}
		.hero-copy {
			grid-column: 1;
			grid-row: 1;
		}
		.hero-total {
			margin-top: 0.5rem;
			text-align: left;
		}
		.hero-total--status {
			justify-content: flex-start;
			white-space: normal;
		}
		.hero-image {
			grid-column: 2;
			grid-row: 1;
		}
		.hero-score :global(.score-summary) {
			grid-column: 1 / -1;
			grid-row: 2;
		}
	}

	@media (40rem <= width < 64rem) {
		.home-hero {
			grid-template-columns: minmax(0, 1fr) clamp(10rem, 25vw, 16rem);
		}
		.hero-title {
			font-size: clamp(1.75rem, 3.5vw, 2rem);
		}
		.hero-image {
			justify-self: end;
		}
	}

	@media (min-width: 64rem) {
		.hero-title {
			font-size: clamp(1.5rem, 3.1vw, 3.5rem);
		}
	}

	@media (min-width: 64rem) {
		/* 1080px의 Normal에서 768px의 Compact까지 같은 세로 밀도를 상속한다.
		   312px은 두 높이 사이의 차이이며, 그 아래에서는 읽기 쉬운 최소 크기를 유지한다. */
		.home-main,
		.home-footer {
			--home-compression: clamp(0px, calc(67.5rem - 100dvh), 19.5rem);
		}
		.home-main {
			--home-hero-width: calc(32rem - var(--home-compression) * 112 / 312);
			--home-hero-bleed: calc(2.5rem - var(--home-compression) * 16 / 312);
			gap: 0;
			padding-top: calc(1.5rem - var(--home-compression) * 16 / 312);
		}
		.home-hero {
			grid-template-columns: repeat(2, minmax(0, 1fr));
			column-gap: 3.25rem;
		}
		.hero-score {
			flex-wrap: nowrap;
			justify-content: space-between;
			column-gap: 4rem;
			padding-top: calc(1.75rem - var(--home-compression) * 28 / 312);
			padding-bottom: calc(var(--home-compression) * 32 / 312);
		}
		.hero-image {
			max-width: var(--home-hero-width);
			/* 4:3 이미지의 위아래 투명 여백만 flow에서 제외한다.
			   slot = image height - 2 × bleed (Normal 304px / Compact 252px). */
			margin-block: calc(-1 * var(--home-hero-bleed));
		}
		.health-records {
			/* Hero와 하단 배너 사이의 간격을 관리한다. */
			margin-top: calc(1.3125rem - var(--home-compression) * 13 / 312);
			margin-bottom: calc(2.5rem - var(--home-compression) * 8 / 312);
		}
		.home-footer {
			padding-top: 1.5rem;
			padding-bottom: calc(3rem - var(--home-compression) * 42 / 312);
			font-size: 0.9375rem;
			line-height: normal;
		}
	}

	@media (min-width: 80rem) {
		.hero-score {
			/* 점수 본문과 상태 박스를 같은 행에서 중앙 정렬한다. */
			display: grid;
			grid-template-columns: 17.375rem 13rem;
			align-items: center;
			padding-right: 0.5rem;
		}
		.hero-copy {
			align-self: stretch;
			width: 17.375rem;
		}
		.hero-score :global(.score-summary) {
			grid-column: 2;
			grid-row: 1;
			align-self: center;
			margin-block: 0;
		}
		.hero-title {
			margin-top: 0;
			font-size: 3.75rem;
			font-weight: 700;
			line-height: normal;
		}
		.hero-total {
			--score-size: 3.75rem;
			margin-top: 0.5rem;
			line-height: normal;
		}
		.score-denominator {
			font-size: 2.75rem;
		}
	}
</style>
