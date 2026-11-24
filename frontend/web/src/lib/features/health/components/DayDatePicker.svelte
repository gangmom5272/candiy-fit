<script lang="ts">
	import { untrack } from 'svelte';
	import { SvelteDate } from 'svelte/reactivity';
	import { resolve } from '$app/paths';
	import { activityToday } from '../activity-api';
	import { healthViewportBounds } from '../health-viewport';
	import leftIcon from '#lib/assets/icons/icon-chevron-left-small.svg';
	import rightIcon from '#lib/assets/icons/icon-chevron-right-small.svg';
	let {
		date,
		maxDate,
		label,
		today = activityToday(),
		onSelect,
		onNavigate
	}: {
		date: string;
		maxDate: string;
		label?: string;
		today?: string;
		onSelect?: (date: string) => void;
		onNavigate?: (event: MouseEvent, date: string) => void;
	} = $props();
	const id = $props.id();
	let calendar = $state<HTMLDivElement>();
	let isOpen = $state(false);
	function placeCalendar() {
		if (!calendar || !isOpen) return;
		const { top, bottom } = healthViewportBounds();
		const gap = Math.max(0, Math.min(8, (bottom - top - 48) / 2));
		calendar.style.top = `${top + gap}px`;
		calendar.style.bottom = `${innerHeight - bottom + gap}px`;
		calendar.style.maxHeight = `${Math.max(0, bottom - top - gap * 2)}px`;
	}
	$effect(() => {
		if (!isOpen) return;
		placeCalendar();
		const observer = new ResizeObserver(placeCalendar);
		for (const element of document.querySelectorAll('header, nav[aria-label="주 메뉴"]'))
			observer.observe(element);
		return () => observer.disconnect();
	});
	let month = $state(untrack(() => date.slice(0, 7)));
	const monthStart = $derived(new SvelteDate(`${month}-01T00:00:00Z`));
	const days = $derived.by(() => {
		const end = new SvelteDate(monthStart);
		end.setUTCMonth(end.getUTCMonth() + 1);
		end.setUTCDate(0);
		return Array.from(
			{ length: end.getUTCDate() },
			(_, index) => `${month}-${String(index + 1).padStart(2, '0')}`
		);
	});
	const previousMonth = $derived(shiftMonth(-1));
	const nextMonth = $derived(shiftMonth(1));
	function shiftMonth(offset: number) {
		const result = new SvelteDate(monthStart);
		result.setUTCMonth(result.getUTCMonth() + offset);
		return result.toISOString().slice(0, 7);
	}
	function handleToggle(event: ToggleEvent) {
		isOpen = event.newState === 'open';
		placeCalendar();
		if (event.newState === 'open' && event.currentTarget instanceof HTMLElement) {
			event.currentTarget.querySelector<HTMLElement>('[aria-current="date"]')?.focus();
		}
	}
	function handleDateSelect(event: MouseEvent) {
		if (event.currentTarget instanceof HTMLElement) {
			event.currentTarget.closest<HTMLElement>('[popover]')?.hidePopover();
		}
	}
	function dayClass(day: string) {
		if (day === date) return 'bg-primary text-primary-foreground';
		return [
			day === today ? 'bg-primary-soft text-text-primary' : '',
			'hover:bg-primary hover:text-primary-foreground'
		];
	}
</script>

<svelte:window onresize={placeCalendar} />

<button
	type="button"
	popovertarget={id}
	aria-label={`날짜 선택, ${label ?? date.replaceAll('-', '.')}`}
	class={[
		'date-picker-trigger min-h-11 cursor-pointer rounded-lg bg-transparent px-2 text-[15px] font-semibold focus-visible:outline-2 focus-visible:outline-ring',
		label ? 'text-text-primary' : 'text-text-secondary'
	]}
	style:font-size={label ? 'inherit' : undefined}
	style:font-weight={label ? 'inherit' : undefined}
	onclick={() => {
		month = date.slice(0, 7);
	}}
>
	<span class="date-picker-label">{label ?? date.replaceAll('-', '.')}</span>
</button>
<div
	bind:this={calendar}
	{id}
	popover="auto"
	role="dialog"
	aria-label="날짜 선택 캘린더"
	ontoggle={handleToggle}
	class="date-calendar fixed inset-0 m-auto w-[min(320px,calc(100vw-40px))] overflow-y-auto overscroll-contain rounded-2xl border border-border bg-card p-4 text-text-primary shadow-lg"
