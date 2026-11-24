<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import HealthBackLink from './HealthBackLink.svelte';
	const { Story } = defineMeta({ title: 'Health/HealthBackLink', component: HealthBackLink });
</script>

<Story name="Default" args={{ href: '/(app)/health', label: '건강 데이터로' }} />
