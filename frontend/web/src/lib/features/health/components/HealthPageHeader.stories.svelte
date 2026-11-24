<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import HealthPageHeader from './HealthPageHeader.svelte';

	const { Story } = defineMeta({
		title: 'Features/health/HealthPageHeader',
		component: HealthPageHeader
	});
</script>

<Story
	name="Home"
	parameters={{
		sveltekit_experimental: { state: { page: { url: new URL('http://localhost/health') } } }
	}}
/>
<Story
	name="Activity"
	parameters={{
		sveltekit_experimental: {
			state: { page: { url: new URL('http://localhost/health/activity') } }
		}
	}}
/>
<Story
	name="Sleep"
	parameters={{
		sveltekit_experimental: { state: { page: { url: new URL('http://localhost/health/sleep') } } }
	}}
/>
<Story
	name="Heart"
	parameters={{
		sveltekit_experimental: { state: { page: { url: new URL('http://localhost/health/heart') } } }
	}}
/>
