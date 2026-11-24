<script lang="ts">
	import { untrack, type Snippet } from 'svelte';
	let {
		id,
		anchor,
		date,
		label,
		value,
		unit,
		valueText,
		missingValueText = '기록 없음',
		rows,
		message,
		note,
		container,
		compact = false,
		gap = 8,
		placement = 'auto',
		wide = false,
		ondismiss,
		children
	}: {
		id: string;
		anchor: HTMLElement;
		date: string;
		label: string;
		value: number | null;
		unit: string;
		valueText?: string;
		missingValueText?: string;
		rows?: readonly { label: string; value: number | null; unit: string }[];
		message?: string;
		note?: string;
		container?: HTMLElement;
		compact?: boolean;
		gap?: number;
		/** 아래 고정 안내는 공간이 부족해도 그래프 위로 뒤집지 않는다. */
		placement?: 'auto' | 'below';
		/** 긴 날짜·시간은 카드 너비 대신 화면 안에서 최대 320px로 표시한다. */
		wide?: boolean;
		ondismiss?: () => void;
		children?: Snippet;
	} = $props();
	let panel = $state<HTMLDivElement>();
	let position = $state({ left: 8, top: 8 });
	let containerWidth = $state<number>();
	let maxHeight = $state(0);
	let scrollable = $state(false);
	let placementFits = $state(true);
	function place() {
		if (!panel) return;
		containerWidth = container?.clientWidth;
		const target = anchor.getBoundingClientRect();
		const containerBounds = container?.getBoundingClientRect();
		const leftEdge = wide ? 8 : Math.max(8, (containerBounds?.left ?? 0) + 8);
		const rightEdge = wide
			? document.documentElement.clientWidth - 8
			: Math.min(innerWidth - 8, (containerBounds?.right ?? innerWidth) - 8);
		const header = document.querySelector('header');
		const navigation = document.querySelector('nav[aria-label="주 메뉴"]');
		const headerFixed = header && ['fixed', 'sticky'].includes(getComputedStyle(header).position);
		const topEdge = Math.max(8, headerFixed ? header.getBoundingClientRect().bottom + 8 : 8);
		const bottomEdge =
			navigation && getComputedStyle(navigation).position === 'fixed'
				? Math.min(innerHeight - 8, navigation.getBoundingClientRect().top - 8)
				: innerHeight - 8;
		maxHeight = Math.max(0, bottomEdge - topEdge);
		// Constrain before measuring so placement uses the scroll panel's rendered height.
		panel.style.maxHeight = `${maxHeight}px`;
		const bounds = panel.getBoundingClientRect();
		scrollable = panel.scrollHeight > panel.clientHeight;
		const above = target.top - topEdge - gap;
		const below = bottomEdge - target.bottom - gap;
		const preferredTop =
			placement === 'below'
				? target.bottom + gap
				: above >= bounds.height || above > below
					? target.top - bounds.height - gap
					: target.bottom + gap;
		placementFits =
			placement !== 'below' ||
			(preferredTop >= topEdge && preferredTop + bounds.height <= bottomEdge);
		position = {
			left: Math.max(
				leftEdge,
				Math.min(target.left + target.width / 2 - bounds.width / 2, rightEdge - bounds.width)
			),
			top:
				placement === 'below'
					? preferredTop
					: Math.max(topEdge, Math.min(preferredTop, bottomEdge - bounds.height))
		};
	}
	$effect(() => {
		// Reposition after text, font metrics or the selected anchor changes.
		void date;
		void label;
		void value;
		void note;
		void message;
		void placement;
		let frame = 0;
		const observer = new ResizeObserver(() => {
			cancelAnimationFrame(frame);
			frame = requestAnimationFrame(place);
		});
		if (panel) observer.observe(panel);
		observer.observe(anchor);
		if (container) observer.observe(container);
		const header = document.querySelector('header');
		const navigation = document.querySelector('nav[aria-label="주 메뉴"]');
		if (header) observer.observe(header);
		if (navigation) observer.observe(navigation);
		untrack(place);
		return () => {
			observer.disconnect();
			cancelAnimationFrame(frame);
		};
	});
</script>

<svelte:window onresize={place} onscroll={place} />

<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions (공간이 부족해 스크롤되는 설명에만 키보드 접근과 Escape 복귀 제공) -->
<div
	bind:this={panel}
	{id}
	role="tooltip"
	data-scrollable={scrollable}
	tabindex={scrollable && ondismiss ? 0 : -1}
	onkeydown={(event) => {
		if (event.key === 'Escape' && panel?.contains(document.activeElement) && ondismiss) {
			event.stopPropagation();
			anchor.focus({ preventScroll: true });
			ondismiss();
		}
	}}
	onfocusout={(event) => {
		if (
			event.relatedTarget instanceof Node &&
			!panel?.contains(event.relatedTarget) &&
			event.relatedTarget !== anchor
		)
			ondismiss?.();
	}}
	class={[
		'fixed z-50 max-w-[calc(100vw-1rem)] overflow-y-auto rounded-2xl border border-border bg-card text-sm shadow-[0_2px_2px_0_var(--color-border)] focus-visible:outline-2 focus-visible:outline-ring',
		scrollable && 'overscroll-y-contain',
		message ? 'w-max' : wide ? 'w-80' : 'w-64'
	]}
	style:left={`${position.left}px`}
	style:top={`${position.top}px`}
	style:max-height={`${maxHeight}px`}
	style:visibility={maxHeight >= 2 && placementFits ? 'visible' : 'hidden'}
	style:max-width={!wide && containerWidth !== undefined
		? `min(calc(100vw - 1rem), ${containerWidth - 16}px)`
		: undefined}
>
	<div class={compact ? 'px-3 py-2' : 'px-4 py-3'}>
		{#if children}
			{@render children()}
		{:else if message}
			<p class="text-xs text-text-secondary">{message}</p>
			{#if note}<p class="mt-2 text-xs text-text-secondary">{note}</p>{/if}
		{:else}
			<p class="text-xs text-text-secondary">{date.replaceAll('-', '.')}</p>
			{#if rows}
				<dl class="mt-2 space-y-2">
					{#each rows as row (row.label)}
						<div class="flex flex-wrap items-center justify-between gap-2">
							<dt>{row.label}</dt>
							<dd class="font-bold">
								{row.value === null
									? '—'
									: `${row.value.toLocaleString('ko-KR', { maximumFractionDigits: 20 })} ${row.unit}`}
							</dd>
						</div>
					{/each}
				</dl>
			{:else}
				<p class={['flex flex-wrap items-center justify-between gap-2', compact ? 'mt-1' : 'mt-2']}>
					<span>{label}</span><strong
						>{value === null
							? missingValueText
							: (valueText ??
								`${value.toLocaleString('ko-KR', { maximumFractionDigits: 20 })} ${unit}`)}</strong
					>
				</p>
			{/if}
			{#if note}<p class="mt-2 text-xs text-text-secondary">{note}</p>{/if}
		{/if}
	</div>
</div>
