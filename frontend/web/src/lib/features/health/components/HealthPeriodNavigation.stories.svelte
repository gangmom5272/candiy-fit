<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import HealthPeriodNavigation from './HealthPeriodNavigation.svelte';

	const { Story } = defineMeta({
		title: 'Features/health/HealthPeriodNavigation',
		component: HealthPeriodNavigation,
		args: {
			periodLabel: '2026.10.19 ~ 10.25',
			note: '기록이 없는 날은 평균에서 제외해요. 날짜를 누르면 하루 상세 기록을 볼 수 있어요.',
			onPrevious: () => undefined,
			onNext: () => undefined
		}
	});
</script>

<Story name="Default" />
<Story name="PreviousDisabled" args={{ previousDisabled: true }} />
<Story name="CurrentWeek" args={{ periodLabel: '2026.10.26 · 현재 주', nextDisabled: true }} />
