<script lang="ts">
	import HealthChartTooltip from './HealthChartTooltip.svelte';
	import { formatAverageSteps as stepsLabel } from '../activity';
	import { barOpacity } from '../health-bar-color';
	let {
		values,
		labels,
		caption,
		period,
		hourly = false
	}: {
		values: readonly (number | null)[];
		labels: readonly string[];
		caption: string;
		period: string;
		hourly?: boolean;
	} = $props();
	const tooltipId = $props.id();
	let figure = $state<HTMLElement>();
	let selected = $state<number | null>(null);
	let anchor = $state<HTMLButtonElement>();
	const selectedValue = $derived(selected === null ? null : (values[selected] ?? null));
	let dismissed: HTMLButtonElement | undefined;
	let pointerType = 'mouse';
	let pinned = false;
	let lastPointerY: number | undefined;
	const maximum = $derived(Math.max(0, ...values.filter((value) => value !== null)));
	const plotHeight = 80;
	const tooltipGap = 10;
	function close() {
		selected = null;
		anchor = undefined;
		pinned = false;
		lastPointerY = undefined;
	}
	function open(index: number, target: HTMLButtonElement) {
		if (dismissed === target) return;
		selected = index;
		anchor = target;
	}
	function dismiss() {
		dismissed = anchor;
		close();
	}
	$effect(() => {
		void values;
		close();
		dismissed = undefined;
	});
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
	function inTooltipBridge(event: PointerEvent, bounds: DOMRect, panel: DOMRect | undefined) {
		if (!panel) return false;
		// 운동 차트와 같이 두 영역을 잇는 경로만 허용한다. 옆으로 보정된 툴팁도 포함한다.
		const upper = panel.bottom <= bounds.top ? panel : bounds;
		const lower = upper === panel ? bounds : panel;
		const startY = upper === panel ? upper.bottom : bounds.top;
		const endY = lower === panel ? lower.top : bounds.bottom;
		const progress = (event.clientY - startY) / (endY - startY);
		return (
			endY > startY &&
			progress >= 0 &&
			progress <= 1 &&
			event.clientX >= upper.left + (lower.left - upper.left) * progress &&
			event.clientX <= upper.right + (lower.right - upper.right) * progress
		);
	}
	function handlePointerMove(event: PointerEvent) {
		if (event.pointerType !== 'mouse') return;
		lastPointerY = event.clientY;
		if (dismissed && !contains(event, dismissed.getBoundingClientRect())) dismissed = undefined;
		if (!anchor || pinned) return;
		const bounds = anchor.getBoundingClientRect();
		const panel = figure?.querySelector<HTMLElement>('[role="tooltip"]')?.getBoundingClientRect();
		if (
			contains(event, bounds) ||
			(panel && contains(event, panel)) ||
			inTooltipBridge(event, bounds, panel)
		)
			return;
		const focused = figure?.querySelector<HTMLButtonElement>('button:focus-visible');
		if (focused && focused !== dismissed) open(Number(focused.dataset.index), focused);
		else close();
	}
	function move(event: KeyboardEvent) {
		if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
		const buttons = [...(figure?.querySelectorAll<HTMLButtonElement>('button') ?? [])];
		const current =
			event.currentTarget instanceof HTMLButtonElement ? buttons.indexOf(event.currentTarget) : -1;
		const next =
			event.key === 'Home'
				? 0
				: event.key === 'End'
					? buttons.length - 1
					: Math.max(
							0,
							Math.min(buttons.length - 1, current + (event.key === 'ArrowRight' ? 1 : -1))
						);
		event.preventDefault();
		buttons[next]?.focus();
	}
</script>

<svelte:window
	onpointermove={handlePointerMove}
	onpointerdown={(event) => {
		if (event.target instanceof Node && !figure?.contains(event.target)) dismiss();
	}}
	onkeydown={(event) => {
		if (event.key === 'Escape') dismiss();
	}}
	onpointerout={(event) => {
		if (
			event.pointerType === 'mouse' &&
			event.relatedTarget === null &&
			!anchor?.matches(':focus-visible')
		)
			close();
	}}