>
	<div class="mb-3 flex items-center justify-between">
		<button
			type="button"
			aria-label="이전 달"
			disabled={month <= '0001-01'}
			onclick={() => {
				month = previousMonth;
			}}
			class="grid size-10 cursor-pointer place-items-center rounded-lg hover:bg-primary-soft disabled:cursor-default disabled:opacity-40"
			><img src={leftIcon} alt="" width="24" height="24" /></button
		>
		<p aria-live="polite" class="font-bold">
			{Number(month.slice(0, 4))}년 {Number(month.slice(5))}월
		</p>
		<button
			type="button"
			aria-label="다음 달"
			disabled={month >= maxDate.slice(0, 7)}
			onclick={() => {
				month = nextMonth;
			}}
			class="grid size-10 cursor-pointer place-items-center rounded-lg hover:bg-primary-soft disabled:cursor-default disabled:opacity-40"
			><img src={rightIcon} alt="" width="24" height="24" /></button
		>
	</div>
	<div aria-hidden="true" class="mb-1 grid grid-cols-7 text-center text-xs text-text-secondary">
		{#each ['일', '월', '화', '수', '목', '금', '토'] as weekday (weekday)}<span>{weekday}</span
			>{/each}
	</div>
	<div class="calendar-days grid grid-cols-7 gap-y-1">
		{#each days as day, index (day)}
			{#if day > maxDate}<button
					type="button"
					disabled
					aria-label={day}
					style:grid-column-start={index === 0 ? monthStart.getUTCDay() + 1 : undefined}
					class="h-10 rounded-lg text-sm text-text-secondary opacity-40">{index + 1}</button
				>
			{:else if onSelect}
				<button
					type="button"
					aria-label={day}
					aria-current={day === date ? 'date' : undefined}
					aria-describedby={day === today ? `${id}-today` : undefined}
					style:grid-column-start={index === 0 ? monthStart.getUTCDay() + 1 : undefined}
					class={[
						'grid h-10 cursor-pointer place-items-center rounded-lg text-sm focus-visible:outline-2 focus-visible:outline-ring',
						dayClass(day)
					]}
					onclick={(event) => {
						handleDateSelect(event);
						onSelect?.(day);
					}}>{index + 1}</button
				>
			{:else}<a
					href={resolve('/(app)/health/day/[date]', { date: day })}
					onclick={(event) => {
						handleDateSelect(event);
						onNavigate?.(event, day);
					}}
					aria-label={day}
					aria-current={day === date ? 'date' : undefined}
					aria-describedby={day === today ? `${id}-today` : undefined}
					style:grid-column-start={index === 0 ? monthStart.getUTCDay() + 1 : undefined}
					class={[
						'grid h-10 cursor-pointer place-items-center rounded-lg text-sm focus-visible:outline-2 focus-visible:outline-ring',
						dayClass(day)
					]}
				>
					{index + 1}
				</a>{/if}
		{/each}
	</div>
	<span id={`${id}-today`} class="sr-only">오늘</span>
	<button
		type="button"
		popovertarget={id}
		popovertargetaction="hide"
		class="mt-3 min-h-10 w-full cursor-pointer rounded-lg border border-border text-sm transition-colors duration-150 hover:border-text-disabled hover:text-text-primary focus-visible:outline-2 focus-visible:outline-ring motion-reduce:transition-none"
		>닫기</button
	>
</div>

<style>
	.date-picker-label {
		display: inline-block;
		transition: transform 140ms ease-out;
	}
	@media (hover: hover) {
		.date-picker-trigger:hover .date-picker-label {
			transform: translateY(-1px);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.date-picker-label {
			transition: none;
			transform: none !important;
		}
	}
	.date-calendar .calendar-days > :global(*) {
		height: 44px;
	}
	@media (min-width: 769px) {
		.date-calendar {
			width: min(448px, calc(100vw - 40px));
			padding: 24px;
		}
		.date-calendar .calendar-days {
			gap: 8px;
		}
		.date-calendar .calendar-days > :global(*) {
			height: 48px;
			font-size: 16px;
		}
	}
</style>
