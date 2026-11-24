<script lang="ts">
	import { oxygenDomain, oxygenSegments, oxygenX, oxygenY, type SleepMetricResult } from '../sleep';
	let { result }: { result: SleepMetricResult } = $props();
	const domain = $derived(oxygenDomain(result.days));
	const step = $derived(Math.max(2, Math.ceil((domain.max - domain.min) / 10) * 2));
	const ticks = $derived(
		Array.from(
			{ length: Math.floor((domain.max - domain.min) / step) + 1 },
			(_, index) => domain.max - index * step
		)
	);
	const segments = $derived(oxygenSegments(result.days, domain));
	const tooltipId = $props.id();
	let selectedDate = $state<string | null>(null);
	let chart = $state<HTMLElement>();
	let tooltip = $state<HTMLDivElement>();
	let tooltipPosition = $state({ left: 0, top: 0, maxHeight: 0, maxWidth: 280 });
	let tooltipScrollable = $state(false);
	let restoringFocus = false;
	const selectedDay = $derived(
		result.days.find((day) => day.date === selectedDate && !day.missing && day.value !== null)
	);
	const selectedRange = $derived(
		result.detail.dailyRanges.find((range) => range.date === selectedDay?.date)
	);
	const dateLabel = (date: string) =>
		`${Number(date.slice(5, 7))}월 ${Number(date.slice(8, 10))}일`;
	const percentLabel = (value: number | null | undefined) =>
		value == null ? '기록 없음' : `${value}%`;

	function placeTooltip() {
		const anchor = chart?.querySelector<HTMLButtonElement>(`button[data-date="${selectedDate}"]`);
		if (!tooltip || !anchor) return;
		const header = document.querySelector('header');
		const navigation = document.querySelector('nav[aria-label="주 메뉴"]');
		const top = Math.max(8, (header?.getBoundingClientRect().bottom ?? 0) + 8);
		const bottom =
			navigation && getComputedStyle(navigation).position === 'fixed'
				? navigation.getBoundingClientRect().top - 8
				: window.innerHeight - 8;
		const maxHeight = Math.max(1, bottom - top);
		const rect = anchor.getBoundingClientRect();
		const panel = tooltip.getBoundingClientRect();
		const card = chart!.closest('section')!.getBoundingClientRect();
		const left = Math.max(8, card.left);
		const right = Math.min(window.innerWidth - 8, card.right);
		const height = Math.min(tooltip.scrollHeight + 2, maxHeight);
		tooltipScrollable = tooltip.scrollHeight + 2 > maxHeight;
		const above = rect.top - top - 8;
		const below = bottom - rect.bottom - 8;
		const preferredTop = above >= height || above > below ? rect.top - height - 8 : rect.bottom + 8;
		tooltipPosition = {
			left: Math.max(
				left,
				Math.min(right - panel.width, rect.x + rect.width / 2 - panel.width / 2)
			),
			top: Math.max(top, Math.min(bottom - height, preferredTop)),
			maxHeight,
			maxWidth: Math.max(1, right - left)
		};
	}

	$effect(() => {
		if (!selectedDay || !tooltip || !chart) return;
		const observer = new ResizeObserver(placeTooltip);
		observer.observe(tooltip);
		observer.observe(chart);
		placeTooltip();
		return () => observer.disconnect();
	});
</script>

<svelte:window
	onresize={placeTooltip}
	onscroll={placeTooltip}
	onkeydown={(event) => {
		if (event.key !== 'Escape') return;
		const anchor =
			selectedDay && tooltip?.contains(document.activeElement)
				? chart?.querySelector<HTMLButtonElement>(`button[data-date="${selectedDay.date}"]`)
				: null;
		selectedDate = null;
		if (anchor) {
			// focus()의 동기 onfocus에만 적용해 닫힌 툴팁이 다시 열리지 않게 한다.
			restoringFocus = true;
			try {
				anchor.focus({ preventScroll: true });
			} finally {
				restoringFocus = false;
			}
		}
	}}
	onpointerdown={(event) => {
		if (event.target instanceof Node && !chart?.contains(event.target)) selectedDate = null;
	}}
/>

<section
	aria-labelledby="sleep-oxygen-title"
	class="min-w-0 rounded-2xl border border-border bg-card px-4 py-6 sm:px-7"
