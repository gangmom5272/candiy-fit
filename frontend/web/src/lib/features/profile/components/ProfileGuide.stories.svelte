<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import type { ComponentProps } from 'svelte';
	import ProfileGuide from './ProfileGuide.svelte';

	// 회원가입·마이페이지 왼쪽의 기본 정보 안내 패널. 문구만 다르고 안내 목록·캐릭터는 같다
	const { Story } = defineMeta({
		title: 'Features/profile/ProfileGuide',
		component: ProfileGuide,
		tags: ['autodocs'],
		args: {
			titleLines: ['입력한 정보는', '건강 데이터를 더 잘', '보여주기 위해 활용돼요!'],
			descriptionLines: [
				'닉네임, 성별, 태어난 연도, 키, 몸무게를',
				'현재 정보에 맞게 수정할 수 있어요.'
			]
		},
		render: template
	});
</script>

{#snippet template(args: ComponentProps<typeof ProfileGuide>)}
	<!-- 마이페이지 카드 왼쪽 칸과 같은 폭. 화면 높이에 따른 간격 변수(--guide-*)는 화면이 정하므로 여기서는 기본값 -->
	<div class="max-w-[454px]">
		<ProfileGuide {...args} />
	</div>
{/snippet}

<Story name="마이페이지 문구" exportName="MyPage" />

<Story
	name="회원가입 문구"
	exportName="Signup"
	args={{
		titleLines: [
			'기본 정보를 입력하면',
			'나에게 맞는 건강 기록을',
			'더 정확하게 보여줄 수 있어요.'
		],
		descriptionLines: ['처음 한 번만 입력하면 되고,', '나중에 마이페이지에서 수정할 수 있어요.']
	}}
/>
