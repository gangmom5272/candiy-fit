<script lang="ts">
	import { resolve } from '$app/paths';
	import type { ActivityDay } from '../activity';
	import ActivityDataTooltip from './ActivityDataTooltip.svelte';

	let {
		days,
		values,
		caption,
		area = false,
		connectMissing = false,
		range = [0, 1],
		label = caption,
		unit = '',
		formatValue,
		formatTooltipValue = formatValue
	}: {
		days: readonly ActivityDay[];
		values: readonly (number | null)[];
		caption: string;
		area?: boolean;
		connectMissing?: boolean;
		range?: readonly [number, number];
		label?: string;
		unit?: string;
		formatValue?: (value: number) => string;
		formatTooltipValue?: (value: number) => string;
	} = $props();

	const chartWidth = 580;
	let figure = $state<HTMLElement>();
	const hasValues = $derived(days.some((_, index) => values[index] != null));
	const domain = $derived({
		min: Math.min(range[0], ...values.filter((value) => value !== null)),
		max: Math.max(range[1], ...values.filter((value) => value !== null))
	});
	const xPosition = (index: number) => 36 + (index * 468) / Math.max(1, days.length - 1);
	const points = $derived(
		days.map((_, index) =>
			values[index] == null && (!connectMissing || !hasValues)
				? null
				: {
						x: xPosition(index),
						// null은 원본에 남기고 그리는 좌표만 기준선에 연결한다.
						y:
							58 - (((values[index] ?? 0) - domain.min) / Math.max(1, domain.max - domain.min)) * 40
					}
		)
	);
	const groups = $derived.by(() => {
		const result: { x: number; y: number }[][] = [];
		let current: { x: number; y: number }[] = [];
		for (const point of points) {
			if (point === null) {
				if (current.length) result.push(current);
				current = [];
			} else current.push(point);
		}
		if (current.length) result.push(current);
		return result;
	});
	const coordinates = (group: { x: number; y: number }[]) =>
		group.map((point) => `${point.x},${point.y}`).join(' ');
</script>

<figure bind:this={figure} class="activity-trend min-w-0">
	<div class="relative rounded-xl bg-surface">
		<svg
			viewBox={`0 0 ${chartWidth} 70`}
			preserveAspectRatio="none"
			class="block h-[70px] w-full"
			aria-hidden="true"
		>
			{#each [18, 35, 52] as y (y)}<line
					x1="18"
					x2="562"
					y1={y}
					y2={y}
					stroke="var(--color-border)"
					vector-effect="non-scaling-stroke"
				/>{/each}
			{#each groups as group (group[0].x)}
				{#if area && group.length > 1}<polygon
						points={`${group[0].x},58 ${coordinates(group)} ${group[group.length - 1].x},58`}
						fill="var(--color-brand)"
						fill-opacity="0.13"
					/>{/if}
				<polyline
					points={coordinates(group)}
					fill="none"
					stroke="var(--color-brand)"
					stroke-width="2"
					stroke-linejoin="round"
					stroke-linecap="round"
					vector-effect="non-scaling-stroke"
				/>
			{/each}
			{#each points as point, index (index)}
				{#if point === null && !connectMissing}<line
						x1={xPosition(index) - 8}
						x2={xPosition(index) + 8}
						y1="58"
						y2="58"
						stroke="var(--color-border)"
						stroke-width="2"
						vector-effect="non-scaling-stroke"
					/>{/if}
			{/each}
		</svg>
		{#each points as point, index (index)}
			{#if point !== null}
				{#if values[index] != null}
					<span
						data-trend-point
						data-date={days[index].date}
						aria-hidden="true"
						style:left={`${(point.x / chartWidth) * 100}%`}
						style:top={`${(point.y / 70) * 100}%`}
						class="pointer-events-none absolute size-[7px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand"
					></span>
				{/if}
				<button
					type="button"
					data-chart-point={index}
					aria-label={`${days[index].date} ${label} ${values[index] == null ? '—' : formatValue ? formatValue(values[index]) : `${values[index]} ${unit}`}`}
					style:left={`${(point.x / chartWidth) * 100}%`}
					style:top={`${(point.y / 70) * 100}%`}
					class="absolute h-8 w-8 -translate-x-1/2 -translate-y-1/2 cursor-pointer rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-info"
				></button>
			{/if}
		{/each}
		{#if !hasValues}<p
				class="pointer-events-none absolute inset-0 grid place-items-center text-xs text-text-secondary"
			>
				선택한 기간의 기록이 없어요.
			</p>{/if}
	</div>
	<div class="relative mt-2 h-6 text-xs leading-4 text-text-secondary">
		{#each days as day, index (day.date)}<a
				data-chart-navigation={index}
				aria-label={`${day.date} 하루 상세 보기`}
				href={resolve('/(app)/health/day/[date]', { date: day.date })}
				style:left={`${(xPosition(index) / chartWidth) * 100}%`}
				class="absolute top-0 min-h-6 min-w-6 -translate-x-1/2 rounded-sm text-center no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
				><span class="sm:hidden">{day.date.slice(8)}</span><span class="hidden sm:inline"
					>{day.label}</span
				></a
			>{/each}
	</div>
	<ActivityDataTooltip
		root={figure}
		dates={days.map((day) => day.date)}
		{values}
		{label}
		{unit}
		formatValue={formatTooltipValue}
		allowMissing={connectMissing}
		navigationMessage="클릭하여 하루 상세 보기"
	/>
	<figcaption class="sr-only">
		{caption}{!hasValues
			? ' 선택한 기간에 기록이 없습니다.'
			: ' 날짜를 선택하면 하루 상세 기록으로 이동합니다.'}
	</figcaption>
</figure>

<style>
	figure :global([role='tooltip']) {
		pointer-events: none;
	}
</style>
