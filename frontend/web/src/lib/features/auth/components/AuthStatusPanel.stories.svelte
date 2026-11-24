<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import type { ComponentProps } from 'svelte';
	import AuthShell from './AuthShell.svelte';
	import AuthStatusPanel from './AuthStatusPanel.svelte';
	import { Button } from '#lib/components/ui/button/index.js';

	type Args = ComponentProps<typeof AuthStatusPanel> & {
		/** 스토리에서만 쓰는 버튼 구성 */
		buttons?: 'login' | 'retry';
	};

	// 로그인 처리 중(/auth/callback), 가입 시간 초과(/onboarding), 로그인 상태 확인 실패((app) 화면)의 안내
	const { Story } = defineMeta({
		title: 'Features/auth/AuthStatusPanel',
		component: AuthStatusPanel,
		tags: ['autodocs'],
		parameters: { layout: 'fullscreen' },
		render: template
	});
</script>

{#snippet template({ buttons, ...args }: Args)}
	<AuthShell>
		<AuthStatusPanel {...args}>
			{#snippet actions()}
				{#if buttons === 'retry'}
					<Button size="lg" class="w-full">다시 시도</Button>
					<Button size="lg" variant="outline" class="w-full" href="#login">로그인 화면으로</Button>
				{:else if buttons === 'login'}
					<Button size="lg" class="w-full" href="#login">로그인 화면으로</Button>
				{/if}
			{/snippet}
		</AuthStatusPanel>
	</AuthShell>
{/snippet}

<Story
	name="로그인하는 중"
	exportName="Loading"
	args={{
		tone: 'loading',
		title: '카카오 계정으로 로그인하는 중이에요',
		message: '잠시만 기다려 주세요. 로그인이 끝나면 바로 이동해요.'
	}}
/>

<!-- 서버 오류는 문의할 때 쓰도록 요청 ID 를 함께 보여 준다 -->
<Story
	name="로그인 실패"
	exportName="Failed"
	args={{
		tone: 'error',
		title: '로그인하지 못했어요',
		message: '로그인을 완료하지 못했어요. 잠시 후 다시 시도해 주세요.',
		requestId: 'req_01JBXEXAMPLE0000',
		buttons: 'login'
	}}
/>

<Story
	name="로그인 취소"
	exportName="Cancelled"
	args={{
		tone: 'error',
		title: '로그인하지 못했어요',
		message: '로그인을 취소했어요.',
		buttons: 'login'
	}}
/>

<Story
	name="가입 시간 초과"
	exportName="SignupExpired"
	args={{
		tone: 'error',
		title: '다시 로그인해 주세요',
		message: '가입 가능 시간(10분)이 지났거나 가입 정보를 확인할 수 없어요.',
		buttons: 'login'
	}}
/>

<Story
	name="로그인 상태 확인 실패"
	exportName="RestoreFailed"
	args={{
		tone: 'error',
		title: '로그인 상태를 확인하지 못했어요',
		message: '잠시 후 다시 시도해 주세요.',
		buttons: 'retry'
	}}
/>
