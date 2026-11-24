<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { fn } from 'storybook/test';
	import SignupPanel from './SignupPanel.svelte';

	// 회원가입 화면(/onboarding) 전체. Figma WEB_기본정보입력
	const { Story } = defineMeta({
		title: 'Features/auth/SignupPanel',
		component: SignupPanel,
		tags: ['autodocs'],
		args: { onSubmit: fn() },
		parameters: { layout: 'fullscreen' }
	});
</script>

<Story name="입력 전" exportName="Default" />

<Story name="가입 요청 중" exportName="Submitting" args={{ isSubmitting: true }} />

<Story
	name="입력칸 오류"
	exportName="FieldErrors"
	args={{
		serverErrors: {
			birthYear: '1900년부터 2026년 사이로 입력해 주세요.',
			heightCm: '키는 소수 둘째 자리까지 입력할 수 있어요.'
		}
	}}
/>

<Story
	name="가입 실패"
	exportName="FormError"
	args={{
		formError: {
			message: '가입하지 못했어요. 잠시 후 다시 시도해 주세요.',
			requestId: 'req_01JBXEXAMPLE0000'
		}
	}}
/>
