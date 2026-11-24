<script lang="ts">
	import { onDestroy } from 'svelte';
	import { resolve } from '$app/paths';
	import { revealHealthFocus } from '../health-viewport';

	let {
		title,
		description,
		buttonLabel,
		keepFocusVisible = false
	}: {
		title: string;
		description: string;
		buttonLabel: string;
		keepFocusVisible?: boolean;
	} = $props();
	const id = $props.id();
	let focusFrame = 0;
	function handleFocus(event: FocusEvent) {
		const target = event.currentTarget;
		if (!keepFocusVisible || !(target instanceof HTMLElement) || !target.matches(':focus-visible'))
			return;
		cancelAnimationFrame(focusFrame);
		focusFrame = requestAnimationFrame(() => {
			if (document.activeElement === target) revealHealthFocus(target);
		});
	}
	onDestroy(() => cancelAnimationFrame(focusFrame));
</script>

<section
	aria-labelledby={`${id}-title`}
	class="flex flex-col justify-between gap-5 rounded-[20px] border border-coral-sub bg-primary-soft px-6 py-3 lg:min-h-20 lg:flex-row lg:items-center"
>
	<div>
		<h2 id={`${id}-title`} class="text-card-title-lg">{title}</h2>
		<!-- primary-soft 배경 위라 보조색(4.46:1) 대신 주요색을 쓴다(색상 역할 배경별 예외) -->
		<p class="mt-2 text-[13px] leading-[normal] text-text-primary">{description}</p>
	</div>
	<a
		onfocus={handleFocus}
		href={resolve('/(app)/coaching')}
		class="flex min-h-13 items-center justify-center rounded-xl bg-primary px-5 py-3 text-center text-sm leading-[normal] font-bold text-primary-foreground hover:bg-primary-hover focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring lg:w-[316px] lg:shrink-0"
		>{buttonLabel}</a
	>
</section>
