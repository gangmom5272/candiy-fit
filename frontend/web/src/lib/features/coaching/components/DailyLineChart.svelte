<script lang="ts">
	import { untrack } from 'svelte';
	import { chartScale, formatAxisValue, scaleFraction } from '../chart';
	import type { ChartBar } from '../evidence-view';
	import { formatMetricValue } from '../format';
	import type { HealthMetric } from '../types';
	import { VISIBLE_DAYS, WeekScroll, type RangeView } from '../week-scroll.svelte';
	import ChartRangeControls from './ChartRangeControls.svelte';
	import ChartScrollBar from './ChartScrollBar.svelte';

	/**
	 * 날짜별 꺾은선 차트(S15P31A403-388, 앱 384와 같다). 지금은 산소포화도에만 쓴다(chart.ts isLineChart).
	 * 값이 90~100에 모여 있어 0에서 시작하는 막대로는 차이가 보이지 않아, 축을 90에서 시작하고 선과 점으로 그린다.
	 * - 기록이 없는 날에서는 선을 끊는다(없는 값을 이어 그리지 않는다). 수집 중인 오늘은 속이 빈 점이다
	 * - 기간이 일주일을 넘으면 최근 7일만 먼저 보이고 나머지는 가로로 넘겨 본다(일별 차트 공통 규칙, week-scroll).
	 *   처음에는 휴대폰 폭에서만 밀었는데, 418 에서 모든 폭의 공통 규칙이 되었다
	 * 라이브러리 없이 SVG 선과 HTML 점으로 그린다(막대 차트와 같은 방침)
	 */
	type Props = {
		metric: HealthMetric;
		bars: ChartBar[];
		/** 평균선. 기록이 하루뿐이면 점과 같은 값이라 넘기지 않는다 */
		average: number | null;
		/**
		 * 점 하나가 하루인지. 주 단위로 묶은 점은 일별 차트 공통 규칙(일주일부터 넘겨 보기)을 따르지 않는다(S15P31A403-420).
		 * 420 전에는 60일처럼 주가 8개를 넘으면 주 단위 차트까지 7칸씩 넘어가고 보기 전환에 "9일"이라고 나왔다
		 */
		daily?: boolean;
		/**
		 * 지금 보기(일주일·기간 전체). 근거 창이 탭마다 기억해 두었다가 그 탭으로 돌아오면 같은 보기로 시작하게 한다(S15P31A403-439).
		 * 넘기지 않으면 일주일로 시작한다
		 */
		view?: RangeView;
		/** 차트 설명. 화면에는 보이지 않고 보조기기에 읽힌다 */
		label: string;
	};

	let { metric, bars, average, daily = true, view = $bindable('week'), label }: Props = $props();

	const scale = $derived(
		chartScale(
			metric,
			bars.map((bar) => bar.value),
			average
		)
	);
	const toPercent = (value: number) => `${scaleFraction(scale, value) * 100}%`;
	/** 칸 가운데의 가로 위치(0~100) */
	const centerX = (index: number) => ((index + 0.5) / bars.length) * 100;
	/** 이웃한 두 날 모두 값이 있을 때만 잇는 선분 */
	const segments = $derived(
		bars.flatMap((bar, index) => {
			const next = bars[index + 1];
			return bar.value !== null && next && next.value !== null
				? [
						{
							key: bar.key,
							x1: centerX(index),
							y1: 100 - scaleFraction(scale, bar.value) * 100,
							x2: centerX(index + 1),
							y2: 100 - scaleFraction(scale, next.value) * 100
						}
					]
				: [];
		})
	);

	const range = new WeekScroll(
		() => (daily ? bars.length : 0),
		undefined,
		() => bars.findLastIndex((bar) => bar.value !== null)
	);
	// 창이 기억해 둔 보기로 시작하고, 사용자가 바꾸면 창에 알린다
	range.view = untrack(() => view);
	$effect(() => {
		view = range.view;
	});
	$effect(() => range.reset());
	$effect(() => range.observe());
</script>

