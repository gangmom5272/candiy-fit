<script lang="ts">
	import type { HealthSummary } from '../summary';
	import { dayMissingValue } from '../day-detail';
	import HealthChartTooltip from './HealthChartTooltip.svelte';
	import { formatSleepMinutes } from '../sleep';
	import { revealHealthFocus } from '../health-viewport';
	import { barMaximum, barOpacity } from '../health-bar-color';
	import {
		healthSummaryAxis,
		healthSummaryY,
		formatHealthSummaryTick
	} from '../health-summary-axis';

	let {
		summary,
		compact = false,
		interactive = false,
		dayHref,
		metricHref,
		onDayNavigate
	}: {
		summary: HealthSummary;
		compact?: boolean;
		interactive?: boolean;
		/** 호출부에서 resolve()와 복귀 상태를 적용한 날짜 링크를 전달한다. */
		dayHref?: (date: string) => string;
		/** 카드가 resolve()한 기존 지표 상세 경로다. */
		metricHref?: string;
		onDayNavigate?: (event: MouseEvent, date: string) => void;
	} = $props();
	const condensed = $derived(compact && !dayHref);
	const columnCount = $derived(Math.max(7, summary.values.length));
	const dateIndices = $derived(
		summary.values.flatMap((_, index) => (summary.days?.[index] ? [index] : []))
	);
	let focusedDate = $state(0);
	const tabbableDate = $derived(dateIndices.includes(focusedDate) ? focusedDate : dateIndices[0]);
	const tooltipId = $props.id();
	let figure = $state<HTMLElement>();
	let selected = $state<number | null>(null);
	let anchor = $state<HTMLElement>();
	const isDateGuide = $derived(anchor?.hasAttribute('data-date-target') ?? false);
	let pointerPosition: { x: number; y: number } | undefined;
	let isPointerInside = $state(false);
	let dismissed = $state<{ index: number; exited: boolean } | null>(null);
	const selectedDay = $derived(selected === null ? undefined : summary.days?.[selected]);
	const pointLabel = $derived(summary.id === 'heart' ? '일별 평균 심박수' : summary.label);
	function open(index: number, target: HTMLElement) {
		selected = index;
		anchor = dayHref
			? (figure?.querySelector<HTMLElement>(`[data-summary-tooltip-anchor="${index}"]`) ?? target)
			: target;
	}
	function close() {
		selected = null;
		anchor = undefined;
	}
	function point(index: number) {
		return figure?.querySelector<HTMLElement>(`[data-chart-point="${index}"]`);
	}
	function focusedPoint() {
		return figure?.querySelector<HTMLElement>('[data-chart-point]:focus-visible');
	}
	function pointBounds(target: HTMLElement) {
		const index = Number(
			target.dataset.chartPoint ??
				target.dataset.summaryTooltipAnchor ??
				target.dataset.summaryPoint
		);
		const bar = figure?.querySelector<SVGRectElement>(`[data-summary-bar="${index}"]`);
		const frame =
			selected === index ? figure?.querySelector<HTMLElement>('[data-bar-selection]') : null;
		const linePoint =
			dayHref && summary.kind === 'line'
				? figure?.querySelector<HTMLElement>(`[data-summary-point="${index}"]`)
				: null;
		const bounds = (frame ?? bar ?? linePoint ?? target).getBoundingClientRect();
		// 막대 테두리의 외곽 = 실제 막대 + 내부 간격 2.5px + border 2px.
		// 날짜 링크가 커져도 선 그래프의 기존 32px hover 범위는 유지한다.
		const gap = bar && !frame ? 4.5 : linePoint ? 11.5 : 0;
		return {
			left: bounds.left - gap,
			right: bounds.right + gap,
			top: bounds.top - gap,
			bottom: bounds.bottom + gap
		};
	}
	function contains(
		event: PointerEvent,
		bounds: Pick<DOMRect, 'left' | 'right' | 'top' | 'bottom'>
	) {
		return (
			event.clientX >= bounds.left &&
			event.clientX <= bounds.right &&
			event.clientY >= bounds.top &&
			event.clientY <= bounds.bottom
		);
	}
	function retainFocusOrClose() {
		const focused = focusedPoint();
		if (focused && Number(focused.dataset.chartPoint) !== dismissed?.index)
			open(Number(focused.dataset.chartPoint), focused);
		else close();
	}
	function dismiss() {
		// 위치용 span 대신 실제 날짜 링크로 스크롤 툴팁의 키보드 포커스를 돌려준다.
		if (
			dayHref &&
			selected !== null &&
			figure?.querySelector('[role="tooltip"]')?.contains(document.activeElement)
		)
			point(selected)?.focus({ preventScroll: true });
		const focused = focusedPoint();
		const index = selected ?? (focused ? Number(focused.dataset.chartPoint) : null);
		if (index !== null) dismissed = { index, exited: false };
		isPointerInside = false;
		close();
	}
	function activate(index: number, target: HTMLElement) {
		dismissed = null;
		isPointerInside = false;
		open(index, target);
	}
	function handlePointerMove(event: PointerEvent) {
		if (event.pointerType !== 'mouse' || !figure || (!interactive && !dayHref)) return;
		pointerPosition = { x: event.clientX, y: event.clientY };
		if (dismissed) {
			const target = point(dismissed.index);
			if (!target || !contains(event, pointBounds(target))) dismissed.exited = true;
		}
		if (selected !== null && anchor) {
			const bounds = isDateGuide ? anchor.getBoundingClientRect() : pointBounds(anchor);
			const panel = figure.querySelector<HTMLElement>('[role="tooltip"]')?.getBoundingClientRect();
			// 연결 구간은 두 영역의 겹치는 가로 폭과 사이의 세로 간격으로만 제한한다.
			const bridge = panel
				? {
						left: Math.max(bounds.left, panel.left),
						right: Math.min(bounds.right, panel.right),
						top: panel.bottom <= bounds.top ? panel.bottom : bounds.bottom,
						bottom: panel.bottom <= bounds.top ? bounds.top : panel.top
					}
				: null;
			if (
				contains(event, bounds) ||
				(panel && contains(event, panel)) ||
				(bridge &&
					bridge.left <= bridge.right &&
					bridge.top <= bridge.bottom &&
					contains(event, bridge))
			) {
				isPointerInside = true;
				return;
			}
		}
		for (const target of figure.querySelectorAll<HTMLElement>('[data-chart-point]')) {
			const index = Number(target.dataset.chartPoint);
			if (!contains(event, pointBounds(target))) continue;
			if (dismissed?.index === index && !dismissed.exited) break;
			dismissed = null;
			isPointerInside = true;
			open(index, target);
			return;
		}
		isPointerInside = false;
		retainFocusOrClose();
	}
	function move(event: KeyboardEvent) {
		const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
		if (!keys.includes(event.key) || !figure) return;
		const points = [
			...figure.querySelectorAll<HTMLElement>(dayHref ? '[data-date-target]' : '[data-chart-point]')
		];
		const current = points.findIndex((point) => point === event.target);
		const next =
			event.key === 'Home'
				? 0
				: event.key === 'End'
					? points.length - 1
					: Math.max(
							0,
							Math.min(points.length - 1, current + (event.key === 'ArrowRight' ? 1 : -1))
						);
		event.preventDefault();
		points[next]?.focus();
	}
	const dates = $derived(summary.dates?.slice(0, summary.values.length) ?? []);
	const x = (index: number) => {
		const day = summary.days?.[index];
		// 건강 홈의 연속 7일은 요일이 바뀌어도 오래된 날짜부터 표시한다.
		// 별도 축 열을 제외한 영역을 쓰고, 양 끝에는 막대 폭(32)의 절반만 남긴다.
		if (!condensed) return 16 + index * (548 / Math.max(6, summary.values.length - 1));
		// API 카드는 실제 요일 slot을 사용하고 Home의 기존 compact 배치는 유지한다.
		const slot = day ? (new Date(`${day.date}T00:00:00Z`).getUTCDay() + 6) % 7 : index;
		return 80 + slot * (450 / (day ? 6 : Math.max(1, summary.values.length - 1)));
	};
	const domain = $derived(healthSummaryAxis(summary));
	const colorMaximum = $derived(barMaximum(summary.values));
	const y = (value: number) => healthSummaryY(value, domain);
	const ticks = $derived(domain.ticks);
	const tickLabel = (value: number) => formatHealthSummaryTick(value, summary.unit);
	const groups = $derived.by(() => {
		const result: string[][] = [];
		let current: string[] = [];
		for (const [index, value] of summary.values.entries()) {
			if (value === null) {
				if (current.length) result.push(current);
				current = [];
			} else current.push(`${x(index)},${y(value)}`);
		}
		if (current.length) result.push(current);
		return result;
	});
