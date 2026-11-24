<script lang="ts">
	import ActivityTrendChart from './ActivityTrendChart.svelte';
	import ExcludedWalkingDisclosure from './ExcludedWalkingDisclosure.svelte';
	import { formatSleepMinutes } from '../sleep';
	import { dayMissingValue, type HealthMetricDay } from '../day-detail';
	import {
		exerciseSessions,
		formatCalories,
		formatExerciseHeartRate,
		getExerciseSummary,
		type ActivityPeriod,
		type ExerciseSession
	} from '../activity';
	let {
		period,
		sessions = exerciseSessions,
		serverTime,
		exerciseDays
	}: {
		period: ActivityPeriod;
		sessions?: readonly ExerciseSession[];
		serverTime?: { avg: number | null; total: number | null; values: (number | null)[] };
		exerciseDays?: readonly HealthMetricDay[];
	} = $props();
	const summary = $derived(getExerciseSummary(period, sessions, serverTime));
	// 종류 카드의 표시 상태만 구분하며 서버 집계와 원본 세션은 변경하지 않는다.
	const namedTypes = $derived(summary.types.filter((type) => type.label.trim().length > 0));
	const missingTypeReasons = $derived(
		exerciseDays
			?.filter((day) => (day.missing || day.value === null) && day.reason !== 'NO_RECORD')
			.map((day) => day.reason) ?? []
	);
	const hasRecognizedSessions = $derived(
		sessions.some(
			(session) => session.countsAsExercise && period.days.some((day) => day.date === session.date)
		)
	);
	const hasDuration = $derived(
		serverTime?.values.some((value) => value !== null && value > 0) ?? false
	);
	const incompleteTypes = $derived(
		namedTypes.length !== summary.types.length ||
			((hasDuration || missingTypeReasons.length > 0) && !summary.types.length)
	);
	const emptyTypesMessage = $derived.by(() => {
		if (hasRecognizedSessions || hasDuration) return '운동 종류 정보가 없어요.';
		return missingTypeReasons.length
			? [...new Set(missingTypeReasons)]
					.map((reason) =>
						reason === 'UNSYNCED'
							? '운동 기록이 아직 동기화되지 않았어요.'
							: dayMissingValue(reason)
					)
					.join(' · ')
			: '선택한 기간에 운동 기록이 없어요.';
	});
	const formatHeartRate = (value: number) => (value === 0 ? '—' : formatExerciseHeartRate(value));
</script>

<section
	aria-labelledby="activity-exercise-title"
	class="activity-exercise min-w-0 [&_.exercise-types-card>div:last-child>div>p]:sr-only"
>
	<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-6">
		<h2 id="activity-exercise-title" class="text-section-title">운동</h2>
	</div>
	<div class="mt-8 grid min-w-0 items-start gap-6 lg:grid-cols-2 lg:gap-8">
		{#each summary.metrics as metric (metric.kind)}
			<section
				aria-labelledby={`activity-${metric.kind}-title`}
				class="exercise-card min-w-0 rounded-3xl border border-border bg-card px-4 py-6 sm:px-7"
				class:self-stretch={metric.kind === 'time'}
			>
				<div class="flex items-center justify-between gap-3">
					<h3 id={`activity-${metric.kind}-title`} class="text-base leading-[normal] font-bold">
						{metric.title}
					</h3>
					<p class="text-right text-sm leading-[normal] font-bold text-primary">
						{metric.hasRecords ? metric.detail : '—'}
					</p>
				</div>
				<p class="mt-2 text-metric">
					{metric.value}
				</p>
				<p class="mt-2.5 text-sm leading-[normal] text-text-secondary">
					{metric.hasRecords ? metric.description : '선택한 기간에 기록이 없어요.'}
				</p>
				<div class="mt-6">
					<ActivityTrendChart
						connectMissing
						label={metric.title}
						unit={metric.kind === 'time' ? '분' : metric.kind === 'calories' ? 'kcal' : 'bpm'}
						formatValue={metric.kind === 'time'
							? formatSleepMinutes
							: metric.kind === 'calories'
								? formatCalories
								: formatExerciseHeartRate}
						formatTooltipValue={metric.kind === 'heart'
							? formatHeartRate
							: metric.kind === 'time'
								? formatSleepMinutes
								: formatCalories}
						days={period.days}
						values={metric.trend}
						range={[0, Math.max(1, ...metric.trend.filter((value) => value !== null))]}
						caption={`${metric.title}. ${period.label}. ${metric.hasRecords ? `${metric.value}. ${metric.description}. ${metric.detail}.` : ''}`}
					/>
				</div>
			</section>
			{#if metric.kind === 'time'}
				<section
					aria-labelledby="exercise-types-title"
					class="exercise-card exercise-types-card min-w-0 self-stretch rounded-3xl border border-border bg-card px-4 py-6 sm:px-7"
				>
					<div class="flex items-start justify-between gap-3">
						<h3 id="exercise-types-title" class="text-card-title">운동 종류</h3>
						<p class="text-right text-sm leading-[normal] font-bold text-primary">
							{summary.hasRecords && !incompleteTypes ? `총 ${summary.types.length}종` : '—'}
						</p>
					</div>
					{#if namedTypes.length}
						<ul aria-label="기록된 운동 종류" class="mt-6 grid gap-x-5 gap-y-2 sm:grid-cols-2">
							{#each namedTypes as exercise (exercise.label)}
								<li
									class="grid min-h-8 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 rounded-lg bg-primary-soft px-3.5 py-1.5"
								>
									<span class="text-sm leading-[normal] font-bold">{exercise.label}</span><span
										class="text-[13px] leading-[normal] font-medium text-text-secondary"
										>{exercise.count}</span
									><span
										class="text-right text-[13px] leading-[normal] font-medium text-text-secondary"
										>{exercise.duration}</span
									>
								</li>
							{/each}
						</ul>
						{#if incompleteTypes}<p class="mt-3 text-sm text-text-secondary">
								운동 종류 정보가 없어요.
							</p>{/if}
					{:else}<p class="mt-6 text-sm text-text-secondary">
							{emptyTypesMessage}
						</p>{/if}
					{#key period.label}
						<ExcludedWalkingDisclosure sessions={summary.excludedWalks} />
					{/key}
				</section>
			{/if}
		{/each}
	</div>
</section>

<style>
	.exercise-card {
		min-height: 230px;
	}
	.exercise-types-card {
		min-height: 0;
	}
</style>
