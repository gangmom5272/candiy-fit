<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import ProfileFormSkeleton from './ProfileFormSkeleton.svelte';
	import ProfileForm from './ProfileForm.svelte';

	// 마이페이지가 저장된 기본 정보를 불러오는 동안 ProfileForm 자리에 두는 스켈레톤
	const { Story } = defineMeta({
		title: 'Features/profile/ProfileFormSkeleton',
		component: ProfileFormSkeleton,
		tags: ['autodocs']
	});
</script>

<Story name="불러오는 중" exportName="Loading">
	<div class="max-w-[52rem]">
		<ProfileFormSkeleton />
	</div>
</Story>

<!-- 같은 폭에서 실제 폼과 나란히 두고 자리·높이가 맞는지 본다 -->
<Story name="실제 폼과 비교" exportName="Compare">
	<div class="grid max-w-[104rem] gap-8 lg:grid-cols-2">
		<ProfileFormSkeleton />
		<ProfileForm
			submitLabel="저장하기"
			initialValues={{
				displayName: '테스트',
				birthYear: '2000',
				gender: 'FEMALE',
				heightCm: '160',
				weightKg: '50'
			}}
			onSubmit={() => {}}
		/>
	</div>
</Story>
