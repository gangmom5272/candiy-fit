<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import Toast from './Toast.svelte';

	const { Story } = defineMeta({
		title: 'Components/feedback/Toast',
		component: Toast,
		tags: ['autodocs'],
		parameters: { layout: 'fullscreen' }
	});
</script>

<Story
	name="오류"
	exportName="Error"
	args={{ kind: 'error', message: '로그아웃하지 못했어요. 잠시 후 다시 시도해 주세요.' }}
/>

<Story
	name="다시 보내기"
	exportName="WithAction"
	args={{
		kind: 'error',
		message: '요청을 처리하지 못했습니다.',
		action: { label: '다시 보내기', onclick: () => {} }
	}}
/>

<Story name="안내" exportName="Info" args={{ kind: 'info', message: '기본 정보를 저장했어요.' }} />
