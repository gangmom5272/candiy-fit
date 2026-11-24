<script lang="ts">
	import './layout.css';
	import favicon from '#lib/assets/favicon.png';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();
</script>

<svelte:head>
	<link rel="icon" type="image/png" sizes="64x64" href={favicon} />
</svelte:head>

{@render children()}
