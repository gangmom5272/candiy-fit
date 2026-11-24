<script lang="ts">
	import DayDatePicker from './DayDatePicker.svelte';
	let {
		periodLabel,
		note,
		previousDisabled = false,
		nextDisabled = false,
		onPrevious,
		onNext,
		date,
		maxDate,
		onDateSelect
	}: {
		periodLabel: string;
		note: string;
		previousDisabled?: boolean;
		nextDisabled?: boolean;
		onPrevious: () => void;
		onNext: () => void;
		date?: string;
		maxDate?: string;
		onDateSelect?: (date: string) => void;
	} = $props();
</script>

<div class="flex min-w-0 flex-col gap-4 lg:flex-row lg:items-center lg:gap-6">
	<div
		class="flex min-w-0 shrink-0 items-center gap-4 sm:gap-8"
		role="group"
		aria-label="주간 기간 선택"
	>
		<button
			type="button"
			aria-label="이전 주"
			disabled={previousDisabled}
			onclick={onPrevious}
			class="grid size-10 shrink-0 cursor-pointer place-items-center rounded-[10px] border border-border bg-card text-text-secondary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring enabled:hover:bg-primary-soft disabled:cursor-default disabled:opacity-40"
		>
			<svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true"
				><path
					d="M12 4 6 10l6 6"
					stroke="currentColor"
					stroke-width="1.5"
					stroke-linecap="round"
					stroke-linejoin="round"
				/></svg
			>
		</button>
		{#if date && maxDate && onDateSelect}
			<div
				class="min-w-0 text-center text-sm leading-[normal] font-bold sm:min-w-[169px] sm:text-[17px]"
			>
				<DayDatePicker {date} {maxDate} label={periodLabel} onSelect={onDateSelect} />
				<output aria-live="polite" class="sr-only">{periodLabel}</output>
			</div>
		{:else}
			<output
				aria-live="polite"
				class="min-w-0 text-center text-sm leading-[normal] font-bold sm:min-w-[169px] sm:text-[17px]"
				>{periodLabel}</output
			>
		{/if}
		<button
			type="button"
			aria-label="다음 주"
			disabled={nextDisabled}
			onclick={onNext}
			class="grid size-10 shrink-0 cursor-pointer place-items-center rounded-[10px] border border-border bg-card text-text-secondary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring enabled:hover:bg-primary-soft disabled:cursor-default disabled:opacity-40"
		>
			<svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true"
				><path
					d="m8 4 6 6-6 6"
					stroke="currentColor"
					stroke-width="1.5"
					stroke-linecap="round"
					stroke-linejoin="round"
				/></svg
			>
		</button>
	</div>
	<p class="flex min-w-0 items-start gap-2 text-xs leading-[normal] text-text-secondary">
		<span class="mt-1 size-1.5 shrink-0 rounded-full bg-coral-main" aria-hidden="true"></span>{note}
	</p>
</div>
