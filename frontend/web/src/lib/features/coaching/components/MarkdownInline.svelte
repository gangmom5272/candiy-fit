<script lang="ts">
	import type { MarkdownInline } from '../markdown';
	import Self from './MarkdownInline.svelte';

	let { nodes }: { nodes: MarkdownInline[] } = $props();
</script>

<!-- 글자는 Svelte가 이스케이프해 출력한다. HTML로 해석하는 경로가 없다 -->
{#each nodes as node, index (index)}
	{#if node.type === 'text'}{node.text}{:else if node.type === 'br'}<br
		/>{:else if node.type === 'cursor'}<span class="ml-0.5 animate-pulse" aria-hidden="true">|</span
		>{:else if node.type === 'strong'}<strong class="font-bold"
			><Self nodes={node.children} /></strong
		>{:else}<em><Self nodes={node.children} /></em>{/if}
{/each}
