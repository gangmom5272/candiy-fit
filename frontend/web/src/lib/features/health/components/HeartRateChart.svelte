<script lang="ts">
	import { resolve } from '$app/paths';
	import ActivityDataTooltip from './ActivityDataTooltip.svelte';
	import { placeHeartLabels } from '../heart-labels';
	import {
		formatHeartPeriod,
		groupHeartMissingDays,
		heartChartDomain,
		heartMissingMessage,
		heartX,
		heartY,
		type HeartMetricResult
	} from '../heart';

	let { result }: { result: HeartMetricResult } = $props();
	const domain = $derived(heartChartDomain(result));
	const average = $derived(result.summary.avg);
	const hasRecords = $derived(
		result.days.some((day) => {
			if (day.missing) return false;
			const range = result.detail.dailyRanges.find((range) => range.date === day.date);
			return day.value !== null || range?.min != null || range?.max != null;
		})
	);
	let figure = $state<HTMLElement>();
	let plotBounds = $state<DOMRectReadOnly>();
	const plotWidth = $derived(plotBounds?.width ?? 0);
	const plotHeight = $derived(plotBounds?.height ?? 0);
	let labelSizes = $state<DOMRectReadOnly[]>([]);
	const labelPositions = $derived.by(() => {
		const marks = result.days.flatMap((day) => {
			if (day.missing) return [];
			const x = (heartX(day.date, result.period.start) / 100) * plotWidth;
			const range = result.detail.dailyRanges.find((range) => range.date === day.date);
			return [
				...(day.value === null
					? []
					: [
							{
								left: x - 7,
								top: (heartY(day.value, domain) / 100) * plotHeight - 7,
								width: 14,
								height: 14
							}
						]),
				...(range?.min == null || range.max == null
					? []
					: [
							{
								left: x - 9,
								top: (heartY(range.max, domain) / 100) * plotHeight - 1,
								width: 18,
								height:
									((heartY(range.min, domain) - heartY(range.max, domain)) / 100) * plotHeight + 2
							}
						])
			];
		});
		return placeHeartLabels(
			result.days.flatMap((day, index) =>
				day.missing || day.value === null
					? []
					: [
							{
								index,
								x: (heartX(day.date, result.period.start) / 100) * plotWidth,
								y: (heartY(day.value, domain) / 100) * plotHeight,
								width: labelSizes[index]?.width ?? 0,
								height: labelSizes[index]?.height ?? 0
							}
						]
			),
			marks,
			plotWidth,
			plotHeight
		);
	});
	const values = $derived(result.days.map((day) => day.value));
	const tooltipRows = $derived(
		result.days.map((day) => {
			const range = result.detail.dailyRanges.find((range) => range.date === day.date);
			return [
				{ label: '평균 심박수', value: day.value, unit: 'bpm' },
				{ label: '최저 심박수', value: range?.min ?? null, unit: 'bpm' },
				{ label: '최고 심박수', value: range?.max ?? null, unit: 'bpm' }
			];
		})
	);
	const missingGroups = $derived(groupHeartMissingDays(result.days));
</script>

<section
	aria-labelledby="heart-chart-title"
	class="heart-card min-w-0 rounded-2xl border border-border bg-card px-4 py-6 sm:px-7"
