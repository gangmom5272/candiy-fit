<script lang="ts">
	import { wholeViewLabel, type RangeView, type WeekScroll } from '../week-scroll.svelte';

	/**
	 * 일별 차트 오른쪽 위에 두는 보기 전환(S15P31A403-418). 기간이 일주일을 넘을 때만 나온다.
	 * "일주일"은 최근 7일부터 가로로 넘겨 보고, 기간 전체("한 달", 짧으면 "14일")는 모든 날을 한 화면에 그린다.
	 * 처음에는 일주일씩 건너뛰는 날짜 버튼도 여기 있었는데, 사용자가 직접 자유롭게 넘기는 쪽으로 정해 뺐다
	 */
	type Props = { range: WeekScroll };

	let { range }: Props = $props();

	const VIEWS = $derived<[RangeView, string][]>([
		['week', '일주일'],
		['all', wholeViewLabel(range.count)]
	]);
</script>

{#if range.available}
	<div
		class="flex rounded-lg border border-border bg-card p-0.5 text-xs"
		role="group"
		aria-label="보는 범위"
		data-chart-range
	>
		{#each VIEWS as [view, name] (view)}
			<button
				type="button"
				aria-pressed={range.view === view}
				onclick={() => (range.view = view)}
				class={[
					'cursor-pointer rounded-md px-2.5 py-1 whitespace-nowrap',
					range.view === view
						? 'bg-primary-soft font-bold text-primary'
						: 'text-text-primary hover:bg-surface'
				]}
			>
				{name}
			</button>
		{/each}
	</div>
{/if}
