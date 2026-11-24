<script lang="ts">
	import DailyStepsChart from './DailyStepsChart.svelte';
	import ActivityAverageChart from './ActivityAverageChart.svelte';
	import {
		activityReferenceDate,
		formatAverageSteps as stepsLabel,
		getHourlyStepAverages,
		getHourlyStepSummary,
		getWeekdayAverages,
		stepSummary,
		weekdayOrder,
		type ActivityPeriod,
		type HourlyStepsDay,
		type StepRecord
	} from '../activity';

	let {
		period,
		recentDays = [],
		hourlyDays = [],
		summary = stepSummary,
		referenceDate = activityReferenceDate,
		average,
		weekdayError = false,
		hourlyError = false
	}: {
		period: ActivityPeriod;
		recentDays?: readonly StepRecord[];
		hourlyDays?: readonly HourlyStepsDay[];
		summary?: typeof stepSummary;
		referenceDate?: string;
		average?: number | null;
		weekdayError?: boolean;
		hourlyError?: boolean;
	} = $props();
	const weekdayValues = $derived(getWeekdayAverages(recentDays, referenceDate));
	const hasWeekdayRecords = $derived(weekdayValues.some((value) => value !== null));
	const weekdaySummary = $derived.by(() => {
		const values = weekdayValues.filter((value) => value !== null);
		if (values.length === 0) return null;
		const positive = values.filter((value) => value > 0);
		return [
			{ label: '최소', value: positive.length ? Math.min(...positive) : null },
			{ label: '최대', value: Math.max(...values) }
		].map((item) => ({
			...item,
			days: weekdayOrder.filter((_, index) => weekdayValues[index] === item.value)
		}));
	});
	const hourlyValues = $derived(
		getHourlyStepAverages(
			hourlyDays.filter((day) => period.days.some((selected) => selected.date === day.date))
		)
	);
	const hourlySummary = $derived(getHourlyStepSummary(hourlyValues));
	// 배지의 최소만 양수 막대를 기준으로 한다. 원본 평균·요약 계산은 유지한다.
	const hourlyMinimum = $derived.by(() => {
		const positive = hourlyValues?.filter((value) => value > 0) ?? [];
		if (!positive.length || !hourlyValues) return null;
		const value = Math.min(...positive);
		return { hour: hourlyValues.indexOf(value), value };
	});
	const hourLabel = (hour: number) => `${String(hour).padStart(2, '0')}시`;
</script>

