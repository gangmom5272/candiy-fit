<script lang="ts">
	import { MAX_LISTED_ACTIVITY_DAYS, type ExerciseActivityView } from '../evidence-view';
	import { formatDayWithWeekday } from '../format';

	/**
	 * 운동 근거의 횟수·칼로리·거리(S15P31A403-375). 근거 상세 창의 운동 탭 안에 둔다.
	 * 위에는 기간 합계, 아래에는 날짜별 표. 표는 일주일까지 그대로 보여 주고 더 길면 접어 둔다.
	 * 값과 문구는 evidence-view.ts exerciseActivityView 가 정한다
	 */
	let { view }: { view: ExerciseActivityView } = $props();
</script>

{#snippet table()}
	<!-- 휴대폰 폭(360px 이상)에는 네 열이 다 들어가고, 더 좁거나 글자를 키우면 표만 가로로 넘긴다. 스크롤 영역이라 키보드로도 밀 수 있게 포커스를 받는다 -->
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div
		class="overflow-x-auto rounded-2xl border border-border bg-surface"
		role="region"
		aria-label="날짜별 운동 횟수·칼로리·거리 표"
		tabindex="0"
	>
		<table class="w-full min-w-max text-sm">
			<caption class="sr-only">날짜별 운동 횟수·칼로리·거리</caption>
			<thead>
				<tr class="border-b border-border text-[13px] text-text-primary">
					<th scope="col" class="py-3 pr-2 pl-4 text-left font-bold">날짜</th>
					<th scope="col" class="px-2 py-3 text-right font-bold sm:px-3">횟수</th>
					<th scope="col" class="px-2 py-3 text-right font-bold sm:px-3">칼로리</th>
					<th scope="col" class="py-3 pr-4 pl-2 text-right font-bold sm:pl-3">거리</th>
				</tr>
			</thead>
			<tbody class="divide-y divide-border">
				{#each view.rows as row (row.date)}
					<tr>
						<th scope="row" class="py-3 pr-2 pl-4 text-left font-semibold whitespace-nowrap">
							{formatDayWithWeekday(row.date)}
							{#if row.inProgress}
								<span class="block text-xs font-normal text-primary">수집 중</span>
							{/if}
						</th>
						{#each row.cells as cell, index (index)}
							<td
								class={[
									'py-3 text-right whitespace-nowrap tabular-nums',
									index === row.cells.length - 1 ? 'pr-4 pl-2 sm:pl-3' : 'px-2 sm:px-3'
								]}
							>
								{cell}
							</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/snippet}

<section class="flex flex-col gap-3" data-evidence-detail="exercise-activity">
	<h3 class="text-card-title">운동 횟수·칼로리·거리</h3>
	{#if view.cards.length > 0}
		<dl
			class="grid grid-cols-2 gap-4 rounded-2xl border border-border bg-surface px-6 py-5 sm:grid-cols-3"
		>
			{#each view.cards as card (card.label)}
				<div class="min-w-0">
					<dt class="text-[13px] font-bold text-text-primary">{card.label}</dt>
					<dd class="mt-1 text-metric-sm whitespace-nowrap">{card.value}</dd>
					{#each card.captions as caption (caption)}
						<dd class="mt-1 text-xs text-text-primary">{caption}</dd>
					{/each}
				</div>
			{/each}
		</dl>
	{/if}
	<!-- 하루 기간은 위 카드와 같은 값 하나뿐이라 표를 두지 않는다 -->
	{#if view.rows.length > 1}
		{#if view.rows.length <= MAX_LISTED_ACTIVITY_DAYS}
			{@render table()}
		{:else}
			<details>
				<summary
					class="cursor-pointer rounded-lg py-1 text-sm font-bold text-text-secondary hover:text-text-primary"
				>
					날짜별 횟수·칼로리·거리 보기 ({view.rows.length}일)
				</summary>
				<div class="mt-3">{@render table()}</div>
			</details>
		{/if}
	{/if}
	<div class="flex flex-col gap-1 text-[13px] text-text-secondary">
		{#each view.notes as note (note)}
			<p>{note}</p>
		{/each}
	</div>
</section>
