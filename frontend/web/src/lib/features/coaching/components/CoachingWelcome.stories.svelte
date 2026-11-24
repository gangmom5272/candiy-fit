<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { fn } from 'storybook/test';
	import CoachingWelcome from './CoachingWelcome.svelte';

	const { Story } = defineMeta({
		title: 'Features/coaching/CoachingWelcome',
		component: CoachingWelcome,
		tags: ['autodocs'],
		args: { onsubmit: fn() },
		parameters: { layout: 'padded' }
	});
</script>

<Story name="첫 화면" exportName="Default" />
