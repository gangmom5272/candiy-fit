<script module lang="ts">
	export interface ScoreStatus {
		metric: string;
		label: string;
		tone: 'success' | 'warning' | 'info' | 'danger';
		score: number | null;
		description: string;
	}
</script>

<script lang="ts">
	import scoreInfoIcon from '#lib/assets/icons/icon-score-info.svg';
	interface Props {
		open?: boolean;
		score?: number | null;
		statuses?: ScoreStatus[];
		notice?: string | null;
	}
	let { open = $bindable(false), score = null, statuses = [], notice = null }: Props = $props();
	const id = $props.id();
	let root: HTMLDivElement;
	let trigger: HTMLButtonElement;
	let isHoverOpen = $state(false);
	const hasLongLabels = $derived(statuses.some((status) => status.label.length > 2));
	const summary = $derived(
		statuses
			.map(({ metric, score }) => `${metric} ${score === null ? '기록 없음' : `${score}점`}`)
			.join(' · ')
	);

	function close() {
		open = false;
		isHoverOpen = false;
	}
	function handlePointerEnter(event: PointerEvent) {
		if (event.pointerType === 'mouse' && !open) {
			isHoverOpen = true;
			open = true;
		}
	}
	function handlePointerLeave() {
		if (isHoverOpen) close();
	}
	function handleClick() {
		if (isHoverOpen) isHoverOpen = false;
		else open = !open;
	}
	function handleOutsidePointer(event: PointerEvent) {
		if (event.target instanceof Node && !root.contains(event.target)) close();
	}
	function handleKeydown(event: KeyboardEvent) {
		if (open && event.key === 'Escape') {
			event.preventDefault();
			close();
			trigger.focus();
		}
	}
	function handleFocusOut(event: FocusEvent) {
		if (!(event.relatedTarget instanceof Node) || !root.contains(event.relatedTarget)) close();
	}
</script>

<svelte:window onpointerdown={handleOutsidePointer} onkeydown={handleKeydown} />

<div
	bind:this={root}
	class="score-summary"
	class:score-summary--extended={hasLongLabels}
	role="group"
	aria-label="건강 상태"
	onpointerenter={handlePointerEnter}
	onpointerleave={handlePointerLeave}
	onfocusout={handleFocusOut}
