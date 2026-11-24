<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import AppHeader from '#lib/components/layout/AppHeader.svelte';
	import AppLinkGuide from './AppLinkGuide.svelte';

	const { Story } = defineMeta({
		title: 'Features/health/AppLinkGuide',
		component: AppLinkGuide,
		parameters: {
			layout: 'fullscreen',
			docs: {
				description: {
					component:
						'미연동이 확정된 사용자용 안내 UI. 현재 CANDIY_USER_NOT_LINKED는 첫 데이터 준비 전도 포함하므로 실제 라우트에는 연결하지 않습니다.'
				}
			},
			sveltekit_experimental: { state: { page: { url: new URL('http://localhost/home') } } }
		}
	});
</script>

<Story name="앱 연동 안내">
	{#snippet template()}
		<!-- 실제 (app) layout이 확보하는 모바일 하단 내비게이션 여백 -->
		<div class="min-h-dvh pb-[calc(5rem+env(safe-area-inset-bottom,0px))] md:pb-0">
			<AppHeader />
			<AppLinkGuide />
		</div>
	{/snippet}
</Story>
