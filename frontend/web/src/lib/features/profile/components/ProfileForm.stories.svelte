<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { expect, fn, userEvent, within } from 'storybook/test';
	import ProfileForm from './ProfileForm.svelte';

	const { Story } = defineMeta({
		title: 'Features/profile/ProfileForm',
		component: ProfileForm,
		tags: ['autodocs'],
		args: { submitLabel: '가입하기', onSubmit: fn() },
		parameters: { layout: 'padded' }
	});
</script>

<Story name="입력 전" exportName="Default" />

<Story name="가입 요청 중" exportName="Submitting" args={{ isSubmitting: true }} />

<Story
	name="서버 입력 오류"
	exportName="ServerErrors"
	args={{
		serverErrors: {
			birthYear: '1900년부터 2026년 사이로 입력해 주세요.',
			heightCm: '키는 소수 둘째 자리까지 입력할 수 있어요.'
		}
	}}
/>

<Story
	name="빈 칸으로 제출"
	exportName="EmptySubmit"
	play={async ({ canvasElement, args }) => {
		const canvas = within(canvasElement);
		await userEvent.click(canvas.getByRole('button', { name: '가입하기' }));
		await expect(canvas.getByLabelText('닉네임')).toHaveFocus();
		await expect(canvas.getByText('성별을 선택해 주세요.')).toBeVisible();
		await expect(args.onSubmit).not.toHaveBeenCalled();
	}}
/>

<Story
	name="올바르게 입력"
	exportName="ValidSubmit"
	play={async ({ canvasElement, args }) => {
		const canvas = within(canvasElement);
		await userEvent.type(canvas.getByLabelText('닉네임'), '새회원');
		await userEvent.type(canvas.getByLabelText('태어난 연도'), '1995');
		await userEvent.click(canvas.getByText('여성'));
		await userEvent.type(canvas.getByLabelText('키', { exact: true }), '165.25');
		await userEvent.type(canvas.getByLabelText('몸무게', { exact: true }), '55');
		await userEvent.click(canvas.getByRole('button', { name: '가입하기' }));
		await expect(args.onSubmit).toHaveBeenCalledWith({
			displayName: '새회원',
			birthYear: 1995,
			gender: 'FEMALE',
			heightCm: 165.25,
			weightKg: 55
		});
	}}
/>

<Story
	name="마이페이지 저장값"
	exportName="WithSavedValues"
	args={{
		submitLabel: '저장하기',
		initialValues: {
			displayName: '캔디핏',
			birthYear: '2007',
			gender: 'MALE',
			heightCm: '180',
			weightKg: '68'
		}
	}}
	play={async ({ canvasElement }) => {
		const canvas = within(canvasElement);
		await expect(canvas.getByLabelText('닉네임')).toHaveValue('캔디핏');
		await expect(canvas.getByRole('radio', { name: '남성' })).toBeChecked();
		await expect(canvas.getByLabelText('몸무게', { exact: true })).toHaveValue('68');
	}}
/>
