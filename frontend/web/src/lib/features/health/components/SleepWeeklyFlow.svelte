<script lang="ts">
	import { resolve } from '$app/paths';
	import ActivityDataTooltip from './ActivityDataTooltip.svelte';
	import {
		formatSleepMinutes,
		formatSleepPeriod,
		sleepAverageDelta,
		sleepClock,
		sleepInterval,
		type SleepMetricResult
	} from '../sleep';
	import { sleepMissingText } from '../sleep-detail';
	let { result, previous }: { result: SleepMetricResult; previous: SleepMetricResult | null } =
		$props();
	const delta = $derived(sleepAverageDelta(result.summary.avg, previous?.summary.avg ?? null));
	const intervals = $derived(result.detail.nights.map(sleepInterval));
	const domainStart = $derived(Math.min(21 * 60, ...intervals.map((i) => i.start)));
	const domainEnd = $derived(Math.max(33 * 60, ...intervals.map((i) => i.end)));
	const position = (minute: number) => ((minute - domainStart) / (domainEnd - domainStart)) * 100;
	const ticks = $derived.by(() => {
		const first = Math.ceil(domainStart / 120) * 2;
		return Array.from(
			{ length: Math.floor(domainEnd / 120) - first / 2 + 1 },
			(_, i) => first + i * 2
		);
	});
	let axisWidth = $state(0);
	let labelWidths = $state<number[]>([]);
	let clockWidths = $state<number[]>([]);
	function clockLayout(start: number, end: number, index: number) {
		const barLeft = (position(start) / 100) * axisWidth;
		const barWidth = ((position(end) - position(start)) / 100) * axisWidth;
		const width = Math.min(
			axisWidth,
			Math.max(barWidth, (clockWidths[index * 2] ?? 0) + (clockWidths[index * 2 + 1] ?? 0) + 8)
		);
		return {
			width,
			left: Math.max(0, Math.min(axisWidth - width, barLeft + barWidth / 2 - width / 2))
		};
	}
	const axisLabels = $derived.by(() => {
		let previousRight = -8;
		return ticks.flatMap((hour, index) => {
			const labelWidth = labelWidths[index] ?? 0;
			const left = Math.max(
				0,
				Math.min(axisWidth - labelWidth, (position(hour * 60) / 100) * axisWidth - labelWidth / 2)
			);
			if (axisWidth === 0 || labelWidth === 0 || left < previousRight + 8) return [];
			previousRight = left + labelWidth;
			return [{ hour, left }];
		});
	});
	let section = $state<HTMLElement>();
	const tooltipValues = $derived(result.days.map((day) => day.value));
</script>

<section
	bind:this={section}
	aria-labelledby="sleep-flow-title"
	class="rounded-3xl border border-border bg-card px-4 py-6 sm:px-7"
>
	<div class="flex flex-wrap items-start justify-between gap-4">
		<div>
			<h2 id="sleep-flow-title" class="text-section-title">주간 수면 흐름</h2>
			<p class="sr-only">
				{formatSleepPeriod(result.period)} · 날짜별 취침부터 기상까지의 흐름
			</p>
		</div>
		<p class="flex flex-wrap items-center gap-3 self-center text-sm text-text-secondary">
			이전 7일 대비
			<span class="font-bold text-text-primary">
				{previous === null
					? '이전 기간 조회 실패'
					: delta === null
						? '비교할 기록 없음'
						: delta === 0
							? '변화 없음'
							: `${delta > 0 ? '+' : '-'}${Math.abs(delta)}분 ${delta > 0 ? '증가' : '감소'}`}
			</span>
		</p>
	</div>
	<div
		class="mt-7 grid grid-cols-[52px_minmax(0,1fr)_76px] gap-x-2 text-xs sm:grid-cols-[80px_minmax(0,1fr)_112px] sm:gap-x-5 sm:text-sm"
	>
		<span class="pb-3 font-medium">날짜</span>
		<span class="sr-only">취침부터 기상까지</span><span aria-hidden="true"></span>
		<span class="pb-3 text-right font-medium">수면 시간</span>
		{#each result.days as day, index (day.date)}
			{@const night = result.detail.nights.find((n) => n.date === day.date)}
			{@const interval = night ? sleepInterval(night) : null}
			<div class="flex min-h-12 items-center">
				<a
					data-chart-navigation={index}
					class="inline-flex w-fit items-center rounded py-1 text-text-secondary underline-offset-4 hover:text-brand hover:underline focus-visible:outline-2 focus-visible:outline-ring"
					href={resolve('/(app)/health/sleep/[date]', { date: day.date })}
					aria-label={`${day.date} 수면 상세`}
				>
					{day.date.slice(5).replace('-', '/')}
				</a>
			</div>
			<div class="relative min-w-0">
				{#each ticks as hour (hour)}
					<span
						aria-hidden="true"
						class="absolute inset-y-0 border-l border-dashed border-border"
						style:left={`${position(hour * 60)}%`}
					></span>
				{/each}
				{#if night && interval}
					{@const clocks = clockLayout(interval.start, interval.end, index)}
					<div
						class="absolute top-1 flex justify-between text-xs leading-normal text-text-secondary"
						aria-hidden="true"
						style:left={`${clocks.left}px`}
						style:width={`${clocks.width}px`}
					>
						<span class="shrink-0 whitespace-nowrap" bind:clientWidth={clockWidths[index * 2]}
							>{sleepClock(night.startAt)}</span
						><span class="shrink-0 whitespace-nowrap" bind:clientWidth={clockWidths[index * 2 + 1]}
							>{sleepClock(night.endAt)}</span
						>
					</div>
					<a
						data-chart-navigation={index}
						href={resolve('/(app)/health/sleep/[date]', { date: day.date })}
						aria-label={`${day.date} 수면 그래프 상세`}
						class="absolute top-6 h-5 rounded-md bg-brand hover:brightness-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
						style:left={`${position(interval.start)}%`}
						style:width={`${position(interval.end) - position(interval.start)}%`}
					>
					</a>
					<span class="sr-only"
						>취침 {sleepClock(night.startAt)}, 기상 {sleepClock(night.endAt)}</span
					>
				{/if}
			</div>
			<span
				class={[
					'flex min-h-12 items-center justify-end text-right font-bold',
					day.value === null || day.missing ? 'text-text-secondary' : 'text-text-primary'
				]}
			>
				{day.missing ? sleepMissingText(day.reason) : formatSleepMinutes(day.value)}
			</span>
		{/each}
		<div></div>
		<div
			bind:clientWidth={axisWidth}
			aria-hidden="true"
			class="relative mt-2 h-5 text-xs text-text-secondary"
		>
			{#each ticks as hour, index (hour)}
				<span bind:clientWidth={labelWidths[index]} class="invisible absolute whitespace-nowrap"
					>{String(hour % 24).padStart(2, '0')}:00</span
				>
			{/each}
			{#each axisLabels as { hour, left } (hour)}
				<span class="absolute whitespace-nowrap" style:left={`${left}px`}
					>{String(hour % 24).padStart(2, '0')}:00</span
				>
			{/each}
		</div>
	</div>
	<ActivityDataTooltip
		root={section}
		dates={result.days.map((day) => day.date)}
		values={tooltipValues}
		label=""
		unit=""
		allowMissing
		navigationMessage="클릭하여 수면 상세 보기"
	/>
</section>
