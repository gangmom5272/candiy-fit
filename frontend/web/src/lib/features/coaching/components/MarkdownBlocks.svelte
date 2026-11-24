<script lang="ts">
	import type { MarkdownBlock } from '../markdown';
	import MarkdownInline from './MarkdownInline.svelte';
	import MarkdownTable from './MarkdownTable.svelte';
	import Self from './MarkdownBlocks.svelte';

	let { blocks }: { blocks: MarkdownBlock[] } = $props();
</script>

{#each blocks as block, index (index)}
	{#if block.type === 'paragraph'}
		<p><MarkdownInline nodes={block.children} /></p>
	{:else if block.type === 'heading'}
		<!-- 소제목: 본문(16px)보다 한 단계 큰 17px 굵게(답변 첫 줄과 같은 단계, #300), 앞 덩어리와 문단 간격 한 번 더 띄운다 -->
		<h3 class="mt-3 text-[17px] font-bold"><MarkdownInline nodes={block.children} /></h3>
	{:else if block.type === 'table'}
		<MarkdownTable table={block} />
	{:else if block.ordered}
		<ol
			class="flex list-decimal flex-col gap-2 pl-5 marker:text-text-secondary"
			start={block.start}
		>
			{#each block.items as item, itemIndex (itemIndex)}
				<li class="pl-1 [&>p]:inline"><Self blocks={item} /></li>
			{/each}
		</ol>
	{:else}
		<ul class="flex list-disc flex-col gap-2 pl-5 marker:text-brand">
			{#each block.items as item, itemIndex (itemIndex)}
				<li class="pl-1 [&>p]:inline"><Self blocks={item} /></li>
			{/each}
		</ul>
	{/if}
{/each}
