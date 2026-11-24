<script lang="ts">
	import { resolve } from '$app/paths';
	import {
		dayMissingValue,
		formatDayMetric,
		type HealthMetricDay
	} from '#lib/features/health/day-detail.js';
	import { formatSleepMinutes } from '#lib/features/health/sleep.js';
	import {
		healthSummaryCards,
		type HealthSummaryMetric
	} from '#lib/features/health/health-summary-api.js';

	interface Props {
		metric: HealthSummaryMetric;
		day?: HealthMetricDay;
		icon: string;
		value?: string;
		isStatus?: boolean;
		notice?: string;
		loading?: boolean;
		preparing?: boolean;
	}

	let {
		metric,
		day,
		icon,
		value,
		isStatus,
		notice,
		loading = false,
		preparing = false
	}: Props = $props();
	const card = $derived(healthSummaryCards[metric]);
	const showStatus = $derived(isStatus ?? (!day || day.missing || day.value === null));
	const displayValue = $derived(
		value ??
			(preparing
				? '준비 중'
				: day
					? day.missing || day.value === null
						? dayMissingValue(day.reason)
						: metric === 'sleeps'
							? formatSleepMinutes(day.value)
							: formatDayMetric(day, metric === 'steps' ? 'steps' : 'min')
					: notice
						? '조회 실패'
						: '불러오는 중')
	);
	const valueParts = $derived(displayValue.split(/(시간|분|보|bpm)/u));
</script>

<a
	aria-busy={loading}
	href={resolve(`/(app)/health/${card.path}${card.hash ?? ''}`)}
	class="metric-card health-link-motion flex min-h-32 min-w-0 flex-col justify-center gap-3 rounded-2xl border border-border bg-card px-4 py-4 text-text-primary no-underline visited:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring sm:min-h-36 sm:px-6 sm:py-4"
>
	<div class="flex min-w-0 flex-col items-center gap-4">
		<div class="flex w-full items-center gap-2">
			<span
				class="flex size-8 items-center justify-center rounded-full bg-primary-soft"
				aria-hidden="true"
			>
				<img src={icon} alt="" width="24" height="24" class="size-6" />
			</span>
			<h2 class="metric-label font-bold break-keep text-text-secondary">
				{metric === 'heart_rates' ? '안정 시 심박수' : card.label}
			</h2>
		</div>
		<p
			class="metric-value text-center font-bold whitespace-nowrap"
			class:metric-value--status={showStatus}
		>
			{#if loading}<span role="status"
					><span class="sr-only">{displayValue}</span><span
						aria-hidden="true"
						class="health-skeleton inline-block h-[1lh] w-24 max-w-full align-middle"
					></span></span
				>{:else if showStatus}{displayValue}{:else}{#each valueParts as part, index (index)}<span
						class:metric-unit={index % 2 === 1}>{part}</span
					>{/each}{/if}
		</p>
	</div>
	{#if notice}
		<p role="alert" class="text-xs leading-relaxed break-keep text-text-secondary">{notice}</p>
	{/if}
</a>

<style>
	.metric-card {
		container-type: inline-size;
	}
	.metric-label {
		font-size: clamp(0.875rem, 8cqw, 1.25rem);
		line-height: 1.25;
	}
	.metric-value {
		font-size: clamp(1.5rem, 15cqw, 2.5rem);
		line-height: 1.2;
	}
	.metric-unit {
		font-size: 0.65em;
		font-weight: 600;
	}
	.metric-value--status {
		font-size: clamp(1.375rem, 12.5cqw, 2rem);
		font-weight: 600;
		/* 글자를 키워도 기존 상태 행 높이는 유지한다. */
		line-height: clamp(1.8rem, 16cqw, 2.4rem);
		color: var(--color-text-primary);
	}
</style>