>
	<div class="flex flex-wrap items-start justify-between gap-4">
		<div>
			<h2 id="sleep-oxygen-title" class="text-card-title-lg">수면 중 산소포화도</h2>
			<p class="sr-only">주 수면 중 측정된 값 기준</p>
		</div>
		{#if result.summary.avg !== null}
			<dl class="flex flex-wrap gap-6">
				<div>
					<dt class="text-sm text-text-secondary">평균 산소포화도</dt>
					<dd class="mt-1 text-metric">{result.summary.avg}%</dd>
				</div>
				<div>
					<dt class="text-sm text-text-secondary">최저</dt>
					<dd class="mt-1 text-metric">
						{result.detail.measuredMin === null
							? '기록 없음'
							: `${result.detail.measuredMin.value}%`}
					</dd>
				</div>
			</dl>
		{/if}
	</div>
	{#if result.summary.avg === null}
		<p class="grid min-h-32 place-items-center text-sm text-text-secondary">
			선택한 기간에 수면 중 산소포화도 기록이 없어요.
		</p>
	{:else}
		<figure
			bind:this={chart}
			class="relative mt-5 min-w-0"
			aria-label="날짜별 수면 중 평균 산소포화도"
		>
			<svg
				viewBox="0 0 580 132"
				preserveAspectRatio="none"
				class="block h-32 w-full"
				aria-hidden="true"
			>
				{#each ticks as tick (tick)}
					<line
						x1="36"
						x2="564"
						y1={oxygenY(tick, domain)}
						y2={oxygenY(tick, domain)}
						stroke="var(--color-border)"
					/>
				{/each}
				{#each segments as points, i (i)}
					<polyline {points} fill="none" stroke="var(--color-brand)" stroke-width="2" />
				{/each}
			</svg>
			{#each result.days as day, i (day.date)}
				{#if !day.missing && day.value !== null}
					<span
						data-oxygen-point
						aria-hidden="true"
						class="pointer-events-none absolute size-[7px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand ring-1 ring-card"
						style:left={`${(oxygenX(i, result.days.length) / 580) * 100}%`}
						style:top={`${(oxygenY(day.value, domain) / 132) * 100}%`}
					></span>
					<button
						data-date={day.date}
						type="button"
						aria-label={`${dateLabel(day.date)} 산소포화도 상세`}
						aria-describedby={selectedDay?.date === day.date ? tooltipId : undefined}
						onpointerenter={(event) => {
							if (event.pointerType === 'mouse') selectedDate = day.date;
						}}
						onpointerleave={(event) => {
							if (
								event.pointerType === 'mouse' &&
								event.currentTarget !== document.activeElement &&
								!(event.relatedTarget instanceof Node && tooltip?.contains(event.relatedTarget))
							)
								selectedDate = null;
						}}
						onfocus={() => {
							if (!restoringFocus) selectedDate = day.date;
						}}
						onblur={(event) => {
							if (!(event.relatedTarget instanceof Node && tooltip?.contains(event.relatedTarget)))
								selectedDate = null;
						}}
						onclick={() => (selectedDate = day.date)}
						class="absolute size-8 -translate-x-1/2 -translate-y-1/2 cursor-pointer rounded-full focus-visible:outline-2 focus-visible:outline-ring"
						style:left={`${(oxygenX(i, result.days.length) / 580) * 100}%`}
						style:top={`${(oxygenY(day.value, domain) / 132) * 100}%`}
					></button>
				{/if}
			{/each}
			{#if selectedDay && selectedDay.value !== null}
				<!-- svelte-ignore a11y_no_noninteractive_tabindex (좁은 화면에서 스크롤되는 툴팁에만 키보드 접근 제공) -->
				<div
					bind:this={tooltip}
					id={tooltipId}
					role="tooltip"
					tabindex={tooltipScrollable ? 0 : undefined}
					onblur={(event) => {
						if (!(event.relatedTarget instanceof Node && chart?.contains(event.relatedTarget)))
							selectedDate = null;
					}}
					onpointerleave={(event) => {
						if (
							event.pointerType === 'mouse' &&
							document.activeElement !== tooltip &&
							!chart?.querySelector('button:focus')
						)
							selectedDate = null;
					}}
					class="fixed z-50 w-[280px] max-w-[calc(100vw-1rem)] overflow-y-auto rounded-2xl border border-border bg-card px-4 py-3 shadow-[0_2px_2px_0_var(--color-border)]"
					class:pointer-events-none={!tooltipScrollable}
					style:left={`${tooltipPosition.left}px`}
					style:top={`${tooltipPosition.top}px`}
					style:max-height={`${tooltipPosition.maxHeight}px`}
					style:max-width={`${tooltipPosition.maxWidth}px`}
					style:visibility={tooltipPosition.maxHeight ? 'visible' : 'hidden'}
				>
					<p class="border-b border-border pb-2 text-lg font-bold">{dateLabel(selectedDay.date)}</p>
					<dl class="divide-y divide-border">
						{#each [['평균 산소포화도', selectedDay.value], ['최저 산소포화도', selectedRange?.min], ['최고 산소포화도', selectedRange?.max]] as [label, value] (label)}
							<div class="flex items-center justify-between gap-3 py-2">
								<dt class="text-sm text-text-secondary">{label}</dt>
								<dd class="text-base font-bold">
									{percentLabel(typeof value === 'number' ? value : null)}
								</dd>
							</div>
						{/each}
					</dl>
				</div>
			{/if}
			<div
				aria-hidden="true"
				class="pointer-events-none absolute inset-0 text-xs leading-normal text-text-secondary"
			>
				{#each ticks as tick (tick)}
					<span
						class="absolute right-[95%] -translate-x-1 -translate-y-1/2"
						style:top={`${(oxygenY(tick, domain) / 132) * 128}px`}>{tick}</span
					>
				{/each}
				{#each result.days as day, i (day.date)}
					<span
						class="absolute bottom-0 -translate-x-1/2"
						style:left={`${(oxygenX(i, result.days.length) / 580) * 100}%`}
						><span class="sm:hidden">{day.date.slice(8)}</span><span class="hidden sm:inline"
							>{day.date.slice(5).replace('-', '/')}</span
						></span
					>
				{/each}
			</div>
			<figcaption class="sr-only">
				{#each result.days as day (day.date)}{day.date}: {day.missing || day.value === null
						? '기록 없음'
						: `${day.value}%`}.
				{/each}
			</figcaption>
		</figure>
	{/if}
</section>
