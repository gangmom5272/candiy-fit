<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { spyOn } from 'storybook/test';
	import OfflineBanner from './OfflineBanner.svelte';

	const { Story } = defineMeta({
		title: 'Components/feedback/OfflineBanner',
		component: OfflineBanner,
		tags: ['autodocs'],
		parameters: { layout: 'fullscreen' }
	});

	function mockOnline(isOnline: boolean) {
		const online = spyOn(navigator, 'onLine', 'get').mockReturnValue(isOnline);
		return () => online.mockRestore();
	}
</script>

<Story name="온라인 · 배너 숨김" exportName="Online" beforeEach={() => mockOnline(true)} />
<Story name="오프라인 · 연결 안내" exportName="Offline" beforeEach={() => mockOnline(false)} />
