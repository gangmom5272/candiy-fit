<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';

	const tabs = [
		{ label: '홈', route: '/(app)/health' },
		{ label: '활동', route: '/(app)/health/activity' },
		{ label: '수면', route: '/(app)/health/sleep' },
		{ label: '심박', route: '/(app)/health/heart' }
	] as const;
</script>

<nav aria-label="건강 데이터" class="mt-[39px] flex gap-4 sm:gap-11">
	{#each tabs as tab (tab.route)}
		{@const path = new URL(resolve(tab.route), page.url.href).pathname}
		{@const isExact = page.url.pathname === path}
		{@const isCurrent =
			isExact || (tab.route !== '/(app)/health' && page.url.pathname.startsWith(`${path}/`))}
		<a
			href={resolve(tab.route)}
			aria-current={isExact ? 'page' : isCurrent ? 'true' : undefined}
			class={[
				'flex min-h-[42px] w-14 items-start justify-center border-b-[3px] pt-1 text-base leading-[normal] font-semibold focus-visible:outline-2 focus-visible:outline-ring',
				isCurrent ? 'border-brand font-bold text-primary' : 'border-transparent text-text-secondary'
			]}>{tab.label}</a
		>
	{/each}
</nav>
