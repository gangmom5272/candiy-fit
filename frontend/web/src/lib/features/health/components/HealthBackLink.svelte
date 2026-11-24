<script lang="ts">
	import { resolve } from '$app/paths';
	import leftIcon from '#lib/assets/icons/icon-chevron-left-small.svg';
	let {
		href,
		date,
		label = '돌아가기',
		onclick
	}: {
		href:
			| '/(app)/health'
			| '/(app)/health/sleep'
			| '/(app)/health/sleep/[date]'
			| '/(app)/health/activity'
			| '/(app)/health/activity#exercise'
			| '/(app)/health/heart';
		date?: string;
		label?: string;
		onclick?: (event: MouseEvent) => void;
	} = $props();
</script>

<a
	href={href === '/(app)/health/sleep/[date]'
		? date
			? resolve(href, { date })
			: resolve('/(app)/health')
		: resolve(href)}
	{onclick}
	class="inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-xl border border-border bg-card px-5 text-sm font-bold text-text-secondary hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
>
	<img src={leftIcon} alt="" width="24" height="24" />{label}
</a>