{#snippet lineLegend()}
	<!-- 점선이 평균선임을 알린다(S15P31A403-393). 수치는 위 요약 상자에 있다 -->
	<p class="flex items-center gap-2" data-average-legend>
		<span class="w-6 border-t border-dashed border-coral-main" aria-hidden="true"></span>
		기간 평균
	</p>
{/snippet}

<figure class="isolate rounded-2xl bg-surface px-5 pt-6 pb-4" aria-label={label}>
	{#if average !== null || range.available}
		<!-- 선의 뜻은 오른쪽 위에 두고, 기간이 일주일을 넘으면 그 자리는 넘기는 버튼과 보기 전환이 쓴다(S15P31A403-418) -->
		<div
			class={[
				'mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-primary',
				range.available ? 'justify-between' : 'justify-end'
			]}
		>
			{#if range.available}
				<div>
					{#if average !== null}{@render lineLegend()}{/if}
				</div>
				<ChartRangeControls {range} />
			{:else}
				{@render lineLegend()}
			{/if}
		</div>
	{/if}
	<div class="flex gap-3">
		<!-- 눈금 글자. 차트를 넘겨도 제자리에 있다 -->
		<div class="relative h-44 w-8 shrink-0 text-right text-xs text-text-primary" aria-hidden="true">
			{#each scale.ticks as tick (tick)}
				<span class="absolute right-0 translate-y-1/2" style:bottom={toPercent(tick)}>
					{formatAxisValue(metric, tick)}
				</span>
			{/each}
		</div>

		<div class="relative min-w-0 flex-1">
			<!-- 눈금선과 평균선은 넘어가는 칸 바깥에 두어 넘겨도 제자리에 있다 -->
			<div class="pointer-events-none absolute inset-x-0 top-0 h-44" aria-hidden="true">
				{#each scale.ticks as tick (tick)}
					<div
						class="absolute inset-x-0 border-t border-border"
						style:bottom={toPercent(tick)}
					></div>
				{/each}
				{#if average !== null}
					<!-- 꺾은선과 같은 색이면 겹쳐 보여 평균선은 코랄로 둔다. 수치는 위 요약 상자에 있어 쓰지 않는다 -->
					<div
						class="absolute inset-x-0 border-t border-dashed border-coral-main"
						style:bottom={toPercent(average)}
					></div>
				{/if}
			</div>

			<!-- 스크롤 영역이라 키보드로도 넘길 수 있게 포커스를 받는다(넘길 내용이 없으면 받지 않는다) -->
			<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
			<div
				bind:this={range.element}
				class={['relative', range.scrolls && range.scrollerClass]}
				role={range.scrolls ? 'region' : undefined}
				aria-label={range.scrolls ? `${label} 가로 스크롤` : undefined}
				tabindex={range.scrolls ? 0 : undefined}
				onscroll={range.onscroll}
				onpointerdown={range.onpointerdown}
				onpointermove={range.onpointermove}
				onpointerup={range.onpointerup}
				onpointercancel={range.onpointerup}
				data-line-chart-scroll
				data-range-scroller={range.scrolls ? '' : undefined}
			>
				<!-- 하루 폭이 넘어가는 칸의 1/7 이 되게, 안쪽을 날 수만큼 넓힌다 -->
				<div style:width={range.scrolls ? `${(bars.length / VISIBLE_DAYS) * 100}%` : undefined}>
					<div class="relative h-44">
						<!-- 선은 영역 크기에 맞춰 늘어나도 굵기가 변하지 않게 한다 -->
						<svg
							class="absolute inset-0 size-full overflow-visible"
							viewBox="0 0 100 100"
							preserveAspectRatio="none"
							aria-hidden="true"
						>
							{#each segments as segment (segment.key)}
								<line
									x1={segment.x1}
									y1={segment.y1}
									x2={segment.x2}
									y2={segment.y2}
									class="stroke-brand"
									stroke-width="2"
									stroke-linecap="round"
									vector-effect="non-scaling-stroke"
								/>
							{/each}
						</svg>

						<ol class="absolute inset-0">
							{#each bars as bar, index (bar.key)}
								<li>
									{#if bar.value !== null}
										<!-- 점은 HTML 로 둔다. SVG 를 가로세로 다르게 늘리면 원이 찌그러진다 -->
										<span
											class={[
												'absolute size-2 -translate-x-1/2 translate-y-1/2 rounded-full',
												bar.inProgress ? 'border-2 border-coral-main bg-card' : 'bg-brand'
											]}
											style:left={`${centerX(index)}%`}
											style:bottom={toPercent(bar.value)}
											title={`${bar.title} ${formatMetricValue(metric, bar.value)}${bar.inProgress ? ' (수집 중)' : ''}`}
											data-line-point
										></span>
									{/if}
									<span class="sr-only">
										{bar.title}
										{bar.value !== null ? formatMetricValue(metric, bar.value) : '기록 없음'}
										{bar.inProgress ? '수집 중' : ''}
									</span>
								</li>
							{/each}
						</ol>
					</div>

					<ol class="mt-2 flex text-xs text-text-primary" aria-hidden="true">
						{#each bars as bar, barIndex (bar.key)}
							<!-- 글자는 칸보다 넓을 수 있어 칸 폭을 늘리지 않고 가운데에 겹쳐 둔다. 넘겨 볼 때는 7칸뿐이라 모든 날짜를 적고, 휴대폰 폭에서만 하루 걸러 적는다 -->
							<li class="relative h-4 min-w-0 flex-1" data-day={bar.key}>
								<span
									class={[
										'absolute left-1/2 -translate-x-1/2 whitespace-nowrap',
										!range.scrolls && !bar.labelCompact && 'max-sm:hidden',
										range.scrolls && !range.labelsOnNarrow(barIndex) && 'max-sm:hidden',
										!range.scrolls && !bar.labelWide && 'sm:hidden'
									]}
								>
									{bar.axisLabel}
								</span>
							</li>
						{/each}
					</ol>
				</div>
			</div>
			<ChartScrollBar {range} />
			{#if range.scrolls}
				<!-- 더 넘길 날이 있는 쪽 가장자리를 옅게 흐려 이어진다는 것을 알린다 -->
				<span
					class={[
						'pointer-events-none absolute top-0 bottom-6 left-0 w-4 bg-linear-to-r from-surface to-transparent transition-opacity',
						range.first === 0 && 'opacity-0'
					]}
					aria-hidden="true"
				></span>
				<span
					class={[
						'pointer-events-none absolute top-0 right-0 bottom-6 w-4 bg-linear-to-l from-surface to-transparent transition-opacity',
						range.first >= range.lastStart && 'opacity-0'
					]}
					aria-hidden="true"
				></span>
			{/if}
		</div>
	</div>
</figure>
