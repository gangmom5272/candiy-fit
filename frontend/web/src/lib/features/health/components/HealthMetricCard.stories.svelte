<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import HealthMetricCard from './HealthMetricCard.svelte';
	import icon from '#lib/assets/icons/icon-foot.svg';
	const { Story } = defineMeta({
		title: 'Features/health/HealthMetricCard',
		component: HealthMetricCard,
		args: { label: '걸음 수', value: '8,120보', icon }
	});
</script>

<Story name="Default" />
<Story name="Missing" args={{ value: '기록 없음' }} />