/>
<figure bind:this={figure} aria-label={caption} class="relative mt-auto pt-8">
	<div
		class={['flex h-20 items-end justify-between gap-1 px-3.5', hourly && 'border-b border-border']}
	>
		{#each values as value, index (index)}
			<div
				class={[
					'relative flex h-full min-w-0 flex-1 items-end',
					hourly ? 'max-w-2.5' : 'max-w-[34px]'
				]}
			>
				{#if value !== null && (!hourly || value > 0)}
					<button
						type="button"
						data-index={index}
						aria-label={`${labels[index]} · 평균 ${stepsLabel(value)}보`}
						aria-describedby={selected === index ? tooltipId : undefined}
						onpointerdown={(event) => {
							pointerType = event.pointerType;
						}}
						onpointerenter={(event) => {
							if (event.pointerType === 'mouse') {
								const panel = figure
									?.querySelector<HTMLElement>('[role="tooltip"]')
									?.getBoundingClientRect();
								if (panel && contains(event, panel)) return;
								if (anchor && panel && !pinned && lastPointerY !== undefined) {
									const bounds = anchor.getBoundingClientRect();
									const towardPanel =
										panel.bottom <= bounds.top
											? event.clientY < lastPointerY
											: event.clientY > lastPointerY;
									// 연결 경로에서는 이웃 막대를 지나도 원래 값을 유지한다. 수평 이동·클릭은 전환한다.
									if (towardPanel && inTooltipBridge(event, bounds, panel)) return;
								}
								pinned = false;
								open(index, event.currentTarget);
							}
						}}
						onfocus={(event) => {
							dismissed = undefined;
							open(index, event.currentTarget);
						}}
						onblur={(event) => {
							if (
								event.relatedTarget instanceof Element &&
								event.relatedTarget.closest('[role="tooltip"]')
							)
								return;
							if (dismissed === event.currentTarget) dismissed = undefined;
							if (!anchor?.matches(':hover') && !figure?.querySelector('[role="tooltip"]:hover'))
								close();
						}}
						onclick={(event) => {
							if (pointerType !== 'mouse' && selected === index && pinned) dismiss();
							else {
								dismissed = undefined;
								open(index, event.currentTarget);
								pinned = pointerType !== 'mouse' && event.detail !== 0;
							}
						}}
						onkeydown={move}
						class="flex h-full w-full cursor-pointer items-end rounded-lg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-info"
					>
						<span
							data-average-bar
							aria-hidden="true"
							class={['block w-full bg-brand', hourly ? 'rounded-t-[3px]' : 'rounded-[7px]']}
							style:height={`${maximum === 0 ? 0 : (value / maximum) * plotHeight}px`}
							style:opacity={barOpacity(value, maximum)}
						></span>
					</button>
				{/if}
			</div>
		{/each}
	</div>
	{#if hourly}
		<div
			aria-hidden="true"
			class="mt-3.5 flex justify-between px-3.5 text-xs leading-4 text-text-secondary"
		>
			{#each ['00시', '06시', '12시', '18시', '24시'] as label (label)}<span>{label}</span>{/each}
		</div>
	{:else}
		<div
			class="mt-3 flex justify-between gap-1 px-3.5 text-center text-xs leading-4 text-text-secondary"
		>
			{#each labels as label, index (index)}
				{@const value = values[index] ?? null}
				<p class="max-w-[34px] min-w-0 flex-1">
					{label.replace('요일', '')}<span class="sr-only"
						>: {value === null ? '기록 없음' : `${stepsLabel(value)}보`}</span
					>
				</p>{/each}
		</div>
	{/if}
	{#if selected !== null && anchor && figure && selectedValue !== null && (!hourly || selectedValue > 0)}
		<HealthChartTooltip
			id={tooltipId}
			ondismiss={dismiss}
			{anchor}
			container={figure.closest('section') ?? figure}
			date={period}
			label={`${labels[selected]} · 평균`}
			value={selectedValue}
			valueText={`${stepsLabel(selectedValue)}보`}
			unit="보"
			compact
			gap={tooltipGap}
		/>
	{/if}
	<figcaption class="sr-only">
		막대에 마우스를 올리거나 키보드로 이동하거나 터치하면 평균을 확인할 수 있어요. Escape로 닫을 수
		있어요.
	</figcaption>
</figure>

<style>
	/* 설명 위에서도 호버는 좌표로 유지하고, 아래 막대의 클릭·터치는 막지 않는다. */
	figure :global([role='tooltip']:not([data-scrollable='true'])) {
		pointer-events: none;
	}
</style>
