<script lang="ts">
	let {
		message,
		layout = 'detail',
		refreshing = false,
		periodLabel = ''
	}: {
		message: string;
		refreshing?: boolean;
		layout?: 'activity' | 'sleep' | 'heart' | 'detail';
		periodLabel?: string;
	} = $props();
</script>

<div
	role="status"
	aria-label={refreshing ? `선택한 기록을 다시 조회하고 있어요. ${message}` : message}
	data-health-loading
	data-loading-phase={refreshing ? 'refresh' : 'initial'}
>
	<span class="sr-only">{message}</span>
	<div aria-hidden="true">
		{#if layout === 'heart'}
			<div data-loading-card class="rounded-2xl border border-border bg-card px-4 py-6 sm:px-7">
				<div class="flex flex-wrap items-start justify-between gap-5">
					<div>
						<p class="health-skeleton w-fit text-section-title text-transparent">일별 심박수</p>
					</div>
					<div>
						<p class="health-skeleton w-fit text-sm font-bold text-transparent">평균 심박수</p>
						<!-- 핵심 수치(text-metric) 줄 높이 35px 와 같다 -->
						<div class="health-skeleton mt-1 h-[35px] w-40 max-w-full"></div>
					</div>
				</div>
				<div class="loading-heart-legend mt-6 flex justify-end gap-6 text-[13px] text-transparent">
					<span class="health-skeleton">최저–최고 범위</span><span class="health-skeleton"
						>하루 평균</span
					>
				</div>
				<div
					class="loading-heart-chart mt-6 mb-8 grid grid-cols-[28px_minmax(0,1fr)] gap-x-2 sm:grid-cols-[36px_minmax(0,1fr)] sm:gap-x-3"
				>
					<div></div>
					<div>
						<div class="health-skeleton heart-loading-plot"></div>
						<div class="loading-heart-dates mt-6 flex h-11 items-center justify-between">
							{#each Array.from({ length: 7 }, (_, index) => index) as index (index)}<span
									class="health-skeleton h-3 w-5"
								></span>{/each}
						</div>
					</div>
				</div>
				<div class="health-skeleton h-4 w-52 max-w-full"></div>
			</div>
		{:else if layout === 'activity'}
			<div
				data-loading-card
				class="min-w-0 rounded-3xl border border-border bg-card px-4 py-6 sm:px-7"
			>
				<div class="flex min-h-14 flex-col justify-between gap-4 sm:flex-row sm:items-center">
					<div>
						<p class="health-skeleton w-fit text-section-title text-transparent">일별 걸음 수</p>
						<p class="mt-2 text-[13px] leading-[normal] text-transparent">
							{periodLabel} · 평균에 반영된 완료 기록 <span class="inline-block w-[1.6em]"></span>
						</p>
					</div>
					<div
						class="flex min-h-14 items-center justify-between gap-8 rounded-[20px] bg-primary-soft px-6 py-2 sm:w-[326px] sm:shrink-0"
					>
						<div>
							<p class="health-skeleton text-xs leading-[normal] text-transparent">
								선택 기간 일평균
							</p>
							<div class="health-skeleton mt-1 h-5 w-20"></div>
						</div>
						<div class="health-skeleton h-5 w-16"></div>
					</div>
				</div>
				<div class="mt-6 grid grid-cols-[36px_minmax(0,1fr)] sm:grid-cols-[52px_minmax(0,1fr)]">
					<div></div>
					<div>
						<div class="health-skeleton h-[169px]"></div>
						<div class="health-skeleton mt-1.5 h-8 sm:h-4"></div>
					</div>
				</div>
			</div>
			<div data-loading-following class="mt-3 grid gap-4 lg:grid-cols-2 lg:gap-8">
				{#each ['요일별 평균 걸음 수', '시간대별 평균 걸음 수'] as title (title)}
					<div class="rounded-3xl border border-border bg-card px-4 py-6 sm:px-7">
						<p class="health-skeleton w-fit text-card-title-lg text-transparent">
							{title}
						</p>
						<div class="health-skeleton mt-2 h-4 w-full"></div>
						<div class="health-skeleton mt-3 h-9 w-full"></div>
						<div class="health-skeleton mt-3 h-[132px]"></div>
					</div>
				{/each}
			</div>
			<div class="mt-12 lg:mt-18">
				<div class="health-skeleton h-9 w-20"></div>
				<div class="mt-8 grid gap-6 lg:grid-cols-2 lg:gap-8">
					{#each [0, 1, 2, 3] as index (index)}<div
							class="rounded-3xl border border-border bg-card px-4 py-6 sm:px-7"
						>
							<div class="health-skeleton h-5 w-28"></div>
							<div class="health-skeleton mt-2 h-8 w-40"></div>
							<div class="health-skeleton mt-2.5 h-4 w-48 max-w-full"></div>
							<div class="health-skeleton mt-6 h-[108px]"></div>
						</div>{/each}
				</div>
			</div>
		{:else if layout === 'sleep'}
			<div data-loading-card class="rounded-3xl border border-border bg-card px-4 py-6 sm:px-7">
				<div class="flex flex-wrap items-start justify-between gap-4">
					<div>
						<p class="health-skeleton w-fit text-section-title text-transparent">주간 수면 흐름</p>
						<p class="mt-3 text-sm text-transparent">
							{periodLabel} · 날짜별 취침부터 기상까지의 흐름
						</p>
					</div>
					<div class="flex flex-wrap items-center gap-3 self-center text-sm text-transparent">
						<span>이전 7일 대비</span><span class="health-skeleton h-5 w-16"></span>
					</div>
				</div>
				<div
					class="mt-7 grid grid-cols-[52px_minmax(0,1fr)_76px] gap-x-2 text-xs sm:grid-cols-[80px_minmax(0,1fr)_112px] sm:gap-x-5 sm:text-sm"
				>
					<span class="pb-3 text-transparent">날짜</span><span></span><span
						class="pb-3 text-right text-transparent">수면 시간</span
					>
					{#each Array.from({ length: 7 }, (_, index) => index) as index (index)}
						<div class="flex min-h-12 items-center">
							<span class="health-skeleton h-3 w-8"></span>
						</div>
						<div class="flex min-h-12 items-center">
							<span class="health-skeleton h-5 w-full"></span>
						</div>
						<div class="flex min-h-12 items-center justify-end">
							<span class="health-skeleton h-3 w-12"></span>
						</div>
					{/each}
					<div></div>
					<div class="health-skeleton mt-2 h-5"></div>
					<div></div>
				</div>
			</div>
			<div data-loading-following class="mt-8 grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
				{#each [0, 1] as index (index)}<div
						class="rounded-2xl border border-border bg-card px-4 py-6 sm:px-7"
					>
						<div class="health-skeleton h-7 w-32 max-w-full"></div>
						<div class="health-skeleton mt-4 h-56"></div>
					</div>{/each}
			</div>
		{:else}
			<div class="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
				{#each [0, 1, 2, 3, 4] as index (index)}
					<div
						class={[
							'min-w-0 rounded-2xl border border-border bg-card p-5 sm:p-6',
							index === 4 ? 'col-span-full' : ''
						]}
					>
						<div class="health-skeleton h-6 w-32 max-w-full"></div>
						<div class="health-skeleton mt-3 h-4 w-48 max-w-full"></div>
						<div class={['health-skeleton mt-6 w-full', index < 4 ? 'h-8' : 'h-48']}></div>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>

<style>
	.heart-loading-plot {
		height: 192px;
	}
	@media (min-width: 640px) {
		.heart-loading-plot {
			height: 240px;
		}
	}
	@media (min-width: 1024px) {
		.heart-loading-plot {
			height: clamp(120px, calc(100dvh - var(--app-header-height, 80px) - 600px), 300px);
		}
		.loading-heart-legend {
			margin-top: 16px;
		}
		.loading-heart-chart {
			margin-top: 24px;
			margin-bottom: 16px;
		}
		.loading-heart-dates {
			margin-top: 16px;
		}
	}
	@media (min-width: 1024px) and (max-height: 900px) {
		.loading-heart-legend {
			margin-top: 12px;
		}
		.loading-heart-chart {
			margin-top: 20px;
			margin-bottom: 8px;
		}
		.loading-heart-dates {
			margin-top: 8px;
		}
	}
</style>