>
	<div class="flex flex-wrap items-start justify-between gap-5">
		<div>
			<h2 id="heart-chart-title" class="text-section-title">일별 심박수</h2>
		</div>
		<div>
			<p class="text-sm font-bold text-text-secondary">평균 심박수</p>
			<p class="mt-1 text-metric" data-period-average={average}>
				{average === null ? '집계된 평균 없음' : `${average} bpm`}
			</p>
		</div>
	</div>
	<ul
		aria-label="심박 차트 범례"
		class="heart-legend mt-6 flex flex-wrap justify-end gap-x-6 gap-y-3 text-[13px] text-text-secondary"
	>
		<li class="flex items-center gap-2">
			<span aria-hidden="true" class="w-7 border-t-2 border-text-secondary"></span>최저–최고 범위
		</li>
		<li class="flex items-center gap-2">
			<span aria-hidden="true" class="size-3 rounded-full bg-brand"></span>하루 평균
		</li>
	</ul>
	{#if !hasRecords}
		<p class="heart-empty mt-8 text-center text-text-secondary" role="status">
			선택한 기간에 심박 기록이 없어요.
		</p>
	{/if}
	<figure
		bind:this={figure}
		class="heart-chart mt-6 mb-8 grid grid-cols-[28px_minmax(0,1fr)] gap-x-2 sm:grid-cols-[36px_minmax(0,1fr)] sm:gap-x-3"
		class:heart-chart-empty={!hasRecords}
	>
		<div aria-hidden="true" class="heart-plot relative">
			{#each hasRecords ? domain.ticks : [] as tick (tick)}
				<span
					class="heart-axis-label absolute right-0 -translate-y-1/2 text-xs text-text-secondary"
					style:top={`${heartY(tick, domain)}%`}>{tick}</span
				>
			{/each}
		</div>
		<div class="min-w-0">
			<div class="heart-plot relative" bind:contentRect={plotBounds}>
				{#each hasRecords ? domain.ticks : [] as tick (tick)}
					<div
						class="absolute w-full border-t border-border"
						aria-hidden="true"
						style:top={`${heartY(tick, domain)}%`}
					></div>
				{/each}
				{#each result.days as day, index (day.date)}
					{@const range = result.detail.dailyRanges.find((range) => range.date === day.date)}
					{@const chartValues = [day.value, range?.min, range?.max].filter(
						(value) => value != null
					)}
					{#if hasRecords && !day.missing && chartValues.length}
						<button
							type="button"
							data-chart-point={index}
							aria-label={`${day.date} 심박수 확인`}
							class="absolute w-8 -translate-x-1/2 cursor-pointer rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-info"
							style:left={`${heartX(day.date, result.period.start)}%`}
							style:top={`calc(${heartY(Math.max(...chartValues), domain)}% - 16px)`}
							style:height={`calc(${heartY(Math.min(...chartValues), domain) - heartY(Math.max(...chartValues), domain)}% + 32px)`}
						></button>
					{/if}
					{#if !day.missing}
						{#if range?.min != null && range?.max != null}
							<div
								data-heart-range={day.date}
								aria-hidden="true"
								class="heart-range pointer-events-none absolute w-0 -translate-x-1/2 border-l-[3px] border-text-secondary/65"
								style:left={`${heartX(day.date, result.period.start)}%`}
								style:top={`${heartY(range.max, domain)}%`}
								style:height={`${heartY(range.min, domain) - heartY(range.max, domain)}%`}
							></div>
						{/if}
						{#if day.value !== null}
							{@const labelPosition = labelPositions.find((label) => label.index === index)}
							<div
								data-heart-point={day.date}
								data-chart-focus-target={index}
								aria-hidden="true"
								class="pointer-events-none absolute size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-brand"
								style:left={`${heartX(day.date, result.period.start)}%`}
								style:top={`${heartY(day.value, domain)}%`}
							>
								<span
									data-heart-label={day.date}
									bind:contentRect={labelSizes[index]}
									class="invisible absolute w-max origin-top-left bg-card text-xs leading-4 font-bold whitespace-nowrap text-text-secondary sm:visible"
									class:scale-0={labelPosition?.hidden ?? true}
									style:visibility={labelPosition?.hidden ? 'hidden' : undefined}
									style:left={`${(labelPosition?.left ?? 0) - (heartX(day.date, result.period.start) / 100) * plotWidth + 5}px`}
									style:top={`${(labelPosition?.top ?? 0) - (heartY(day.value, domain) / 100) * plotHeight + 5}px`}
									>{day.value}</span
								>
							</div>
						{/if}
					{/if}
				{/each}
			</div>
			<div class="heart-dates relative mt-6 h-11">
				{#each result.days as day, index (day.date)}
					{@const range = result.detail.dailyRanges.find((range) => range.date === day.date)}
					<a
						data-chart-navigation={index}
						href={resolve('/(app)/health/day/[date]', { date: day.date })}
						aria-label={`${day.date} 하루 상세`}
						aria-describedby={`heart-day-${day.date}`}
						class="absolute flex min-h-11 -translate-x-1/2 items-center rounded text-xs whitespace-nowrap text-text-secondary hover:text-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:text-sm"
						style:left={`${heartX(day.date, result.period.start)}%`}
						><span class="sm:hidden">{Number(day.date.slice(8))}일</span><span
							class="hidden sm:inline">{day.date.slice(5).replace('-', '/')}</span
						></a
					>
					<p id={`heart-day-${day.date}`} class="sr-only">
						{#if day.missing}{heartMissingMessage(day.reason)}{:else}
							{day.value !== null ? `평균 ${day.value} bpm.` : '평균 기록 없음.'}
							{range?.min != null ? `최저 ${range.min} bpm.` : '최저 기록 없음.'}
							{range?.max != null ? `최고 ${range.max} bpm.` : '최고 기록 없음.'}
						{/if}
					</p>
				{/each}
			</div>
		</div>
		<ActivityDataTooltip
			root={figure}
			dates={result.days.map((day) => day.date)}
			{values}
			rows={tooltipRows}
			label="심박수"
			unit="bpm"
			allowMissing
			focusOverflowOnTab
			navigationMessage="클릭하여 하루 상세 보기"
		/>
		<figcaption class="sr-only">
			날짜별 심박수. 최저·최고는 세로 범위, 하루 평균은 점으로 표시합니다. 단위 bpm.
		</figcaption>
	</figure>
	{#if missingGroups.length}
		<ul
			aria-label="심박 기록 안내"
			class="flex flex-wrap gap-x-6 gap-y-2 text-xs text-text-secondary"
		>
			{#each missingGroups as group (group.start)}
				<li>{formatHeartPeriod(group)} · {heartMissingMessage(group.reason)}</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	.heart-chart :global([role='tooltip'][data-scrollable='true']) {
		overscroll-behavior: contain;
	}
	.heart-plot {
		height: 192px;
	}
	@media (min-width: 640px) {
		.heart-plot {
			height: 240px;
		}
	}
	@media (min-width: 1024px) {
		.heart-legend {
			margin-top: 16px;
		}
		.heart-chart {
			margin-top: 24px;
			margin-bottom: 16px;
		}
		.heart-dates {
			margin-top: 16px;
		}
		/* 헤더·기간 선택·CTA 공간을 남기고 화면 높이에 따라 차트만 조절한다. */
		.heart-plot {
			height: clamp(120px, calc(100dvh - var(--app-header-height, 80px) - 600px), 300px);
		}
	}
	@media (min-width: 1024px) and (max-height: 900px) {
		.heart-legend {
			margin-top: 12px;
		}
		.heart-chart {
			margin-top: 20px;
			margin-bottom: 8px;
		}
		.heart-dates {
			margin-top: 8px;
		}
		.heart-empty {
			margin-top: 12px;
		}
		.heart-axis-label {
			font-size: 12px;
		}
	}
	/* 빈 기간은 빈 상태와 날짜 링크를 제공하며 불필요한 수치 축을 펼치지 않는다. */
	.heart-chart-empty .heart-plot {
		height: 0;
	}
	.heart-range::before,
	.heart-range::after {
		content: '';
		position: absolute;
		left: -9px;
		width: 15px;
		border-top: 2px solid var(--color-text-secondary);
	}
	.heart-range::before {
		top: 0;
	}
	.heart-range::after {
		bottom: 0;
	}
</style>
