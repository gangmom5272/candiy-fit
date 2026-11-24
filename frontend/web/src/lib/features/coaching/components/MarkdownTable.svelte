<script lang="ts">
	import type { MarkdownBlock } from '../markdown';
	import MarkdownInline from './MarkdownInline.svelte';

	let { table }: { table: Extract<MarkdownBlock, { type: 'table' }> } = $props();

	let scroller: HTMLDivElement | undefined = $state();
	let moreLeft = $state(false);
	let moreRight = $state(false);

	/** 가로로 더 밀 수 있는 쪽을 잰다. 휴대폰에서 표가 열 경계에서 잘리면 끝난 표처럼 보여 가장자리를 흐리게 한다 */
	function measure() {
		if (!scroller) return;
		const max = scroller.scrollWidth - scroller.clientWidth;
		moreLeft = scroller.scrollLeft > 1;
		moreRight = scroller.scrollLeft < max - 1;
	}

	// 화면 폭이 바뀌거나 스트리밍으로 행이 늘면 표 너비가 달라져 다시 잰다.
	// 웹폰트가 늦게 적용되면 글자 폭만 바뀌어 크기 관찰로 놓칠 수 있어 폰트를 다 불러온 뒤에도 잰다
	$effect(() => {
		if (!scroller) return;
		let active = true;
		const observer = new ResizeObserver(measure);
		observer.observe(scroller);
		for (const child of scroller.children) observer.observe(child);
		measure();
		document.fonts?.ready.then(() => active && measure());
		return () => {
			active = false;
			observer.disconnect();
		};
	});
</script>

<!-- 표는 내용 폭만 차지한다. 넓은 화면에서 답변 폭 전체로 늘리면 열 사이가 멀어 읽기 어렵다 -->
<div class="relative w-fit max-w-full">
	<!-- 표가 화면보다 넓으면 이 상자만 가로로 밀어 본다. 밀 수 있을 때만 키보드 포커스를 받는다
	     (스크롤 영역은 포커스할 수 있어야 한다는 접근성 규칙. 다 보이는 표에서 Tab이 멈추지 않게) -->
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div
		bind:this={scroller}
		class="overflow-x-auto rounded-xl border border-border focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
		role="region"
		aria-label="비교 표"
		tabindex={moreLeft || moreRight ? 0 : undefined}
		onscroll={measure}
	>
		<table class="w-max border-collapse text-left text-sm">
			<!-- 회색 글자(text-secondary)는 surface 배경에서 대비 4.43으로 기준(4.5)에 못 미쳐 본문 색을 쓴다 -->
			<thead class="bg-surface text-text-primary">
				<tr>
					{#each table.header as cell, cellIndex (cellIndex)}
						<th
							scope="col"
							class="px-3 py-2.5 font-semibold whitespace-nowrap sm:px-4"
							style:text-align={table.align[cellIndex] ?? undefined}
						>
							<MarkdownInline nodes={cell} />
						</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each table.rows as row, rowIndex (rowIndex)}
					<tr class="border-t border-border">
						{#each row as cell, cellIndex (cellIndex)}
							<!-- 첫 열은 날짜·지표 이름 같은 항목 이름이다 -->
							{#if cellIndex === 0}
								<th
									scope="row"
									class="px-3 py-2.5 font-semibold whitespace-nowrap sm:px-4"
									style:text-align={table.align[cellIndex] ?? undefined}
								>
									<MarkdownInline nodes={cell} />
								</th>
							{:else}
								<td
									class="px-3 py-2.5 whitespace-nowrap tabular-nums sm:px-4"
									style:text-align={table.align[cellIndex] ?? undefined}
								>
									<MarkdownInline nodes={cell} />
								</td>
							{/if}
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
	{#if moreLeft}
		<div
			class="pointer-events-none absolute inset-y-px left-px w-8 rounded-l-xl bg-linear-to-r from-card to-transparent"
			data-more="left"
			aria-hidden="true"
		></div>
	{/if}
	{#if moreRight}
		<div
			class="pointer-events-none absolute inset-y-px right-px w-8 rounded-r-xl bg-linear-to-l from-card to-transparent"
			data-more="right"
			aria-hidden="true"
		></div>
	{/if}
</div>