>
	<button
		bind:this={trigger}
		type="button"
		class="summary-trigger relative w-56 max-w-full cursor-pointer rounded-lg border border-l-8 border-border border-l-brand bg-primary-soft px-8 py-3 text-left focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring"
		aria-expanded={open}
		aria-controls={`${id}-panel`}
		aria-describedby={`${id}-description`}
		onclick={handleClick}
	>
		<img
			src={scoreInfoIcon}
			alt=""
			width="24"
			height="24"
			class="summary-icon absolute -top-2 -right-2"
		/>
		<span class="summary-statuses grid gap-2">
			{#each statuses as status (status.metric)}
				<span class="summary-status flex items-center justify-between gap-6 text-xl font-semibold">
					<span class="shrink-0 whitespace-nowrap">{status.metric}</span>
					<span
						class="status-label whitespace-nowrap"
						class:status-label--state={status.label.length > 2}
						class:text-success={status.tone === 'success'}
						class:text-warning={status.tone === 'warning'}
						class:text-danger={status.tone === 'danger'}
						class:text-info={status.tone === 'info'}>{status.label}</span
					>
				</span>
			{/each}
		</span>
	</button>
	<span id={`${id}-description`} class="sr-only">오늘의 점수 설명</span>
	{#if open}
		<div class="score-panel">
			<!-- svelte-ignore a11y_no_noninteractive_tabindex (설명 패널을 키보드로 스크롤할 수 있도록 focus를 제공한다.) -->
			<section
				id={`${id}-panel`}
				aria-labelledby={`${id}-title`}
				tabindex="0"
				class="score-details rounded-[20px] border border-border bg-card p-5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:p-6"
			>
				<p class="text-sm text-primary">오늘의 점수 설명</p>
				<h2 id={`${id}-title`} class="mt-1 text-[28px] leading-tight font-bold">
					왜 이런 상태로 보일까요?
				</h2>
				<p class="mt-2 text-sm leading-relaxed text-text-secondary">
					{notice ??
						(score === null
							? '오늘의 점수 기록이 없어요.'
							: `${summary}을 기준으로 오늘의 점수는 ${score}점이에요.`)}
				</p>
				<ul class="mt-6 grid gap-3">
					{#each statuses as status (status.metric)}
						<li
							class="detail-row grid items-center gap-x-5 gap-y-2 rounded-2xl px-3 py-3"
							class:bg-success-soft={status.tone === 'success'}
							class:bg-warning-soft={status.tone === 'warning'}
							class:bg-danger-soft={status.tone === 'danger'}
							class:bg-info-soft={status.tone === 'info'}
						>
							<span
								class="w-fit rounded-full px-1 text-xs"
								class:status-badge--success={status.tone === 'success'}
								class:status-badge--warning={status.tone === 'warning'}
								class:status-badge--danger={status.tone === 'danger'}
								class:status-badge--info={status.tone === 'info'}
								class:status-badge--state={status.score === null}
								>{status.score === null
									? status.metric
									: `${status.metric} · ${status.label}`}</span
							>
							<strong class="text-xl whitespace-nowrap" class:detail-state={status.score === null}
								>{status.score === null ? status.label : `${status.score}점`}</strong
							>
							<p class="detail-description min-w-0 text-sm leading-relaxed text-text-secondary">
								{status.description}
							</p>
						</li>
					{/each}
				</ul>
			</section>
		</div>
	{/if}
</div>

<style>
	.score-summary {
		position: relative;
		width: 100%;
		min-width: 0;
	}
	.score-panel {
		width: 100%;
		padding-top: 0.75rem;
		overflow-wrap: break-word;
	}
	.score-summary--extended .summary-trigger {
		padding-inline: 1rem;
	}
	.score-summary--extended .summary-status {
		gap: 0.75rem;
	}
	.score-summary--extended .status-label {
		font-size: 1rem;
	}
	.score-summary--extended .status-label--state {
		color: var(--color-text-secondary);
	}
	.detail-state {
		font-size: 1rem;
	}
	.score-details {
		max-height: calc(100dvh - var(--app-header-height, 0px) - 2rem);
		scroll-margin-top: calc(var(--app-header-height, 0px) + 1rem);
		overflow-y: auto;
	}
	.detail-row {
		grid-template-columns: auto 1fr;
	}
	.detail-description {
		grid-column: 1 / -1;
	}
	.status-badge--success {
		color: var(--color-success);
		background-color: var(--color-success-badge);
	}
	.status-badge--danger {
		color: var(--color-danger);
		background-color: var(--color-danger-soft);
	}
	.status-badge--warning {
		color: var(--color-warning);
		background-color: var(--color-warning-badge);
	}
	.status-badge--info {
		color: var(--color-info);
		background-color: var(--color-info-badge);
	}
	.status-badge--state {
		color: var(--color-text-secondary);
	}
	@media (width < 64rem) {
		.summary-trigger {
			width: 100%;
			padding-inline: 0.5rem;
		}
		.summary-statuses {
			grid-template-columns: repeat(3, minmax(0, 1fr));
			gap: 0.5rem;
		}
		.summary-status,
		.score-summary--extended .summary-status {
			min-width: 0;
			flex-direction: column;
			justify-content: flex-start;
			gap: 0.25rem;
			font-size: 1rem;
			line-height: 1.5rem;
			text-align: center;
		}
		.status-label {
			max-width: 100%;
			font-size: 1.125rem;
			white-space: normal;
			overflow-wrap: anywhere;
			word-break: keep-all;
		}
		.score-summary--extended .summary-trigger {
			padding-inline: 0.5rem;
		}
		.score-details {
			max-height: calc(
				100dvh - var(--app-header-height, 0px) - var(--app-bottom-nav-height, 0px) - 2rem
			);
		}
	}
	@media (40rem <= width < 64rem) {
		.summary-status,
		.score-summary--extended .summary-status {
			font-size: 1.125rem;
			line-height: 1.75rem;
		}
		.status-label {
			font-size: 1.25rem;
		}
	}
	@media (min-width: 40rem) {
		.detail-row {
			grid-template-columns: auto max-content minmax(0, 1fr);
		}
		.detail-description {
			grid-column: auto;
		}
	}
	@media (min-width: 64rem) {
		.score-summary {
			width: 13rem;
			max-width: 100%;
			margin-bottom: 0.625rem;
		}
		.summary-trigger {
			width: 100%;
			border-width: 0 0 0 0.5rem;
			padding: 0.75rem 2rem;
		}
		.summary-statuses {
			gap: 0.75rem;
		}
		.summary-icon {
			top: -0.5rem;
			right: -0.5rem;
		}
		.summary-status {
			gap: 2rem;
			font-size: 24px;
			font-weight: 500;
			line-height: 28px;
		}
		.status-label {
			font-size: 22px;
		}
		.score-panel {
			position: absolute;
			top: 0;
			left: 100%;
			padding-left: 0.75rem;
			padding-top: 0;
			z-index: 40;
			width: min(30rem, calc(100vw - 46rem));
		}
		.score-details {
			max-height: 40dvh;
		}
	}
	@media (min-width: 80rem) and (min-height: 37.5rem) {
		.score-details {
			max-height: none;
			overflow: visible;
		}
		.score-summary--extended .score-details {
			max-height: calc(100dvh - var(--app-header-height, 0px) - 4rem);
			overflow-y: auto;
		}
	}
</style>