</script>

<svelte:window
	onpointermove={handlePointerMove}
	onpointerout={(event) => {
		if (event.pointerType === 'mouse' && event.relatedTarget === null) {
			isPointerInside = false;
			retainFocusOrClose();
		}
	}}
	onkeydown={(event) => {
		if (event.key === 'Escape') dismiss();
	}}
	onpointerdown={(event) => {
		if (event.target instanceof Node && !figure?.contains(event.target)) dismiss();
	}}
/>

<figure
	bind:this={figure}
	aria-label={`${summary.label} ${summary.days ? '주간' : '최근 7일'} 기록`}
	class={[
		'relative min-w-0',
		condensed
			? 'h-full'
			: [
					'grid grid-cols-[max-content_minmax(0,1fr)] rounded-xl bg-background',
					dayHref ? 'gap-3.5' : 'gap-1.5'
				]
	]}
>
	{#if dayHref && metricHref}
		<!-- 축과 바깥 여백도 같은 지표 경로로 연결하고, plot의 링크는 scrollpane 안에 둔다. -->
		<!-- eslint-disable svelte/no-navigation-without-resolve -->
		<a
			href={metricHref}
			data-metric-frame-target
			tabindex="-1"
			aria-label={`${summary.label} 차트 상세 보기`}
			class="absolute inset-0 z-0 cursor-pointer"
			onpointerdown={dismiss}
		></a>
		<!-- eslint-enable svelte/no-navigation-without-resolve -->
	{/if}
	{#if !condensed}
		<div
			aria-hidden="true"
			class={[
				'grid h-33 text-right text-xs leading-[normal] text-text-secondary',
				dayHref && metricHref && 'pointer-events-none'
			]}
		>
			{#each ticks as tick, index (index)}
				<span
					class="relative col-start-1 row-start-1 -translate-y-1/2 self-start"
					style:top={`${y(tick)}px`}>{tickLabel(tick)}</span
				>
			{/each}
		</div>
	{/if}
	<div
		data-health-chart-scroll={dayHref ? '' : undefined}
		class={[
			'min-w-0',
			condensed ? 'h-full' : 'pr-6',
			dayHref && '-ml-[13px] overflow-x-auto pl-[13px]'
		]}
	>
		<div
			class={['relative min-w-0', condensed ? 'h-full' : '']}
			style:min-width={dayHref ? `${Math.max(180, columnCount * 24)}px` : undefined}
		>
			<svg
				viewBox="0 0 580 132"
				preserveAspectRatio="none"
				class={['block w-full rounded-xl bg-background', condensed ? 'h-full' : 'h-33']}
				aria-hidden="true"
			>
				{#each ticks as tick, index (index)}
					<line
						x1={condensed ? 38 : 0}
						x2={condensed ? 550 : 580}
						y1={y(tick)}
						y2={y(tick)}
						stroke="var(--color-border)"
					/>
				{/each}
				{#if summary.average !== null}<line
						x1={condensed ? 38 : 0}
						x2={condensed ? 550 : 580}
						y1={y(summary.average)}
						y2={y(summary.average)}
						stroke="var(--color-coral-main)"
						stroke-width="1.4"
						stroke-dasharray="5 5"
					/>{/if}
				{#if summary.kind === 'line'}
					{#each groups as group, index (index)}<polyline
							points={group.join(' ')}
							fill="none"
							stroke="var(--color-brand)"
							stroke-width="2"
							stroke-linejoin="round"
						/>{/each}
				{/if}
				{#each summary.values as value, index (index)}
					{#if value === null}
						{#if summary.kind === 'bar'}
							<line
								x1={x(index) - 16}
								x2={x(index) + 16}
								y1="97"
								y2="97"
								stroke="var(--color-border)"
								stroke-width="2"
							/>
						{/if}
					{:else if summary.kind === 'bar'}
						<rect
							data-summary-bar={interactive || dayHref ? index : undefined}
							x={x(index) - 16}
							y={y(value)}
							width="32"
							height={96 - y(value)}
							rx="6"
							fill="var(--color-brand)"
							opacity={barOpacity(value, colorMaximum)}
						/>
					{/if}
				{/each}
			</svg>
			{#if summary.kind === 'line'}
				{#each summary.values as value, index (index)}
					<!-- SVG의 비균등 확대에서 분리하고, 중심 좌표와 기존 지름 9px를 유지한다. -->
					<span
						aria-hidden="true"
						data-summary-point={index}
						data-missing={value === null ? 'true' : undefined}
						class={[
							'pointer-events-none absolute size-[9px] -translate-x-1/2 -translate-y-1/2 rounded-full',
							value === null ? 'bg-border' : 'bg-coral-main'
						]}
						style:left={`${(x(index) / 580) * 100}%`}
						style:top={`${((value === null ? 96 : y(value)) / 132) * 100}%`}
					></span>
				{/each}
			{/if}
			{#if dayHref && metricHref}
				<!-- 호출부에서 resolve한 지표 경로이며, 날짜 라벨 외 plot은 기존 상세로 이동한다. -->
				<!-- eslint-disable svelte/no-navigation-without-resolve -->
				<a
					href={metricHref}
					data-metric-target
					tabindex="-1"
					aria-label={`${summary.label} 상세 보기`}
					class="absolute inset-0 z-0 cursor-pointer"
					onpointerdown={dismiss}
				></a>
				<!-- eslint-enable svelte/no-navigation-without-resolve -->
			{/if}
			{#if interactive || dayHref}
				{#if dayHref}
					{#each summary.values as value, index (index)}
						{@const day = summary.days?.[index]}
						{#if day}
							{@const date = dates[index] ?? day.date.slice(5).replace('-', '.')}
							{#if value !== null}
								<!-- 날짜 라벨 링크와 분리하고 기존 button의 툴팁 기준 상자를 유지한다. -->
								<span
									aria-hidden="true"
									data-summary-tooltip-anchor={index}
									class="pointer-events-none absolute size-8 -translate-x-1/2 -translate-y-1/2"
									style:left={`${(x(index) / 580) * 100}%`}
									style:top={`${((summary.kind === 'bar' ? (y(value) + 96) / 2 : y(value)) / 132) * 100}%`}
									style:height={summary.kind === 'bar'
										? `max(32px, calc(${((96 - y(value)) / 132) * 100}% + 16px))`
										: undefined}
								></span>
							{/if}
							<!-- 호출부에서 이미 resolve한 href를 다시 변환하지 않는다. -->
							<!-- eslint-disable svelte/no-navigation-without-resolve -->
							<a
								href={dayHref(day.date)}
								data-date-target={day.date}
								data-chart-point={value !== null ? index : undefined}
								data-missing-point={value === null ? '' : undefined}
								tabindex={index === tabbableDate ? 0 : -1}
								aria-label={`${day.date} ${pointLabel} ${value === null ? dayMissingValue(day.reason ?? null) : summary.id === 'sleep' ? formatSleepMinutes(value) : `${value} ${summary.unit}`} 하루 상세 보기`}
								aria-describedby={selected === index ? tooltipId : undefined}
								class="absolute top-[104px] z-10 h-6 w-max min-w-6 -translate-x-1/2 cursor-pointer rounded-sm pt-1.5 text-center text-xs leading-[normal] text-text-secondary focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
								style:left={`${(x(index) / 580) * 100}%`}
								onpointermove={(event) => {
									// 패널 재배치의 pointerenter 대신 실제 좌표 이동으로만 안내를 연다.
									if (
										event.pointerType !== 'mouse' ||
										(pointerPosition?.x === event.clientX && pointerPosition.y === event.clientY) ||
										anchor === event.currentTarget
									)
										return;
									activate(index, event.currentTarget);
									anchor = event.currentTarget;
								}}
								onpointerdown={value === null ? dismiss : undefined}
								onfocus={(event) => {
									focusedDate = index;
									const keyboardFocus = event.currentTarget.matches(':focus-visible');
									if (keyboardFocus) revealHealthFocus(event.currentTarget);
									if (value === null) dismiss();
									else if (keyboardFocus) activate(index, event.currentTarget);
								}}
								onblur={(event) => {
									if (
										event.relatedTarget instanceof Element &&
										event.relatedTarget.closest('[role="tooltip"]')
									)
										return;
									if (
										!isPointerInside ||
										(event.relatedTarget instanceof Node && !figure?.contains(event.relatedTarget))
									)
										close();
								}}
								onclick={(event) => {
									event.stopPropagation();
									focusedDate = index;
									if (value === null) dismiss();
									else activate(index, event.currentTarget);
									onDayNavigate?.(event, day.date);
								}}
								onkeydown={move}
							>
								<span data-summary-date={date} class="block">
									<span class="sm:hidden">{date.slice(-2)}</span><span class="hidden sm:inline"
										>{date}</span
									>
								</span>
							</a>
							<!-- eslint-enable svelte/no-navigation-without-resolve -->
							{#if value !== null && selected === index && !isDateGuide}
								{#if summary.kind === 'bar'}
									<span
										aria-hidden="true"
										data-bar-selection
										class="bar-selection pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-1/2 rounded-lg border-2 border-brand"
										style:left={`${(x(index) / 580) * 100}%`}
										style:top={`${((y(value) + 96) / 2 / 132) * 100}%`}
										style:width={`calc(${(32 / 580) * 100}% + 9px)`}
										style:height={`calc(${((96 - y(value)) / 132) * 100}% + 9px)`}
									></span>
								{:else}
									<span
										aria-hidden="true"
										data-point-selection={index}
										class="pointer-events-none absolute z-10 size-8 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-brand"
										style:left={`${(x(index) / 580) * 100}%`}
										style:top={`${(y(value) / 132) * 100}%`}
									></span>
								{/if}
							{/if}
						{/if}
					{/each}
				{:else}
					{#each summary.values as value, index (index)}
						{#if summary.days?.[index]}
							{#if value === null}
								<span
									aria-hidden="true"
									data-missing-point
									class="absolute z-10 size-8 -translate-x-1/2 -translate-y-1/2"
									style:left={`${(x(index) / 580) * 100}%`}
									style:top={`${(96 / 132) * 100}%`}
									onpointerdown={dismiss}
								></span>
							{:else}
								<button
									type="button"
									data-chart-point={index}
									aria-label={`${summary.days[index].date} ${pointLabel} ${summary.id === 'sleep' ? formatSleepMinutes(value) : `${value} ${summary.unit}`}`}
									aria-describedby={selected === index ? tooltipId : undefined}
									class={[
										'absolute z-10 size-8 -translate-x-1/2 -translate-y-1/2 cursor-pointer border-2 border-transparent',
										summary.kind === 'bar'
											? 'rounded-lg focus-visible:outline-none'
											: 'rounded-full hover:border-brand focus-visible:border-brand focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring'
									]}
									style:border-color={selected === index && summary.kind !== 'bar'
										? 'var(--color-brand)'
										: undefined}
									style:left={`${(x(index) / 580) * 100}%`}
									style:top={`${((summary.kind === 'bar' ? (y(value) + 96) / 2 : y(value)) / 132) * 100}%`}
									style:height={summary.kind === 'bar'
										? `max(32px, calc(${((96 - y(value)) / 132) * 100}% + 16px))`
										: undefined}
									onfocus={(event) => activate(index, event.currentTarget)}
									onblur={(event) => {
										if (
											event.relatedTarget instanceof Element &&
											event.relatedTarget.closest('[role="tooltip"]')
										)
											return;
										if (
											!isPointerInside ||
											(event.relatedTarget instanceof Node &&
												!figure?.contains(event.relatedTarget))
										)
											close();
									}}
									onclick={(event) => activate(index, event.currentTarget)}
									onkeydown={move}
								></button>
								{#if summary.kind === 'bar' && selected === index}
									<!-- 선택 영역은 유지하고, 보이는 테두리만 실제 막대에서 2.5px 떨어뜨린다.
					     border 2px을 포함해 외곽 크기는 양쪽에 각각 4.5px씩 더한다. -->
									<span
										aria-hidden="true"
										data-bar-selection
										class="bar-selection pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-1/2 rounded-lg border-2 border-brand"
										style:left={`${(x(index) / 580) * 100}%`}
										style:top={`${((y(value) + 96) / 2 / 132) * 100}%`}
										style:width={`calc(${(32 / 580) * 100}% + 9px)`}
										style:height={`calc(${((96 - y(value)) / 132) * 100}% + 9px)`}
									></span>
								{/if}
							{/if}
						{/if}
					{/each}
				{/if}
				{#if selected !== null && (summary.values[selected] !== null || isDateGuide) && selectedDay && anchor}
					<HealthChartTooltip
						id={tooltipId}
						ondismiss={dismiss}
						{anchor}
						date={selectedDay.date}
						label={pointLabel}
						value={summary.values[selected]}
						unit={summary.unit}
						message={isDateGuide ? '클릭하여 하루 상세 보기' : undefined}
						placement={isDateGuide ? 'below' : 'auto'}
						valueText={summary.id === 'sleep'
							? formatSleepMinutes(summary.values[selected])
							: undefined}
						note={!isDateGuide && selectedDay.isInProgress
							? '오늘 기록 · 기간 평균에서 제외'
							: undefined}
					/>
				{/if}
			{/if}
			<!-- 모바일은 상단 조회 기간과 함께 일자만 표시하고, 전체 날짜·데이터는 유지한다. -->
			{#if !condensed && !dayHref}<div
					aria-hidden="true"
					class="pointer-events-none absolute inset-0 text-xs leading-[normal] text-text-secondary"
				>
					{#each dates as date, index (date)}
						<span
							data-summary-date={date}
							class="absolute top-[110px] -translate-x-1/2"
							style:left={`${(x(index) / 580) * 100}%`}
							><span class="sm:hidden">{date.slice(-2)}</span><span class="hidden sm:inline"
								>{date}</span
							></span
						>
					{/each}
				</div>{/if}
		</div>
	</div>
	<figcaption class="sr-only">
		{summary.value}. {#if summary.average !== null}점선은 평균이에요.{/if} 회색 표시는 기록이 없는 날이며,
		0을 뜻하지 않아요.
		{#each summary.values as value, index (index)}
			{dates[index]}: {value === null
				? dayMissingValue(summary.days?.[index]?.reason ?? null)
				: summary.id === 'sleep'
					? formatSleepMinutes(value)
					: `${value}${summary.unit}`}.
		{/each}
	</figcaption>
</figure>

<style>
	/* Escape로 설명을 닫아도 현재 키보드 포커스 위치는 계속 보여준다. */
	button:focus-visible:not(:has(+ .bar-selection)) {
		outline: 2px solid var(--color-ring);
		outline-offset: 2px;
	}

	button:focus-visible + .bar-selection {
		outline: 2px solid var(--color-ring);
		outline-offset: 2px;
	}
</style>
