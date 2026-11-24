<script lang="ts">
	import { resolve } from '$app/paths';
	import HealthSummaryChart from './HealthSummaryChart.svelte';
	import type { HealthSummary } from '../summary';

	let {
		summary,
		label,
		error,
		preparing = false,
		sectionId,
		referenceDate,
		dayHref,
		onDayNavigate,
		loadingMessage = '기록을 불러오는 중이에요.',
		onRetry
	}: {
		summary?: HealthSummary;
		label?: string;
		error?: string;
		preparing?: boolean;
		sectionId?: string;
		referenceDate?: string;
		dayHref?: (date: string) => string;
		onDayNavigate?: (event: MouseEvent, date: string) => void;
		loadingMessage?: string;
		onRetry?: () => void;
	} = $props();
	const metricHref = $derived(
		summary
			? `${resolve(`/(app)/health/${summary.path}`)}${referenceDate ? `?${new URLSearchParams({ date: referenceDate })}` : ''}${summary.hash ?? ''}`
			: undefined
	);
</script>

<section
	id={sectionId}
	aria-busy={!summary && !error && !preparing}
	class={[
		'health-summary-card relative flex min-h-72 min-w-0 flex-col rounded-3xl border px-6 pt-4 pb-4',
		error && !preparing ? 'border-transparent bg-danger-soft' : 'border-border bg-card'
	]}
>
	<svelte:element
		this={summary ? 'a' : 'div'}
		href={metricHref}
		class="health-summary-link after:absolute after:inset-0 after:rounded-3xl focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-offset-4 focus-visible:after:outline-ring"
	>
		<h2 class="text-card-title">
			{summary?.label ?? label}
		</h2>
		{#if summary}
			<div class="mt-5 flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
				<p class="min-w-0 text-metric [overflow-wrap:anywhere]">{summary.value}</p>
				<div class="ml-auto text-right">
					{#if summary.id !== 'heart'}
						<p class="text-xs leading-[normal] font-bold text-text-secondary">
							{#if summary.recordedDays !== undefined}
								평균에 반영된 기록 {summary.recordedDays}일
							{:else}
								기록 {summary.values.filter((value) => value !== null).length}일 / {summary.values
									.length}일
							{/if}
						</p>
					{/if}
					{#if summary.average !== null}<p
							class={[
								summary.id !== 'heart' && 'mt-1',
								'flex items-center justify-end gap-1.5 text-[11px] leading-[normal] font-bold text-text-secondary'
							]}
						>
							<span class="w-5 border-t border-dashed border-coral-main" aria-hidden="true"
							></span>평균선
						</p>{/if}
				</div>
			</div>
			<p class="mt-2 text-xs leading-[normal] text-text-secondary">{summary.description}</p>
		{/if}
	</svelte:element>
	{#if summary}
		<div class="relative mt-5">
			<HealthSummaryChart
				{summary}
				interactive
				{dayHref}
				{onDayNavigate}
				metricHref={dayHref ? metricHref : undefined}
			/>
		</div>
	{:else if preparing}
		<p role="status" class="mt-5 text-sm leading-relaxed text-card-foreground">
			건강 기록을 준비 중이에요.
		</p>
		<button
			type="button"
			onclick={onRetry}
			aria-label={`${label} 다시 확인`}
			class="relative mt-3 cursor-pointer self-start rounded-xl border border-border bg-card px-4 py-2 text-sm font-bold text-card-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
			>다시 확인</button
		>
	{:else if !error}
		<div role="status" class="mt-5">
			<span class="sr-only">{loadingMessage}</span>
			<div aria-hidden="true" class="space-y-3">
				<div class="health-skeleton h-7 w-32"></div>
				<div class="health-skeleton h-3 w-40"></div>
				<div class="health-skeleton h-[132px] w-full"></div>
			</div>
		</div>
	{:else}
		<p role="alert" class="mt-5 text-sm [overflow-wrap:anywhere] text-text-primary">
			{error}
		</p>
		<button
			type="button"
			onclick={onRetry}
			aria-label={`${label} 다시 시도`}
			class="relative mt-3 cursor-pointer self-start rounded-xl border border-border bg-card px-4 py-2 text-sm font-bold text-text-primary hover:bg-surface focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text-primary"
			>다시 시도</button
		>
	{/if}
</section>
