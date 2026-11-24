<script lang="ts">
	import { resolve } from '$app/paths';
	import type { ActivityDay } from '../activity';
	import { dayMissingValue } from '../day-detail';
	import { formatHealthSummaryTick } from '../health-summary-axis';
	import { barMaximum, barOpacity } from '../health-bar-color';
	import ActivityDataTooltip from './ActivityDataTooltip.svelte';

	let { days, average: serverAverage }: { days: readonly ActivityDay[]; average?: number | null } =
		$props();
	const plotWidth = 1166;
	let figure = $state<HTMLElement>();
	let chartSize = $state<DOMRectReadOnly>();
	const chartWidth = $derived(chartSize?.width ?? 0);
	let valueWidths = $state<number[]>([]);
	let dateSizes = $state<DOMRectReadOnly[]>([]);
	const tooltipValues = $derived(days.map((day) => (day.missing ? null : day.steps)));
	const plotHeight = 156;
	const measured = $derived(
		days.flatMap((day) => (day.missing || day.steps === null ? [] : [day.steps]))
	);
	const colorMaximum = $derived(barMaximum(measured));
	// 기본 0~12,000보 범위를 유지하고, 초과한 값은 정수 간격의 4개 눈금으로 확장한다.
	const maximum = $derived.by(() => {
		const requiredMax = Math.max(
			0,
			...measured.filter(Number.isFinite),
			serverAverage !== null && serverAverage !== undefined && Number.isFinite(serverAverage)
				? serverAverage
				: 0
		);
		if (requiredMax <= 12_000) return 12_000;
		const requiredInterval = Math.max(1, requiredMax / 3);
		const magnitude = 10 ** Math.floor(Math.log10(requiredInterval));
		const multiple =
			[1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((value) => value * magnitude >= requiredInterval) ??
			10;
		const roundedMax = Math.ceil(multiple * magnitude) * 3;
		// 부동소수점 표현 범위를 넘는 올림만 포기하고 원본 최댓값은 포함한다.
		return Number.isFinite(roundedMax) ? Math.max(requiredMax, roundedMax) : requiredMax;
	});
	const tickInterval = $derived(maximum / 3);
	const average = $derived.by(() => {
		if (serverAverage !== undefined) return serverAverage;
		if (!measured.length) return null;
		const total = measured.reduce((sum, value) => sum + value, 0);
		return Number.isFinite(total)
			? total / measured.length
			: (measured.reduce((sum, value) => sum + value / maximum, 0) / measured.length) * maximum;
	});
	// 원본 값은 유지하고, 표시용 좌표만 0부터 상한까지의 영역으로 제한한다.
	const height = (value: number) =>
		Number.isFinite(value) ? Math.max(0, Math.min(1, value / maximum)) * plotHeight : 0;
	const hasRecords = $derived(measured.length > 0);
	const valueHeight = 14;
	const valueGap = 4;
	// 숫자는 원문을 유지한다. 양끝을 plot 안으로 맞추고 실제 폭이 겹치는 숫자만 위로 배치한다.
	const valuePositions = $derived.by(() => {
		const placed: { left: number; top: number; width: number }[] = [];
		return days.map((day, index) => {
			if (day.missing || day.steps === null) return null;
			const width = valueWidths[index] ?? 0;
			const left = Math.max(
				0,
				Math.min(((index + 0.5) * chartWidth) / 7 - width / 2, chartWidth - width)
			);
			let top = 6 + plotHeight - height(day.steps) - 8 - valueHeight;
			for (const previous of [...placed].sort((a, b) => b.top - a.top)) {
				if (
					left < previous.left + previous.width + valueGap &&
					left + width + valueGap > previous.left &&
					top < previous.top + valueHeight + valueGap &&
					top + valueHeight + valueGap > previous.top
				)
					top = previous.top - valueHeight - valueGap;
			}
			const position = { left, top, width };
			placed.push(position);
			return position;
		});
	});
	const valueHeadroom = $derived(
		Math.max(0, ...valuePositions.map((position) => (position ? -position.top : 0)))
	);
	const dateHeight = $derived(
		Math.max(16, ...dateSizes.flatMap((size) => (size ? [size.height] : [])))
	);
	// Y축 폭 때문에 날짜 칸이 좁아지면, 실제 링크가 겹치지 않는 첫 행에 배치한다.
	const datePositions = $derived.by(() => {
		const rowEnds: number[] = [];
		return days.map((_, index) => {
			const width = dateSizes[index]?.width ?? 24;
			const inset = Math.min(1, Math.max(0, (chartWidth - width) / 2));
			const left = Math.max(
				inset,
				Math.min(((index + 0.5) * chartWidth) / 7 - width / 2, chartWidth - width - inset)
			);
			let row = rowEnds.findIndex((right) => left >= right + 1);
			if (row < 0) row = rowEnds.length;
			rowEnds[row] = left + width;
			return { left: left - (index * chartWidth) / 7, row };
		});
	});
</script>

<figure
	bind:this={figure}
	aria-label="일별 걸음 수 차트"
	class="daily-steps-chart grid min-w-0 grid-cols-[max-content_minmax(0,1fr)] gap-x-2"
>
	<div
		class="flex h-[169px] flex-col justify-between text-right text-xs leading-[13px] text-text-secondary"
		style:margin-top={`${valueHeadroom}px`}
		aria-hidden="true"
	>
		{#each [maximum, tickInterval * 2, tickInterval, 0] as tick (tick)}<span
				>{formatHealthSummaryTick(tick, '보')}</span
			>{/each}
	</div>
	<div bind:contentRect={chartSize} class="relative min-w-0">
		<svg
			viewBox={`0 0 ${plotWidth} ${plotHeight}`}
			preserveAspectRatio="none"
			class="pointer-events-none absolute inset-x-0 h-[156px] w-full overflow-visible"
			style:top={`${valueHeadroom + 6}px`}
			aria-hidden="true"
		>
			{#each [0, 52, 104, 156] as y (y)}
				<line
					x1="0"
					x2={plotWidth}
					y1={y}
					y2={y}
					stroke="var(--color-border)"
					vector-effect="non-scaling-stroke"
				/>
			{/each}
			{#if average !== null && Number.isFinite(average) && average >= 0}
				<line
					x1="0"
					x2={plotWidth}
					y1={plotHeight - height(average)}
					y2={plotHeight - height(average)}
					stroke="var(--color-coral-sub)"
					stroke-width="1.5"
					stroke-dasharray="4 4"
					vector-effect="non-scaling-stroke"
				/>
			{/if}
			{#each days as day, index (day.date)}
				{#if !day.missing && day.steps !== null && Number.isFinite(day.steps) && day.steps >= 0}
					<a
						data-chart-point={index}
						href={resolve('/(app)/health/day/[date]', { date: day.date })}
						tabindex="-1"
						aria-label={`${day.label} ${day.weekday} ${day.steps.toLocaleString('ko-KR')}보`}
						class="pointer-events-auto cursor-pointer"
					>
						<rect
							x={((index + 0.5) * plotWidth) / 7 - 29}
							y={plotHeight - height(day.steps)}
							width="58"
							height={height(day.steps)}
							rx="8"
							fill="var(--color-brand)"
							opacity={barOpacity(day.steps, colorMaximum)}
						/>
					</a>
				{/if}
			{/each}
		</svg>
		{#if !hasRecords}
			<p
				class="pointer-events-none absolute inset-x-0 top-16 text-center text-xs text-text-secondary sm:text-sm"
			>
				선택한 기간의 걸음 수 기록이 없어요.
			</p>
		{/if}
		<div
			class="pointer-events-none relative"
			style:height={`${valueHeadroom + 169}px`}
			aria-hidden="true"
		>
			{#each days as day, index (day.date)}
				{@const position = valuePositions[index]}
				{#if position && day.steps !== null}
					<span
						data-step-value
						bind:offsetWidth={valueWidths[index]}
						style:left={`${position.left}px`}
						style:top={`${position.top + valueHeadroom}px`}
						class="absolute w-max text-xs leading-3.5 font-bold whitespace-nowrap text-text-primary"
						>{day.steps.toLocaleString('ko-KR')}</span
					>
				{/if}
			{/each}
		</div>
		<div class="pointer-events-none grid grid-cols-7">
			{#each days as day, index (day.date)}
				{@const position = datePositions[index]}
				<div
					class="relative min-w-0 text-center"
					style:padding-top={`${position.row * (dateHeight + 4)}px`}
				>
					<a
						bind:contentRect={dateSizes[index]}
						style:left={`${position.left}px`}
						data-chart-point={!day.missing && day.steps !== null ? index : undefined}
						href={resolve('/(app)/health/day/[date]', { date: day.date })}
						aria-label={`${day.missing || day.steps === null ? dayMissingValue(day.reason ?? null) : `${day.steps.toLocaleString('ko-KR')}보`} · ${day.label} ${day.weekday} · ${day.date} 하루 상세 보기`}
						class="pointer-events-auto relative mt-1.5 block w-fit min-w-6 cursor-pointer rounded-sm text-xs leading-4 text-text-primary no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
					>
						<span aria-hidden="true"
							><span class="sm:hidden">{day.date.slice(8)}</span><span class="hidden sm:inline"
								>{day.label}</span
							><span class="block sm:ml-1 sm:inline">{day.weekday}</span></span
						>
					</a>
				</div>
			{/each}
		</div>
	</div>
	<ActivityDataTooltip
		root={figure}
		dates={days.map((day) => day.date)}
		values={tooltipValues}
		label="걸음 수"
		unit="보"
		message="클릭하여 하루 상세 보기"
	/>
	<figcaption class="sr-only">
		기록이 있는 날의 평균을 보여줘요. 날짜를 누르면 하루 기록을 볼 수 있어요.
	</figcaption>
</figure>

<style>
	figure :global([role='tooltip']:not([data-scrollable='true'])) {
		pointer-events: none;
	}
</style>
