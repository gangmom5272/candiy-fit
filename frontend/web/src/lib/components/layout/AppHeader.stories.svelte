<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { fn } from 'storybook/test';
	import AppHeader from './AppHeader.svelte';

	// 앱 화면에서는 layout이 onLogout을 넘긴다. 빼면 로그아웃 메뉴가 비활성이 된다.
	const { Story } = defineMeta({
		title: 'Components/layout/AppHeader',
		component: AppHeader,
		args: { onLogout: fn() },
		parameters: { layout: 'fullscreen' }
	});
</script>

<Story
	name="Home"
	parameters={{
		sveltekit_experimental: { state: { page: { url: new URL('http://localhost/home') } } }
	}}
/>
<Story
	name="Health"
	parameters={{
		sveltekit_experimental: { state: { page: { url: new URL('http://localhost/health/sleep') } } }
	}}
/>
<Story
	name="Coaching"
	parameters={{
		sveltekit_experimental: {
			state: { page: { url: new URL('http://localhost/coaching/example-conversation') } }
		}
	}}
/>
<Story
	name="로그아웃 연결 없음"
	exportName="WithoutLogout"
	args={{ onLogout: undefined }}
	parameters={{
		sveltekit_experimental: { state: { page: { url: new URL('http://localhost/home') } } }
	}}
/>