<section aria-label="걸음 수 기록" class="activity-steps min-w-0">
	<section
		aria-labelledby="daily-steps-title"
		class="daily-steps-card min-w-0 rounded-3xl border border-border bg-card px-4 py-6 sm:px-7"
	>
		<div class="flex min-h-14 flex-col justify-between gap-4 sm:flex-row sm:items-center">
			<div>
				<h2 id="daily-steps-title" class="text-section-title">일별 걸음 수</h2>
				<p
					class={period.hasRecords
						? 'sr-only'
						: 'mt-2 text-[13px] leading-[normal] text-text-secondary'}
				>
					{period.hasRecords
						? `${period.label} · 평균에 반영된 완료 기록 ${summary.recordedDays}일`
						: '기록 없음'}
				</p>
			</div>
			<div
				class="flex min-h-14 items-center justify-between gap-8 rounded-[20px] bg-primary-soft px-6 py-2 sm:w-[326px] sm:shrink-0"
			>
				<div>
					<p class="text-xs leading-[normal] text-foreground">선택 기간 일평균</p>
					<p class="text-lg leading-[normal] font-bold">
						{period.hasRecords ? summary.average : '기록 없음'}
					</p>
				</div>
				<p class="text-sm font-bold text-text-primary">
					{period.hasRecords ? summary.change : '—'}
				</p>
			</div>
		</div>
		<div class="mt-6"><DailyStepsChart days={period.days} {average} /></div>
	</section>

	<div class="mt-3 grid min-w-0 gap-4 lg:grid-cols-2 lg:gap-8">
		<section
			aria-labelledby="weekday-average-title"
			class="step-summary-card flex min-w-0 flex-col rounded-3xl border border-border bg-card px-4 py-6 sm:px-7"
		>
			<h3 id="weekday-average-title" class="text-card-title-lg">요일별 평균 걸음 수</h3>
			<p class="sr-only">최근 4주 · 같은 요일 기록 평균 (기록이 있는 날 기준)</p>
			{#if hasWeekdayRecords}
				{#if weekdaySummary}
					<dl
						aria-label="요일별 걸음 수 요약"
						class="mt-3 flex flex-wrap gap-2 text-xs leading-5 text-foreground"
					>
						{#each weekdaySummary as item (item.label)}
							<div class="flex items-baseline gap-1.5 rounded-lg bg-surface px-2.5 py-1.5">
								<dt class="shrink-0">{item.label}</dt>
								<dd
									class="min-w-0 font-semibold text-text-primary"
									aria-label={item.value === null ? '0보다 큰 기록 없음' : undefined}
								>
									{#if item.value === null}—{:else}{item.days.join('·')}요일 ·
										<span class="whitespace-nowrap">{stepsLabel(item.value)}보</span>{/if}
								</dd>
							</div>
						{/each}
					</dl>
				{/if}
				<ActivityAverageChart
					values={weekdayValues}
					labels={weekdayOrder.map((day) => `${day}요일`)}
					caption="요일별 평균 걸음 수 차트"
					period={`최근 4주 · 기준일 ${referenceDate}`}
				/>
			{:else}
				<p
					class="grid flex-1 place-items-center py-10 text-center text-xs leading-5 text-text-secondary"
				>
					{weekdayError
						? '요일별 평균 데이터를 불러오지 못했어요.'
						: '최근 4주에 걸음 수 기록이 없어요.'}
				</p>
			{/if}
		</section>
		<section
			aria-labelledby="hourly-average-title"
			class="step-summary-card flex min-w-0 flex-col rounded-3xl border border-border bg-card px-4 py-6 sm:px-7"
		>
			<h3 id="hourly-average-title" class="text-card-title-lg">시간대별 평균 걸음 수</h3>
			{#if hourlySummary}
				<dl
					aria-label="시간대별 걸음 수 요약"
					class="mt-3 flex flex-wrap gap-2 text-xs leading-5 text-foreground"
				>
					<div class="flex items-baseline gap-1.5 rounded-lg bg-surface px-2.5 py-1.5">
						<dt class="shrink-0">최소</dt>
						<dd
							class="font-semibold whitespace-nowrap text-text-primary"
							aria-label={hourlyMinimum === null ? '0보다 큰 기록 없음' : undefined}
						>
							{#if hourlyMinimum}{hourLabel(hourlyMinimum.hour)} · {stepsLabel(
									hourlyMinimum.value
								)}보{:else}—{/if}
						</dd>
					</div>
					<div class="flex items-baseline gap-1.5 rounded-lg bg-surface px-2.5 py-1.5">
						<dt class="shrink-0">최대</dt>
						<dd class="font-semibold whitespace-nowrap text-text-primary">
							{hourLabel(hourlySummary.max.hour)} · {stepsLabel(hourlySummary.max.value)}보
						</dd>
					</div>
				</dl>
			{/if}
			{#if hourlyValues !== null && hourlySummary}
				<ActivityAverageChart
					values={hourlyValues}
					labels={hourlyValues.map((_, hour) => hourLabel(hour))}
					caption="시간대별 평균 걸음 수 분포"
					period={period.label}
					hourly
				/>
			{:else}
				<p
					class="grid flex-1 place-items-center py-10 text-center text-xs leading-5 text-text-secondary"
				>
					{hourlyError ? '시간대별 데이터를 불러오지 못했어요.' : '선택한 기간의 기록이 없어요.'}
				</p>
			{/if}
		</section>
	</div>
</section>
